#!/usr/bin/env python3
# =============================================================================
# pynq_load_weights.py —— Stage 5 · 权重从 DDR 经 DMA 灌入 PL + 握手 + 全网络对拍
# -----------------------------------------------------------------------------
# 复用 Stage 4 的 n2_stage4.bit（不改 RTL、不重新综合）。
#
# 与 stage4_smoke.run_point 的本质区别（为什么它是"预览"而这才是正式版）：
#   run_point 把 enc+13 层+START 一次性连发 —— 在仿真里已证明会挂死：
#   ① 权重 bank 只有 2 个（乒乓），13 层全发完后早期层内容被覆盖；
#   ② ready 标志被消费后，中途层的预取请求永远等不到装载 → S_LOADW 挂死。
#   本脚本改为【prefetch 驱动的逐层供给】—— 与 tb_n2_axis_wrapper /
#   tb_stage5_stream 的调度完全一致，也就是 Stage 计划里说的
#   "DMA MM2S 把权重流式发到 PL 的 weight_buffer（乒乓）+ 握手"：
#
#     PS 轮询 GPIO ch2 状态字：
#       prefetch(status[31:28]) = 调度器正在请求第几层权重（15=无请求）
#       armed  (status[27])     = L0/L1 双 bank 就绪，可发 START
#       busy   (status[26])     = 推理中
#       parser_idle(status[24]) = wrapper 解析器空闲（块与块之间的发送时机）
#     请求第 L 层 → DMA MM2S 发第 L 层的 W+B+Q 块（bank=L&1，Q 块带 done）
#     armed → 发 START → 推理期间继续按 prefetch 供 L2..L12
#     result 拍经 S2MM 回 DDR → 与黄金 EXP 逐位对拍
#
# 验收（对照 Stage 计划 S5 + S6/S7 的一部分）：
#   1. PL 收到的权重 == DDR 里的权重：推理结果 bit 级一致 ⇒ 全部 690KB
#      权重逐字节必然正确（比抽查若干字节强得多的证明）。
#   2. 单层→全网络：结果同时含 attn(L8) 与 sig(L12) 两路 tap，一次对拍
#      覆盖 13 层全部调度/乒乓/skip 拼接。
#
# 用法（板上 Jupyter，n2_stage4.bit/.hwh + test_vectors/ 已拷到同目录）：
#   from pynq import Overlay
#   from pynq_load_weights import stage5_run, stage5_smoke
#   ol = Overlay("n2_stage4.bit")
#   stage5_smoke(ol)                # 先冒烟：ECHO + 状态口
#   stage5_run(ol)                  # Stage 5 验收：两点全网络 bit 级对拍
#   stage5_run(ol, npass=3)         # 稳定性：重复 3 轮
# 命令行：python3 pynq_load_weights.py [tv3 路径]
#
# 依赖：n2_stream.py（协议打包器，与仿真 TB5 共用同一套函数——
#       把 software/stage5/n2_stream.py 和本文件放同一目录）
# =============================================================================
import sys
import time

import numpy as np
from pynq import Overlay, allocate, MMIO

from n2_stream import (P, OP_ECHO, u64, parse_tv3, selfcheck,
                       enc_beats, layer_beats, start_beat)

# ───────────────────────── 状态字段（与 wrapper status_o 一致）──────────────
PF_NONE = 0xF

# 单次 MM2S 传输上限（字节）。axi_dma 直通模式 LENGTH 有效位宽 =
# c_sg_length_width（本 BD 默认 14bit → 最大 16383）。取 8192 的整数倍
# （=1024 拍）既安全又保持 64B word 对齐；若你的 DMA 配了 23/26bit 可调大。
MAX_XFER = 8192


class DMARecoveryRequired(RuntimeError):
    """The active simple-mode packet cannot be aborted without reloading PL."""

