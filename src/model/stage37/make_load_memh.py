#!/usr/bin/env python3
"""Make a simulation stream for the Stage37 runtime model-loader protocol."""
from __future__ import annotations
import argparse
from pathlib import Path
import numpy as np

OP_LOAD=8
def address(target,address,segment=0): return (target<<60)|(address<<48)|(segment<<42)

def main():
 ap=argparse.ArgumentParser();ap.add_argument("model",type=Path);ap.add_argument("out",type=Path)
 a=ap.parse_args();records=[]
 with np.load(a.model,allow_pickle=False) as z:
  w=np.ascontiguousarray(z['weights_u8']).view('<u8').reshape(-1,16)
  for ai,row in enumerate(w):
   for si,v in enumerate(row): records += [address(0,ai,si),int(v)]
  for ai,v in enumerate(z['indices_u16']): records += [address(1,ai),int(v)]
  b=np.ascontiguousarray(z['biases_i16'],dtype='<i2').view('<u8').reshape(-1,32)
  for ai,row in enumerate(b):
   for si,v in enumerate(row): records += [address(2,ai,si),int(v)]
  for ai,v in enumerate(z['descriptors_u64']): records += [address(3,ai),int(v)]
  for ai,v in enumerate(z['shifts_u8']): records += [address(4,ai),int(v)]
  for target,key in ((5,'att_factor_u16'),(6,'att_phase_cos_i16'),(7,'att_phase_sin_i16'),
                     (8,'sig_amp_u16'),(9,'sig_phase_cos_i16'),(10,'sig_phase_sin_i16'),
                     (11,'path_loss_u16')):
   for ai,v in enumerate(np.ascontiguousarray(z[key]).view(np.uint16)):
    records += [address(target,ai),int(v)]
  model_id=int(z['model_id'])
 packet=[(OP_LOAD<<60)|((len(records)//2)<<32)|model_id]+records
 a.out.parent.mkdir(parents=True,exist_ok=True)
 a.out.write_text('\n'.join(f'{v:016x}' for v in packet)+'\n',encoding='ascii')
 print('words',len(packet),'records',len(records)//2,'model_id',f'{model_id:08x}')
if __name__=='__main__':main()
