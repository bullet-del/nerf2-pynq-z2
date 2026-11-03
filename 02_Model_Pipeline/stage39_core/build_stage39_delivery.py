#!/usr/bin/env python3
from __future__ import annotations
import argparse,hashlib,json,zipfile
from datetime import datetime,timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'outputs'
PACKAGES=(('board',OUT/'N2-NeRF2-Stage39-board','n2-stage39-board-package-manifest-v1'),
          ('pc',OUT/'N2-NeRF2-Stage39-PC-App','n2-stage39-pc-package-manifest-v1'))
NOTEBOOK_REL='Stage39_QAT_Four_Scenes.ipynb'
BOARD_PACKAGE_REVISION='stage39-board-20261008-import-notebook-r2'
def sha(path):
 h=hashlib.sha256()
 with Path(path).open('rb') as f:
  for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
 return h.hexdigest()
def notebook_source_sha(path):
 notebook=json.loads(Path(path).read_text(encoding='utf-8-sig'))
 if notebook.get('nbformat')!=4 or not isinstance(notebook.get('cells'),list):
  raise RuntimeError(f'invalid Jupyter notebook: {path}')
 cells=[]
 for index,cell in enumerate(notebook['cells']):
  if not isinstance(cell,dict) or not isinstance(cell.get('cell_type'),str):
   raise RuntimeError(f'invalid cell {index} in {path}')
  source=cell.get('source','')
  if isinstance(source,list):
   if not all(isinstance(part,str) for part in source):
    raise RuntimeError(f'invalid source in cell {index} of {path}')
   source=''.join(source)
  if not isinstance(source,str):
   raise RuntimeError(f'invalid source in cell {index} of {path}')
  cells.append({'cell_type':cell['cell_type'],'source':source})
 canonical=json.dumps({'nbformat':4,'cells':cells},ensure_ascii=False,sort_keys=True,separators=(',',':')).encode('utf-8')
 return hashlib.sha256(canonical).hexdigest()
def include(kind,root,path):
 rel=path.relative_to(root);pos=rel.as_posix();parts=rel.parts
 if '__pycache__' in parts or path.suffix=='.pyc' or 'evidence' in parts:return False
 if kind=='board' and (pos in ('stage38_board_package_manifest.json','Stage38_Raw_Only_Four_Scenes.ipynb','STAGE38_RUN.md')):return False
 if kind=='pc':
  if pos in ('stage38_package_manifest.json','run_stage38_app.bat','STAGE38_RUN.md','STAGE38_RAW_ACCURACY.json'):return False
  if pos.startswith('pc_app/stage38_') or pos.startswith('software/stage38/'):return False
 return path.is_file()
def selected(kind,root):return sorted((p for p in root.rglob('*') if include(kind,root,p)),key=lambda p:p.relative_to(root).as_posix())
def main():
 parser=argparse.ArgumentParser()
 parser.add_argument('--package',choices=('all','board','pc'),default='all')
 args=parser.parse_args()
 delivery_path=OUT/'stage39_delivery_sha256.json'
 if args.package!='all' and delivery_path.is_file():
  delivery=json.loads(delivery_path.read_text(encoding='utf-8'))
  if delivery.get('schema')!='n2-stage39-delivery-v1':raise RuntimeError('existing delivery manifest identity mismatch')
 else:
  delivery={'schema':'n2-stage39-delivery-v1','packages':{}}
 delivery['created_utc']=datetime.now(timezone.utc).isoformat()
 packages=PACKAGES if args.package=='all' else tuple(package for package in PACKAGES if package[0]==args.package)
 for kind,root,schema in packages:
  manifest_path=root/('stage39_board_package_manifest.json' if kind=='board' else 'stage39_pc_package_manifest.json')
  files=selected(kind,root)
  immutable_files=[p for p in files if p!=manifest_path and not (kind=='board' and p.relative_to(root).as_posix()==NOTEBOOK_REL)]
  immutable_scope=('all ZIP files except this manifest and Jupyter-managed notebook runtime state; '
    'notebook cell types and sources are verified separately; runtime evidence directories are intentionally absent'
    if kind=='board' else 'all ZIP files except this manifest; runtime evidence directories are intentionally absent')
  manifest={'schema':schema,'created_utc':datetime.now(timezone.utc).isoformat(),'package':root.name,
    'immutable_scope':immutable_scope,'files':{p.relative_to(root).as_posix():sha(p) for p in immutable_files}}
  if kind=='board':
   manifest['package_revision']=BOARD_PACKAGE_REVISION
   manifest['notebook_sources']={NOTEBOOK_REL:notebook_source_sha(root/NOTEBOOK_REL)}
  manifest_path.write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
  files=selected(kind,root)
  archive=OUT/(root.name+'.zip')
  with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED,compresslevel=6,allowZip64=True) as z:
   for i,p in enumerate(files,1):
    z.write(p,(Path(root.name)/p.relative_to(root)).as_posix())
    if i%500==0:print(f'[{kind}] {i}/{len(files)}',flush=True)
  delivery['packages'][kind]={'directory':root.name,'archive':archive.name,'files':len(files),'bytes':archive.stat().st_size,'sha256':sha(archive),'manifest_sha256':sha(manifest_path)}
  print(kind,delivery['packages'][kind],flush=True)
 delivery_path.write_text(json.dumps(delivery,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
if __name__=='__main__':main()
