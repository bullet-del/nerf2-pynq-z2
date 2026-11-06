#!/usr/bin/env python3
"""Pure NumPy geometry, metrics and HDMI frame tools for Stage27.

The official NeRF2 spectrum image treats elevation as radius and azimuth as
polar angle after ``np.flipud(spectrum).T``.  These helpers use that exact
coordinate convention and have no PYNQ dependency, so the host and board use
the same reconstruction and display code.
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np


FULL_ROWS = 90
FULL_COLS = 360
DISPLAY_ROWS = 45
DISPLAY_COLS = 90
PANEL_COLS = 29
PANEL_STARTS = (0, 30, 60)
PLOT_TOP = 7
PLOT_ROWS = 20
HEAT_MAX_INDEX = 250
BLACK_INDEX = 254
WHITE_INDEX = 255
DISPLAY_BYTES = DISPLAY_ROWS * DISPLAY_COLS


_FONT_3X5 = {
    "A": ("010", "101", "111", "101", "101"),
    "F": ("111", "100", "110", "100", "100"),
    "G": ("011", "100", "101", "101", "011"),
    "P": ("110", "101", "110", "100", "100"),
    "C": ("011", "100", "100", "100", "011"),
    "T": ("111", "010", "010", "010", "010"),
}


_VIRIDIS_ANCHORS = (
    (68, 1, 84), (71, 44, 122), (59, 81, 139), (44, 113, 142),
    (33, 144, 141), (39, 173, 129), (92, 200, 99), (170, 220, 50),
    (253, 231, 37),
)


def viridis256():
    """Return the same dependency-free 256-entry LUT used by the PL display."""
    lut = np.zeros((256, 3), dtype=np.uint8)
    segment = 255.0 / (len(_VIRIDIS_ANCHORS) - 1)
    for index in range(256):
        anchor = min(int(index / segment), len(_VIRIDIS_ANCHORS) - 2)
        fraction = (index - anchor * segment) / segment
        for channel in range(3):
            lo = _VIRIDIS_ANCHORS[anchor][channel]
            hi = _VIRIDIS_ANCHORS[anchor + 1][channel]
            lut[index, channel] = int(lo * (1.0 - fraction) + hi * fraction)
    lut[BLACK_INDEX] = (0, 0, 0)
    lut[WHITE_INDEX] = (255, 255, 255)
    return lut


def validate_full_map(name, value):
    value = np.asarray(value, dtype=np.float64)
    if value.shape != (FULL_ROWS, FULL_COLS):
        raise ValueError("%s must have shape (90, 360), got %r" % (name, value.shape))
    if not np.isfinite(value).all():
        raise ValueError("%s contains NaN or Inf" % name)
    return value


def regular_grid_from_ray_indices(ray_indices, shape):
    """Validate row-major Cartesian sampling and return source rows/columns."""
    height, width = (int(shape[0]), int(shape[1]))
    indices = np.asarray(ray_indices, dtype=np.int64).reshape(-1)
    if height < 2 or width < 3 or indices.size != height * width:
        raise ValueError("invalid sampled angular grid")
    if np.any(indices < 0) or np.any(indices >= FULL_ROWS * FULL_COLS):
        raise ValueError("ray index is outside the official 90x360 grid")
    matrix = indices.reshape(height, width)
    rows = matrix[:, 0] // FULL_COLS
    cols = matrix[0] % FULL_COLS
    expected = rows[:, None] * FULL_COLS + cols[None, :]
    if not np.array_equal(matrix, expected):
        raise ValueError("ray_indices are not a row-major Cartesian angular grid")
    if np.any(np.diff(rows) <= 0) or np.any(np.diff(cols) <= 0):
        raise ValueError("sampled elevation/azimuth coordinates must be strictly increasing")
    return rows, cols


def reconstruct_official_map(sampled, ray_indices):
    """Reconstruct 90x360 using linear elevation and circular azimuth interpolation."""
    sampled = np.asarray(sampled, dtype=np.float64)
    if sampled.ndim != 2 or not np.isfinite(sampled).all():
        raise ValueError("sampled FPGA map must be a finite 2-D array")
    rows, cols = regular_grid_from_ray_indices(ray_indices, sampled.shape)

    full_columns = np.arange(FULL_COLS, dtype=np.float64)
    azimuth_interpolated = np.empty((len(rows), FULL_COLS), dtype=np.float64)
    periodic_columns = np.r_[cols[-1] - FULL_COLS, cols, cols[0] + FULL_COLS]
    for row in range(len(rows)):
        periodic_values = np.r_[sampled[row, -1], sampled[row], sampled[row, 0]]
        azimuth_interpolated[row] = np.interp(
            full_columns, periodic_columns, periodic_values
        )

    full_rows = np.arange(FULL_ROWS, dtype=np.float64)
    reconstructed = np.empty((FULL_ROWS, FULL_COLS), dtype=np.float64)
    for column in range(FULL_COLS):
        reconstructed[:, column] = np.interp(
            full_rows, rows, azimuth_interpolated[:, column]
        )
    return reconstructed, rows, cols


def _valid_box_mean(value, window=7):
    # Integral-image form keeps compatibility with the older NumPy shipped by
    # common PYNQ-Z2 images (sliding_window_view was added only in NumPy 1.20).
    value = np.asarray(value, dtype=np.float64)
    integral = np.pad(value, ((1, 0), (1, 0)), mode="constant")
    integral = integral.cumsum(axis=0).cumsum(axis=1)
    total = (
        integral[window:, window:]
        - integral[:-window, window:]
        - integral[window:, :-window]
        + integral[:-window, :-window]
    )
    return total / float(window * window)


def ssim_data_range_1(reference, prediction, window=7):
    """Dependency-free equivalent of skimage's default grayscale SSIM interior."""
    x = validate_full_map("SSIM reference", reference)
    y = validate_full_map("SSIM prediction", prediction)
    if window < 3 or window % 2 == 0:
        raise ValueError("SSIM window must be odd and at least 3")
    ux, uy = _valid_box_mean(x, window), _valid_box_mean(y, window)
    uxx, uyy = _valid_box_mean(x * x, window), _valid_box_mean(y * y, window)
    uxy = _valid_box_mean(x * y, window)
    covariance_norm = (window * window) / float(window * window - 1)
    vx = covariance_norm * (uxx - ux * ux)
    vy = covariance_norm * (uyy - uy * uy)
    vxy = covariance_norm * (uxy - ux * uy)
    c1, c2 = 0.01 ** 2, 0.03 ** 2
    numerator = (2.0 * ux * uy + c1) * (2.0 * vxy + c2)
    denominator = (ux * ux + uy * uy + c1) * (vx + vy + c2)
    return float(np.mean(numerator / denominator))


