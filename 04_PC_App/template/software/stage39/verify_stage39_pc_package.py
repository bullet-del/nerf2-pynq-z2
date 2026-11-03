#!/usr/bin/env python3
from __future__ import annotations
import hashlib,json,re
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2];MANIFEST=ROOT/'stage39_pc_package_manifest.json'
SCENES=('s23','Meetingroom','Office1','Office2')
def sha(p):
 h=hashlib.sha256()
 with Path(p).open('rb') as f:
  for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
 return h.hexdigest()
def main():
 m=json.loads(MANIFEST.read_text(encoding='utf-8'))
 if m.get('schema')!='n2-stage39-pc-package-manifest-v1':raise RuntimeError('Stage39 PC manifest identity mismatch')
 for rel,expected in m['files'].items():
  p=ROOT/rel
  if not p.is_file() or sha(p)!=expected:raise RuntimeError('Stage39 PC immutable file verification failed: '+rel)
 core=(ROOT/'pc_app/stage39_scene_core.py').read_text(encoding='utf-8');server=(ROOT/'pc_app/stage39_pc_server.py').read_text(encoding='utf-8')
 app=(ROOT/'pc_app/stage39_static/app.js').read_text(encoding='utf-8');html=(ROOT/'pc_app/stage39_static/index.html').read_text(encoding='utf-8')
 finalizer=(ROOT/'software/stage39/finalize_stage39_evidence.py').read_text(encoding='utf-8')
 if 'stage39-hardware-exact-qat-four-scene-service-v1' not in core or 'stage39_runtime_model_sha256' not in core:raise RuntimeError('Stage39 PC/board identity handshake is missing')
 if 'physical_full_map_vs_portable_integer_bit_exact' not in app:raise RuntimeError('Stage39 physical exactness UI is missing')
 if re.search(r'FPGA calibrated|fpga_calibrated|output_calibration\.(npy|json)',core+'\n'+server+'\n'+app+'\n'+html,re.I):raise RuntimeError('A derived output-calibration branch remains')
 if 'n2-stage39-physical-finalization-v1' not in finalizer or not (ROOT/'evidence_templates/stage39_board_input_power.csv').is_file():raise RuntimeError('Stage39 physical finalization workflow is missing')
 registry=json.loads((ROOT/'scene_registry.json').read_text(encoding='utf-8'))
 if registry.get('schema')!='n2-stage39-hardware-exact-qat-four-scene-registry-v1' or len(registry.get('scenes',[]))!=4:raise RuntimeError('Stage39 scene registry mismatch')
 bit_hashes=set()
 for scene in SCENES:
  spec=json.loads((ROOT/'scenes'/scene/'scene.json').read_text(encoding='utf-8'));hw=spec['hardware'];bit_hashes.add(hw['bit_sha256'])
  if hw.get('qat_algorithm_id')!='stage39-hardware-exact-qat-ste-v1' or hw.get('output_calibration') is not None:raise RuntimeError(scene+': invalid Stage39 QAT policy')
  if len(hw.get('runtime_model_sha256',''))!=64:raise RuntimeError(scene+': runtime model identity is missing')
  if not spec.get('stage39_gap_under_0p015'):raise RuntimeError(scene+': Stage39 accuracy gate failed')
 if len(bit_hashes)!=1:raise RuntimeError('Scenes do not declare the same bitstream')
 print('STAGE39 PC PACKAGE VERIFY PASS');print('files_checked =',len(m['files']));print('scenes = 4; output = raw_int8_q15; output_calibration = none')
if __name__=='__main__':main()
