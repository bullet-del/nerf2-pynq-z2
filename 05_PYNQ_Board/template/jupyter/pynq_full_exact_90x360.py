#!/usr/bin/env python3
"""Stage28: exact 90x360 FPGA ray inference and clear 720p HDMI comparison."""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

import numpy as np
from pynq import Overlay, allocate

from n2_batch import batch_enc_beats
from pynq_full_pl_ray import FullPLRayRunner, Q15_ONE, _free_buffer, _verify_overlay_pair
from stage27_display import sampled_metrics
from stage28_exact_common import (
    PrecomputedRayEncoder,
    comparison_metrics,
    compose_clear_three_way,
    save_rgb_png,
    verify_precomputed_against_bundle,
)


DRIVER_BUILD_ID = "stage28-full-exact-90x360-v3-prequantized-input"
ROWS = 90
COLS = 360
RAYS = ROWS * COLS
_HDMI_SESSION = None


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json_atomic(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    os.replace(str(temporary), str(path))


def _stop_saved_hdmi_session():
    """Release an earlier Base Overlay video session before reconfiguration."""
    global _HDMI_SESSION
    if _HDMI_SESSION is not None:
        try:
            _HDMI_SESSION[1].stop()
        except Exception:
            # Reprogramming the PL may already have invalidated the old handle.
            pass
        _HDMI_SESSION = None


class StreamingFullPLRayRunner(FullPLRayRunner):
    """Reuse Stage26 weights and protocol with only one 64-sample ray in CMA."""

    def __init__(self, overlay, sentinel_bundle, tv):
        super().__init__(overlay, sentinel_bundle, tv)
        first_shape = (2, self.enc_beats)
        old = self.enc
        self.enc = allocate(shape=(first_shape[0] * first_shape[1],), dtype=np.uint64)
        self.nbatches = 2
        _free_buffer(old)

    def run_encoded_ray(self, encoded, timeout=30.0):
        encoded = np.asarray(encoded, dtype=np.int8)
        if encoded.shape != (64, 3, 63):
            raise ValueError("one exact ray encoding must have shape (64, 3, 63)")
        first = np.asarray(batch_enc_beats(encoded[:32]), dtype=np.uint64)
        second = np.asarray(batch_enc_beats(encoded[32:]), dtype=np.uint64)
        if len(first) != self.enc_beats or len(second) != self.enc_beats:
            raise AssertionError("encoded batch word count changed")
        self.enc[:self.enc_beats] = first
        self.enc[self.enc_beats:] = second
        if hasattr(self.enc, "flush"):
            self.enc.flush()
        return self.run_ray(0, timeout=timeout)


def _load_reference(reference):
    with np.load(reference, allow_pickle=False) as data:
        required = {
            "gt_full", "pc_full", "ray_origins", "ray_directions", "tx",
            "input_scales", "spectrum_id", "test_output_index", "samples_per_ray",
        }
        missing = required.difference(data.files)
        if missing:
            raise KeyError("exact reference is missing: %s" % sorted(missing))
        values = {key: np.array(data[key], copy=True) for key in required}
    if values["gt_full"].shape != (ROWS, COLS) or values["pc_full"].shape != (ROWS, COLS):
        raise ValueError("GT and PC references must both be exact 90x360 arrays")
    if values["ray_origins"].shape != (RAYS, 3) or values["ray_directions"].shape != (RAYS, 3):
        raise ValueError("reference must contain all 32400 official rays")
    if int(values["samples_per_ray"]) != 64:
        raise ValueError("Stage28 requires exactly 64 samples per ray")
    return values


def _new_or_resume_map(output, identity, force_restart):
    work_path = output / "fpga_full_q15_work.npy"
    progress_path = output / "progress.json"
    if force_restart or not (work_path.is_file() and progress_path.is_file()):
        result = np.lib.format.open_memmap(
            str(work_path), mode="w+", dtype=np.uint32, shape=(ROWS, COLS)
        )
        result[:] = 0
        result.flush()
        progress = dict(identity)
        progress.update({
            "completed_rows": 0,
            "completed_rays": 0,
            "end_to_end_seconds": 0.0,
            "pl_transaction_seconds": 0.0,
            "complete": False,
        })
        _write_json_atomic(progress_path, progress)
        return result, progress, work_path, progress_path

    progress = json.loads(progress_path.read_text(encoding="utf-8"))
    mismatches = [key for key, expected in identity.items() if progress.get(key) != expected]
    if mismatches:
        stamp = time.strftime("%Y%m%d_%H%M%S")
        archived_work = output / ("fpga_full_q15_work_incompatible_%s.npy" % stamp)
        archived_progress = output / ("progress_incompatible_%s.json" % stamp)
        os.replace(str(work_path), str(archived_work))
        os.replace(str(progress_path), str(archived_progress))
        print(
            "[Stage28] 检测到旧版断点（%s），已保留备份并自动从0行启动v3。" %
            ",".join(mismatches)
        )
        return _new_or_resume_map(output, identity, True)
    rows = int(progress.get("completed_rows", -1))
    if rows < 0 or rows > ROWS or int(progress.get("completed_rays", -1)) != rows * COLS:
        raise RuntimeError("invalid Stage28 resume progress")
    result = np.lib.format.open_memmap(str(work_path), mode="r+")
    if result.shape != (ROWS, COLS) or result.dtype != np.uint32:
        raise RuntimeError("invalid Stage28 resume array")
    return result, progress, work_path, progress_path


def _display_base_overlay(rgb):
    """Switch to the standard PYNQ base overlay and send one 1280x720 RGB frame."""
    global _HDMI_SESSION
    rgb = np.asarray(rgb, dtype=np.uint8)
    if rgb.shape != (720, 1280, 3):
        raise ValueError("HDMI RGB frame must have shape (720, 1280, 3)")
    from pynq.lib.video import PIXEL_RGB, VideoMode
    from pynq.overlays.base import BaseOverlay

    # BaseOverlay is the documented PYNQ-Z2 entry point and resolves the
    # board-installed base.bit/base.hwh pair independently of the notebook CWD.
    _stop_saved_hdmi_session()
    base = BaseOverlay("base.bit")
    if not hasattr(base, "video") or not hasattr(base.video, "hdmi_out"):
        raise RuntimeError("the installed PYNQ base.bit has no video.hdmi_out hierarchy")
    hdmi = base.video.hdmi_out
    hdmi.configure(VideoMode(1280, 720, 24), PIXEL_RGB)
    hdmi.start()
    frame = hdmi.newframe()
    if frame.shape != rgb.shape:
        raise RuntimeError("base HDMI frame shape is %r, expected %r" % (frame.shape, rgb.shape))
    frame[:] = rgb
    hdmi.writeframe(frame)
    _HDMI_SESSION = (base, hdmi)
    return {"mode": [1280, 720, 24], "pixel_format": "PIXEL_RGB", "base_overlay": "base.bit"}


def show_saved_hdmi(out="evidence/stage28_full_exact", style="official"):
    """Redisplay an already completed result without rerunning 32400 rays."""
    from PIL import Image
    if style not in ("official", "shared"):
        raise ValueError("style must be 'official' or 'shared'")
    path = Path(out) / (style + "_hdmi_1280x720.png")
    if not path.is_file():
        raise FileNotFoundError("saved HDMI image is missing: %s" % path)
    return _display_base_overlay(np.asarray(Image.open(path).convert("RGB"), dtype=np.uint8))


def run_quick_check(
    bit="sim/n2_stage26_ray.bit",
    bundle="test_vectors/stage27_official_compare/board_ray_16x32_s23_03315.npz",
    tv="test_vectors/stage24_ray/tv3_ray_net.txt",
    reference="sim/stage27_official_compare/s23_03315/reference_s23_03315.npz",
    out="evidence/stage28_quick_check",
):
    """Run 512 complete rays as a live check; never mark it as full-exact evidence."""
    from pynq_official_compare import run as run_sampled

    Path(out).mkdir(parents=True, exist_ok=True)

    report, fpga = run_sampled(
        bit=bit, bundle=bundle, tv=tv, reference=reference, out=out,
        color_mode="shared",
    )
    with np.load(reference, allow_pickle=False) as data:
        gt, pc = data["gt_full"], data["pc_full"]
    rgb, _ = compose_clear_three_way(gt, pc, fpga, 1280, 720, "official")
    destination = Path(out) / "quick_check_hdmi_1280x720.png"
    save_rgb_png(destination, rgb)
    hdmi = _display_base_overlay(rgb)
    report["formal_full_exact_evidence"] = False
    report["quick_check_only"] = True
    report["angular_interpolation_used"] = True
    report["high_resolution_hdmi"] = hdmi
    _write_json_atomic(Path(out) / "report.json", report)
    print("[Stage28 QUICK-CHECK] PASS：硬件实时自检完成；该结果不能替代32400射线正式证据。")
    return report, fpga


def run(
    bit="sim/n2_stage26_ray.bit",
    sentinel_bundle="test_vectors/stage27_official_compare/board_ray_16x32_s23_03315.npz",
    tv="test_vectors/stage24_ray/tv3_ray_net.txt",
    reference="sim/stage27_official_compare/s23_03315/reference_s23_03315.npz",
    encoding_manifest="test_vectors/stage28_full_exact/manifest.json",
    encoding_rows_dir="test_vectors/stage28_full_exact/enc_rows",
    out="evidence/stage28_full_exact",
    force_restart=False,
    display_style="official",
    show_hdmi=True,
):
    """Run or resume all 32400 FPGA rays; no angular interpolation is used."""
    if display_style not in ("official", "shared"):
        raise ValueError("display_style must be 'official' or 'shared'")
    output = Path(out)
    output.mkdir(parents=True, exist_ok=True)
    reference_values = _load_reference(reference)
    reference_hash = _sha256(reference)
    bundle_hash = _sha256(sentinel_bundle)
    identity = {
        "schema": "n2-stage28-progress-v1",
        "reference_sha256": reference_hash,
        "sentinel_bundle_sha256": bundle_hash,
        "spectrum_id": str(reference_values["spectrum_id"]),
        "rows": ROWS,
        "cols": COLS,
    }

    encoder = PrecomputedRayEncoder(encoding_manifest, encoding_rows_dir, reference_hash)
    identity["encoding_manifest_sha256"] = encoder.manifest_sha256
    with np.load(sentinel_bundle, allow_pickle=False) as sentinels:
        encoder_check = verify_precomputed_against_bundle(encoder, sentinels)
        sentinel_indices = sentinels["ray_indices"].astype(np.int64)
        sentinel_expected_q15 = np.rint(
            sentinels["expected_q15_renderer"].astype(np.float64) * Q15_ONE
        ).astype(np.uint32)
    if not encoder_check["bit_exact"]:
        raise AssertionError("prequantized full inputs differ from supplied Stage24 encoding")
    print("[Stage28] encoder selfcheck PASS:", encoder_check)

    result, progress, work_path, progress_path = _new_or_resume_map(
        output, identity, bool(force_restart)
    )
    first_row = int(progress["completed_rows"])
    prior_wall = float(progress["end_to_end_seconds"])
    prior_pl = float(progress["pl_transaction_seconds"])
    overlay_build_id = None
    bit_hash = _sha256(bit)
    hwh_hash = _sha256(Path(bit).with_suffix(".hwh"))

    if first_row < ROWS:
        _stop_saved_hdmi_session()
        overlay_build_id, checked_bit, checked_hwh = _verify_overlay_pair(bit)
        if checked_bit != bit_hash or checked_hwh != hwh_hash:
            raise AssertionError("overlay hash verification returned inconsistent values")
        print("[Stage28] FULL-EXACT 从第 %d 行开始；共90行，每行360条完整射线。" % first_row)
        runner = StreamingFullPLRayRunner(Overlay(bit), sentinel_bundle, tv)
        wall_start = time.perf_counter()
        new_pl = 0.0
        try:
            for row in range(first_row, ROWS):
                encoded_row = encoder.load_row(row)
                row_start = time.perf_counter()
                for column in range(COLS):
                    value, transaction_seconds = runner.run_encoded_ray(encoded_row[column])
                    result[row, column] = np.uint32(value)
                    new_pl += transaction_seconds
                result.flush()
                elapsed_now = time.perf_counter() - wall_start
                progress.update({
                    "completed_rows": row + 1,
                    "completed_rays": (row + 1) * COLS,
                    "end_to_end_seconds": prior_wall + elapsed_now,
                    "pl_transaction_seconds": prior_pl + new_pl,
                    "last_row_seconds": time.perf_counter() - row_start,
                    "complete": row + 1 == ROWS,
                })
                _write_json_atomic(progress_path, progress)
                remaining = ROWS - row - 1
                print(
                    "[Stage28] %d/90 rows, %d/32400 rays, last row %.2fs, ETA %.1f min" %
                    (row + 1, (row + 1) * COLS, progress["last_row_seconds"],
                     remaining * progress["last_row_seconds"] / 60.0)
                )
        finally:
            runner.close()
    else:
        overlay_build_id = "reused-complete-result"
        print("[Stage28] 已存在完整32400射线结果；跳过重算并直接生成比较/HDMI。")

    if int(progress["completed_rows"]) != ROWS:
        raise RuntimeError("Stage28 stopped before all 32400 rays completed; rerun to resume")
    q15_full = np.asarray(result, dtype=np.uint32).copy()
    fpga = q15_full.astype(np.float64) / Q15_ONE
    np.save(output / "fpga_full_exact_q15.npy", q15_full)
    np.save(output / "fpga_full_exact_90x360.npy", fpga)

    actual_sentinels = q15_full.reshape(-1)[sentinel_indices]
    sentinel_exact = bool(np.array_equal(actual_sentinels, sentinel_expected_q15))
    sentinel_different = int(np.count_nonzero(actual_sentinels != sentinel_expected_q15))
    if not sentinel_exact:
        raise AssertionError("completed full map failed distributed Q15 sentinel comparison")

    gt = reference_values["gt_full"].astype(np.float64)
    pc = reference_values["pc_full"].astype(np.float64)
    official_hdmi, official_scales = compose_clear_three_way(
        gt, pc, fpga, 1280, 720, "official"
    )
    shared_hdmi, shared_scales = compose_clear_three_way(
        gt, pc, fpga, 1280, 720, "shared"
    )
    save_rgb_png(output / "official_hdmi_1280x720.png", official_hdmi)
    save_rgb_png(output / "shared_hdmi_1280x720.png", shared_hdmi)
    clear_official, _ = compose_clear_three_way(gt, pc, fpga, 3000, 1100, "official")
    clear_shared, _ = compose_clear_three_way(gt, pc, fpga, 3000, 1100, "shared")
    save_rgb_png(output / "official_three_way_full_resolution.png", clear_official)
    save_rgb_png(output / "shared_three_way_full_resolution.png", clear_shared)

    metrics = {
        "gt_vs_pc_full_exact_90x360": comparison_metrics(gt, pc),
        "pc_vs_fpga_full_exact_90x360": comparison_metrics(pc, fpga),
        "gt_vs_fpga_full_exact_90x360": comparison_metrics(gt, fpga),
    }
    report = {
        "schema": "n2-stage28-board-full-exact-v1",
        "driver_build_id": DRIVER_BUILD_ID,
        "overlay_build_id": overlay_build_id,
        "bit_sha256": bit_hash,
        "hwh_sha256": hwh_hash,
        "official_repository": "https://github.com/XPengZhao/NeRF2",
        "official_commit": "130b55ea3856aa2f5a42f9329cfa2e845c78e243",
        "spectrum_id": str(reference_values["spectrum_id"]),
        "direct_test_image": "pred_spectrum/%d.png" % int(reference_values["test_output_index"]),
        "data_resolution": [ROWS, COLS],
        "fpga_hardware_rays": RAYS,
        "samples_per_ray": 64,
        "mlp_queries": RAYS * 64,
        "angular_interpolation_used": False,
        "display_interpolation_used": False,
        "input_encoding_source": "host-prequantized deterministic row shards",
        "platform_runtime_sin_used": False,
        "encoding_manifest_sha256": encoder.manifest_sha256,
        "every_fpga_pixel_is_a_complete_hardware_ray": True,
        "encoder_check": encoder_check,
        "distributed_q15_sentinel_check": {
            "rays_checked": int(len(sentinel_indices)),
            "different_rays": sentinel_different,
            "bit_exact": sentinel_exact,
        },
        "timing": {
            "end_to_end_seconds": float(progress["end_to_end_seconds"]),
            "pl_transaction_seconds_sum": float(progress["pl_transaction_seconds"]),
            "rays_per_second_end_to_end": RAYS / float(progress["end_to_end_seconds"]),
            "resume_was_used": first_row > 0,
            "reused_already_complete_result": first_row == ROWS,
        },
        "metrics": metrics,
        "images": {
            "official_style_full_resolution": "official_three_way_full_resolution.png",
            "shared_scale_full_resolution": "shared_three_way_full_resolution.png",
            "official_style_hdmi": "official_hdmi_1280x720.png",
            "shared_scale_hdmi": "shared_hdmi_1280x720.png",
        },
        "hdmi": {
            "requested": bool(show_hdmi),
            "style": display_style,
            "resolution": [1280, 720],
            "official_scales": [[float(a), float(b)] for a, b in official_scales],
            "shared_scales": [[float(a), float(b)] for a, b in shared_scales],
            "uses_pynq_base_overlay_ddr_framebuffer": True,
            "note": "base.bit is loaded only after the custom inference overlay has finished and results are saved",
        },
    }
    _write_json_atomic(output / "report.json", report)

    if show_hdmi:
        frame = official_hdmi if display_style == "official" else shared_hdmi
        hdmi_info = _display_base_overlay(frame)
        report["hdmi"]["upload_ok"] = True
        report["hdmi"]["driver"] = hdmi_info
        _write_json_atomic(output / "report.json", report)
        print("[HDMI] PASS：1280x720 清晰显示 GT | PC | FPGA。")
    print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
    print("[Stage28] PASS：32400/32400 个FPGA像素均为完整64采样点硬件射线，无插值。")
    return report, fpga


if __name__ == "__main__":
    run()
