#!/usr/bin/env python3
"""Pure NumPy helpers for exact 90x360 Stage28 inference and clear rendering."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np

from stage27_display import comparison_metrics, validate_full_map, viridis256


FULL_ROWS = 90
FULL_COLS = 360
SAMPLES_PER_RAY = 64


_FONT_5X7 = {
    "A": ("01110", "10001", "10001", "11111", "10001", "10001", "10001"),
    "C": ("01111", "10000", "10000", "10000", "10000", "10000", "01111"),
    "F": ("11111", "10000", "10000", "11110", "10000", "10000", "10000"),
    "G": ("01111", "10000", "10000", "10111", "10001", "10001", "01111"),
    "N": ("10001", "11001", "10101", "10011", "10001", "10001", "10001"),
    "P": ("11110", "10001", "10001", "11110", "10000", "10000", "10000"),
    "T": ("11111", "00100", "00100", "00100", "00100", "00100", "00100"),
    "/": ("00001", "00010", "00100", "01000", "10000", "00000", "00000"),
    " ": ("00000", "00000", "00000", "00000", "00000", "00000", "00000"),
}


def positional_encode_batch(matrix, multires=10):
    matrix = np.asarray(matrix)
    encoded = [matrix]
    for level in range(multires):
        frequency = 2.0 ** level
        encoded.extend((np.sin(frequency * matrix), np.cos(frequency * matrix)))
    return np.concatenate(encoded, axis=0)


class ExactRayEncoder:
    """Generate the same three 63-D INT8 encodings used by the exported model."""

    def __init__(self, origins, directions, tx, input_scales, near=0.0, far=5.0):
        self.origins = np.asarray(origins, dtype=np.float32).reshape(FULL_ROWS * FULL_COLS, 3)
        self.directions = np.asarray(directions, dtype=np.float32).reshape(FULL_ROWS * FULL_COLS, 3)
        self.tx = np.asarray(tx, dtype=np.float32).reshape(3)
        self.input_scales = np.asarray(input_scales, dtype=np.float64)
        if self.input_scales.shape != (3, 63):
            raise ValueError("input_scales must have shape (3, 63)")
        if not np.isfinite(self.origins).all() or not np.isfinite(self.directions).all():
            raise ValueError("ray geometry contains NaN or Inf")
        self.t = np.linspace(near, far, SAMPLES_PER_RAY, dtype=np.float64)

    def encode_indices(self, ray_indices):
        ray_indices = np.asarray(ray_indices, dtype=np.int64).reshape(-1)
        if np.any(ray_indices < 0) or np.any(ray_indices >= FULL_ROWS * FULL_COLS):
            raise ValueError("ray index outside 0..32399")
        origins = self.origins[ray_indices]
        directions = self.directions[ray_indices]
        # Preserve the Stage24 host builder's float32 sample-point rounding.
        points = origins[:, None, :] + directions[:, None, :] * self.t[None, :, None]
        points = points.astype(np.float32)
        views = np.broadcast_to(directions[:, None, :], points.shape)
        tx = np.broadcast_to(self.tx[None, None, :], points.shape)
        matrices = (
            points.reshape(-1, 3).T,
            views.reshape(-1, 3).T,
            tx.reshape(-1, 3).T,
        )
        quantised = []
        for source, scale in zip(matrices, self.input_scales):
            encoded = positional_encode_batch(source)
            quantised.append(
                np.clip(np.rint(encoded / scale[:, None]), -128, 127).astype(np.int8)
            )
        stacked = np.stack([value.T for value in quantised], axis=1)
        return stacked.reshape(len(ray_indices), SAMPLES_PER_RAY, 3, 63)


def _sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_array(value):
    return hashlib.sha256(np.ascontiguousarray(value).tobytes()).hexdigest()


class PrecomputedRayEncoder:
    """Load host-quantised INT8 inputs one elevation row at a time."""

    def __init__(self, manifest_path, rows_dir, reference_sha256):
        self.manifest_path = Path(manifest_path)
        self.rows_dir = Path(rows_dir)
        self.manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        if self.manifest.get("schema") != "n2-stage28-prequantized-input-v1":
            raise RuntimeError("unsupported Stage28 prequantized input manifest")
        if self.manifest.get("shape") != [90, 360, 64, 3, 63]:
            raise RuntimeError("prequantized input shape is not 90x360x64x3x63")
        if self.manifest.get("dtype") != "int8":
            raise RuntimeError("prequantized input dtype is not int8")
        if self.manifest.get("reference_sha256") != reference_sha256:
            raise RuntimeError("prequantized inputs do not match the supplied reference")
        rows = self.manifest.get("rows")
        if not isinstance(rows, list) or len(rows) != FULL_ROWS:
            raise RuntimeError("prequantized input manifest must contain exactly 90 rows")
        self.rows = {}
        for item in rows:
            row = int(item["row"])
            if row in self.rows or row < 0 or row >= FULL_ROWS:
                raise RuntimeError("invalid or duplicate row in prequantized input manifest")
            self.rows[row] = item
        if set(self.rows) != set(range(FULL_ROWS)):
            raise RuntimeError("prequantized input manifest row set is incomplete")
        self.manifest_sha256 = _sha256_file(self.manifest_path)

    def load_row(self, row):
        row = int(row)
        if row not in self.rows:
            raise ValueError("encoded elevation row outside 0..89")
        metadata = self.rows[row]
        path = self.rows_dir / metadata["file"]
        if not path.is_file():
            raise FileNotFoundError("prequantized input row is missing: %s" % path)
        if path.stat().st_size != int(metadata["bytes"]):
            raise RuntimeError("prequantized input row byte count changed: %s" % path)
        if _sha256_file(path) != metadata["file_sha256"]:
            raise RuntimeError("prequantized input row file hash failed: %s" % path)
        with np.load(path, allow_pickle=False) as data:
            if set(data.files) != {"enc_q", "ray_indices"}:
                raise RuntimeError("prequantized row has unexpected arrays: %s" % path)
            enc_q = np.array(data["enc_q"], dtype=np.int8, copy=True)
            indices = np.array(data["ray_indices"], dtype=np.int64, copy=True)
        expected_indices = np.arange(row * FULL_COLS, (row + 1) * FULL_COLS, dtype=np.int64)
        if enc_q.shape != (FULL_COLS, SAMPLES_PER_RAY, 3, 63):
            raise RuntimeError("prequantized input row shape failed: %s" % path)
        if not np.array_equal(indices, expected_indices):
            raise RuntimeError("prequantized input ray order failed: %s" % path)
        if _sha256_array(enc_q) != metadata["enc_q_sha256"]:
            raise RuntimeError("prequantized input array hash failed: %s" % path)
        return enc_q


def verify_precomputed_against_bundle(encoder, bundle):
    ray_indices = bundle["ray_indices"].astype(np.int64)
    expected = bundle["enc_q"].astype(np.int8).reshape(len(ray_indices), SAMPLES_PER_RAY, 3, 63)
    actual = np.empty_like(expected)
    for row in np.unique(ray_indices // FULL_COLS):
        positions = np.flatnonzero(ray_indices // FULL_COLS == row)
        columns = ray_indices[positions] % FULL_COLS
        actual[positions] = encoder.load_row(int(row))[columns]
    different = int(np.count_nonzero(actual != expected))
    return {
        "source": "host-prequantized-row-shards",
        "platform_runtime_sin_used": False,
        "rays_checked": int(len(ray_indices)),
        "values_checked": int(actual.size),
        "different_values": different,
        "bit_exact": different == 0,
    }


def verify_encoder_against_bundle(encoder, bundle):
    ray_indices = bundle["ray_indices"].astype(np.int64)
    expected = bundle["enc_q"].astype(np.int8).reshape(len(ray_indices), SAMPLES_PER_RAY, 3, 63)
    actual = encoder.encode_indices(ray_indices)
    different = int(np.count_nonzero(actual != expected))
    return {
        "rays_checked": int(len(ray_indices)),
        "values_checked": int(actual.size),
        "different_values": different,
        "bit_exact": different == 0,
    }


def _sample_official_cells(value, radius, theta):
    """Map screen pixels to the exact flat cells used by official pcolormesh.

    This is deliberately nearest-cell rasterisation.  It never invents an
    angular value between two of the original 90x360 samples.
    """
    value = validate_full_map("high-resolution polar source", value)
    radial_cell = np.minimum(
        np.floor(np.clip(radius, 0.0, 1.0) * FULL_ROWS).astype(np.int64),
        FULL_ROWS - 1,
    )
    elevation = FULL_ROWS - 1 - radial_cell
    azimuth = np.minimum(
        np.floor(np.mod(theta, 2.0 * np.pi) * FULL_COLS / (2.0 * np.pi)).astype(np.int64),
        FULL_COLS - 1,
    )
    return value[elevation, azimuth]


def render_polar_rgb(value, size, scale):
    """Rasterise one complete 90x360 map with official polar orientation."""
    if size < 180:
        raise ValueError("polar panel size must be at least 180 pixels")
    yy, xx = np.indices((size, size), dtype=np.float64)
    centre = (size - 1) / 2.0
    nx = (xx - centre) / centre
    ny = (yy - centre) / centre
    radius = np.sqrt(nx * nx + ny * ny)
    inside = radius <= 1.0
    theta = np.mod(np.arctan2(-ny, nx), 2.0 * np.pi)
    sampled = _sample_official_cells(value, radius, theta)
    lo, hi = scale
    span = max(float(hi) - float(lo), 1e-12)
    # 250 colour entries are used because the inherited LUT reserves entries
    # 254/255 for black/white.  The source cell values themselves stay intact.
    indices = np.clip(np.rint((sampled - lo) * 250.0 / span), 0, 250).astype(np.uint8)
    rgb = np.full((size, size, 3), 255, dtype=np.uint8)
    palette = viridis256()
    rgb[inside] = palette[indices[inside]]
    # Thin black outline makes the three equal coordinate domains explicit.
    boundary = inside & (radius >= 1.0 - 2.0 / size)
    rgb[boundary] = 0
    return rgb


def _draw_text(canvas, text, centre_x, top, scale=7):
    width = (len(text) * 5 + max(0, len(text) - 1)) * scale
    left = int(round(centre_x - width / 2.0))
    for character_index, character in enumerate(text):
        glyph = _FONT_5X7[character]
        x0 = left + character_index * 6 * scale
        for row, pixels in enumerate(glyph):
            for column, pixel in enumerate(pixels):
                if pixel == "1":
                    y1, y2 = top + row * scale, top + (row + 1) * scale
                    x1, x2 = x0 + column * scale, x0 + (column + 1) * scale
                    canvas[y1:y2, x1:x2] = 0


def compose_clear_three_way(
    gt, pc, fpga, width=1280, height=720, color_mode="official",
    show_colorbar=False,
):
    """Create a clear RGB GT|PC|FPGA frame from three complete 90x360 maps."""
    maps = [validate_full_map(name, value) for name, value in
            (("GT", gt), ("PC", pc), ("FPGA", fpga))]
    if color_mode not in ("official", "shared"):
        raise ValueError("color_mode must be 'official' or 'shared'")
    if width < 960 or height < 540:
        raise ValueError("clear three-way frame requires at least 960x540")
    margin = max(20, width // 100)
    gap = max(12, width // 120)
    panel_size = min((width - 2 * margin - 2 * gap) // 3, height - 230)
    plot_top = 92
    if panel_size < 180:
        raise ValueError("output canvas is too small for three polar panels")
    used = 3 * panel_size + 2 * gap
    left = (width - used) // 2
    starts = [left + index * (panel_size + gap) for index in range(3)]
    centres = [start + panel_size // 2 for start in starts]

    if color_mode == "shared":
        lo = min(float(value.min()) for value in maps)
        hi = max(float(value.max()) for value in maps)
        scales = [(lo, hi)] * 3
    else:
        scales = [(float(value.min()), float(value.max())) for value in maps]

    canvas = np.full((height, width, 3), 255, dtype=np.uint8)
    for start, value, scale_pair in zip(starts, maps, scales):
        canvas[plot_top:plot_top + panel_size, start:start + panel_size] = \
            render_polar_rgb(value, panel_size, scale_pair)
    label_scale = max(5, min(9, width // 160))
    for label, centre in zip(("GT", "PC", "FPGA"), centres):
        _draw_text(canvas, label, centre, 18, label_scale)

    if show_colorbar:
        bar_left, bar_right = width // 8, width - width // 8
        bar_top, bar_height = height - 92, 34
        ramp = np.rint(np.linspace(0, 250, bar_right - bar_left)).astype(np.uint8)
        canvas[bar_top:bar_top + bar_height, bar_left:bar_right] = viridis256()[ramp][None, :, :]
        canvas[bar_top - 3:bar_top, bar_left - 3:bar_right + 3] = 0
        canvas[bar_top + bar_height:bar_top + bar_height + 3, bar_left - 3:bar_right + 3] = 0
        canvas[bar_top - 3:bar_top + bar_height + 3, bar_left - 3:bar_left] = 0
        canvas[bar_top - 3:bar_top + bar_height + 3, bar_right:bar_right + 3] = 0
    return canvas, scales


def prepare_physical_hdmi_frame(rgb, display_aspect="four_three_stretch"):
    """Compensate a monitor that stretches a 16:9 timing to a 4:3 panel.

    The saved/browser image always keeps square pixels.  Only the frame sent to
    the physical HDMI output is vertically pre-compressed.  A 4:3 display that
    expands the 1280x720 timing then restores each polar panel to a circle.
    """
    from PIL import Image
    value = np.asarray(rgb, dtype=np.uint8)
    if value.shape != (720, 1280, 3):
        raise ValueError("physical HDMI frame must be 720x1280 RGB")
    mode = str(display_aspect)
    if mode == "square_pixels":
        return np.array(value, copy=True), {
            "display_aspect": mode, "vertical_precompression": 1.0,
            "content_box": [0, 0, 1280, 720],
        }
    if mode != "four_three_stretch":
        raise ValueError("hdmi_display_aspect must be square_pixels or four_three_stretch")
    target_height = 540
    try:
        resample = Image.Resampling.LANCZOS
    except AttributeError:
        resample = Image.LANCZOS
    resized = np.asarray(
        Image.fromarray(value, mode="RGB").resize((1280, target_height), resample),
        dtype=np.uint8,
    )
    output = np.full_like(value, 255)
    top = (720 - target_height) // 2
    output[top:top + target_height] = resized
    return output, {
        "display_aspect": mode, "vertical_precompression": 0.75,
        "content_box": [0, top, 1280, target_height],
        "purpose": "restore circular polar panels on a 4:3 display stretching 1280x720",
    }


def save_rgb_png(path, rgb):
    from PIL import Image
    Image.fromarray(np.asarray(rgb, dtype=np.uint8), mode="RGB").save(Path(path))


__all__ = [
    "ExactRayEncoder", "PrecomputedRayEncoder", "comparison_metrics",
    "compose_clear_three_way", "prepare_physical_hdmi_frame", "save_rgb_png", "verify_encoder_against_bundle",
    "verify_precomputed_against_bundle",
]
