#!/usr/bin/env python3
"""Full post-freeze FP32 and hardware-integer evaluation for one scene."""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import sys
import time
from pathlib import Path

import imageio.v2 as imageio
import numpy as np
import pandas as pd
import torch
import yaml
from scipy.spatial.transform import Rotation
from skimage.metrics import structural_similarity


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec); assert spec.loader is not None
    sys.modules[name] = module; spec.loader.exec_module(module); return module


def geometry(datadir):
    info = yaml.safe_load((datadir / "gateway_info.yml").read_text(encoding="utf-8"))
    rx = np.asarray(info["gateway1"]["position"], np.float32)
    quat = np.asarray(info["gateway1"]["orientation"], np.float64)
    az = np.tile(np.linspace(1, 360, 360, dtype=np.float32) * np.pi / 180, 90)
    el = np.repeat(np.linspace(1, 90, 90, dtype=np.float32) * np.pi / 180, 360)
    direction = np.stack((np.cos(el)*np.cos(az), np.cos(el)*np.sin(az), np.sin(el)), -1)
    direction = direction @ Rotation.from_quat(quat).as_matrix().T.astype(np.float32)
    t = np.linspace(0, 5, 64, dtype=np.float32)
    points = rx[None, None] + direction[:, None] * t[None, :, None]
    return direction.astype(np.float32), points.astype(np.float32), t


class FastFactorizedFP32:
    def __init__(self, reference, checkpoint, config, datadir, device="cuda"):
        self.reference = reference
        self.model, _ = reference.load_model(checkpoint, config, device=device)
        self.device = torch.device(device)
        direction, points, t = geometry(datadir)
        self.direction_np = direction
        self.direction = torch.tensor(direction, device=self.device)
        self.t = torch.tensor(t, device=self.device).expand(32400, -1)
        # Assert the checkpoint really uses the deployable factorization.
        if torch.count_nonzero(self.model.attenuation_linears[5].weight[:, 63:]).item():
            raise ValueError("FP32 checkpoint is not Stage39 factorized at attenuation layer 5")
        if torch.count_nonzero(self.model.signal_linears[0].weight[:, :256]).item():
            raise ValueError("FP32 checkpoint is not Stage39 factorized at signal layer 0")
        chunks = []
        with torch.inference_mode():
            for start in range(0, 32400 * 64, 65536):
                p = torch.tensor(points.reshape(-1, 3)[start:start+65536], device=self.device)
                x = self.model.embed_pts_fn(p)
                x = torch.relu(torch.nn.functional.linear(x,
                    self.model.attenuation_linears[5].weight[:, :63],
                    self.model.attenuation_linears[5].bias))
                x = torch.relu(self.model.attenuation_linears[6](x))
                x = torch.relu(self.model.attenuation_linears[7](x))
                chunks.append(self.model.attenuation_output(x).cpu())
        self.att = torch.cat(chunks).reshape(32400, 64, 2).to(self.device)
        self.view = self.model.embed_view_fn(self.direction)

    @torch.inference_mode()
    def render(self, tx, chunk=4096):
        values = []
        tx = torch.tensor(np.asarray(tx, np.float32), device=self.device)[None]
        txe = self.model.embed_tx_fn(tx).expand(32400, -1)
        for start in range(0, 32400, chunk):
            stop = min(start + chunk, 32400)
            signal_input = torch.cat((self.view[start:stop], txe[start:stop]), -1)
            x = torch.relu(torch.nn.functional.linear(signal_input,
                self.model.signal_linears[0].weight[:, 256:],
                self.model.signal_linears[0].bias))
            x = torch.relu(self.model.signal_linears[1](x))
            sig = self.model.signal_output(x)
            raw = torch.cat((self.att[start:stop],
                             sig[:, None, :].expand(-1, 64, -1)), -1)
            values.append(self.reference.spectrum_from_raw(
                raw, self.t[start:stop], self.direction[start:stop]).cpu())
        return torch.cat(values).numpy().reshape(90, 360).astype(np.float64)


def image_metrics(gt, pred, direction):
    diff = pred - gt
    mse = float(np.mean(diff * diff)); mae = float(np.mean(np.abs(diff)))
    psnr = float("inf") if mse == 0 else float(10 * np.log10(1.0 / mse))
    ssim = float(structural_similarity(gt, pred, data_range=1.0))
    gi, pi = int(np.argmax(gt)), int(np.argmax(pred))
    dot = float(np.clip(np.dot(direction[gi], direction[pi]), -1.0, 1.0))
    angular = float(np.degrees(np.arccos(dot)))
    ge, ga = divmod(gi, 360); pe, pa = divmod(pi, 360)
    az_error = float(min(abs(ga-pa), 360-abs(ga-pa)))
    el_error = float(abs(ge-pe))
    around = np.degrees(np.arccos(np.clip(direction @ direction[gi], -1.0, 1.0))) <= 10.0
    gt_energy = float(np.sum(gt.reshape(-1)[around] ** 2) / max(np.sum(gt ** 2), 1e-20))
    pred_energy = float(np.sum(pred.reshape(-1)[around] ** 2) / max(np.sum(pred ** 2), 1e-20))
    return {"ssim": ssim, "psnr_db": psnr, "mse": mse, "mae": mae,
            "peak_angular_error_deg": angular, "peak_azimuth_error_deg": az_error,
            "peak_elevation_error_deg": el_error,
            "main_lobe_energy_fraction_gt": gt_energy,
            "main_lobe_energy_fraction_pred": pred_energy,
            "main_lobe_energy_abs_error": abs(pred_energy-gt_energy)}


