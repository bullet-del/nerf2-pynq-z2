#!/usr/bin/env python3
"""Stage26 board runner: NB=32 INT8 MLP + Q15 ray renderer, both in PL.

Two NB=32 hardware batches form one NeRF2 ray (64 ordered samples).  The ray
accumulator stays live between the two batches.  DMA receives one Q15 magnitude
instead of 64 raw tuples, removing result-return and PS floating-point renderer
overhead from the measured inference interval.
"""
from __future__ import annotations

import json
import hashlib
import time
from pathlib import Path

import numpy as np
from pynq import MMIO, Overlay, allocate

from n2_batch import batch_enc_beats
from n2_stream import P, layer_beats, parse_tv3, selfcheck, start_beat
from pynq_heatmap import FIELD_WORDS, LUT_BASE, field_hdr, viridis256
from pynq_load_weights import (
    PF_NONE, RawDMA, _find_ip, read_status, soft_reset, st_armed, st_busy,
    st_prefetch, st_widle,
)

NB = 32
SAMPLES_PER_RAY = 64
Q15_ONE = 32768.0
DRIVER_BUILD_ID = "stage26-board-hdmi-v3"
# The inference placement/routing in this v3 artifact is identical to the v2
# bitstream that completed all 128 rays on the real board.  A post-route MMCM
# correction changes only HDMI clocks from 26.25/131.25 to 25/125 MHz.
SUPPORTED_OVERLAYS = {
    (
        "9d67e4d588ffdcd07acf105bef2f090779666b749b3864924e6e20f6dca779ba",
        "6b6862573920d9132e37bbcdce98a5721d07b2499a746cb395ddcee91e8f8e5a",
    ): "stage26-nb32-rayfifo-hdmi-clock-v3",
}

DISPLAY_ROWS = 45
DISPLAY_COLS = 90
PANEL_COLS = 29
HEADER_ROWS = 6
DATA_ROWS = DISPLAY_ROWS - HEADER_ROWS
DISPLAY_BYTES = DISPLAY_ROWS * DISPLAY_COLS


def _free_buffer(buf):
    """Release PYNQ CMA memory on success and on exceptions (safe on old PYNQ)."""
    release = getattr(buf, "freebuffer", None)
    if release is not None:
        release()


def _file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verify_overlay_pair(bit):
    bit_path = Path(bit)
    hwh_path = bit_path.with_suffix(".hwh")
    if not bit_path.is_file() or not hwh_path.is_file():
        raise FileNotFoundError("Stage26 必须同时存在同名 .bit 和 .hwh：%s" % bit_path)
    bit_hash = _file_sha256(bit_path)
    hwh_hash = _file_sha256(hwh_path)
    build_id = SUPPORTED_OVERLAYS.get((bit_hash, hwh_hash))
    if build_id is None:
        raise RuntimeError(
            "检测到旧版或不匹配的 Stage26 bit/hwh。请覆盖解压新版板端包。"
            " bit_sha256=%s hwh_sha256=%s" % (bit_hash, hwh_hash)
        )
    print("[stage26] artifact check PASS:", build_id, "+", DRIVER_BUILD_ID)
    return build_id, bit_hash, hwh_hash


