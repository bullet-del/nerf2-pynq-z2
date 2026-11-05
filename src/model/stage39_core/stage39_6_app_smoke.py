#!/usr/bin/env python3
from __future__ import annotations
import argparse,json,subprocess,sys,time,urllib.request
from datetime import datetime,timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1];PKG=ROOT/'outputs/N2-NeRF2-Stage39-PC-App';PORT=8391
SCENES=('s23','Meetingroom','Office1','Office2');OUT=ROOT/'work_stage39/reports/stage39_6_app_smoke.json'
def request(path,payload=None,timeout=30):
 data=None if payload is None else json.dumps(payload).encode();r=urllib.request.Request(f'http://127.0.0.1:{PORT}'+path,data=data,headers={'Content-Type':'application/json'})
 with urllib.request.urlopen(r,timeout=timeout) as f:
  body=f.read();kind=f.headers.get_content_type()
 return json.loads(body.decode()) if kind=='application/json' else body
def main():
 global PKG,PORT,OUT
 parser=argparse.ArgumentParser();parser.add_argument('--package',type=Path,default=PKG);parser.add_argument('--port',type=int,default=PORT);parser.add_argument('--report',type=Path,default=OUT);args=parser.parse_args()
 PKG=args.package.resolve();PORT=args.port;OUT=args.report.resolve()
 command=[sys.executable,str(PKG/'pc_app/stage39_pc_server.py'),'--port',str(PORT),'--no-browser']
 process=subprocess.Popen(command,cwd=ROOT,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
 rows=[]
 try:
  deadline=time.monotonic()+60
  while True:
   if process.poll() is not None:raise RuntimeError('Stage39 App exited during smoke-test startup')
   try:registry=request('/api/scenes',timeout=1);break
   except Exception:
    if time.monotonic()>deadline:raise TimeoutError('Stage39 App did not become ready')
    time.sleep(.25)
  if registry.get('schema')!='n2-stage39-hardware-exact-qat-four-scene-registry-v1':raise AssertionError('registry identity mismatch')
  for scene in SCENES:
   cat=request(f'/scene/{scene}/api/catalog');candidate=cat['candidates'][0]['spectrum_id']
   if cat.get('schema')!='n2-stage39-judge-public-catalog-v1' or cat['scene']['hardware']['qat_algorithm_id']!='stage39-hardware-exact-qat-ste-v1':raise AssertionError(scene+': catalog identity mismatch')
   page=request(f'/scene/{scene}/');cdf=request(f'/scene/{scene}/four_scene_ssim_cdf.png')
   preview=request(f'/scene/{scene}/api/pc-preview',{'spectrum_id':candidate},timeout=120)
   if b'Stage39' not in page or len(cdf)<10000 or preview.get('fpga_used') is not False or preview.get('gt_available') is not True:raise AssertionError(scene+': UI or PC-preview smoke mismatch')
   request(preview['image_url'])
   rows.append({'scene':scene,'candidate':candidate,'catalog_samples':cat['test_samples'],'pc_preview_seconds':preview['seconds'],'pc_preview_fpga_used':preview['fpga_used'],'image_ok':True})
  report={'schema':'n2-stage39-app-smoke-v1','stage':'Stage39.6','created_utc':datetime.now(timezone.utc).isoformat(),'status':'PASS','scenes':rows}
 finally:
  process.terminate()
  try:process.wait(timeout=10)
  except subprocess.TimeoutExpired:process.kill();process.wait()
 OUT.parent.mkdir(parents=True,exist_ok=True);OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps({'status':report['status'],'scenes':len(rows)},ensure_ascii=False))
if __name__=='__main__':main()
