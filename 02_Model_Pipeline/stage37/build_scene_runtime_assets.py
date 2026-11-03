#!/usr/bin/env python3
"""Build compact per-scene assets consumed by the one Stage37 bitstream."""
from __future__ import annotations
import argparse, hashlib, importlib.util, json, sys
from pathlib import Path
import numpy as np


def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def dump(path,value): path.write_text(json.dumps(value,indent=2),encoding='utf-8')


def pack_constants(view_q, out):
    view=np.asarray(view_q,np.int8).reshape(90,360,63)
    entries=[]; out.mkdir(parents=True,exist_ok=True)
    zero=np.zeros(64,np.int8).view('<u8')
    for packet in range(18):
        first=packet*5; block=view[first:first+5].reshape(-1,63)
        pad=np.zeros((len(block),64),np.int8);pad[:,:63]=block
        words=np.ascontiguousarray(pad).view('<u8').reshape(len(block),8)
        records=np.empty((len(block)//2,32),np.uint64)
        records[:,:8]=words[0::2];records[:,8:16]=zero
        records[:,16:24]=words[1::2];records[:,24:32]=zero
        name=f'constants_{first:03d}_{first+4:03d}.npz';path=out/name
        np.savez_compressed(path,value=records)
        entries.append({'packet_index':packet,'first_row':first,'row_count':5,
          'ray_count':1800,'pair_count':900,'file':name,'file_bytes':path.stat().st_size,
          'sha256':sha(path)})
    manifest={'schema':'n2-stage37-signal-constant-packets-v1','rows':90,'cols':360,
      'rows_per_packet':5,'packet_count':18,'packets':entries}
    dump(out/'manifest.json',manifest);return manifest


def positional(x):
    values=[x]
    for f in 2.**np.arange(10,dtype=np.float32):values.extend((np.sin(x*f),np.cos(x*f)))
    return np.concatenate(values,-1)


def qencode(x,scale):return np.clip(np.rint(positional(x)/scale),-128,127).astype(np.int8)


def build_sentinel(points, view, scales, att_i8, out, count=512):
    indices=np.unique(np.rint(np.linspace(0,32399,count)).astype(np.int64))
    if len(indices)%2:indices=indices[:-1]
    pts_q=qencode(points[indices].reshape(-1,3),np.asarray(scales[0])).reshape(len(indices),64,63)
    view_q=qencode(view[indices],np.asarray(scales[1]))
    tx_q=np.zeros((len(indices),63),np.int8)
    pad=np.zeros((len(indices),64,3,64),np.int8)
    pad[:,:,:,:63]=np.stack((pts_q,np.repeat(view_q[:,None],64,1),np.repeat(tx_q[:,None],64,1)),2)
    words=np.ascontiguousarray(pad).view('<u8').reshape(len(indices),64,3,8)
    rec=np.empty((len(indices)//2,1056),np.uint64)
    for pair in range(len(indices)//2):
        a,b=2*pair,2*pair+1
        rec[pair,:32]=np.concatenate((words[a,0,1],words[a,0,2],words[b,0,1],words[b,0,2]))
        rec[pair,32:]=np.stack((words[a,:,0],words[b,:,0]),1).reshape(-1)
    stream=np.empty(1+rec.size,np.uint64);stream[0]=np.uint64((6<<60)|len(indices));stream[1:]=rec.reshape(-1)
    expected=np.ascontiguousarray(att_i8[indices]).view('<u2').reshape(len(indices),64)
    np.savez_compressed(out,ray_indices=indices,stream_u64=stream,expected_u16=expected)
    return {'rays':len(indices),'values':int(expected.size),'sha256':sha(out)}


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--evaluator',type=Path,required=True)
    ap.add_argument('--source-root',type=Path,required=True);ap.add_argument('--scene',required=True)
    ap.add_argument('--tv',type=Path,required=True);ap.add_argument('--manifest',type=Path,required=True)
    ap.add_argument('--datadir',type=Path,required=True);ap.add_argument('--runtime-model',type=Path,required=True)
    ap.add_argument('--out',type=Path,required=True);a=ap.parse_args()
    spec=importlib.util.spec_from_file_location('stage37_fixed',a.evaluator);m=importlib.util.module_from_spec(spec)
    assert spec.loader is not None;spec.loader.exec_module(m)
    model=m.Fixed(a.source_root,a.tv,a.manifest,a.datadir)
    att=model.att.cpu().numpy().astype(np.int8);view_q=model.view.cpu().numpy().astype(np.int8)
    direction,points=m.geometry(a.datadir);sc=json.loads(a.manifest.read_text())['input_scales']
    a.out.mkdir(parents=True,exist_ok=True)
    cache=np.ascontiguousarray(att).view('<u2').reshape(90,360,64)
    np.save(a.out/'attenuation_u16.npy',cache,allow_pickle=False)
    constants=pack_constants(view_q,a.out/'constant_packets')
    sentinel=build_sentinel(points.reshape(32400,64,3),direction,sc,att,a.out/'cache_sentinel_512.npz')
    report={'schema':'n2-stage37-scene-runtime-assets-v1','scene':a.scene,
      'runtime_model_sha256':sha(a.runtime_model),'quant_manifest_sha256':sha(a.manifest),
      'cache_sha256':sha(a.out/'attenuation_u16.npy'),'cache_shape':[90,360,64],
      'cache_is_tx_independent':True,'cache_contains_final_heatmap':False,
      'constant_manifest_sha256':sha(a.out/'constant_packets'/'manifest.json'),
      'sentinel':sentinel,'same_bitstream_required':True}
    dump(a.out/'runtime_assets.json',report);print(json.dumps(report,indent=2))
    del model

if __name__=='__main__':main()
