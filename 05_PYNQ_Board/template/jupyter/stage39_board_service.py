#!/usr/bin/env python3
"""Persistent raw-only HTTP service for the Stage39 four-scene accelerator."""
from __future__ import annotations
import argparse,asyncio,base64,gc,hashlib,io,json,os,secrets,shutil,sys,threading,time,traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs,urlparse
import numpy as np
from PIL import Image

HERE=Path(__file__).resolve();ROOT=HERE.parents[1];os.chdir(str(ROOT))
from stage39_import_guard import prepare_stage39_imports
N2_STREAM=prepare_stage39_imports(ROOT)
from pynq_stage38_raw import (UniversalHardware,array_b64,free_compare_frame,
                              DRIVER_BUILD_ID,FPGA_BUILD_ID)
from stage28_exact_common import compose_clear_three_way,prepare_physical_hdmi_frame,save_rgb_png

SERVICE_BUILD_ID='stage39-hardware-exact-qat-four-scene-service-v1'
CHALLENGE_SPEC='n2-stage37-sha256-counter-rejection-ray-challenge-v2'
REGISTRY=json.loads((ROOT/'scene_registry.json').read_text());SPECS={v['id']:v for v in REGISTRY['scenes']}
HW=None;DEVICE=None;RUN_LOCK=threading.RLock();STATE_LOCK=threading.RLock();STATE={};LAST_ERROR=None
BATCH={};BATCH_THREADS={};BATCH_STOP={}
HARDWARE_THREAD_ID=None

def _hardware_thread_initializer():
    """Give the one PYNQ owner thread its own asyncio event loop.

    PYNQ 2022.1 calls nest_asyncio while constructing an Overlay.  Python 3.10
    no longer creates event loops automatically in worker threads, so every
    Overlay/DMA/HDMI operation is deliberately confined to this initialized
    thread.  HTTP and full-test worker threads only submit jobs here.
    """
    global HARDWARE_THREAD_ID
    loop=asyncio.new_event_loop();asyncio.set_event_loop(loop)
    HARDWARE_THREAD_ID=threading.get_ident()

HARDWARE_EXECUTOR=ThreadPoolExecutor(max_workers=1,thread_name_prefix='stage39-pynq',
                                     initializer=_hardware_thread_initializer)

def hardware_call(function,*args,**kwargs):
    if threading.get_ident()==HARDWARE_THREAD_ID:
        return function(*args,**kwargs)
    return HARDWARE_EXECUTOR.submit(function,*args,**kwargs).result()

def now():return datetime.now(timezone.utc).isoformat()
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def atomic(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True);tmp=path.with_suffix(path.suffix+'.tmp')
    tmp.write_text(json.dumps(value,ensure_ascii=False,allow_nan=False,indent=2)+'\n',encoding='utf-8');os.replace(tmp,path)
def decode_array(value):
    raw=base64.b64decode(str(value).encode('ascii'),validate=True)
    with np.load(io.BytesIO(raw),allow_pickle=False) as z:a=np.asarray(z['value'],np.float64)
    if a.shape!=(90,360) or not np.isfinite(a).all():raise ValueError('PC map must be finite 90x360')
    return a
def event_loop():
    try:l=asyncio.get_event_loop()
    except RuntimeError:l=asyncio.new_event_loop();asyncio.set_event_loop(l)
    return l
def initialize_pynq_device(force=False):
    global DEVICE
    if DEVICE is not None and not force:return DEVICE
    event_loop();from pynq.pl_server import Device
    from pynq.pl_server.embedded_device import EmbeddedDevice  # noqa:F401
    cached=Device.__dict__.get('_devices')
    if force or (cached is not None and len(cached)==0):
        for key in ('_devices','_active_device'):
            if hasattr(Device,key):delattr(Device,key)
    devices=list(Device.devices)
    if not devices:raise RuntimeError('No Devices Found; restart the Notebook kernel and verify /sys/class/fpga_manager/fpga0')
    DEVICE=Device.active_device;return DEVICE
