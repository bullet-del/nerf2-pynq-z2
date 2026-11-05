#!/usr/bin/env python3
"""Fit a scene scalar output calibration from training maps only and test it."""
from __future__ import annotations
import argparse,importlib.util,json
from pathlib import Path
import imageio.v2 as imageio
import numpy as np,pandas as pd
from skimage.metrics import structural_similarity

def load_fixed(path):
 spec=importlib.util.spec_from_file_location('stage37_fixed_eval',path);m=importlib.util.module_from_spec(spec)
 assert spec.loader is not None;spec.loader.exec_module(m);return m.Fixed

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--evaluator',type=Path,required=True);ap.add_argument('--root',type=Path,required=True);ap.add_argument('--tv',type=Path,required=True);ap.add_argument('--manifest',type=Path,required=True);ap.add_argument('--datadir',type=Path,required=True);ap.add_argument('--fit-maps',type=int,default=50);ap.add_argument('--ssim-grid',action='store_true');ap.add_argument('--out',type=Path,required=True);a=ap.parse_args()
 model=load_fixed(a.evaluator)(a.root,a.tv,a.manifest,a.datadir);pos=pd.read_csv(a.datadir/'tx_pos.csv').values.astype(np.float32)
 train=np.atleast_1d(np.loadtxt(a.datadir/'train_index.txt',dtype=str));select=train[np.linspace(0,len(train)-1,min(a.fit_maps,len(train)),dtype=int)]
 sx=sy=sxx=sxy=0.;n=0;fit=[]
 for i in select:
  x=model.render(pos[int(i)-1]).ravel();y=imageio.imread(a.datadir/'spectrum'/f'{i}.png').astype(np.float64).ravel()/255
  sx+=x.sum();sy+=y.sum();sxx+=x@x;sxy+=x@y;n+=len(x);fit.append((x.reshape(90,360),y.reshape(90,360)))
 gain=(sxy-sx*sy/n)/(sxx-sx*sx/n+1e-30);bias=(sy-gain*sx)/n
 if a.ssim_grid:
  best=(-1.,gain,bias)
  for g in np.linspace(gain-.02,gain+.02,9):
   for b in np.linspace(bias-.01,bias+.01,21):
    score=np.mean([structural_similarity(y,np.clip(g*x+b,0,1),data_range=1) for x,y in fit])
    if score>best[0]:best=(score,float(g),float(b))
  _,gain,bias=best
 test=np.atleast_1d(np.loadtxt(a.datadir/'test_index.txt',dtype=str));raw=[];cal=[]
 for j,i in enumerate(test,1):
  x=model.render(pos[int(i)-1]);y=imageio.imread(a.datadir/'spectrum'/f'{i}.png').astype(np.float64)/255
  raw.append(structural_similarity(y,x,data_range=1));cal.append(structural_similarity(y,np.clip(gain*x+bias,0,1),data_range=1))
  if j%50==0 or j==len(test):print(f'[{j}/{len(test)}] raw={np.mean(raw):.6f} calibrated={np.mean(cal):.6f}',flush=True)
 report={'fit_split':'train','fit_maps':len(select),'fit_objective':'ssim_grid' if a.ssim_grid else 'mse','gain':float(gain),'bias':float(bias),'test_maps':len(test),'raw_mean_ssim':float(np.mean(raw)),'calibrated_mean_ssim':float(np.mean(cal))}
 a.out.parent.mkdir(parents=True,exist_ok=True);a.out.write_text(json.dumps(report,indent=2),encoding='utf-8');print(json.dumps(report,indent=2))
if __name__=='__main__':main()
