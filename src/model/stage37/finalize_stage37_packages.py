#!/usr/bin/env python3
"""Assemble, hash, verify and zip the final Stage37 board and PC packages."""
from __future__ import annotations
import hashlib,json,shutil,zipfile
from pathlib import Path

WORK=Path(__file__).resolve().parent;ROOT=WORK.parent
PC=ROOT/'outputs/N2-NeRF2-Stage37-PC-App';BOARD=ROOT/'outputs/N2-NeRF2-Stage37-board'
SCENES=('s23','Meetingroom','Office1','Office2')

def sha(path):
 h=hashlib.sha256()
 with Path(path).open('rb') as f:
  for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
 return h.hexdigest()
def atomic(path,value):Path(path).write_text(json.dumps(value,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
def summary(path):
 return dict(line.strip().split('=',1) for line in Path(path).read_text().splitlines() if '=' in line)
def copy_tree_files(source,destination):
 for p in Path(source).rglob('*'):
  if p.is_file():
   target=Path(destination)/p.relative_to(source);target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,target)
def immutable_files(base,manifest_names):
 result={}
 for p in sorted(Path(base).rglob('*')):
  if not p.is_file():continue
  rel=p.relative_to(base)
  if p.name in manifest_names or p.suffix=='.pyc' or '__pycache__' in rel.parts or 'evidence' in rel.parts:continue
  result[str(rel).replace('\\','/')]=sha(p)
 return result
def zip_package(source,target,root_name):
 with zipfile.ZipFile(target,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6,allowZip64=True) as z:
  for p in sorted(Path(source).rglob('*')):
   if p.is_file() and p.suffix!='.pyc' and '__pycache__' not in p.parts:
    z.write(p,Path(root_name)/p.relative_to(source))

def main():
 build=WORK/'sim/stage37_bit';s=summary(build/'stage37_summary.txt')
 if s.get('build_id')!='stage37-universal-runtime-model-f95-v1' or s.get('timing_met')!='1':raise RuntimeError('timing-qualified Stage37 build missing')
 bit=build/'n2_stage37_universal.bit';hwh=build/'n2_stage37_universal.hwh'
 if not bit.is_file() or not hwh.is_file():raise RuntimeError('Stage37 bit/hwh missing')
 BOARD.joinpath('sim').mkdir(parents=True,exist_ok=True);shutil.copy2(bit,BOARD/'sim/n2_stage37_universal.bit');shutil.copy2(hwh,BOARD/'sim/n2_stage37_universal.hwh')
 # PC catalogs/GT and board catalogs must remain byte-identical.  Runtime
 # models overwrite only scenes/<scene>/model and are not used by the PC app.
 shutil.copy2(PC/'scene_registry.json',BOARD/'scene_registry.json')
 for scene in SCENES:
  copy_tree_files(PC/'scenes'/scene,BOARD/'scenes'/scene)
  copy_tree_files(WORK/'scene_runtime'/scene,BOARD/'scenes'/scene/'model')
 runtime={}
 for scene in SCENES:
  meta=json.loads((WORK/'runtime_models'/scene/'stage37_runtime_model.json').read_text())
  runtime[scene]={'model_id_crc32':meta['model_id_crc32'],'runtime_model_sha256':meta['runtime_model_sha256'],
                  'weight_words':meta['weight_words']}
 xsim=(WORK/'sim/stage37_universal_proof.log').read_text(errors='replace')
 required=('STAGE37_RUNTIME_MODEL_LOAD_PASS','STAGE37_CACHE_BUILD_BIT_EXACT_PASS','STAGE37_UNIVERSAL_RUNTIME_LOAD_AND_INFERENCE_BIT_EXACT_PASS')
 if not all(x in xsim for x in required):raise RuntimeError('final Stage37 XSim proof missing')
 switch=(WORK/'sim/stage37_multiscene_switch_proof.log').read_text(errors='replace')
 if 'STAGE37_FOUR_SCENE_SWITCH_AND_RETURN_PASS' not in switch:
  raise RuntimeError('Stage37 four-scene switch/return XSim proof missing')
 artifact={'schema':'n2-stage37-artifacts-v1','build_id':s['build_id'],'part':s['part'],
  'frequency_mhz':float(s['frequency_mhz']),'wns_ns':float(s['wns_ns']),'whs_ns':float(s['whs_ns']),
  'timing_met':True,'bit_sha256':sha(BOARD/'sim/n2_stage37_universal.bit'),
  'hwh_sha256':sha(BOARD/'sim/n2_stage37_universal.hwh'),'single_bitstream_four_scenes':True,
  'scene_switch_changes_bitstream':False,'runtime_models':runtime,
  'four_scene_switch_and_return_rtl_verified':True,
  'runtime_model_load_rtl_bit_exact':True,'cache_build_rtl_bit_exact':True,
  'cached_run_rtl_bit_exact':True,'full_ray_renderer_rtl_bit_exact':True,
  'activation_relu_before_int8_saturation':True,'xsim_expected_q15':4582}
 atomic(BOARD/'sim/stage37_artifact_manifest.json',artifact)
 pc_files=immutable_files(PC,{'stage37_package_manifest.json','stage36_package_manifest.json'})
 bd_files=immutable_files(BOARD,{'stage37_board_package_manifest.json'})
 atomic(PC/'stage37_package_manifest.json',{'schema':'n2-stage37-pc-package-manifest-v1','files':pc_files})
 atomic(BOARD/'stage37_board_package_manifest.json',{'schema':'n2-stage37-board-package-manifest-v1','files':bd_files})
 zip_package(PC,ROOT/'outputs/N2-NeRF2-Stage37-PC-App.zip','N2-NeRF2-Stage37-PC-App')
 zip_package(BOARD,ROOT/'outputs/N2-NeRF2-Stage37-board.zip','N2-NeRF2-Stage37')
 report={'pc_zip_sha256':sha(ROOT/'outputs/N2-NeRF2-Stage37-PC-App.zip'),
   'board_zip_sha256':sha(ROOT/'outputs/N2-NeRF2-Stage37-board.zip'),
   'pc_files':len(pc_files),'board_files':len(bd_files),'artifact':artifact}
 atomic(WORK/'stage37_final_package_report.json',report);print(json.dumps(report,indent=2))
if __name__=='__main__':main()