def hardware():
    global HW
    if threading.get_ident()!=HARDWARE_THREAD_ID:
        raise RuntimeError('PYNQ hardware access must run on the Stage39 hardware thread')
    if HW is None:HW=UniversalHardware(ROOT,initialize_pynq_device())
    return HW
def set_state(scene,**kw):
    with STATE_LOCK:
        value=STATE.setdefault(scene,{'active':False,'phase':'service_ready'});value.update(kw,updated_utc=now())
def state(scene):
    with STATE_LOCK:return dict(STATE.get(scene,{'active':False,'phase':'service_ready'}))
def scene_root(scene):return ROOT/'scenes'/scene
def catalogs(scene):
    sr=scene_root(scene);cat=json.loads((sr/'data/stage31/catalog.json').read_text());allcat=json.loads((sr/'data/stage32/all_test_catalog.json').read_text())
    return cat,allcat,{v['spectrum_id']:v for v in allcat['samples']}
def record_error(scene,exc,phase):
    global LAST_ERROR
    LAST_ERROR={'ok':False,'scene_id':scene,'phase':phase,'exception_type':type(exc).__name__,'error':str(exc),
      'traceback_tail':traceback.format_exc().splitlines()[-12:],'utc':now(),'diagnostic_url':'/api/last-error'}
    atomic(scene_root(scene)/'evidence/stage39/last_error.json',LAST_ERROR);set_state(scene,active=False,phase='error');return LAST_ERROR

def challenge_canonical_input(nonce,scene,tx,count,bit_sha256,runtime_model_sha256):
    value={'spec':CHALLENGE_SPEC,'nonce':str(nonce),'scene_id':str(scene),
      'tx_float32':['%.9g'%float(np.float32(x)) for x in tx],
      'ray_population':32400,'ray_count':int(count),'resolution':[90,360],
      'bit_sha256':str(bit_sha256),'runtime_model_sha256':str(runtime_model_sha256)}
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False)
def challenge_indices(canonical,count):
    """Public, deterministic SHA-256 counter sampler with no RNG state."""
    selected=set();counter=0;limit=(1<<32)//32400*32400
    while len(selected)<int(count):
        digest=hashlib.sha256((canonical+'|counter='+str(counter)).encode('utf-8')).digest();counter+=1
        for offset in range(0,32,4):
            value=int.from_bytes(digest[offset:offset+4],'big')
            if value>=limit:continue
            selected.add(value%32400)
            if len(selected)>=int(count):break
    return np.asarray(sorted(selected),np.int64)