def comparison_metrics(reference, prediction):
    reference = validate_full_map("metric reference", reference)
    prediction = validate_full_map("metric prediction", prediction)
    error = prediction - reference
    mse = float(np.mean(error * error))
    if np.std(reference) == 0 or np.std(prediction) == 0:
        pearson = None
    else:
        pearson = float(np.corrcoef(reference.ravel(), prediction.ravel())[0, 1])
    return {
        "mae": float(np.mean(np.abs(error))),
        "rmse": math.sqrt(mse),
        "max_abs": float(np.max(np.abs(error))),
        "psnr_db_data_range_1": float("inf") if mse == 0 else -10.0 * math.log10(mse),
        "pearson_r": pearson,
        "ssim_data_range_1": ssim_data_range_1(reference, prediction),
    }


def sampled_metrics(reference, prediction):
    reference = np.asarray(reference, dtype=np.float64)
    prediction = np.asarray(prediction, dtype=np.float64)
    if reference.shape != prediction.shape or reference.ndim != 2:
        raise ValueError("sampled metric arrays must be matching 2-D arrays")
    if not np.isfinite(reference).all() or not np.isfinite(prediction).all():
        raise ValueError("sampled metric arrays contain NaN or Inf")
    error = prediction - reference
    mse = float(np.mean(error * error))
    pearson = None if np.std(reference) == 0 or np.std(prediction) == 0 else float(
        np.corrcoef(reference.ravel(), prediction.ravel())[0, 1]
    )
    return {
        "mae": float(np.mean(np.abs(error))),
        "rmse": math.sqrt(mse),
        "max_abs": float(np.max(np.abs(error))),
        "pearson_r": pearson,
    }


def _bilinear_periodic(value, elevation, azimuth):
    """Sample a 90x360 array at floating indices with periodic azimuth."""
    value = validate_full_map("polar source", value)
    elevation = np.clip(np.asarray(elevation, dtype=np.float64), 0.0, FULL_ROWS - 1.0)
    azimuth = np.mod(np.asarray(azimuth, dtype=np.float64), FULL_COLS)
    e0 = np.floor(elevation).astype(np.int64)
    e1 = np.minimum(e0 + 1, FULL_ROWS - 1)
    a0 = np.floor(azimuth).astype(np.int64)
    a1 = (a0 + 1) % FULL_COLS
    fe, fa = elevation - e0, azimuth - a0
    return (
        value[e0, a0] * (1.0 - fe) * (1.0 - fa)
        + value[e1, a0] * fe * (1.0 - fa)
        + value[e0, a1] * (1.0 - fe) * fa
        + value[e1, a1] * fe * fa
    )


