#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TextIO


TOKENS = {"{python}", "{root}", "{contract}"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_token(value: str, root: Path, contract: Path) -> str:
    replacements = {
        "{python}": sys.executable,
        "{root}": str(root),
        "{contract}": str(contract),
    }
    rendered = value
    for token, replacement in replacements.items():
        rendered = rendered.replace(token, replacement)
    if "{" in rendered or "}" in rendered:
        raise ValueError(f"unsupported token in argv value: {value}")
    return rendered


def stream_process(argv: list[str], cwd: Path, log_path: Path) -> int:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8", newline="") as log:
        process = subprocess.Popen(
            argv,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
        )
        assert process.stdout is not None
        for line in process.stdout:
            print(line, end="")
            log.write(line)
        return process.wait()


def write_markdown(report: dict[str, Any], path: Path) -> None:
    lines = [
        "# FPGA/ML reproduction workflow report",
        "",
        f"- Project: {report.get('project')}",
        f"- Pipeline: `{report['pipeline']}`",
        f"- Status: **{report['status']}**",
        f"- Started: {report['started_utc']}",
        f"- Finished: {report['finished_utc']}",
        f"- Dry run: `{str(report['dry_run']).lower()}`",
        "",
        "## Ordered steps",
        "",
        "| Step | Status | Duration (s) | Return code | Log |",
        "|---|---:|---:|---:|---|",
    ]
    for step in report["steps"]:
        lines.append(
            f"| `{step['id']}` | {step['status']} | {step['duration_s']:.3f} | "
            f"{step['return_code'] if step['return_code'] is not None else ''} | "
            f"`{step.get('log', '')}` |"
        )
    lines.extend(["", "## Audit", ""])
    audit = report.get("audit")
    if audit:
        lines.extend([
            f"- Mode: `{audit['mode']}`",
            f"- Status: **{audit['status']}**",
            f"- JSON: `{audit['report']}`",
        ])
    else:
        lines.append("Audit was not executed.")
    if report.get("failure"):
        lines.extend(["", "## First failure", "", report["failure"]])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a contract-defined FPGA/ML reproduction workflow")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--contract", type=Path, default=Path("reproducibility-contract.json"))
    parser.add_argument("--pipeline", required=True)
    parser.add_argument("--report-dir", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    root = Path(os.path.abspath(args.root.expanduser()))
    contract_path = args.contract.expanduser()
    if not contract_path.is_absolute():
        contract_path = root / contract_path
    contract_path = Path(os.path.abspath(contract_path))
    report_dir = args.report_dir.expanduser()
    if not report_dir.is_absolute():
        report_dir = root / report_dir
    report_dir = Path(os.path.abspath(report_dir))
    report_dir.mkdir(parents=True, exist_ok=True)

    contract = load_json(contract_path)
    if contract.get("schema") != "fpga-ml-reproducibility-contract-v1":
        raise RuntimeError("contract schema mismatch")
    pipelines = contract.get("workflow_pipelines", {})
    if args.pipeline not in pipelines:
        raise RuntimeError(f"unknown pipeline: {args.pipeline}; available={sorted(pipelines)}")
    pipeline = pipelines[args.pipeline]
    command_items = contract.get("ordered_commands", [])
    commands = {item.get("id"): item for item in command_items}
    if len(commands) != len(command_items):
        raise RuntimeError("ordered command identifiers must be unique")
    step_ids = pipeline.get("steps", [])
    if not step_ids:
        raise RuntimeError("selected pipeline has no steps")

    plan: list[tuple[str, list[str]]] = []
    for step_id in step_ids:
        item = commands.get(step_id)
        if item is None:
            raise RuntimeError(f"pipeline references unknown step: {step_id}")
        raw_argv = item.get("argv")
        if not isinstance(raw_argv, list) or not raw_argv or not all(isinstance(v, str) for v in raw_argv):
            raise RuntimeError(f"step {step_id} requires a non-empty string argv array")
        plan.append((step_id, [resolve_token(v, root, contract_path) for v in raw_argv]))

    started = utc_now()
    step_reports: list[dict[str, Any]] = []
    failure: str | None = None
    for index, (step_id, argv) in enumerate(plan, start=1):
        log_path = report_dir / "logs" / f"{index:02d}_{step_id}.log"
        print(f"\n[{index}/{len(plan)}] {step_id}: {subprocess.list2cmdline(argv)}")
        begin = time.perf_counter()
        if args.dry_run:
            return_code = None
            status = "PLANNED"
        else:
            return_code = stream_process(argv, root, log_path)
            status = "PASS" if return_code == 0 else "FAIL"
        duration = time.perf_counter() - begin
        step_reports.append({
            "id": step_id,
            "status": status,
            "argv": argv,
            "return_code": return_code,
            "duration_s": duration,
            "log": str(log_path.relative_to(report_dir)) if not args.dry_run else "",
        })
        if status == "FAIL":
            failure = f"Step `{step_id}` failed with return code {return_code}. See `{log_path}`."
            break

    if failure:
        executed = {step["id"] for step in step_reports}
        for step_id, argv in plan:
            if step_id not in executed:
                step_reports.append({
                    "id": step_id,
                    "status": "NOT_RUN",
                    "argv": argv,
                    "return_code": None,
                    "duration_s": 0.0,
                    "log": "",
                })

    audit_info = None
    status = "PLANNED" if args.dry_run else "FAIL" if failure else "PASS"
    if not args.dry_run and not failure:
        audit_mode = pipeline.get("audit_mode", "source")
        audit_script = root / "skills" / "fpga-ml-reproducibility-audit" / "scripts" / "audit_fpga_repro.py"
        audit_path = report_dir / "reproducibility_audit.json"
        audit_log = report_dir / "logs" / "audit.log"
        audit_argv = [
            sys.executable,
            str(audit_script),
            "--root", str(root),
            "--contract", str(contract_path),
            "--mode", audit_mode,
            "--output", str(audit_path),
        ]
        audit_rc = stream_process(audit_argv, root, audit_log)
        audit_status = load_json(audit_path).get("status", "FAIL") if audit_path.is_file() else "FAIL"
        audit_info = {
            "mode": audit_mode,
            "status": audit_status,
            "return_code": audit_rc,
            "report": str(audit_path.relative_to(report_dir)),
            "log": str(audit_log.relative_to(report_dir)),
        }
        status = audit_status
        if audit_status != "PASS":
            failure = f"The `{audit_mode}` evidence audit ended with status {audit_status}."

    report = {
        "schema": "fpga-ml-reproduction-workflow-report-v1",
        "project": contract.get("project"),
        "root": str(root),
        "contract": str(contract_path),
        "pipeline": args.pipeline,
        "dry_run": args.dry_run,
        "started_utc": started,
        "finished_utc": utc_now(),
        "status": status,
        "steps": step_reports,
        "audit": audit_info,
        "failure": failure,
    }
    json_path = report_dir / "workflow_report.json"
    md_path = report_dir / "workflow_report.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_markdown(report, md_path)
    print(f"\nWorkflow status: {status}\nJSON: {json_path}\nMarkdown: {md_path}")
    raise SystemExit(0 if status in {"PASS", "PLANNED"} else 1)


if __name__ == "__main__":
    main()