def challenge_transcript_sha256(challenge):
    core={'selection_algorithm':challenge['selection_algorithm'],
      'canonical_input':challenge['canonical_input'],'ray_indices':challenge['ray_indices'],
      'samples':challenge['samples'],'rerun_vs_full_map_bit_exact':challenge['rerun_vs_full_map_bit_exact'],
      'arm_vs_fpga_bit_exact':None if challenge.get('arm') is None else challenge['arm']['arm_vs_fpga_bit_exact']}
    encoded=json.dumps(core,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()
def finite_metrics(a,b):
    a=np.asarray(a,np.float64);b=np.asarray(b,np.float64);d=a-b
    return {'mse':float(np.mean(d*d)),'mae':float(np.mean(np.abs(d))),
            'max_abs':float(np.max(np.abs(d)))}

def run_request(scene,payload):
    request_started=time.perf_counter()
    cat,allcat,byid=catalogs(scene);mode=str(payload.get('mode',''));show=bool(payload.get('show_hdmi',False))
    if mode=='measured':
        sid=str(payload.get('spectrum_id',''))
        if sid not in byid:raise ValueError('measured mode requires a held-out test ID')
        item=byid[sid];tx=np.asarray(item['tx'],np.float32);gt=np.asarray(Image.open(scene_root(scene)/item['gt_png']).convert('L'),np.float64)/255
    elif mode=='free':
        sid=None;item=None;gt=None;tx=np.asarray(payload.get('tx'),np.float32)
        bounds=np.asarray([SPECS[scene]['tx_bounds_m'][k] for k in 'xyz'])
        if tx.shape!=(3,) or not np.isfinite(tx).all() or np.any(tx<bounds[:,0]) or np.any(tx>bounds[:,1]):raise ValueError('Tx is outside '+scene+' x/y/z range')
    else:raise ValueError('mode must be measured or free')
    pc=decode_array(payload['pc_map_npz_base64']);rid=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ_')+secrets.token_hex(4)
    out=scene_root(scene)/'evidence/stage39/live'/rid;out.mkdir(parents=True)
    def progress(done,total,seconds):set_state(scene,active=True,phase='fpga_full_map',completed_rows=done,total_rows=total,map_elapsed_seconds=seconds)
    set_state(scene,active=True,phase='runtime_model_and_cache_check',completed_rows=0,total_rows=90)
    def execute_on_hardware():
      with RUN_LOCK:
        report,fpga,q15=hardware().run_map(scene,tx,pc,gt,progress)
        nonce=str(payload.get('nonce','')).strip();count=int(payload.get('challenge_count',8))
        if nonce:
            if count not in (8,16,32):raise ValueError('challenge_count must be 8, 16, or 32')
            bit_sha256=hardware().artifact['bit_sha256'];runtime_model_sha256=sha(scene_root(scene)/'model/stage37_runtime_model.npz')
            canonical=challenge_canonical_input(nonce,scene,tx,count,bit_sha256,runtime_model_sha256)
            indices=challenge_indices(canonical,count);challenge=hardware().rerun_indices(scene,tx,indices,q15)
            full_q15=np.asarray(challenge['expected_q15'],np.uint32);rerun_q15=np.asarray(challenge['rerun_q15'],np.uint32)
            if not np.array_equal(full_q15,rerun_q15):
                raise AssertionError('Stage39 random challenge rerun differs from the completed FPGA map')
            samples=[{'ray_index':int(index),'elevation_row':int(index//360),
              'azimuth_column':int(index%360),'full_map_q15':int(expected),
              'rerun_q15':int(actual),'delta_q15':int(actual)-int(expected),'arm_q15':None}
              for index,expected,actual in zip(indices,full_q15,rerun_q15)]
            selection_bytes=np.asarray(indices,dtype='<u4').tobytes()
            challenge.update({'nonce':nonce,'selection_algorithm':CHALLENGE_SPEC,
              'canonical_input':canonical,'canonical_input_sha256':hashlib.sha256(canonical.encode('utf-8')).hexdigest(),
              'selection_sha256':hashlib.sha256(selection_bytes).hexdigest(),
              'ray_count':int(len(indices)),'ray_indices':indices.tolist(),
              'samples':samples,'mismatch_count':0,'max_abs_q15_delta':0,
              'production_packet_count':int(len(np.unique((indices//360)//20))),
              'rerun_hardware_rays':int(challenge['surrounding_hardware_rays']),
              'rerun_vs_full_map_bit_exact':bool(challenge['bit_exact']),
              'timing':{'fpga_challenge_transaction_seconds':float(challenge['fpga_stream_seconds'])},
              'arm':None,'metrics':{}})
            fpga_selected=np.asarray(challenge['rerun_q15'],np.uint32).astype(np.float64)/(1<<15)
            challenge['metrics']['pc_vs_fpga']=finite_metrics(pc.reshape(-1)[indices],fpga_selected)
            if bool(payload.get('run_arm',False)):
                arm,arm_seconds=hardware().arm_indices(scene,tx,indices)
                arm_exact=bool(np.array_equal(arm,np.asarray(challenge['rerun_q15'],np.uint32)))
                arm_fp=arm.astype(np.float64)/(1<<15)
                challenge['arm']={'arm_vs_fpga_bit_exact':arm_exact,'raw_q15_sha256':hashlib.sha256(arm.tobytes()).hexdigest(),
                  'timing':{'challenge_compute_seconds':float(arm_seconds)},'implementation':'independent NumPy sparse INT8/Q15 on Zynq ARM'}
                for sample,arm_value in zip(samples,np.asarray(arm,np.uint32)):
                    sample['arm_q15']=int(arm_value);sample['arm_minus_fpga_q15']=int(arm_value)-int(sample['rerun_q15'])
                challenge['metrics']['fpga_vs_arm']=finite_metrics(fpga_selected,arm_fp)
                if not arm_exact:raise AssertionError('Stage39 ARM integer reference differs from FPGA challenge rays')
            challenge['transcript_sha256']=challenge_transcript_sha256(challenge)
            report['random_ray_challenge']=challenge
        else:report['random_ray_challenge']=None
        inference_done=time.perf_counter()-request_started
        frame,_=compose_clear_three_way(gt,pc,fpga,1280,720,str(payload.get('display_style','shared'))) if gt is not None else (free_compare_frame(pc,fpga),None)
        if frame is not None:save_rgb_png(out/'hdmi_1280x720.png',frame)
        if show and frame is not None:
            from pynq_full_exact_90x360 import _display_base_overlay
            physical,aspect=prepare_physical_hdmi_frame(frame,str(payload.get('hdmi_display_aspect','four_three_stretch')))
            save_rgb_png(out/'physical_hdmi_1280x720.png',physical)
            try:
                hdmi_driver=_display_base_overlay(physical)
            finally:
                # BaseOverlay replaces the accelerator bitstream and leaves
                # video S2MM running.  Never reuse the now-stale accelerator
                # Overlay/RawDMA handles on the next Tx request.
                hardware().mark_hdmi_overlay_active()
            report['hdmi']={'requested':True,'upload_ok':True,'driver':hdmi_driver,
              'aspect_correction':aspect,'accelerator_restore':'deferred_until_next_hardware_request'}
        else:report['hdmi']={'requested':show,'upload_ok':False,'skipped_reason':'HDMI output was not requested'}
        request_done=time.perf_counter()-request_started
        report['timing'].update({'run_entry_to_inference_complete_seconds':float(inference_done),
          'run_entry_to_result_ready_seconds':float(request_done),
          'run_entry_to_hdmi_seconds':float(request_done),
          'inference_complete_to_hdmi_seconds':float(max(0.,request_done-inference_done))})
        report.update(run_id=rid,spectrum_id=sid);atomic(out/'report.json',report);set_state(scene,active=False,phase='complete',run_id=rid,completed_rows=90)
        return {'ok':True,'scene_id':scene,'run_id':rid,'report':report,'fpga_map_npz_base64':array_b64(fpga),
          'fpga_raw_q15_sha256':hashlib.sha256(q15.tobytes()).hexdigest(),
          'evidence_directory':str(out.relative_to(scene_root(scene)))}
    return hardware_call(execute_on_hardware)

def batch_paths(scene):
    d=scene_root(scene)/'evidence/stage39/all_test';return d,d/'records.jsonl',d/'status.json',d/'result.json'
def read_records(path):
    if not path.is_file():return []
    rows=[];repaired=False
    for index,line in enumerate(path.read_text(encoding='utf-8').splitlines()):
        if not line.strip():continue
        try:row=json.loads(line)
        except json.JSONDecodeError:
            repaired=True;break
        if int(row.get('test_output_index',-1))!=len(rows):
            repaired=True;break
        saved=path.parent/str(row.get('fpga_map_npz',''))
        if not saved.is_file() or sha(saved)!=row.get('fpga_map_npz_sha256'):
            repaired=True;break
        rows.append(row)
    if repaired:
        tmp=Path(str(path)+'.repair')
        with tmp.open('w',encoding='utf-8') as f:
            for row in rows:f.write(json.dumps(row,ensure_ascii=False)+'\n')
            f.flush();os.fsync(f.fileno())
        os.replace(tmp,path)
    return rows
def batch_status(scene):
    _,_,p,_=batch_paths(scene)
    if p.is_file():return json.loads(p.read_text())
    total=SPECS[scene]['test_count'];return {'ok':True,'scene_id':scene,'status':'not_started','completed':0,'total':total}
def _raw_record(row):
    keep=('test_output_index','spectrum_id','tx','pc_ssim_vs_gt','fpga_raw_ssim_vs_gt',
          'map_seconds','fpga_raw_q15_sha256','fpga_map_npz','fpga_map_npz_sha256',
          'bit_sha256','runtime_model_sha256','test_catalog_sha256')
    value={key:row[key] for key in keep if key in row}
    value['fpga_ssim_vs_gt']=value['fpga_raw_ssim_vs_gt']
    return value

def batch_identity(scene):
    hw=SPECS[scene]['hardware']
    return {'schema':'n2-stage39-all-test-identity-v1','scene_id':scene,
      'bit_sha256':hw['bit_sha256'],'runtime_model_sha256':hw['runtime_model_sha256'],
      'test_catalog_sha256':sha(scene_root(scene)/'data/stage32/all_test_catalog.json'),
      'output_mode':'raw_int8_q15','qat_algorithm_id':'stage39-hardware-exact-qat-ste-v1'}

def archive_batch_if_needed(scene,restart):
    d,records_path,status_path,result_path=batch_paths(scene);d.mkdir(parents=True,exist_ok=True)
    identity_path=d/'identity.json';expected=batch_identity(scene);saved=None
    if identity_path.is_file():
        try:saved=json.loads(identity_path.read_text())
        except Exception:saved=None
    has_evidence=records_path.is_file() or result_path.is_file() or (d/'maps').is_dir()
    incompatible=has_evidence and saved!=expected
    if restart or incompatible:
        arc=d/('archive_'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'_'+secrets.token_hex(3))
        arc.mkdir(parents=True,exist_ok=False)
        for p in (records_path,status_path,result_path,identity_path,d/'maps'):
            if p.exists():os.replace(p,arc/p.name)
    atomic(identity_path,expected)
    return expected,bool(incompatible)

def run_batch(scene,restart=False):
    d,records_path,status_path,result_path=batch_paths(scene);d.mkdir(parents=True,exist_ok=True)
    cat,allcat,byid=catalogs(scene);samples=allcat['samples'];total=len(samples)
    identity,archived_incompatible=archive_batch_if_needed(scene,bool(restart))
    rows=[_raw_record(row) for row in read_records(records_path)]
    for row in rows:
        if any(row.get(key)!=identity[key] for key in ('bit_sha256','runtime_model_sha256','test_catalog_sha256')):
            raise RuntimeError('saved Stage39 row identity differs from the active bitstream/model/test catalog; restart this scene batch')
    stop=BATCH_STOP.setdefault(scene,threading.Event());stop.clear()
    try:
      for index in range(len(rows),total):
        if stop.is_set():atomic(status_path,{'ok':True,'scene_id':scene,'status':'paused','completed':index,'total':total});return
        item=samples[index];gt=np.asarray(Image.open(scene_root(scene)/item['gt_png']).convert('L'),np.float64)/255
        set_state(scene,active=True,phase='all_test_fpga_raw_map',sample=index+1,spectrum_id=item['spectrum_id'])
        report,fpga,q15=hardware_call(lambda: hardware().run_map(scene,np.asarray(item['tx'],np.float32),None,gt))
        raw=report['metrics']['gt_vs_fpga_raw'];rel='maps/'+item['spectrum_id']+'_q15.npz';mp=d/rel;mp.parent.mkdir(exist_ok=True)
        with mp.open('wb') as f:np.savez_compressed(f,q15=q15)
        row={'test_output_index':index,'spectrum_id':item['spectrum_id'],'tx':item['tx'],
          'pc_ssim_vs_gt':item['pc_ssim_vs_gt'],'fpga_raw_ssim_vs_gt':raw['ssim_data_range_1'],
          'fpga_ssim_vs_gt':raw['ssim_data_range_1'],'map_seconds':report['timing']['full_map_cached_pipeline_seconds'],
          'fpga_raw_q15_sha256':hashlib.sha256(q15.tobytes()).hexdigest(),
          'fpga_map_npz':rel,'fpga_map_npz_sha256':sha(mp),
          'bit_sha256':identity['bit_sha256'],'runtime_model_sha256':identity['runtime_model_sha256'],
          'test_catalog_sha256':identity['test_catalog_sha256']}
        with records_path.open('a',encoding='utf-8') as f:f.write(json.dumps(row,ensure_ascii=False)+'\n');f.flush();os.fsync(f.fileno())
        rows.append(row);atomic(status_path,{'ok':True,'scene_id':scene,'status':'running','completed':len(rows),'total':total,'current_spectrum_id':item['spectrum_id']})
        del gt,fpga,q15,report;gc.collect()
      raw=np.asarray([x['fpga_raw_ssim_vs_gt'] for x in rows]);pc=np.asarray([x['pc_ssim_vs_gt'] for x in rows])
      representative={}
      for sid in cat.get('representatives',{}).values():
        row=next((x for x in rows if x['spectrum_id']==sid),None)
        if row is not None:
          with np.load(d/row['fpga_map_npz'],allow_pickle=False) as z:q=np.asarray(z['q15'],np.uint32)
          representative[sid]=array_b64(q.astype(np.float64)/(1<<15))
      mean_seconds=float(np.mean([x['map_seconds'] for x in rows]));median_seconds=float(np.median([x['map_seconds'] for x in rows]))
      result={'schema':'n2-stage39-full-test-fpga-validation-v1-qat-raw-only','scene_id':scene,
       'sample_count':total,'rows':rows,
       'summary':{'pc_ssim_mean':float(pc.mean()),'pc_fp32_ssim_mean':float(pc.mean()),
        'fpga_raw_ssim_mean':float(raw.mean()),'pc_minus_fpga_raw_mean_ssim':float(pc.mean()-raw.mean()),
        'mean_map_seconds':mean_seconds,'median_map_seconds':median_seconds,
        'maximum_map_seconds':float(max(x['map_seconds'] for x in rows))},
       'representative_fpga_maps':representative,'output_mode':'raw_int8_q15',
        'single_bitstream_four_scenes':True,'bit_sha256':identity['bit_sha256'],
        'runtime_model_sha256':identity['runtime_model_sha256'],
        'test_catalog_sha256':identity['test_catalog_sha256'],
        'incompatible_previous_evidence_archived':archived_incompatible}
      atomic(result_path,result);atomic(status_path,{'ok':True,'scene_id':scene,'status':'complete','completed':total,'total':total})
    except Exception as exc:
      err=record_error(scene,exc,'all_test');atomic(status_path,{'ok':False,'scene_id':scene,'status':'error','completed':len(rows),'total':total,'error':err['error'],'phase':err['phase']})
    finally:set_state(scene,active=False,phase='service_ready')
def start_batch(scene,restart):
    t=BATCH_THREADS.get(scene)
    if t is not None and t.is_alive():return batch_status(scene)
    t=threading.Thread(target=run_batch,args=(scene,bool(restart)),daemon=True);BATCH_THREADS[scene]=t;t.start();return {'ok':True,'scene_id':scene,'status':'starting','completed':0,'total':SPECS[scene]['test_count']}
def map_response(scene,sid):
    _,_,_,result_path=batch_paths(scene)
    if not result_path.is_file():raise RuntimeError('complete FPGA raw test-set evidence is required')
    result=json.loads(result_path.read_text());identity=batch_identity(scene)
    if any(result.get(key)!=identity[key] for key in ('bit_sha256','runtime_model_sha256','test_catalog_sha256')):
        raise RuntimeError('saved Stage39 result identity differs from the active runtime package')
    row=next((x for x in result['rows'] if x['spectrum_id']==sid),None)
    if row is None:raise ValueError('unknown test spectrum_id')
    mp=result_path.parent/row['fpga_map_npz']
    if sha(mp)!=row['fpga_map_npz_sha256']:raise RuntimeError('saved FPGA raw map hash mismatch')
    with np.load(mp,allow_pickle=False) as z:q=np.asarray(z['q15'],np.uint32)
    fpga=q.astype(np.float64)/(1<<15)
    return {'ok':True,'scene_id':scene,'spectrum_id':sid,'fpga_map_npz_base64':array_b64(fpga),
      'fpga_raw_q15_sha256':row['fpga_raw_q15_sha256'],
      'source':{'kind':'saved_stage39_qat_fpga_raw_full_test','scene_id':scene,
        'bit_sha256':result['bit_sha256'],'map_sha256':row['fpga_map_npz_sha256']}}

class Handler(BaseHTTPRequestHandler):
 def log_message(self,fmt,*args):print('[Stage39 HTTP] '+fmt%args,flush=True)
 def scene_path(self):
    p=urlparse(self.path);parts=p.path.split('/',3)
    if len(parts)==4 and parts[1]=='scene' and parts[2] in SPECS:return parts[2],'/'+parts[3],p.query
    return REGISTRY['default_scene'],p.path,p.query
 def send_json(self,code,value):
    b=json.dumps(value,ensure_ascii=False,allow_nan=False).encode()
    try:self.send_response(code);self.send_header('Content-Type','application/json; charset=utf-8');self.send_header('Content-Length',str(len(b)));self.send_header('Connection','close');self.end_headers();self.wfile.write(b)
    except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError):pass
 def do_GET(self):
  scene,path,query=self.scene_path()
  try:
    if path=='/api/health':
        self.send_json(200,{'ok':True,'scene_id':scene,'hardware_available':True,
          'service_build_id':SERVICE_BUILD_ID,'runner_build_id':DRIVER_BUILD_ID,'fpga_build_id':FPGA_BUILD_ID,
          'same_bitstream_for_all_scenes':True,'output_mode':'raw_int8_q15',
          'training_method':'train-only hardware-exact QAT','output_calibration':None,
          'stage39_runtime_model_sha256':sha(scene_root(scene)/'model/stage37_runtime_model.npz'),
          'state':state(scene)})
    elif path=='/api/active':self.send_json(200,{'ok':True,'scene_id':scene,'state':state(scene)})
    elif path=='/api/catalog':
        cat,allcat,_=catalogs(scene);self.send_json(200,{'ok':True,'scene_id':scene,'candidate_count':len(allcat['samples']),'candidates':allcat['samples']})
    elif path=='/api/all-test/status':self.send_json(200,batch_status(scene))
    elif path=='/api/all-test/result':
        p=batch_paths(scene)[3];value=json.loads(p.read_text())
        if value.get('schema')!='n2-stage39-full-test-fpga-validation-v1-qat-raw-only':
            raise RuntimeError('saved full-test summary is not Stage39 raw-only evidence')
        identity=batch_identity(scene)
        if any(value.get(key)!=identity[key] for key in ('bit_sha256','runtime_model_sha256','test_catalog_sha256')):
            raise RuntimeError('saved Stage39 summary identity differs from the active runtime package')
        self.send_json(200,{'ok':True,'result':value})
    elif path=='/api/all-test/map':self.send_json(200,map_response(scene,parse_qs(query).get('spectrum_id',[''])[0]))
    elif path=='/api/last-error':self.send_json(200,LAST_ERROR or {'ok':True,'error':None})
    else:self.send_json(404,{'ok':False,'error':'unknown endpoint'})
  except Exception as exc:self.send_json(500,record_error(scene,exc,path))
 def do_POST(self):
  scene,path,_=self.scene_path()
  try:
    n=int(self.headers.get('Content-Length','0'));payload=json.loads(self.rfile.read(n).decode()) if n else {}
    if path=='/api/run':self.send_json(200,run_request(scene,payload))
    elif path=='/api/selftest':
      started=time.perf_counter()
      def selftest_hardware():
       with RUN_LOCK:
        cfg=hardware().configure(scene,True)
        configured=time.perf_counter()-started
        sid=json.loads((scene_root(scene)/'data/stage32/all_test_catalog.json').read_text())['samples'][0]
        rep,_,_=hardware().run_map(scene,np.asarray(sid['tx'],np.float32))
        contract=json.loads((scene_root(scene)/'model/stage39_physical_selftest.json').read_text())
        if str(sid['spectrum_id'])!=str(contract['spectrum_id']):
            raise AssertionError('Stage39 physical self-test spectrum identity mismatch')
        actual=rep['provenance']['raw_q15_sha256']
        if actual!=contract['raw_q15_sha256']:
            raise AssertionError('Stage39 full 32400-ray FPGA result differs from portable integer reference')
        return cfg,configured,rep,contract
      cfg,configured,rep,contract=hardware_call(selftest_hardware)
      self.send_json(200,{'ok':True,'scene_id':scene,'configuration':cfg,'overlay_seconds':configured,
       'full_map_seconds':rep['timing']['full_map_cached_pipeline_seconds'],'fpga_hardware_rays':32400,
       'cache_sentinel_rays':512,'cache_sentinel_bit_exact':True,'same_bitstream_for_all_scenes':True,
       'physical_full_map_vs_portable_integer_bit_exact':True,
       'expected_raw_q15_sha256':contract['raw_q15_sha256'],
       'actual_raw_q15_sha256':rep['provenance']['raw_q15_sha256'],
       'runtime_model_sha256':contract['runtime_model_sha256'],
       'under_1p2_seconds':rep['timing']['full_map_cached_pipeline_seconds']<1.2})
    elif path=='/api/benchmark':
      repeats=int(payload.get('repeats',50))
      if repeats<1 or repeats>50:raise ValueError('benchmark repeats must be 1..50')
      def benchmark_hardware():
       with RUN_LOCK:
        contract=json.loads((scene_root(scene)/'model/stage39_physical_selftest.json').read_text())
        _,allcat,byid=catalogs(scene);item=byid[str(contract['spectrum_id'])]
        tx=np.asarray(item['tx'],np.float32);times=[];hashes=[];recoveries=[]
        for index in range(repeats):
            rep,_,_=hardware().run_map(scene,tx)
            times.append(float(rep['timing']['full_map_cached_pipeline_seconds']))
            hashes.append(rep['provenance']['raw_q15_sha256'])
            recoveries.append(int(rep.get('dma_pl_recovery',{}).get('automatic_retry_count',0)))
        values=np.asarray(times,np.float64)
        result={'schema':'n2-stage39-physical-latency-stability-v1','scene_id':scene,
          'repeats':repeats,'spectrum_id':contract['spectrum_id'],
          'median_seconds':float(np.median(values)),'p95_seconds':float(np.percentile(values,95)),
          'minimum_seconds':float(values.min()),'maximum_seconds':float(values.max()),
          'mean_seconds':float(values.mean()),'times_seconds':times,
          'stable_raw_q15_hash':len(set(hashes))==1,
          'matches_portable_integer':all(x==contract['raw_q15_sha256'] for x in hashes),
          'raw_q15_sha256':hashes[0],'total_automatic_retries':sum(recoveries),
          'median_under_1p2':float(np.median(values))<=1.2,
          'p95_under_1p5':float(np.percentile(values,95))<=1.5,
          'created_utc':now()}
        atomic(scene_root(scene)/'evidence/stage39/benchmark_50.json',result)
        return result
      self.send_json(200,{'ok':True,'result':hardware_call(benchmark_hardware)})
    elif path=='/api/all-test/start':self.send_json(202,start_batch(scene,payload.get('restart',False)))
    elif path=='/api/all-test/stop':BATCH_STOP.setdefault(scene,threading.Event()).set();self.send_json(202,{'ok':True,'scene_id':scene,'status':'stopping'})
    else:self.send_json(404,{'ok':False,'error':'unknown endpoint'})
  except Exception as exc:self.send_json(500,record_error(scene,exc,path))

def serve(host='0.0.0.0',port=8311):
    # Discover PYNQ and create the Overlay owner from the same event-loop thread
    # that will perform every later PL transaction.
    hardware_call(initialize_pynq_device,False)
    ThreadingHTTPServer.daemon_threads=True;ThreadingHTTPServer.allow_reuse_address=True
    server=ThreadingHTTPServer((host,int(port)),Handler);print(f'Stage39 universal service ready: http://{host}:{port}',flush=True);server.serve_forever()
if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--host',default='0.0.0.0');parser.add_argument('--port',type=int,default=8311)
    args=parser.parse_args();serve(args.host,args.port)

