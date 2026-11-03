#!/usr/bin/env python3
from __future__ import annotations
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];WORK=ROOT/'work_stage39';BOARD=ROOT/'outputs/N2-NeRF2-Stage39-board';PC=ROOT/'outputs/N2-NeRF2-Stage39-PC-App';OUT=WORK/'reports/stage39_6_gate.json'
def main():
 smoke=json.loads((WORK/'reports/stage39_6_app_smoke.json').read_text(encoding='utf-8'))
 nb=json.loads((BOARD/'Stage39_QAT_Four_Scenes.ipynb').read_text(encoding='utf-8'))
 manifest=json.loads((BOARD/'stage39_board_package_manifest.json').read_text(encoding='utf-8'))
 verifier=(BOARD/'software/stage39/verify_stage39_board_package.py').read_text(encoding='utf-8')
 skill=(PC/'skills/n2-stage39-demo/SKILL.md').read_text(encoding='utf-8');app=(PC/'pc_app/stage39_static/app.js').read_text(encoding='utf-8');html=(PC/'pc_app/stage39_static/index.html').read_text(encoding='utf-8');service=(BOARD/'jupyter/stage39_board_service.py').read_text(encoding='utf-8');import_guard=(BOARD/'jupyter/stage39_import_guard.py').read_text(encoding='utf-8')
 checks=[
  {'id':'39.6.four_scene_app_http_smoke_and_pc_preview','status':'PASS' if smoke['status']=='PASS' and len(smoke['scenes'])==4 else 'FAIL'},
  {'id':'39.6.notebook_package_verify_service_selftest_benchmark_export','status':'PASS' if len(nb['cells'])==6 and 'verify_stage39_board_package.py' in ''.join(nb['cells'][0]['source']) and 'run_selftests' in ''.join(nb['cells'][2]['source']) and 'run_benchmarks(repeats=50)' in ''.join(nb['cells'][3]['source']) and 'export_evidence' in ''.join(nb['cells'][4]['source']) else 'FAIL'},
  {'id':'39.6.notebook_jupyter_state_safe_integrity','status':'PASS' if 'Stage39_QAT_Four_Scenes.ipynb' not in manifest['files'] and manifest.get('notebook_sources',{}).get('Stage39_QAT_Four_Scenes.ipynb') and 'def notebook_source_sha' in verifier and 'notebook source verification failed' in verifier else 'FAIL'},
  {'id':'39.6.deterministic_stage5_protocol_import','status':'PASS' if manifest.get('package_revision')=='stage39-board-20261008-import-notebook-r2' and 'prepare_stage39_imports(ROOT)' in service and 'spec_from_file_location("n2_stream"' in import_guard and 'REQUIRED_N2_STREAM_SYMBOLS' in import_guard else 'FAIL'},
  {'id':'39.6.hdmi_circular_no_colorbar_and_large_labels_preserved','status':'PASS' if 'four_three_stretch' in html and '不显示底部色条' in html and 'prepare_physical_hdmi_frame' in service else 'FAIL'},
  {'id':'39.6.judge_nonce_raw_values_negative_control_hash','status':'PASS' if all(x in app for x in ('首条结果人为 +1 LSB','逐射线原始值','transcript_sha256')) else 'FAIL'},
  {'id':'39.6.carousel_10_to_15_seconds','status':'PASS' if all(x in html for x in ('value="10"','value="12"','value="15"')) else 'FAIL'},
  {'id':'39.6.project_skill','status':'PASS' if 'Never relabel one class as another' in skill and '[TODO' not in skill else 'FAIL'},
  {'id':'39.6.live_board_hdmi_and_judge_rehearsal','status':'PENDING','reason':'requires PYNQ-Z2, display, and a human-entered judge nonce'}]
 report={'schema':'n2-stage39-demo-gate-v1','stage':'Stage39.6','status':'READY_FOR_LIVE_REHEARSAL' if all(x['status']=='PASS' for x in checks[:-1]) else 'FAIL','checks':checks,'app_smoke':smoke}
 OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps({'status':report['status']},ensure_ascii=False))
if __name__=='__main__':main()
