#!/usr/bin/env python3
"""Synthetic control test for the Stage39 exporter/finalizer code paths.

This file never produces competition evidence.  It uses a temporary directory
and deletes it after validating the archive, identity, timing, accuracy, power,
and live-rehearsal gates.
"""
from __future__ import annotations
import csv, importlib.util, json, shutil, sys, tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
BOARD=ROOT/'outputs/N2-NeRF2-Stage39-board'
PC=ROOT/'outputs/N2-NeRF2-Stage39-PC-App'
SCENES=('s23','Meetingroom','Office1','Office2')

def module(name,path):
 spec=importlib.util.spec_from_file_location(name,path);value=importlib.util.module_from_spec(spec)
 spec.loader.exec_module(value);return value
def write(path,value):path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value,indent=2)+'\n',encoding='utf-8')

def main():
 pg=module('stage39_physical_gate_test',BOARD/'jupyter/stage39_physical_gate.py')
 fin=module('finalize_stage39_evidence_test',PC/'software/stage39/finalize_stage39_evidence.py')
 specs={x['id']:x for x in json.loads((PC/'scene_registry.json').read_text(encoding='utf-8'))['scenes']}
 with tempfile.TemporaryDirectory(prefix='stage39_synthetic_control_') as td:
  tmp=Path(td);board=tmp/'board';pc=tmp/'pc';pg.ROOT=board;fin.ROOT=pc
  shutil.copytree(PC/'scenes',pc/'scenes',ignore=shutil.ignore_patterns('evidence'))
  shutil.copy2(PC/'scene_registry.json',pc/'scene_registry.json')
  self_rows=[];bench_rows=[]
  for scene in SCENES:
   hw=specs[scene]['hardware'];digest=('a'+str(SCENES.index(scene)))*32
   self_rows.append({'scene_id':scene,'physical_full_map_vs_portable_integer_bit_exact':True,
    'fpga_hardware_rays':32400,'actual_raw_q15_sha256':digest,'expected_raw_q15_sha256':digest,
    'runtime_model_sha256':hw['runtime_model_sha256'],'full_map_seconds':1.0})
   row={'schema':'n2-stage39-physical-latency-stability-v1','scene_id':scene,'repeats':50,
    'median_seconds':1.0,'p95_seconds':1.0,'minimum_seconds':.99,'maximum_seconds':1.01,
    'times_seconds':[1.0]*50,'stable_raw_q15_hash':True,'matches_portable_integer':True}
   bench_rows.append(row);write(board/f'scenes/{scene}/evidence/stage39/benchmark_50.json',row)
   catalog=pc/f'scenes/{scene}/data/stage32/all_test_catalog.json'
   cat_hash=fin.hashlib.sha256(catalog.read_bytes()).hexdigest();count=specs[scene]['test_count']
   identity={'schema':'n2-stage39-all-test-identity-v1','scene_id':scene,
    'bit_sha256':hw['bit_sha256'],'runtime_model_sha256':hw['runtime_model_sha256'],
    'test_catalog_sha256':cat_hash,'output_mode':'raw_int8_q15','qat_algorithm_id':'stage39-hardware-exact-qat-ste-v1'}
   rows=[{'test_output_index':i,'spectrum_id':'synthetic-%s-%d'%(scene,i),
    'bit_sha256':hw['bit_sha256'],'runtime_model_sha256':hw['runtime_model_sha256'],
    'test_catalog_sha256':cat_hash} for i in range(count)]
   result={'schema':'n2-stage39-full-test-fpga-validation-v1-qat-raw-only','scene_id':scene,
    'sample_count':count,'rows':rows,'summary':{'pc_ssim_mean':.70,'fpga_raw_ssim_mean':.69,
    'pc_minus_fpga_raw_mean_ssim':.01},'output_mode':'raw_int8_q15','bit_sha256':hw['bit_sha256'],
    'runtime_model_sha256':hw['runtime_model_sha256'],'test_catalog_sha256':cat_hash}
   write(board/f'scenes/{scene}/evidence/stage39/all_test/identity.json',identity)
   write(board/f'scenes/{scene}/evidence/stage39/all_test/result.json',result)
   write(pc/f'scenes/{scene}/evidence/stage39_pc_app/all_1225/validation.json',result)
  write(board/'evidence/stage39_physical_gate/selftests.json',{'status':'PASS','scenes':self_rows})
  write(board/'evidence/stage39_physical_gate/benchmark_50.json',{'status':'PASS','repeats_per_scene':50,'scenes':bench_rows})
  export=pg.export_evidence();archive=Path(export['archive'])
  manifest,files=fin.load_physical_export(archive)
  selftests=fin.validate_selftests(files,specs);benchmarks=fin.validate_benchmarks(files)
  exported=fin.validate_exported_full_tests(files,specs);local=fin.validate_pc_full_tests(specs)
  power=tmp/'power.csv'
  with power.open('w',encoding='utf-8',newline='') as stream:
   writer=csv.DictWriter(stream,fieldnames=('scene','state','repeat','voltage_v','current_a','power_w','instrument','timestamp_utc','notes'));writer.writeheader()
   for scene in SCENES:
    for state,current in (('idle',.4),('inference',.6)):
     for repeat in range(10):writer.writerow({'scene':scene,'state':state,'repeat':repeat+1,'voltage_v':5,'current_a':current,'power_w':5*current,'instrument':'synthetic-control','timestamp_utc':'2000-01-01T00:00:00Z','notes':'NOT PHYSICAL EVIDENCE'})
  powers=fin.validate_power(power,benchmarks)
  assert len(selftests)==len(benchmarks)==len(exported)==len(local)==len(powers)==4
  print(json.dumps({'status':'PASS','scope':'synthetic control only; not competition evidence','archive_schema':manifest['schema'],'scenes':4}))

if __name__=='__main__':main()
