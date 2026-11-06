#!/usr/bin/env python3
"""Package a sparse INT8 export for the runtime-loadable Stage37 bitstream."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import zlib
from pathlib import Path

import numpy as np


TARGET = {"weight": 0, "index": 1, "bias": 2, "descriptor": 3, "shift": 4,
          "att_factor": 5, "att_phase_cos": 6, "att_phase_sin": 7,
          "sig_amp": 8, "sig_phase_cos": 9, "sig_phase_sin": 10,
          "path_loss": 11}


def sha256(path: Path):
    h=hashlib.sha256(); h.update(path.read_bytes()); return h.hexdigest()


def read_memh(path: Path, nbytes: int):
    rows=[]
    for line in path.read_text(encoding="ascii").splitlines():
        line=line.strip()
        if line: rows.append(np.frombuffer(bytes.fromhex(line)[::-1], dtype=np.uint8).copy())
    result=np.stack(rows)
    if result.shape[1] != nbytes: raise ValueError(f"{path}: width {result.shape[1]} != {nbytes}")
    return result


def load_make_luts(source: Path):
    spec=importlib.util.spec_from_file_location("n2_stage37_fixed_renderer", source)
    module=importlib.util.module_from_spec(spec); assert spec.loader is not None
    spec.loader.exec_module(module); return module.make_luts


def pack_descriptor(d):
    value=(int(d["layer"]) | (int(d["group"])<<4) |
           (int(d["compact_k"])<<6) | (int(d["weight_base"])<<15) |
           (int(d["index_base"])<<27) | (int(d["bias_address"])<<39) |
           (int(d["outputs_valid"])<<44) | (int(bool(d["last_group"]))<<52))
    return np.uint64(value)


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--sparse-manifest",type=Path,required=True)
    ap.add_argument("--sparse-dir",type=Path,required=True)
    ap.add_argument("--quant-manifest",type=Path,required=True)
    ap.add_argument("--fixed-renderer",type=Path,required=True)
    ap.add_argument("--scene",required=True)
    ap.add_argument("--checkpoint",type=Path,required=True)
    ap.add_argument("--out",type=Path,required=True)
    args=ap.parse_args()
    sparse=json.loads(args.sparse_manifest.read_text(encoding="utf-8"))
    quant=json.loads(args.quant_manifest.read_text(encoding="utf-8"))
    descriptors=sparse["descriptors"]
    if len(descriptors)!=23: raise ValueError("Stage37 requires the common 23 descriptors")
    if int(sparse["weight_words"])>3072: raise ValueError("sparse model exceeds 3072 runtime weight words")
    if any(i<256 for d in descriptors if d["layer"]==5 for i in d["indices"]):
        raise ValueError("attenuation layer 5 still depends on the discarded feature branch")
    if any(i<256 for d in descriptors if d["layer"]==10 for i in d["indices"]):
        raise ValueError("signal layer 10 still depends on per-sample spatial features")
    if descriptors[10]["layer"]!=5 or descriptors[19]["layer"]!=10:
        raise ValueError("common Stage37 descriptor start addresses changed")

    weights=read_memh(args.sparse_dir/"sparse_weights_p128.memh",128)
    indices=np.asarray([int(x,16) for x in
        (args.sparse_dir/"sparse_indices.memh").read_text(encoding="ascii").split()],np.uint16)
    bias_bytes=read_memh(args.sparse_dir/"sparse_bias_p128.memh",256)
    biases=np.ascontiguousarray(bias_bytes).view("<i2").reshape(len(bias_bytes),128)
    desc=np.asarray([pack_descriptor(d) for d in descriptors],np.uint64)
    shifts=np.asarray(quant["shifts"],np.uint8)
    make_luts=load_make_luts(args.fixed_renderer)
    luts=make_luts(np.asarray(quant["final_scales"],np.float64))
    payload=[weights.tobytes(),indices.tobytes(),biases.tobytes(),desc.tobytes(),shifts.tobytes()]
    for key in ("att_factor","att_phase_cos","att_phase_sin","sig_amp",
                "sig_phase_cos","sig_phase_sin","path_loss"):
        payload.append(np.asarray(luts[key]).astype("<i2",copy=False).tobytes())
    model_id=zlib.crc32(b"".join(payload)) & 0xffffffff
    args.out.parent.mkdir(parents=True,exist_ok=True)
    np.savez_compressed(args.out,weights_u8=weights,indices_u16=indices,biases_i16=biases,
        descriptors_u64=desc,shifts_u8=shifts,
        att_factor_u16=np.asarray(luts["att_factor"],np.uint16),
        att_phase_cos_i16=np.asarray(luts["att_phase_cos"],np.int16),
        att_phase_sin_i16=np.asarray(luts["att_phase_sin"],np.int16),
        sig_amp_u16=np.asarray(luts["sig_amp"],np.uint16),
        sig_phase_cos_i16=np.asarray(luts["sig_phase_cos"],np.int16),
        sig_phase_sin_i16=np.asarray(luts["sig_phase_sin"],np.int16),
        path_loss_u16=np.asarray(luts["path_loss"],np.uint16),
        model_id=np.asarray(model_id,np.uint32),scene=np.asarray(args.scene))
    report={"schema":"n2-stage37-runtime-model-v1","scene":args.scene,
      "model_id_crc32":"%08x"%model_id,"checkpoint_sha256":sha256(args.checkpoint),
      "weight_words":int(len(weights)),"weight_capacity":3072,
      "factorized_graph_proved":True,"signal_feature_columns_zero":True,
      "attenuation_layer5_feature_columns_zero":True,"final_scales":quant["final_scales"],
      "runtime_model_sha256":sha256(args.out)}
    args.out.with_suffix(".json").write_text(json.dumps(report,indent=2),encoding="utf-8")
    print(json.dumps(report,indent=2))


if __name__=="__main__": main()
