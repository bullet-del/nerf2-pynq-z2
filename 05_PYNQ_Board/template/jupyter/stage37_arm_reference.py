#!/usr/bin/env python3
"""Independent NumPy/ARM reference for selected Stage37 production rays.

This module consumes the runtime model file rather than any FPGA result.  It
repeats the sparse INT8 signal MLP and the 64-sample Q15 renderer on the Zynq
ARM for judge-selected rays, which makes the challenge an independent
software-versus-hardware check while keeping the normal full-map path fast.
"""
from __future__ import annotations

import math
import time
from pathlib import Path

import numpy as np


def _descriptor(value):
    value=int(value)
    return {'layer':value&15,'group':(value>>4)&3,'k':(value>>6)&511,
            'weight_base':(value>>15)&4095,'index_base':(value>>27)&4095,
            'bias_address':(value>>39)&31,'outputs_valid':(value>>44)&255,
            'last_group':bool((value>>52)&1)}


class Stage37ARMReference:
    def __init__(self,runtime_model):
        with np.load(Path(runtime_model),allow_pickle=False) as z:
            self.weights=np.asarray(z['weights_u8'],np.uint8).view(np.int8).copy()
            self.indices=np.asarray(z['indices_u16'],np.uint16).copy()
            self.biases=np.asarray(z['biases_i16'],np.int16).copy()
            self.descriptors=[_descriptor(v) for v in z['descriptors_u64']]
            self.shifts=np.asarray(z['shifts_u8'],np.uint8).copy()
            self.att_factor=np.asarray(z['att_factor_u16'],np.uint16).copy()
            self.att_pcos=np.asarray(z['att_phase_cos_i16'],np.int16).copy()
            self.att_psin=np.asarray(z['att_phase_sin_i16'],np.int16).copy()
            self.sig_amp=np.asarray(z['sig_amp_u16'],np.uint16).copy()
            self.sig_pcos=np.asarray(z['sig_phase_cos_i16'],np.int16).copy()
            self.sig_psin=np.asarray(z['sig_phase_sin_i16'],np.int16).copy()
            self.path_loss=np.asarray(z['path_loss_u16'],np.uint16).copy()

    def _layer(self,layer,x,relu):
        x=np.asarray(x,np.int8).reshape(-1);parts=[]
        for d in self.descriptors:
            if d['layer']!=int(layer):continue
            k=d['k'];idx=self.indices[d['index_base']:d['index_base']+k].astype(np.int64)
            weights=self.weights[d['weight_base']:d['weight_base']+k].astype(np.int64)
            accum=x[idx].astype(np.int64)@weights
            n=d['outputs_valid'];value=np.right_shift(accum,int(self.shifts[layer]))
            value=value+self.biases[d['bias_address']].astype(np.int64)
            if relu:value=np.maximum(value,0)
            parts.append(np.clip(value[:n],-128,127).astype(np.int8))
        if not parts:raise RuntimeError('runtime model has no descriptor for layer %d'%layer)
        return np.concatenate(parts)

    @staticmethod
    def _constant_input(record,lane):
        record=np.asarray(record,np.uint64).reshape(32);base=0 if int(lane)==0 else 16
        view=np.ascontiguousarray(record[base:base+8]).view(np.int8)
        tx=np.ascontiguousarray(record[base+8:base+16]).view(np.int8)
        # Logical layer-10 input is 256 + 63 + 63 = 382 values.  The stream
        # pads each 63-byte encoding to one 512-bit word, but that padding is
        # not part of the neural-network index space.
        result=np.zeros(382,np.int8);result[256:319]=view[:63];result[319:382]=tx[:63]
        return result

    def signal(self,record,lane):
        value=self._layer(10,self._constant_input(record,lane),True)
        value=self._layer(11,value,True)
        return self._layer(12,value,False)[:2]

    def renderer(self,signal,attenuation_u16):
        signal=np.asarray(signal,np.int8).reshape(2)
        attenuation=np.asarray(attenuation_u16,np.uint16).reshape(64)
        sig_amp=int(self.sig_amp[(int(signal[0])&255)^128])
        sig_pc=int(self.sig_pcos[(int(signal[1])&255)^128])
        sig_ps=int(self.sig_psin[(int(signal[1])&255)^128])
        factor=32768;phase_re=17705;phase_im=27573;acc_re=0;acc_im=0
        for sample,packed in enumerate(attenuation):
            packed=int(packed);ia=(packed&255)^128;ip=((packed>>8)&255)^128
            dr=((sig_pc*phase_re)>>15)-((sig_ps*phase_im)>>15)
            di=((sig_pc*phase_im)>>15)+((sig_ps*phase_re)>>15)
            next_re=((phase_re*int(self.att_pcos[ip]))>>15)-((phase_im*int(self.att_psin[ip]))>>15)
            next_im=((phase_re*int(self.att_psin[ip]))>>15)+((phase_im*int(self.att_pcos[ip]))>>15)
            amp=(sig_amp*factor)>>15; amp=(amp*int(self.path_loss[sample]))>>15
            acc_re+=(amp*dr)>>15;acc_im+=(amp*di)>>15
            factor=(factor*int(self.att_factor[ia]))>>15
            phase_re,phase_im=next_re,next_im
        return np.uint32(math.isqrt(acc_re*acc_re+acc_im*acc_im))

    def run_indices(self,constant_source,cache,tx_q,indices):
        started=time.perf_counter();indices=np.asarray(indices,np.int64).reshape(-1)
        cache=np.asarray(cache).reshape(32400,64);result=np.empty(len(indices),np.uint32)
        loaded={}
        for out_index,ray in enumerate(indices):
            packet=int(ray//1800)
            if packet not in loaded:loaded[packet]=constant_source.load(packet,tx_q)[0]
            first=packet*1800;local=int(ray-first);record=loaded[packet][local//2]
            result[out_index]=self.renderer(self.signal(record,local&1),cache[ray])
        return result,time.perf_counter()-started