class FullPLRayRunner:
    def __init__(self, overlay, bundle, tv, max_xfer=1 << 20):
        self.gm = MMIO(_find_ip(overlay, "gpio")["phys_addr"], 0x100)
        self.dma = RawDMA(_find_ip(overlay, "dma")["phys_addr"])
        self.max_xfer = max_xfer
        self.z = np.load(bundle, allow_pickle=False)
        self.nrays = len(self.z["ray_indices"])
        if int(self.z["samples_per_ray"]) != SAMPLES_PER_RAY or len(self.z["enc_q"]) != self.nrays * SAMPLES_PER_RAY:
            raise ValueError("bundle is not ordered as nrays x 64 samples")

        parsed = parse_tv3(tv, P)
        _, _, weights, biases, shifts, _, _, _ = parsed
        print("[stage26] tv3 selfcheck:", selfcheck(parsed))
        self.layers = []
        self.layer_nbytes = []
        for layer in range(len(weights)):
            beats = layer_beats(layer, weights, biases, shifts)
            buf = allocate(shape=(len(beats),), dtype=np.uint64)
            buf[:] = np.asarray(beats, np.uint64)
            if hasattr(buf, "flush"):
                buf.flush()
            self.layers.append(buf)
            self.layer_nbytes.append(len(beats) * 8)

        enc = self.z["enc_q"]
        self.nbatches = len(enc) // NB
        if self.nbatches * NB != len(enc) or self.nbatches != self.nrays * 2:
            raise ValueError("Stage26 NB=32 requires exactly two batches per ray")
        first = batch_enc_beats(enc[:NB])
        self.enc_beats = len(first)
        packed = np.empty((self.nbatches, self.enc_beats), np.uint64)
        for bi in range(self.nbatches):
            packed[bi] = batch_enc_beats(enc[bi*NB:(bi+1)*NB])
        self.enc = allocate(shape=(packed.size,), dtype=np.uint64)
        self.enc[:] = packed.ravel()
        if hasattr(self.enc, "flush"):
            self.enc.flush()

        sb = start_beat()
        self.start = allocate(shape=(len(sb),), dtype=np.uint64)
        self.start[:] = np.asarray(sb, np.uint64)
        if hasattr(self.start, "flush"):
            self.start.flush()
        self.rx = allocate(shape=(1,), dtype=np.uint64)
        self.magnitude_q15 = np.zeros(self.nrays, np.uint32)
        self._dma_reset()

    def close(self):
        for buf in self.layers:
            _free_buffer(buf)
        _free_buffer(self.enc)
        _free_buffer(self.start)
        _free_buffer(self.rx)
        close_npz = getattr(self.z, "close", None)
        if close_npz is not None:
            close_npz()

    def _dma_reset(self):
        mm = self.dma.mm
        mm.write(0x00, 0x04)
        mm.write(0x30, 0x04)
        for _ in range(200):
            if not (mm.read(0x00) & 4) and not (mm.read(0x30) & 4):
                break
            time.sleep(0.001)
        mm.write(0x00, 0x01)
        mm.write(0x30, 0x01)

    def _send(self, buf, nbytes, offset=0, timeout=20.0):
        done = 0
        while done < nbytes:
            n = min(self.max_xfer, nbytes - done)
            self.dma.send(buf, n, tmo=timeout, offset=offset + done)
            done += n

    def _serve_layer(self, layer, timeout):
        t0 = time.time()
        while not st_widle(read_status(self.gm)):
            if time.time() - t0 > timeout:
                raise TimeoutError("layer parser did not become idle")
            time.sleep(1e-5)
        self._send(self.layers[layer], self.layer_nbytes[layer], timeout=timeout)

    def _run_batch(self, batch_index, expect_magnitude, timeout):
        served = [False] * len(self.layers)
        self._send(self.enc, self.enc_beats * 8, batch_index * self.enc_beats * 8, timeout)
        started = False
        seen_busy = False
        t0 = time.perf_counter()
        while True:
            status = read_status(self.gm)
            if status & 0x1:
                self._dma_reset()
                raise RuntimeError(
                    "renderer input FIFO overflow/protocol error: status=%#010x" % status
                )
            if status & 0x2:
                self._dma_reset()
                raise RuntimeError("result FIFO overflow: status=%#010x" % status)
            if started and st_busy(status):
                seen_busy = True
            pf = st_prefetch(status)
            if pf != PF_NONE and pf < len(self.layers) and not served[pf]:
                served[pf] = True
                self._serve_layer(pf, timeout)
            elif not started and st_armed(status):
                self.dma.send(self.start, len(self.start) * 8)
                started = True
            elif expect_magnitude and self.dma.recv_done():
                return
            elif (not expect_magnitude) and seen_busy and not st_busy(status) and all(served):
                return
            if time.perf_counter() - t0 > timeout:
                self._dma_reset()
                raise TimeoutError(f"batch {batch_index}: status={status:#010x}, started={started}")

    def run_ray(self, ray_index, timeout=30.0):
        soft_reset(self.gm)                # renderer reset only once per complete ray
        self.dma.arm_recv(self.rx, 8)      # remains armed while the first half has no output
        t0 = time.perf_counter()
        self._run_batch(2*ray_index, False, timeout)
        self._run_batch(2*ray_index+1, True, timeout)
        self.dma.wait_recv(self.rx, tmo=1.0)
        elapsed = time.perf_counter() - t0
        status = read_status(self.gm)
        if status & 0x3:
            raise RuntimeError(f"PL protocol/FIFO error, status={status:#010x}")
        return int(self.rx[0]) & 0xFFFFFFFF, elapsed

    def run_all(self, log_every=16):
        t0 = time.perf_counter()
        for ri in range(self.nrays):
            value, _ = self.run_ray(ri)
            self.magnitude_q15[ri] = value
            if (ri + 1) % log_every == 0:
                elapsed = time.perf_counter() - t0
                print(f"[stage26] {ri+1}/{self.nrays} rays, {elapsed/(ri+1)*1e3:.2f} ms/ray")
        return self.magnitude_q15.astype(np.float64) / Q15_ONE, time.perf_counter() - t0


