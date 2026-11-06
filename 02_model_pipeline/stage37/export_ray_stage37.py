#!/usr/bin/env python3
"""Export the S23 network for ray marching with per-encoded-feature scales.

The 13-layer graph and tv3 stream format stay compatible with the existing
P32/NB8 RTL. Different input-feature scales are absorbed into weight columns.
"""
from __future__ import annotations
import argparse, hashlib, json, math, os, sys
from pathlib import Path
import numpy as np
import torch

HERE=Path(__file__).absolute(); ROOT=Path(os.environ.get('N2_SOURCE_ROOT',HERE.parents[1]))
sys.path.insert(0,str(ROOT/'software'/'stage11'))
from reference import load_model

NAMES=['attenuation_linears.'+str(i) for i in range(8)]+[
 'attenuation_output','feature_layer','signal_linears.0','signal_linears.1','signal_output']
RELU=[True]*8+[False,False,True,True,False]

def p2ceil(x): return 2.**math.ceil(math.log2(max(float(x),2.**-30)))
def feature_scales(enc):
    mx=np.max(np.abs(enc),axis=0)
    return np.array([p2ceil(v/110.0) for v in mx],np.float64)

def export(model,xyz,out,percentile=100.0):
    out.mkdir(parents=True,exist_ok=True); t=torch.tensor(xyz,dtype=torch.float32)
    fs=[model.embed_pts_fn,model.embed_view_fn,model.embed_tx_fn]
    enc=[f(t[:,3*i:3*i+3]).detach().cpu().numpy().astype(np.float64) for i,f in enumerate(fs)]
    se=[feature_scales(e) for e in enc]
    eq=[np.clip(np.rint(e/s[None]),-128,127).astype(np.int8) for e,s in zip(enc,se)]
    main_q=main_f=main_s=None; wq=[];bq=[];shifts=[];scales=[];sat=[]
    result=np.zeros((len(xyz),4),np.int8)
    for l,name in enumerate(NAMES):
        layer=model.get_submodule(name); w=layer.weight.detach().cpu().numpy().astype(np.float64)
        b=layer.bias.detach().cpu().numpy().astype(np.float64)
        if l==0: xq,xf,sx=eq[0],enc[0],se[0]
        elif l==5:
            w=np.concatenate((w[:,63:],w[:,:63]),1)
            xq=np.concatenate((main_q,eq[0]),1); xf=np.concatenate((main_f,enc[0]),1)
            sx=np.r_[np.full(256,main_s),se[0]]
        elif l==10:
            xq=np.concatenate((main_q,eq[1],eq[2]),1); xf=np.concatenate((main_f,enc[1],enc[2]),1)
            sx=np.r_[np.full(256,main_s),se[1],se[2]]
        else: xq,xf,sx=main_q,main_f,np.full(w.shape[1],main_s)
        yf=xf@w.T+b
        if RELU[l]: yf=np.maximum(yf,0)
        # A single extreme calibration value can otherwise waste most of the
        # INT8 output range (Office2 signal output is the concrete case).
        # Percentile clipping is recorded and validated on the full test set.
        sy=p2ceil(np.percentile(np.abs(yf),percentile)/100)
        coeff=w*sx[None]/sy; amax=np.max(np.abs(coeff))
        shift=min(30,int(math.floor(math.log2(127/amax)))) if amax else 0
        if shift<0: sy*=2.**(-shift); coeff=w*sx[None]/sy; shift=0
        ww=np.clip(np.rint(coeff*2.**shift),-127,127).astype(np.int8)
        bb=np.rint(b/sy).astype(np.int64)
        if np.max(np.abs(bb))>32767: raise ValueError(f'L{l} bias overflow')
        bb=bb.astype(np.int16)
        acc=xq.astype(np.int64)@ww.T.astype(np.int64)
        y=(acc>>shift)+bb; sat.append(float(np.mean((y<-128)|(y>127))))
        y=np.clip(y,-128,127).astype(np.int8)
        if RELU[l]: y=np.maximum(y,0)
        if l in (8,12): result[:,0 if l==8 else 2:2 if l==8 else 4]=y
        else: main_q,main_f,main_s=y,yf,sy
        wq.append(ww);bq.append(bb);shifts.append(shift);scales.append(sy)
    p=32; count=min(3,len(xyz)); lines=[f'{p} 13 {count}']
    for l,w in enumerate(wq):
        n,k=w.shape;ng=(n+p-1)//p;lines.append(f'1 {l} {k*ng} {ng}')
    for sample in range(count):
        for kind in range(3):
            v=np.pad(eq[kind][sample],(0,1))
            for z in range(2): lines.append(f'2 {kind} '+' '.join(map(str,v[z*32:(z+1)*32])))
    for l,w in enumerate(wq):
        n,k=w.shape;wp=np.pad(w,((0,(-n)%32),(0,0)))
        for col in range(k):
            for g in range(len(wp)//32): lines.append(f'3 {l} '+' '.join(map(str,wp[g*32:(g+1)*32,col])))
    for l,b in enumerate(bq):
        bp=np.pad(b,(0,(-len(b))%32))
        for g in range(len(bp)//32):lines.append(f'4 {l} '+' '.join(map(str,bp[g*32:(g+1)*32])))
    for l,s in enumerate(shifts):lines.append(f'5 {l} {s}')
    for i in range(count):lines.append(f'6 {i} '+' '.join(map(str,result[i])))
    (out/'tv3_ray_net.txt').write_text('\n'.join(lines)+'\n',encoding='ascii')
    with torch.no_grad(): ref=model(t[:,:3],t[:,3:6],t[:,6:]).cpu().numpy()
    final=np.array([scales[8]]*2+[scales[12]]*2)
    report={'schema':'n2-ray-export-v1','input_scales':[s.tolist() for s in se],
      'input_scale_summary':[{'min':float(s.min()),'max':float(s.max())} for s in se],
      'layer_output_scales':scales,'final_scales':final.tolist(),'shifts':shifts,
      'calibration_saturation':sat,'calibration_mae':np.mean(np.abs(result*final-ref),0).tolist(),
      'stage37_activation_percentile':float(percentile),
      'legacy_skip_columns_permuted':True,'tv3_compatible_with_existing_mlp_rtl':True}
    (out/'manifest_ray.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    np.savez_compressed(out/'calibration_reference.npz',xyz=xyz,enc_q=np.stack(eq,1),fixed=result,fp32=ref)
    return report

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--ckpt',type=Path,required=True);ap.add_argument('--config',type=Path,required=True)
    ap.add_argument('--calibration',type=Path,required=True);ap.add_argument('--out',type=Path,default=Path('test_vectors/stage24_ray'))
    ap.add_argument('--percentile',type=float,default=100.0)
    a=ap.parse_args(); model,_=load_model(str(a.ckpt),str(a.config)); xyz=np.load(a.calibration,allow_pickle=False)
    if not 90.0 <= a.percentile <= 100.0: raise ValueError('percentile must be in [90,100]')
    r=export(model,xyz,a.out,a.percentile);r['checkpoint_sha256']=hashlib.sha256(a.ckpt.read_bytes()).hexdigest()
    (a.out/'manifest_ray.json').write_text(json.dumps(r,indent=2),encoding='utf-8');print(json.dumps(r,indent=2))
if __name__=='__main__':main()
