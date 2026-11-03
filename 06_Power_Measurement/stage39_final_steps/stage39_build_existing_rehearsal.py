#!/usr/bin/env python3
"""Bundle and verify the user's already completed Office2 live demonstrations."""
from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from stage39_rehearsal_common import atomic_json, inventory_files, require, validate_report


def copy_run(source_report, destination):
    source = Path(source_report).resolve()
    require(source.is_file(), "report does not exist: %s" % source)
    destination.mkdir(parents=True, exist_ok=False)
    shutil.copy2(source, destination / "report.json")
    for name in ("comparison.png", "pc_fp32.npy", "fpga.npy"):
        candidate = source.parent / name
        if candidate.is_file():
            shutil.copy2(candidate, destination / name)
    return json.loads(source.read_text(encoding="utf-8"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pc-app-root", required=True)
    parser.add_argument("--measured-report", required=True)
    parser.add_argument("--free-report", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    root = Path(args.pc_app_root).resolve()
    registry = json.loads((root / "scene_registry.json").read_text(encoding="utf-8"))
    specs = {row["id"]: row for row in registry["scenes"]}
    destination = Path(args.output).resolve()
    require(not destination.exists(), "output already exists: %s" % destination)
    destination.mkdir(parents=True)
    measured = copy_run(args.measured_report, destination / "01_blind_measured")
    free = copy_run(args.free_report, destination / "02_arbitrary_tx")
    scene = measured.get("scene_id"); require(scene == free.get("scene_id"), "scene mismatch")
    nonce = (measured.get("board") or {}).get("random_ray_challenge", {}).get("nonce")
    require(nonce and nonce == (free.get("board") or {}).get("random_ray_challenge", {}).get("nonce"),
            "the two reports do not share one judge nonce")
    hardware = specs[scene]["hardware"]
    measured_summary = validate_report(
        measured, scene=scene, mode="measured", nonce=nonce,
        bit_sha256=hardware["bit_sha256"], runtime_model_sha256=hardware["runtime_model_sha256"],
        expected_spectrum_id=measured["ground_truth"]["spectrum_id"], challenge_count=16)
    free_summary = validate_report(
        free, scene=scene, mode="free", nonce=nonce,
        bit_sha256=hardware["bit_sha256"], runtime_model_sha256=hardware["runtime_model_sha256"],
        expected_tx=free["tx"], challenge_count=16)
    t0 = datetime.fromisoformat(measured["created_utc"])
    t1 = datetime.fromisoformat(free["created_utc"])
    require(t1 > t0, "free-Tx report is not later than blind report")
    gap = (t1 - t0).total_seconds()
    require(gap <= 300, "reports are more than five minutes apart")
    manifest = {
        "schema": "n2-stage39-continuous-live-rehearsal-v1",
        "status": "DIGITAL_FULL_PASS",
        "source": "existing consecutive Stage39 PC App reports",
        "sequence_start_utc": measured["created_utc"],
        "sequence_end_utc": free["created_utc"],
        "report_timestamp_gap_seconds": gap,
        "single_scene_sequential_evidence": True,
        "scene": scene,
        "judge_nonce": nonce,
        "requirements": {
            "blind_measured_test": True,
            "arbitrary_continuous_tx": True,
            "physical_hdmi_upload_both_runs": True,
            "nonce_challenge_both_runs": True,
            "arm_sparse_reference_bit_exact_both_runs": True,
            "pc_independent_verifier_both_runs": True,
            "negative_control_detected_both_runs": True,
        },
        "runs": [measured_summary, free_summary],
        "camera_recording": {
            "attached": False,
            "limitation": "The machine evidence proves ordering and all digital checks, but does not itself prove an uninterrupted external-camera recording."
        },
    }
    manifest["files"] = inventory_files(destination)
    atomic_json(destination / "manifest.json", manifest)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    print("manifest =", destination / "manifest.json")


if __name__ == "__main__":
    main()

