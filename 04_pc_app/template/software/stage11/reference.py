"""Auditable CPU/CUDA reference. Spectrum follows upstream implementation, not Eq.13 power.
RSSI mode explicitly selects upstream's 36-direction compatibility or all supplied rays.
The latter changes the model and requires retraining. No checkpoint fallback.
"""
import importlib.util
import sys
from pathlib import Path
import numpy as np
import torch
import torch.nn.functional as F

UPSTREAM = Path(__file__).resolve().parents[1] / 'third_party' / 'nerf2'


def _load_official_nerf2_class():
    """Load the bundled upstream class without using the ambiguous name ``model``.

    The Windows judge package also contains ``pc_app/model`` for the checkpoint.
    Depending on the launch directory, Python can otherwise resolve that data
    directory as a namespace package called ``model`` before it reaches the
    upstream NeRF2 source file.
    """
    source = UPSTREAM / 'model.py'
    if not source.is_file():
        raise FileNotFoundError(
            'Bundled official NeRF2 model source is missing: ' + str(source)
        )
    module_name = '_n2_bundled_official_nerf2_model'
    cached = sys.modules.get(module_name)
    if cached is None:
        spec = importlib.util.spec_from_file_location(module_name, source)
        if spec is None or spec.loader is None:
            raise ImportError('Cannot create an import specification for ' + str(source))
        cached = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = cached
        try:
            spec.loader.exec_module(cached)
        except Exception:
            sys.modules.pop(module_name, None)
            raise
    cls = getattr(cached, 'NeRF2', None)
    if cls is None:
        raise ImportError('Official source does not define NeRF2: ' + str(source))
    return cls


NeRF2 = _load_official_nerf2_class()


def load_model(checkpoint, config, device='cpu'):
    import yaml
    cfg = yaml.safe_load(Path(config).read_text(encoding='utf-8'))
    model = NeRF2(**cfg['networks']).to(device)
    ck = torch.load(checkpoint, map_location=device)
    model.load_state_dict(ck['nerf2_network_state_dict'], strict=True)
    model.eval()
    return model, cfg


def sample_points(origin, direction, near, far, samples):
    if samples < 2 or far <= near or near < 0:
        raise ValueError('Require samples>=2 and 0<=near<far')
    t = torch.linspace(near, far, samples, device=origin.device, dtype=origin.dtype)
    t = t.expand(origin.shape[:-1] + (samples,))
    return origin[..., None, :] + t[..., None] * direction[..., None, :], t


def spectrum_from_raw(raw, t, direction):
    """Exact expression order of upstream spectrum, including exclusive prefixes and 1 rad offset."""
    d = torch.cat((t[..., 1:] - t[..., :-1], torch.full_like(t[..., :1], 1e10)), -1)
    d = d * torch.linalg.vector_norm(direction, dim=-1, keepdim=True)
    aa, ap, sa, sp = raw.unbind(-1)
    ap, sp = torch.sigmoid(ap)*2*np.pi, torch.sigmoid(sp)*2*np.pi
    aa, sa = F.leaky_relu(aa).abs(), F.leaky_relu(sa).abs()
    alpha = 1-torch.exp(-aa*d)
    one = torch.ones_like(t[..., :1])
    attenuation = torch.cumprod(torch.cat((one, 1-alpha+1e-10), -1), -1)[..., :-1]
    phase = torch.cumsum(torch.cat((one, ap*d), -1), -1)[..., :-1]
    path = torch.cat((t[..., 1:], torch.full_like(t[..., :1], 1e10)), -1)
    return (sa*torch.exp(1j*sp)*attenuation*torch.exp(1j*phase)*(0.025/path)).sum(-1).abs()


def render_spectrum(model, tx, origin, direction, near=0, far=5, samples=64):
    pts, t = sample_points(origin, direction, near, far, samples)
    raw = model(pts, direction[..., None, :].expand_as(pts), tx[..., None, :].expand_as(pts))
    return spectrum_from_raw(raw, t, direction)


def rssi_complex(raw, t, direction, frequency_hz=2.4e9):
    """Per-ray complex sums; sum directions BEFORE abs for corrected chunking."""
    d = torch.cat((t[..., 1:] - t[..., :-1], torch.full_like(t[..., :1], 1e10)), -1)
    d = d * torch.linalg.vector_norm(direction, dim=-1, keepdim=True)
    aa, ap, sa, sp = raw.unbind(-1)
    ap, sp = torch.sigmoid(ap)*2*np.pi-np.pi, torch.sigmoid(sp)*2*np.pi-np.pi
    aa, sa = F.leaky_relu(aa).abs(), F.leaky_relu(sa).abs()
    amp = torch.exp(torch.cumsum(-aa*d, -1))
    phase = torch.exp(1j*torch.cumsum(ap+2*np.pi*d/(299792458.0/frequency_hz), -1))
    return (sa*torch.exp(1j*sp)*amp*phase).sum(-1)


def directions(device='cpu', dtype=torch.float32):
    a = torch.arange(0, 360, 10, device=device, dtype=dtype)*np.pi/180
    b = torch.arange(10, 91, 10, device=device, dtype=dtype)*np.pi/180
    b, a = torch.meshgrid(b, a, indexing='ij')
    return torch.stack((a.cos()*b.cos(), a.sin()*b.cos(), b.sin()), -1).reshape(-1, 3)


def render_rssi(model, tx, rx, near=0, far=5, samples=32, frequency_hz=2.4e9,
                mode='all324', chunk=36):
    if mode not in ('all324', 'upstream36'):
        raise ValueError(mode)
    dirs = directions(tx.device, tx.dtype)
    if mode == 'upstream36':
        dirs = dirs[:36]
    total = torch.zeros(tx.shape[0], dtype=torch.complex64, device=tx.device)
    for start in range(0, len(dirs), chunk):
        dr = dirs[start:start+chunk][None].expand(len(tx), -1, -1)
        origin = rx[:, None].expand_as(dr)
        pts, t = sample_points(origin, dr, near, far, samples)
        raw = model(pts, dr[..., None, :].expand_as(pts), tx[:, None, None, :].expand_as(pts))
        total = total + rssi_complex(raw, t, dr, frequency_hz).sum(-1)
    # Upstream uses an affine RSSI surrogate, not a physical voltage conversion.
    return -100*(1-total.abs())
