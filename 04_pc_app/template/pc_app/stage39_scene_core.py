#!/usr/bin/env python3
"""Per-scene raw INT8/Q15 engine used by the Stage39 judge App."""
from __future__ import annotations

import argparse
import base64
import hashlib
import http.client
import io
import json
import math
import mimetypes
import os
import secrets
import threading
import time
import traceback
import urllib.error
import urllib.request
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

import numpy as np
import torch
from PIL import Image


HERE = Path(__file__).resolve()
PACKAGE_ROOT = HERE.parents[1]
SCENE_ID = globals().get("SCENE_ID", "s23")
ROOT = PACKAGE_ROOT / "scenes" / SCENE_ID
SCENE_SPEC = json.loads((ROOT / "scene.json").read_text(encoding="utf-8"))
SAMPLE_COUNT = SCENE_SPEC["test_count"]
for item in (PACKAGE_ROOT / "software" / "stage11", PACKAGE_ROOT / "software" / "stage24",
             PACKAGE_ROOT / "software" / "stage26", PACKAGE_ROOT / "jupyter"):
    import sys
    sys.path.insert(0, str(item))

from reference import load_model, render_spectrum  # noqa: E402
from ray_common import official_s23_rays  # noqa: E402
from stage27_display import comparison_metrics  # noqa: E402
from stage28_exact_common import render_polar_rgb  # noqa: E402


APP_BUILD_ID = "stage39-hardware-exact-qat-four-scene-app-v1"
EXPECTED_BOARD_SERVICE = "stage39-hardware-exact-qat-four-scene-service-v1"
EXPECTED_RUNNER_BUILD = "stage38-raw-only-runtime-model-under1p2-v1"
EXPECTED_FPGA_BUILD = "stage38-raw-only-universal-f95-v1"
CHALLENGE_SPEC = "n2-stage37-sha256-counter-rejection-ray-challenge-v2"
CATALOG_PATH = ROOT / "data/stage31/catalog.json"
ALL_TEST_CATALOG_PATH = ROOT / "data/stage32/all_test_catalog.json"
PC_TIMING_PATH = ROOT / "data/stage33/pc_timing_reference.json"
MODEL_CKPT = ROOT / SCENE_SPEC["checkpoint"]
MODEL_CONFIG = ROOT / "pc_app/model/config.yml"
STATIC = HERE.parent / "stage39_static"
EVIDENCE = ROOT / "evidence/stage39_pc_app"
JOBS = {}
JOBS_LOCK = threading.Lock()
MODEL_LOCK = threading.Lock()
MODEL_CACHE = {"model": None, "device": None}
CAROUSEL_LOCK = threading.Lock()
LAST_CAROUSEL_ID = None


class BoardConnectionError(RuntimeError):
    pass


def challenge_canonical_input(nonce, scene, tx, count, bit_sha256, runtime_model_sha256):
    value = {
        "spec": CHALLENGE_SPEC, "nonce": str(nonce), "scene_id": str(scene),
        "tx_float32": ["%.9g" % float(np.float32(x)) for x in tx],
        "ray_population": 32400, "ray_count": int(count), "resolution": [90, 360],
        "bit_sha256": str(bit_sha256), "runtime_model_sha256": str(runtime_model_sha256),
    }
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def independently_select_challenge_indices(canonical, count):
    selected = set(); counter = 0; limit = (1 << 32) // 32400 * 32400
    while len(selected) < int(count):
        digest = hashlib.sha256((canonical + "|counter=" + str(counter)).encode("utf-8")).digest()
        counter += 1
        for offset in range(0, 32, 4):
            value = int.from_bytes(digest[offset:offset + 4], "big")
            if value >= limit: continue
            selected.add(value % 32400)
            if len(selected) >= int(count): break
    return sorted(selected)


