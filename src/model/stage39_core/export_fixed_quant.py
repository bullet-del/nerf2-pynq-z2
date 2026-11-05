#!/usr/bin/env python3
"""Export a QAT checkpoint with its frozen train-only quantization contract."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import torch


NAMES = ["attenuation_linears.%d" % i for i in range(8)] + [
    "attenuation_output", "feature_layer", "signal_linears.0",
    "signal_linears.1", "signal_output"]
RELU = [True] * 8 + [False, False, True, True, False]


def sha256(path):
    h = hashlib.sha256(); h.update(Path(path).read_bytes()); return h.hexdigest()


def load_reference(path: Path):
    spec = importlib.util.spec_from_file_location("stage39_reference_export", path)
    module = importlib.util.module_from_spec(spec); assert spec.loader is not None
    sys.modules[spec.name] = module; spec.loader.exec_module(module); return module


def positional(x):
    values = [x]
    for f in 2. ** np.arange(10, dtype=np.float64):
        values.extend((np.sin(x * f), np.cos(x * f)))
    return np.concatenate(values, -1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reference", type=Path, required=True)
    ap.add_argument("--ckpt", type=Path, required=True)
    ap.add_argument("--config", type=Path, required=True)
    ap.add_argument("--calibration", type=Path, required=True)
    ap.add_argument("--quant-manifest", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    reference = load_reference(args.reference)
    model, _ = reference.load_model(args.ckpt, args.config)
    xyz = np.load(args.calibration, allow_pickle=False).astype(np.float64)
    contract = json.loads(args.quant_manifest.read_text(encoding="utf-8"))
    se = [np.asarray(x, np.float64) for x in contract["input_scales"]]
    enc = [positional(xyz[:, 3*i:3*i+3]) for i in range(3)]
    eq = [np.clip(np.rint(e / s), -128, 127).astype(np.int8) for e, s in zip(enc, se)]
    scales = [float(x) for x in contract["layer_output_scales"]]
    shifts = [int(x) for x in contract["shifts"]]
    main_q = main_f = None; main_s = None
    wq, bq, saturation = [], [], []
    result = np.zeros((len(xyz), 4), np.int8)
    for index, name in enumerate(NAMES):
        layer = model.get_submodule(name)
        w = layer.weight.detach().cpu().numpy().astype(np.float64)
        b = layer.bias.detach().cpu().numpy().astype(np.float64)
        if index == 0:
            xq, xf, sx = eq[0], enc[0], se[0]
        elif index == 5:
            w = np.concatenate((w[:, 63:], w[:, :63]), 1)
            xq = np.concatenate((main_q, eq[0]), 1)
            xf = np.concatenate((main_f, enc[0]), 1)
            sx = np.r_[np.full(256, main_s), se[0]]
        elif index == 10:
            xq = np.concatenate((main_q, eq[1], eq[2]), 1)
            xf = np.concatenate((main_f, enc[1], enc[2]), 1)
            sx = np.r_[np.full(256, main_s), se[1], se[2]]
        else:
            xq, xf, sx = main_q, main_f, np.full(w.shape[1], main_s)
        sy, shift = scales[index], shifts[index]
        ww = np.clip(np.rint(w * sx[None] / sy * (2.0 ** shift)), -127, 127).astype(np.int8)
        bb64 = np.rint(b / sy).astype(np.int64)
        if np.max(np.abs(bb64)) > 32767:
            raise ValueError(f"L{index} bias overflow")
        bb = bb64.astype(np.int16)
        acc = xq.astype(np.int64) @ ww.T.astype(np.int64)
        y64 = (acc >> shift) + bb
        saturation.append(float(np.mean((y64 < -128) | (y64 > 127))))
        y = np.clip(y64, -128, 127).astype(np.int8)
        if RELU[index]: y = np.maximum(y, 0)
        yf = xf @ w.T + b
        if RELU[index]: yf = np.maximum(yf, 0)
        if index in (8, 12):
            result[:, 0 if index == 8 else 2:2 if index == 8 else 4] = y
        else:
            main_q, main_f, main_s = y, yf, sy
        wq.append(ww); bq.append(bb)

    p = 32; count = min(3, len(xyz)); lines = [f"{p} 13 {count}"]
    for index, w in enumerate(wq):
        n, k = w.shape; ng = (n + p - 1) // p
        lines.append(f"1 {index} {k*ng} {ng}")
    for sample in range(count):
        for kind in range(3):
            v = np.pad(eq[kind][sample], (0, 1))
            for z in range(2):
                lines.append(f"2 {kind} " + " ".join(map(str, v[z*32:(z+1)*32])))
    for index, w in enumerate(wq):
        n, k = w.shape; wp = np.pad(w, ((0, (-n) % 32), (0, 0)))
        for column in range(k):
            for group in range(len(wp) // 32):
                lines.append(f"3 {index} " + " ".join(map(str, wp[group*32:(group+1)*32, column])))
    for index, bias in enumerate(bq):
        bp = np.pad(bias, (0, (-len(bias)) % 32))
        for group in range(len(bp) // 32):
            lines.append(f"4 {index} " + " ".join(map(str, bp[group*32:(group+1)*32])))
    for index, shift in enumerate(shifts): lines.append(f"5 {index} {shift}")
    for sample in range(count): lines.append(f"6 {sample} " + " ".join(map(str, result[sample])))
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "tv3_ray_net.txt").write_text("\n".join(lines) + "\n", encoding="ascii")
    final = np.asarray([scales[8], scales[8], scales[12], scales[12]], np.float64)
    report = dict(contract)
    report.update({
        "schema": "n2-stage39-fixed-quant-export-v1",
        "qat_checkpoint_sha256": sha256(args.ckpt),
        "quant_contract_sha256": sha256(args.quant_manifest),
        "train_calibration_sha256": sha256(args.calibration),
        "layer_output_scales": scales, "final_scales": final.tolist(),
        "shifts": shifts, "calibration_saturation": saturation,
        "output_calibration": None, "fixed_contract_reused": True,
        "legacy_skip_columns_permuted": True,
        "tv3_compatible_with_existing_mlp_rtl": True,
    })
    (args.out / "manifest_ray.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    np.savez_compressed(args.out / "calibration_reference.npz", xyz=xyz,
                        enc_q=np.stack(eq, 1), fixed=result)
    print(json.dumps(report, indent=2), flush=True)


if __name__ == "__main__":
    main()
