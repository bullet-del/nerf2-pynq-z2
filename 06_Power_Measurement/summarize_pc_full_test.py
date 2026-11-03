#!/usr/bin/env python3
"""Validate and summarize the four authoritative exhaustive PC GPU runs."""
from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path


SCENES = ("s23", "Meetingroom", "Office1", "Office2")


def percentile(values: list[float], q: float) -> float:
    values = sorted(values)
    if len(values) == 1:
        return values[0]
    position = (len(values) - 1) * q
    lower = int(position)
    upper = min(lower + 1, len(values) - 1)
    fraction = position - lower
    return values[lower] * (1.0 - fraction) + values[upper] * fraction


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path)
    args = parser.parse_args()
    root = args.results_root.resolve()
    output = (args.output_root or root).resolve()
    output.mkdir(parents=True, exist_ok=True)

    found: dict[str, tuple[Path, dict]] = {}
    for path in root.rglob("summary.json"):
        value = json.loads(path.read_text(encoding="utf-8"))
        if value.get("schema") != "stage39-pc-gpu-power-benchmark-v2":
            continue
        scene = value.get("scene")
        if scene in found:
            raise ValueError(f"multiple formal summaries for {scene}: {found[scene][0]} and {path}")
        found[scene] = (path, value)
    missing = sorted(set(SCENES) - set(found))
    if missing:
        raise ValueError(f"missing formal full-test summaries: {missing}")

    rows = []
    for scene in SCENES:
        summary_path, value = found[scene]
        validity = value["validity"]
        required = (
            "full_test_set_measured",
            "every_selected_test_map_measured_every_repeat",
            "all_maps_have_power_samples",
            "deterministic_output_hashes_across_repeats",
        )
        if not all(validity.get(key) is True for key in required):
            raise AssertionError(f"{scene}: formal validity failed: {validity}")
        per_map_path = summary_path.parent / "per_map_runs.csv"
        with per_map_path.open("r", newline="", encoding="utf-8-sig") as stream:
            per_map = list(csv.DictReader(stream))
        expected_rows = int(value["workload"]["total_test_maps"]) * len(value["repeats"])
        if len(per_map) != expected_rows:
            raise AssertionError(f"{scene}: {len(per_map)} per-map rows, expected {expected_rows}")
        if value.get("metric_alignment", {}).get("status") != "REALIGNED_FROM_EXISTING_PER_MAP_MEASUREMENTS":
            raise AssertionError(f"{scene}: primary metric boundary has not been aligned")
        expected_ids = [str(item) for item in value["workload"]["spectrum_ids"]]
        by_repeat: dict[int, list[dict[str, str]]] = defaultdict(list)
        for row in per_map:
            by_repeat[int(row["repeat"])].append(row)
        repeat_by_id = {int(item["repeat"]): item for item in value["repeats"]}
        if set(by_repeat) != set(repeat_by_id):
            raise AssertionError(f"{scene}: per-map repeat IDs do not match summary")
        hashes: dict[str, set[str]] = defaultdict(set)
        for repeat_id, repeat_rows in sorted(by_repeat.items()):
            repeat_rows.sort(key=lambda row: int(row["test_ordinal"]))
            if [row["spectrum_id"] for row in repeat_rows] != expected_ids:
                raise AssertionError(f"{scene} repeat {repeat_id}: test_index order mismatch")
            if any(int(row["power_samples"]) < 1 for row in repeat_rows):
                raise AssertionError(f"{scene} repeat {repeat_id}: map without a power sample")
            duration = sum(float(row["duration_s"]) for row in repeat_rows)
            energy = sum(float(row["total_gpu_energy_j"]) for row in repeat_rows)
            dynamic = sum(float(row["dynamic_gpu_energy_j"]) for row in repeat_rows)
            saved = repeat_by_id[repeat_id]
            checks = (
                (duration, float(saved["duration_s"]), "duration"),
                (energy, float(saved["total_gpu_energy_j"]), "total energy"),
                (dynamic, float(saved["dynamic_gpu_energy_j"]), "dynamic energy"),
                (energy / duration, float(saved["map_interval_active_power_w"]), "active power"),
            )
            for actual, expected_value, label in checks:
                if not math.isclose(actual, expected_value, rel_tol=1e-12, abs_tol=1e-9):
                    raise AssertionError(
                        f"{scene} repeat {repeat_id}: {label} mismatch "
                        f"({actual} != {expected_value})"
                    )
            for row in repeat_rows:
                hashes[row["spectrum_id"]].add(row["output_sha256"])
        if set(hashes) != set(expected_ids) or any(len(items) != 1 for items in hashes.values()):
            raise AssertionError(f"{scene}: output hashes are incomplete or nondeterministic")
        durations = [float(row["duration_s"]) for row in per_map]
        powers = [float(row["active_power_w"]) for row in per_map]
        energies = [float(row["total_gpu_energy_j"]) for row in per_map]
        aggregate = value["aggregate"]
        aggregate_checks = (
            (
                statistics.median(r["map_interval_active_power_w"] for r in value["repeats"]),
                aggregate["median_active_power_w"],
                "median active power",
            ),
            (
                statistics.median(r["gpu_energy_per_map_j"] for r in value["repeats"]),
                aggregate["median_gpu_energy_per_map_j"],
                "median total energy",
            ),
            (
                statistics.median(
                    r["dynamic_gpu_energy_per_map_j"] for r in value["repeats"]
                ),
                aggregate["median_dynamic_gpu_energy_per_map_j"],
                "median dynamic energy",
            ),
        )
        for actual, expected_value, label in aggregate_checks:
            if not math.isclose(actual, expected_value, rel_tol=1e-12, abs_tol=1e-9):
                raise AssertionError(f"{scene}: {label} mismatch")
        rows.append(
            {
                "scene": scene,
                "test_maps": value["workload"]["total_test_maps"],
                "repeats": len(value["repeats"]),
                "measured_map_inferences": len(per_map),
                "checkpoint": value["provenance"]["checkpoint"],
                "checkpoint_sha256": value["provenance"]["checkpoint_sha256"],
                "idle_power_w": value["idle"]["instant_power_mean_w"],
                "median_seconds_per_map": aggregate["median_seconds_per_map"],
                "median_active_power_w": aggregate["median_active_power_w"],
                "median_total_energy_per_map_j": aggregate["median_gpu_energy_per_map_j"],
                "median_dynamic_energy_per_map_j": aggregate[
                    "median_dynamic_gpu_energy_per_map_j"
                ],
                "per_map_duration_p05_s": percentile(durations, 0.05),
                "per_map_duration_p50_s": statistics.median(durations),
                "per_map_duration_p95_s": percentile(durations, 0.95),
                "per_map_power_p05_w": percentile(powers, 0.05),
                "per_map_power_p50_w": statistics.median(powers),
                "per_map_power_p95_w": percentile(powers, 0.95),
                "per_map_energy_p05_j": percentile(energies, 0.05),
                "per_map_energy_p50_j": statistics.median(energies),
                "per_map_energy_p95_j": percentile(energies, 0.95),
                "minimum_power_samples_per_map": validity["minimum_power_samples_per_map"],
                "formal_validity": "PASS",
                "result_directory": str(summary_path.parent),
            }
        )

    csv_path = output / "pc_gpu_full_test_summary.csv"
    with csv_path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    report = {
        "schema": "stage39-pc-gpu-exhaustive-four-scene-summary-v1",
        "status": "PASS",
        "power_boundary": "NVIDIA driver-reported GPU power domain",
        "primary_metric_boundary": "sum of measured per-map inference windows; excludes host input construction",
        "secondary_metric_boundary": "continuous scene sweep; includes short inter-map input-construction gaps",
        "total_unique_test_maps": sum(int(row["test_maps"]) for row in rows),
        "total_measured_map_inferences": sum(row["measured_map_inferences"] for row in rows),
        "rows": rows,
    }
    json_path = output / "pc_gpu_full_test_summary.json"
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    md_lines = [
        "# Stage39 PC GPU 四场景完整测试集功耗结果",
        "",
        "结论：**PASS**。四个场景均按原始 `test_index.txt` 全量测量 3 次；每张图都有功率样本，且同一图三次输出 SHA-256 一致。",
        "",
        "功率边界：NVIDIA 驱动报告的 RTX 4050 Laptop GPU 功率域，不代表整台电脑墙插功耗。",
        "",
        "| 场景 | test 图数 | 实际推理数 | s/图 | Active W | 总 J/图 | 动态 J/图 | 最少样本/图 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        md_lines.append(
            f"| {row['scene']} | {row['test_maps']} | {row['measured_map_inferences']} | "
            f"{row['median_seconds_per_map']:.6f} | {row['median_active_power_w']:.3f} | "
            f"{row['median_total_energy_per_map_j']:.3f} | "
            f"{row['median_dynamic_energy_per_map_j']:.3f} | "
            f"{row['minimum_power_samples_per_map']} |"
        )
    md_lines += [
        "",
        f"唯一 test 图总数：{report['total_unique_test_maps']}；三轮实际完整图推理总数：{report['total_measured_map_inferences']}。",
        "",
        "场景级主指标按每轮所有逐图推理窗口的实测能量求和，再取三轮中位数；每个逐图窗口包含 CPU→GPU、NeRF2 渲染、GPU 同步和 GPU→CPU，不含输入构造、模型加载、SSIM、绘图及文件输出。原连续 sweep（额外包含图间输入构造间隙）保留在场景 `summary.json` 作为次级证据。",
    ]
    md_path = output / "PC_GPU四场景完整测试集功耗结果.md"
    md_path.write_text("\n".join(md_lines) + "\n", encoding="utf-8")
    print(json.dumps({"status": "PASS", "csv": str(csv_path), "json": str(json_path), "markdown": str(md_path)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
