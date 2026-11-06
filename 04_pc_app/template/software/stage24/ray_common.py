"""Shared S23 ray geometry and official NeRF2 spectrum equations.

The implementation intentionally preserves the upstream spectrum renderer's
exclusive attenuation/phase prefixes and its one-radian initial phase.
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np


def quaternion_matrix_xyzw(q):
    """Return a 3x3 rotation matrix for scipy/PyTorch xyzw quaternion order."""
    x, y, z, w = np.asarray(q, dtype=np.float64)
    n = x*x + y*y + z*z + w*w
    if n < np.finfo(float).eps:
        return np.eye(3)
    s = 2.0 / n
    return np.array([
        [1-s*(y*y+z*z), s*(x*y-z*w), s*(x*z+y*w)],
        [s*(x*y+z*w), 1-s*(x*x+z*z), s*(y*z-x*w)],
        [s*(x*z-y*w), s*(y*z+x*w), 1-s*(x*x+y*y)],
    ], dtype=np.float64)


def official_s23_rays(gateway_pos, gateway_orientation,
                      n_elevation=90, n_azimuth=360):
    """Match Spectrum_dataset.gen_rays_spectrum, flattened elevation-major."""
    az = np.linspace(1.0, 360.0, n_azimuth) * math.pi / 180.0
    el = np.linspace(1.0, 90.0, n_elevation) * math.pi / 180.0
    azm = np.tile(az, n_elevation)
    elm = np.repeat(el, n_azimuth)
    local = np.stack((np.cos(elm)*np.cos(azm),
                      np.cos(elm)*np.sin(azm), np.sin(elm)), axis=1)
    direction = local @ quaternion_matrix_xyzw(gateway_orientation).T
    origin = np.repeat(np.asarray(gateway_pos, np.float64)[None], len(direction), 0)
    return origin.astype(np.float32), direction.astype(np.float32)


def sample_points_np(origin, direction, near=0.0, far=5.0, samples=64):
    t = np.linspace(near, far, samples, dtype=np.float64)
    pts = origin[..., None, :] + direction[..., None, :] * t[None, :, None]
    return pts.astype(np.float32), np.broadcast_to(t, pts.shape[:-1]).copy()


def leaky_abs(x, slope=0.01):
    return np.abs(np.where(x >= 0.0, x, slope*x))


def spectrum_from_raw_np(raw, t, direction):
    """CPU NumPy equivalent of upstream Renderer_spectrum.raw2outputs."""
    raw = np.asarray(raw, np.float64)
    t = np.asarray(t, np.float64)
    direction = np.asarray(direction, np.float64)
    d = np.concatenate((np.diff(t, axis=-1), np.full_like(t[..., :1], 1e10)), -1)
    d *= np.linalg.norm(direction, axis=-1, keepdims=True)
    aa, ap, sa, sp = (raw[..., i] for i in range(4))
    ap = 2*np.pi/(1+np.exp(-ap))
    sp = 2*np.pi/(1+np.exp(-sp))
    aa, sa = leaky_abs(aa), leaky_abs(sa)
    alpha = 1-np.exp(-aa*d)
    attenuation = np.cumprod(np.concatenate((np.ones_like(t[..., :1]),
        1-alpha+1e-10), -1), -1)[..., :-1]
    phase = np.cumsum(np.concatenate((np.ones_like(t[..., :1]), ap*d), -1), -1)[..., :-1]
    path = np.concatenate((t[..., 1:], np.full_like(t[..., :1], 1e10)), -1)
    signal = np.sum(sa*np.exp(1j*sp)*attenuation*np.exp(1j*phase)*(0.025/path), -1)
    return np.abs(signal)


def load_s23_metadata(datadir: Path, spectrum_id: str):
    import csv
    import yaml
    info = yaml.safe_load((datadir/'gateway_info.yml').read_text(encoding='utf-8'))['gateway1']
    with (datadir/'tx_pos.csv').open(newline='', encoding='utf-8') as f:
        rows = list(csv.DictReader(f))
    row = rows[int(spectrum_id)-1]
    tx = np.array([float(row[k]) for k in ('x', 'y', 'z')], np.float32)
    return np.asarray(info['position'], np.float32), info['orientation'], tx
