#!/usr/bin/env python3
"""Portable bit-accurate reference for Stage39 sparse INT8/Q15 selected rays.

This module has no PYNQ dependency.  It reads the same runtime package as the
board, re-encodes Tx on the PC, executes the sparse integer MLP and reproduces
the 64-sample Q15 renderer operation by operation.
"""
from __future__ import annotations

import hashlib
import json
import math
import time
from pathlib import Path

import numpy as np


def sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def positional_encode(value: np.ndarray) -> np.ndarray:
    value = np.asarray(value, np.float32).reshape(3)
    fields = [value]
    for frequency in 2.0 ** np.arange(10, dtype=np.float32):
        fields.extend((np.sin(value * frequency), np.cos(value * frequency)))
    return np.concatenate(fields).astype(np.float64)


def quantize_tx(tx: np.ndarray, input_scales: np.ndarray) -> np.ndarray:
    scales = np.asarray(input_scales, np.float64)
    if scales.shape == (3, 63):
        scales = scales[2]
    if scales.shape != (63,) or np.any(scales <= 0):
        raise ValueError("Tx scale shape/value error")
    return np.clip(np.rint(positional_encode(tx) / scales), -128, 127).astype(np.int8)


def tx_words(tx_q: np.ndarray) -> np.ndarray:
    raw = np.zeros(64, np.int8)
    raw[:63] = np.asarray(tx_q, np.int8).reshape(63)
    return np.ascontiguousarray(raw).view("<u8").reshape(8)


class ConstantSource:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.manifest = json.loads((self.root / "manifest.json").read_text(encoding="utf-8"))
        if self.manifest.get("schema") != "n2-stage37-signal-constant-packets-v1":
            raise RuntimeError("unsupported constant packet schema")

    def load(self, index: int, tx_q: np.ndarray):
        entry = self.manifest["packets"][int(index)]
        path = self.root / entry["file"]
        if path.stat().st_size != int(entry["file_bytes"]) or sha256(path) != entry["sha256"]:
            raise RuntimeError("constant packet integrity failure: " + str(path))
        with np.load(path, allow_pickle=False) as values:
            records = np.asarray(values["value"], np.uint64).copy()
        words = tx_words(tx_q)
        records[:, 8:16] = words
        records[:, 24:32] = words
        return records, entry


def descriptor(value: int) -> dict[str, int | bool]:
    value = int(value)
    return {"layer": value & 15, "group": (value >> 4) & 3,
            "k": (value >> 6) & 511, "weight_base": (value >> 15) & 4095,
            "index_base": (value >> 27) & 4095, "bias_address": (value >> 39) & 31,
            "outputs_valid": (value >> 44) & 255, "last_group": bool((value >> 52) & 1)}


