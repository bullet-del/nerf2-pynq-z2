#!/usr/bin/env python3
"""Align Stage37 App PC caches/catalogs with the factorized checkpoints."""
from __future__ import annotations
import hashlib,json,sys,time
from pathlib import Path
import numpy as np
from PIL import Image

ROOT=Path(__file__).resolve().parents[0]
PACKAGE=ROOT.parent/'outputs/N2-NeRF2-Stage37-PC-App'
sys.path[:0]=[
    str(PACKAGE/'software/stage11'),
    str(PACKAGE/'software/stage24'),
    str(PACKAGE/'jupyter'),
]
from reference import load_model,render_spectrum  # noqa:E402
from ray_common import official_s23_rays  # noqa:E402
from stage28_exact_common import comparison_metrics,render_polar_rgb  # noqa:E402
import torch  # noqa:E402

METRICS={'s23':'s23_factorized_fp32.json','Meetingroom':'Meetingroom_fp32.json',
         'Office1':'Office1_fp32.json','Office2':'Office2_fp32.json'}
SCENES=tuple(METRICS)

def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def atomic(path,value):
    path=Path(path);tmp=path.with_suffix(path.suffix+'.tmp')
    tmp.write_text(json.dumps(value,ensure_ascii=False,allow_nan=False,indent=2)+'\n',encoding='utf-8');tmp.replace(path)
def metrics(gt,pred):
    v=comparison_metrics(gt,pred);v['mse']=float(v['rmse']**2)
    for k,x in list(v.items()):
        if isinstance(x,(float,np.floating)) and not np.isfinite(x):v[k]=None
    return v

@torch.inference_mode()
def render_selected(scene_root,spec,catalog,ids):
    device='cuda' if torch.cuda.is_available() else 'cpu'
    model,_=load_model(scene_root/spec['checkpoint'],scene_root/'pc_app/model/config.yml',device)
    origins,directions=official_s23_rays(catalog['gateway_position'],catalog['gateway_orientation_xyzw'])
    ro=torch.from_numpy(origins).to(device);rd=torch.from_numpy(directions).to(device)
    byid={x['spectrum_id']:x for x in json.loads((scene_root/'data/stage32/all_test_catalog.json').read_text())['samples']}
    out=scene_root/'data/stage37_pc_maps';out.mkdir(parents=True,exist_ok=True);values={}
    for seq,sid in enumerate(ids,1):
        tx=np.asarray(byid[sid]['tx'],np.float32);pieces=[];started=time.perf_counter()
        for begin in range(0,len(origins),512):
            end=min(len(origins),begin+512);tt=torch.from_numpy(np.broadcast_to(tx,(end-begin,3)).copy()).to(device)
            pieces.append(render_spectrum(model,tt,ro[begin:end],rd[begin:end]))
        if device=='cuda':torch.cuda.synchronize()
        elapsed=time.perf_counter()-started;pred=torch.cat(pieces).detach().cpu().numpy().reshape(90,360).astype(np.float32)
        target=out/(sid+'.npy');np.save(target,pred,allow_pickle=False)
        gt=np.asarray(Image.open(scene_root/byid[sid]['gt_png']).convert('L'),np.float64)/255.
        values[sid]={'path':str(target.relative_to(scene_root)).replace('\\','/'),'sha256':sha(target),
                     'seconds':elapsed,'metrics':metrics(gt,pred),'prediction':pred}
        print(f'[{spec["id"]} {seq}/{len(ids)}] {sid} {elapsed:.3f}s SSIM={values[sid]["metrics"]["ssim_data_range_1"]:.6f}',flush=True)
    del model
    if device=='cuda':torch.cuda.empty_cache()
    return values

