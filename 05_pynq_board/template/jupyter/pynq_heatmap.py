#!/usr/bin/env python3
# =============================================================================
# pynq_heatmap.py —— Stage 9 · 信号场热力图 HDMI 输出（配 n2_stage9.bit）
# -----------------------------------------------------------------------------
# n2_stage9.bit = Stage 10 批处理引擎 + 片上 HDMI 显示模块（640×480@60，
# 90×45 场每格 7×10 像素居中显示）。PS 通过同一条 DMA 流的 OP=0x4 FIELD
# 块写显示 RAM：
#   块头 [63:60]=0x4 [55:40]=起始字地址 [39:24]=字数；随后每拍 1 个 64bit 字
#   地址 0..511   = field RAM（4050 字节场数据，8 字节/字，行主序）
#   地址 512..639 = LUT RAM（256 项 ×32bit {0,R,G,B}，两项/字）
#
# 用法（板上，与 stage5/8/10 文件同目录）：
#   from pynq import Overlay
#   from pynq_heatmap import HeatmapDemo
#   ol = Overlay("n2_stage9.bit")
#   hd = HeatmapDemo(ol)
#   hd.upload_lut()               # ① viridis 调色板 → 屏幕出现深蓝底框
#   hd.show_golden()              # ② 先把 PC 黄金场图推上屏（秒级，验显示通路）
#   hd.live_field()               # ③ ★ 板端实时推理 4050 点边算边刷（~17s 扫完）
#   hd.demo_loop()                # ④ 演示：多 tx 场景循环（需预生成 npz，见 README）
# =============================================================================
import glob
import time

import numpy as np
from pynq import Overlay, allocate

OP_FIELD = 0x4
FIELD_WORDS = 512          # 4KB
LUT_BASE = 512             # LUT 起始字地址


def field_hdr(addr, nwords):
    return (OP_FIELD << 60) | ((addr & 0xFFFF) << 40) | ((nwords & 0xFFFF) << 24)


# ── viridis 调色板（matplotlib 采样 16 锚点 + 线性插值，免依赖）────────────
_V16 = [(68,1,84),(71,44,122),(59,81,139),(44,113,142),(33,144,141),
        (39,173,129),(92,200,99),(170,220,50),(253,231,37),(253,231,37),
        (253,231,37),(253,231,37),(253,231,37),(253,231,37),(253,231,37),
        (253,231,37)]
def viridis256():
    # 前 9 个锚点覆盖 0..255（后段饱和黄），线性插值
    anchors = _V16[:9]
    lut = np.zeros((256, 3), dtype=np.uint8)
    seg = 255 / (len(anchors) - 1)
    for i in range(256):
        a = min(int(i / seg), len(anchors) - 2)
        t = (i - a * seg) / seg
        for c in range(3):
            lut[i, c] = int(anchors[a][c] * (1 - t) + anchors[a + 1][c] * t)
    return lut


