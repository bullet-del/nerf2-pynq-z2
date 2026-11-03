"""Bit-oriented Q15 reference for the Stage25 streaming ray accumulator."""
from __future__ import annotations
import math
import numpy as np

Q=15; ONE=1<<Q

def q15(x, signed=True):
    y=np.rint(np.asarray(x)*ONE).astype(np.int64)
    return np.clip(y,-32768 if signed else 0,32767 if signed else 32768)

def make_luts(final_scales, near=0.0, far=5.0, samples=64):
    raw=np.arange(-128,128,dtype=np.float64)
    aa=np.abs(np.where(raw*final_scales[0]>=0,raw*final_scales[0],.01*raw*final_scales[0]))
    sa=np.abs(np.where(raw*final_scales[2]>=0,raw*final_scales[2],.01*raw*final_scales[2]))
    delta=(far-near)/(samples-1)
    ap=2*np.pi/(1+np.exp(-raw*final_scales[1]))
    sp=2*np.pi/(1+np.exp(-raw*final_scales[3]))
    t=np.linspace(near,far,samples)
    path=np.r_[t[1:],1e10]
    return {
      'att_factor':q15(np.exp(-aa*delta)+1e-10,False),
      'att_phase_cos':q15(np.cos(ap*delta)), 'att_phase_sin':q15(np.sin(ap*delta)),
      'sig_amp':q15(sa,False), 'sig_phase_cos':q15(np.cos(sp)),
      'sig_phase_sin':q15(np.sin(sp)), 'path_loss':q15(.025/path,False),
      'phase_init_cos':int(q15(math.cos(1.0))), 'phase_init_sin':int(q15(math.sin(1.0))),
      'q_fraction_bits':Q,'samples':samples,'near':near,'far':far,
    }

def mulq(a,b): return (int(a)*int(b))>>Q
def cmul(ar,ai,br,bi): return mulq(ar,br)-mulq(ai,bi), mulq(ar,bi)+mulq(ai,br)

def render_raw_int8(raw,lut):
    """Return Q15 magnitudes and complex accumulators for raw [R,64,4]."""
    raw=np.asarray(raw,np.int8)
    if raw.ndim!=3 or raw.shape[1:]!=(lut['samples'],4): raise ValueError(raw.shape)
    mags=[]; complexes=[]
    for ray in raw:
        att=ONE; pr=lut['phase_init_cos']; pi=lut['phase_init_sin']; re=im=0
        for i,v in enumerate(ray):
            idx=[int(x)+128 for x in v]
            dr,di=cmul(lut['sig_phase_cos'][idx[3]],lut['sig_phase_sin'][idx[3]],pr,pi)
            amp=mulq(lut['sig_amp'][idx[2]],att); amp=mulq(amp,lut['path_loss'][i])
            re+=mulq(amp,dr); im+=mulq(amp,di)
            att=mulq(att,lut['att_factor'][idx[0]])
            pr,pi=cmul(pr,pi,lut['att_phase_cos'][idx[1]],lut['att_phase_sin'][idx[1]])
        mags.append(math.isqrt(re*re+im*im)); complexes.append((re,im))
    return np.asarray(mags,np.int64),np.asarray(complexes,np.int64)

def write_memh(lut,out):
    from pathlib import Path
    out=Path(out);out.mkdir(parents=True,exist_ok=True)
    arrays=['att_factor','att_phase_cos','att_phase_sin','sig_amp','sig_phase_cos','sig_phase_sin','path_loss']
    for name in arrays:
        a=np.asarray(lut[name],np.int64)
        (out/f'{name}.memh').write_text('\n'.join(f'{int(x)&0xffff:04x}' for x in a)+'\n',encoding='ascii')

