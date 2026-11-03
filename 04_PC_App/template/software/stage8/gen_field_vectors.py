#!/usr/bin/env python3
# =============================================================================
# gen_field_vectors.py —— Stage 8 · 场生成：网格编码 + 定点黄金场图生成器
# -----------------------------------------------------------------------------
# 核心思想：tv3_net.txt 已经是"硬件真正吃进去的东西"（转置权重 word / b_add /
# shift），本脚本直接从它【反推】W/B/S，对角度域网格逐点做与 RTL 逐位一致的
# 定点前向 → 黄金场图。因此：
#   · 验收判据 = 板端场图 == 定点黄金 bit 级一致（与几何参数选择无关）；
#   · 换真实权重 = 先用 --weights/--manifest 重跑 stage3 gen_net_vectors.py
#     再重跑本脚本，代码零改动；
#   · 自校验 = 用 tv3 自带的 enc(kind=2) 重算 finals，必须 == EXP(kind=6)，
#     证明"权重重建 + 定点前向"与 Stage 3 黄金完全一致。
#
# 网格几何（角度域，对齐 Stage 1 文档：横=方位、纵=俯仰）：
#   az_i = (i+0.5)/n_azi*360°, el_j = (j+0.5)/n_ele*90°
#   d    = (cos el·cos az, cos el·sin az, sin el)      单位方向
#   pts  = gateway + radius·d      view = d            tx = 常量（可配置）
#   （若需与 Stage 1 golden_field.py 几何完全对齐，改 --gateway/--radius/--tx
#     即可；验收判据不依赖几何。）
#
# 输出：
#   test_vectors/tv8_field.npz   板端包：enc_q(int8,[Npts,3,63]) + 黄金
#                                 golden(int8,[Npts,4]) + 网格元数据
#   test_vectors/tv8_probe.txt   3 个真实网格点的 tv3 格式文件（角点/中心），
#                                 供 tb_n2_axis_wrapper +FILE= 仿真对拍
#   sim/stage8_field_golden.npy  黄金场图 (n_ele,n_azi,4) int8
#   sim/stage8_field_golden.png  sig_amp 通道热力图（有 matplotlib 时）
#
# 用法（N2-NeRF2 根目录）：
#   python software\stage8\gen_field_vectors.py                      ;# 90x45 全网格
#   python software\stage8\gen_field_vectors.py --n_azi 32 --n_ele 16 ;# 快速小网格
#   python software\stage8\gen_field_vectors.py --tx 0.65,0.0,0.10    ;# 挪发射端
# 合格判据：打印 "self-test OK (finals == EXP)" + 网格统计 + 文件写出。
# =============================================================================
import argparse
import math
import os
import sys

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "stage5"))
from n2_stream import parse_tv3, P                     # noqa: E402

# NeRF² 拓扑（与 rtl/core/layer_scheduler.v 的 ltab / stage3 gen 一致）
TOPO = [
    (63,  256, True), (256, 256, True), (256, 256, True), (256, 256, True),
    (256, 256, True), (319, 256, True), (256, 256, True), (256, 256, True),
    (256, 2,   False), (256, 256, False), (382, 256, True),
    (256, 128, True), (128, 2,   False),
]
NL = len(TOPO)
S_IN = 1.0 / 127.0
CH_NAMES = ["attn_amp", "attn_phase", "sig_amp", "sig_phase"]


# ───────────────────────── 从 tv3 反推 W / b_add / shift ───────────────────
def rebuild_layers(tv):
    """kind=3 转置 word → W[l](N,K) int8；kind=4 → b_add(N,) int16；kind=5 → S"""
    lc, enc, Wwords, Bwords, Q, EXP, npts, enc_nwe = tv
    W, B, S = [], [], []
    for l, (K, N, _) in enumerate(TOPO):
        ng = (N + P - 1) // P
        words = Wwords[l]                        # K*ng 个 32 字节 word
        assert len(words) == K * ng, f"L{l} word 数 {len(words)} != {K*ng}"
        # word[k*ng+g] 字节 j = W[g*P+j, k]
        arr = np.frombuffer(b"".join(words), dtype=np.int8).reshape(K, ng, P)
        Wl = np.zeros((ng * P, K), dtype=np.int8)
        for g in range(ng):
            Wl[g * P:(g + 1) * P, :] = arr[:, g, :].T
        W.append(Wl[:N, :].copy())
        bw = np.concatenate(Bwords[l])           # ng 个 word × P int16
        B.append(bw[:N].astype(np.int16).copy())
        S.append(int(Q[l]))
    return W, B, S