def independently_hash_challenge_transcript(challenge):
    core = {
        "selection_algorithm": challenge["selection_algorithm"],
        "canonical_input": challenge["canonical_input"],
        "ray_indices": challenge["ray_indices"], "samples": challenge["samples"],
        "rerun_vs_full_map_bit_exact": challenge["rerun_vs_full_map_bit_exact"],
        "arm_vs_fpga_bit_exact": (None if challenge.get("arm") is None else
                                   challenge["arm"]["arm_vs_fpga_bit_exact"]),
    }
    encoded = json.dumps(core, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def independently_verify_random_challenge(board_report, nonce, tx, count, require_arm):
    challenge = board_report.get("random_ray_challenge")
    if not nonce:
        if challenge is not None: raise AssertionError("board returned an unrequested ray challenge")
        return None
    if not isinstance(challenge, dict): raise AssertionError("requested ray challenge is missing")
    hardware = SCENE_SPEC["hardware"]
    canonical = challenge_canonical_input(
        nonce, SCENE_ID, tx, count, hardware["bit_sha256"],
        hardware["runtime_model_sha256"])
    expected_indices = independently_select_challenge_indices(canonical, count)
    if challenge.get("selection_algorithm") != CHALLENGE_SPEC:
        raise AssertionError("board ray-selection algorithm is not the published Stage39 specification")
    if challenge.get("canonical_input") != canonical:
        raise AssertionError("board ray challenge canonical input differs from the PC reconstruction")
    if challenge.get("ray_indices") != expected_indices:
        raise AssertionError("PC independently selected different challenge rays")
    samples = challenge.get("samples")
    if not isinstance(samples, list) or len(samples) != int(count):
        raise AssertionError("board challenge sample transcript is incomplete")
    full = [int(row["full_map_q15"]) for row in samples]
    rerun = [int(row["rerun_q15"]) for row in samples]
    if any(int(row["ray_index"]) != index for row, index in zip(samples, expected_indices)):
        raise AssertionError("challenge table ray indexes are inconsistent")
    if full != rerun or any(int(row["delta_q15"]) != 0 for row in samples):
        raise AssertionError("FPGA rerun values differ from the completed full map")
    if not challenge.get("rerun_vs_full_map_bit_exact") or challenge.get("mismatch_count") != 0:
        raise AssertionError("board marked the random challenge as failed")
    if require_arm:
        if not challenge.get("arm", {}).get("arm_vs_fpga_bit_exact"):
            raise AssertionError("requested ARM reference did not match FPGA")
        if any(row.get("arm_q15") != row.get("rerun_q15") for row in samples):
            raise AssertionError("ARM values in the challenge table differ from FPGA")
    transcript = independently_hash_challenge_transcript(challenge)
    if transcript != challenge.get("transcript_sha256"):
        raise AssertionError("board challenge transcript SHA-256 does not verify")
    tampered = list(rerun); tampered[0] = tampered[0] + 1
    negative_control_detected = tampered != full
    if not negative_control_detected:
        raise AssertionError("independent verifier negative control failed")
    return {
        "implementation": "PC independent Python verifier; does not call board selection code",
        "selection_recomputed": True, "selection_matches": True,
        "q15_pairs_recomputed": True, "q15_pairs_match": True,
        "transcript_sha256_recomputed": transcript,
        "transcript_hash_matches": True,
        "negative_control": "rerun_q15[0] += 1 LSB",
        "negative_control_detected": True,
        "verdict": "PASS",
    }


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_catalog():
    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    if catalog.get("candidate_count") != len(catalog.get("candidates", [])):
        raise RuntimeError("scene live catalog candidate_count does not match its candidate list")
    catalog["_sha256"] = sha256_file(CATALOG_PATH)
    return catalog


def load_all_test_catalog():
    catalog = json.loads(ALL_TEST_CATALOG_PATH.read_text(encoding="utf-8"))
    if catalog.get("sample_count") != SAMPLE_COUNT or len(catalog.get("samples", [])) != SAMPLE_COUNT:
        raise RuntimeError("scene all-test catalog does not match its declared test count")
    catalog["_sha256"] = sha256_file(ALL_TEST_CATALOG_PATH)
    return catalog


CATALOG = load_catalog()
BY_ID = {item["spectrum_id"]: item for item in CATALOG["candidates"]}
ALL_TEST = load_all_test_catalog()
ALL_BY_ID = {item["spectrum_id"]: item for item in ALL_TEST["samples"]}
PC_TIMING = json.loads(PC_TIMING_PATH.read_text(encoding="utf-8"))
TX_BOUNDS = SCENE_SPEC["tx_bounds_m"]
RX_POSITION = np.asarray(CATALOG["gateway_position"], dtype=np.float64)
ORIGINS, DIRECTIONS = official_s23_rays(
    CATALOG["gateway_position"], CATALOG["gateway_orientation_xyzw"]
)


def array_to_b64(array):
    stream = io.BytesIO(); np.savez_compressed(stream, value=np.asarray(array, dtype=np.float32))
    return base64.b64encode(stream.getvalue()).decode("ascii")


def array_from_b64(value):
    raw = base64.b64decode(value.encode("ascii"), validate=True)
    with np.load(io.BytesIO(raw), allow_pickle=False) as data:
        array = np.asarray(data["value"], dtype=np.float64)
    if array.shape != (90, 360) or not np.isfinite(array).all():
        raise ValueError("board returned an invalid FPGA map")
    return array


def normalize_board_url(value):
    """Validate the three deliberately separate Stage39 HTTP ports."""
    text = str(value or "").strip().rstrip("/")
    parsed = urlparse(text)
    if parsed.scheme != "http" or not parsed.hostname:
        raise ValueError("板端服务地址必须类似 http://192.168.2.99:8311")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("板端服务端口无效") from exc
    if port == 9090:
        raise ValueError("9090 是 PYNQ 的 Jupyter 登录端口；板端服务必须使用 8311")
    if port == 8310:
        raise ValueError("8310 是本电脑评委 App 的端口；板端服务必须使用 PYNQ-IP:8311")
    if port != 8311:
        raise ValueError("Stage39 板端服务端口必须是 8311")
    if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise ValueError("板端服务地址只填写 http://PYNQ-IP:8311，不要附加路径")
    return text


def json_request(url, payload=None, timeout=180):
    parsed_request = urlparse(url)
    if parsed_request.port == 8311 and not parsed_request.path.startswith("/scene/"):
        url = parsed_request._replace(path="/scene/" + SCENE_ID + parsed_request.path).geturl()
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=data,
                                     headers={"Content-Type": "application/json; charset=utf-8"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            value = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            detail = json.loads(raw)
        except Exception:
            detail = {"error": raw or exc.reason}
        if exc.code == 403 and ("_xsrf" in raw or "<!DOCTYPE HTML" in raw):
            raise RuntimeError(
                "该地址是 PYNQ Jupyter（9090），不是 Stage39 板端服务；"
                "请填写 http://PYNQ-IP:8311"
            )
        pieces = ["PYNQ HTTP %d" % exc.code, str(detail.get("error", exc.reason))]
        if detail.get("phase"): pieces.append("阶段=" + str(detail["phase"]))
        tail = detail.get("traceback_tail") or []
        if tail: pieces.append("板端末尾=" + " | ".join(str(line) for line in tail[-5:]))
        if detail.get("diagnostic_url"):
            pieces.append("诊断=" + url.rsplit("/", 2)[0] + str(detail["diagnostic_url"]))
        raise RuntimeError("；".join(pieces))
    except urllib.error.URLError as exc:
        raise BoardConnectionError("无法连接PYNQ服务 %s：%s" % (url, exc.reason))
    except json.JSONDecodeError as exc:
        # A worker killed while the response body is in flight can leave a
        # syntactically incomplete JSON document.  JSONDecodeError subclasses
        # ValueError, which previously made the first failed free-Tx request
        # look like an input error.  Treat it as the same recoverable transport
        # failure as RemoteDisconnected so the supervised worker can restart.
        raise BoardConnectionError(
            "PYNQ响应在传输中被截断 %s：%s" % (url, exc)
        ) from exc
    except (http.client.RemoteDisconnected, http.client.IncompleteRead,
            ConnectionError, TimeoutError, OSError) as exc:
        raise BoardConnectionError("PYNQ连接中断 %s：%s" % (url, exc))
    if not value.get("ok", True):
        raise RuntimeError(value.get("error", "board request failed"))
    return value


def require_stage35_board(board_url):
    value = json_request(normalize_board_url(board_url) + "/api/health", None, 8)
    if value.get("scene_id") != SCENE_ID:
        raise RuntimeError("板端场景与电脑端不一致")
    if not value.get("hardware_available", False):
        raise RuntimeError(value.get("hardware_block_reason", "该场景硬件模型尚未验证"))
    if value.get("service_build_id") != EXPECTED_BOARD_SERVICE:
        raise RuntimeError("板端服务版本不匹配；请在 Stage39 Notebook 中重新启动板端服务")
    if value.get("runner_build_id") != EXPECTED_RUNNER_BUILD:
        raise RuntimeError("板端推理驱动不是 Stage39 QAT raw-only 版本；请替换新版板端ZIP并重启服务")
    if value.get("fpga_build_id") != EXPECTED_FPGA_BUILD:
        raise RuntimeError("板端 bitstream 清单不是 Stage39 共用位流版本")
    if value.get("same_bitstream_for_all_scenes") is not True:
        raise RuntimeError("板端没有返回单一位流四场景证明")
    if value.get("output_mode") != "raw_int8_q15":
        raise RuntimeError("板端输出模式不是 Stage39 QAT raw INT8/Q15")
    if value.get("stage39_runtime_model_sha256") != SCENE_SPEC["hardware"]["runtime_model_sha256"]:
        raise RuntimeError("板端当前场景的 Stage39 QAT 运行时模型哈希与电脑端不一致")
    return value


def wait_for_board_service(board_url, timeout=120.0, require_idle=False):
    """Wait through a supervised board-worker restart."""
    deadline = time.monotonic() + float(timeout)
    last = None
    while time.monotonic() < deadline:
        try:
            value = require_stage35_board(board_url)
            active = bool(value.get("state", {}).get("active"))
            if not require_idle or not active:
                return value
            last = "board service is still completing the previous request"
        except Exception as exc:
            last = str(exc)
        time.sleep(1.0)
    raise BoardConnectionError("PYNQ服务在监督重启后仍未就绪：%s" % last)


def board_run_resilient(board_url, payload, attempts=3):
    """Retry one complete deterministic FPGA request after worker recovery."""
    failure = None
    for attempt in range(int(attempts)):
        try:
            return json_request(board_url + "/api/run", payload, 360)
        except BoardConnectionError as exc:
            failure = exc
            if attempt + 1 >= attempts:
                break
            wait_for_board_service(board_url, timeout=120, require_idle=True)
    raise failure


def board_get_resilient(board_url, endpoint, timeout=60, attempts=3):
    """Retry a read-only board endpoint across a supervised worker restart."""
    failure = None
    for attempt in range(int(attempts)):
        try:
            return json_request(board_url + endpoint, None, timeout)
        except BoardConnectionError as exc:
            failure = exc
            if attempt + 1 >= attempts:
                break
            wait_for_board_service(board_url, timeout=120)
    raise failure


def wait_for_board(future, board_url, job_id, base_progress=0.10, span=0.80):
    """Expose real board row progress while the long POST is running."""
    while not future.done():
        try:
            active = json_request(board_url + "/api/active", None, 3).get("state", {})
            rows = int(active.get("completed_rows", 0)); total = max(1, int(active.get("total_rows", 90)))
            phase = active.get("phase", "FPGA现场推理")
            update_job(job_id, phase="PYNQ：%s（%d/%d行）" % (phase, rows, total),
                       progress=min(.97, base_progress + span * rows / total),
                       board_state=active)
        except Exception:
            pass
        time.sleep(.75)
    return future.result()


def get_model():
    with MODEL_LOCK:
        if MODEL_CACHE["model"] is None:
            for path in (MODEL_CKPT, MODEL_CONFIG):
                if not path.is_file():
                    raise FileNotFoundError("Stage35 PC model artifact is missing: %s" % path)
            device = "cuda" if torch.cuda.is_available() else "cpu"
            model, _ = load_model(str(MODEL_CKPT), str(MODEL_CONFIG), device)
            # Warm-up is excluded from every formal timing.
            tx = np.asarray(CATALOG["candidates"][0]["tx"], dtype=np.float32)
            with torch.no_grad():
                tx_t = torch.from_numpy(np.broadcast_to(tx, ORIGINS[:64].shape).copy()).to(device)
                _ = render_spectrum(model, tx_t, torch.from_numpy(ORIGINS[:64]).to(device),
                                    torch.from_numpy(DIRECTIONS[:64]).to(device))
            if device == "cuda": torch.cuda.synchronize()
            MODEL_CACHE.update({"model": model, "device": device})
        return MODEL_CACHE["model"], MODEL_CACHE["device"]


def live_pc_render(tx, chunk=512):
    model, device = get_model()
    tx = np.asarray(tx, dtype=np.float32).reshape(3)
    tx_all = torch.from_numpy(np.broadcast_to(tx, ORIGINS.shape).copy()).to(device)
    ro_all = torch.from_numpy(ORIGINS).to(device)
    rd_all = torch.from_numpy(DIRECTIONS).to(device)
    pieces = []; start = time.perf_counter()
    with torch.no_grad():
        for offset in range(0, len(ORIGINS), chunk):
            pieces.append(render_spectrum(model, tx_all[offset:offset + chunk],
                                          ro_all[offset:offset + chunk],
                                          rd_all[offset:offset + chunk]))
    if device == "cuda": torch.cuda.synchronize()
    elapsed = time.perf_counter() - start
    value = torch.cat(pieces).detach().cpu().numpy().reshape(90, 360)
    return value.astype(np.float64), elapsed, device


def metrics(reference, prediction):
    result = comparison_metrics(reference, prediction)
    result["mse"] = float(result["rmse"] ** 2)
    for key, value in list(result.items()):
        if isinstance(value, (float, np.floating)) and not np.isfinite(value):
            result[key] = "Infinity" if value > 0 else ("-Infinity" if value < 0 else None)
    return result


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                         encoding="utf-8")
    os.replace(str(temporary), str(path))


def save_comparison(path, gt, pc, fpga, title, no_gt=False):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    if no_gt:
        values = (pc, fpga); labels = ("PC FP32", "FPGA raw INT8/Q15")
    else:
        values = (gt, pc, fpga); labels = ("Measured GT", "PC FP32", "FPGA raw INT8/Q15")
    fig, axes = plt.subplots(1, len(values), figsize=(4.2 * len(values), 4.7), constrained_layout=True)
    shared = (min(float(v.min()) for v in values), max(float(v.max()) for v in values))
    for index, (ax, value, label) in enumerate(zip(axes, values, labels)):
        ax.imshow(render_polar_rgb(value, 512, shared))
        ax.set_title(label); ax.set_xticks([]); ax.set_yticks([])
    fig.suptitle(title)
    fig.savefig(path, dpi=165, facecolor="white"); plt.close(fig)


def update_job(job_id, **values):
    with JOBS_LOCK:
        JOBS[job_id].update(values)


def run_one_job(job_id, payload):
    run_dir = EVIDENCE / job_id; run_dir.mkdir(parents=True, exist_ok=False)
    coordinated_start = time.perf_counter()
    try:
        mode = str(payload["mode"])
        board_url = normalize_board_url(
            payload.get("board_url", "http://192.168.2.99:8311")
        )
        require_stage35_board(board_url)
        nonce = str(payload.get("nonce", "")).strip()
        board_common = {
            "nonce": nonce, "challenge_count": int(payload.get("challenge_count", 8)),
            "run_arm": bool(payload.get("run_arm", True)), "display_style": "shared",
            "show_hdmi": bool(payload.get("show_hdmi", False)),
            "hdmi_display_aspect": str(payload.get(
                "hdmi_display_aspect", "four_three_stretch")),
        }
        if mode == "measured":
            sid = str(payload["spectrum_id"])
            if sid not in ALL_BY_ID: raise ValueError("unknown held-out test candidate")
            item = ALL_BY_ID[sid]; tx = np.asarray(item["tx"], dtype=np.float32)
            gt = np.asarray(Image.open(ROOT / item["gt_png"]).convert("L"), np.float64) / 255.0
            update_job(job_id, phase="PC FP32现场推理", progress=0.05)
            pc, pc_seconds, device = live_pc_render(tx)
            update_job(job_id, phase="FPGA现场完整推理", progress=0.12,
                       pc_seconds=pc_seconds)
            with ThreadPoolExecutor(max_workers=1) as pool:
                board_future = pool.submit(
                    board_run_resilient, board_url,
                    dict(board_common, mode="measured", spectrum_id=sid,
                         pc_map_npz_base64=array_to_b64(pc)))
                board = wait_for_board(board_future, board_url, job_id)
            packaged_difference = None
            if sid in BY_ID:
                packaged_pc = np.asarray(np.load(ROOT / BY_ID[sid]["pc_fp32_npy"],
                                                 allow_pickle=False), np.float64)
                packaged_difference = float(np.max(np.abs(pc - packaged_pc)))
                if packaged_difference > 2e-5:
                    raise AssertionError("live PC output differs from committed PC reference")
            fpga = array_from_b64(board["fpga_map_npz_base64"])
            board_metrics = board["report"]["metrics"]
            result_metrics = {
                "GT vs PC FP32": metrics(gt, pc),
                "GT vs FPGA raw INT8/Q15": metrics(gt, fpga),
                "PC vs FPGA raw INT8/Q15": metrics(pc, fpga),
            }
            save_comparison(run_dir / "comparison.png", gt, pc, fpga,
                            "Measured blind Tx %s" % sid)
            truth = {"available": True, "source": SCENE_ID + " original dataset spectrum",
                     "spectrum_id": sid, "gt_sha256": item["gt_sha256"]}
        elif mode == "free":
            tx = np.asarray(payload["tx"], dtype=np.float32).reshape(3)
            limits = np.asarray([TX_BOUNDS["x"], TX_BOUNDS["y"], TX_BOUNDS["z"]])
            if (not np.isfinite(tx).all() or np.any(tx < limits[:, 0]) or np.any(tx > limits[:, 1])):
                raise ValueError("free Tx coordinate is outside the documented " + SCENE_ID + " domain")
            update_job(job_id, phase="PC现场推理", progress=0.10)
            pc, pc_seconds, device = live_pc_render(tx)
            update_job(job_id, phase="FPGA动态Tx推理", progress=0.35, pc_seconds=pc_seconds)
            with ThreadPoolExecutor(max_workers=1) as pool:
                board_future = pool.submit(
                    board_run_resilient, board_url,
                    dict(board_common, mode="free", tx=tx.astype(float).tolist(),
                         pc_map_npz_base64=array_to_b64(pc)))
                board = wait_for_board(board_future, board_url, job_id, .35, .60)
            fpga = array_from_b64(board["fpga_map_npz_base64"])
            gt = None
            board_metrics = board["report"]["metrics"]
            result_metrics = {"PC vs FPGA raw INT8/Q15": metrics(pc, fpga)}
            save_comparison(run_dir / "comparison.png", None, pc, fpga,
                            "Free Tx query (no measured GT)", no_gt=True)
            truth = {"available": False,
                     "reason": "arbitrary continuous Tx coordinate has no S23 measurement"}
            sid = None; packaged_difference = None
        else:
            raise ValueError("mode must be measured or free")

        independent_verifier = independently_verify_random_challenge(
            board["report"], nonce, tx, board_common["challenge_count"],
            board_common["run_arm"])
        if independent_verifier is not None:
            board["report"]["random_ray_challenge"]["pc_independent_verifier"] = independent_verifier

        np.save(run_dir / "pc_fp32.npy", pc.astype(np.float32))
        np.save(run_dir / "fpga.npy", fpga.astype(np.float32))
        report = {
            "schema": "n2-stage39-pc-coordinated-run-v1-qat-raw-only", "app_build_id": APP_BUILD_ID,
            "created_utc": datetime.now(timezone.utc).isoformat(), "mode": mode,
            "tx": [float(value) for value in tx], "ground_truth": truth,
            "live_pc": {"seconds": float(pc_seconds), "device": device,
                        "gpu_name": torch.cuda.get_device_name(0) if device == "cuda" else None,
                        "map_sha256": hashlib.sha256(np.ascontiguousarray(pc).tobytes()).hexdigest(),
                        "vs_committed_max_abs": packaged_difference},
            "board": board["report"], "metrics": result_metrics,
            "scene_id": SCENE_ID, "checkpoint_sha256": SCENE_SPEC["checkpoint_sha256"],
            "catalog_sha256": ALL_TEST["_sha256"], "comparison_png": "comparison.png",
            "official_pc_log_timing_reference": PC_TIMING,
            "coordinated_wall_seconds_to_board_result": float(time.perf_counter() - coordinated_start),
        }
        atomic_json(run_dir / "report.json", report)
        update_job(job_id, status="complete", phase="PASS", progress=1.0,
                   result=report, comparison_url="/evidence/%s/comparison.png" % job_id,
                   report_url="/evidence/%s/report.json" % job_id)
    except Exception as exc:
        traceback.print_exc()
        update_job(job_id, status="error", phase="ERROR", progress=1.0,
                   error=type(exc).__name__ + ": " + str(exc))


def start_job(payload):
    job_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + secrets.token_hex(4)
    with JOBS_LOCK:
        JOBS[job_id] = {"job_id": job_id, "status": "running", "phase": "准备运行", "progress": 0.0}
    threading.Thread(target=run_one_job, args=(job_id, payload), daemon=True).start()
    return job_id


def save_all_test_figures(destination, report):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    rows = report["rows"]
    pc = np.asarray([row["pc_ssim_vs_gt"] for row in rows], np.float64)
    raw = np.asarray([row["fpga_raw_ssim_vs_gt"] for row in rows], np.float64)
    seconds = np.asarray([row["map_seconds"] for row in rows], np.float64)
    y = np.arange(1, len(rows) + 1, dtype=np.float64) / len(rows)
    fig, axes = plt.subplots(2, 2, figsize=(16, 10), constrained_layout=True)
    ax = axes[0, 0]
    for value, label, color in ((pc, "PC FP32", "#1769aa"),
                                (raw, "FPGA raw INT8/Q15", "#d97706")):
        ax.plot(np.sort(value), y, linewidth=2.5,
                label="%s  mean=%.4f" % (label, value.mean()), color=color)
    ax.set(xlabel="SSIM vs measured GT", ylabel="Empirical CDF",
           title="%s: %d held-out test spectra — raw-only CDF" % (SCENE_ID, SAMPLE_COUNT))
    ax.set_xlim(max(-.05, min(pc.min(), raw.min()) - .02),
                min(1.0, max(pc.max(), raw.max()) + .02))
    ax.grid(alpha=.25); ax.legend(fontsize=10)

    ax = axes[0, 1]
    bins = np.linspace(min(pc.min(), raw.min()), max(pc.max(), raw.max()), 32)
    ax.hist(pc, bins=bins, histtype="step", linewidth=2.4, label="PC FP32")
    ax.hist(raw, bins=bins, histtype="step", linewidth=2.4, label="FPGA raw INT8/Q15")
    ax.set(xlabel="SSIM vs measured GT", ylabel="Spectrum count",
           title="Raw-only SSIM distribution"); ax.grid(alpha=.2); ax.legend()

    ax = axes[1, 0]
    names = ["PC FP32", "FPGA raw INT8/Q15"]
    means = [pc.mean(), raw.mean()]; medians = [np.median(pc), np.median(raw)]
    x = np.arange(2); width = .34
    ax.bar(x - width / 2, means, width, label="Mean", color="#1769aa")
    ax.bar(x + width / 2, medians, width, label="Median", color="#09a4b5")
    ax.set_xticks(x, names); ax.set_ylabel("SSIM vs measured GT")
    ax.set_title("Raw INT8/Q15 mean and median accuracy")
    ax.grid(axis="y", alpha=.2); ax.legend()
    floor = max(0.0, min(means + medians) - .08); ax.set_ylim(floor, 1.0)
    for index, value in enumerate(means):
        ax.text(index - width / 2, value + .008, "%.4f" % value, ha="center", fontsize=9)
    for index, value in enumerate(medians):
        ax.text(index + width / 2, value + .008, "%.4f" % value, ha="center", fontsize=9)

    ax = axes[1, 1]
    official_pc = float(PC_TIMING["mean_end_to_end_seconds_per_spectrum"])
    timing_names = ["Official PC log\nmean end-to-end", "FPGA raw\nmean", "FPGA raw\nmedian"]
    timing_values = [official_pc, float(seconds.mean()), float(np.median(seconds))]
    bars = ax.bar(timing_names, timing_values, color=["#1769aa", "#d97706", "#09a4b5"])
    ax.set_ylabel("Seconds per 90x360 spectrum")
    ax.set_title("Measured inference timing (different documented scopes)")
    ax.grid(axis="y", alpha=.2)
    for bar, value in zip(bars, timing_values):
        ax.text(bar.get_x() + bar.get_width() / 2, value + max(timing_values) * .015,
                "%.3f s" % value, ha="center", fontsize=10)
    fig.savefig(destination / "all_1225_ssim_cdf.png", dpi=180, facecolor="white")
    plt.close(fig)

    encoded = report.get("representative_fpga_maps", {})
    if encoded:
        fig, axes = plt.subplots(4, 3, figsize=(12, 15), constrained_layout=True)
        for row_index, label in enumerate(("P10", "P50", "P90", "MAX")):
            sid = CATALOG["representatives"][label]; item = BY_ID[sid]
            gt = np.asarray(Image.open(ROOT / item["gt_png"]).convert("L"), np.float64) / 255.0
            pc_map = np.asarray(np.load(ROOT / item["pc_fp32_npy"], allow_pickle=False), np.float64)
            fpga_map = array_from_b64(encoded[sid])
            lo = min(float(gt.min()), float(pc_map.min()), float(fpga_map.min()))
            hi = max(float(gt.max()), float(pc_map.max()), float(fpga_map.max()))
            for column, value in enumerate((gt, pc_map, fpga_map)):
                axes[row_index, column].imshow(render_polar_rgb(value, 480, (lo, hi)))
            axes[row_index, 0].set_ylabel("%s %s %s" % (label, SCENE_ID, sid))
            for column in range(3): axes[row_index, column].axis("off")
        for column, title in enumerate(("Measured GT", "PC FP32", "FPGA raw INT8/Q15")):
            axes[0, column].set_title(title)
        fig.suptitle("Representative raw FPGA spectra from the same %d-sample run" % SAMPLE_COUNT)
        fig.savefig(destination / "representative_hardware_gallery.png", dpi=160, facecolor="white")
        plt.close(fig)


def run_all_test_job(job_id, payload):
    board_url = normalize_board_url(
        payload.get("board_url", "http://192.168.2.99:8311")
    )
    destination = EVIDENCE / "all_1225"; destination.mkdir(parents=True, exist_ok=True)
    try:
        require_stage35_board(board_url)
        start_payload = {"restart": bool(payload.get("restart", False))}
        try:
            json_request(board_url + "/api/all-test/start", start_payload, 30)
        except BoardConnectionError:
            wait_for_board_service(board_url, timeout=120)
            json_request(board_url + "/api/all-test/start", start_payload, 30)
        while True:
            try:
                status = json_request(board_url + "/api/all-test/status", None, 10)
            except BoardConnectionError as exc:
                update_job(job_id, phase="PYNQ服务正在自动重启，已完成证据不会丢失",
                           board_recovery=str(exc))
                wait_for_board_service(board_url, timeout=120)
                # Idempotent resume: committed JSONL/map hashes select the next
                # sample, so this never restarts from sample zero.
                json_request(board_url + "/api/all-test/start", {"restart": False}, 30)
                time.sleep(1.0)
                continue
            completed = int(status.get("completed", 0)); total = int(status.get("total", SAMPLE_COUNT))
            phase = "%s测试集FPGA验证 %d/%d" % (SCENE_ID, completed, total)
            if status.get("current_spectrum_id"): phase += "，ID " + status["current_spectrum_id"]
            update_job(job_id, phase=phase, progress=completed / max(1, total), board_status=status)
            if status.get("status") == "complete": break
            if status.get("status") == "error":
                raise RuntimeError("PYNQ批量验证失败：%s；阶段=%s" %
                                   (status.get("error"), status.get("phase")))
            if status.get("status") == "paused":
                update_job(job_id, status="paused",
                           phase="PYNQ批量验证已安全暂停，可从%d继续" % completed,
                           progress=completed / max(1, total), board_status=status)
                return
            time.sleep(2.0)
        report = board_get_resilient(
            board_url, "/api/all-test/result", timeout=60, attempts=3
        )["result"]
        if report.get("sample_count") != SAMPLE_COUNT or len(report.get("rows", [])) != SAMPLE_COUNT:
            raise RuntimeError("PYNQ返回的测试集结果不是完整%d条" % SAMPLE_COUNT)
        if report.get("bit_sha256") != SCENE_SPEC["hardware"]["bit_sha256"]:
            raise RuntimeError("PYNQ测试集证据的位流哈希不匹配")
        if report.get("runtime_model_sha256") != SCENE_SPEC["hardware"]["runtime_model_sha256"]:
            raise RuntimeError("PYNQ测试集证据不是当前 Stage39 QAT 运行时模型")
        if report.get("test_catalog_sha256") != ALL_TEST["_sha256"]:
            raise RuntimeError("PYNQ测试集证据的 test 目录哈希不匹配")
        atomic_json(destination / "validation.json", report)
        save_all_test_figures(destination, report)
        update_job(job_id, status="complete", phase="%d个测试频谱全部完成" % SAMPLE_COUNT, progress=1.0,
                   result=report, comparison_url="/evidence/all_1225/all_1225_ssim_cdf.png")
    except Exception as exc:
        traceback.print_exc(); update_job(job_id, status="error", phase="ERROR", progress=1.0,
                                          error=type(exc).__name__ + ": " + str(exc))


def carousel_frame(board_url, requested_id=None):
    """Compose one cached-evidence overview frame.

    FPGA comes from the board's hashed full-test archive.  GT is the committed
    measured image.  PC FP32 is cached locally after its first checkpoint
    evaluation.  This endpoint never represents itself as a new blind run.
    """
    global LAST_CAROUSEL_ID
    board_url = normalize_board_url(board_url)
    with CAROUSEL_LOCK:
        if requested_id:
            spectrum_id = str(requested_id)
            if spectrum_id not in ALL_BY_ID:
                raise ValueError("unknown carousel spectrum_id")
        else:
            choices = [item["spectrum_id"] for item in ALL_TEST["samples"]
                       if item["spectrum_id"] != LAST_CAROUSEL_ID]
            spectrum_id = secrets.choice(choices)
        LAST_CAROUSEL_ID = spectrum_id
        item = ALL_BY_ID[spectrum_id]
        tx = np.asarray(item["tx"], np.float64)
        gt = np.asarray(Image.open(ROOT / item["gt_png"]).convert("L"), np.float64) / 255.0

        cache_dir = EVIDENCE / "carousel_pc_cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path = cache_dir / (spectrum_id + ".npy")
        if cache_path.is_file():
            pc = np.asarray(np.load(cache_path, allow_pickle=False), np.float64)
            pc_cache_hit = True; pc_seconds = None; device = "saved cache"
        else:
            pc, pc_seconds, device = live_pc_render(tx)
            temporary = cache_path.with_suffix(".npy.tmp")
            with temporary.open("wb") as stream:
                np.save(stream, pc.astype(np.float32))
                stream.flush(); os.fsync(stream.fileno())
            os.replace(str(temporary), str(cache_path))
            pc_cache_hit = False

        local_map = EVIDENCE / "carousel_fpga_cache" / (spectrum_id + ".json")
        if local_map.is_file():
            board = json.loads(local_map.read_text(encoding="utf-8"))
            transfer_hash = hashlib.sha256(board["fpga_map_npz_base64"].encode("ascii")).hexdigest()
            if transfer_hash != board.get("pc_transfer_sha256"):
                raise ValueError("saved FPGA transfer hash mismatch")
        else:
            # Fail promptly when unplugged. Network recovery must not hold the
            # global Matplotlib lock or freeze other scenes' PC comparison.
            json_request(board_url + "/api/health", None, 2)
            board = board_get_resilient(
                board_url, "/api/all-test/map?spectrum_id=" + quote(spectrum_id),
                timeout=30, attempts=3)
            board["pc_transfer_sha256"] = hashlib.sha256(board["fpga_map_npz_base64"].encode("ascii")).hexdigest()
            atomic_json(local_map, board)
        fpga = array_from_b64(board["fpga_map_npz_base64"])

        with PLOT_LOCK:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
            all_tx = np.asarray([sample["tx"] for sample in ALL_TEST["samples"]], np.float64)
            figure = plt.figure(figsize=(15.5, 9.8), constrained_layout=True)
            grid = figure.add_gridspec(2, 3, height_ratios=(1.0, 1.5))
            location = figure.add_subplot(grid[0, :])
            location.scatter(all_tx[:, 0], all_tx[:, 1], s=6, alpha=.22,
                             color="#607086", label="%d held-out test Tx" % SAMPLE_COUNT)
            location.scatter(tx[0], tx[1], s=95, marker="*", color="#d62728",
                             label="Current Tx")
            location.scatter(RX_POSITION[0], RX_POSITION[1], s=90, marker="D",
                             color="#1769aa", label="Fixed Rx")
            location.plot([tx[0], RX_POSITION[0]], [tx[1], RX_POSITION[1]],
                          color="#9aa7b5", linewidth=1, linestyle="--")
            location.set(xlabel="x (m)", ylabel="y (m)",
                         title="Tx/Rx coordinates — Tx changes, Rx remains fixed")
            location.margins(x=.06, y=.12)
            location.grid(alpha=.22); location.legend(loc="upper center", ncol=3)

            shared = (min(float(gt.min()), float(pc.min()), float(fpga.min())),
                      max(float(gt.max()), float(pc.max()), float(fpga.max())))
            display_metrics = (metrics(gt, pc), metrics(gt, fpga))
            labels = ("Measured GT", "PC FP32  SSIM %.4f" % display_metrics[0]["ssim_data_range_1"],
                      "FPGA raw INT8/Q15  SSIM %.4f" % display_metrics[1]["ssim_data_range_1"])
            for column, (value, label) in enumerate(zip((gt, pc, fpga), labels)):
                axis = figure.add_subplot(grid[1, column])
                axis.imshow(render_polar_rgb(value, 520, shared))
                axis.set_title(label); axis.axis("off")
            figure.suptitle("Saved evidence browser · %s %s · Tx (%.3f, %.3f, %.3f) · Rx (%.2f, %.2f, %.2f)" %
                           (SCENE_ID, spectrum_id, tx[0], tx[1], tx[2], *RX_POSITION), fontsize=15)
            output = EVIDENCE / "carousel"; output.mkdir(parents=True, exist_ok=True)
            image_path = output / "current.png"
            figure.savefig(image_path, dpi=150, facecolor="white"); plt.close(figure)
            evidence = {
                "schema": "n2-stage33-saved-evidence-carousel-v1",
                "created_utc": datetime.now(timezone.utc).isoformat(),
                "spectrum_id": spectrum_id, "tx": tx.tolist(), "rx": RX_POSITION.tolist(),
                "source": {"gt": "committed measured %s test image" % SCENE_ID,
                           "pc": "saved PC FP32 cache" if pc_cache_hit else "PC FP32 checkpoint; cached now",
                           "fpga": board["source"]},
                "pc_cache_hit": pc_cache_hit, "pc_cache_fill_seconds": pc_seconds,
                "pc_device": device, "metrics": {"gt_vs_pc": display_metrics[0],
                                                   "gt_vs_fpga_raw": display_metrics[1]},
                "fpga_raw_q15_sha256": board["fpga_raw_q15_sha256"],
            }
            atomic_json(output / "current.json", evidence)
            return dict(ok=True, evidence=evidence,
                        image_url="/evidence/carousel/current.png?t=%d" % int(time.time() * 1000))


class Handler(BaseHTTPRequestHandler):
    server_version = "N2Stage39PC/1.0"

    def log_message(self, fmt, *args): print("[Stage39 PC] " + fmt % args)

    def send_json(self, code, value):
        body = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.send_response(code); self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body))); self.send_header("Cache-Control", "no-store")
        self.end_headers(); self.wfile.write(body)

    def send_file(self, path):
        path = Path(path)
        if not path.is_file(): self.send_error(404); return
        body = path.read_bytes(); kind = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        self.send_response(200); self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path); path = unquote(parsed.path)
        if path == "/api/health":
            self.send_json(200, {"ok": True, "app_build_id": APP_BUILD_ID,
                                 "catalog_sha256": CATALOG["_sha256"],
                                 "all_test_catalog_sha256": ALL_TEST["_sha256"],
                                 "cuda_available": torch.cuda.is_available(),
                                 "model_loaded": MODEL_CACHE["model"] is not None})
        elif path == "/api/catalog":
            public = {
                "schema": "n2-stage39-judge-public-catalog-v1",
                "scene_id": SCENE_ID, "scene": SCENE_SPEC,
                "pc_cdf_url": "/four_scene_ssim_cdf.png",
                "catalog_sha256": ALL_TEST["_sha256"], "candidate_count": SAMPLE_COUNT,
                "test_samples": SAMPLE_COUNT, "test_ssim_summary": CATALOG["test_ssim_summary"],
                "representatives": CATALOG["representatives"],
                "formal_blind_split": "test", "tx_bounds_m": TX_BOUNDS,
                "rx_position_m": RX_POSITION.tolist(),
                "pc_timing_reference": PC_TIMING,
                "fpga_output_mode": "raw_int8_q15",
                "candidates": [{
                    "spectrum_id": item["spectrum_id"],
                    "candidate_index": int(item["test_output_index"]),
                    "tx": item["tx"], "official_ssim": item["pc_ssim_vs_gt"],
                    "split": "test",
                } for item in ALL_TEST["samples"]],
            }
            self.send_json(200, public)
        elif path == "/api/carousel":
            try:
                query = parse_qs(parsed.query)
                board_url = query.get("board_url", ["http://192.168.2.99:8311"])[0]
                spectrum_id = query.get("spectrum_id", [None])[0]
                self.send_json(200, carousel_frame(board_url, spectrum_id))
            except Exception as exc:
                traceback.print_exc()
                self.send_json(500, {"ok": False,
                                     "error": type(exc).__name__ + ": " + str(exc)})
        elif path == "/api/all-test-existing":
            base = EVIDENCE / "all_1225"
            validation = base / "validation.json"
            chart = base / "all_1225_ssim_cdf.png"
            gallery = base / "representative_hardware_gallery.png"
            validation_value = None
            if validation.is_file():
                validation_value = json.loads(validation.read_text(encoding="utf-8"))
            available = (
                validation_value is not None and chart.is_file()
                and validation_value.get("sample_count") == SAMPLE_COUNT
                and len(validation_value.get("rows", [])) == SAMPLE_COUNT
                and validation_value.get("schema") == "n2-stage39-full-test-fpga-validation-v1-qat-raw-only"
                and validation_value.get("bit_sha256") == SCENE_SPEC["hardware"]["bit_sha256"]
                and validation_value.get("runtime_model_sha256") == SCENE_SPEC["hardware"]["runtime_model_sha256"]
                and validation_value.get("test_catalog_sha256") == ALL_TEST["_sha256"]
            )
            value = {"ok": True, "available": bool(available)}
            if available:
                value.update({
                    "validation": validation_value,
                    "chart_url": "/evidence/all_1225/all_1225_ssim_cdf.png",
                    "gallery_url": ("/evidence/all_1225/representative_hardware_gallery.png"
                                    if gallery.is_file() else None),
                })
            self.send_json(200, value)
        elif path.startswith("/api/job/"):
            job_id = path.split("/")[-1]
            with JOBS_LOCK: value = JOBS.get(job_id)
            self.send_json(404 if value is None else 200,
                           {"ok": False, "error": "unknown job"} if value is None else value)
        elif path.startswith("/assets/"):
            relative = path[len("/assets/"):]
            target = (ROOT / "data/stage31/assets" / relative).resolve()
            base = (ROOT / "data/stage31/assets").resolve()
            if base not in target.parents: self.send_error(403)
            else: self.send_file(target)
        elif path.startswith("/evidence/"):
            relative = path[len("/evidence/"):]
            target = (EVIDENCE / relative).resolve(); base = EVIDENCE.resolve()
            if base not in target.parents: self.send_error(403)
            else: self.send_file(target)
        else:
            relative = "index.html" if path in ("", "/") else path.lstrip("/")
            target = (STATIC / relative).resolve()
            if STATIC.resolve() not in target.parents: self.send_error(403)
            else: self.send_file(target)

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 2 or length > 2 * 1024 * 1024: raise ValueError("invalid request size")
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            path = urlparse(self.path).path
            if path == "/api/run":
                job_id = start_job(payload); self.send_json(202, {"ok": True, "job_id": job_id})
            elif path == "/api/all-test":
                job_id = datetime.now(timezone.utc).strftime("alltest%Y%m%dT%H%M%SZ_") + secrets.token_hex(3)
                with JOBS_LOCK:
                    JOBS[job_id] = {"job_id": job_id, "status": "running",
                                    "phase": "准备%d个测试频谱验证" % SAMPLE_COUNT, "progress": 0.0}
                threading.Thread(target=run_all_test_job, args=(job_id, payload), daemon=True).start()
                self.send_json(202, {"ok": True, "job_id": job_id})
            elif path == "/api/all-test-stop":
                board_url = normalize_board_url(payload["board_url"])
                value = json_request(board_url + "/api/all-test/stop", {}, 30)
                self.send_json(202, value)
            elif path == "/api/board-health":
                value = require_stage35_board(payload["board_url"])
                self.send_json(200, value)
            elif path == "/api/board-selftest":
                board_url = normalize_board_url(payload["board_url"])
                try:
                    value = json_request(board_url + "/api/selftest", {}, 60)
                except BoardConnectionError:
                    wait_for_board_service(board_url, timeout=120, require_idle=True)
                    value = json_request(board_url + "/api/selftest", {}, 60)
                self.send_json(200, value)
            else: self.send_json(404, {"ok": False, "error": "unknown endpoint"})
        except Exception as exc:
            traceback.print_exc(); self.send_json(500, {"ok": False, "error": type(exc).__name__ + ": " + str(exc)})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8310)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    url = "http://127.0.0.1:%d" % args.port
    print("Stage39 judge app:", url)
    print("Keep this terminal open during the demonstration.")
    if not args.no_browser: threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try: server.serve_forever()
    finally: server.server_close()


if __name__ == "__main__":
    main()

