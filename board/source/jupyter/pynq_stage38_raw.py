#!/usr/bin/env python3
"""Stage38 raw-only universal bitstream driver for four runtime-loaded scenes."""
from __future__ import annotations
import base64, hashlib, io, json, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import numpy as np
from pynq import MMIO, Overlay, allocate

from pynq_load_weights import DMARecoveryRequired, RawDMA, _find_ip, read_status
from pynq_full_pl_ray import _free_buffer, Q15_ONE
from stage28_exact_common import (_draw_text, comparison_metrics,
                                  compose_clear_three_way, render_polar_rgb,
                                  save_rgb_png)
from stage31_dynamic_tx import quantize_tx
from stage34_packets import tx_words
from stage37_arm_reference import Stage37ARMReference
from stage37_runtime_loader import Stage37RuntimeLoader

ROWS,COLS,SAMPLES=90,360,64;RAYS=ROWS*COLS
SOURCE_ROWS=5;GROUP=4;ROWS_PER_PACKET=20;PACKETS=5
MAX_RAYS=ROWS_PER_PACKET*COLS;MAX_WORDS=1+(MAX_RAYS//2)*(32+SAMPLES)
DRIVER_BUILD_ID='stage38-raw-only-runtime-model-under1p2-v1'
FPGA_BUILD_ID='stage38-raw-only-universal-f95-v1'

def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def array_b64(value):
    b=io.BytesIO();np.savez_compressed(b,value=np.asarray(value,np.float32));return base64.b64encode(b.getvalue()).decode('ascii')

def decode_status(value):
    value=int(value);return {'raw':'0x%08x'%value,'error':bool(value&0x80000000),
      'idle':bool(value&0x40000000),'factor_busy':bool(value&0x20000000),
      'model_valid':bool(value&0x10000000),'protocol_error':bool(value&0x08000000),
      'command':(value>>25)&3,'pairs_remaining':value&0x3ffff}

def free_compare_frame(pc,fpga,width=1280,height=720):
    """Draw the free-Tx PC/FPGA comparison with the measured-mode label size.

    The 5x7 renderer is deliberately shared with ``compose_clear_three_way``.
    It works on the old Pillow build in the PYNQ image and keeps both polar
    plots circular on a 1280x720 framebuffer.
    """
    pc=np.asarray(pc,np.float64).reshape(ROWS,COLS)
    fpga=np.asarray(fpga,np.float64).reshape(ROWS,COLS)
    margin,gap,top=20,14,92
    size=min((int(width)-2*margin-gap)//2,int(height)-230)
    left=(int(width)-(2*size+gap))//2;starts=(left,left+size+gap)
    shared=(min(float(pc.min()),float(fpga.min())),
            max(float(pc.max()),float(fpga.max())))
    canvas=np.full((int(height),int(width),3),255,np.uint8)
    for x,value in zip(starts,(pc,fpga)):
        canvas[top:top+size,x:x+size]=render_polar_rgb(value,size,shared)
    scale=max(5,min(9,int(width)//160))
    for x,label in zip(starts,('PC','FPGA')):
        _draw_text(canvas,label,x+size//2,18,scale)
    _draw_text(canvas,'GT N/A',int(width)//2,int(height)-80,scale)
    return canvas

def verify_artifact(bit,manifest):
    bit=Path(bit);hwh=bit.with_suffix('.hwh');m=json.loads(Path(manifest).read_text())
    if m.get('schema')!='n2-stage38-artifacts-v1' or m.get('build_id')!=FPGA_BUILD_ID:raise RuntimeError('Stage38 artifact identity mismatch')
    if not m.get('timing_met') or float(m.get('frequency_mhz',0))!=95.:raise RuntimeError('Stage38 95 MHz timing proof missing')
    if sha(bit)!=m['bit_sha256'] or sha(hwh)!=m['hwh_sha256']:raise RuntimeError('Stage38 bit/hwh SHA-256 mismatch')
    if not m.get('single_bitstream_four_scenes'):raise RuntimeError('artifact is not certified as one universal bitstream')
    return m

class ConstantSource:
    def __init__(self,root):
        self.root=Path(root);self.manifest=json.loads((self.root/'manifest.json').read_text())
        if self.manifest.get('schema')!='n2-stage37-signal-constant-packets-v1':raise RuntimeError('constant manifest schema mismatch')
        self.entries=self.manifest['packets'];self.hash=sha(self.root/'manifest.json')
    def load(self,index,tx_q):
        e=self.entries[int(index)];p=self.root/e['file']
        if p.stat().st_size!=e['file_bytes'] or sha(p)!=e['sha256']:raise RuntimeError('constant packet hash mismatch: '+str(p))
        with np.load(p,allow_pickle=False) as z:v=np.asarray(z['value'],np.uint64).copy()
        w=tx_words(tx_q);v[:,8:16]=w;v[:,24:32]=w;return v,e
    def group(self,index,tx_q):
        values=[];entries=[]
        for i in range(index*GROUP,min((index+1)*GROUP,18)):
            v,e=self.load(i,tx_q);values.append(v);entries.append(e)
        return np.concatenate(values),{'first_row':entries[0]['first_row'],
          'row_count':sum(x['row_count'] for x in entries),'ray_count':sum(x['ray_count'] for x in entries)}

def pack_fast(constants,cache,nrays):
    constants=np.asarray(constants,np.uint64);cache=np.asarray(cache,np.uint16).reshape(nrays,SAMPLES)
    pairs=nrays//2;paired=cache.reshape(pairs,2,SAMPLES).astype(np.uint64)
    cw=paired[:,0]|(paired[:,1]<<np.uint64(16));rec=np.empty((pairs,96),np.uint64)
    rec[:,:32]=constants;rec[:,32:]=cw;out=np.empty(1+rec.size,np.uint64)
    out[0]=np.uint64((5<<60)|nrays);out[1:]=rec.reshape(-1);return out

class FastRunner:
    def __init__(self,overlay,restart=True):
        self.gpio=MMIO(_find_ip(overlay,'gpio')['phys_addr'],0x100);self.dma=RawDMA(_find_ip(overlay,'dma')['phys_addr'])
        self.tx=[allocate(shape=(MAX_WORDS,),dtype=np.uint64),allocate(shape=(MAX_WORDS,),dtype=np.uint64)]
        self.rx=allocate(shape=(MAX_RAYS,),dtype=np.uint64);self.recoveries=[]
        if restart:self.dma.restart()
    def close(self):
        for x in self.tx:_free_buffer(x)
        _free_buffer(self.rx);self.tx=[];self.rx=None
    def idle(self,t=2):
        st=time.monotonic()
        while time.monotonic()-st<t:
            value=int(read_status(self.gpio));d=decode_status(value)
            if d['error']:raise RuntimeError('Stage38 PL error: %r'%d)
            if d['idle'] and d['model_valid']:return value
            time.sleep(.0002)
        raise TimeoutError('Stage38 idle/model timeout: %r'%decode_status(read_status(self.gpio)))
    def prepare(self,slot,packed,nrays):
        packed=np.asarray(packed,np.uint64);self.tx[slot][:len(packed)]=packed;self.tx[slot].flush()
        return slot,len(packed),int(nrays)
    def run(self,prepared,timeout=5):
        slot,nwords,nrays=prepared;self.idle();self.dma.arm_recv(self.rx,nrays*8);start=time.perf_counter()
        self.dma.send_preflushed(self.tx[slot],nwords*8,tmo=timeout);self.dma.wait_recv(self.rx,tmo=timeout)
        elapsed=time.perf_counter()-start;self.idle()
        return (np.asarray(self.rx[:nrays],np.uint64)&np.uint64(0xffffffff)).astype(np.uint32),elapsed

class UniversalHardware:
    def __init__(self,package_root,device=None):
        self.root=Path(package_root);self.device=device;self.overlay=None;self.runner=None;self.scene=None;self.cache=None;self.scene_meta=None;self.model_load=None
        # The PYNQ base HDMI design and the Stage38 accelerator are complete
        # bitstreams, so they cannot be resident in PL at the same time.
        # Track which design actually owns PL; keeping only the old Overlay
        # Python object is insufficient because BaseOverlay("base.bit") has
        # already replaced its hardware underneath it.
        self.pl_owner='none'
        self.artifact=verify_artifact(self.root/'sim/n2_stage38_raw.bit',self.root/'sim/stage38_artifact_manifest.json')
    def _stop_hdmi_before_accelerator(self):
        if self.pl_owner=='base_hdmi':
            from pynq_full_exact_90x360 import _stop_saved_hdmi_session
            _stop_saved_hdmi_session()
            time.sleep(.02)
            self.pl_owner='none'
    def _program_accelerator(self):
        self._stop_hdmi_before_accelerator()
        self.overlay=Overlay(str(self.root/'sim/n2_stage38_raw.bit'),
                             download=True,device=self.device)
        self.pl_owner='stage38_accelerator'
        time.sleep(.05)
        return self.overlay
    def mark_hdmi_overlay_active(self):
        """Invalidate every accelerator handle after BaseOverlay is loaded.

        The HDMI frame is intentionally left running.  The next hardware
        request first stops that saved video session and then downloads the
        same universal Stage38 bitstream before touching accelerator DMA.
        """
        old=self.runner
        self.runner=None;self.overlay=None;self.cache=None;self.scene=None
        self.scene_meta=None;self.model_load=None;self.pl_owner='base_hdmi'
        if old is not None:
            try:old.close()
            except Exception:pass
    def close(self):
        self._stop_hdmi_before_accelerator()
        if self.runner is not None:
            try:self.runner.close()
            except Exception:pass
        self.runner=None;self.overlay=None;self.cache=None;self.scene=None;self.pl_owner='none'
    def recover_overlay(self):
        """Reset PL before releasing a buffer that a wedged DMA may still own."""
        old=self.runner;self.runner=None;self.cache=None;self.scene=None
        try:
            # Be explicit about downloading even when the path equals the
            # currently programmed bitstream.  A failed long DMA packet can
            # leave both the wrapper and AXI DMA latched; constructing only
            # metadata is not a recovery in that state.
            self._program_accelerator()
        except Exception:
            self.overlay=None;self.pl_owner='none'
            raise
        finally:
            if old is not None:
                try:old.close()
                except Exception:pass
    def configure(self,scene,force=False):
        scene=str(scene);sr=self.root/'scenes'/scene
        if (not force and self.pl_owner=='stage38_accelerator' and
                self.scene==scene and self.runner is not None):
            return {'scene':scene,'reused':True,**self.model_load}
        if self.runner is not None:
            self.runner.close();self.runner=None
        self.scene=None;self.cache=None;self.scene_meta=None;self.model_load=None
        recoveries=[]
        for attempt in range(2):
            try:
                if self.overlay is None or self.pl_owner!='stage38_accelerator':
                    self._program_accelerator()
                loaded=Stage37RuntimeLoader(self.overlay).load(sr/'model/stage37_runtime_model.npz')
                # RuntimeLoader has already reset both direct-mode channels
                # and completed ECHO/COMMIT.  Starting FastRunner without a
                # second DMA reset avoids opening another S2MM lifecycle.
                self.runner=FastRunner(self.overlay,restart=False)
                assets=json.loads((sr/'model/runtime_assets.json').read_text())
                cache_path=sr/'model/attenuation_u16.npy'
                if sha(cache_path)!=assets['cache_sha256']:raise RuntimeError('scene attenuation cache SHA-256 mismatch')
                cache=np.load(cache_path,mmap_mode='r',allow_pickle=False)
                if cache.shape!=(ROWS,COLS,SAMPLES) or cache.dtype!=np.uint16:raise RuntimeError('scene cache shape/type mismatch')
                self.cache=cache;self.scene_meta=json.loads((sr/'scene.json').read_text())
                self.selftest_cache_sentinel(sr)
                self.scene=scene;self.model_load=loaded
                self.model_load['configuration_overlay_reload_count']=len(recoveries)
                self.model_load['configuration_recoveries']=recoveries
                return {'scene':scene,'reused':False,**self.model_load}
            except (DMARecoveryRequired,TimeoutError,RuntimeError) as exc:
                recoveries.append({'attempt':attempt+1,
                                   'exception_type':type(exc).__name__,
                                   'error':str(exc),
                                   'action':'reload_same_universal_overlay'})
                self.scene=None;self.cache=None;self.scene_meta=None;self.model_load=None
                if attempt:raise
                self.recover_overlay()
    def selftest_cache_sentinel(self,sr):
        p=sr/'model/cache_sentinel_512.npz'
        with np.load(p,allow_pickle=False) as z:stream=np.asarray(z['stream_u64'],np.uint64);expected=np.asarray(z['expected_u16'],np.uint16)
        tx=allocate(shape=stream.shape,dtype=np.uint64);rx=allocate(shape=(expected.size//2,),dtype=np.uint64)
        try:
            tx[:]=stream;tx.flush();self.runner.idle();self.runner.dma.arm_recv(rx,rx.nbytes)
            self.runner.dma.send_preflushed(tx,tx.nbytes,tmo=20);self.runner.dma.wait_recv(rx,tmo=20);self.runner.idle()
            words=np.asarray(rx,np.uint64).reshape(len(expected)//2,SAMPLES);actual=np.empty_like(expected)
            actual[0::2]=(words&0xffff).astype(np.uint16);actual[1::2]=((words>>16)&0xffff).astype(np.uint16)
            if not np.array_equal(actual,expected):raise AssertionError('Stage38 512-ray cache sentinel is not bit exact')
        finally:_free_buffer(tx);_free_buffer(rx)
    def _run_map_once(self,scene,tx,pc=None,gt=None,progress=None):
        setup=self.configure(scene);sr=self.root/'scenes'/scene
        manifest=json.loads((sr/'model/quant_manifest.json').read_text());tx=np.asarray(tx,np.float32).reshape(3)
        txq=quantize_tx(tx,np.asarray(manifest['input_scales']))
        source=ConstantSource(sr/'model/constant_packets');cache=np.asarray(self.cache).reshape(RAYS,SAMPLES)
        result=np.empty((ROWS,COLS),np.uint32);stream_sum=0.;packet_times=[];start=time.perf_counter()
        with ThreadPoolExecutor(max_workers=1) as pool:
            def prep(slot,index):
                c,e=source.group(index,txq);first=e['first_row']*COLS;return self.runner.prepare(slot,pack_fast(c,cache[first:first+e['ray_count']],e['ray_count']),e['ray_count']),e
            slot=0;future=pool.submit(prep,slot,0)
            for index in range(PACKETS):
                packet,e=future.result();next_slot=1-slot
                future=pool.submit(prep,next_slot,index+1) if index+1<PACKETS else None
                values,seconds=self.runner.run(packet);row=e['first_row'];count=e['row_count'];result[row:row+count]=values.reshape(count,COLS)
                stream_sum+=seconds;packet_times.append(seconds)
                if progress:progress(row+count,ROWS,time.perf_counter()-start)
                slot=next_slot
        elapsed=time.perf_counter()-start;raw=result.astype(np.float64)/Q15_ONE;fpga=raw
        metrics={}
        if pc is not None:metrics.update({'pc_vs_fpga_raw':comparison_metrics(pc,raw)})
        if gt is not None:metrics.update({'gt_vs_fpga_raw':comparison_metrics(gt,raw)})
        if pc is not None and gt is not None:metrics['gt_vs_pc']=comparison_metrics(gt,pc)
        report={'schema':'n2-stage38-universal-map-v1-raw-only','scene_id':scene,'driver_build_id':DRIVER_BUILD_ID,'fpga_build_id':FPGA_BUILD_ID,
          'tx':tx.tolist(),'resolution':[ROWS,COLS],'fpga_hardware_rays':RAYS,'samples_per_ray':SAMPLES,
          'mlp_queries':RAYS*SAMPLES,'angular_interpolation_used':False,'display_interpolation_used':False,
          'every_fpga_pixel_is_a_complete_hardware_ray':True,'same_bitstream_for_all_scenes':True,
          'scene_switch_changes_bitstream':False,'runtime_model_load':setup,'fixed_rx_intermediate_cache_used':True,
          'cache_tx_specific':False,'cached_final_output_used':False,'metrics':metrics,
          'timing':{'full_map_cached_pipeline_seconds':elapsed,'fpga_stream_seconds_sum':stream_sum,
             'full_map_row_pipeline_seconds':elapsed,'fast_packet_seconds_max':max(packet_times),
             'under_1p2_seconds':elapsed<1.2},
          'provenance':{'bit_sha256':self.artifact['bit_sha256'],'runtime_model_sha256':sha(sr/'model/stage37_runtime_model.npz'),
             'constant_manifest_sha256':source.hash,'cache_sha256':sha(sr/'model/attenuation_u16.npy'),
             'raw_q15_sha256':hashlib.sha256(result.tobytes()).hexdigest()},
          'output_mode':'raw_int8_q15','output_conversion':'raw_q15 / 32768'}
        return report,fpga,result
    def run_map(self,scene,tx,pc=None,gt=None,progress=None):
        """Run one complete map and recover once by reprogramming the same bitstream.

        A wedged AXI DMA cannot always be repaired with its software reset bit.
        Reloading the universal Overlay resets DMA and PL state, then reloads
        only the selected scene's runtime model.  The retry recomputes the
        complete map, so a partial result is never returned as valid evidence.
        """
        recoveries=[]
        for attempt in range(2):
            try:
                report,fpga,result=self._run_map_once(scene,tx,pc,gt,progress)
                report['dma_pl_recovery']={'automatic_retry_count':len(recoveries),
                                           'events':recoveries}
                return report,fpga,result
            except (DMARecoveryRequired,TimeoutError,RuntimeError) as exc:
                recoveries.append({'attempt':attempt+1,'exception_type':type(exc).__name__,
                                   'error':str(exc),'action':'reload_same_universal_overlay'})
                self.recover_overlay()
                if attempt:raise
    def rerun_indices(self,scene,tx,indices,expected_full):
        self.configure(scene);sr=self.root/'scenes'/scene
        manifest=json.loads((sr/'model/quant_manifest.json').read_text());tx=np.asarray(tx,np.float32).reshape(3)
        txq=quantize_tx(tx,np.asarray(manifest['input_scales']));source=ConstantSource(sr/'model/constant_packets')
        cache=np.asarray(self.cache).reshape(RAYS,SAMPLES);indices=np.asarray(indices,np.int64).reshape(-1)
        if np.any(indices<0) or np.any(indices>=RAYS):raise ValueError('challenge ray outside 0..32399')
        actual=np.empty(len(indices),np.uint32);assigned=np.zeros(len(indices),bool);stream=0.;rays=0
        for packet_index in np.unique((indices//COLS)//ROWS_PER_PACKET):
            constants,e=source.group(int(packet_index),txq);first=e['first_row']*COLS;nrays=e['ray_count']
            packed=pack_fast(constants,cache[first:first+nrays],nrays)
            values,seconds=self.runner.run(self.runner.prepare(0,packed,nrays));stream+=seconds;rays+=nrays
            selected=np.flatnonzero((indices>=first)&(indices<first+nrays));actual[selected]=values[indices[selected]-first];assigned[selected]=True
        if not assigned.all():raise AssertionError('challenge selection incomplete')
        expected=np.asarray(expected_full,np.uint32).reshape(-1)[indices]
        return {'indices':indices.tolist(),'expected_q15':expected.tolist(),'rerun_q15':actual.tolist(),
          'bit_exact':bool(np.array_equal(actual,expected)),'production_packets_rerun':True,
          'surrounding_hardware_rays':int(rays),'fpga_stream_seconds':float(stream)}
    def arm_indices(self,scene,tx,indices):
        """Independently recompute selected rays on the Zynq ARM/NumPy path."""
        self.configure(scene);sr=self.root/'scenes'/scene
        manifest=json.loads((sr/'model/quant_manifest.json').read_text());tx=np.asarray(tx,np.float32).reshape(3)
        txq=quantize_tx(tx,np.asarray(manifest['input_scales']))
        source=ConstantSource(sr/'model/constant_packets')
        reference=Stage37ARMReference(sr/'model/stage37_runtime_model.npz')
        return reference.run_indices(source,self.cache,txq,np.asarray(indices,np.int64))
