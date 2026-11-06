#!/usr/bin/env python3
from __future__ import annotations
import argparse,hashlib,json
from pathlib import Path

SCENES=("s23","Meetingroom","Office1","Office2")
REPORTS=("stage39_0_gate.json","stage39_1_gate.json","stage39_2_gate.json",
         "stage39_3_gate.json","stage39_6_gate.json","stage39_final_gate.json")
def locate(start):
 p=Path(start).resolve()
 for candidate in (p,*p.parents):
  if (candidate/'stage39_pc_package_manifest.json').is_file() and (candidate/'scene_registry.json').is_file():return candidate
 raise RuntimeError('extracted N2-NeRF2-Stage39-PC-App package was not found')
def load(path):return json.loads(Path(path).read_text(encoding='utf-8')) if Path(path).is_file() else None
def sha(path):
 h=hashlib.sha256()
 with Path(path).open('rb') as f:
  for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
 return h.hexdigest()
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--workspace',default=Path.cwd());args=ap.parse_args();root=locate(args.workspace)
 manifest=load(root/'stage39_pc_package_manifest.json');bad=[]
 for relative,expected in manifest.get('files',{}).items():
  path=root/relative
  if not path.is_file() or sha(path)!=expected:bad.append(relative)
 registry=load(root/'scene_registry.json');specs={x['id']:x for x in registry.get('scenes',[])}
 reports={name:(load(root/'stage39_reports'/name) or {}).get('status','MISSING') for name in REPORTS}
 full_test={}
 for scene in SCENES:
  path=root/'scenes'/scene/'evidence/stage39_pc_app/all_1225/validation.json';value=load(path)
  valid=bool(value and value.get('schema')=='n2-stage39-full-test-fpga-validation-v1-qat-raw-only'
   and value.get('sample_count')==specs[scene]['test_count']
   and value.get('runtime_model_sha256')==specs[scene]['hardware']['runtime_model_sha256']
   and value.get('bit_sha256')==specs[scene]['hardware']['bit_sha256'])
  full_test[scene]='PASS' if valid else 'PENDING'
 physical=load(root/'stage39_reports/stage39_physical_finalization.json')
 result={'package_root':str(root),'immutable_package':'PASS' if not bad else 'FAIL',
  'bad_files':bad,'software_reports':reports,'physical_full_test':full_test,
  'physical_finalization':'PENDING' if physical is None else physical.get('status','INVALID'),
  'claim_rule':'Final physical claims require stage39_physical_finalization.json status FULL_PASS.'}
 print(json.dumps(result,ensure_ascii=False,indent=2))
 raise SystemExit(0 if not bad and all(v!='MISSING' for v in reports.values()) else 1)
if __name__=='__main__':main()
