#!/usr/bin/env python3
"""Fit the one Stage37 train-only Q15 calibration algorithm.

Every scene uses the same deterministic train/validation split, weighted PAVA
fit, blend grid, and 65,536-entry Q15 lookup at runtime. The test split is
evaluated only after the LUT has been frozen.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path

import imageio.v2 as imageio
import numpy as np
import pandas as pd
import torch
import torch.nn.functional as torch_functional
import yaml
from scipy.spatial.transform import Rotation
from skimage.metrics import structural_similarity

SCHEMA = "n2-stage37-universal-train-only-monotone-q15-lut-v2"
ALGORITHM = "deterministic-fp32-teacher-train-validation-weighted-pava-blend-v1"
BLEND_GRID = np.linspace(0.0, 1.0, 9, dtype=np.float64)
MAX_Q15 = 65535
Q15_ONE = 32768.0


def load(path):
    spec = importlib.util.spec_from_file_location("fixed_eval", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.Fixed


def pava(values, weights):
    values = np.asarray(values, np.float64)
    weights = np.asarray(weights, np.float64)
    levels, masses, starts, ends = [], [], [], []
    for index, (value, weight) in enumerate(zip(values, weights)):
        levels.append(value); masses.append(weight)
        starts.append(index); ends.append(index)
        while len(levels) > 1 and levels[-2] > levels[-1]:
            mass = masses[-2] + masses[-1]
            level = (levels[-2] * masses[-2] + levels[-1] * masses[-1]) / mass
            levels[-2:] = [level]; masses[-2:] = [mass]
            ends[-2:] = [ends[-1]]; starts.pop()
    result = np.empty(len(values), np.float64)
    for level, first, last in zip(levels, starts, ends):
        result[first:last + 1] = level
    return result


class FastFactorizedFP32:
    """Exact fast inference for a checkpoint satisfying the Stage37 factorization."""

    def __init__(self, reference_path, checkpoint, config, datadir, device="cuda"):
        spec = importlib.util.spec_from_file_location("stage37_calibration_reference", reference_path)
        reference = importlib.util.module_from_spec(spec); spec.loader.exec_module(reference)
        self.model, cfg = reference.load_model(str(checkpoint), str(config), device)
        self.model.eval(); self.device = torch.device(device)
        if torch.count_nonzero(self.model.attenuation_linears[5].weight[:, 63:]).item():
            raise ValueError("FP32 teacher is not Stage37-factorized at attenuation layer 5")
        if torch.count_nonzero(self.model.signal_linears[0].weight[:, :256]).item():
            raise ValueError("FP32 teacher is not Stage37-factorized at signal layer 0")
        info = yaml.safe_load((datadir / "gateway_info.yml").read_text(encoding="utf-8"))
        origin = np.asarray(info["gateway1"]["position"], np.float32)
        rotation = Rotation.from_quat(info["gateway1"]["orientation"]).as_matrix().T.astype(np.float32)
        azimuth = np.tile(np.linspace(1, 360, 360, dtype=np.float32) * np.pi / 180, 90)
        elevation = np.repeat(np.linspace(1, 90, 90, dtype=np.float32) * np.pi / 180, 360)
        directions = np.stack((np.cos(elevation) * np.cos(azimuth),
                               np.cos(elevation) * np.sin(azimuth),
                               np.sin(elevation)), -1) @ rotation
        near = float(cfg["render"]["near"]); far = float(cfg["render"]["far"])
        samples = int(cfg["render"]["n_samples"])
        if samples != 64:
            raise ValueError("Stage37 FP32 teacher must use 64 samples per ray")
        distance = torch.linspace(near, far, samples, device=self.device)
        segment = torch.cat((distance[1:] - distance[:-1],
                             torch.full_like(distance[:1], 1e10)), 0)
        path = torch.cat((distance[1:], torch.full_like(distance[:1], 1e10)), 0)
        bases = []
        with torch.inference_mode():
            for row in range(90):
                dr = torch.from_numpy(directions[row * 360:(row + 1) * 360]).to(self.device)
                points = (torch.from_numpy(origin).to(self.device)[None, None, :] +
                          dr[:, None, :] * distance[None, :, None])
                encoded = self.model.embed_pts_fn(points).reshape(-1, 63)
                layer = self.model.attenuation_linears[5]
                value = torch_functional.relu(torch_functional.linear(
                    encoded, layer.weight[:, :63], layer.bias))
                value = torch_functional.relu(self.model.attenuation_linears[6](value))
                value = torch_functional.relu(self.model.attenuation_linears[7](value))
                attenuation_raw = self.model.attenuation_output(value).reshape(360, samples, 2)
                amplitude = torch_functional.leaky_relu(attenuation_raw[..., 0]).abs()
                phase_step = torch.sigmoid(attenuation_raw[..., 1]) * (2 * np.pi)
                alpha = 1 - torch.exp(-amplitude * segment[None, :])
                transmittance = torch.cumprod(torch.cat((
                    torch.ones_like(alpha[:, :1]), 1 - alpha + 1e-10), -1), -1)[:, :-1]
                phase = torch.cumsum(torch.cat((torch.ones_like(phase_step[:, :1]),
                                                phase_step * segment[None, :]), -1), -1)[:, :-1]
                coefficient = (transmittance * torch.exp(1j * phase) *
                               (0.025 / path[None, :])).sum(-1).abs()
                bases.append(coefficient)
            self.base_magnitude = torch.cat(bases)
            self.encoded_view = self.model.embed_view_fn(
                torch.from_numpy(directions).to(self.device))

    @torch.inference_mode()
    def render(self, tx):
        tx = torch.as_tensor(np.asarray(tx, np.float32), device=self.device)[None, :]
        encoded_tx = self.model.embed_tx_fn(tx).expand(len(self.encoded_view), -1)
        encoded = torch.cat((self.encoded_view, encoded_tx), -1)
        first = self.model.signal_linears[0]
        value = torch_functional.relu(torch_functional.linear(
            encoded, first.weight[:, 256:], first.bias))
        value = torch_functional.relu(self.model.signal_linears[1](value))
        signal = self.model.signal_output(value)
        signal_amplitude = torch_functional.leaky_relu(signal[:, 0]).abs()
        return (signal_amplitude * self.base_magnitude).cpu().numpy().reshape(90, 360)


def render_pair(model, teacher, positions, datadir, spectrum_id):
    tx = positions[int(spectrum_id) - 1]
    prediction = model.render(tx)
    teacher_prediction = teacher.render(tx)
    raw_q15 = np.clip(np.rint(prediction * Q15_ONE), 0, MAX_Q15).astype(np.int64)
    truth = imageio.imread(datadir / "spectrum" / (str(spectrum_id) + ".png")).astype(np.float64) / 255.0
    return prediction, teacher_prediction, raw_q15, truth


def fit_candidate(model, teacher, positions, datadir, spectrum_ids):
    sums = np.zeros(MAX_Q15 + 1, np.float64)
    counts = np.zeros(MAX_Q15 + 1, np.float64)
    for number, spectrum_id in enumerate(spectrum_ids, 1):
        _, teacher_prediction, raw_q15, _ = render_pair(
            model, teacher, positions, datadir, spectrum_id)
        flat_q15 = raw_q15.ravel(); flat_teacher = teacher_prediction.ravel()
        sums += np.bincount(flat_q15, weights=flat_teacher, minlength=MAX_Q15 + 1)
        counts += np.bincount(flat_q15, minlength=MAX_Q15 + 1)
        if number % 20 == 0 or number == len(spectrum_ids):
            print("fit", number, "/", len(spectrum_ids), flush=True)
    known = np.flatnonzero(counts)
    if not len(known):
        raise RuntimeError("calibration training selection produced no Q15 samples")
    isotonic = pava(sums[known] / counts[known], counts[known])
    values = np.interp(np.arange(MAX_Q15 + 1), known, isotonic,
                       left=isotonic[0], right=isotonic[-1])
    return np.clip(np.rint(values * Q15_ONE), 0, MAX_Q15).astype(np.uint16)


def blended_lut(candidate, alpha):
    identity = np.arange(MAX_Q15 + 1, dtype=np.float64)
    value = identity + float(alpha) * (candidate.astype(np.float64) - identity)
    return np.maximum.accumulate(np.clip(np.rint(value), 0, MAX_Q15)).astype(np.uint16)


def mean_ssim_for_lut(model, teacher, positions, datadir, spectrum_ids, lut):
    scores = []
    for spectrum_id in spectrum_ids:
        _, teacher_prediction, raw_q15, _ = render_pair(
            model, teacher, positions, datadir, spectrum_id)
        calibrated = lut[raw_q15].astype(np.float64) / Q15_ONE
        scores.append(structural_similarity(teacher_prediction, calibrated, data_range=1.0))
    return float(np.mean(scores))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--evaluator", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--tv", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--datadir", type=Path, required=True)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--fit-maps", type=int, default=120,
                        help="total deterministic train calibration maps, including validation")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    model = load(args.evaluator)(args.root, args.tv, args.manifest, args.datadir)
    teacher = FastFactorizedFP32(args.reference, args.checkpoint, args.config, args.datadir)
    positions = pd.read_csv(args.datadir / "tx_pos.csv").values.astype(np.float32)
    train = np.atleast_1d(np.loadtxt(args.datadir / "train_index.txt", dtype=str))
    selected = train[np.linspace(0, len(train) - 1,
                                 min(int(args.fit_maps), len(train)), dtype=int)]
    if len(selected) < 12:
        raise ValueError("universal calibration requires at least 12 training maps")
    validation = selected[::6]
    fit_mask = np.ones(len(selected), dtype=bool); fit_mask[::6] = False
    fit_ids = selected[fit_mask]

    candidate = fit_candidate(model, teacher, positions, args.datadir, fit_ids)
    validation_scores = []
    for alpha in BLEND_GRID:
        score = mean_ssim_for_lut(model, teacher, positions, args.datadir, validation,
                                  blended_lut(candidate, alpha))
        validation_scores.append(score)
        print("validation alpha=%.3f mean_ssim=%.9f" % (alpha, score), flush=True)
    selected_index = int(np.argmax(np.asarray(validation_scores)))
    selected_alpha = float(BLEND_GRID[selected_index])

    final_candidate = fit_candidate(model, teacher, positions, args.datadir, selected)
    lut = blended_lut(final_candidate, selected_alpha)

    test = np.atleast_1d(np.loadtxt(args.datadir / "test_index.txt", dtype=str))
    raw_scores, calibrated_scores, teacher_scores = [], [], []
    for number, spectrum_id in enumerate(test, 1):
        prediction, teacher_prediction, raw_q15, truth = render_pair(
            model, teacher, positions, args.datadir, spectrum_id)
        calibrated = lut[raw_q15].astype(np.float64) / Q15_ONE
        raw_scores.append(structural_similarity(truth, prediction, data_range=1.0))
        calibrated_scores.append(structural_similarity(truth, calibrated, data_range=1.0))
        teacher_scores.append(structural_similarity(truth, teacher_prediction, data_range=1.0))
        if number % 50 == 0 or number == len(test):
            print(number, len(test), np.mean(raw_scores), np.mean(calibrated_scores), flush=True)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    lut_path = args.out.with_suffix(".npy")
    np.save(lut_path, lut, allow_pickle=False)
    report = {
        "schema": SCHEMA,
        "algorithm": ALGORITHM,
        "runtime_operation": "calibrated_q15 = lut[clip(raw_q15,0,65535)]",
        "fit_target": "factorized-fp32-checkpoint-teacher",
        "scene_id": args.datadir.name,
        "checkpoint_sha256": hashlib.sha256(args.checkpoint.read_bytes()).hexdigest(),
        "quant_manifest_sha256": hashlib.sha256(args.manifest.read_bytes()).hexdigest(),
        "fit_split": "train",
        "training_ground_truth_used_for_fitting": False,
        "test_split_used_for_fitting": False,
        "selected_train_maps": int(len(selected)),
        "fit_train_maps": int(len(fit_ids)),
        "validation_train_maps": int(len(validation)),
        "validation_rule": "every-sixth-of-evenly-spaced-selected-training-maps",
        "blend_grid": BLEND_GRID.tolist(),
        "validation_mean_ssim_by_blend": [float(v) for v in validation_scores],
        "selected_blend": selected_alpha,
        "lut_entries": int(len(lut)),
        "lut_dtype": str(lut.dtype),
        "lut_file": "output_calibration.npy",
        "raw_mean_ssim": float(np.mean(raw_scores)),
        "calibrated_mean_ssim": float(np.mean(calibrated_scores)),
        "fp32_mean_ssim": float(np.mean(teacher_scores)),
        "mean_ssim_gap": float(abs(np.mean(teacher_scores) - np.mean(calibrated_scores))),
        "lut_sha256": hashlib.sha256(lut_path.read_bytes()).hexdigest(),
    }
    args.out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2), flush=True)


if __name__ == "__main__":
    main()
