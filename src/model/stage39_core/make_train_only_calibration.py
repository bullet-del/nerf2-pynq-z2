#!/usr/bin/env python3
"""Create deterministic Stage39 activation vectors from the training split only.

Each row contains one point, view direction, and transmitter coordinate.  No
test index, test image, or test transmitter is opened by this program.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import imageio.v2 as imageio
import numpy as np
import pandas as pd
import yaml
from scipy.spatial.transform import Rotation


ALGORITHM_ID = "stage39-train-only-stratified-ray-sample-v1"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def geometry(datadir: Path):
    sample = next((datadir / "spectrum").glob("*.png"))
    nele, nazi = imageio.imread(sample).shape
    if (nele, nazi) != (90, 360):
        raise ValueError(f"Stage39 requires 90x360 spectra, got {(nele, nazi)}")
    info = yaml.safe_load((datadir / "gateway_info.yml").read_text(encoding="utf-8"))
    pos = np.asarray(info["gateway1"]["position"], np.float32)
    quat = np.asarray(info["gateway1"]["orientation"], np.float64)
    az = np.tile(np.linspace(1, 360, nazi, dtype=np.float32) * np.pi / 180, nele)
    el = np.repeat(np.linspace(1, 90, nele, dtype=np.float32) * np.pi / 180, nazi)
    direction = np.stack((np.cos(el) * np.cos(az), np.cos(el) * np.sin(az),
                          np.sin(el)), -1)
    direction = direction @ Rotation.from_quat(quat).as_matrix().T.astype(np.float32)
    return pos, direction.astype(np.float32)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scene", required=True)
    ap.add_argument("--datadir", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--count", type=int, default=8192)
    ap.add_argument("--seed", type=int, default=3902)
    args = ap.parse_args()
    if args.count < 1024:
        raise ValueError("at least 1024 training-only vectors are required")

    train_path = args.datadir / "train_index.txt"
    train_ids = np.atleast_1d(np.loadtxt(train_path, dtype=np.int64))
    all_pos = pd.read_csv(args.datadir / "tx_pos.csv").values.astype(np.float32)
    train_pos = all_pos[train_ids - 1]
    origin, directions = geometry(args.datadir)
    rng = np.random.default_rng(args.seed)

    # Deterministic stratification covers every elevation band, azimuth sector,
    # depth interval, and training transmitter before pseudo-random permutation.
    ordinal = np.arange(args.count, dtype=np.int64)
    ray = ((ordinal * 7919 + rng.integers(0, 32400)) % 32400).astype(np.int64)
    sample = ((ordinal * 29 + rng.integers(0, 64)) % 64).astype(np.int64)
    tx_row = ((ordinal * 103 + rng.integers(0, len(train_pos))) % len(train_pos)).astype(np.int64)
    t = np.linspace(0.0, 5.0, 64, dtype=np.float32)
    point = origin[None] + directions[ray] * t[sample, None]
    xyz = np.concatenate((point, directions[ray], train_pos[tx_row]), axis=1).astype(np.float32)
    if xyz.shape != (args.count, 9) or not np.all(np.isfinite(xyz)):
        raise AssertionError("invalid calibration matrix")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    np.save(args.out, xyz, allow_pickle=False)
    report = {
        "schema": "n2-stage39-train-only-calibration-v1",
        "algorithm_id": ALGORITHM_ID,
        "scene": args.scene,
        "count": int(args.count),
        "seed": int(args.seed),
        "train_index_sha256": sha256(train_path),
        "train_transmitters": int(len(train_ids)),
        "test_artifacts_opened": [],
        "output_sha256": sha256(args.out),
        "coordinate_min": xyz.min(0).astype(float).tolist(),
        "coordinate_max": xyz.max(0).astype(float).tolist(),
    }
    args.out.with_suffix(".json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2), flush=True)


if __name__ == "__main__":
    main()