# ───────────────────────── 定点前向（批量向量化，RTL 同款）─────────────────
def forward_fixed_batch(W, S, B, enc3):
    """enc3: [e_pts, e_view, e_tx]，每个 (63, Npts) int8 → (Npts,4) int8"""
    main = None
    fin01 = fin23 = None
    for l, (K, N, act) in enumerate(TOPO):
        if l == 0:
            x = enc3[0]
        elif l == 5:
            x = np.vstack([main, enc3[0]])
        elif l == 10:
            x = np.vstack([main, enc3[1], enc3[2]])
        else:
            x = main
        assert x.shape[0] == K, f"L{l} 输入维 {x.shape[0]} != {K}"
        acc = W[l].astype(np.int64) @ x.astype(np.int64)          # (N,Npts)
        y = np.clip(np.right_shift(acc, S[l]) + B[l][:, None],
                    -128, 127).astype(np.int8)
        if act:
            y = np.maximum(y, 0).astype(np.int8)
        if l == 8:
            fin01 = y[:2]
        elif l == 12:
            fin23 = y[:2]
        else:
            main = y
    return np.vstack([fin01, fin23]).T.copy()                     # (Npts,4)


# ───────────────────────── 位置编码（与 stage3 gen 逐位一致）───────────────
def positional_encode_batch(xyz, multires=10):
    """xyz: (3, Npts) float → (63, Npts) float。
    ⚠ 频率 2^l 无 π——与 NeRF2 官方 Embedder 一致（真 bug⑤ 修复，见 stage3）"""
    out = [xyz]
    for l in range(multires):
        f = 2.0 ** l
        out += [np.sin(f * xyz), np.cos(f * xyz)]
    return np.concatenate(out, axis=0)


def quantize_enc(e):
    return np.clip(np.round(e / S_IN), -127, 127).astype(np.int8)


# ───────────────────────── 网格几何 ─────────────────────────
def make_grid(n_azi, n_ele, gateway, radius, tx, grid="center"):
    """grid='center': (i+0.5)/n 格中心（S8 原版）
       grid='stage1': linspace(1°,360°)/(1°,90°)，复刻 Stage1 golden_field.py
                      的 torch.linspace 网格（端点含 1° 与 360°/90°）"""
    if grid == "stage1":
        az = np.linspace(1, 360, n_azi) / 180 * math.pi
        el = np.linspace(1, 90, n_ele) / 180 * math.pi
    else:
        az = (np.arange(n_azi) + 0.5) / n_azi * 2 * math.pi
        el = (np.arange(n_ele) + 0.5) / n_ele * (math.pi / 2)
    AZ, EL = np.meshgrid(az, el)                 # (n_ele, n_azi)，行=俯仰
    d = np.stack([np.cos(EL) * np.cos(AZ),
                  np.cos(EL) * np.sin(AZ),
                  np.sin(EL)], axis=0).reshape(3, -1)   # (3, Npts)
    pts = np.asarray(gateway, dtype=np.float64)[:, None] + radius * d
    txm = np.tile(np.asarray(tx, dtype=np.float64)[:, None], (1, d.shape[1]))
    for name, m in (("pts", pts), ("view", d), ("tx", txm)):
        if np.abs(m).max() > 1.0 + 1e-9:
            print(f"[warn] {name} 坐标超出 ±1（max={np.abs(m).max():.3f}），"
                  f"量化会饱和 —— 调 --gateway/--radius/--tx")
    return pts, d, txm


# ───────────────────────── tv8_probe.txt（复用 tv3 层记录）─────────────────
def write_probe(tv3_path, out_path, enc_q, golden, idx3):
    """保留 tv3 的 1/3/4/5 行，替换头/2/6 行为 3 个网格探针点"""
    nwe = (63 + P - 1) // P
    keep, header_done = [], False
    for line in open(tv3_path):
        t = line.split(None, 1)
        if not t:
            continue
        if not header_done:
            header_done = True                   # 首行 = 头，丢弃重写
            continue
        if t[0] in ("1", "3", "4", "5"):
            keep.append(line.rstrip("\n"))
    L = [f"{P} {NL} {len(idx3)}"]
    # 头后必须先给 1 行（层配置）再编码 —— 保持 tv3 原有顺序：先 1，后 2，再 3/4/5
    lines_1 = [ln for ln in keep if ln.startswith("1 ")]
    lines_rest = [ln for ln in keep if not ln.startswith("1 ")]
    L += lines_1
    for p, gi in enumerate(idx3):
        for kind in range(3):
            e = enc_q[gi, kind]                  # (63,) int8
            for w in range(nwe):
                by = [int(e[w * P + j]) if w * P + j < 63 else 0
                      for j in range(P)]
                L.append(f"2 {kind} " + " ".join(map(str, by)))
    L += lines_rest
    for p, gi in enumerate(idx3):
        L.append(f"6 {p} " + " ".join(str(int(v)) for v in golden[gi]))
    with open(out_path, "w") as f:
        f.write("\n".join(L) + "\n")


