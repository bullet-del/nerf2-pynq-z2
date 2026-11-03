#!/usr/bin/env python3
"""Runtime scene-model loader for the single Stage37 PYNQ-Z2 bitstream."""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import numpy as np
from pynq import MMIO, allocate

from pynq_load_weights import RawDMA, _find_ip, read_status
from pynq_full_pl_ray import _free_buffer

OP_LOAD=8; OP_COMMIT=9; OP_ECHO=15
# One OP_LOAD header followed by complete (address, data) records.  Keeping
# every direct-mode DMA transaction at 8 KiB avoids the board-dependent long
# MM2S transaction failure seen when switching from the smaller s23 model to
# the larger Office/Meetingroom models.  The first chunk is one header plus
# 511 records; every later chunk is 512 complete records.  AXI TLAST is not a
# parser delimiter in the Stage37 RTL, so this is exactly the same logical
# packet without ever splitting a record.
FIRST_LOAD_WORDS=1023
NEXT_LOAD_WORDS=1024
LOADER_BUILD_ID="stage37-record-aligned-chunked-loader-v2"
TARGET_WEIGHT=0; TARGET_INDEX=1; TARGET_BIAS=2; TARGET_DESCRIPTOR=3; TARGET_SHIFT=4
LUT_KEYS=((5,"att_factor_u16"),(6,"att_phase_cos_i16"),(7,"att_phase_sin_i16"),
          (8,"sig_amp_u16"),(9,"sig_phase_cos_i16"),(10,"sig_phase_sin_i16"),
          (11,"path_loss_u16"))


def _sha256(path):
    h=hashlib.sha256(); h.update(Path(path).read_bytes()); return h.hexdigest()


def _address(target, address, segment=0):
    return np.uint64((int(target)<<60)|(int(address)<<48)|(int(segment)<<42))


def build_load_packet(runtime_model):
    with np.load(runtime_model,allow_pickle=False) as z:
        weights=np.ascontiguousarray(z["weights_u8"],dtype=np.uint8)
        indices=np.asarray(z["indices_u16"],dtype=np.uint16)
        biases=np.ascontiguousarray(z["biases_i16"],dtype="<i2")
        descriptors=np.asarray(z["descriptors_u64"],dtype=np.uint64)
        shifts=np.asarray(z["shifts_u8"],dtype=np.uint8)
        model_id=int(np.asarray(z["model_id"]).item())
        records=[]
        weight_words=weights.view("<u8").reshape(len(weights),16)
        for address,row in enumerate(weight_words):
            for segment,value in enumerate(row):
                records.extend((_address(TARGET_WEIGHT,address,segment),np.uint64(value)))
        for address,value in enumerate(indices):
            records.extend((_address(TARGET_INDEX,address),np.uint64(value)))
        bias_words=biases.view("<u8").reshape(len(biases),32)
        for address,row in enumerate(bias_words):
            for segment,value in enumerate(row):
                records.extend((_address(TARGET_BIAS,address,segment),np.uint64(value)))
        for address,value in enumerate(descriptors):
            records.extend((_address(TARGET_DESCRIPTOR,address),np.uint64(value)))
        for address,value in enumerate(shifts):
            records.extend((_address(TARGET_SHIFT,address),np.uint64(value)))
        for target,key in LUT_KEYS:
            values=np.ascontiguousarray(z[key]).view(np.uint16)
            for address,value in enumerate(values):
                records.extend((_address(target,address),np.uint64(value)))
    count=len(records)//2
    if not 0<count<(1<<28): raise ValueError("invalid Stage37 load record count")
    packet=np.empty(1+len(records),dtype=np.uint64)
    packet[0]=np.uint64((OP_LOAD<<60)|(count<<32)|model_id)
    packet[1:]=records
    return packet,model_id,count


