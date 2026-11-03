#!/usr/bin/env python3
"""Validate a PYNQ Overlay against a portable JSON contract and golden vectors."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import platform
import re
import signal
import statistics
import sys
import time
from pathlib import Path
from types import ModuleType
from typing import Any, Callable, Mapping

import numpy as np

from inspect_hwh import inspect_hwh


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def resolve(base: Path, value: str) -> Path:
    path = Path(value).expanduser()
    return Path(os.path.abspath(path if path.is_absolute() else base / path))


def load_module(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location("pynq_validation_adapter", path)
    if spec is None or spec.loader is None:
        raise ImportError(path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for name in ("prepare", "run_once", "close"):
        if not callable(getattr(module, name, None)):
            raise TypeError(f"adapter must define callable {name}()")
    return module


def load_npz(path: Path) -> dict[str, np.ndarray]:
    with np.load(path, allow_pickle=False) as value:
        return {name: np.asarray(value[name]) for name in value.files}


def array_sha256(value: np.ndarray) -> str:
    array = np.ascontiguousarray(value)
    digest = hashlib.sha256()
    digest.update(str(array.dtype).encode("ascii"))
    digest.update(json.dumps(array.shape).encode("ascii"))
    digest.update(array.tobytes())
    return digest.hexdigest()


def percentile(values: list[float], q: float) -> float:
    return float(np.percentile(np.asarray(values, np.float64), q))


def timing_summary(values: list[float]) -> dict[str, float]:
    return {
        "count": len(values), "mean_seconds": float(statistics.fmean(values)),
        "median_seconds": float(statistics.median(values)),
        "p95_seconds": percentile(values, 95), "min_seconds": min(values),
        "max_seconds": max(values),
    }


def markdown(report: dict[str, Any]) -> str:
    lines = [
        "# PYNQ Overlay validation report", "",
        f"- Project: `{report.get('project')}`",
        f"- Status: **{report.get('status')}**",
        f"- Physical board evidence: `{str(report.get('physical_board_evidence')).lower()}`",
        f"- BIT SHA-256: `{report.get('overlay', {}).get('bit_sha256', '')}`",
        f"- HWH SHA-256: `{report.get('overlay', {}).get('hwh_sha256', '')}`", "",
        "## Checks", "", "| Check | Status | Detail |", "|---|---|---|",
    ]
    for check in report.get("checks", []):
        detail = str(check.get("detail", "")).replace("|", "\\|")
        lines.append(f"| `{check.get('id')}` | {check.get('status')} | {detail} |")
    if report.get("timing"):
        timing = report["timing"]
        lines.extend(["", "## Latency", "",
                      f"- Repetitions: `{timing.get('count')}`",
                      f"- Mean: `{timing.get('mean_seconds')}` s",
                      f"- Median: `{timing.get('median_seconds')}` s",
                      f"- P95: `{timing.get('p95_seconds')}` s"])
    if report.get("comparisons"):
        lines.extend(["", "## Golden-reference comparison", "",
                      "| Output | Pass | Shape | Dtype | Max absolute error |",
                      "|---|---:|---|---|---:|"])
        for value in report["comparisons"]:
            lines.append(f"| `{value['name']}` | {value['pass']} | `{value['actual_shape']}` | "
                         f"`{value['actual_dtype']}` | {value['max_abs_error']} |")
    if report.get("failure"):
        lines.extend(["", "## Failure", "", str(report["failure"])])
    lines.extend(["", "## Evidence boundary", "",
                  str(report.get("evidence_boundary", ""))])
    return "\n".join(lines) + "\n"


class MockOverlay:
    def __init__(self, inventory: dict[str, Any]):
        self.ip_dict = {item["instance"]: {"type": item.get("vlnv") or item.get("modtype")}
                        for item in inventory["modules"]}


def mock_allocate(*, shape, dtype, **_kwargs):
    return np.zeros(shape=shape, dtype=dtype)


def get_ip(overlay: Any, name: str) -> Any:
    current = overlay
    for part in name.replace("/", ".").split("."):
        current = getattr(current, part)
    return current


def register_checks(overlay: Any, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    results = []
    for number, item in enumerate(items, 1):
        ip = get_ip(overlay, item["ip"])
        value = getattr(ip.register_map, item["register"])
        if item.get("field"):
            value = getattr(value, item["field"])
        actual = int(value)
        expected = item.get("expected")
        if expected is None:
            passed = True
        elif isinstance(expected, str):
            passed = actual == int(expected, 0)
        else:
            passed = actual == expected
        results.append({"id": f"register-{number}", "status": "PASS" if passed else "FAIL",
                        "detail": f"{item['ip']}.{item['register']}={actual}",
                        "actual": actual, "expected": expected})
        if not passed:
            raise RuntimeError(f"register check failed: {item}")
    return results


class TransactionTimeout(RuntimeError):
    pass


def call_with_timeout(function: Callable[[], Any], seconds: float) -> Any:
    if seconds <= 0 or not hasattr(signal, "SIGALRM"):
        return function()
    previous = signal.getsignal(signal.SIGALRM)
    def handler(_signum, _frame):
        raise TransactionTimeout(f"transaction exceeded {seconds} seconds")
    signal.signal(signal.SIGALRM, handler)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        return function()
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def compare_outputs(actual: Mapping[str, Any], golden: Mapping[str, np.ndarray],
                    config: dict[str, Any]) -> list[dict[str, Any]]:
    if set(actual) != set(golden):
        raise ValueError(f"adapter output keys {sorted(actual)} != golden keys {sorted(golden)}")
    exact = bool(config.get("exact", False))
    rtol = float(config.get("rtol", 1e-5))
    atol = float(config.get("atol", 1e-7))
    rows = []
    for name in sorted(golden):
        found = np.asarray(actual[name])
        wanted = np.asarray(golden[name])
        shape_pass = found.shape == wanted.shape
        dtype_pass = found.dtype == wanted.dtype
        if shape_pass:
            if np.issubdtype(found.dtype, np.number) and np.issubdtype(wanted.dtype, np.number):
                delta = np.abs(found.astype(np.float64) - wanted.astype(np.float64))
                max_abs = float(delta.max(initial=0.0))
                mean_abs = float(delta.mean()) if delta.size else 0.0
            else:
                max_abs = 0.0 if np.array_equal(found, wanted) else float("inf")
                mean_abs = max_abs
            value_pass = (np.array_equal(found, wanted) if exact
                          else bool(np.allclose(found, wanted, rtol=rtol, atol=atol, equal_nan=False)))
        else:
            max_abs = float("inf"); mean_abs = float("inf"); value_pass = False
        passed = shape_pass and (dtype_pass if exact else True) and value_pass
        rows.append({
            "name": name, "pass": passed, "exact_required": exact,
            "actual_shape": list(found.shape), "golden_shape": list(wanted.shape),
            "actual_dtype": str(found.dtype), "golden_dtype": str(wanted.dtype),
            "dtype_pass": dtype_pass, "max_abs_error": max_abs, "mean_abs_error": mean_abs,
            "actual_sha256": array_sha256(found), "golden_sha256": array_sha256(wanted),
        })
        if not passed:
            raise AssertionError(f"golden comparison failed for {name}: {rows[-1]}")
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate a PYNQ Overlay and generate evidence reports")
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--mock", action="store_true",
                        help="exercise adapter/comparison logic on a PC; never physical evidence")
    args = parser.parse_args()
    contract_path = args.contract.resolve()
    base = contract_path.parent
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    started = time.time()
    checks: list[dict[str, Any]] = []
    report: dict[str, Any] = {
        "schema": "pynq-overlay-validation-report-v1", "status": "FAIL",
        "physical_board_evidence": False, "started_unix": started,
        "host": {"platform": platform.platform(), "python": sys.version},
        "checks": checks, "failure": None,
        "evidence_boundary": "Only PASS_BOARD proves execution through a real PYNQ Overlay; power and external-I/O claims require separate measurement.",
    }
    context = None
    adapter = None
    try:
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        if contract.get("schema") != "pynq-overlay-validation-contract-v1":
            raise ValueError("unsupported contract schema")
        report["project"] = contract.get("project")
        overlay_spec = contract["overlay"]
        bit = resolve(base, overlay_spec["bit"]); hwh = resolve(base, overlay_spec["hwh"])
        for path in (bit, hwh):
            if not path.is_file():
                raise FileNotFoundError(path)
        if bit.stem != hwh.stem:
            raise ValueError("BIT and HWH must have the same base name for PYNQ pairing")
        bit_hash = sha256(bit); hwh_hash = sha256(hwh)
        if bit_hash != overlay_spec["bit_sha256"] or hwh_hash != overlay_spec["hwh_sha256"]:
            raise RuntimeError("BIT/HWH identity hash mismatch")
        checks.append({"id": "overlay-identity", "status": "PASS", "detail": "BIT/HWH hashes match"})
        inventory = inspect_hwh(hwh, bit)
        report["overlay"] = {"bit": str(bit), "hwh": str(hwh),
                             "bit_sha256": bit_hash, "hwh_sha256": hwh_hash,
                             "inventory": inventory}
        hwh_types = {item["instance"]: str(item.get("vlnv") or item.get("modtype") or "")
                     for item in inventory["modules"]}
        for number, requirement in enumerate(contract.get("required_ip", []), 1):
            name_pattern = re.compile(requirement["pattern"])
            type_pattern = re.compile(requirement.get("expected_type_pattern", ".*"))
            matches = [name for name, kind in hwh_types.items()
                       if name_pattern.search(name) and type_pattern.search(kind)]
            minimum = int(requirement.get("minimum_matches", 1))
            passed = len(matches) >= minimum
            checks.append({"id": f"hwh-required-ip-{number}", "status": "PASS" if passed else "FAIL",
                           "detail": f"matches={matches}, minimum={minimum}"})
            if not passed:
                raise RuntimeError(f"required IP missing from HWH: {requirement}")
        adapter_path = resolve(base, contract["adapter"])
        if not adapter_path.is_file():
            raise FileNotFoundError(adapter_path)
        compile(adapter_path.read_text(encoding="utf-8"), str(adapter_path), "exec")
        checks.append({"id": "adapter-syntax", "status": "PASS", "detail": str(adapter_path)})
        test = contract["test"]
        input_path = resolve(base, test["input_npz"])
        golden_path = resolve(base, test["golden_npz"])
        missing_vectors = [str(path) for path in (input_path, golden_path) if not path.is_file()]
        if args.dry_run:
            if missing_vectors:
                checks.append({"id": "vectors", "status": "PENDING", "detail": ", ".join(missing_vectors)})
            else:
                inputs = load_npz(input_path); golden = load_npz(golden_path)
                checks.append({"id": "vectors", "status": "PASS",
                               "detail": f"input keys={sorted(inputs)}, golden keys={sorted(golden)}"})
            report["status"] = "PLANNED" if not missing_vectors else "INCOMPLETE"
            report["failure"] = None
        else:
            if missing_vectors:
                raise FileNotFoundError("missing vector files: " + ", ".join(missing_vectors))
            inputs = load_npz(input_path); golden = load_npz(golden_path)
            adapter = load_module(adapter_path)
            if args.mock:
                overlay = MockOverlay(inventory); allocate = mock_allocate
                overlay_load_seconds = 0.0
            else:
                from pynq import Overlay, allocate
                load_started = time.perf_counter()
                overlay = Overlay(str(bit), download=bool(overlay_spec.get("download", True)))
                overlay_load_seconds = time.perf_counter() - load_started
            report["overlay_load_seconds"] = overlay_load_seconds
            live_types = {name: str(value.get("type", "")) for name, value in overlay.ip_dict.items()}
            for number, requirement in enumerate(contract.get("required_ip", []), 1):
                name_pattern = re.compile(requirement["pattern"])
                type_pattern = re.compile(requirement.get("expected_type_pattern", ".*"))
                matches = [name for name, kind in live_types.items()
                           if name_pattern.search(name) and type_pattern.search(kind)]
                minimum = int(requirement.get("minimum_matches", 1))
                if len(matches) < minimum:
                    raise RuntimeError(f"required live Overlay IP missing: {requirement}")
                checks.append({"id": f"live-required-ip-{number}", "status": "PASS",
                               "detail": f"matches={matches}"})
            if not args.mock:
                checks.extend(register_checks(overlay, contract.get("register_checks", [])))
            elif contract.get("register_checks"):
                checks.append({"id": "register-checks", "status": "SKIPPED_MOCK",
                               "detail": "register_map checks require a real Overlay"})
            context = adapter.prepare(overlay, allocate, contract, inputs)
            timeout = float(test.get("timeout_seconds", 30.0))
            warmup = int(test.get("warmup", 2)); repeats = int(test.get("repeats", 10))
            if warmup < 0 or repeats < 1:
                raise ValueError("warmup must be nonnegative and repeats must be positive")
            for _ in range(warmup):
                call_with_timeout(lambda: adapter.run_once(context, inputs), timeout)
            timings = []; comparisons = None
            for _ in range(repeats):
                begin = time.perf_counter()
                actual = call_with_timeout(lambda: adapter.run_once(context, inputs), timeout)
                timings.append(time.perf_counter() - begin)
                comparisons = compare_outputs(actual, golden, test.get("comparison") or {})
            report["timing"] = timing_summary(timings)
            report["comparisons"] = comparisons
            checks.append({"id": "golden-reference", "status": "PASS",
                           "detail": f"{len(comparisons or [])} outputs across {repeats} repetitions"})
            report["status"] = "PASS_MOCK" if args.mock else "PASS_BOARD"
            report["physical_board_evidence"] = not args.mock
            report["failure"] = None
    except Exception as exc:
        report["status"] = "FAIL"
        report["failure"] = f"{type(exc).__name__}: {exc}"
    finally:
        if adapter is not None and context is not None:
            try:
                adapter.close(context)
            except Exception as exc:
                report.setdefault("cleanup_warnings", []).append(f"{type(exc).__name__}: {exc}")
        report["finished_unix"] = time.time()
        report["elapsed_seconds"] = report["finished_unix"] - started
        report_path = output / "validation_report.json"
        report_path.write_text(json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2) + "\n",
                               encoding="utf-8")
        (output / "validation_report.md").write_text(markdown(report), encoding="utf-8")
        hashes = []
        for path in sorted(output.glob("*")):
            if path.is_file() and path.name != "SHA256SUMS.txt":
                hashes.append(f"{sha256(path)}  {path.name}")
        (output / "SHA256SUMS.txt").write_text("\n".join(hashes) + "\n", encoding="utf-8")
        print(json.dumps({"status": report["status"], "report": str(report_path),
                          "failure": report["failure"]}, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report["status"] in {"PLANNED", "INCOMPLETE", "PASS_MOCK", "PASS_BOARD"} else 1)


if __name__ == "__main__":
    main()
