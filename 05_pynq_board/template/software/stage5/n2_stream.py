#!/usr/bin/env python3
# =============================================================================
# n2_stream.py —— Stage 5 · 流协议打包器 / 解码自检（纯 numpy，无 pynq 依赖）
# -----------------------------------------------------------------------------
# 单一事实源（single source of truth）：
#   · PC 端 gen_stream.py 用它把 tv3_net.txt 打包成 RTL 可回放的 tv5_stream.txt
#   · 板端 pynq_load_weights.py 用它生成 DMA 发送的 64bit 拍序列
# 两条路径共用同一套打包函数 ⇒ "仿真通过 = 板端字节流也正确"。
#
# 协议（与 rtl/combined/n2_axis_wrapper.v 逐 bit 一致，见 README_stage4）：
#   TDATA[63:60]=op   0x1=WLOAD 块头  0x2=START  0xF=ECHO
#   WLOAD 头：[59:57]kind [56]bank [55:40]addr [39:24]nwords [23]done
#   随后每 word=8 拍（64B 小端，低段在前），地址自增
#
# ⚠ 已证伪的方案（沙盒实验 2026-08-23）：
#   "enc+13 层+START 一次性连发"（stage4_smoke.run_point 的做法）不可用：
#   ① 权重 bank 只有 2 个（偶层 bank0/奇层 bank1），13 层全发完后 bank0
#      只剩 L12、bank1 只剩 L11，前面各层内容全被覆盖；
#   ② ready[0] 被 START 消费后 L2 的预取永远等不到装载 → 调度器在
#      S_LOADW 挂死（实验 TB 100k 拍无结果）。
#   正确做法 = 本文件配套的"prefetch 驱动逐层供给"（与 tb_n2_axis_wrapper
#   相同）：PS 轮询 status.prefetch，请求第 L 层就发第 L 层的 W+B+Q 块。
# =============================================================================
import numpy as np

OP_WLOAD, OP_START, OP_ECHO = 0x1, 0x2, 0xF
P = 32                                   # 与 bitstream generic 一致


# ───────────────────────── 基础打包（与 stage4_smoke.py 相同实现）──────────
def u64(op, payload=0):
    return (op << 60) | (payload & ((1 << 60) - 1))


def wload_hdr(kind, bank, addr, nwords, done):
    """WLOAD 块头（41bit 有效 + 23bit 补零 —— 这 23bit 补零就是 Stage 4
    抓出的 81>64bit 截断 bug 的修正，Python 侧天然不会溢出但布局须一致）"""
    return (OP_WLOAD << 60) | ((kind & 7) << 57) | ((bank & 1) << 56) \
        | ((addr & 0xFFFF) << 40) | ((nwords & 0xFFFF) << 24) \
        | ((1 if done else 0) << 23)


def word512_beats(b64):
    """64 字节(512bit) word → 8 拍 64bit（小端，低段在前）"""
    assert len(b64) == 64
    return [int.from_bytes(b64[i * 8:(i + 1) * 8], "little") for i in range(8)]


# ───────────────────────── tv3_net.txt 解析（与 TB/stage4_smoke 同源）──────
def parse_tv3(path="test_vectors/tv3_net.txt", p=P):
    toks = open(path).read().split()
    i = 0
    pf, nl, npts = int(toks[i]), int(toks[i + 1]), int(toks[i + 2]); i += 3
    assert pf == p, f"tv3 P={pf} != {p}"
    lc, enc, W, B, Q, EXP = {}, [], {}, {}, {}, []
    enc_nwe = (63 + p - 1) // p
    cnt = {k: 0 for k in range(3)}
    while i < len(toks):
        t = int(toks[i]); i += 1
        if t == 1:                                     # LC l wcnt bcnt
            l = int(toks[i]); lc[l] = (int(toks[i + 1]), int(toks[i + 2])); i += 3
        elif t == 2:                                   # ENC kind + P 字节
            k = int(toks[i]); i += 1
            data = bytes((int(x) & 0xFF) for x in toks[i:i + p]); i += p
            pt, wd = cnt[k] // enc_nwe, cnt[k] % enc_nwe
            while len(enc) <= pt:
                enc.append({})
            enc[pt][k, wd] = data; cnt[k] += 1
        elif t == 3:                                   # W l + P 字节
            l = int(toks[i]); i += 1
            data = bytes((int(x) & 0xFF) for x in toks[i:i + p]); i += p
            W.setdefault(l, []).append(data)
        elif t == 4:                                   # B l + P 个 int16
            l = int(toks[i]); i += 1
            arr = np.array([int(x) for x in toks[i:i + p]], dtype=np.int16)
            i += p
            B.setdefault(l, []).append(arr)
        elif t == 5:                                   # Q l shift
            l = int(toks[i]); Q[l] = int(toks[i + 1]); i += 2
        elif t == 6:                                   # EXP pt + 4 值
            pt = int(toks[i]); EXP.append([int(x) for x in toks[i + 1:i + 5]]); i += 5
    return lc, enc, W, B, Q, EXP, npts, enc_nwe


# ───────────────────────── 分节打包（prefetch 驱动的最小发送单元）──────────
def enc_beats(pt, enc, enc_nwe):
    """一个点的 3 段位置编码（kind=2/3/4），点开始时一次发完"""
    out = []
    for k in range(3):
        out.append(wload_hdr(k + 2, 0, 0, enc_nwe, False))
        for w in range(enc_nwe):
            d = enc[pt][k, w]
            out += word512_beats(d + bytes(64 - len(d)))
    return out


