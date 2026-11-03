from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from common import DEFAULT_CONFIG, KIT_ROOT, PC_PACKAGE, SCENES, load_config, utc_now, write_json


def sparse_macs(runtime_model: Path) -> dict[int, int]:
    totals: dict[int, int] = {}
    with np.load(runtime_model, allow_pickle=False) as data:
        for raw in data["descriptors_u64"]:
            value = int(raw); layer = value & 15; count = (value >> 6) & 511
            totals[layer] = totals.get(layer, 0) + count
    return totals


def main() -> None:
    parser = argparse.ArgumentParser(description="Build Stage39 accuracy and fixed-Rx ablation from fresh outputs")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--profile", choices=("full", "smoke"), default="full",
                        help="full is the formal 1,725-map run; smoke is only for pipeline validation")
    args = parser.parse_args(); config = load_config(args.config)
    profile_root = KIT_ROOT / "work" / args.profile
    report_dir = profile_root / "reports"; report_dir.mkdir(parents=True, exist_ok=True)
    rows, cache_rows, evaluations = [], [], {}
    for scene in SCENES:
        qr = profile_root / "qat" / scene
        value = json.loads((qr / "full_evaluation.json").read_text(encoding="utf-8")); evaluations[scene] = value
        ptq = json.loads((qr / "ptq_full_evaluation.json").read_text(encoding="utf-8"))
        runtime = profile_root / "runtime" / scene
        package = json.loads((runtime / "package_manifest.json").read_text(encoding="utf-8"))
        original = value["pc_fp32"]["summary"]["ssim"]["mean"]
        qat = value["hardware_integer"]["summary"]["ssim"]["mean"]
        ptq_mean = ptq["hardware_integer"]["summary"]["ssim"]["mean"]
        rows.append({"scene": scene, "test_maps": value["maps"],
            "factorized_pc_fp32": original, "train_only_ptq": ptq_mean,
            "hardware_exact_qat_integer": qat, "qat_gain_over_ptq": qat-ptq_mean,
            "pc_minus_qat_gap": original-qat, "weight_words": package["weight_words"],
            "weight_capacity": package["weight_capacity"],
            "fits_one_unmodified_bitstream": package["fits_unchanged_bitstream"]})
        counts = sparse_macs(runtime / "model/stage37_runtime_model.npz")
        attenuation_per_sample = sum(counts.get(i, 0) for i in (5, 6, 7, 8))
        signal_per_ray = sum(counts.get(i, 0) for i in (10, 11, 12))
        maps = int(value["maps"]); rays = 32400; samples = 64
        attenuation_map = attenuation_per_sample * rays * samples
        signal_map = signal_per_ray * rays
        uncached = maps * (attenuation_map + signal_map)
        cached = attenuation_map + maps * signal_map
        cache_rows.append({"scene": scene, "test_maps": maps,
            "attenuation_sparse_macs_per_sample": attenuation_per_sample,
            "signal_sparse_macs_per_ray": signal_per_ray,
            "no_fixed_rx_cache_sparse_macs": uncached,
            "fixed_rx_cache_sparse_macs": cached,
            "source_derived_sparse_mac_reduction_percent": 100.0*(1.0-cached/uncached),
            "source_derived_sparse_mac_speedup": uncached/cached})
    with (report_dir / "accuracy_ablation.csv").open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)
    with (report_dir / "fixed_rx_cache_ablation.csv").open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(cache_rows[0])); writer.writeheader(); writer.writerows(cache_rows)
    x = np.arange(len(rows)); width = .25
    fig, axis = plt.subplots(figsize=(11, 6), constrained_layout=True)
    for offset, (label, key, color) in enumerate((
        ("Factorized PC FP32", "factorized_pc_fp32", "#1769aa"),
        ("Train-only PTQ", "train_only_ptq", "#d97706"),
        ("Hardware-exact QAT", "hardware_exact_qat_integer", "#079c8c"))):
        values = [row[key] for row in rows]
        axis.bar(x + (offset-1)*width, values, width, label=label, color=color)
    axis.set_xticks(x, SCENES); axis.set_ylabel("Mean SSIM vs measured GT")
    total_maps = sum(row["test_maps"] for row in rows)
    run_label = "complete held-out test" if args.profile == "full" else "pipeline smoke test"
    axis.set_title(f"Stage39 {run_label} accuracy ablation ({total_maps:,} maps)")
    axis.grid(axis="y", alpha=.22); axis.legend(); fig.savefig(report_dir / "accuracy_ablation.png", dpi=180)
    plt.close(fig)
    fig, axis = plt.subplots(figsize=(10, 6), constrained_layout=True)
    for scene in SCENES:
        values = sorted(row["ssim"] for row in evaluations[scene]["hardware_integer"]["per_map"])
        axis.plot(values, np.linspace(0, 1, len(values)), label=f"{scene} ({len(values)})")
    axis.set(xlabel="FPGA/QAT integer SSIM vs measured GT", ylabel="Empirical CDF",
             title=f"Stage39 four-scene {run_label} SSIM CDF")
    axis.grid(alpha=.25); axis.legend(); fig.savefig(report_dir / "four_scene_ssim_cdf.png", dpi=180)
    plt.close(fig)
    fig, axes = plt.subplots(1, 2, figsize=(12, 5), constrained_layout=True)
    axes[0].bar(SCENES, [row["hardware_exact_qat_integer"] for row in rows], color="#1769aa")
    axes[0].set_title("Mean SSIM"); axes[0].tick_params(axis="x", rotation=20)
    axes[1].bar(SCENES, [row["pc_minus_qat_gap"] for row in rows], color="#079c8c")
    axes[1].axhline(config["acceptance"]["maximum_mean_ssim_gap"], color="#c62828", linestyle="--")
    axes[1].set_title("PC FP32 minus QAT integer SSIM"); axes[1].tick_params(axis="x", rotation=20)
    fig.savefig(report_dir / "aggregate_metrics.png", dpi=180); plt.close(fig)
    report = {"schema": "n2-stage39-cold-source-derived-ablation-v1", "created_utc": utc_now(),
        "status": "PASS" if args.profile == "full" else "PIPELINE_SMOKE_ONLY",
        "profile": args.profile, "maps": total_maps, "accuracy_rows": rows,
        "fixed_rx_cache_rows": cache_rows,
        "architecture_scope": "Sparse neural MAC workload derived from runtime descriptors. The Q15 renderer is identical in both cache cases and is excluded.",
        "historical_stage34_or_stage35_artifacts_used": False, "output_calibration": None}
    write_json(report_dir / "ablation.json", report)
    target = PC_PACKAGE / "pc_app/stage39_static"; target.mkdir(parents=True, exist_ok=True)
    for name in ("accuracy_ablation.png", "four_scene_ssim_cdf.png", "aggregate_metrics.png"):
        (target / name).write_bytes((report_dir / name).read_bytes())
    package_reports = PC_PACKAGE / "stage39_reports"; package_reports.mkdir(exist_ok=True)
    for path in report_dir.glob("*"):
        if path.is_file(): (package_reports / path.name).write_bytes(path.read_bytes())
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__": main()
