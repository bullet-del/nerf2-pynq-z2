#!/usr/bin/env python3
"""Pure NumPy packer for the Stage34 exact dual-ray AXI protocol."""
from __future__ import annotations
import numpy as np

ROWS,COLS,SAMPLES,KINDS,FEATURES=90,360,64,3,63
PADDED=64
OP_RUN=4
WORDS_PER_PAIR=32+SAMPLES*16

def run_header(ray_count):
    ray_count=int(ray_count)
    if ray_count<2 or ray_count>=(1<<24) or ray_count%2:
        raise ValueError("Stage34 ray_count must be positive, even, and below 2^24")
    return np.uint64((OP_RUN<<60)|ray_count)

def pack_encoded_rays(encoded_rays):
    z=np.asarray(encoded_rays,dtype=np.int8)
    if z.ndim!=4 or z.shape[1:]!=(SAMPLES,KINDS,FEATURES) or z.shape[0]%2:
        raise ValueError("encoded Stage34 rays must have shape (even N,64,3,63), got %r"%(z.shape,))
    # Signal inputs are mathematically constant along a ray.  Refuse a shard
    # that violates that invariant instead of silently changing inference.
    if np.any(z[:,:,1,:]!=z[:,0:1,1,:]) or np.any(z[:,:,2,:]!=z[:,0:1,2,:]):
        raise AssertionError("view/Tx encoding changed along a ray")
    n=z.shape[0]
    pad=np.zeros((n,SAMPLES,KINDS,PADDED),dtype=np.int8)
    pad[:,:,:,:FEATURES]=z
    words=np.ascontiguousarray(pad).view("<u8").reshape(n,SAMPLES,KINDS,8)
    pair_words=np.empty((n//2,WORDS_PER_PAIR),dtype=np.uint64)
    for p in range(n//2):
        r0,r1=2*p,2*p+1
        constants=np.concatenate((words[r0,0,1].ravel(),words[r0,0,2].ravel(),
                                  words[r1,0,1].ravel(),words[r1,0,2].ravel()))
        points=np.stack((words[r0,:,0,:],words[r1,:,0,:]),axis=1).reshape(-1)
        pair_words[p]=np.concatenate((constants,points))
    result=np.empty(1+pair_words.size,dtype=np.uint64)
    result[0]=run_header(n);result[1:]=pair_words.reshape(-1)
    if result.nbytes>=(1<<23):raise AssertionError("one row exceeds AXI DMA length field")
    return result

def pack_encoded_row(encoded_row):
    z=np.asarray(encoded_row,dtype=np.int8)
    if z.shape!=(COLS,SAMPLES,KINDS,FEATURES):
        raise ValueError("encoded Stage34 row has wrong shape %r"%(z.shape,))
    return pack_encoded_rays(z)

def selfcheck(encoded_row):
    z=np.asarray(encoded_row,dtype=np.int8)
    words=pack_encoded_row(z)
    expected=1+(COLS//2)*WORDS_PER_PAIR
    return {"rays":COLS,"pairs":COLS//2,"stream_words":int(words.size),
            "stream_bytes":int(words.nbytes),"expected_words":expected,
            "view_tx_constant":True,"bit_exact":int(words.size)==expected}
