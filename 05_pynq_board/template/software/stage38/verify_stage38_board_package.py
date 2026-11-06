#!/usr/bin/env python3
from __future__ import annotations
import ast,hashlib,json,re
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
MANIFEST=ROOT/'stage38_board_package_manifest.json'
SCENES=('s23','Meetingroom','Office1','Office2')
def sha(p):
 h=hashlib.sha256()
 with Path(p).open('rb') as f:
  for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
 return h.hexdigest()

LOCAL_ROOTS=(ROOT/'jupyter',ROOT/'software/stage5',ROOT/'software/stage8',
             ROOT/'software/stage10',ROOT/'software/stage24')
def local_module(name):
 rel=Path(*name.split('.'))
 for base in LOCAL_ROOTS:
  for candidate in (base/(str(rel)+'.py'),base/rel/'__init__.py'):
   if candidate.is_file():return candidate
 return None
def assigned_names(target):
 if isinstance(target,ast.Name):return {target.id}
 if isinstance(target,(ast.Tuple,ast.List)):
  result=set()
  for value in target.elts:result.update(assigned_names(value))
  return result
 return set()
def module_exports(path):
 tree=ast.parse(Path(path).read_text(encoding='utf-8'),filename=str(path));result=set()
 def visit_statements(statements):
  for node in statements:
   if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef)):result.add(node.name)
   elif isinstance(node,(ast.Assign,ast.AnnAssign)):
    targets=node.targets if isinstance(node,ast.Assign) else [node.target]
    for target in targets:result.update(assigned_names(target))
   elif isinstance(node,(ast.Import,ast.ImportFrom)):
    for alias in node.names:result.add(alias.asname or alias.name.split('.')[0])
   elif isinstance(node,ast.If):visit_statements(node.body);visit_statements(node.orelse)
   elif isinstance(node,ast.Try):
    visit_statements(node.body);visit_statements(node.orelse);visit_statements(node.finalbody)
    for handler in node.handlers:visit_statements(handler.body)
  
 visit_statements(tree.body);return tree,result
def verify_local_import_symbols(entries):
 pending=[Path(x) for x in entries];checked=set()
 while pending:
  path=pending.pop()
  if path in checked:continue
  checked.add(path);tree,_=module_exports(path)
  for node in ast.walk(tree):
   if not isinstance(node,ast.ImportFrom) or node.level or not node.module:continue
   target=local_module(node.module)
   if target is None:continue
   _,exports=module_exports(target)
   missing=[alias.name for alias in node.names if alias.name!='*' and alias.name not in exports]
   if missing:raise RuntimeError('%s imports missing symbol(s) %s from %s' %
                                 (path.relative_to(ROOT),','.join(missing),target.relative_to(ROOT)))
   pending.append(target)
def main():
 m=json.loads(MANIFEST.read_text(encoding='utf-8'))
 if m.get('schema')!='n2-stage38-board-package-manifest-v1':raise RuntimeError('Stage38 board manifest identity mismatch')
 for rel,expected in m['files'].items():
  p=ROOT/rel
  if not p.is_file() or sha(p)!=expected:raise RuntimeError('Stage38 board immutable file verification failed: '+rel)
 required=(ROOT/'jupyter/pynq_stage38_raw.py',ROOT/'jupyter/stage38_board_service.py',
           ROOT/'jupyter/stage38_service_launcher.py',ROOT/'jupyter/stage38_service_supervisor.py',
           ROOT/'Stage38_Raw_Only_Four_Scenes.ipynb')
 if not all(p.is_file() for p in required):raise RuntimeError('Stage38 raw-only service files are incomplete')
 verify_local_import_symbols(required[:4])
 universal=required[0].read_text(encoding='utf-8');service=required[1].read_text(encoding='utf-8')
 if re.search(r'calibrated|output_calibration|校准',universal+'\n'+service,re.I):
  raise RuntimeError('Stage38 active board path still contains output processing')
 if "fpga=raw" not in universal or "'output_mode':'raw_int8_q15'" not in universal:
  raise RuntimeError('Stage38 hardware map is not returned directly from raw Q15')
 if 'migrate_stage37_raw_evidence' not in service or "q.astype(np.float64)/(1<<15)" not in service:
  raise RuntimeError('Stage38 raw evidence migration/map response is missing')
 artifact=json.loads((ROOT/'sim/stage38_artifact_manifest.json').read_text(encoding='utf-8'))
 if artifact.get('schema')!='n2-stage38-artifacts-v1' or artifact.get('output_mode')!='raw_int8_q15':
  raise RuntimeError('Stage38 artifact manifest is not raw-only')
 if sha(ROOT/'sim/n2_stage38_raw.bit')!=artifact['bit_sha256'] or sha(ROOT/'sim/n2_stage38_raw.hwh')!=artifact['hwh_sha256']:
  raise RuntimeError('Stage38 bit/hwh hash mismatch')
 for scene in SCENES:
  base=ROOT/'scenes'/scene
  for rel in ('data/stage33/output_calibration.json','data/stage33/output_calibration.npy','model/output_calibration.json','model/output_calibration.npy'):
   if (base/rel).exists():raise RuntimeError(scene+': forbidden output-processing asset remains: '+rel)
 notebook=json.loads(required[4].read_text(encoding='utf-8'))
 source=''.join(''.join(cell.get('source',[])) for cell in notebook.get('cells',[]))
 if 'stage38_service_launcher import start' not in source or 'n2_stage38_raw.bit' not in source:
  raise RuntimeError('Stage38 Notebook does not launch the detached raw-only service')
 print('STAGE38 BOARD PACKAGE VERIFY PASS');print('files_checked =',len(m['files']));print('output_mode = raw_int8_q15')
if __name__=='__main__':main()
