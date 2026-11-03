#!/usr/bin/env python3
"""Aggregate repeated PYNQ validation reports without changing raw evidence."""
from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="Summarize PYNQ Overlay validation reports")
    parser.add_argument("--reports", nargs="+", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    rows = []
    for path in args.reports:
        value = json.loads(path.read_text(encoding="utf-8"))
        if value.get("schema") != "pynq-overlay-validation-report-v1":
            raise ValueError(f"unsupported report: {path}")
        timing = value.get("timing") or {}
        rows.append({
            "path": str(path.resolve()), "project": value.get("project"),
            "status": value.get("status"),
            "physical_board_evidence": bool(value.get("physical_board_evidence")),
            "bit_sha256": (value.get("overlay") or {}).get("bit_sha256"),
            "median_seconds": timing.get("median_seconds"),
            "p95_seconds": timing.get("p95_seconds"),
        })
    medians = [float(row["median_seconds"]) for row in rows if row["median_seconds"] is not None]
    board = [row for row in rows if row["status"] == "PASS_BOARD"]
    summary = {
        "schema": "pynq-overlay-validation-summary-v1",
        "report_count": len(rows), "pass_board_count": len(board),
        "pass_mock_count": sum(row["status"] == "PASS_MOCK" for row in rows),
        "failure_count": sum(row["status"] == "FAIL" for row in rows),
        "all_same_bitstream": len({row["bit_sha256"] for row in rows if row["bit_sha256"]}) <= 1,
        "median_of_run_medians_seconds": None if not medians else statistics.median(medians),
        "rows": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md = ["# PYNQ Overlay validation summary", "",
          f"- Reports: `{summary['report_count']}`",
          f"- PASS_BOARD: `{summary['pass_board_count']}`",
          f"- PASS_MOCK: `{summary['pass_mock_count']}`",
          f"- Failures: `{summary['failure_count']}`",
          f"- Same bitstream: `{str(summary['all_same_bitstream']).lower()}`",
          f"- Median of run medians: `{summary['median_of_run_medians_seconds']}` s", "",
          "| Project | Status | Physical | Median seconds | Report |", "|---|---|---:|---:|---|"]
    for row in rows:
        md.append(f"| `{row['project']}` | {row['status']} | {row['physical_board_evidence']} | "
                  f"{row['median_seconds']} | `{row['path']}` |")
    args.output.with_suffix(".md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in summary.items() if key != "rows"},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
