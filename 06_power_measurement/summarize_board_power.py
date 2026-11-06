#!/usr/bin/env python3
"""Validate FNB58 board runs and calculate total/dynamic J per map."""
from __future__ import annotations

import argparse
import csv
import json
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


SCENES = ("s23", "Meetingroom", "Office1", "Office2")
PLATFORMS = ("PYNQ_ARM", "PYNQ_FPGA")
EXPECTED_MAPS = {"s23": 1225, "Meetingroom": 200, "Office1": 200, "Office2": 100}


def number(row, key, required=False):
    value = str(row.get(key, "")).strip()
    if not value:
        if required:
            raise ValueError(f"row {row['_line']}: {key} is required")
        return None
    result = float(value)
    if result < 0:
        raise ValueError(f"row {row['_line']}: {key} cannot be negative")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", type=Path)
    parser.add_argument("--out", type=Path, default=Path("board_power_summary.json"))
    parser.add_argument("--platforms", nargs="+", choices=PLATFORMS, default=["PYNQ_FPGA"])
    args = parser.parse_args()
    platforms = tuple(dict.fromkeys(args.platforms))

    with args.csv.open("r", encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    if not rows:
        raise SystemExit("input CSV has no rows")
    groups = defaultdict(list)
    normalized = []
    for line, row in enumerate(rows, 2):
        row["_line"] = line
        scene = row["scene"].strip()
        platform = row["platform"].strip()
        state = row["state"].strip().lower()
        if scene not in SCENES or platform not in PLATFORMS or state not in {"idle", "active"}:
            raise ValueError(f"row {line}: invalid scene/platform/state")
        if platform not in platforms: continue
        duration = number(row, "duration_s", required=True)
        if duration <= 0:
            raise ValueError(f"row {line}: duration_s must be positive")
        energy_wh = number(row, "energy_wh")
        avg_power = number(row, "avg_power_w")
        voltage = number(row, "avg_voltage_v")
        current = number(row, "avg_current_a")
        if energy_wh is None and avg_power is None:
            raise ValueError(f"row {line}: fill energy_wh or avg_power_w")
        measured_energy = energy_wh * 3600.0 if energy_wh is not None else avg_power * duration
        measured_power = measured_energy / duration
        if avg_power is not None and abs(avg_power - measured_power) > max(0.15, measured_power * 0.12):
            raise ValueError(f"row {line}: energy_wh and avg_power_w disagree")
        if voltage is not None and current is not None:
            vi_power = voltage * current
            if abs(vi_power - measured_power) > max(0.20, measured_power * 0.15):
                raise ValueError(f"row {line}: voltage*current and measured power disagree")
        maps = int(number(row, "maps", required=True))
        if state == "active" and maps <= 0:
            raise ValueError(f"row {line}: active run needs maps > 0")
        expected_maps = EXPECTED_MAPS[scene] if state == "active" else 0
        if maps != expected_maps:
            raise ValueError(
                f"row {line}: {scene}/{state} maps must be {expected_maps}, got {maps}"
            )
        instrument = row.get("instrument", "").strip()
        source_file = row.get("source_file", "").strip()
        start_utc = row.get("start_utc", "").strip()
        end_utc = row.get("end_utc", "").strip()
        if "FNB58" not in instrument.upper():
            raise ValueError(f"row {line}: instrument must identify the FNIRSI FNB58")
        if not source_file:
            raise ValueError(f"row {line}: source_file is required for auditability")
        if not start_utc or not end_utc:
            raise ValueError(f"row {line}: start_utc and end_utc are required")
        parsed_start = datetime.fromisoformat(start_utc.replace("Z", "+00:00"))
        parsed_end = datetime.fromisoformat(end_utc.replace("Z", "+00:00"))
        utc_duration = (parsed_end - parsed_start).total_seconds()
        if utc_duration <= 0 or abs(utc_duration - duration) > max(2.0, duration * 0.02):
            raise ValueError(
                f"row {line}: UTC interval ({utc_duration:.3f}s) and duration_s ({duration:.3f}s) disagree"
            )
        item = {
            "scene": scene,
            "platform": platform,
            "state": state,
            "repeat": int(number(row, "repeat", required=True)),
            "maps": maps,
            "duration_s": duration,
            "measured_energy_j": measured_energy,
            "measured_power_w": measured_power,
            "instrument": instrument,
            "start_utc": start_utc,
            "end_utc": end_utc,
            "source_file": source_file,
            "notes": row.get("notes", ""),
        }
        normalized.append(item)
        groups[(scene, platform, state)].append(item)

    results = []
    for scene in SCENES:
        for platform in platforms:
            idle = groups[(scene, platform, "idle")]
            active = groups[(scene, platform, "active")]
            if len(idle) != 3 or len(active) != 3:
                raise ValueError(f"{scene}/{platform} needs exactly 3 idle and 3 active rows")
            for state_name, subset in (("idle", idle), ("active", active)):
                repeats = [item["repeat"] for item in subset]
                if sorted(repeats) != [1, 2, 3]:
                    raise ValueError(
                        f"{scene}/{platform}/{state_name} repeats must be exactly 1,2,3"
                    )
            idle_power = statistics.median(item["measured_power_w"] for item in idle)
            for item in active:
                dynamic_j = max(0.0, item["measured_energy_j"] - idle_power * item["duration_s"])
                results.append(
                    {
                        **item,
                        "idle_power_median_w": idle_power,
                        "seconds_per_map": item["duration_s"] / item["maps"],
                        "total_energy_per_map_j": item["measured_energy_j"] / item["maps"],
                        "dynamic_energy_per_map_j": dynamic_j / item["maps"],
                    }
                )

    aggregates = []
    for scene in SCENES:
        by_platform = {}
        for platform in platforms:
            subset = [r for r in results if r["scene"] == scene and r["platform"] == platform]
            agg = {
                "scene": scene,
                "platform": platform,
                "repeats": len(subset),
                "median_seconds_per_map": statistics.median(r["seconds_per_map"] for r in subset),
                "median_active_power_w": statistics.median(r["measured_power_w"] for r in subset),
                "median_idle_power_w": statistics.median(r["idle_power_median_w"] for r in subset),
                "median_total_energy_per_map_j": statistics.median(r["total_energy_per_map_j"] for r in subset),
                "median_dynamic_energy_per_map_j": statistics.median(r["dynamic_energy_per_map_j"] for r in subset),
            }
            aggregates.append(agg)
            by_platform[platform] = agg
        if set(platforms) != set(PLATFORMS): continue
        arm, fpga = by_platform["PYNQ_ARM"], by_platform["PYNQ_FPGA"]
        aggregates.append(
            {
                "scene": scene,
                "comparison": "PYNQ_ARM_vs_PYNQ_FPGA_same_board_input_boundary",
                "fpga_speedup_x": arm["median_seconds_per_map"] / fpga["median_seconds_per_map"],
                "fpga_total_energy_reduction_x": arm["median_total_energy_per_map_j"] / fpga["median_total_energy_per_map_j"],
                "fpga_dynamic_energy_reduction_x": arm["median_dynamic_energy_per_map_j"] / fpga["median_dynamic_energy_per_map_j"] if fpga["median_dynamic_energy_per_map_j"] > 0 else None,
            }
        )

    output = {
        "schema": "stage39-board-power-comparison-v1",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "power_boundary": "complete PYNQ-Z2 5 V Micro-USB J8 board input measured by FNIRSI FNB58",
        "platforms": list(platforms),
        "rows": results,
        "aggregates": aggregates,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(output, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    csv_out = args.out.with_suffix(".csv")
    with csv_out.open("w", newline="", encoding="utf-8-sig") as stream:
        flat = [row for row in aggregates if "platform" in row]
        writer = csv.DictWriter(stream, fieldnames=list(flat[0]))
        writer.writeheader()
        writer.writerows(flat)
    print(f"PASS: {args.out}")
    print(f"PASS: {csv_out}")


if __name__ == "__main__":
    main()
