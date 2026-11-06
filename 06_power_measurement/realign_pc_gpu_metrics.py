#!/usr/bin/env python3
"""Realign existing Stage39 PC results to the exact per-map inference windows.

The exhaustive runs already contain every map's measured duration and energy.
This migration makes the sum of those per-map inference windows the primary
scene metric.  It preserves the original continuous-sweep metric as secondary
evidence; no measurement is synthesized and no inference is rerun.
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics
from collections import defaultdict
from pathlib import Path


SCENES = ("s23", "Meetingroom", "Office1", "Office2")


def load_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as stream:
        return list(csv.DictReader(stream))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-root", type=Path, required=True)
    args = parser.parse_args()
    root = args.results_root.resolve()

    summaries: dict[str, Path] = {}
    for path in root.rglob("summary.json"):
        value = json.loads(path.read_text(encoding="utf-8"))
        scene = value.get("scene")
        if value.get("schema") == "stage39-pc-gpu-power-benchmark-v2" and scene in SCENES:
            if scene in summaries:
                raise ValueError(f"duplicate {scene} summary")
            summaries[scene] = path
    if set(summaries) != set(SCENES):
        raise ValueError(f"expected {SCENES}; found {tuple(summaries)}")

    report: dict[str, object] = {
        "status": "PASS",
        "method": "sum measured per-map inference-window energy within each repeat",
        "scenes": {},
    }
    for scene in SCENES:
        summary_path = summaries[scene]
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        rows = load_rows(summary_path.parent / "per_map_runs.csv")
        grouped: dict[int, list[dict[str, str]]] = defaultdict(list)
        for row in rows:
            grouped[int(row["repeat"])].append(row)
        expected = int(summary["workload"]["maps_per_repeat"])
        repeat_by_id = {int(value["repeat"]): value for value in summary["repeats"]}
        if set(grouped) != set(repeat_by_id):
            raise AssertionError(f"{scene}: repeat mismatch")

        for repeat_id in sorted(grouped):
            repeat_rows = grouped[repeat_id]
            if len(repeat_rows) != expected:
                raise AssertionError(
                    f"{scene} repeat {repeat_id}: {len(repeat_rows)} rows, expected {expected}"
                )
            result = repeat_by_id[repeat_id]
            if "continuous_sweep_power" not in result:
                result["continuous_sweep_power"] = result["power"]
                result["continuous_sweep_total_gpu_energy_j"] = result["total_gpu_energy_j"]
                result["continuous_sweep_gpu_energy_per_map_j"] = result["gpu_energy_per_map_j"]
                result["continuous_sweep_dynamic_gpu_energy_j"] = result["dynamic_gpu_energy_j"]
                result["continuous_sweep_dynamic_gpu_energy_per_map_j"] = result[
                    "dynamic_gpu_energy_per_map_j"
                ]
            duration = sum(float(row["duration_s"]) for row in repeat_rows)
            total_energy = sum(float(row["total_gpu_energy_j"]) for row in repeat_rows)
            dynamic_energy = sum(float(row["dynamic_gpu_energy_j"]) for row in repeat_rows)
            if abs(duration - float(result["duration_s"])) > 1e-6:
                raise AssertionError(f"{scene} repeat {repeat_id}: duration mismatch")
            result["map_interval_active_power_w"] = total_energy / duration
            result["total_gpu_energy_j"] = total_energy
            result["gpu_energy_per_map_j"] = total_energy / expected
            result["dynamic_gpu_energy_j"] = dynamic_energy
            result["dynamic_gpu_energy_per_map_j"] = dynamic_energy / expected

        workload = summary["workload"]
        workload.pop("included", None)
        workload.pop("excluded", None)
        workload.update(
            {
                "primary_per_map_interval_included": [
                    "CPU-to-GPU transfer",
                    "NeRF2 FP32 render_ss",
                    "GPU synchronization",
                    "GPU-to-CPU output transfer",
                ],
                "secondary_continuous_sweep_additionally_includes": [
                    "short host input-construction gaps between maps"
                ],
                "excluded_from_primary_per_map_interval": [
                    "host input construction",
                    "model loading",
                    "SSIM",
                    "plotting",
                    "PNG/file output",
                ],
                "excluded_from_both_primary_and_secondary": [
                    "model loading",
                    "SSIM",
                    "plotting",
                    "PNG/file output",
                ],
            }
        )
        repeats = summary["repeats"]
        summary["metric_alignment"] = {
            "status": "REALIGNED_FROM_EXISTING_PER_MAP_MEASUREMENTS",
            "primary": "sum of measured per-map inference windows",
            "secondary": "original continuous sweep including inter-map input construction gaps",
            "rerun_required": False,
        }
        summary["aggregate"] = {
            "median_seconds_per_map": statistics.median(r["seconds_per_map"] for r in repeats),
            "median_kernel_seconds_per_map": statistics.median(
                r["kernel_seconds_per_map"] for r in repeats
            ),
            "median_active_power_w": statistics.median(
                r["map_interval_active_power_w"] for r in repeats
            ),
            "median_gpu_energy_per_map_j": statistics.median(
                r["gpu_energy_per_map_j"] for r in repeats
            ),
            "median_dynamic_gpu_energy_per_map_j": statistics.median(
                r["dynamic_gpu_energy_per_map_j"] for r in repeats
            ),
            "median_continuous_sweep_active_power_w": statistics.median(
                r["continuous_sweep_power"]["instant_power_mean_w"] for r in repeats
            ),
            "median_continuous_sweep_gpu_energy_per_map_j": statistics.median(
                r["continuous_sweep_gpu_energy_per_map_j"] for r in repeats
            ),
            "median_continuous_sweep_dynamic_gpu_energy_per_map_j": statistics.median(
                r["continuous_sweep_dynamic_gpu_energy_per_map_j"] for r in repeats
            ),
        }
        summary_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
            encoding="utf-8",
        )

        runs_path = summary_path.parent / "runs.csv"
        fields = [
            "scene",
            "repeat",
            "maps",
            "duration_s",
            "seconds_per_map",
            "kernel_seconds_per_map",
            "idle_power_w",
            "active_power_w",
            "continuous_sweep_active_power_w",
            "gpu_energy_per_map_j",
            "continuous_sweep_gpu_energy_per_map_j",
            "dynamic_gpu_energy_per_map_j",
            "power_samples",
            "full_test_set",
        ]
        with runs_path.open("w", newline="", encoding="utf-8-sig") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            for result in repeats:
                writer.writerow(
                    {
                        "scene": scene,
                        "repeat": result["repeat"],
                        "maps": result["maps"],
                        "duration_s": result["duration_s"],
                        "seconds_per_map": result["seconds_per_map"],
                        "kernel_seconds_per_map": result["kernel_seconds_per_map"],
                        "idle_power_w": summary["idle"]["instant_power_mean_w"],
                        "active_power_w": result["map_interval_active_power_w"],
                        "continuous_sweep_active_power_w": result["continuous_sweep_power"][
                            "instant_power_mean_w"
                        ],
                        "gpu_energy_per_map_j": result["gpu_energy_per_map_j"],
                        "continuous_sweep_gpu_energy_per_map_j": result[
                            "continuous_sweep_gpu_energy_per_map_j"
                        ],
                        "dynamic_gpu_energy_per_map_j": result[
                            "dynamic_gpu_energy_per_map_j"
                        ],
                        "power_samples": result["power"]["sample_count"],
                        "full_test_set": summary["workload"]["exact_full_test_set"],
                    }
                )
        aggregate = summary["aggregate"]
        continuous_energy = aggregate["median_continuous_sweep_gpu_energy_per_map_j"]
        primary_energy = aggregate["median_gpu_energy_per_map_j"]
        report["scenes"][scene] = {
            **aggregate,
            "primary_minus_continuous_energy_j_per_map": primary_energy
            - continuous_energy,
            "primary_minus_continuous_energy_percent": 100.0
            * (primary_energy - continuous_energy)
            / continuous_energy,
        }

    report_path = root / "pc_gpu_metric_realign_report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