def st_prefetch(st): return (st >> 28) & 0xF
def st_armed(st):    return (st >> 27) & 1
def st_busy(st):     return (st >> 26) & 1
def st_done(st):     return (st >> 25) & 1
def st_widle(st):    return (st >> 24) & 1


# ───────────────────────── RawDMA（与 stage4_smoke.py 相同实现）─────────────
class RawDMA:
    """不依赖中断的 AXI DMA（直通模式）驱动，寄存器轮询。"""
    def __init__(self, base):
        self.mm = MMIO(base, 0x100)

    def _wait_bit(self, reg, mask, expect, tmo=10.0, label=""):
        t0 = time.time()
        while True:
            v = self.mm.read(reg)
            if (v & mask) == expect:
                return v
            if time.time() - t0 > tmo:
                raise TimeoutError(
                    f"DMA {label} timeout: reg={reg:#x} got={v:#x} | "
                    f"MM2S_SR={self.mm.read(0x04):#x} "
                    f"S2MM_SR={self.mm.read(0x34):#x}")
            time.sleep(1e-5)

    def restart(self):
        """Gracefully stop, reset, and restart both simple-mode channels.

        AXI DMA reset is only issued after both channels report Halted.  If an
        S2MM transaction is stuck waiting for TLAST, software reset requests can
        themselves remain asserted; the caller must reload the Overlay instead.
        """
        self.mm.write(0x00, 0x00); self.mm.write(0x30, 0x00)
        try:
            self._wait_bit(0x04, 0x1, 0x1, 2.0, "MM2S halt")
            self._wait_bit(0x34, 0x1, 0x1, 2.0, "S2MM halt")
        except TimeoutError as exc:
            raise DMARecoveryRequired(
                "DMA packet cannot halt cleanly; Overlay reload required: %r" %
                self.snapshot()) from exc
        # PG021 defines either channel's Reset bit as a reset of the complete
        # AXI DMA core.  Drive exactly one Reset bit after both channels are
        # Halted; asserting both channel bits at once is unnecessary and was
        # the source of the observed 0x00010006/0x0000100x stuck state.
        self.mm.write(0x00, 0x04)
        for _ in range(200):
            if not (self.mm.read(0x00) & 4):
                break
            time.sleep(0.001)
        else:
            raise DMARecoveryRequired(
                "DMA reset did not self-clear; Overlay reload required: %r" % self.snapshot())
        try:
            self._wait_bit(0x04, 0x1, 0x1, 2.0, "MM2S reset-halt")
            self._wait_bit(0x34, 0x1, 0x1, 2.0, "S2MM reset-halt")
            # Clear any IOC/Delay/Error IRQ latches left by the previous packet.
            self.mm.write(0x04, 0x7000); self.mm.write(0x34, 0x7000)
            self.mm.write(0x00, 0x01); self.mm.write(0x30, 0x01)
            self._wait_bit(0x04, 0x1, 0x0, 2.0, "MM2S run")
            self._wait_bit(0x34, 0x1, 0x0, 2.0, "S2MM run")
        except TimeoutError as exc:
            raise DMARecoveryRequired(
                "DMA did not return to direct-mode Run; Overlay reload required: %r" %
                self.snapshot()) from exc

    @staticmethod
    def _dma_errors(status):
        """Return the AXI-DMA channel error bits (internal/slave/decode)."""
        return int(status) & 0x70

    def snapshot(self):
        """Small, JSON-safe register snapshot used by board diagnostics."""
        return {
            "mm2s_cr": "0x%08x" % int(self.mm.read(0x00)),
            "mm2s_sr": "0x%08x" % int(self.mm.read(0x04)),
            "s2mm_cr": "0x%08x" % int(self.mm.read(0x30)),
            "s2mm_sr": "0x%08x" % int(self.mm.read(0x34)),
        }

    def _wait_done_or_error(self, reg, tmo, label):
        """Wait for Idle while failing immediately on a real DMA error.

        DMASR bits 4..6 are the channel errors.  Bit 14 is only the aggregate
        error-interrupt flag, so checking the primary bits produces a useful
        diagnostic even when interrupts are disabled.
        """
        t0 = time.monotonic()
        while True:
            value = int(self.mm.read(reg))
            if self._dma_errors(value):
                raise RuntimeError(
                    "DMA %s error: reg=%#x value=%#x snapshot=%r" %
                    (label, reg, value, self.snapshot()))
            if value & 0x2:
                return value
            if time.monotonic() - t0 > tmo:
                raise TimeoutError(
                    "DMA %s timeout: reg=%#x got=%#x snapshot=%r" %
                    (label, reg, value, self.snapshot()))
            time.sleep(1e-5)

    def arm_recv(self, buf, nbytes):
        nbytes = int(nbytes)
        if nbytes <= 0 or nbytes >= (1 << 23):
            raise ValueError("invalid S2MM byte count %d" % nbytes)
        # Clear stale IRQ flags before programming a new direct-mode transfer.
        self.mm.write(0x34, 0x7000)
        self.mm.write(0x48, buf.physical_address)
        self.mm.write(0x58, nbytes)
        # The readback is also an AXI-Lite ordering barrier: the following
        # MM2S launch cannot overtake the S2MM address/length writes.
        status = int(self.mm.read(0x34))
        if status & 0x1:
            raise RuntimeError("S2MM remained halted after arm: %r" % self.snapshot())
        if self._dma_errors(status):
            raise RuntimeError("S2MM failed while arming: %r" % self.snapshot())
        return status

    def recv_done(self):
        """非阻塞查询：S2MM Idle=1 即传输完成"""
        return bool(self.mm.read(0x34) & 0x2)

    def wait_recv(self, buf, tmo=10.0):
        self._wait_done_or_error(0x34, tmo, "S2MM done")
        if hasattr(buf, "invalidate"):
            buf.invalidate()

    def send(self, buf, nbytes, tmo=10.0, offset=0):
        """启动 MM2S 传输（DDR→PL）并等待完成。offset=缓冲区内字节偏移。"""
        if hasattr(buf, "flush"):
            buf.flush()
        return self.send_preflushed(buf, nbytes, tmo=tmo, offset=offset)

    def send_preflushed(self, buf, nbytes, tmo=10.0, offset=0):
        """发送已由调用者 flush 的缓冲区。

        Stage34 使用两个 CMA 缓冲区：PL 计算当前包时，后台线程
        填充并 flush 下一个包。这个入口避免在启动 DMA 时再重复 flush
        整个缓冲区。
        """
        nbytes = int(nbytes); offset = int(offset)
        if nbytes <= 0 or nbytes >= (1 << 23):
            raise ValueError("invalid MM2S byte count %d" % nbytes)
        self.mm.write(0x04, 0x7000)
        self.mm.write(0x18, buf.physical_address + offset)
        self.mm.write(0x28, nbytes)
        self._wait_done_or_error(0x04, tmo, "MM2S done")

    def send_chunked(self, buf, nbytes, max_xfer, tmo=10.0):
        """分块 MM2S 发送：绕开直通模式 LENGTH 寄存器位宽上限。
        ── Stage 5 上板抓出的真 bug ──
        axi_dma 直通模式单次传输 ≤ 2^c_sg_length_width-1 字节（默认 14bit
        =16383B）。L0 块 32856B=0x8058 写入 14bit 字段只剩 0x58=88B=11 拍，
        解析器恰好卡在 PS_PACK 等剩余拍（status=0x00000000, parser_idle=0,
        pf 停在 0）。wrapper 按拍计数、不看传输边界，拆多次发完全等价。"""
        off = 0
        while off < nbytes:
            n = min(max_xfer, nbytes - off)
            self.send(buf, n, tmo=tmo, offset=off)
            off += n

    def status(self):
        return self.mm.read(0x04), self.mm.read(0x34)


