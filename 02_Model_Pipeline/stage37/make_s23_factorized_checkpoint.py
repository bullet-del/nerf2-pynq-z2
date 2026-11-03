#!/usr/bin/env python3
import argparse, hashlib, json
from pathlib import Path
import torch

ap=argparse.ArgumentParser(); ap.add_argument('source',type=Path); ap.add_argument('out',type=Path)
a=ap.parse_args(); value=torch.load(a.source,map_location='cpu')
state=value['nerf2_network_state_dict']
state['signal_linears.0.weight'][:, :256].zero_()
# Official L5 order is [encoded point (63), feature (256)].
state['attenuation_linears.5.weight'][:, 63:].zero_()
value['nerf2_network_state_dict']=state
value['stage37']={'schema':'n2-stage37-factorized-checkpoint-v1','scene':'s23',
 'source_checkpoint_sha256':hashlib.sha256(a.source.read_bytes()).hexdigest(),
 'signal_feature_columns_zero':True,'attenuation_layer5_feature_columns_zero':True,
 'steps':0,'method':'exact structural mask; no retraining'}
a.out.parent.mkdir(parents=True,exist_ok=True);torch.save(value,a.out)
a.out.with_suffix('.json').write_text(json.dumps(value['stage37'],indent=2),encoding='utf-8')
print(a.out)
