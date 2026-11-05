#!/usr/bin/env python3
"""Verify all external artifacts, then invoke the immutable Stage39 finalizer."""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from stage39_rehearsal_common import atomic_json, require, sha256_file


SCENES = ("s23", "Meetingroom", "Office1", "Office2")


def validate_physical_export(path):
    path = Path(path)
    require(path.is_file(), "physical evidence ZIP does not exist: %s" % path)
    with zipfile.ZipFile(path) as archive:
        require(archive.testzip() is None, "physical evidence ZIP is corrupt")
        manifest = json.loads(archive.read("physical_evidence_manifest.json").decode("utf-8"))
    contains = manifest.get("contains_full_test_summaries") or {}
    missing = [scene for scene in SCENES if contains.get(scene) is not True]
    require(not missing, "physical evidence ZIP lacks all-test results for: " + ", ".join(missing))
    return manifest


def validate_rehearsal(path):
    path = Path(path).resolve()
    value = json.loads(path.read_text(encoding="utf-8"))
    require(value.get("schema") == "n2-stage39-continuous-live-rehearsal-v1",
            "live rehearsal schema mismatch")
    require(value.get("status") == "DIGITAL_FULL_PASS", "live rehearsal did not digitally PASS")
    requirements = value.get("requirements") or {}
    require(requirements and all(item is True for item in requirements.values()),
            "one or more live rehearsal requirements did not PASS")
    modes = {row.get("mode") for row in value.get("runs", [])}
    require(modes == {"measured", "free"}, "rehearsal must include measured and free modes")
    for entry in value.get("files", []):
        target = path.parent / entry["path"]
        require(target.is_file(), "rehearsal evidence file missing: " + entry["path"])
        require(target.stat().st_size == entry["bytes"], "rehearsal evidence size mismatch")
        require(sha256_file(target) == entry["sha256"], "rehearsal evidence hash mismatch")
    return value


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pc-app-root", required=True)
    parser.add_argument("--physical-export", required=True)
    parser.add_argument("--power-csv", required=True)
    parser.add_argument("--rehearsal-manifest", required=True)
    parser.add_argument("--video-file", help="optional uninterrupted external-camera recording")
    parser.add_argument("--user-observed-hdmi", action="store_true",
                        help="human witness confirms both physical HDMI outputs were seen")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    root = Path(args.pc_app_root).resolve()
    physical = Path(args.physical_export).resolve()
    power = Path(args.power_csv).resolve()
    rehearsal_path = Path(args.rehearsal_manifest).resolve()
    require(root.is_dir(), "PC App root does not exist")
    require(power.is_file(), "80-row power CSV does not exist")
    physical_manifest = validate_physical_export(physical)
    rehearsal = validate_rehearsal(rehearsal_path)

    video = Path(args.video_file).resolve() if args.video_file else None
    if video is not None:
        require(video.is_file() and video.stat().st_size >= 100000,
                "camera video is missing or unexpectedly small")
    require(video is not None or args.user_observed_hdmi,
            "attach the uninterrupted camera video or pass --user-observed-hdmi after witnessing both HDMI outputs")

    canonical_zip = root / "Stage39_physical_evidence.zip"
    canonical_power = root / "stage39_board_input_power.csv"
    if physical != canonical_zip:
        shutil.copy2(physical, canonical_zip)
    if power != canonical_power:
        shutil.copy2(power, canonical_power)
    finalizer = root / "software/stage39/finalize_stage39_evidence.py"
    command = [sys.executable, str(finalizer), "--physical-export", str(canonical_zip),
               "--power-csv", str(canonical_power), "--live-rehearsal-pass"]
    completed = subprocess.run(command, cwd=str(root), text=True, capture_output=True)
    print(completed.stdout)
    if completed.stderr:
        print(completed.stderr, file=sys.stderr)
    require(completed.returncode == 0, "immutable Stage39 finalizer did not return FULL_PASS")
    report_path = root / "stage39_reports/stage39_physical_finalization.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    require(report.get("status") == "FULL_PASS" and report.get("claims_allowed") is True,
            "final Stage39 report is not FULL_PASS")

    bundle = {
        "schema": "n2-stage39-verified-final-acceptance-bundle-v1",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "status": "FULL_PASS",
        "claims_allowed": True,
        "artifacts": {
            "physical_export": {"path": str(canonical_zip), "sha256": sha256_file(canonical_zip),
                                "manifest_created_utc": physical_manifest.get("created_utc")},
            "power_csv": {"path": str(canonical_power), "sha256": sha256_file(canonical_power)},
            "live_rehearsal": {"path": str(rehearsal_path), "sha256": sha256_file(rehearsal_path),
                               "sequence_start_utc": rehearsal.get("sequence_start_utc"),
                               "sequence_end_utc": rehearsal.get("sequence_end_utc")},
            "camera_video": (None if video is None else
                              {"path": str(video), "bytes": video.stat().st_size,
                               "sha256": sha256_file(video)}),
            "human_hdmi_witness": bool(args.user_observed_hdmi),
            "immutable_finalizer_report": {"path": str(report_path),
                                            "sha256": sha256_file(report_path)},
        },
        "finalizer_checks": report["checks"],
    }
    destination = Path(args.output).resolve()
    atomic_json(destination, bundle)
    print("VERIFIED STAGE39 FULL_PASS")
    print("acceptance bundle =", destination)


if __name__ == "__main__":
    raise SystemExit(main())