def figures(scene_root,scene,summary,representatives,byid,rendered):
    import matplotlib;matplotlib.use('Agg');import matplotlib.pyplot as plt
    assets=scene_root/'data/stage31/assets';assets.mkdir(parents=True,exist_ok=True)
    values=np.sort(np.asarray(summary,np.float64));y=np.arange(1,len(values)+1)/len(values)
    fig,ax=plt.subplots(figsize=(10,5.5),constrained_layout=True);ax.plot(values,y,lw=2.6,color='#1769aa',label=f'PC FP32 mean={values.mean():.4f}')
    ax.set(xlabel='SSIM vs measured GT',ylabel='Empirical CDF',title=f'{scene}: {len(values)} held-out test spectra — factorized PC FP32')
    ax.grid(alpha=.25);ax.legend();fig.savefig(assets/'pc_ssim_cdf.png',dpi=180,facecolor='white');plt.close(fig)
    fig,axes=plt.subplots(4,2,figsize=(10,18),constrained_layout=True)
    for row,label in enumerate(('P10','P50','P90','MAX')):
        sid=representatives[label];item=byid[sid];gt=np.asarray(Image.open(scene_root/item['gt_png']).convert('L'),np.float64)/255.;pc=rendered[sid]['prediction']
        limits=(min(float(gt.min()),float(pc.min())),max(float(gt.max()),float(pc.max())))
        axes[row,0].imshow(render_polar_rgb(gt,480,limits));axes[row,1].imshow(render_polar_rgb(pc,480,limits))
        axes[row,0].set_ylabel(f'{label} {sid}\nSSIM {rendered[sid]["metrics"]["ssim_data_range_1"]:.4f}',fontsize=11)
        for ax in axes[row]:ax.axis('off')
    axes[0,0].set_title('Measured GT',fontsize=16);axes[0,1].set_title('PC factorized FP32',fontsize=16)
    fig.suptitle(f'{scene} representative held-out spectra',fontsize=17);fig.savefig(assets/'representative_gt_pc_gallery.png',dpi=160,facecolor='white');plt.close(fig)

def main():
  audit={}
  for scene in SCENES:
    sr=PACKAGE/'scenes'/scene;spec=json.loads((sr/'scene.json').read_text());catp=sr/'data/stage31/catalog.json';allp=sr/'data/stage32/all_test_catalog.json'
    cat=json.loads(catp.read_text());allcat=json.loads(allp.read_text());byid={x['spectrum_id']:x for x in allcat['samples']}
    report=json.loads((ROOT/'metrics'/METRICS[scene]).read_text());rows=report['maps'];lookup={str(x['index']):float(x['ssim']) for x in rows}
    if set(lookup)!=set(byid):raise RuntimeError(scene+' metric IDs differ from all-test catalog')
    for item in allcat['samples']:item['pc_ssim_vs_gt']=lookup[item['spectrum_id']]
    ordered=sorted(lookup,key=lookup.get);n=len(ordered)
    reps={'P10':ordered[int(round(.10*(n-1)))],'P50':ordered[int(round(.50*(n-1)))],
          'P90':ordered[int(round(.90*(n-1)))],'MAX':ordered[-1]}
    candidate_ids=[]
    for item in cat.get('candidates',[]):
        sid=str(item['spectrum_id'])
        if sid in byid and sid not in candidate_ids:candidate_ids.append(sid)
    for sid in reps.values():
        if sid not in candidate_ids:candidate_ids.append(sid)
    rendered=render_selected(sr,spec,cat,candidate_ids)
    candidates=[]
    for index,sid in enumerate(candidate_ids):
        item=dict(byid[sid]);r=rendered[sid];item.update({'pc_fp32_npy':r['path'],'pc_fp32_sha256':r['sha256'],
          'pc_seconds_builder':r['seconds'],'pc_metrics':r['metrics'],'candidate_index':index,
          'official_ssim':lookup[sid],'split':'test'});candidates.append(item)
        byid[sid].update({'pc_fp32_npy':r['path'],'pc_fp32_sha256':r['sha256'],'pc_seconds_builder':r['seconds'],'pc_metrics':r['metrics']})
    ssim=np.asarray(list(lookup.values()),np.float64);summary={'mean':float(ssim.mean()),'median':float(np.median(ssim)),
      'minimum':float(ssim.min()),'maximum':float(ssim.max())}
    cat.update({'schema':'n2-stage37-factorized-scene-catalog-v1','scene_id':scene,'test_samples':n,
      'candidate_count':len(candidates),'test_ssim_summary':summary,'representatives':reps,'candidates':candidates})
    cat.setdefault('source_sha256',{})['checkpoint_sha256']=sha(sr/spec['checkpoint'])
    allcat['schema']='n2-stage37-factorized-all-test-catalog-v1';allcat['samples']=[byid[x['spectrum_id']] for x in allcat['samples']]
    atomic(catp,cat);atomic(allp,allcat);figures(sr,scene,ssim,reps,byid,rendered)
    audit[scene]={'samples':n,'factorized_pc_mean_ssim':summary['mean'],'candidate_maps_regenerated':len(candidates),
                  'representatives':reps,'checkpoint_sha256':sha(sr/spec['checkpoint'])}
  atomic(ROOT/'stage37_pc_asset_audit.json',audit);print(json.dumps(audit,indent=2),flush=True)
if __name__=='__main__':main()
