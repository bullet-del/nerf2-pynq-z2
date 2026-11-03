#!/usr/bin/env python3
# =============================================================================
# n2_batch.py —— Stage 10 · 批处理协议打包器（单一事实源，PC/板端共用）
# -----------------------------------------------------------------------------
# 批模式（NB 点/批，层主序）下与 Stage 5 协议的唯一差异是 enc 块：
#   Stage5(NB=1)：每 kind 一块，nwords=2   （word 地址 = w）
#   Stage10(NB) ：每 kind 一块，nwords=NB*2（word 地址 = bp*2+w → enc_m 的
#                 字节槽 bp*64+…，见 layer_scheduler kind2/3/4 写入逻辑）
# 层块（W/B/Q）与 START 与 Stage 5 完全一致（每批只发一轮）。
# 结果：S_DONE 连续 NB 拍 → S2MM 一次收 NB*8 字节，第 i 拍低 32bit = 点 i。
# =============================================================================
import os
import sys

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "stage5"))
from n2_stream import (P, OP_WLOAD, OP_START, OP_ECHO, u64, wload_hdr,   # noqa
                       word512_beats, parse_tv3, layer_beats, start_beat,
                       decode_stream)

ENC_NWE = (63 + P - 1) // P            # 2 word/点/kind（P=32）


def batch_enc_beats(enc_batch):
    """enc_batch: (nb,3,63) int8 → 3 个 kind 块，每块 nb*ENC_NWE word。
    word 地址自增：bp*ENC_NWE+w → RTL enc_m 字节槽 = addr*P+j（bp*64 对齐）"""
    nb = enc_batch.shape[0]
    out = []
    for kind in range(3):
        out.append(wload_hdr(kind + 2, 0, 0, nb * ENC_NWE, False))
        for bpi in range(nb):
            eb = enc_batch[bpi, kind].astype(np.int8).tobytes()
            for w in range(ENC_NWE):
                seg = eb[w * P:(w + 1) * P]
                seg = seg + bytes(P - len(seg))
                out += word512_beats(seg + bytes(64 - P))
    return out


def batch_result_decode(rx_words, nb):
    """S2MM 收到的 nb 个 64bit 拍 → (nb,4) int8（拍 i 低 32bit=点 i 结果）"""
    out = np.zeros((nb, 4), dtype=np.int8)
    for i in range(nb):
        r = int(rx_words[i]) & 0xFFFFFFFF
        out[i] = np.frombuffer(r.to_bytes(4, "little"), dtype=np.int8)
    return out


def selfcheck_batch(nb=8):
    """打包→decode_stream 回放：enc 块字节槽应等于 bp*64+w*P 位置的源数据"""
    rng = np.random.default_rng(1)
    enc = rng.integers(-127, 128, (nb, 3, 63), dtype=np.int64).astype(np.int8)
    ws, ds, st = decode_stream(batch_enc_beats(enc))
    assert st == 0 and ds == []
    n = 0
    for (kind, bank, addr, data) in ws:
        bpi, w = addr // ENC_NWE, addr % ENC_NWE
        src = enc[bpi, kind - 2].tobytes()[w * P:(w + 1) * P]
        src = src + bytes(P - len(src))
        assert data[:P] == src and data[P:] == bytes(64 - P), \
            f"kind{kind} addr{addr} 字节不一致"
        n += 1
    return n


if __name__ == "__main__":
    n = selfcheck_batch(8)
    print(f"[n2_batch] selfcheck OK: NB=8 enc 打包 {n} word 逐字节一致")
