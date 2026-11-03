#!/usr/bin/env python3
"""Shared verification helpers for Stage39 live-rehearsal evidence."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


EXPECTED_APP_BUILD = "stage39-hardware-exact-qat-four-scene-app-v1"
EXPECTED_REPORT_SCHEMA = "n2-stage39-pc-coordinated-run-v1-qat-raw-only"
EXPECTED_CHALLENGE_SPEC = "n2-stage37-sha256-counter-rejection-ray-challenge-v2"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n",
                         encoding="utf-8")
    temporary.replace(path)


def transcript_sha256(challenge):
    core = {
        "selection_algorithm": challenge["selection_algorithm"],
        "canonical_input": challenge["canonical_input"],
        "ray_indices": challenge["ray_indices"],
        "samples": challenge["samples"],
        "rerun_vs_full_map_bit_exact": challenge["rerun_vs_full_map_bit_exact"],
        "arm_vs_fpga_bit_exact": (None if challenge.get("arm") is None else
                                   challenge["arm"]["arm_vs_fpga_bit_exact"]),
    }
    encoded = json.dumps(core, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def validate_report(report, *, scene, mode, nonce, bit_sha256,
                    runtime_model_sha256, expected_spectrum_id=None,
                    expected_tx=None, challenge_count=16):
    """Validate every claim needed by the continuous Stage39 rehearsal."""
    require(report.get("schema") == EXPECTED_REPORT_SCHEMA, "report schema mismatch")
    require(report.get("app_build_id") == EXPECTED_APP_BUILD, "App build mismatch")
    require(report.get("scene_id") == scene, "report scene mismatch")
    require(report.get("mode") == mode, "report mode mismatch")
    require(isinstance(report.get("created_utc"), str), "report UTC timestamp missing")

    board = report.get("board") or {}
    require(board.get("scene_id") == scene, "board scene mismatch")
    require(board.get("output_mode") == "raw_int8_q15", "FPGA output is not raw INT8/Q15")
    require(board.get("resolution") == [90, 360], "FPGA result is not 90x360")
    require(board.get("fpga_hardware_rays") == 32400, "FPGA did not calculate 32,400 rays")
    require(board.get("every_fpga_pixel_is_a_complete_hardware_ray") is True,
            "not every output pixel is a complete FPGA ray")
    require(board.get("cached_final_output_used") is False, "cached final output was used")
    require(board.get("same_bitstream_for_all_scenes") is True,
            "common four-scene bitstream proof is missing")
    require(board.get("scene_switch_changes_bitstream") is False,
            "scene switch unexpectedly changed the bitstream")
    timing = board.get("timing") or {}
    require(timing.get("under_1p2_seconds") is True, "FPGA full-map time exceeds 1.2 s")

    provenance = board.get("provenance") or {}
    require(provenance.get("bit_sha256") == bit_sha256, "bitstream SHA-256 mismatch")
    require(provenance.get("runtime_model_sha256") == runtime_model_sha256,
            "runtime-model SHA-256 mismatch")
    require(len(str(provenance.get("raw_q15_sha256", ""))) == 64,
            "raw Q15 map SHA-256 missing")

    hdmi = board.get("hdmi") or {}
    require(hdmi.get("requested") is True, "HDMI was not requested")
    require(hdmi.get("upload_ok") is True, "physical HDMI upload did not succeed")
    require((hdmi.get("driver") or {}).get("mode") == [1280, 720, 24],
            "HDMI driver mode mismatch")

    challenge = board.get("random_ray_challenge") or {}
    require(challenge.get("nonce") == nonce, "judge nonce mismatch")
    require(challenge.get("selection_algorithm") == EXPECTED_CHALLENGE_SPEC,
            "random-ray selection algorithm mismatch")
    indices = challenge.get("ray_indices") or []
    samples = challenge.get("samples") or []
    require(challenge.get("ray_count") == challenge_count, "challenge ray count mismatch")
    require(len(indices) == challenge_count and len(set(indices)) == challenge_count,
            "challenge indices are incomplete or duplicated")
    require(len(samples) == challenge_count, "challenge raw sample table is incomplete")
    require(challenge.get("rerun_vs_full_map_bit_exact") is True,
            "challenge rerun is not bit exact")
    require(challenge.get("mismatch_count") == 0 and
            challenge.get("max_abs_q15_delta") == 0,
            "challenge contains a nonzero Q15 mismatch")
    require(all(row.get("full_map_q15") == row.get("rerun_q15") and
                row.get("delta_q15") == 0 for row in samples),
            "challenge raw-Q15 table is inconsistent")
    require((challenge.get("arm") or {}).get("arm_vs_fpga_bit_exact") is True,
            "ARM sparse reference is not bit exact with FPGA")
    require(all(row.get("arm_q15") == row.get("rerun_q15") for row in samples),
            "ARM raw-Q15 values differ from FPGA")
    verifier = challenge.get("pc_independent_verifier") or {}
    require(verifier.get("verdict") == "PASS", "PC independent verifier did not PASS")
    require(verifier.get("selection_matches") is True and
            verifier.get("q15_pairs_match") is True and
            verifier.get("negative_control_detected") is True,
            "PC independent verifier details are incomplete")
    recomputed = transcript_sha256(challenge)
    require(challenge.get("transcript_sha256") == recomputed,
            "challenge transcript SHA-256 does not verify")
    require(verifier.get("transcript_sha256_recomputed") == recomputed,
            "PC verifier transcript SHA-256 differs")

    if mode == "measured":
        truth = report.get("ground_truth") or {}
        require(truth.get("available") is True, "blind measured run has no GT")
        require(str(truth.get("spectrum_id")) == str(expected_spectrum_id),
                "blind spectrum identity mismatch")
    elif mode == "free":
        require((report.get("ground_truth") or {}).get("available") is False,
                "free-Tx run unexpectedly claims measured GT")
    else:
        raise ValueError("unsupported rehearsal mode: " + str(mode))

    if expected_tx is not None:
        actual = report.get("tx") or []
        require(len(actual) == 3 and
                all(abs(float(a) - float(b)) <= 2e-5 for a, b in zip(actual, expected_tx)),
                "Tx coordinate mismatch")

    return {
        "scene": scene,
        "mode": mode,
        "created_utc": report["created_utc"],
        "spectrum_id": (report.get("ground_truth") or {}).get("spectrum_id"),
        "tx": [float(x) for x in report["tx"]],
        "nonce": nonce,
        "challenge_rays": challenge_count,
        "challenge_transcript_sha256": recomputed,
        "raw_q15_sha256": provenance["raw_q15_sha256"],
        "fpga_full_map_seconds": float(timing.get(
            "full_map_row_pipeline_seconds", timing["full_map_cached_pipeline_seconds"])),
        "hdmi_upload_ok": True,
        "arm_vs_fpga_bit_exact": True,
        "pc_independent_verifier": "PASS",
        "negative_control_detected": True,
    }


def inventory_files(root):
    root = Path(root)
    values = []
    for path in sorted(p for p in root.rglob("*") if p.is_file() and p.name != "manifest.json"):
        values.append({"path": path.relative_to(root).as_posix(),
                       "bytes": path.stat().st_size,
                       "sha256": sha256_file(path)})
    return values

