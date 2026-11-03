"""One Stage39 App controlling one universal bitstream in four scenes."""
from __future__ import annotations
import argparse,hashlib,json,sys,threading,time,traceback,types,webbrowser
from datetime import datetime,timezone
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse
ROOT=Path(__file__).resolve().parents[1]
REGISTRY=json.loads((ROOT/'scene_registry.json').read_text(encoding='utf-8'))
SPECS={v['id']:v for v in REGISTRY['scenes']}
MODULES={};LOAD_LOCK=threading.RLock();GPU_LOCK=threading.RLock();PLOT_LOCK=threading.RLock()

def context(scene):
    if scene not in SPECS:raise ValueError('Unknown scene: '+scene)
    with LOAD_LOCK:
        if scene not in MODULES:
            m=types.ModuleType('_n2_scene_'+scene)
            m.__file__=str(ROOT/'pc_app/stage39_scene_core.py');m.SCENE_ID=scene
            sys.modules[m.__name__]=m
            exec(compile(Path(m.__file__).read_text(encoding='utf-8'),m.__file__,'exec'),m.__dict__)
            render=m.live_pc_render
            def locked_render(*args,_render=render,**kwargs):
                with GPU_LOCK:return _render(*args,**kwargs)
            m.live_pc_render=locked_render
            m.PLOT_LOCK=PLOT_LOCK
            for name in ('save_comparison','save_all_test_figures'):
                f=getattr(m,name)
                def locked_plot(*args,_f=f,**kwargs):
                    with PLOT_LOCK:return _f(*args,**kwargs)
                setattr(m,name,locked_plot)
            MODULES[scene]=m
        return MODULES[scene]

def prefix_urls(value,scene):
    if isinstance(value,dict):return {k:prefix_urls(v,scene) for k,v in value.items()}
    if isinstance(value,list):return [prefix_urls(v,scene) for v in value]
    if isinstance(value,str) and value.startswith(('/assets/','/evidence/','/api/job/')):
        return '/scene/'+scene+value
    return value

def sync_evidence(m,job_id,payload):
    try:
        url=m.normalize_board_url(payload['board_url']);m.require_stage35_board(url)
        cache=m.EVIDENCE/'carousel_fpga_cache';cache.mkdir(parents=True,exist_ok=True)
        for index,row in enumerate(m.ALL_TEST['samples']):
            sid=row['spectrum_id'];target=cache/(sid+'.json')
            if not target.is_file():
                value=m.board_get_resilient(url,'/api/all-test/map?spectrum_id='+m.quote(sid),30)
                validate_saved_map(value,sid)
                m.atomic_json(target,value)
            m.update_job(job_id,progress=(index+1)/m.SAMPLE_COUNT,phase=f'下载保存证据 {index+1}/{m.SAMPLE_COUNT}')
        m.update_job(job_id,status='complete',phase='全部 FPGA 保存图已下载，断电后可动态浏览',progress=1)
    except Exception as exc:
        m.update_job(job_id,status='error',error=str(exc),phase='证据下载未完成；已下载部分保留')

def validate_saved_map(value,sid):
    if value.get('spectrum_id')!=sid or not value.get('fpga_raw_q15_sha256'):
        raise ValueError('Saved FPGA evidence identity mismatch')
    if hashlib.sha256(value['fpga_map_npz_base64'].encode('ascii')).hexdigest()!=value.get('pc_transfer_sha256',hashlib.sha256(value['fpga_map_npz_base64'].encode('ascii')).hexdigest()):
        raise ValueError('Saved FPGA transfer hash mismatch')
    value['pc_transfer_sha256']=hashlib.sha256(value['fpga_map_npz_base64'].encode('ascii')).hexdigest()

