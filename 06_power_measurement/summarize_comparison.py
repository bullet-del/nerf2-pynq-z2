#!/usr/bin/env python3
"""Combine PC NVIDIA and PYNQ ARM/FPGA power summaries into one table."""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


SCENES = ("s23", "Meetingroom", "Office1", "Office2")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pc-results", type=Path, required=True)
    parser.add_argument("--board-summary", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=Path("stage39_power_comparison.csv"))
    args = parser.parse_args()

    pc_by_scene = {}
    for path in args.pc_results.rglob("summary.json"):
        value = json.loads(path.read_text(encoding="utf-8"))
        if value.get("schema") != "stage39-pc-gpu-power-benchmark-v2":
            continue
        validity = value.get("validity", {})
        if not (
            validity.get("full_test_set_measured")
            and validity.get("every_selected_test_map_measured_every_repeat")
            and validity.get("all_maps_have_power_samples")
            and validity.get("deterministic_output_hashes_across_repeats")
        ):
            raise ValueError(f"PC summary is not a valid exhaustive run: {path}")
        scene = value["scene"]
        if scene in pc_by_scene:
            raise ValueError(f"multiple PC summaries for {scene}; move superseded runs out of {args.pc_results}")
        pc_by_scene[scene] = value
    missing = sorted(set(SCENES) - set(pc_by_scene))
    if missing:
        raise ValueError(f"missing PC summaries: {missing}")

    board = json.loads(args.board_summary.read_text(encoding="utf-8"))
    if board.get("schema") != "stage39-board-power-comparison-v1":
        raise ValueError("unexpected board summary schema")
    board_rows = {
        (row["scene"], row["platform"]): row
        for row in board["aggregates"]
        if "platform" in row
    }

    rows = []
    for scene in SCENES:
        pc = pc_by_scene[scene]
        agg = pc["aggregate"]
        rows.append(
            {
                "scene": scene,
                "platform": "PC_RTX4050_FP32",
                "power_boundary": "NVIDIA driver GPU power domain",
                "boundary_group": "PC_GPU_DOMAIN",
                "repeats": len(pc["repeats"]),
                "maps_per_repeat": pc["workload"]["maps_per_repeat"],
                "median_seconds_per_map": agg["median_seconds_per_map"],
                "median_active_power_w": agg["median_active_power_w"],
                "median_total_energy_per_map_j": agg["median_gpu_energy_per_map_j"],
                "median_dynamic_energy_per_map_j": agg["median_dynamic_gpu_energy_per_map_j"],
            }
        )
        for platform in ("PYNQ_ARM", "PYNQ_FPGA"):
            item = board_rows[(scene, platform)]
            rows.append(
                {
                    "scene": scene,
                    "platform": platform + "_INT8_Q15",
                    "power_boundary": "complete PYNQ-Z2 5V J8 board input",
                    "boundary_group": "PYNQ_BOARD_INPUT",
                    "repeats": item["repeats"],
                    "maps_per_repeat": "see board raw rows",
                    "median_seconds_per_map": item["median_seconds_per_map"],
                    "median_active_power_w": item["median_active_power_w"],
                    "median_total_energy_per_map_j": item["median_total_energy_per_map_j"],
                    "median_dynamic_energy_per_map_j": item["median_dynamic_energy_per_map_j"],
                }
            )

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    claims = []
    for scene in SCENES:
        arm = board_rows[(scene, "PYNQ_ARM")]
        fpga = board_rows[(scene, "PYNQ_FPGA")]
        pc = pc_by_scene[scene]["aggregate"]
        claims.append(
            {
                "scene": scene,
                "primary_same_boundary": {
                    "fpga_speedup_over_arm_x": arm["median_seconds_per_map"] / fpga["median_seconds_per_map"],
                    "fpga_total_energy_reduction_over_arm_x": arm["median_total_energy_per_map_j"] / fpga["median_total_energy_per_map_j"],
                    "validity": "same PYNQ-Z2 board-input boundary; suitable as primary energy-efficiency claim",
                },
                "descriptive_cross_boundary": {
                    "fpga_board_j_per_map_divided_by_pc_gpu_j_per_map": fpga["median_total_energy_per_map_j"] / pc["median_gpu_energy_per_map_j"],
                    "validity": "different power boundaries and precision; report descriptively, not as a whole-system efficiency proof",
                },
            }
        )
    args.out.with_suffix(".json").write_text(json.dumps({"rows": rows, "claims": claims}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"PASS: {args.out}")
    print(f"PASS: {args.out.with_suffix('.json')}")


if __name__ == "__main__":
    main()
