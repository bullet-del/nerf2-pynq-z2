#!/usr/bin/env python3
"""Create a publication-ready figure from pc_gpu_full_test_summary.csv."""
from __future__ import annotations

import argparse
import csv
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("summary_csv", type=Path)
    parser.add_argument("--output", type=Path, default=Path("pc_gpu_full_test_comparison.png"))
    args = parser.parse_args()
    with args.summary_csv.open("r", newline="", encoding="utf-8-sig") as stream:
        rows = list(csv.DictReader(stream))
    if len(rows) != 4 or any(row.get("formal_validity") != "PASS" for row in rows):
        raise ValueError("expected four validated scene rows")

    scenes = [row["scene"] for row in rows]
    seconds = np.asarray([float(row["median_seconds_per_map"]) for row in rows])
    power = np.asarray([float(row["median_active_power_w"]) for row in rows])
    total_energy = np.asarray([float(row["median_total_energy_per_map_j"]) for row in rows])
    dynamic_energy = np.asarray([float(row["median_dynamic_energy_per_map_j"]) for row in rows])
    x = np.arange(len(scenes))

    plt.rcParams.update({"font.size": 9, "axes.titlesize": 10, "axes.labelsize": 9})
    fig, axes = plt.subplots(1, 3, figsize=(10.4, 3.25), constrained_layout=True)
    colors = ["#2E6F9E", "#3B8C6E", "#B06A3C", "#7B5AA6"]

    axes[0].bar(x, seconds, color=colors, width=0.68)
    axes[0].set_title("Latency")
    axes[0].set_ylabel("Seconds per 90x360 map")
    axes[0].set_ylim(0, max(seconds) * 1.18)

    axes[1].bar(x, power, color=colors, width=0.68)
    axes[1].set_title("Active GPU power")
    axes[1].set_ylabel("Watts")
    axes[1].set_ylim(0, max(power) * 1.18)

    width = 0.34
    axes[2].bar(x - width / 2, total_energy, width, label="Total", color="#2E6F9E")
    axes[2].bar(x + width / 2, dynamic_energy, width, label="Dynamic", color="#E28E2C")
    axes[2].set_title("GPU energy")
    axes[2].set_ylabel("Joules per map")
    axes[2].set_ylim(0, max(total_energy) * 1.22)
    axes[2].legend(frameon=False, fontsize=8, loc="upper right")

    for axis, values in ((axes[0], seconds), (axes[1], power)):
        for index, value in enumerate(values):
            axis.text(index, value, f"{value:.3f}", ha="center", va="bottom", fontsize=8)
    for index, (total, dynamic) in enumerate(zip(total_energy, dynamic_energy)):
        axes[2].text(index - width / 2, total, f"{total:.2f}", ha="center", va="bottom", fontsize=7)
        axes[2].text(index + width / 2, dynamic, f"{dynamic:.2f}", ha="center", va="bottom", fontsize=7)
    for axis in axes:
        axis.set_xticks(x, scenes, rotation=18, ha="right")
        axis.grid(axis="y", color="#D0D0D0", linewidth=0.6, alpha=0.7)
        axis.set_axisbelow(True)
        axis.spines[["top", "right"]].set_visible(False)

    fig.suptitle(
        "NeRF2 FP32 exhaustive test-set inference on RTX 4050 Laptop GPU\n"
        "3 full sweeps per scene; NVIDIA driver GPU power domain",
        fontsize=11,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(args.output, dpi=300, bbox_inches="tight")
    pdf = args.output.with_suffix(".pdf")
    fig.savefig(pdf, bbox_inches="tight")
    plt.close(fig)
    print(f"PASS: {args.output}")
    print(f"PASS: {pdf}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