# ───────────────────────── 主流程 ─────────────────────────
def main():
    root = os.path.abspath(os.path.join(_HERE, "..", ".."))
    ap = argparse.ArgumentParser()
    ap.add_argument("--tv", default=os.path.join(root, "test_vectors", "tv3_net.txt"))
    ap.add_argument("--n_azi", type=int, default=90)
    ap.add_argument("--n_ele", type=int, default=45)
    ap.add_argument("--gateway", default="0,0,0")
    ap.add_argument("--radius", type=float, default=0.8)
    ap.add_argument("--tx", default="0.40,-0.10,0.55")
    ap.add_argument("--grid", default="center", choices=["center", "stage1"],
                    help="stage1=复刻 golden_field.py 的 linspace 角度网格")
    ap.add_argument("--out_dir", default=os.path.join(root, "test_vectors"))
    ap.add_argument("--sim_dir", default=os.path.join(root, "sim"))
    args = ap.parse_args()

    gateway = [float(v) for v in args.gateway.split(",")]
    tx = [float(v) for v in args.tx.split(",")]

    # 1) 解析 tv3 + 重建权重
    tv = parse_tv3(args.tv, P)
    W, B, S = rebuild_layers(tv)
    print(f"[gen_field] 权重重建：{NL} 层，shift={S}")

    # 2) 自校验：tv3 自带 enc → finals 必须 == EXP（证明重建+前向与 Stage3 一致）
    lc, enc, _, _, _, EXP, npts, enc_nwe = tv
    e3 = []
    for kind in range(3):
        cols = []
        for pt in range(npts):
            raw = b"".join(enc[pt][kind, w] for w in range(enc_nwe))
            cols.append(np.frombuffer(raw, dtype=np.int8)[:63])
        e3.append(np.stack(cols, axis=1))        # (63, npts)
    fin = forward_fixed_batch(W, S, B, e3)
    exp = np.array(EXP, dtype=np.int8)
    assert np.array_equal(fin, exp), f"self-test FAILED: {fin.tolist()} != {EXP}"
    print(f"[gen_field] self-test OK (finals == EXP: {fin.tolist()})")

    # 3) 网格编码 + 黄金场图
    pts, view, txm = make_grid(args.n_azi, args.n_ele, gateway, args.radius,
                               tx, args.grid)
    npt = pts.shape[1]
    eq = [quantize_enc(positional_encode_batch(m)) for m in (pts, view, txm)]
    golden = forward_fixed_batch(W, S, B, eq)    # (Npts,4) int8
    sig = golden[:, 2].astype(np.int32)
    print(f"[gen_field] 网格 {args.n_ele}x{args.n_azi} = {npt} 点  "
          f"sig_amp: min={sig.min()} max={sig.max()} mean={sig.mean():.1f} "
          f"unique={len(np.unique(sig))}")
    if len(np.unique(sig)) < 4:
        print("[warn] sig_amp 几乎无变化（合成权重下可能发生）——验收判据"
              "（bit 级一致）不受影响；演示效果请换真实权重重跑")

    # 4) 写板端包 npz
    os.makedirs(args.out_dir, exist_ok=True)
    enc_q = np.stack([e.T for e in eq], axis=1)  # (Npts, 3, 63) int8
    npz = os.path.join(args.out_dir, "tv8_field.npz")
    np.savez_compressed(
        npz, enc_q=enc_q, golden=golden,
        n_azi=args.n_azi, n_ele=args.n_ele,
        gateway=np.array(gateway), radius=args.radius, tx=np.array(tx),
        channels=np.array(CH_NAMES))
    print(f"[gen_field] 写出 {npz} ({os.path.getsize(npz)/1024:.0f} KiB)")

    # 5) 3 个探针点（首点/中心/末点）→ tv8_probe.txt（TB4 +FILE= 仿真对拍）
    idx3 = [0, npt // 2, npt - 1]
    probe = os.path.join(args.out_dir, "tv8_probe.txt")
    write_probe(args.tv, probe, enc_q, golden, idx3)
    print(f"[gen_field] 写出 {probe} (探针点 {idx3}，"
          f"EXP={[golden[i].tolist() for i in idx3]})")

    # 6) 黄金场图 npy + png
    os.makedirs(args.sim_dir, exist_ok=True)
    fmap = golden.reshape(args.n_ele, args.n_azi, 4)
    np.save(os.path.join(args.sim_dir, "stage8_field_golden.npy"), fmap)
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(9, 4.2))
        im = ax.imshow(fmap[:, :, 2], origin="lower", aspect="auto",
                       cmap="viridis",
                       extent=[0, 360, 0, 90])
        ax.set_xlabel("azimuth (deg)"); ax.set_ylabel("elevation (deg)")
        ax.set_title(f"N2 golden field sig_amp (int8)  tx={tx}")
        fig.colorbar(im, ax=ax, label="sig_amp")
        png = os.path.join(args.sim_dir, "stage8_field_golden.png")
        fig.savefig(png, dpi=120, bbox_inches="tight")
        print(f"[gen_field] 写出 {png}")
    except ImportError:
        print("[gen_field] matplotlib 不可用，跳过 PNG（npy 已保存）")

    print("[gen_field] DONE")


if __name__ == "__main__":
    main()
