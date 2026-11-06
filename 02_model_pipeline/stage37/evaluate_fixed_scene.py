#!/usr/bin/env python3
"""Fast GPU reference for a Stage37 INT8/Q15 scene model over its full test set."""
from __future__ import annotations
import argparse,json,sys
from pathlib import Path
import imageio.v2 as imageio
import numpy as np
import pandas as pd
import torch,yaml
from scipy.spatial.transform import Rotation
from skimage.metrics import structural_similarity

def positional(x):
    values=[x]
    for f in 2.**np.arange(10,dtype=np.float32): values.extend((np.sin(x*f),np.cos(x*f)))
    return np.concatenate(values,-1)

def qencode(x,scale): return np.clip(np.rint(positional(x)/scale),-128,127).astype(np.int8)

def geometry(datadir):
    info=yaml.safe_load((datadir/'gateway_info.yml').read_text(encoding='utf-8'))
    pos=np.asarray(info['gateway1']['position'],np.float32);quat=info['gateway1']['orientation']
    az=np.tile(np.linspace(1,360,360,dtype=np.float32)*np.pi/180,90)
    el=np.repeat(np.linspace(1,90,90,dtype=np.float32)*np.pi/180,360)
    direction=np.stack((np.cos(el)*np.cos(az),np.cos(el)*np.sin(az),np.sin(el)),-1)
    direction=direction@Rotation.from_quat(quat).as_matrix().T.astype(np.float32)
    t=np.linspace(0,5,64,dtype=np.float32)
    points=pos[None,None]+direction[:,None]*t[None,:,None]
    return direction.astype(np.float32),points.astype(np.float32)

class Fixed:
 def __init__(self,root,tv,manifest,datadir,device='cuda'):
  sys.path[:0]=[str(root/'software/stage5'),str(root/'software/stage8'),str(root/'software/stage24')]
  from n2_stream import parse_tv3,P
  from gen_field_vectors import rebuild_layers
  from fixed_renderer import make_luts
  parsed=parse_tv3(str(tv),P);w,b,s=rebuild_layers(parsed)
  self.w=[torch.tensor(x.T.astype(np.float32),device=device) for x in w]
  self.b=[torch.tensor(x.astype(np.float32),device=device) for x in b];self.shifts=s
  self.man=json.loads(manifest.read_text(encoding='utf-8'));self.device=device
  if np.count_nonzero(w[5][:,:256]) or np.count_nonzero(w[10][:,:256]):
   raise ValueError('not a Stage37 factorized model')
  direction,points=geometry(datadir);sc=self.man['input_scales']
  at=[]
  with torch.inference_mode():
   for row in range(90):
    q=qencode(points[row*360:(row+1)*360].reshape(-1,3),np.asarray(sc[0]))
    x=torch.tensor(q.astype(np.float32),device=device)
    x=self.layer(5,x,True,start=256);x=self.layer(6,x,True);x=self.layer(7,x,True)
    x=self.layer(8,x,False)[:,:2].to(torch.int64).reshape(360,64,2);at.append(x)
  self.att=torch.cat(at,0);self.view=torch.tensor(qencode(direction,np.asarray(sc[1])).astype(np.float32),device=device)
  lut=make_luts(self.man['final_scales']);self.lut={k:torch.tensor(v,dtype=torch.int64,device=device) for k,v in lut.items() if k in ('att_factor','att_phase_cos','att_phase_sin','sig_amp','sig_phase_cos','sig_phase_sin','path_loss')}
  self.pr=int(lut['phase_init_cos']);self.pi=int(lut['phase_init_sin']);self.txscale=np.asarray(sc[2])
 def layer(self,i,x,relu,start=0):
  y=torch.floor((x@self.w[i][start:])*2.**(-self.shifts[i]))+self.b[i]
  y=torch.clamp(y,-128,127);return torch.clamp_min(y,0) if relu else y
 @torch.inference_mode()
 def render(self,tx):
  tq=qencode(np.asarray(tx,np.float32)[None],self.txscale).astype(np.float32)
  t=torch.tensor(tq,device=self.device).expand(len(self.view),-1)
  sig=self.layer(10,torch.cat((self.view,t),1),True,start=256)
  sig=self.layer(12,self.layer(11,sig,True),False)[:,:2].to(torch.int64)
  n=len(sig);d=self.device;factor=torch.full((n,),32768,dtype=torch.int64,device=d)
  pr=torch.full((n,),self.pr,dtype=torch.int64,device=d);pi=torch.full((n,),self.pi,dtype=torch.int64,device=d)
  re=torch.zeros(n,dtype=torch.int64,device=d);im=re.clone();lut=self.lut
  sc=lut['sig_phase_cos'][sig[:,1]+128];ss=lut['sig_phase_sin'][sig[:,1]+128];sa=lut['sig_amp'][sig[:,0]+128]
  for k in range(64):
   dr=((sc*pr)>>15)-((ss*pi)>>15);di=((sc*pi)>>15)+((ss*pr)>>15)
   amp=((sa*factor)>>15);amp=(amp*lut['path_loss'][k])>>15
   re+=(amp*dr)>>15;im+=(amp*di)>>15
   a=self.att[:,k,:]+128;factor=(factor*lut['att_factor'][a[:,0]])>>15
   c=lut['att_phase_cos'][a[:,1]];s=lut['att_phase_sin'][a[:,1]]
   pr,pi=((pr*c)>>15)-((pi*s)>>15),((pr*s)>>15)+((pi*c)>>15)
  square=re*re+im*im;root=torch.floor(torch.sqrt(square.to(torch.float64))).to(torch.int64)
  root=torch.where(root*root>square,root-1,root);root=torch.where((root+1)*(root+1)<=square,root+1,root)
  return root.cpu().numpy().reshape(90,360).astype(np.float64)/32768.

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--root',type=Path,required=True);ap.add_argument('--tv',type=Path,required=True);ap.add_argument('--manifest',type=Path,required=True);ap.add_argument('--datadir',type=Path,required=True);ap.add_argument('--out',type=Path,required=True);ap.add_argument('--limit',type=int,default=0);a=ap.parse_args()
 model=Fixed(a.root,a.tv,a.manifest,a.datadir);idx=np.atleast_1d(np.loadtxt(a.datadir/'test_index.txt',dtype=str));idx=idx[:a.limit or None]
 pos=pd.read_csv(a.datadir/'tx_pos.csv').values.astype(np.float32);rows=[]
 for j,i in enumerate(idx,1):
  pred=model.render(pos[int(i)-1]);gt=imageio.imread(a.datadir/'spectrum'/f'{i}.png').astype(np.float64)/255
  rows.append(float(structural_similarity(gt,pred,data_range=1.0)))
  if j%20==0 or j==len(idx): print(f'[{j}/{len(idx)}] mean={np.mean(rows):.6f}',flush=True)
 report={'maps':len(rows),'mean_ssim':float(np.mean(rows)),'median_ssim':float(np.median(rows)),'ssim':rows}
 a.out.parent.mkdir(parents=True,exist_ok=True);a.out.write_text(json.dumps(report,indent=2),encoding='utf-8');print(json.dumps({k:v for k,v in report.items() if k!='ssim'},indent=2))
if __name__=='__main__':main()