_LABELS = {
    # The monitor itself identifies the three panels: M=measured, P=PC, F=FPGA.
    "M": ("10001", "11011", "10101", "10001", "10001"),
    "P": ("11110", "10001", "11110", "10000", "10000"),
    "F": ("11111", "10000", "11110", "10000", "10000"),
}


def _validate_map(name, value):
    value = np.asarray(value, dtype=np.float64)
    if value.ndim != 2 or value.size == 0:
        raise ValueError("%s heatmap must be a non-empty 2-D array" % name)
    if not np.isfinite(value).all():
        raise ValueError("%s heatmap contains NaN or Inf" % name)
    return value


def _resize_nearest(value, h=DATA_ROWS, w=PANEL_COLS):
    yi = np.rint(np.linspace(0, value.shape[0] - 1, h)).astype(np.int64)
    xi = np.rint(np.linspace(0, value.shape[1] - 1, w)).astype(np.int64)
    return value[np.ix_(yi, xi)]


def _compose_three_way_frame(measured, pc, fpga):
    """Return the exact 90x45 HDMI index frame and its shared scale."""
    source = [_validate_map(n, x) for n, x in
              (("measured", measured), ("pc", pc), ("fpga", fpga))]
    if source[0].shape != source[1].shape or source[0].shape != source[2].shape:
        raise ValueError("measured, PC and FPGA heatmaps must have the same shape")

    lo = float(min(x.min() for x in source))
    hi = float(max(x.max() for x in source))
    span = max(hi - lo, 1e-12)
    panels = [np.clip(np.rint((_resize_nearest(x) - lo) * 250.0 / span),
                      0, 250).astype(np.uint8) for x in source]

    # 29 + separator + 29 + separator + 29 + right border = exactly 90.
    # Row 0..4 carries M/P/F labels, row 5 is a divider, rows 6..44 are data.
    image = np.zeros((DISPLAY_ROWS, DISPLAY_COLS), dtype=np.uint8)
    starts = (0, 30, 60)
    image[HEADER_ROWS:, 0:29] = panels[0]
    image[HEADER_ROWS:, 30:59] = panels[1]
    image[HEADER_ROWS:, 60:89] = panels[2]
    image[:, (29, 59, 89)] = 255
    image[HEADER_ROWS - 1, :] = 255
    for label, x0 in zip(("M", "P", "F"), starts):
        glyph = _LABELS[label]
        gx = x0 + (PANEL_COLS - len(glyph[0])) // 2
        for gy, row in enumerate(glyph):
            for dx, pixel in enumerate(row):
                if pixel == "1":
                    image[gy, gx + dx] = 255

    if image.shape != (DISPLAY_ROWS, DISPLAY_COLS) or image.nbytes != DISPLAY_BYTES:
        raise AssertionError("internal HDMI frame geometry error: %r" % (image.shape,))
    return image, lo, hi