class HeatmapDemo:
    def __init__(self, ol, npz_path="test_vectors/tv8_field.npz",
                 tv_path="test_vectors/tv3_net.txt"):
        # Stage26 only reuses the HDMI constants/functions in this module.  Keep
        # the legacy Stage10 driver lazy so importing pynq_heatmap does not make
        # pynq_batch_field.py a hard dependency of the Stage26 runner.
        from pynq_batch_field import BatchFieldRunner, NB

        self.nb = NB
        self.fr = BatchFieldRunner(ol, npz_path, tv_path)   # 推理引擎复用 S10
        self.dma = self.fr.dma
        self.n_azi, self.n_ele = self.fr.n_azi, self.fr.n_ele
        self.npts = self.fr.npts
        self.tx = allocate(shape=(1 + FIELD_WORDS,), dtype=np.uint64)
        # 归一化参数（sig_amp 通道 int8 → 0..255 索引）
        g = self.fr.golden[:, 2].astype(np.int32)
        self.vmin, self.vmax = int(g.min()), int(g.max())
        print(f"[hdmi] 显示归一化: sig_amp [{self.vmin},{self.vmax}] → LUT 0..255")

    # ── 底层：写显示 RAM ──
    def _write(self, addr, words):
        n = len(words)
        self.tx[0] = field_hdr(addr, n)
        self.tx[1:1 + n] = words
        if hasattr(self.tx, "flush"):
            self.tx.flush()
        self.dma.send(self.tx, (1 + n) * 8)

    def upload_lut(self, lut=None):
        """256 项调色板 → LUT RAM（两项/字：偶下标在低 32bit）"""
        lut = viridis256() if lut is None else lut
        w = np.zeros(128, dtype=np.uint64)
        for i in range(128):
            lo = (int(lut[2*i, 0]) << 16) | (int(lut[2*i, 1]) << 8) | int(lut[2*i, 2])
            hi = (int(lut[2*i+1, 0]) << 16) | (int(lut[2*i+1, 1]) << 8) | int(lut[2*i+1, 2])
            w[i] = (hi << 32) | lo
        self._write(LUT_BASE, w)
        print("[hdmi] LUT 上传完成（viridis）")

    def _norm(self, sig):
        """int8 sig_amp → uint8 索引"""
        s = np.clip((sig.astype(np.int32) - self.vmin) * 255 //
                    max(1, self.vmax - self.vmin), 0, 255).astype(np.uint8)
        return s

    def push_field(self, sig_amp_flat):
        """4050 点 sig_amp（int8, 行主序）→ field RAM（一次 507 字）"""
        idx = self._norm(sig_amp_flat)
        buf = np.zeros(FIELD_WORDS * 8, dtype=np.uint8)
        buf[:self.npts] = idx
        self._write(0, buf.view(np.uint64))

    def push_rows(self, row0, sig_rows):
        """增量：从 row0 行首起连续写 len(sig_rows) 个点（允许尾部不完整行——
        NB=8 不整除行宽 90，批边界几乎从不落在行边界上）"""
        idx = self._norm(np.asarray(sig_rows))
        b0 = row0 * self.n_azi
        n = idx.size
        # 对齐到 8 字节字边界
        w0, w1 = b0 // 8, (b0 + n + 7) // 8
        full = np.zeros(FIELD_WORDS * 8, dtype=np.uint8)
        full[b0:b0 + n] = idx
        self._write(w0, full.view(np.uint64)[w0:w1])

    # ── 演示入口 ──
    def show_golden(self):
        """PC 黄金场图直接上屏（验证显示通路，不跑推理）"""
        self.push_field(self.fr.golden[:, 2])
        print("[hdmi] 黄金场图已上屏 —— HDMI 应显示 45×90 viridis 热力图")

    def live_field(self, rows_per_update=5):
        """★ 板端实时推理并逐批刷屏：观众可看到扫描线式的场图生成过程"""
        t0 = time.time()
        line = np.zeros(self.npts, dtype=np.int8)
        done = 0
        for bi in range(self.fr.nbatch):
            res, _ = self.fr.run_batch(bi)
            lo = bi * self.nb
            hi = min(lo + self.nb, self.npts)
            line[lo:hi] = res[:hi - lo, 2]
            done = hi
            # 每凑满 rows_per_update 行刷一次屏（含尾部不完整行，扫描线效果）
            if done % (self.n_azi * rows_per_update) < self.nb or done == self.npts:
                self.push_rows(0, line[:done])
        dt = time.time() - t0
        ok = int((line == self.fr.golden[:, 2]).sum())
        print(f"[hdmi] 实时场图完成：{dt:.1f}s，sig_amp 一致 {ok}/{self.npts}")
        return line

    def demo_loop(self, pattern="test_vectors/tv9_scene*.npz", dwell=3.0, loops=0):
        """多场景循环演示：每个 npz = 一个 tx 位置的 {enc_q, golden}。
        场景包用 PC 端 gen_field_vectors.py --tx ... --out_npz 生成后拷板。
        loops=0 无限循环（Ctrl+C 停）。"""
        scenes = sorted(glob.glob(pattern))
        if not scenes:
            print(f"[hdmi] 未找到场景包 {pattern}；先看 show_golden()/live_field()")
            return
        print(f"[hdmi] {len(scenes)} 个场景循环，{dwell}s/幅")
        it = 0
        try:
            while loops == 0 or it < loops:
                for s in scenes:
                    z = np.load(s)
                    self.push_field(z["golden"][:, 2])
                    time.sleep(dwell)
                it += 1
        except KeyboardInterrupt:
            print("[hdmi] demo 停止")


if __name__ == "__main__":
    ol = Overlay("n2_stage9.bit")
    hd = HeatmapDemo(ol)
    hd.upload_lut()
    hd.show_golden()
