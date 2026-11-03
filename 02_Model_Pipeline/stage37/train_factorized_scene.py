#!/usr/bin/env python3
"""Fine-tune one NeRF2 checkpoint for the common Stage37 accelerator graph.

The resulting checkpoint retains the official NeRF2 module names and tensor
shapes.  The sample-point feature block of signal_linears.0 is permanently
masked to zero, so the signal MLP is evaluated once per ray while the
attenuation MLP is evaluated once per sample and cached for a fixed receiver.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import random
import time
from pathlib import Path

import imageio.v2 as imageio
import numpy as np
import pandas as pd
import torch
import yaml
from scipy.spatial.transform import Rotation
from skimage.metrics import structural_similarity


def load_reference(path: Path):
    spec = importlib.util.spec_from_file_location("n2_stage37_reference_train", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def build_rays(datadir: Path, device: torch.device):
    sample_path = next((datadir / "spectrum").glob("*.png"))
    nele, nazi = imageio.imread(sample_path).shape
    info = yaml.safe_load((datadir / "gateway_info.yml").read_text(encoding="utf-8"))
    pos = np.asarray(info["gateway1"]["position"], np.float32)
    quat = np.asarray(info["gateway1"]["orientation"], np.float64)
    az = torch.linspace(1, 360, nazi) * np.pi / 180
    el = torch.linspace(1, 90, nele) * np.pi / 180
    az, el = az.repeat(nele), torch.repeat_interleave(el, nazi)
    direction = torch.stack((torch.cos(el)*torch.cos(az), torch.cos(el)*torch.sin(az),
                             torch.sin(el)))
    direction = torch.from_numpy(Rotation.from_quat(quat).as_matrix()).float() @ direction
    origin = torch.from_numpy(np.tile(pos, (nele*nazi, 1))).to(device)
    return origin, direction.T.contiguous().to(device), (nele, nazi)


def load_split(datadir: Path, split: str):
    indexes = np.atleast_1d(np.loadtxt(datadir / f"{split}_index.txt", dtype=str))
    images = np.empty((len(indexes), 90*360), dtype=np.uint8)
    for i, index in enumerate(indexes):
        images[i] = imageio.imread(datadir / "spectrum" / f"{index}.png").reshape(-1)
    positions = pd.read_csv(datadir / "tx_pos.csv").values.astype(np.float32)
    split_positions = positions[np.asarray(indexes, dtype=np.int64)-1]
    return indexes, images, split_positions


def mask_feature_block(model):
    with torch.no_grad():
        # The fast attenuation graph starts at official layer 5 and consumes
        # the positional skip directly.  Layers 0..4 therefore have no path
        # into layer 5 in every Stage37-compatible checkpoint.
        # Official NeRF2 concatenates [pts(63), feature(256)] at the skip.
        # The Stage24 exporter permutes this to [feature, pts] for RTL, hence
        # original checkpoint columns 63:319 are the removable feature block.
        model.attenuation_linears[5].weight[:, 63:].zero_()
        model.signal_linears[0].weight[:, :256].zero_()


def render_training(reference, model, tx, origin, direction, cfg):
    return reference.render_spectrum(model, tx, origin, direction,
        near=float(cfg["render"]["near"]), far=float(cfg["render"]["far"]),
        samples=int(cfg["render"]["n_samples"]))


@torch.inference_mode()
def validate(reference, model, cfg, origin, direction, indexes, images, positions,
             count: int, chunk: int):
    scores, mses = [], []
    selected = np.linspace(0, len(indexes)-1, min(count, len(indexes)), dtype=np.int64)
    for mi in selected:
        parts = []
        tx = torch.from_numpy(positions[mi]).to(origin.device)
        for start in range(0, len(direction), chunk):
            n = min(chunk, len(direction)-start)
            parts.append(render_training(reference, model, tx[None].expand(n, -1),
                origin[start:start+n], direction[start:start+n], cfg).cpu())
        pred = torch.cat(parts).numpy().reshape(90, 360)
        truth = images[mi].astype(np.float32).reshape(90, 360)/255.0
        scores.append(structural_similarity(truth, pred, data_range=1.0))
        mses.append(np.mean((truth-pred)**2))
    return {"count": len(selected), "mean_ssim": float(np.mean(scores)),
            "mean_mse": float(np.mean(mses)), "ssim": [float(x) for x in scores]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scene", required=True)
    ap.add_argument("--reference", type=Path, required=True)
    ap.add_argument("--checkpoint", type=Path, required=True)
    ap.add_argument("--config", type=Path, required=True)
    ap.add_argument("--datadir", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--steps", type=int, default=3000)
    ap.add_argument("--batch", type=int, default=512)
    ap.add_argument("--lr", type=float, default=2e-4)
    ap.add_argument("--seed", type=int, default=20260905)
    ap.add_argument("--validation-maps", type=int, default=10)
    args = ap.parse_args()

    random.seed(args.seed); np.random.seed(args.seed); torch.manual_seed(args.seed)
    device = torch.device("cuda")
    reference = load_reference(args.reference)
    model, cfg = reference.load_model(args.checkpoint, args.config, device=device)
    source = torch.load(args.checkpoint, map_location="cpu")
    origin, direction, shape = build_rays(args.datadir, device)
    if shape != (90, 360):
        raise ValueError(f"Stage37 expects 90x360 spectra, got {shape}")
    print(f"[{args.scene}] loading train/test images", flush=True)
    train_index, train_images, train_positions = load_split(args.datadir, "train")
    test_index, test_images, test_positions = load_split(args.datadir, "test")

    mask_feature_block(model)
    frozen = set(model.feature_layer.parameters())
    for layer in model.attenuation_linears[:5]:
        frozen.update(layer.parameters())
    params = [p for p in model.parameters() if p not in frozen]
    optimizer = torch.optim.Adam(params, lr=args.lr, weight_decay=0.0)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=args.steps,
                                                            eta_min=args.lr*0.05)
    initial = validate(reference, model, cfg, origin, direction, test_index, test_images,
                       test_positions, args.validation_maps, 2048)
    print(f"[{args.scene}] initial validation {initial}", flush=True)
    model.train()
    started = time.perf_counter()
    running = 0.0
    for step in range(1, args.steps+1):
        map_ids = np.random.randint(0, len(train_index), size=args.batch)
        ray_ids = np.random.randint(0, len(direction), size=args.batch)
        tx = torch.from_numpy(train_positions[map_ids]).to(device)
        gt = torch.from_numpy(train_images[map_ids, ray_ids].astype(np.float32)/255.0).to(device)
        pred = render_training(reference, model, tx, origin[ray_ids], direction[ray_ids], cfg)
        loss = torch.mean((pred-gt)**2)
        optimizer.zero_grad(set_to_none=True)
        loss.backward()
        model.attenuation_linears[5].weight.grad[:, 63:].zero_()
        model.signal_linears[0].weight.grad[:, :256].zero_()
        torch.nn.utils.clip_grad_norm_(params, 5.0)
        optimizer.step(); mask_feature_block(model); scheduler.step()
        running += float(loss.detach())
        if step == 1 or step % 100 == 0:
            elapsed = time.perf_counter()-started
            print(f"[{args.scene}] step={step}/{args.steps} loss100={running/(1 if step==1 else 100):.7f} "
                  f"elapsed={elapsed:.1f}s", flush=True)
            running = 0.0

    model.eval(); mask_feature_block(model)
    final = validate(reference, model, cfg, origin, direction, test_index, test_images,
                     test_positions, args.validation_maps, 2048)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    source["nerf2_network_state_dict"] = model.state_dict()
    source["stage37"] = {"schema": "n2-stage37-factorized-checkpoint-v1",
        "scene": args.scene, "source_checkpoint_sha256": sha256(args.checkpoint),
        "signal_feature_columns_zero": True,
        "attenuation_layer5_feature_columns_zero": True,
        "common_hardware_graph": "L5-L8 attenuation + L10-L12 signal + Q15 renderer",
        "steps": args.steps,
        "batch": args.batch, "learning_rate": args.lr, "initial": initial, "final": final}
    torch.save(source, args.out)
    report_path = args.out.with_suffix(".json")
    report_path.write_text(json.dumps(source["stage37"], indent=2), encoding="utf-8")
    print(json.dumps(source["stage37"], indent=2), flush=True)


if __name__ == "__main__":
    main()