def _pack_lut(lut):
    lut = np.asarray(lut, dtype=np.uint8)
    if lut.shape != (256, 3):
        raise ValueError("HDMI LUT must have shape (256, 3)")
    words = np.zeros(128, np.uint64)
    for i in range(128):
        a = (int(lut[2*i, 0]) << 16) | (int(lut[2*i, 1]) << 8) | int(lut[2*i, 2])
        b = (int(lut[2*i+1, 0]) << 16) | (int(lut[2*i+1, 1]) << 8) | int(lut[2*i+1, 2])
        words[i] = (b << 32) | a
    return words


def _wait_parser_idle(gm, timeout=2.0):
    t0 = time.perf_counter()
    while not st_widle(read_status(gm)):
        if time.perf_counter() - t0 > timeout:
            raise TimeoutError("HDMI FIELD command did not return the parser to idle")
        time.sleep(1e-4)


def _display_three_way(runner, measured, pc, fpga):
    """Upload M|P|F comparison to physical HDMI and verify both DMA writes."""
    image, lo, hi = _compose_three_way_frame(measured, pc, fpga)
    field = np.zeros(FIELD_WORDS * 8, np.uint8)
    if field.size < DISPLAY_BYTES:
        raise AssertionError("FIELD_WORDS is too small for the 90x45 frame")
    field[:DISPLAY_BYTES] = image.ravel()

    lut = viridis256()
    lut[255] = [255, 255, 255]
    lut_words = _pack_lut(lut)
    tx = allocate(shape=(1 + FIELD_WORDS,), dtype=np.uint64)
    t0 = time.perf_counter()
    try:
        # Compatibility and safety for the already board-proven v2 bitstream:
        # its IDLE input ready was coupled to S2MM ready.  The final ray receive
        # has completed here, so arm one harmless 8-byte sink to keep ready high
        # throughout both input-only FIELD transfers.  FIELD creates no output.
        runner.dma.arm_recv(runner.rx, 8)
        _wait_parser_idle(runner.gm)
        tx[:] = 0
        tx[0] = field_hdr(LUT_BASE, len(lut_words))
        tx[1:1 + len(lut_words)] = lut_words
        if hasattr(tx, "flush"):
            tx.flush()
        runner.dma.send(tx, (1 + len(lut_words)) * 8, tmo=5.0)
        _wait_parser_idle(runner.gm)

        tx[:] = 0
        tx[0] = field_hdr(0, FIELD_WORDS)
        tx[1:] = field.view(np.uint64)
        if hasattr(tx, "flush"):
            tx.flush()
        runner.dma.send(tx, (1 + FIELD_WORDS) * 8, tmo=5.0)
        _wait_parser_idle(runner.gm)
    finally:
        # Cancel the deliberately pending sink before its CMA buffer is freed.
        runner._dma_reset()
        _free_buffer(tx)
    return lo, hi, image, lut, time.perf_counter() - t0


def _write_ppm(path, rgb):
    rgb = np.asarray(rgb, dtype=np.uint8)
    if rgb.ndim != 3 or rgb.shape[2] != 3:
        raise ValueError("PPM image must be HxWx3")
    with Path(path).open("wb") as stream:
        stream.write(("P6\n%d %d\n255\n" % (rgb.shape[1], rgb.shape[0])).encode("ascii"))
        stream.write(rgb.tobytes())