# ───────────────────────── IP 查找 / GPIO ─────────────────────────
def _find_ip(ol, *needles):
    for name, info in ol.ip_dict.items():
        if any(k in name.lower() for k in needles):
            return info
    raise RuntimeError(f"找不到 IP {needles}：{list(ol.ip_dict.keys())}")

def read_status(gm):
    return gm.read(0x8)                 # GPIO ch2 @ 0x8

def soft_reset(gm):
    gm.write(0x0, 1); time.sleep(0.01); gm.write(0x0, 0); time.sleep(0.01)


# ───────────────────────── Stage 5 主体 ─────────────────────────
class N2Loader:
    """权重加载器：预打包全部分节 → prefetch 驱动逐层 DMA 灌入。"""

    def __init__(self, ol, tv_path="test_vectors/tv3_net.txt"):
        self.gm = MMIO(_find_ip(ol, "gpio")["phys_addr"], 0x100)
        self.dma = RawDMA(_find_ip(ol, "dma")["phys_addr"])
        self.tv = parse_tv3(tv_path, P)
        lc, enc, W, B, Q, EXP, npts, enc_nwe = self.tv
        self.nl, self.npts, self.EXP = len(lc), npts, EXP

        # 上板前先跑软件自检：打包器 ↔ 协议解码器 逐字节一致
        stats = selfcheck(self.tv)
        print(f"[loader] selfcheck OK：{stats['words_checked']} word 逐字节一致")

        # 预打包：每层一个 DMA 缓冲（点间可复用，只有 enc 逐点变化）
        self.lay_buf, self.lay_len = [], []
        nbytes_total = 0
        for l in range(self.nl):
            beats = layer_beats(l, W, B, Q)
            buf = allocate(shape=(len(beats),), dtype=np.uint64)
            buf[:] = np.array(beats, dtype=np.uint64)
            if hasattr(buf, "flush"):
                buf.flush()
            self.lay_buf.append(buf)
            self.lay_len.append(len(beats) * 8)
            nbytes_total += len(beats) * 8
        self.enc_buf, self.enc_len = [], []
        for pt in range(npts):
            beats = enc_beats(pt, enc, enc_nwe)
            buf = allocate(shape=(len(beats),), dtype=np.uint64)
            buf[:] = np.array(beats, dtype=np.uint64)
            if hasattr(buf, "flush"):
                buf.flush()
            self.enc_buf.append(buf)
            self.enc_len.append(len(beats) * 8)
        sb = start_beat()
        self.start_buf = allocate(shape=(len(sb),), dtype=np.uint64)
        self.start_buf[:] = np.array(sb, dtype=np.uint64)
        if hasattr(self.start_buf, "flush"):
            self.start_buf.flush()
        self.rx = allocate(shape=(1,), dtype=np.uint64)
        print(f"[loader] 预打包完成：{self.nl} 层 / {npts} 点，"
              f"权重流总量 {nbytes_total/1024:.0f} KiB")

    # ── 供层：等 parser 空闲 → MM2S 分块发一层的 W+B+Q ──
    def _serve_layer(self, l, tmo=5.0):
        t0 = time.time()
        while not st_widle(read_status(self.gm)):
            if time.time() - t0 > tmo:
                raise TimeoutError(f"L{l}: parser 不空闲，status="
                                   f"{read_status(self.gm):#010x}")
            time.sleep(1e-5)
        # 分块发送（≤MAX_XFER/次）：块中途 parser 处于 PS_PACK 属正常状态，
        # 只有块与块之间才检查 parser_idle
        self.dma.send_chunked(self.lay_buf[l], self.lay_len[l],
                              MAX_XFER, tmo=tmo)

    # ── 一个点的完整流程（prefetch 驱动，与 TB5 逐步一致）──
    def run_point(self, pt, verbose=True, tmo=30.0):
        gm, dma = self.gm, self.dma
        soft_reset(gm)
        dma.restart()
        served = [False] * self.nl
        t0 = time.time()

        dma.arm_recv(self.rx, 8)                    # 先挂结果接收（1 拍）
        dma.send_chunked(self.enc_buf[pt], self.enc_len[pt],  # 点编码
                         MAX_XFER)                  # (216B，实际 1 次就发完)

        # 装填 L0/L1 → armed → START → 推理中继续供 L2..L12 → 结果
        started = False
        while True:
            st = read_status(gm)
            pf = st_prefetch(st)
            if pf != PF_NONE and pf < self.nl and not served[pf]:
                served[pf] = True
                self._serve_layer(pf, tmo=tmo)
            elif not started and st_armed(st):
                dma.send(self.start_buf, len(self.start_buf) * 8)
                started = True
            elif started and dma.recv_done():
                break
            if time.time() - t0 > tmo:
                raise TimeoutError(
                    f"point {pt} 超时：status={st:#010x} pf={pf} "
                    f"armed={st_armed(st)} busy={st_busy(st)} "
                    f"served={[i for i, s in enumerate(served) if s]}")
        self.dma.wait_recv(self.rx, tmo=1.0)        # 已完成，取数+invalidate
        dt = (time.time() - t0) * 1e3

        r = int(self.rx[0]) & 0xFFFFFFFF
        got = [(r >> (8 * i)) & 0xFF for i in range(4)]
        signed = [v - 256 if v > 127 else v for v in got]
        ok = signed == self.EXP[pt]
        if verbose:
            print(f"[stage5] point {pt}: rtl={signed} exp={self.EXP[pt]} "
                  f"{'PASS' if ok else 'FAIL'}  ({dt:.1f} ms 含 13 层权重灌入)")
        return ok


