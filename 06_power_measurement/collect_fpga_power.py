"""Audit all twelve FPGA captures and emit integration rows plus 80 gate samples."""
from __future__ import annotations
import argparse
import csv
import json
import subprocess
import sys
from datetime import timedelta
from pathlib import Path
from audit_board_power_capture import decode, parse_time

SCENES=('s23','Meetingroom','Office1','Office2')


def read(path): return json.loads(Path(path).read_text(encoding='utf-8'))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--manifest',type=Path,required=True)
    p.add_argument('--output-root',type=Path,required=True)
    a=p.parse_args(); manifest=read(a.manifest); base=a.manifest.resolve().parent
    if manifest.get('schema')!='stage39-fpga-power-captures-v1': raise ValueError('Wrong manifest schema')
    expected={(s,'R'+str(r)) for s in SCENES for r in (1,2,3)}
    entries=manifest['runs']
    if len(entries)!=12 or {(e['scene'],e['repeat']) for e in entries}!=expected:
        raise ValueError('Exactly four scenes times R1/R2/R3 are required')
    if a.output_root.exists(): raise FileExistsError('Choose a new output directory: '+str(a.output_root))
    a.output_root.mkdir(parents=True)
    rows=[]; gate=[]; reports=[]
    for e in entries:
        paths={k:(base/e[k]).resolve() for k in ('run','cfn','alignment')}
        run=read(paths['run'])
        if run['scene']!=e['scene'] or run['repeat_id']!=e['repeat']:
            raise ValueError('Run identity differs from capture list: '+str(paths['run']))
        out=a.output_root/(e['scene'].lower()+'_'+e['repeat'].lower())
        subprocess.run([sys.executable,str(Path(__file__).with_name('audit_board_power_capture.py')),
            '--run',str(paths['run']),'--cfn',str(paths['cfn']),
            '--alignment',str(paths['alignment']),'--out-dir',str(out)],check=True)
        report=read(next(out.glob('*_audit.json')));reports.append(report)
        with next(out.glob('*_power_rows.csv')).open(encoding='utf-8-sig',newline='') as stream:
            rows.extend(csv.DictReader(stream))
        if e['repeat']=='R2':
            _, samples, _=decode(paths['cfn'])
            origin=parse_time(report['nominal_meter_time_zero_utc'])
            for state,label in (('idle','idle'),('active','inference')):
                start=(parse_time(run[state+'_start_utc'])-origin).total_seconds()
                end=(parse_time(run[state+'_end_utc'])-origin).total_seconds()
                eligible=[s for s in samples if start<=s['relative_time_s']<=end]
                if len(eligible)<10: raise ValueError('Fewer than 10 actual samples in '+state)
                # Ten fixed, evenly spaced observations; no selection by wattage.
                selected=[eligible[round(i*(len(eligible)-1)/9)] for i in range(10)]
                for i,s in enumerate(selected,1):
                    gate.append({'scene':e['scene'],'state':label,'repeat':i,
                        'voltage_v':s['voltage_V'],'current_a':s['current_A'],'power_w':s['power_W'],
                        'instrument':'FNIRSI FNB58 USB-PC',
                        'timestamp_utc':(origin+timedelta(seconds=s['relative_time_s'])).isoformat(),
                        'notes':'R2 raw PBUS sample; ten evenly spaced observations; '+paths['cfn'].name})
    for name,values in [('board_power_runs.csv',rows),('stage39_board_input_power.csv',gate)]:
        with (a.output_root/name).open('w',encoding='utf-8',newline='') as stream:
            writer=csv.DictWriter(stream,fieldnames=list(values[0]));writer.writeheader();writer.writerows(values)
    subprocess.run([sys.executable,str(Path(__file__).with_name('summarize_board_power.py')),
        str(a.output_root/'board_power_runs.csv'),'--out',str(a.output_root/'fpga_power_summary.json')],check=True)
    summary={'schema':'stage39-complete-fpga-capture-processing-v1','status':'PASS',
        'runs':12,'measured_map_inferences':sum(int(r['summary']['maps']) for r in reports),
        'gate_samples':len(gate),'arm_power_required':False,
        'missing_recorder_stop_events':[{'scene':r['scene'],'repeat':r['repeat_id']} for r in reports
            if r['time_alignment'].get('recorder_stop_observed') is False],
        'scope':'Original CFN PBUS trapezoidal integral; reviewed video phase anchors; full board USB measurement point'}
    (a.output_root/'processing.json').write_text(json.dumps(summary,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(summary,indent=2))


if __name__=='__main__': main()