class Handler(BaseHTTPRequestHandler):
    def log_message(self,fmt,*args):print('[Stage39 PC] '+fmt%args,flush=True)
    def send_json(self,code,value):
        value=prefix_urls(value,self.scene)
        body=json.dumps(value,ensure_ascii=False,allow_nan=False).encode('utf-8')
        try:
            self.send_response(code);self.send_header('Content-Type','application/json; charset=utf-8');self.send_header('Content-Length',str(len(body)));self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(body)
        except (BrokenPipeError,ConnectionError):pass
    def send_file(self,path):return self.module.Handler.send_file(self,path)
    def choose(self):
        self.scene='s23';p=urlparse(self.path)
        if p.path.startswith('/scene/'):
            parts=p.path.split('/',3)
            if len(parts)!=4 or parts[2] not in SPECS:raise ValueError('Unknown scene route')
            self.scene=parts[2];self.path='/'+parts[3]+(('?'+p.query) if p.query else '')
        self.module=context(self.scene)
    def do_GET(self):
        try:
            if self.path=='/api/scenes':
                self.scene='s23';return self.send_json(200,dict(ok=True,**REGISTRY))
            self.choose();return self.module.Handler.do_GET(self)
        except Exception as exc:
            traceback.print_exc();self.send_json(400,{'ok':False,'error':str(exc)})
    def do_POST(self):
        try:
            self.choose();path=urlparse(self.path).path;m=self.module
            if path in ('/api/run','/api/all-test','/api/board-selftest'):
                hw=SPECS[self.scene]['hardware']
                if not (hw.get('factorization_compatible') and hw.get('runtime_model_sha256')):
                    return self.send_json(422,{'ok':False,'scene_id':self.scene,'error':self.scene+' 缺少可装载的 Stage39 运行时模型。'})
            if path not in ('/api/pc-preview','/api/sync-evidence'):return m.Handler.do_POST(self)
            length=int(self.headers.get('Content-Length','0'))
            if not 2<=length<=2*1024*1024:raise ValueError('Invalid request body size')
            payload=json.loads(self.rfile.read(length).decode('utf-8'))
            if path=='/api/sync-evidence':
                job='sync_'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')
                with m.JOBS_LOCK:m.JOBS[job]={'job_id':job,'status':'running','progress':0,'phase':'下载保存证据'}
                threading.Thread(target=sync_evidence,args=(m,job,payload),daemon=True).start()
                return self.send_json(202,{'ok':True,'job_id':job})
            import numpy as np
            from PIL import Image
            sid=str(payload.get('spectrum_id',''))
            if payload.get('mode')=='free':
                tx=np.asarray(payload.get('tx'),dtype=np.float64)
                limits=np.asarray([m.TX_BOUNDS[k] for k in 'xyz'])
                if tx.shape!=(3,) or not np.isfinite(tx).all() or np.any(tx<limits[:,0]) or np.any(tx>limits[:,1]):raise ValueError('Tx outside this scene domain')
                gt=None;sid=None
            else:
                if sid not in m.ALL_BY_ID:raise ValueError('Not a held-out test sample')
                row=m.ALL_BY_ID[sid];tx=row['tx']
                gt=np.asarray(Image.open(m.ROOT/row['gt_png']).convert('L'),float)/255
            prediction,seconds,device=m.live_pc_render(tx)
            preview_id=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')+'_'+m.secrets.token_hex(6)
            base=m.EVIDENCE/'pc_preview'/preview_id;base.mkdir(parents=True,exist_ok=True)
            with PLOT_LOCK:
                import matplotlib;matplotlib.use('Agg');import matplotlib.pyplot as plt
                values=[('PC (GT N/A)',prediction)] if gt is None else [('GT',gt),('PC',prediction)]
                fig,axes=plt.subplots(1,len(values),figsize=(5*len(values),5),constrained_layout=True)
                limits=(min(v.min() for _,v in values),max(v.max() for _,v in values))
                for ax,(label,value) in zip(np.atleast_1d(axes),values):
                    ax.imshow(m.render_polar_rgb(value,480,limits));ax.set_title(label,fontsize=24);ax.axis('off')
                fig.savefig(base/'comparison.png',dpi=140);plt.close(fig)
            return self.send_json(200,{'ok':True,'scene_id':self.scene,'spectrum_id':sid,'tx':np.asarray(tx).tolist(),'seconds':seconds,'device':device,'metrics':None if gt is None else m.metrics(gt,prediction),'image_url':'/evidence/pc_preview/'+preview_id+'/comparison.png','fpga_used':False,'gt_available':gt is not None})
        except Exception as exc:
            traceback.print_exc();self.send_json(400,{'ok':False,'error':type(exc).__name__+': '+str(exc)})

class Server(ThreadingHTTPServer):
    allow_reuse_address=True;daemon_threads=True
def main():
    ap=argparse.ArgumentParser();ap.add_argument('--host',default='127.0.0.1');ap.add_argument('--port',type=int,default=8310);ap.add_argument('--no-browser',action='store_true');a=ap.parse_args()
    for scene in SPECS:context(scene)
    server=Server((a.host,a.port),Handler);url=f'http://127.0.0.1:{a.port}'
    print('Stage39 one-bitstream four-scene App:',url,flush=True)
    if not a.no_browser:threading.Timer(1,lambda:webbrowser.open(url)).start()
    try:server.serve_forever()
    finally:server.server_close()
if __name__=='__main__':main()