class Stage37RuntimeLoader:
    def __init__(self,overlay):
        self.gpio=MMIO(_find_ip(overlay,"gpio")["phys_addr"],0x100)
        self.dma=RawDMA(_find_ip(overlay,"dma")["phys_addr"])

    def _diagnostic(self,status=None):
        if status is None: status=int(read_status(self.gpio))
        status=int(status)
        return {"status":"0x%08x"%status,
                "aggregate_error":bool(status&0x80000000),
                "idle":bool(status&0x40000000),
                "factor_busy":bool(status&0x20000000),
                "model_valid":bool(status&0x10000000),
                "protocol_error":bool(status&0x08000000),
                "command":(status>>25)&3,
                "pairs_remaining":status&0x3ffff,
                "dma":self.dma.snapshot()}

    def _idle(self,timeout=5.0,require_model=False,phase="idle"):
        start=time.monotonic()
        while time.monotonic()-start<timeout:
            status=int(read_status(self.gpio))
            if status&0x80000000:
                raise RuntimeError("Stage37 PL error during %s: %r"%
                                   (phase,self._diagnostic(status)))
            if status&0x40000000:
                if require_model and not(status&0x10000000):
                    raise RuntimeError("Stage37 model commit was not accepted during %s: %r"%
                                       (phase,self._diagnostic(status)))
                return status
            time.sleep(.0002)
        raise TimeoutError("Stage37 PL idle timeout during %s: %r"%
                           (phase,self._diagnostic()))

    def reset_transport(self):
        # Hold the PL parser in reset while the complete AXI DMA is returned
        # to direct-mode Run.  Deassertion is followed by an explicit status
        # check; a stale error can therefore never leak into a new scene.
        self.gpio.write(0,1); time.sleep(.01)
        try:self.dma.restart()
        finally:
            self.gpio.write(0,0); time.sleep(.01)
        status=self._idle(phase="post_reset")
        if status!=0x40000000:
            raise RuntimeError("Stage37 reset did not clear all model/parser state: %r"%
                               self._diagnostic(status))
        return status

    def _echo_barrier(self,control,rx,model_id,timeout):
        token=((OP_ECHO<<60)|0x037000000000000|(int(model_id)&0xffffffff))
        control[0]=np.uint64(token); control.flush()
        self.dma.arm_recv(rx,8)
        self.dma.send_preflushed(control,8,tmo=timeout)
        self.dma.wait_recv(rx,tmo=timeout)
        actual=int(rx[0])
        if actual!=token:
            raise RuntimeError("Stage37 pre-load ECHO mismatch: expected=%#018x actual=%#018x diagnostic=%r"%
                               (token,actual,self._diagnostic()))
        return actual

    def _send_model(self,tx,nwords,timeout):
        nwords=int(nwords); offset=0; chunks=0
        while offset<nwords:
            if offset==0:
                count=min(FIRST_LOAD_WORDS,nwords)
                if count!=nwords and (count-1)%2:
                    raise AssertionError("Stage37 first load chunk split a model record")
            else:
                count=min(NEXT_LOAD_WORDS,nwords-offset)
                if count%2:
                    raise AssertionError("Stage37 continuation chunk split a model record")
            self.dma.send_preflushed(tx,count*8,tmo=timeout,offset=offset*8)
            offset+=count;chunks+=1
            status=int(read_status(self.gpio))
            if status&0x80000000:
                raise RuntimeError("Stage37 model load failed after chunk %d/%d words: %r"%
                                   (chunks,offset,self._diagnostic(status)))
            # Only the final complete record may return the parser to IDLE.
            if offset<nwords and status&0x40000000:
                raise RuntimeError("Stage37 model parser became idle before the packet ended "
                                   "after chunk %d/%d words: %r"%
                                   (chunks,offset,self._diagnostic(status)))
        return chunks

    def load(self,runtime_model,manifest=None,timeout=10.0):
        runtime_model=Path(runtime_model)
        if manifest is None: manifest=runtime_model.with_suffix(".json")
        meta=json.loads(Path(manifest).read_text(encoding="utf-8"))
        if meta.get("schema")!="n2-stage37-runtime-model-v1":
            raise RuntimeError("not a Stage37 runtime model manifest")
        if meta.get("runtime_model_sha256")!=_sha256(runtime_model):
            raise RuntimeError("Stage37 runtime model SHA-256 mismatch")
        packet,model_id,records=build_load_packet(runtime_model)
        tx=rx=control=None
        started=time.perf_counter()
        status=None;chunks=0;attempts=0;recoveries=[]
        try:
            tx=allocate(shape=packet.shape,dtype=np.uint64); tx[:]=packet; tx.flush()
            control=allocate(shape=(1,),dtype=np.uint64)
            rx=allocate(shape=(1,),dtype=np.uint64)
            for attempt in range(2):
                attempts=attempt+1
                try:
                    self.reset_transport()
                    self._echo_barrier(control,rx,model_id,timeout)
                    chunks=self._send_model(tx,len(packet),timeout)
                    self._idle(timeout,phase="model_load_complete")
                    control[0]=np.uint64((OP_COMMIT<<60)|model_id); control.flush()
                    self.dma.arm_recv(rx,8)
                    self.dma.send_preflushed(control,8,tmo=timeout)
                    self.dma.wait_recv(rx,tmo=timeout)
                    ack=int(rx[0])
                    if (ack>>60)!=OP_COMMIT or (ack&0xffffffff)!=model_id or not((ack>>32)&1):
                        raise RuntimeError("Stage37 model commit ACK failed: %#018x diagnostic=%r"%
                                           (ack,self._diagnostic()))
                    status=self._idle(timeout,require_model=True,phase="model_commit")
                    break
                except Exception as exc:
                    recoveries.append({"attempt":attempt+1,
                                       "exception_type":type(exc).__name__,
                                       "error":str(exc),
                                       "action":"soft_reset_and_retry" if attempt==0 else "request_overlay_reload"})
                    if attempt:raise
        finally:
            for value in (tx,control,rx):
                if value is not None: _free_buffer(value)
        return {"schema":"n2-stage37-runtime-load-v1","scene":meta["scene"],
                "model_id_crc32":"%08x"%model_id,"records":records,
                "load_seconds":time.perf_counter()-started,"status":"0x%08x"%status,
                "loader_build_id":LOADER_BUILD_ID,
                "record_aligned_dma_chunks":chunks,"attempts":attempts,
                "recoveries":recoveries,"same_bitstream_for_all_scenes":True}