def run(bit="sim/n2_stage26_ray.bit", bundle="test_vectors/stage26_ray/board_ray_8x16_s23_03315.npz",
        tv="test_vectors/stage24_ray/tv3_ray_net.txt",
        three_way="sim/stage26_threeway/s23_03315/three_way_values.npz",
        out="evidence/stage26_full_pl_ray"):
    overlay_build_id, bit_hash, hwh_hash = _verify_overlay_pair(bit)
    out_dir = Path(out)
    out_dir.mkdir(parents=True, exist_ok=True)
    overlay = Overlay(bit)
    runner = FullPLRayRunner(overlay, bundle, tv)
    try:
        fpga, elapsed = runner.run_all()
        z = runner.z
        shape = (int(z["ray_n_ele"]), int(z["ray_n_azi"]))
        if shape[0] * shape[1] != runner.nrays:
            raise ValueError("bundle ray shape does not match ray count")
        expected = z["expected_q15_renderer"].astype(np.float64).reshape(-1)
        if expected.size != runner.nrays:
            raise ValueError("expected_q15_renderer length does not match ray count")
        exact = bool(np.array_equal(runner.magnitude_q15.astype(np.int64),
                                    np.rint(expected * Q15_ONE).astype(np.int64)))
        fpga_map = fpga.reshape(shape)
        np.save(out_dir / "fpga_ray_heatmap.npy", fpga_map)

        with np.load(three_way, allow_pickle=False) as tw:
            missing = {"measured", "pc_fp32"}.difference(tw.files)
            if missing:
                raise KeyError("three-way file is missing: %s" % sorted(missing))
            measured = tw["measured"].reshape(shape)
            pc = tw["pc_fp32"].reshape(shape)
        print("[HDMI] 正在上传三路对比画面：M=实测，P=PC FP32，F=FPGA")
        lo, hi, hdmi_frame, lut, hdmi_seconds = _display_three_way(
            runner, measured, pc, fpga_map)
        np.save(out_dir / "hdmi_three_way_indices.npy", hdmi_frame)
        _write_ppm(out_dir / "hdmi_three_way_preview.ppm", lut[hdmi_frame])
        frame_hash = hashlib.sha256(hdmi_frame.tobytes()).hexdigest()
    finally:
        runner.close()

    report = {
        "schema": "n2-stage26-full-pl-ray-v1",
        "build_id": overlay_build_id,
        "driver_build_id": DRIVER_BUILD_ID,
        "bit_sha256": bit_hash,
        "hwh_sha256": hwh_hash,
        "scope": "PL 13-layer INT8 MLP plus PL Q15 complete 64-sample ray renderer",
        "rays": runner.nrays,
        "mlp_queries": runner.nrays * 64,
        "complete_inference_seconds": elapsed,
        "rays_per_second": runner.nrays / elapsed,
        "mlp_queries_per_second": runner.nrays * 64 / elapsed,
        "projected_90x360_seconds_at_measured_ray_rate": 32400 * elapsed / runner.nrays,
        "q15_ray_results_bit_exact": exact,
        "mae_vs_rtl_sim_expected": float(np.mean(np.abs(fpga - expected))),
        "hdmi_upload_ok": True,
        "hdmi_upload_seconds": hdmi_seconds,
        "hdmi_logical_resolution": [DISPLAY_COLS, DISPLAY_ROWS],
        "hdmi_frame_sha256": frame_hash,
        "hdmi": "M(left)=S23 measured, P(middle)=PC official FP32, F(right)=FPGA; shared colour scale",
        "display_min": lo,
        "display_max": hi,
    }
    (out_dir / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    print("[HDMI] PASS：M=实测，P=PC官方FP32，F=FPGA完整推理；三图共用色标。")
    return report, fpga_map


if __name__ == "__main__":
    run()
