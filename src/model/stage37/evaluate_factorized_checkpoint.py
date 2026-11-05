#!/usr/bin/env python3
"""Measure the accuracy cost of the Stage35/37 factorized signal branch.

The first 256 columns of signal_linears.0 carry the sample-point feature.
Forcing those columns to zero makes the signal output constant along a ray,
which is the mathematical precondition used by the sub-1.2 s PL pipeline.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path

import imageio.v2 as imageio
import numpy as np
import pandas as pd
import torch
import yaml
from scipy.spatial.transform import Rotation
from skimage.metrics import structural_similarity


def load_reference(path: Path):
    spec = importlib.util.spec_from_file_location("n2_stage37_reference", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def rays(datadir: Path, device: torch.device):
    sample = imageio.imread(next((datadir / "spectrum").glob("*.png")))
    nele, nazi = sample.shape
    info = yaml.safe_load((datadir / "gateway_info.yml").read_text(encoding="utf-8"))
    pos = np.asarray(info["gateway1"]["position"], np.float32)
    quat = np.asarray(info["gateway1"]["orientation"], np.float64)
    az = torch.linspace(1, 360, nazi) * np.pi / 180
    el = torch.linspace(1, 90, nele) * np.pi / 180
    az = az.repeat(nele)
    el = torch.repeat_interleave(el, nazi)
    directions = torch.stack((torch.cos(el) * torch.cos(az),
                              torch.cos(el) * torch.sin(az), torch.sin(el)))
    directions = torch.from_numpy(Rotation.from_quat(quat).as_matrix()).float() @ directions
    origin = torch.from_numpy(np.tile(pos, (nele * nazi, 1)))
    return origin.to(device), directions.T.contiguous().to(device), (nele, nazi)


@torch.inference_mode()
def render_map(reference, model, tx, origin, direction, cfg, chunk):
    values = []
    for start in range(0, len(direction), chunk):
        n = min(chunk, len(direction) - start)
        txb = tx.expand(n, -1)
        values.append(reference.render_spectrum(
            model, txb, origin[start:start+n], direction[start:start+n],
            near=float(cfg["render"]["near"]), far=float(cfg["render"]["far"]),
            samples=int(cfg["render"]["n_samples"])).cpu())
    return torch.cat(values).numpy()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reference", type=Path, required=True)
    ap.add_argument("--checkpoint", type=Path, required=True)
    ap.add_argument("--config", type=Path, required=True)
    ap.add_argument("--datadir", type=Path, required=True)
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--chunk", type=int, default=2048)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    device = torch.device("cuda")
    ref = load_reference(args.reference)
    model, cfg = ref.load_model(args.checkpoint, args.config, device=device)
    with torch.no_grad():
        removed = model.signal_linears[0].weight[:, :256]
        removed_l1 = float(removed.abs().sum().item())
        removed_nnz = int(torch.count_nonzero(removed).item())
        removed.zero_()
    origin, direction, shape = rays(args.datadir, device)
    positions = pd.read_csv(args.datadir / "tx_pos.csv").values.astype(np.float32)
    indexes = np.atleast_1d(np.loadtxt(args.datadir / "test_index.txt", dtype=str))
    if args.limit:
        indexes = indexes[:args.limit]
    rows = []
    for sequence, index in enumerate(indexes, 1):
        prediction = render_map(ref, model, torch.from_numpy(positions[int(index)-1]).to(device)[None],
                                origin, direction, cfg, args.chunk).reshape(shape)
        truth = imageio.imread(args.datadir / "spectrum" / f"{index}.png").astype(np.float32) / 255.0
        rows.append({"index": str(index), "ssim": float(structural_similarity(
            truth, prediction, data_range=1.0)), "mse": float(np.mean((truth-prediction)**2))})
        if sequence % 20 == 0 or sequence == len(indexes):
            print(f"[{sequence}/{len(indexes)}] mean SSIM="
                  f"{np.mean([r['ssim'] for r in rows]):.6f}", flush=True)
    report = {"factorization": "signal_linears.0.weight[:,0:256] == 0",
              "removed_fp32_nonzero": removed_nnz, "removed_fp32_l1": removed_l1,
              "maps": rows, "mean_ssim": float(np.mean([r["ssim"] for r in rows]))}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2), flush=True)


if __name__ == "__main__":
    main()
