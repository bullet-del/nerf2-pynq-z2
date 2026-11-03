#!/usr/bin/env python3
"""Stage39.3 physical timing and board-input power evidence gate.

This script deliberately refuses estimates.  It accepts only the board-generated
50-run benchmark JSON and user-entered external meter observations.
"""
from __future__ import annotations
import csv, json, statistics
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "work_stage39"
BOARD = ROOT / "outputs/N2-NeRF2-Stage39-board"
SCENES = ("s23", "Meetingroom", "Office1", "Office2")
POWER = WORK / "measurements/stage39_board_input_power.csv"
OUT = WORK / "reports/stage39_3_gate.json"


def benchmark_path(scene: str) -> Path:
    return BOARD / "scenes" / scene / "evidence/stage39/benchmark_50.json"


def power_rows():
    if not POWER.is_file():
        return []
    with POWER.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def main() -> None:
    checks, scenes = [], []
    for scene in SCENES:
        path = benchmark_path(scene)
        if not path.is_file():
            scenes.append({"scene": scene, "physical_benchmark": "PENDING"})
            continue
        value = json.loads(path.read_text(encoding="utf-8"))
        valid = (value.get("schema") == "n2-stage39-physical-latency-stability-v1"
                 and value.get("repeats") == 50
                 and value.get("stable_raw_q15_hash") is True
                 and value.get("matches_portable_integer") is True)
        scenes.append({"scene": scene, "physical_benchmark": "PASS" if valid else "FAIL",
                       "median_seconds": value.get("median_seconds"),
                       "p95_seconds": value.get("p95_seconds"),
                       "median_under_1p2": value.get("median_under_1p2"),
                       "p95_under_1p5": value.get("p95_under_1p5"),
                       "stable_raw_q15_hash": value.get("stable_raw_q15_hash"),
                       "matches_portable_integer": value.get("matches_portable_integer")})
    complete = all(row["physical_benchmark"] != "PENDING" for row in scenes)
    timing_pass = complete and all(row["physical_benchmark"] == "PASS"
        and row["median_under_1p2"] and row["p95_under_1p5"] for row in scenes)
    checks.append({"id": "39.3.fifty_runs_each_scene", "required": True,
                   "status": "PASS" if complete else "PENDING"})
    checks.append({"id": "39.3.median_le_1p2_p95_le_1p5", "required": True,
                   "status": "PASS" if timing_pass else ("FAIL" if complete else "PENDING")})

    rows = power_rows(); power_summary = {}
    power_ok = True
    for scene in SCENES:
        power_summary[scene] = {}
        for state in ("idle", "inference"):
            values = []
            for row in rows:
                if row.get("scene") == scene and row.get("state") == state:
                    try:
                        voltage, current = float(row["voltage_v"]), float(row["current_a"])
                        value = float(row.get("power_w") or voltage * current)
                        if voltage <= 0 or current < 0 or value < 0: raise ValueError
                        values.append(value)
                    except (KeyError, ValueError):
                        power_ok = False
            if len(values) < 10: power_ok = False
            power_summary[scene][state] = {"samples": len(values),
                "median_w": statistics.median(values) if values else None,
                "mean_w": statistics.fmean(values) if values else None}
        if power_summary[scene]["idle"]["median_w"] is not None and power_summary[scene]["inference"]["median_w"] is not None:
            power_summary[scene]["dynamic_median_w"] = (
                power_summary[scene]["inference"]["median_w"]-
                power_summary[scene]["idle"]["median_w"])
    checks.append({"id": "39.3.external_board_input_power_10x_idle_10x_active_each_scene",
                   "required": True, "status": "PASS" if power_ok else "PENDING",
                   "reason": None if power_ok else "Fill the supplied CSV from a real USB/DC inline power meter"})
    report = {"schema": "n2-stage39-timing-power-gate-v1", "stage": "Stage39.3",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "status": "PASS" if all(x["status"] == "PASS" for x in checks) else
                  ("FAIL" if any(x["status"] == "FAIL" for x in checks) else "PENDING_PHYSICAL_MEASUREMENT"),
        "timing_scope": "FPGA full 90x360 map, 32400 rays, 64 samples/ray; excludes HDMI and HTTP",
        "power_scope": "whole PYNQ-Z2 board DC input measured externally; no estimated or Vivado-only power accepted",
        "checks": checks, "scenes": scenes, "power": power_summary}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "report": str(OUT)}, ensure_ascii=False))


if __name__ == "__main__": main()
