#!/usr/bin/env python3
"""Build an exact, checkpoint-specific sparse model image for Stage29.

Only input columns whose complete P=128 output group is zero are omitted.  A
reconstruction check and the three official TV3 vectors prove that this does
not change the fixed-point network result.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve()
ROOT = HERE.parents[2]
for directory in (ROOT / "software" / "stage5", ROOT / "software" / "stage8"):
    sys.path.insert(0, str(directory))

from n2_stream import P as SOURCE_P, parse_tv3  # noqa: E402
from gen_field_vectors import TOPO, forward_fixed_batch, rebuild_layers  # noqa: E402


TARGET_P = 128
MODEL_SCHEMA = "n2-stage29-sparse-p128-v1"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _memh_line_unsigned_le(raw: bytes) -> str:
    """Verilog bit 0 is the first byte/lane; readmemh writes MS nibble first."""
    return raw[::-1].hex()


def _decode_tv3_enc(parsed):
    _, enc, _, _, _, _, npts, enc_nwe = parsed
    result = np.zeros((npts, 3, 63), dtype=np.int8)
    for point in range(npts):
        for kind in range(3):
            raw = b"".join(enc[point][kind, word] for word in range(enc_nwe))
            result[point, kind] = np.frombuffer(raw[:63], dtype=np.int8)
    return result


def _sparse_forward(weights, biases, shifts, descriptors, enc3):
    """Descriptor-order software model of the Stage29 scheduler."""
    npoints = enc3[0].shape[1]
    main = None
    attn = sig = None
    output_by_layer = {}
    for descriptor in descriptors:
        layer = descriptor["layer"]
        group = descriptor["group"]
        keep = np.asarray(descriptor["indices"], dtype=np.int64)
        if layer == 0:
            source = enc3[0]
        elif layer == 5:
            source = np.vstack((main, enc3[0]))
        elif layer == 10:
            source = np.vstack((main, enc3[1], enc3[2]))
        else:
            source = main
        rows = weights[layer][group * TARGET_P:(group + 1) * TARGET_P]
        acc = rows[:, keep].astype(np.int64) @ source[keep].astype(np.int64)
        bias = biases[layer][group * TARGET_P:(group + 1) * TARGET_P]
        y = np.clip(np.right_shift(acc, shifts[layer]) + bias[:, None], -128, 127)
        if TOPO[layer][2]:
            y = np.maximum(y, 0)
        y = y.astype(np.int8)
        output_by_layer.setdefault(layer, []).append(y)
        if descriptor["last_group"]:
            combined = np.vstack(output_by_layer.pop(layer))[:TOPO[layer][1]]
            if layer == 8:
                attn = combined
            elif layer == 12:
                sig = combined
            else:
                main = combined
    return np.vstack((attn, sig)).T.copy()


def build(tv_path: Path, rtl_dir: Path, report_path: Path):
    parsed = parse_tv3(str(tv_path), SOURCE_P)
    weights, biases, shifts = rebuild_layers(parsed)
    enc_q = _decode_tv3_enc(parsed)
    dense_expected = forward_fixed_batch(
        weights, shifts, biases, [enc_q[:, kind, :].T for kind in range(3)]
    )
    file_expected = np.asarray(parsed[5], dtype=np.int8)
    if not np.array_equal(dense_expected, file_expected):
        raise AssertionError("dense fixed-point model does not reproduce TV3 EXP")

    descriptors = []
    weight_words = []
    index_values = []
    bias_words = []
    reconstructed = [np.zeros_like(value) for value in weights]
    weight_base = index_base = 0
    nonzero = total = 0

    for layer, ((_, outputs, _), dense_w, dense_b) in enumerate(
        zip(TOPO, weights, biases)
    ):
        groups = (outputs + TARGET_P - 1) // TARGET_P
        for group in range(groups):
            lo, hi = group * TARGET_P, min((group + 1) * TARGET_P, outputs)
            rows = dense_w[lo:hi]
            actual_keep = np.flatnonzero(np.any(rows != 0, axis=0))
            keep = actual_keep if len(actual_keep) else np.asarray([0], dtype=np.int64)
            desc_words = []
            for original_column in keep:
                word = np.zeros(TARGET_P, dtype=np.int8)
                word[:hi - lo] = rows[:, original_column]
                desc_words.append(word)
                reconstructed[layer][lo:hi, original_column] = rows[:, original_column]
            bias_word = np.zeros(TARGET_P, dtype=np.int16)
            bias_word[:hi - lo] = dense_b[lo:hi]
            descriptor = {
                "descriptor": len(descriptors),
                "layer": layer,
                "group": group,
                "outputs_valid": hi - lo,
                "compact_k": len(keep),
                "weight_base": weight_base,
                "index_base": index_base,
                "bias_address": len(bias_words),
                "last_group": group == groups - 1,
                "indices": [int(value) for value in keep],
                "all_zero_group": len(actual_keep) == 0,
            }
            descriptors.append(descriptor)
            weight_words.extend(desc_words)
            index_values.extend(int(value) for value in keep)
            bias_words.append(bias_word)
            weight_base += len(keep)
            index_base += len(keep)
            nonzero += int(np.count_nonzero(rows))
            total += int(rows.size)

    if any(not np.array_equal(a, b) for a, b in zip(weights, reconstructed)):
        raise AssertionError("sparse model cannot reconstruct the supplied INT8 weights")
    sparse_expected = _sparse_forward(
        weights, biases, shifts, descriptors,
        [enc_q[:, kind, :].T for kind in range(3)],
    )
    if not np.array_equal(sparse_expected, dense_expected):
        raise AssertionError("sparse descriptor model changed a TV3 output bit")

    rtl_dir.mkdir(parents=True, exist_ok=True)
    weights_path = rtl_dir / "sparse_weights_p128.memh"
    indices_path = rtl_dir / "sparse_indices.memh"
    biases_path = rtl_dir / "sparse_bias_p128.memh"
    params_path = rtl_dir / "stage29_model_params.vh"
    tv_paths = [rtl_dir / ("tv3_%s.memh" % name) for name in ("pts", "view", "tx")]
    tv_expected_path = rtl_dir / "tv3_expected.memh"

    weights_path.write_text(
        "\n".join(_memh_line_unsigned_le(word.tobytes()) for word in weight_words) + "\n",
        encoding="ascii",
    )
    indices_path.write_text(
        "\n".join(f"{value:03x}" for value in index_values) + "\n", encoding="ascii"
    )
    biases_path.write_text(
        "\n".join(_memh_line_unsigned_le(word.tobytes()) for word in bias_words) + "\n",
        encoding="ascii",
    )

    fields = {
        "d_layer": (4, "layer"),
        "d_group": (2, "group"),
        "d_k": (9, "compact_k"),
        "d_wbase": (11, "weight_base"),
        "d_ibase": (11, "index_base"),
        "d_baddr": (5, "bias_address"),
        "d_nvalid": (8, "outputs_valid"),
        "d_last_group": (1, "last_group"),
    }
    lines = [
        "// Generated by software/stage29/build_sparse_model.py; do not hand edit.",
        f"localparam integer STAGE29_NUM_DESC = {len(descriptors)};",
        f"localparam integer STAGE29_WEIGHT_WORDS = {len(weight_words)};",
        f"localparam integer STAGE29_INDEX_WORDS = {len(index_values)};",
        f"localparam integer STAGE29_BIAS_WORDS = {len(bias_words)};",
    ]
    for function_name, (width, key) in fields.items():
        lines += [
            f"function [{width - 1}:0] {function_name};",
            "  input [4:0] d;",
            "  begin case (d)",
        ]
        for descriptor in descriptors:
            value = int(descriptor[key])
            lines.append(f"    5'd{descriptor['descriptor']}: {function_name} = {width}'d{value};")
        lines += [
            f"    default: {function_name} = {width}'d0;",
            "  endcase end",
            "endfunction",
        ]
    lines += [
        "function [4:0] layer_shift;",
        "  input [3:0] l;",
        "  begin case (l)",
    ]
    for layer, shift in enumerate(shifts):
        lines.append(f"    4'd{layer}: layer_shift = 5'd{shift};")
    lines += ["    default: layer_shift = 5'd0;", "  endcase end", "endfunction"]
    params_path.write_text("\n".join(lines) + "\n", encoding="ascii")
    for kind, path in enumerate(tv_paths):
        records = []
        for point in range(enc_q.shape[0]):
            raw = enc_q[point, kind].tobytes() + bytes(1)
            records.append(_memh_line_unsigned_le(raw))
        path.write_text("\n".join(records) + "\n", encoding="ascii")
    tv_expected_path.write_text(
        "\n".join(_memh_line_unsigned_le(row.tobytes()) for row in dense_expected) + "\n",
        encoding="ascii",
    )

    cycles_per_sample = len(weight_words) + len(descriptors) * 6 + 2
    report = {
        "schema": MODEL_SCHEMA,
        "source_tv": str(tv_path.resolve()),
        "source_tv_sha256": _sha256(tv_path),
        "source_parallelism": SOURCE_P,
        "target_parallelism": TARGET_P,
        "layers": len(TOPO),
        "descriptors": descriptors,
        "weight_words": len(weight_words),
        "index_words": len(index_values),
        "bias_words": len(bias_words),
        "dense_weight_values": total,
        "nonzero_weight_values": nonzero,
        "zero_fraction": 1.0 - nonzero / total,
        "dense_reconstruction_bit_exact": True,
        "tv3_sparse_vs_dense_bit_exact": True,
        "tv3_expected_values": dense_expected.astype(int).tolist(),
        "conservative_cycles_per_sample": cycles_per_sample,
        "estimated_32400x64_core_seconds_at_75mhz": cycles_per_sample * 32400 * 64 / 75e6,
        "files": {},
    }
    for path in (weights_path, indices_path, biases_path, params_path, *tv_paths,
                 tv_expected_path):
        report["files"][path.name] = {"bytes": path.stat().st_size, "sha256": _sha256(path)}
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({
        "report": str(report_path),
        "zero_fraction": report["zero_fraction"],
        "weight_words": report["weight_words"],
        "conservative_core_seconds_75mhz": report["estimated_32400x64_core_seconds_at_75mhz"],
        "bit_exact": True,
    }, indent=2))
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--tv", type=Path,
        default=ROOT / "test_vectors" / "stage24_ray" / "tv3_ray_net.txt",
    )
    parser.add_argument("--rtl-dir", type=Path, default=ROOT / "rtl" / "stage29" / "model")
    parser.add_argument(
        "--report", type=Path,
        default=ROOT / "sim" / "stage29_sparse" / "model_manifest.json",
    )
    args = parser.parse_args()
    build(args.tv, args.rtl_dir, args.report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
