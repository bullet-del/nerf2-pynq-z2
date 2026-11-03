#!/usr/bin/env python3
from __future__ import annotations
import hashlib,json,re
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
MANIFEST=ROOT/'stage38_package_manifest.json'
SCENES=('s23','Meetingroom','Office1','Office2')
def sha(p):
 h=hashlib.sha256()
 with Path(p).open('rb') as f:
  for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
 return h.hexdigest()
def main():
 m=json.loads(MANIFEST.read_text(encoding='utf-8'))
 if m.get('schema')!='n2-stage38-pc-package-manifest-v1':raise RuntimeError('Stage38 PC manifest identity mismatch')
 for rel,expected in m['files'].items():
  p=ROOT/rel
  if not p.is_file() or sha(p)!=expected:raise RuntimeError('Stage38 PC immutable file verification failed: '+rel)
 server=(ROOT/'pc_app/stage38_pc_server.py').read_text(encoding='utf-8')
 core=(ROOT/'pc_app/stage38_scene_core.py').read_text(encoding='utf-8')
 app=(ROOT/'pc_app/stage38_static/app.js').read_text(encoding='utf-8')
 html=(ROOT/'pc_app/stage38_static/index.html').read_text(encoding='utf-8')
 joined='\n'.join((server,core,app,html))
 if 'stage38-raw-only-four-scene-service-v1' not in core or 'raw_int8_q15' not in core:
  raise RuntimeError('Stage38 PC/board raw-only handshake is missing')
 if re.search(r'calibrated|calibration|校准',joined,re.I):
  raise RuntimeError('Stage38 served App still contains an output-calibration branch')
 if 'FPGA 原始平均 SSIM' not in app or 'fpga_calibrated_ssim_mean' in app:
  raise RuntimeError('Stage38 App summary is not raw-only')
 if '归一化射线吞吐率' not in app or 'normalizedSpeedup' not in app:
  raise RuntimeError('Stage38 timing workload normalization is missing')
 registry=json.loads((ROOT/'scene_registry.json').read_text(encoding='utf-8'))
 if registry.get('schema')!='n2-stage38-raw-only-four-scene-registry-v1' or registry.get('output_mode')!='raw_int8_q15':
  raise RuntimeError('Stage38 scene registry is not raw-only')
 for scene in SCENES:
  base=ROOT/'scenes'/scene
  for rel in ('data/stage33/output_calibration.json','data/stage33/output_calibration.npy','model/output_calibration.json','model/output_calibration.npy'):
   if (base/rel).exists():raise RuntimeError(scene+': forbidden output-processing asset remains: '+rel)
  validation=json.loads((base/'evidence/stage38_pc_app/all_1225/validation.json').read_text(encoding='utf-8'))
  if validation.get('schema')!='n2-stage38-full-test-fpga-validation-v1-raw-only' or validation.get('output_mode')!='raw_int8_q15':
   raise RuntimeError(scene+': bundled validation is not Stage38 raw-only')
  if len(validation.get('rows',[]))!=validation.get('sample_count'):
   raise RuntimeError(scene+': raw validation row count mismatch')
  if any(any('calibrat' in key.lower() for key in row) for row in validation['rows']):
   raise RuntimeError(scene+': derived fields remain in raw validation rows')
  if not (base/'evidence/stage38_pc_app/all_1225/all_1225_ssim_cdf.png').is_file():
   raise RuntimeError(scene+': raw-only CDF is missing')
 print('STAGE38 PC PACKAGE VERIFY PASS');print('files_checked =',len(m['files']));print('output_mode = raw_int8_q15')
if __name__=='__main__':main()
