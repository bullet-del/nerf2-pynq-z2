#!/usr/bin/env python3
"""Regenerate the independent Stage37 S23 golden vectors for one runtime model."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np


def write_memh(path: Path, values):
    path.write_text("\n".join("%016x" % int(x) for x in values) + "\n", encoding="ascii")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source-root", type=Path, required=True)
    ap.add_argument("--tv", type=Path, required=True)
    ap.add_argument("--manifest", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--base-vector", type=Path,
                    help="Independent 1057-word two-ray input; header is rewritten for cache build")
    args = ap.parse_args()
    root = args.source_root.absolute()
    for p in (root / "software/stage5", root / "software/stage8", root / "software/stage24"):
        sys.path.insert(0, str(p))
    from n2_stream import P, parse_tv3  # noqa: E402
    from gen_field_vectors import forward_fixed_batch, rebuild_layers  # noqa: E402
    from fixed_renderer import make_luts, render_raw_int8  # noqa: E402

    base_vector = args.base_vector or (root / "sim/stage34/two_ray_dual_stream.memh")
    exact = np.asarray([int(v, 16) for v in base_vector.read_text().split()],
                       dtype=np.uint64)
    if exact.size != 1057 or int(exact[0]) != 0x4000000000000002:
        raise RuntimeError("unexpected Stage34 independent two-ray vector")
    record = exact[1:].reshape(1, 1056)
    constants = record[:, :32]
    point_words = record[:, 32:].reshape(1, 64, 2, 8)
    encoded = np.zeros((2, 64, 3, 63), dtype=np.int8)
    constant_bytes = constants.reshape(1, 4, 8).view(np.int8).reshape(1, 4, 64)
    encoded[0, :, 1], encoded[0, :, 2] = constant_bytes[0, 0, :63], constant_bytes[0, 1, :63]
    encoded[1, :, 1], encoded[1, :, 2] = constant_bytes[0, 2, :63], constant_bytes[0, 3, :63]
    points = point_words.view(np.int8).reshape(1, 64, 2, 64)
    encoded[0, :, 0], encoded[1, :, 0] = points[0, :, 0, :63], points[0, :, 1, :63]

    parsed = parse_tv3(str(args.tv), P)
    weights, biases, shifts = rebuild_layers(parsed)
    flat = encoded.reshape(128, 3, 63)
    raw = forward_fixed_batch(weights, shifts, biases,
                              [flat[:, kind, :].T for kind in range(3)]).reshape(2, 64, 4)
    att_u16 = np.ascontiguousarray(raw[:, :, :2]).view("<u2").reshape(2, 64)
    cache_words = att_u16[0].astype(np.uint64) | (att_u16[1].astype(np.uint64) << np.uint64(16))

    quant = json.loads(args.manifest.read_text(encoding="utf-8"))
    lut = make_luts(np.asarray(quant["final_scales"], dtype=np.float64))
    expected = [int(render_raw_int8(raw[r:r+1], lut)[0][0]) for r in range(2)]
    build_stream = exact.copy(); build_stream[0] = np.uint64(0x6000000000000002)
    cached_stream = np.empty(97, dtype=np.uint64)
    cached_stream[0] = np.uint64(0x5000000000000002)
    cached_stream[1:33] = constants.reshape(-1)
    cached_stream[33:] = cache_words
    args.out.mkdir(parents=True, exist_ok=True)
    write_memh(args.out / "two_ray_cache_build.memh", build_stream)
    write_memh(args.out / "two_ray_cached_run.memh", cached_stream)
    write_memh(args.out / "two_ray_expected_cache.memh", cache_words)
    report = {"schema": "n2-stage37-independent-sim-vector-v1", "expected_q15": expected,
              "first_raw_per_ray": [raw[0, 0].astype(int).tolist(), raw[1, 0].astype(int).tolist()],
              "cache_words": 64, "tv": str(args.tv.absolute())}
    (args.out / "two_ray_stage37.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