def _polar_panel(value):
    """Rasterise official polar orientation into one 29x20 HDMI panel."""
    yy, xx = np.indices((PLOT_ROWS, PANEL_COLS), dtype=np.float64)
    nx = (xx - (PANEL_COLS - 1) / 2.0) / ((PANEL_COLS - 1) / 2.0)
    ny = (yy - (PLOT_ROWS - 1) / 2.0) / ((PLOT_ROWS - 1) / 2.0)
    radius = np.sqrt(nx * nx + ny * ny)
    inside = radius <= 1.0
    # Matplotlib polar: theta=0 points right and increases counter-clockwise.
    theta = np.mod(np.arctan2(-ny, nx), 2.0 * np.pi)
    azimuth = theta * FULL_COLS / (2.0 * np.pi)
    # Official np.flipud(spectrum).T: centre is elevation row 89, edge row 0.
    elevation = (1.0 - np.clip(radius, 0.0, 1.0)) * (FULL_ROWS - 1.0)
    sampled = _bilinear_periodic(value, elevation, azimuth)
    return sampled, inside


def _draw_text(frame, text, panel_start, row=1):
    width = len(text) * 3 + max(0, len(text) - 1)
    start = panel_start + max(0, (PANEL_COLS - width) // 2)
    for char_index, char in enumerate(text):
        glyph = _FONT_3X5[char]
        x0 = start + char_index * 4
        for y, pixels in enumerate(glyph):
            for x, pixel in enumerate(pixels):
                if pixel == "1" and 0 <= x0 + x < DISPLAY_COLS:
                    frame[row + y, x0 + x] = BLACK_INDEX


def compose_hdmi_polar_frame(gt, pc, fpga, color_mode="shared"):
    """Return a 45x90 GT|PC|FPGA polar frame and its scale metadata."""
    maps = [validate_full_map(name, value) for name, value in
            (("GT", gt), ("PC", pc), ("FPGA", fpga))]
    if color_mode not in ("shared", "official"):
        raise ValueError("color_mode must be 'shared' or 'official'")
    if color_mode == "shared":
        lo = min(float(value.min()) for value in maps)
        hi = max(float(value.max()) for value in maps)
        scales = [(lo, hi)] * 3
    else:
        scales = [(float(value.min()), float(value.max())) for value in maps]

    frame = np.full((DISPLAY_ROWS, DISPLAY_COLS), WHITE_INDEX, dtype=np.uint8)
    for start, label, value, (lo, hi) in zip(PANEL_STARTS, ("GT", "PC", "FPGA"), maps, scales):
        polar, inside = _polar_panel(value)
        span = max(hi - lo, 1e-12)
        indices = np.clip(np.rint((polar - lo) * HEAT_MAX_INDEX / span),
                          0, HEAT_MAX_INDEX).astype(np.uint8)
        panel = np.full((PLOT_ROWS, PANEL_COLS), WHITE_INDEX, dtype=np.uint8)
        panel[inside] = indices[inside]
        frame[PLOT_TOP:PLOT_TOP + PLOT_ROWS, start:start + PANEL_COLS] = panel
        _draw_text(frame, label, start)

    frame[:, (29, 59, 89)] = WHITE_INDEX
    # Shared-scale colour bar. In official mode it indicates palette order only.
    ramp = np.rint(np.linspace(0, HEAT_MAX_INDEX, 87)).astype(np.uint8)
    frame[32:35, 1:88] = ramp[None, :]
    frame[31, 1:88] = BLACK_INDEX
    frame[35, 1:88] = BLACK_INDEX
    frame[31:36, (0, 88)] = BLACK_INDEX
    if frame.shape != (DISPLAY_ROWS, DISPLAY_COLS) or frame.nbytes != DISPLAY_BYTES:
        raise AssertionError("internal HDMI geometry error")
    return frame, scales


def write_ppm(path, rgb):
    rgb = np.asarray(rgb, dtype=np.uint8)
    if rgb.shape != (DISPLAY_ROWS, DISPLAY_COLS, 3):
        raise ValueError("HDMI RGB preview must have shape (45, 90, 3)")
    with Path(path).open("wb") as stream:
        stream.write(b"P6\n90 45\n255\n")
        stream.write(rgb.tobytes())
