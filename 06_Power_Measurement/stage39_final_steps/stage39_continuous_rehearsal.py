#!/usr/bin/env python3
"""Run blind, arbitrary-Tx, HDMI, ARM and nonce checks as one sequence."""
from __future__ import annotations

import argparse
import json
import random
import secrets
import shutil
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from stage39_rehearsal_common import atomic_json, inventory_files, require, validate_report


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def request_json(url, payload=None, timeout=30):
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=body,
        headers={"Content-Type": "application/json; charset=utf-8"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            value = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError("HTTP %d from %s: %s" % (exc.code, url, detail[:1000])) from exc
    if value.get("ok", True) is False:
        raise RuntimeError(value.get("error", "request failed"))
    return value


def download(url, destination, timeout=60):
    with urllib.request.urlopen(url, timeout=timeout) as response:
        payload = response.read()
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_bytes(payload)
    temporary.replace(destination)


def scene_url(app_url, scene, path):
    return app_url.rstrip("/") + "/scene/" + urllib.parse.quote(scene) + path


def absolute_app_url(app_url, value):
    return urllib.parse.urljoin(app_url.rstrip("/") + "/", value.lstrip("/"))


def poll(app_url, scene, job_id, timeout):
    deadline = time.monotonic() + timeout
    last_phase = None
    while time.monotonic() < deadline:
        value = request_json(scene_url(app_url, scene, "/api/job/" + job_id), timeout=15)
        phase = str(value.get("phase", ""))
        if phase != last_phase:
            print("  %s: %.1f%%" % (phase, 100.0 * float(value.get("progress", 0))), flush=True)
            last_phase = phase
        if value.get("status") == "complete":
            return value
        if value.get("status") in ("error", "paused"):
            raise RuntimeError("job %s ended as %s: %s" %
                               (job_id, value.get("status"), value.get("error", phase)))
        time.sleep(1.0)
    raise TimeoutError("job did not finish within %d seconds" % timeout)


def run_one(app_url, scene, payload, output, timeout, catalog, expected_spectrum_id=None):
    output.mkdir(parents=True, exist_ok=False)
    atomic_json(output / "request.json", payload)
    started = request_json(scene_url(app_url, scene, "/api/run"), payload, timeout=20)
    job = poll(app_url, scene, started["job_id"], timeout)
    atomic_json(output / "job_complete.json", job)
    report_url = absolute_app_url(app_url, job["report_url"])
    comparison_url = absolute_app_url(app_url, job["comparison_url"])
    download(report_url, output / "report.json")
    download(comparison_url, output / "comparison.png")
    report = json.loads((output / "report.json").read_text(encoding="utf-8"))
    require(report == job.get("result"), "downloaded report differs from completed job result")
    hardware = catalog["scene"]["hardware"]
    summary = validate_report(
        report, scene=scene, mode=payload["mode"], nonce=payload["nonce"],
        bit_sha256=hardware["bit_sha256"],
        runtime_model_sha256=hardware["runtime_model_sha256"],
        expected_spectrum_id=expected_spectrum_id,
        expected_tx=payload.get("tx"), challenge_count=payload["challenge_count"])
    atomic_json(output / "verified_summary.json", summary)
    return summary


def choose_free_tx(bounds, candidates):
    rng = random.SystemRandom()
    known = [[float(x) for x in row["tx"]] for row in candidates]
    for _ in range(100):
        tx = []
        for axis in ("x", "y", "z"):
            low, high = (float(x) for x in bounds[axis])
            tx.append(round(low + (high - low) * rng.uniform(0.23, 0.77), 6))
        if all(max(abs(a - b) for a, b in zip(tx, item)) > 1e-4 for item in known):
            return tx
    raise RuntimeError("could not generate a free Tx distinct from the test catalog")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--app-url", default="http://127.0.0.1:8310")
    parser.add_argument("--board-url", default="http://192.168.2.99:8311")
    parser.add_argument("--scene", default="Office2",
                        choices=("s23", "Meetingroom", "Office1", "Office2"))
    parser.add_argument("--output-root", default=str(Path(__file__).parent / "results"))
    parser.add_argument("--timeout-seconds", type=int, default=900)
    parser.add_argument("--nonce", help="omit to generate a cryptographically random nonce")
    args = parser.parse_args()

    start = utc_now(); token = args.nonce or ("judge-" + secrets.token_urlsafe(18))
    require(1 <= len(token) <= 64 and token.isprintable(), "nonce must be 1-64 printable characters")
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ_") + secrets.token_hex(4)
    output = Path(args.output_root).resolve() / ("live_rehearsal_" + run_id)
    output.mkdir(parents=True, exist_ok=False)

    print("Stage39 continuous rehearsal ID:", run_id, flush=True)
    print("Fresh judge nonce:", token, flush=True)
    print("Keep one continuous camera recording on the PC result and physical HDMI screen.", flush=True)
    health = request_json(scene_url(args.app_url, args.scene, "/api/health"), timeout=15)
    catalog = request_json(scene_url(args.app_url, args.scene, "/api/catalog"), timeout=30)
    require(health.get("app_build_id") == "stage39-hardware-exact-qat-four-scene-app-v1",
            "wrong PC App build")
    require(catalog.get("scene_id") == args.scene and catalog.get("fpga_output_mode") == "raw_int8_q15",
            "catalog scene/output mode mismatch")
    candidates = catalog.get("candidates") or []
    require(candidates, "blind-test candidate catalog is empty")
    blind = secrets.choice(candidates)
    free_tx = choose_free_tx(catalog["tx_bounds_m"], candidates)
    atomic_json(output / "preflight.json", {"start_utc": start, "health": health,
                                             "catalog_identity": {
                                                 "scene_id": catalog["scene_id"],
                                                 "catalog_sha256": catalog["catalog_sha256"],
                                                 "test_samples": catalog["test_samples"],
                                                 "tx_bounds_m": catalog["tx_bounds_m"]}})

    common = {"board_url": args.board_url, "nonce": token, "challenge_count": 16,
              "run_arm": True, "show_hdmi": True,
              "hdmi_display_aspect": "four_three_stretch"}
    measured_payload = dict(common, mode="measured", spectrum_id=str(blind["spectrum_id"]))
    print("[1/2] Random blind test sample selected by the script; starting FPGA + ARM + HDMI...", flush=True)
    measured = run_one(args.app_url, args.scene, measured_payload, output / "01_blind_measured",
                       args.timeout_seconds, catalog, str(blind["spectrum_id"]))
    print("[1/2] PASS; revealed spectrum_id =", blind["spectrum_id"], flush=True)

    free_payload = dict(common, mode="free", tx=free_tx)
    print("[2/2] Arbitrary continuous Tx =", free_tx, flush=True)
    free = run_one(args.app_url, args.scene, free_payload, output / "02_arbitrary_tx",
                   args.timeout_seconds, catalog)
    print("[2/2] PASS", flush=True)

    manifest = {
        "schema": "n2-stage39-continuous-live-rehearsal-v1",
        "status": "DIGITAL_FULL_PASS",
        "sequence_id": run_id,
        "sequence_start_utc": start,
        "sequence_end_utc": utc_now(),
        "single_process_sequential_execution": True,
        "scene": args.scene,
        "fresh_random_nonce": token,
        "requirements": {
            "random_blind_test": True,
            "arbitrary_continuous_tx": True,
            "physical_hdmi_upload_both_runs": True,
            "random_nonce_challenge_both_runs": True,
            "arm_sparse_reference_bit_exact_both_runs": True,
            "pc_independent_verifier_both_runs": True,
            "negative_control_detected_both_runs": True,
        },
        "runs": [measured, free],
        "camera_recording": {
            "attached": False,
            "instruction": "Attach the uninterrupted phone video separately; keep PC result and physical HDMI screen in frame."
        },
    }
    manifest["files"] = inventory_files(output)
    atomic_json(output / "manifest.json", manifest)
    print("DIGITAL_FULL_PASS")
    print("Evidence directory =", output)
    print("Manifest =", output / "manifest.json")
    print("Stop the continuous camera recording only after this message is visible.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