class IntegerReference:
    def __init__(self, scene_root: Path):
        self.scene_root = Path(scene_root)
        model_path = self.scene_root / "model/stage37_runtime_model.npz"
        with np.load(model_path, allow_pickle=False) as model:
            self.weights = np.asarray(model["weights_u8"], np.uint8).view(np.int8).copy()
            self.indices = np.asarray(model["indices_u16"], np.uint16).copy()
            self.biases = np.asarray(model["biases_i16"], np.int16).copy()
            self.descriptors = [descriptor(value) for value in model["descriptors_u64"]]
            self.shifts = np.asarray(model["shifts_u8"], np.uint8).copy()
            self.att_factor = np.asarray(model["att_factor_u16"], np.uint16).copy()
            self.att_pcos = np.asarray(model["att_phase_cos_i16"], np.int16).copy()
            self.att_psin = np.asarray(model["att_phase_sin_i16"], np.int16).copy()
            self.sig_amp = np.asarray(model["sig_amp_u16"], np.uint16).copy()
            self.sig_pcos = np.asarray(model["sig_phase_cos_i16"], np.int16).copy()
            self.sig_psin = np.asarray(model["sig_phase_sin_i16"], np.int16).copy()
            self.path_loss = np.asarray(model["path_loss_u16"], np.uint16).copy()
        quant = json.loads((self.scene_root / "model/quant_manifest.json").read_text(encoding="utf-8"))
        self.input_scales = np.asarray(quant["input_scales"], np.float64)
        self.cache = np.load(self.scene_root / "model/attenuation_u16.npy",
                             mmap_mode="r", allow_pickle=False).reshape(32400, 64)
        self.constants = ConstantSource(self.scene_root / "model/constant_packets")

    def _layer(self, layer: int, source: np.ndarray, relu: bool) -> np.ndarray:
        source = np.asarray(source, np.int8).reshape(-1)
        parts = []
        for item in self.descriptors:
            if item["layer"] != int(layer):
                continue
            k = int(item["k"])
            indices = self.indices[int(item["index_base"]):int(item["index_base"])+k].astype(np.int64)
            weights = self.weights[int(item["weight_base"]):int(item["weight_base"])+k].astype(np.int64)
            accumulator = source[indices].astype(np.int64) @ weights
            value = np.right_shift(accumulator, int(self.shifts[layer]))
            value += self.biases[int(item["bias_address"])].astype(np.int64)
            if relu:
                value = np.maximum(value, 0)
            parts.append(np.clip(value[:int(item["outputs_valid"])], -128, 127).astype(np.int8))
        if not parts:
            raise RuntimeError(f"missing layer {layer} descriptor")
        return np.concatenate(parts)

    @staticmethod
    def _constant_input(record: np.ndarray, lane: int) -> np.ndarray:
        record = np.asarray(record, np.uint64).reshape(32)
        base = 0 if int(lane) == 0 else 16
        view = np.ascontiguousarray(record[base:base+8]).view(np.int8)
        tx = np.ascontiguousarray(record[base+8:base+16]).view(np.int8)
        result = np.zeros(382, np.int8)
        result[256:319], result[319:382] = view[:63], tx[:63]
        return result

    def signal(self, record: np.ndarray, lane: int) -> np.ndarray:
        value = self._layer(10, self._constant_input(record, lane), True)
        value = self._layer(11, value, True)
        return self._layer(12, value, False)[:2]

    def renderer(self, signal: np.ndarray, attenuation: np.ndarray) -> np.uint32:
        signal = np.asarray(signal, np.int8).reshape(2)
        attenuation = np.asarray(attenuation, np.uint16).reshape(64)
        sig_amp = int(self.sig_amp[(int(signal[0]) & 255) ^ 128])
        sig_pc = int(self.sig_pcos[(int(signal[1]) & 255) ^ 128])
        sig_ps = int(self.sig_psin[(int(signal[1]) & 255) ^ 128])
        factor, phase_re, phase_im, acc_re, acc_im = 32768, 17705, 27573, 0, 0
        for sample, packed_value in enumerate(attenuation):
            packed = int(packed_value)
            ia, ip = (packed & 255) ^ 128, ((packed >> 8) & 255) ^ 128
            dr = ((sig_pc * phase_re) >> 15) - ((sig_ps * phase_im) >> 15)
            di = ((sig_pc * phase_im) >> 15) + ((sig_ps * phase_re) >> 15)
            next_re = ((phase_re * int(self.att_pcos[ip])) >> 15) - ((phase_im * int(self.att_psin[ip])) >> 15)
            next_im = ((phase_re * int(self.att_psin[ip])) >> 15) + ((phase_im * int(self.att_pcos[ip])) >> 15)
            amplitude = (sig_amp * factor) >> 15
            amplitude = (amplitude * int(self.path_loss[sample])) >> 15
            acc_re += (amplitude * dr) >> 15
            acc_im += (amplitude * di) >> 15
            factor = (factor * int(self.att_factor[ia])) >> 15
            phase_re, phase_im = next_re, next_im
        return np.uint32(math.isqrt(acc_re*acc_re + acc_im*acc_im))

    def run_indices(self, tx: np.ndarray, indices: np.ndarray) -> tuple[np.ndarray, float]:
        started = time.perf_counter()
        indices = np.asarray(indices, np.int64).reshape(-1)
        tx_q = quantize_tx(tx, self.input_scales)
        result = np.empty(len(indices), np.uint32)
        loaded = {}
        for output_index, ray in enumerate(indices):
            packet = int(ray // 1800)
            if packet not in loaded:
                loaded[packet] = self.constants.load(packet, tx_q)[0]
            local = int(ray - packet*1800)
            record = loaded[packet][local//2]
            result[output_index] = self.renderer(self.signal(record, local & 1), self.cache[ray])
        return result, time.perf_counter() - started