def layer_beats(l, W, B, Q):
    """一层的 W+B+Q 块（Q 块带 done 标志），与 TB serve_layer 逐拍一致。
    只在 status.prefetch==l 时发送（乒乓 bank=l&1）。"""
    out = []
    wc, bc = len(W[l]), len(B[l])
    out.append(wload_hdr(0, l & 1, 0, wc, False))
    for w in W[l]:
        out += word512_beats(w + bytes(64 - len(w)))
    out.append(wload_hdr(1, l & 1, 0, bc, False))
    for b in B[l]:
        out += word512_beats(b.tobytes())
    out.append(wload_hdr(5, l & 1, l, 1, True))
    out += word512_beats(np.int16(Q[l]).tobytes() + bytes(62))
    return out


def start_beat():
    return [u64(OP_START)]


# ───────────────────────── 软件解码器（wrapper 解析 FSM 的行为模型）────────
def decode_stream(beats):
    """按 n2_axis_wrapper 的解析 FSM 逐拍解码，返回
    (writes, dones, starts)：
      writes = [(kind, bank, addr, 64B data), ...]   等价 wload_valid 拍
      dones  = [(bank), ...]                          等价 wload_done 拍
      starts = 发出的 START 数
    用于上板前自检：打包器产出 == RTL 将看到的 wload 序列。"""
    writes, dones, starts = [], [], 0
    i, n = 0, len(beats)
    while i < n:
        b = beats[i]; i += 1
        op = (b >> 60) & 0xF
        if op == OP_START:
            starts += 1
        elif op == OP_WLOAD:
            kind = (b >> 57) & 7
            bank = (b >> 56) & 1
            addr = (b >> 40) & 0xFFFF
            nwords = (b >> 24) & 0xFFFF
            done = (b >> 23) & 1
            for _ in range(nwords):
                assert i + 8 <= n, "流在 word 中途截断"
                w = b"".join(int(beats[i + j]).to_bytes(8, "little")
                             for j in range(8))
                i += 8
                writes.append((kind, bank, addr, w))
                addr += 1
            if done:
                dones.append(bank)
        # 其它 op（ECHO 等）无 wload 副作用
    return writes, dones, starts


def selfcheck(tv):
    """对每个分节打包→解码→与 tv3 原始数据逐字节比对。全部一致返回统计。"""
    lc, enc, W, B, Q, EXP, npts, enc_nwe = tv
    nl = len(lc)
    n_checked = 0

    # 1) 层块：kind0 word == W[l][i] + 32B 零；kind1 == bias int16；kind5 低 5bit==Q
    for l in range(nl):
        ws, ds, st = decode_stream(layer_beats(l, W, B, Q))
        assert st == 0 and ds == [l & 1], f"L{l} done/bank 错误: {ds}"
        k0 = [w for w in ws if w[0] == 0]
        k1 = [w for w in ws if w[0] == 1]
        k5 = [w for w in ws if w[0] == 5]
        assert len(k0) == len(W[l]) and len(k1) == len(B[l]) and len(k5) == 1
        for i, (kind, bank, addr, data) in enumerate(k0):
            assert bank == (l & 1) and addr == i
            assert data[:32] == W[l][i] and data[32:] == bytes(32), \
                f"L{l} W word{i} 字节不一致"
            n_checked += 1
        for i, (kind, bank, addr, data) in enumerate(k1):
            assert bank == (l & 1) and addr == i
            assert data == B[l][i].tobytes(), f"L{l} B word{i} 不一致"
            n_checked += 1
        kind, bank, addr, data = k5[0]
        assert bank == (l & 1) and addr == l and (data[0] & 0x1F) == (Q[l] & 0x1F)
        n_checked += 1

    # 2) 编码块：kind=2/3/4，字节 == enc 数据 + 零填充
    for pt in range(npts):
        ws, ds, st = decode_stream(enc_beats(pt, enc, enc_nwe))
        assert st == 0 and ds == []
        for (kind, bank, addr, data) in ws:
            src = enc[pt][kind - 2, addr]
            assert data[:len(src)] == src and data[len(src):] == bytes(64 - len(src))
            n_checked += 1

    # 3) START
    _, _, st = decode_stream(start_beat())
    assert st == 1
    return {"layers": nl, "npts": npts, "words_checked": n_checked}


# ───────────────────────── tv5_stream.txt 写出（TB5 回放格式）──────────────
# 格式（十进制 token + 16 位十六进制拍）：
#   0 NPTS NL                    文件头
#   1 pt nbeats <hex×n>          点 pt 的编码分节
#   2 l  nbeats <hex×n>          第 l 层的 W+B+Q 分节
#   3 nbeats <hex×n>             START 分节
#   4 pt v0 v1 v2 v3             黄金期望（int8）
def write_stream(tv, out_path):
    lc, enc, W, B, Q, EXP, npts, enc_nwe = tv
    nl = len(lc)
    lines = [f"0 {npts} {nl}"]
    total = 0

    def emit(tag, beats):
        nonlocal total
        lines.append(tag + f" {len(beats)}")
        lines.extend(f"{b:016x}" for b in beats)
        total += len(beats)

    for pt in range(npts):
        emit(f"1 {pt}", enc_beats(pt, enc, enc_nwe))
    for l in range(nl):
        emit(f"2 {l}", layer_beats(l, W, B, Q))
    emit("3", start_beat())
    for pt in range(npts):
        lines.append(f"4 {pt} " + " ".join(str(v) for v in EXP[pt]))
    with open(out_path, "w") as f:
        f.write("\n".join(lines) + "\n")
    return total