def summarize(rows, key):
    values = np.asarray([row[key] for row in rows], np.float64)
    return {"mean": float(np.mean(values)), "median": float(np.median(values)),
            "p05": float(np.percentile(values, 5)), "p95": float(np.percentile(values, 95)),
            "min": float(np.min(values)), "max": float(np.max(values))}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scene", required=True)
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--reference", type=Path, required=True)
    ap.add_argument("--checkpoint", type=Path, required=True)
    ap.add_argument("--config", type=Path, required=True)
    ap.add_argument("--tv", type=Path, required=True)
    ap.add_argument("--manifest", type=Path, required=True)
    ap.add_argument("--datadir", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    # The fixed integer evaluator imports the stable Stage5 stream parser by
    # module name.  Resolve it from the packaged reference instead of relying
    # on the caller's PYTHONPATH.
    packaged_software = args.reference.resolve().parents[1]
    sys.path.insert(0, str(packaged_software / "stage5"))
    sys.path.insert(0, str(packaged_software / "stage8"))
    sys.path.insert(0, str(packaged_software / "stage10"))
    sys.path.insert(0, str(packaged_software / "stage24"))
    board_stage24 = Path("outputs/N2-NeRF2-Stage39-board/software/stage24").resolve()
    if board_stage24.is_dir():
        sys.path.insert(0, str(board_stage24))

    reference = load_module("stage39_eval_reference", args.reference)
    fixed_module = load_module("stage39_eval_fixed", Path("work_stage37/evaluate_fixed_scene.py"))
    fp32 = FastFactorizedFP32(reference, args.checkpoint, args.config, args.datadir)
    integer = fixed_module.Fixed(args.root, args.tv, args.manifest, args.datadir)
    ids = np.atleast_1d(np.loadtxt(args.datadir / "test_index.txt", dtype=str))
    ids = ids[:args.limit or None]
    positions = pd.read_csv(args.datadir / "tx_pos.csv").values.astype(np.float32)
    float_rows, integer_rows = [], []
    started = time.perf_counter()
    for number, spectrum_id in enumerate(ids, 1):
        tx = positions[int(spectrum_id)-1]
        gt = imageio.imread(args.datadir / "spectrum" / f"{spectrum_id}.png").astype(np.float64) / 255.0
        pc = fp32.render(tx); hw = integer.render(tx)
        float_rows.append({"spectrum_id": str(spectrum_id), **image_metrics(gt, pc, fp32.direction_np)})
        integer_rows.append({"spectrum_id": str(spectrum_id), **image_metrics(gt, hw, fp32.direction_np)})
        if number % 20 == 0 or number == len(ids):
            print(f"[{args.scene}] {number}/{len(ids)} pc={np.mean([x['ssim'] for x in float_rows]):.6f} "
                  f"integer={np.mean([x['ssim'] for x in integer_rows]):.6f}", flush=True)
    keys = ["ssim", "psnr_db", "mse", "mae", "peak_angular_error_deg",
            "peak_azimuth_error_deg", "peak_elevation_error_deg",
            "main_lobe_energy_abs_error"]
    report = {
        "schema": "n2-stage39-full-scene-evaluation-v1", "scene": args.scene,
        "maps": int(len(ids)), "test_only_after_checkpoint_frozen": True,
        "output_calibration": None, "elapsed_seconds": time.perf_counter()-started,
        "pc_fp32": {"summary": {k: summarize(float_rows, k) for k in keys}, "per_map": float_rows},
        "hardware_integer": {"summary": {k: summarize(integer_rows, k) for k in keys},
                             "per_map": integer_rows},
    }
    report["mean_ssim_gap_pc_minus_integer"] = (
        report["pc_fp32"]["summary"]["ssim"]["mean"] -
        report["hardware_integer"]["summary"]["ssim"]["mean"])
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"scene": args.scene, "maps": len(ids),
        "pc_mean_ssim": report["pc_fp32"]["summary"]["ssim"]["mean"],
        "integer_mean_ssim": report["hardware_integer"]["summary"]["ssim"]["mean"],
        "gap": report["mean_ssim_gap_pc_minus_integer"]}, indent=2), flush=True)


if __name__ == "__main__":
    main()