def stage5_smoke(ol):
    """上板第一步：ECHO 回环 + 状态口自检（等价 Stage 4 冒烟）"""
    gm = MMIO(_find_ip(ol, "gpio")["phys_addr"], 0x100)
    dma = RawDMA(_find_ip(ol, "dma")["phys_addr"])
    soft_reset(gm)
    dma.restart()
    tx = allocate(shape=(1,), dtype=np.uint64)
    rx = allocate(shape=(1,), dtype=np.uint64)
    ok = True
    for i in range(4):
        tx[0] = u64(OP_ECHO, 0x5A5E_0000_0001 + i)
        dma.arm_recv(rx, 8)
        dma.send(tx, 8)
        dma.wait_recv(rx)
        ok &= int(rx[0]) == int(tx[0])
    st = read_status(gm)
    print(f"[stage5] ECHO 4/4 {'OK' if ok else 'FAIL'}；status={st:#010x} "
          f"prefetch={st_prefetch(st)} armed={st_armed(st)} "
          f"busy={st_busy(st)} parser_idle={st_widle(st)}")
    assert ok, "ECHO 回环失败：先回 Stage 4 排查 DMA 通路"
    return True


def stage5_run(ol, tv_path="test_vectors/tv3_net.txt", npass=1):
    """Stage 5 验收主入口：全部点 bit 级对拍（可多轮跑稳定性）"""
    ld = N2Loader(ol, tv_path)
    total, good = 0, 0
    for rnd in range(npass):
        for pt in range(ld.npts):
            good += ld.run_point(pt)
            total += 1
    verdict = "PASS" if good == total else "FAIL"
    print(f"[stage5] STAGE 5 RESULT: {good}/{total} {verdict}")
    if verdict == "PASS":
        print("[stage5] ⇒ 权重经 DDR→DMA→PL 通路逐字节正确（结果 bit 级一致），")
        print("[stage5]   Stage 5 验收达成；同时覆盖 S6 单层/S7 全网络的单点正确性。")
    return good == total


if __name__ == "__main__":
    tvp = sys.argv[1] if len(sys.argv) > 1 else "test_vectors/tv3_net.txt"
    ol = Overlay("n2_stage4.bit")
    stage5_smoke(ol)
    stage5_run(ol, tvp)
