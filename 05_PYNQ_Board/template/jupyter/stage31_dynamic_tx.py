#!/usr/bin/env python3
"""Exact dynamic-Tx encoder for the Stage29/31 90x360 ray stream."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np

from stage28_exact_common import PrecomputedRayEncoder, positional_encode_batch


BUILD_ID = "stage31-dynamic-tx-template-v1"
ROWS, COLS, SAMPLES, FEATURES = 90, 360, 64, 63


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def quantize_tx(tx, input_scales):
    tx = np.asarray(tx, dtype=np.float32).reshape(3)
    if not np.isfinite(tx).all():
        raise ValueError("Tx coordinate contains NaN or Inf")
    scales = np.asarray(input_scales, dtype=np.float64)
    if scales.shape == (3, 63):
        scales = scales[2]
    if scales.shape != (63,) or np.any(scales <= 0) or not np.isfinite(scales).all():
        raise ValueError("Tx input scales must contain 63 positive finite values")
    encoded = positional_encode_batch(tx.reshape(3, 1))[:, 0]
    return np.clip(np.rint(encoded / scales), -128, 127).astype(np.int8)


class DynamicTxRowEncoder:
    """Reuse exact fixed-Rx pts/view rows and replace only the Tx encoding."""

    def __init__(self, template_manifest, template_rows_dir, template_reference,
                 model_manifest, tx):
        self.template_manifest = Path(template_manifest)
        self.template_rows_dir = Path(template_rows_dir)
        self.template_reference = Path(template_reference)
        self.model_manifest_path = Path(model_manifest)
        for path in (self.template_manifest, self.template_reference, self.model_manifest_path):
            if not path.is_file():
                raise FileNotFoundError(path)
        reference_hash = sha256_file(self.template_reference)
        self.template = PrecomputedRayEncoder(
            self.template_manifest, self.template_rows_dir, reference_hash
        )
        model = json.loads(self.model_manifest_path.read_text(encoding="utf-8"))
        if model.get("schema") != "n2-ray-export-v1":
            raise RuntimeError("unsupported Stage31 ray-model manifest")
        self.input_scales = np.asarray(model["input_scales"], dtype=np.float64)
        if self.input_scales.shape != (3, FEATURES):
            raise RuntimeError("Stage31 input scale shape changed")
        self.checkpoint_sha256 = str(model["checkpoint_sha256"])
        self.template_manifest_sha256 = sha256_file(self.template_manifest)
        self.set_tx(tx)

    def set_tx(self, tx):
        self.tx = np.asarray(tx, dtype=np.float32).reshape(3)
        self.tx_q = quantize_tx(self.tx, self.input_scales)
        self.tx_q_sha256 = hashlib.sha256(self.tx_q.tobytes()).hexdigest()

    def load_row(self, row):
        encoded = self.template.load_row(int(row))
        # load_row already returns a writable copy.  A single 63-byte vector is
        # broadcast across all 360*64 queries without recomputing pts/view.
        encoded[:, :, 2, :] = self.tx_q
        return encoded

    def encode_indices(self, ray_indices):
        indices = np.asarray(ray_indices, dtype=np.int64).reshape(-1)
        if np.any(indices < 0) or np.any(indices >= ROWS * COLS):
            raise ValueError("ray index outside 0..32399")
        result = np.empty((len(indices), SAMPLES, 3, FEATURES), dtype=np.int8)
        for row in np.unique(indices // COLS):
            positions = np.flatnonzero(indices // COLS == row)
            result[positions] = self.load_row(int(row))[indices[positions] % COLS]
        return result

    def identity(self):
        return {
            "build_id": BUILD_ID,
            "template_manifest_sha256": self.template_manifest_sha256,
            "checkpoint_sha256": self.checkpoint_sha256,
            "tx": [float(value) for value in self.tx],
            "tx_q_sha256": self.tx_q_sha256,
            "pts_view_reused_exactly": True,
            "tx_runtime_transcendental_values": 60,
            "tx_broadcast_queries": ROWS * COLS * SAMPLES,
        }


def verify_known_samples(root):
    """Verify all 391,910,400 encoded values across the three Stage30 cases."""
    root = Path(root)
    template_manifest = root / "test_vectors/stage30_blind/s23_01181/input_manifest.json"
    template_rows = root / "test_vectors/stage30_blind/s23_01181/enc_rows"
    template_reference = root / "sim/stage30_blind/s23_01181/reference_s23_01181.npz"
    model_manifest = root / "test_vectors/stage24_ray/manifest_ray.json"
    cases = {}
    total_different = 0
    total_values = 0
    for spectrum_id in ("00761", "01181", "05380"):
        sentinel_path = root / ("test_vectors/stage30_blind/s23_%s/"
                                "sentinel_8x16_sparse_exact.npz" % spectrum_id)
        with np.load(sentinel_path, allow_pickle=False) as sentinel:
            tx = np.asarray(sentinel["tx"], dtype=np.float32)
        encoder = DynamicTxRowEncoder(
            template_manifest, template_rows, template_reference, model_manifest, tx
        )
        different = 0
        values = 0
        for row in range(ROWS):
            actual = encoder.load_row(row)
            target_path = root / ("test_vectors/stage30_blind/s23_%s/enc_rows/"
                                  "row_%03d.npz" % (spectrum_id, row))
            with np.load(target_path, allow_pickle=False) as target:
                expected = np.asarray(target["enc_q"], dtype=np.int8)
            different += int(np.count_nonzero(actual != expected))
            values += int(actual.size)
        cases[spectrum_id] = {
            "tx": [float(value) for value in tx], "values": values,
            "different_values": different, "bit_exact": different == 0,
        }
        total_different += different
        total_values += values
    return {
        "schema": "n2-stage31-dynamic-tx-regression-v1",
        "cases": cases,
        "values_checked": total_values,
        "different_values": total_different,
        "bit_exact": total_different == 0,
    }


__all__ = ["BUILD_ID", "DynamicTxRowEncoder", "quantize_tx", "verify_known_samples"]
