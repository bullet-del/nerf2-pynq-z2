#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


LEVEL = {"source": 0, "software": 1, "physical": 2}


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def inside(root: Path, relative: str) -> Path:
    path = Path(os.path.abspath(root / relative))
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"path escapes project root: {relative}") from exc
    return path


def pointer(value: Any, expression: str) -> tuple[bool, Any]:
    if expression == "":
        return True, value
    if not expression.startswith("/"):
        raise ValueError(f"JSON pointer must start with '/': {expression}")
    current = value
    for raw in expression[1:].split("/"):
        token = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict) and token in current:
            current = current[token]
        elif isinstance(current, list) and token.isdigit() and int(token) < len(current):
            current = current[int(token)]
        else:
            return False, None
    return True, current


def compare(exists: bool, actual: Any, op: str, expected: Any) -> bool:
    if op == "exists":
        return exists == bool(expected)
    if not exists:
        return False
    if op == "eq":
        return actual == expected
    if op == "ne":
        return actual != expected
    if op == "le":
        return actual <= expected
    if op == "ge":
        return actual >= expected
    if op == "truthy":
        return bool(actual) == bool(expected)
    if op == "length_eq":
        return len(actual) == int(expected)
    raise ValueError(f"unsupported operator: {op}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit an FPGA/ML reproducibility contract")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--contract", type=Path, default=Path("build/workflow.json"))
    parser.add_argument("--mode", choices=tuple(LEVEL), default="source")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    root = Path(os.path.abspath(args.root.expanduser()))
    contract_path = args.contract.expanduser()
    if not contract_path.is_absolute():
        contract_path = root / contract_path
    contract_path = Path(os.path.abspath(contract_path))
    contract = load_json(contract_path)
    if contract.get("schema") != "fpga-ml-reproducibility-contract-v1":
        raise RuntimeError("contract schema mismatch")

    checks: list[dict[str, Any]] = []
    failures: list[str] = []
    pending: list[str] = []
    source = contract.get("source", {})

    for relative in source.get("required_files", []):
        ok = inside(root, relative).is_file()
        checks.append({"id": f"source.file:{relative}", "status": "PASS" if ok else "FAIL"})
        if not ok:
            failures.append(f"missing required file: {relative}")
    for relative in source.get("required_directories", []):
        ok = inside(root, relative).is_dir()
        checks.append({"id": f"source.directory:{relative}", "status": "PASS" if ok else "FAIL"})
        if not ok:
            failures.append(f"missing required directory: {relative}")
    for item in source.get("glob_counts", []):
        count = len([path for path in root.glob(item["pattern"]) if path.is_file()])
        minimum = int(item.get("minimum", 0))
        maximum = int(item.get("maximum", 2**63 - 1))
        ok = minimum <= count <= maximum
        checks.append({"id": f"source.glob:{item['pattern']}", "status": "PASS" if ok else "FAIL",
                       "count": count, "minimum": minimum, "maximum": maximum})
        if not ok:
            failures.append(f"glob count {item['pattern']}={count}, expected {minimum}..{maximum}")

    excluded = tuple(source.get("scan_exclude_prefixes", []))
    excluded_fragments = tuple(source.get("scan_exclude_path_fragments", []))
    suffixes = tuple(source.get("forbidden_suffixes", []))
    fragments = tuple(source.get("forbidden_path_fragments", []))
    forbidden: list[str] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        if any(relative == prefix.rstrip("/") or relative.startswith(prefix) for prefix in excluded):
            continue
        if any(fragment in path.relative_to(root).parts for fragment in excluded_fragments):
            continue
        if (suffixes and path.suffix.lower() in {value.lower() for value in suffixes}) or any(
                fragment in path.parts for fragment in fragments):
            forbidden.append(relative)
    checks.append({"id": "source.forbidden_paths", "status": "PASS" if not forbidden else "FAIL",
                   "paths": forbidden})
    failures.extend(f"forbidden source path: {path}" for path in forbidden)

    for item in contract.get("json_checks", []):
        required_for = item.get("required_for", "source")
        if LEVEL[required_for] > LEVEL[args.mode]:
            continue
        relative = item["path"]
        path = inside(root, relative)
        if not path.is_file():
            status = "FAIL" if required_for == "source" else "PENDING"
            checks.append({"id": item["id"], "status": status, "path": relative,
                           "reason": "file is missing"})
            (failures if status == "FAIL" else pending).append(f"{item['id']}: missing {relative}")
            continue
        try:
            data = load_json(path)
            exists, actual = pointer(data, item.get("pointer", ""))
            ok = compare(exists, actual, item["op"], item.get("value"))
            checks.append({"id": item["id"], "status": "PASS" if ok else "FAIL",
                           "path": relative, "pointer": item.get("pointer", ""),
                           "actual": actual, "expected": item.get("value"), "op": item["op"]})
            if not ok:
                failures.append(f"{item['id']}: {actual!r} {item['op']} {item.get('value')!r} failed")
        except Exception as exc:
            checks.append({"id": item["id"], "status": "FAIL", "path": relative,
                           "reason": f"{type(exc).__name__}: {exc}"})
            failures.append(f"{item['id']}: {type(exc).__name__}: {exc}")

    status = "FAIL" if failures else "PENDING" if pending else "PASS"
    report = {
        "schema": "fpga-ml-reproducibility-audit-v1",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "project": contract.get("project"),
        "root": str(root),
        "contract": str(contract_path),
        "mode": args.mode,
        "status": status,
        "failures": failures,
        "pending": pending,
        "checks": checks,
        "ordered_commands": contract.get("ordered_commands", []),
        "claim_boundaries": contract.get("claim_boundaries", []),
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    print(rendered, end="")
    if args.output:
        output = args.output.expanduser()
        if not output.is_absolute():
            output = root / output
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8")
    raise SystemExit(0 if status == "PASS" else 2 if status == "PENDING" else 1)


if __name__ == "__main__":
    main()
