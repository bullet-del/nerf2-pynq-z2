#!/usr/bin/env python3
"""Hardware-exact-aware QAT for the common Stage37/39 factorized graph.

The training forward path uses the same INT8 weights, INT16 bias, arithmetic
right shift, INT8 saturation, ReLU order, and Q15 renderer LUTs as the RTL.
Straight-through estimators supply gradients.  Only the training split is
opened; test evaluation is a separate, post-freeze program.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import random
import sys
import time
from pathlib import Path

import imageio.v2 as imageio
import numpy as np
import pandas as pd
import torch
import yaml
from scipy.spatial.transform import Rotation


ALGORITHM_ID = "stage39-hardware-exact-qat-ste-v1"
LAYER_NAMES = ["attenuation_linears.%d" % i for i in range(8)] + [
    "attenuation_output", "feature_layer", "signal_linears.0",
    "signal_linears.1", "signal_output"]
RELU = [True] * 8 + [False, False, True, True, False]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def load_reference(path: Path):
    spec = importlib.util.spec_from_file_location("stage39_reference_qat", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class STE(torch.autograd.Function):
    @staticmethod
    def forward(ctx, x, mode, lo, hi):
        y = torch.floor(x) if int(mode) == 1 else torch.round(x)
        return torch.clamp(y, float(lo), float(hi))

    @staticmethod
    def backward(ctx, grad):
        return grad, None, None, None


def round_clip(x, lo, hi):
    return STE.apply(x, 0, lo, hi)


def floor_clip(x, lo, hi):
    return STE.apply(x, 1, lo, hi)


def floor_ste(x):
    return STE.apply(x, 1, -1.0e30, 1.0e30)


def lut_ste(table, q):
    """Exact integer-table value in forward, centered finite-difference in backward."""
    index = torch.clamp((q.detach() + 128).to(torch.long), 0, 255)
    base = table[index]
    lower = table[torch.clamp(index - 1, 0, 255)]
    upper = table[torch.clamp(index + 1, 0, 255)]
    slope = (upper - lower) * 0.5
    return base + (q - q.detach()) * slope


def positional(x):
    values = [x]
    for exponent in range(10):
        f = float(2 ** exponent)
        values.extend((torch.sin(x * f), torch.cos(x * f)))
    return torch.cat(values, -1)


def encode_q(x, scale):
    return round_clip(positional(x) / scale, -128, 127)


def build_rays(datadir: Path, device):
    sample_path = next((datadir / "spectrum").glob("*.png"))
    nele, nazi = imageio.imread(sample_path).shape
    if (nele, nazi) != (90, 360):
        raise ValueError(f"Stage39 expects 90x360 spectra, got {(nele, nazi)}")
    info = yaml.safe_load((datadir / "gateway_info.yml").read_text(encoding="utf-8"))
    pos = np.asarray(info["gateway1"]["position"], np.float32)
    quat = np.asarray(info["gateway1"]["orientation"], np.float64)
    az = torch.linspace(1, 360, nazi, device=device) * np.pi / 180
    el = torch.linspace(1, 90, nele, device=device) * np.pi / 180
    az, el = az.repeat(nele), torch.repeat_interleave(el, nazi)
    direction = torch.stack((torch.cos(el) * torch.cos(az),
                             torch.cos(el) * torch.sin(az), torch.sin(el)))
    rot = torch.from_numpy(Rotation.from_quat(quat).as_matrix()).float().to(device)
    direction = (rot @ direction).T.contiguous()
    origin = torch.from_numpy(np.tile(pos, (nele * nazi, 1))).to(device)
    return origin, direction


def load_train(datadir: Path):
    train_path = datadir / "train_index.txt"
    names = np.atleast_1d(np.loadtxt(train_path, dtype=str))
    ids = np.asarray(names, dtype=np.int64)
    images = np.empty((len(ids), 90 * 360), dtype=np.uint8)
    for row, spectrum_id in enumerate(names):
        images[row] = imageio.imread(datadir / "spectrum" / f"{spectrum_id}.png").reshape(-1)
    positions = pd.read_csv(datadir / "tx_pos.csv").values.astype(np.float32)[ids - 1]
    return ids, images, positions, train_path


def mask_graph(model):
    with torch.no_grad():
        model.attenuation_linears[5].weight[:, 63:].zero_()
        model.signal_linears[0].weight[:, :256].zero_()


class HardwareQAT(torch.nn.Module):
    def __init__(self, model, quant, fixed_renderer: Path, device):
        super().__init__()
        self.model = model
        self.shifts = [int(x) for x in quant["shifts"]]
        self.scales = [float(x) for x in quant["layer_output_scales"]]
        self.input_scales = [torch.tensor(x, dtype=torch.float32, device=device)
                             for x in quant["input_scales"]]
        spec = importlib.util.spec_from_file_location("stage39_fixed_renderer", fixed_renderer)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        luts = module.make_luts(np.asarray(quant["final_scales"], np.float64))
        for key in ("att_factor", "att_phase_cos", "att_phase_sin", "sig_amp",
                    "sig_phase_cos", "sig_phase_sin", "path_loss"):
            self.register_buffer(key, torch.tensor(np.asarray(luts[key]), dtype=torch.float32))
        self.phase_init_cos = int(luts["phase_init_cos"])
        self.phase_init_sin = int(luts["phase_init_sin"])

    def layer(self, index, xq, sx, select=None):
        layer = self.model.get_submodule(LAYER_NAMES[index])
        w = layer.weight
        if index == 5:
            # Official layout is [pts63, feature256].  The RTL permutes this
            # to [feature256, pts63]; the feature part is structurally zero.
            w = w[:, :63]
        elif index == 10:
            # Signal feature columns are structurally zero.
            w = w[:, 256:]
        if select is not None:
            w = w[:, select]
        sy = self.scales[index]
        shift = self.shifts[index]
        coeff = w * sx[None, :] / sy
        wq = round_clip(coeff * float(2 ** shift), -127, 127)
        bq = round_clip(layer.bias / sy, -32768, 32767)
        acc = xq @ wq.T
        y = floor_ste(acc / float(2 ** shift)) + bq
        # RTL saturates after adding bias.
        y = torch.clamp(y, -128.0, 127.0)
        if RELU[index]:
            y = torch.clamp_min(y, 0.0)
        return y

    def raw(self, point, direction, tx):
        pq = encode_q(point, self.input_scales[0])
        vq = encode_q(direction, self.input_scales[1])
        tq = encode_q(tx, self.input_scales[2])
        x = self.layer(5, pq, self.input_scales[0])
        for i in (6, 7):
            sx = torch.full((x.shape[-1],), self.scales[i - 1], device=x.device)
            x = self.layer(i, x, sx)
        ax = self.layer(8, x, torch.full((256,), self.scales[7], device=x.device))
        sig_in = torch.cat((vq, tq), -1)
        sig_scale = torch.cat((self.input_scales[1], self.input_scales[2]))
        sx = self.layer(10, sig_in, sig_scale)
        sx = self.layer(11, sx, torch.full((256,), self.scales[10], device=x.device))
        sx = self.layer(12, sx, torch.full((128,), self.scales[11], device=x.device))
        return torch.cat((ax[..., :2], sx[..., :2]), -1)

    def render(self, origin, direction, tx):
        # Produce exactly the 64 integer raw samples that the PL renderer sees.
        t = torch.linspace(0.0, 5.0, 64, device=origin.device)
        point = origin[:, None, :] + direction[:, None, :] * t[None, :, None]
        view = direction[:, None, :].expand_as(point)
        tx64 = tx[:, None, :].expand_as(point)
        raw = self.raw(point.reshape(-1, 3), view.reshape(-1, 3),
                       tx64.reshape(-1, 3)).reshape(-1, 64, 4)
        n = len(raw)
        factor = torch.full((n,), 32768.0, device=raw.device)
        phase_r = torch.full((n,), float(self.phase_init_cos), device=raw.device)
        phase_i = torch.full((n,), float(self.phase_init_sin), device=raw.device)
        re = torch.zeros(n, device=raw.device)
        im = torch.zeros(n, device=raw.device)
        sc = lut_ste(self.sig_phase_cos, raw[:, 0, 3])
        ss = lut_ste(self.sig_phase_sin, raw[:, 0, 3])
        sa = lut_ste(self.sig_amp, raw[:, 0, 2])
        for k in range(64):
            dr = floor_ste(sc * phase_r / 32768.0) - floor_ste(ss * phase_i / 32768.0)
            di = floor_ste(sc * phase_i / 32768.0) + floor_ste(ss * phase_r / 32768.0)
            amp = floor_ste(sa * factor / 32768.0)
            amp = floor_ste(amp * self.path_loss[k] / 32768.0)
            re = re + floor_ste(amp * dr / 32768.0)
            im = im + floor_ste(amp * di / 32768.0)
            af = lut_ste(self.att_factor, raw[:, k, 0])
            factor = floor_ste(factor * af / 32768.0)
            c = lut_ste(self.att_phase_cos, raw[:, k, 1])
            s = lut_ste(self.att_phase_sin, raw[:, k, 1])
            nr = floor_ste(phase_r * c / 32768.0) - floor_ste(phase_i * s / 32768.0)
            ni = floor_ste(phase_r * s / 32768.0) + floor_ste(phase_i * c / 32768.0)
            phase_r, phase_i = nr, ni
        square = torch.clamp(re * re + im * im, min=0.0)
        # The forward value is the integer square root used by RTL.  The +1
        # surrogate avoids the undefined derivative of sqrt at exactly zero.
        surrogate = torch.sqrt(square + 1.0)
        exact_root = torch.floor(torch.sqrt(square.detach()))
        root = exact_root + surrogate - surrogate.detach()
        return torch.clamp_min(root, 0.0) / 32768.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scene", required=True)
    ap.add_argument("--reference", type=Path, required=True)
    ap.add_argument("--fixed-renderer", type=Path, required=True)
    ap.add_argument("--checkpoint", type=Path, required=True)
    ap.add_argument("--config", type=Path, required=True)
    ap.add_argument("--datadir", type=Path, required=True)
    ap.add_argument("--quant-manifest", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--steps", type=int, default=1800)
    ap.add_argument("--batch", type=int, default=192)
    ap.add_argument("--lr", type=float, default=1e-5)
    ap.add_argument("--seed", type=int, default=3902)
    args = ap.parse_args()

    random.seed(args.seed); np.random.seed(args.seed); torch.manual_seed(args.seed)
    if not torch.cuda.is_available():
        raise RuntimeError("Stage39.2 QAT requires CUDA")
    device = torch.device("cuda")
    reference = load_reference(args.reference)
    model, _ = reference.load_model(args.checkpoint, args.config, device=device)
    source = torch.load(args.checkpoint, map_location="cpu")
    quant = json.loads(args.quant_manifest.read_text(encoding="utf-8"))
    origin, direction = build_rays(args.datadir, device)
    train_ids, train_images, train_positions, train_path = load_train(args.datadir)
    mask_graph(model)

    # Only layers in the deployed graph are trainable; structural zeros remain zero.
    for p in model.parameters():
        p.requires_grad_(False)
    deployed = [5, 6, 7, 8, 10, 11, 12]
    params = []
    for index in deployed:
        for p in model.get_submodule(LAYER_NAMES[index]).parameters():
            p.requires_grad_(True); params.append(p)
    qat = HardwareQAT(model, quant, args.fixed_renderer, device).to(device)
    optimizer = torch.optim.Adam(params, lr=args.lr)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, args.steps,
                                                            eta_min=args.lr * 0.05)
    started = time.perf_counter(); running = 0.0
    model.train()
    for step in range(1, args.steps + 1):
        map_row = np.random.randint(0, len(train_ids), size=args.batch)
        ray_id = np.random.randint(0, 32400, size=args.batch)
        tx = torch.from_numpy(train_positions[map_row]).to(device)
        gt = torch.from_numpy(train_images[map_row, ray_id].astype(np.float32) / 255.0).to(device)
        pred = qat.render(origin[ray_id], direction[ray_id], tx)
        loss = torch.mean((pred - gt) ** 2) + 0.05 * torch.mean(torch.abs(pred - gt))
        if not torch.isfinite(loss):
            raise FloatingPointError(f"non-finite Stage39 QAT loss at step {step}")
        optimizer.zero_grad(set_to_none=True); loss.backward()
        model.attenuation_linears[5].weight.grad[:, 63:].zero_()
        model.signal_linears[0].weight.grad[:, :256].zero_()
        for parameter in params:
            if parameter.grad is not None:
                torch.nan_to_num_(parameter.grad, nan=0.0, posinf=1.0, neginf=-1.0)
        torch.nn.utils.clip_grad_norm_(params, 1.0)
        optimizer.step(); mask_graph(model); scheduler.step()
        running += float(loss.detach())
        if step == 1 or step % 100 == 0 or step == args.steps:
            divisor = 1 if step == 1 else (step % 100 or 100)
            print(f"[{args.scene}] qat {step}/{args.steps} loss={running/divisor:.8f} "
                  f"elapsed={time.perf_counter()-started:.1f}s", flush=True)
            running = 0.0

    model.eval(); mask_graph(model)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    source["nerf2_network_state_dict"] = model.state_dict()
    metadata = {
        "schema": "n2-stage39-hardware-exact-qat-checkpoint-v1",
        "algorithm_id": ALGORITHM_ID,
        "scene": args.scene,
        "source_checkpoint_sha256": sha256(args.checkpoint),
        "train_index_sha256": sha256(train_path),
        "test_artifacts_opened": [],
        "steps": int(args.steps), "batch": int(args.batch),
        "learning_rate": float(args.lr), "seed": int(args.seed),
        "quant_manifest_sha256": sha256(args.quant_manifest),
        "hardware_forward": ["INT8 weights", "INT16 bias", "arithmetic right shift",
                             "INT8 saturation", "ReLU", "Q15 LUT renderer"],
        "output_calibration": None,
        "elapsed_seconds": time.perf_counter() - started,
    }
    source["stage39_qat"] = metadata
    torch.save(source, args.out)
    metadata["checkpoint_sha256"] = sha256(args.out)
    args.out.with_suffix(".json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    print(json.dumps(metadata, indent=2), flush=True)


if __name__ == "__main__":
    main()
