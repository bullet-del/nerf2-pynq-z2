#!/usr/bin/env python3
from __future__ import annotations
import json
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];R=ROOT/'work_stage39/reports';OUT=R/'stage39_final_gate.json'
def load(path):return json.loads(Path(path).read_text(encoding='utf-8'))
def main():
 stages={'39.0':load(R/'stage39_0_gate.json'),'39.1':load(R/'stage39_1_gate.json'),'39.2':load(R/'stage39_2_gate.json'),'39.3':load(R/'stage39_3_gate.json'),'39.4':load(R/'stage39_4/ablation.json'),'39.5':load(R/'stage39_5/stage39_5_gate.json'),'39.6':load(R/'stage39_6_gate.json')}
 physical=[]
 for key,value in stages.items():
  for c in value.get('checks',[]):
   if c.get('status')=='PENDING':physical.append({'stage':key,**c})
 software_fail=[{'stage':k,**c} for k,v in stages.items() for c in v.get('checks',[]) if c.get('status')=='FAIL']
 report={'schema':'n2-stage39-final-gate-v1','created_utc':datetime.now(timezone.utc).isoformat(),
  'status':'READY_FOR_REQUIRED_PHYSICAL_GATES' if not software_fail else 'FAIL',
  'software_failures':software_fail,'pending_physical_or_live_checks':physical,
  'stage_status':{k:v.get('status') for k,v in stages.items()},
  'completion_rule':'FULL PASS only after Stage39.2 physical exactness, Stage39.3 50-run timing and measured power, Stage39.4 physical ablations, Stage39.5 physical full-test archive, and Stage39.6 live HDMI rehearsal all pass.'}
 OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps({'status':report['status'],'software_failures':len(software_fail),'pending':len(physical)},ensure_ascii=False))
if __name__=='__main__':main()
