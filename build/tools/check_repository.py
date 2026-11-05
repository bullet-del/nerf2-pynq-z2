"""Check AMD layout, documentation, Python and recorded functional results.

No source checksum manifest is required or generated. Model/deployment identity
fields in scientific records remain part of the original numeric contract.
"""
from __future__ import annotations
import ast
import json
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT=Path(__file__).resolve().parents[2]
TOP={'src','sim','build','board','data','skill','report','docs','.github'}
RESERVED={'README.md','LICENSE','SKILL.md'}
EXCLUDED={'.git','.Xil','__pycache__','external','work','outputs','.pytest_cache'}
SCENES={'s23':1225,'Meetingroom':200,'Office1':200,'Office2':100}
REQUIRED=[
 'README.md','LICENSE','src/rtl/n2_stage37_universal_stream_wrapper.v',
 'build/config/environment.yml','build/config/reproduction_config.json',
 'build/tcl/build_stage39.tcl','build/tcl/run_stage39_rtl_tests.tcl',
 'build/pipeline/reproduce_stage39.py','build/tools/configure_repository.py',
 'src/host/pc_app/stage39_pc_server.py','board/source/jupyter/stage39_board_service.py',
 'report/design_report.pdf','report/llm_collaboration.md','report/repository_update.md',
 'data/downloads.json','sim/validation/universal.log','sim/validation/multiscene.log',
 'build/reports/stage37_utilization.rpt','build/reports/stage37_timing_routed.rpt',
 'build/prebuilt/n2_stage38_raw.bit','build/prebuilt/n2_stage38_raw.hwh',
 'skill/nerf2-checkpoint-fpga-export/README.md','skill/pynq-overlay-validation/README.md',
 'skill/pynq-overlay-validation/verification.md','docs/directory_map.md',
]

def source_files():
    for p in ROOT.rglob('*'):
        rel=p.relative_to(ROOT)
        if not p.is_file() or any(part in EXCLUDED for part in rel.parts):continue
        if rel.as_posix()=='build/config/reproduction_config.local.json':continue
        if len(rel.parts)==1 and (p.name.startswith('vivado') and p.suffix in {'.log','.jou','.str'}):continue
        if rel.as_posix().startswith(('build/output/','src/power/results/')):continue
        yield p

def path_error(relative):
    for part in Path(relative).parts:
        if part in RESERVED or part in {'.github','.gitignore','.gitattributes'}:continue
        if not re.fullmatch(r'[a-z0-9][a-z0-9_.-]*',part):return part
    return None

def markdown_errors(files):
    errors=[]
    for p in files:
        if p.suffix!='.md':continue
        text=re.sub(r'```.*?```','',p.read_text(encoding='utf-8'),flags=re.S)
        for m in re.finditer(r'!?\[[^\]]*\]\(([^\n)]+)\)',text):
            raw=m[1].split(' "')[0].strip('<>');u=urlsplit(raw)
            if u.scheme or not u.path or '<' in raw:continue
            if not (p.parent/unquote(u.path)).exists():errors.append(str(p.relative_to(ROOT))+': '+raw)
    return errors

def check():
    errors=[];files=list(source_files());python_count=0
    for rel in REQUIRED:
        if not (ROOT/rel).is_file():errors.append('Missing: '+rel)
    for directory in TOP:
        if not (ROOT/directory).is_dir():errors.append('Missing directory: '+directory)
    for p in files:
        rel=p.relative_to(ROOT).as_posix()
        if '/' in rel and rel.split('/')[0] not in TOP:errors.append('Unexpected top directory: '+rel)
        if path_error(rel):errors.append('Path must use lower-case English: '+rel)
        if 'sha256' in p.name.lower() or p.name.lower() in {'input_manifest.json','publication_identity.json','core_source_identity.json','submission_manifest.json'}:
            errors.append('Standalone publication checksum inventory: '+rel)
        if p.suffix.lower() in {'.ps1','.pyc'}:errors.append('Unwanted script/cache: '+rel)
        if p.stat().st_size>=100*1024*1024:errors.append('Regular GitHub file too large: '+rel)
        try:
            if p.suffix=='.py':ast.parse(p.read_bytes(),filename=rel);python_count+=1
            if p.suffix in {'.json','.ipynb'}:json.loads(p.read_text(encoding='utf-8'))
        except Exception as e:errors.append(rel+': '+str(e))
    errors+=markdown_errors(files)
    rtl=list((ROOT/'src/rtl').glob('*.v'))+list((ROOT/'src/rtl').glob('*.sv'))
    if len(rtl)!=9:errors.append('Expected nine RTL sources')
    config=json.loads((ROOT/'build/config/reproduction_config.json').read_text(encoding='utf-8'))
    if list(config['scenes'])!=list(SCENES):errors.append('Changed scene order')
    if config['qat']['steps']!=1800 or config['qat']['learning_rate']!=1e-5:errors.append('Changed formal QAT settings')
    if config['calibration']['count']!=8192 or config['acceptance']['maximum_mean_ssim_gap']!=0.015:errors.append('Changed formal calibration/accuracy contract')
    total=0
    for scene,count in SCENES.items():
        rows=json.loads((ROOT/'data/reference_results'/scene.lower()/'validation.json').read_text(encoding='utf-8'))['rows']
        if len(rows)!=count or len({r['spectrum_id'] for r in rows})!=count:errors.append('Full test coverage: '+scene)
        total+=len(rows)
        if config['scenes'][scene]['activation_percentile']!=99.5:errors.append('Changed activation scale: '+scene)
    final=json.loads((ROOT/'board/evidence/physical_finalization.json').read_text(encoding='utf-8'))
    if final['status']!='FULL_PASS' or not final['claims_allowed']:errors.append('Archived physical finalization did not pass')
    for path,token in [('sim/validation/universal.log','STAGE39_RUNTIME_LOAD_CACHE_AND_INFERENCE_PASS'),('sim/validation/multiscene.log','STAGE39_FOUR_SCENE_SWITCH_AND_RETURN_PASS')]:
        if token not in (ROOT/path).read_text(encoding='utf-8',errors='replace'):errors.append('Missing executed RTL PASS: '+path)
    readme=(ROOT/'README.md').read_text(encoding='utf-8')
    for label in ['src/','sim/','build/','board/','data/','skill/','report/']:
        if '| `'+label+'` |' not in readme:errors.append('README guide correspondence missing: '+label)
    if errors:raise RuntimeError('\n'.join(errors))
    print('REPOSITORY CHECK PASS')
    print('AMD directory map: 7/7; lower-case paths and document links: PASS')
    print('Python syntax: {}; recorded held-out maps: {}'.format(python_count,total))
    print('No standalone source SHA256 inventory; recorded results retain their original scope.')
    return {'source_files':len(files),'python_files':python_count,'maps':total}

if __name__=='__main__':check()
