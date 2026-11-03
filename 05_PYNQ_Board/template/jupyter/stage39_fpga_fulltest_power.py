#!/usr/bin/env python3
"""Measure one exhaustive Stage39 FPGA test-set sweep on PYNQ-Z2.

Copy this file into Stage39-board/jupyter and stop the HTTP service before
running it. FNB58 logging spans one continuous sweep containing every entry
in data/stage32/all_test_catalog.json. Run it three times with repeat IDs
R1/R2/R3 and a separate FNB58 log for each run.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np


SCENES = ("s23", "Meetingroom", "Office1", "Office2")


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package-root", type=Path)
    parser.add_argument("--scene", choices=SCENES, required=True)
    parser.add_argument("--repeat-id", required=True)
    parser.add_argument("--settle-seconds", type=int, default=15)
    parser.add_argument("--idle-seconds", type=int, default=60)
    parser.add_argument("--countdown", type=int, default=10)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    here = Path(__file__).resolve()
    root = (args.package_root or here.parents[1]).resolve()
    if not (root / "scene_registry.json").is_file():
        parser.error(f"Stage39 board package not found: {root}")
    if args.idle_seconds < 30 or args.settle_seconds < 0 or args.countdown < 0:
        parser.error("idle-seconds must be >=30; settle/countdown cannot be negative")

    output = args.output or (
        root / "scenes" / args.scene / "evidence" / "stage39" / "power"
        / f"fpga_fulltest_{args.repeat_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    )
    if output.exists():
        parser.error(f"Output already exists; preserve evidence and choose another filename: {output}")
    from stage39_power_clock import check_measurement_clock
    clock_validation = check_measurement_clock(root)
    print(f"CLOCK CHECK PASS: {utc_now()}", flush=True)

    sys.path.insert(0, str(root / "jupyter"))
    sys.path.insert(0, str(root / "software" / "stage24"))
    import stage39_board_service as service

    scene_root = root / "scenes" / args.scene
    _, all_catalog, _ = service.catalogs(args.scene)
    samples = all_catalog["samples"]
    expected_count = int(service.SPECS[args.scene]["test_count"])
    if len(samples) != expected_count:
        raise AssertionError(f"catalog has {len(samples)} rows, expected {expected_count}")
    spectrum_ids = [str(item["spectrum_id"]) for item in samples]
    if len(set(spectrum_ids)) != expected_count:
        raise AssertionError("all-test catalog spectrum IDs are not unique")

    # If the normal Stage39 all-test service has already completed, use its
    # stored per-map hashes as an independent expected-output check.
    saved_result_path = scene_root / "evidence" / "stage39" / "all_test" / "result.json"
    expected_hashes = {}
    if saved_result_path.is_file():
        saved = json.loads(saved_result_path.read_text(encoding="utf-8"))
        identity = service.batch_identity(args.scene)
        if saved.get("schema") != "n2-stage39-full-test-fpga-validation-v1-qat-raw-only":
            raise AssertionError("saved full-test result has the wrong Stage39 schema")
        if any(
            saved.get(key) != identity[key]
            for key in ("bit_sha256", "runtime_model_sha256", "test_catalog_sha256")
        ):
            raise AssertionError("saved full-test result belongs to a different bitstream/model/catalog")
        expected_hashes = {
            str(row["spectrum_id"]): row["fpga_raw_q15_sha256"]
            for row in saved.get("rows", [])
        }
        if set(expected_hashes) != set(spectrum_ids):
            raise AssertionError("saved Stage39 all-test result does not match the active catalog")

    print("Initializing PYNQ and loading the Stage39 universal bitstream...", flush=True)
    service.hardware_call(service.initialize_pynq_device, False)

    def configure_and_warm():
        with service.RUN_LOCK:
            service.hardware().configure(args.scene, True)
            item = samples[0]
            return service.hardware().run_map(
                args.scene, np.asarray(item["tx"], np.float32), None, None
            )

    warm_report, _, warm_q15 = service.hardware_call(configure_and_warm)
    warm_hash = hashlib.sha256(np.asarray(warm_q15, np.uint32).tobytes()).hexdigest()
    if expected_hashes and warm_hash != expected_hashes[spectrum_ids[0]]:
        raise AssertionError("warm-up FPGA output differs from saved full-test evidence")
    print(
        f"Warm-up PASS: {warm_report['timing']['full_map_cached_pipeline_seconds']:.6f}s. "
        "Start a new FNB58 UsbMeter USB-PC/offline log now.",
        flush=True,
    )
    time.sleep(args.settle_seconds)
    idle_start_utc = utc_now()
    idle_started = time.perf_counter()
    print(f"FPGA IDLE START UTC={idle_start_utc}: record this {args.idle_seconds}s interval", flush=True)
    time.sleep(args.idle_seconds)
    idle_ended = time.perf_counter()
    idle_end_utc = utc_now()
    print(f"FPGA IDLE END UTC={idle_end_utc}", flush=True)
    for remaining in range(args.countdown, 0, -1):
        print(f"FPGA full-test sweep starts in {remaining:2d}s", flush=True)
        time.sleep(1)

    map_results = []
    progress_step = max(1, expected_count // 20)
    active_start_utc = utc_now()
    active_started = time.perf_counter()
    print(f"FPGA ACTIVE START UTC={active_start_utc}", flush=True)
    for index, item in enumerate(samples):
        spectrum_id = str(item["spectrum_id"])
        tx = np.asarray(item["tx"], np.float32)
        map_start_utc = utc_now()
        map_started = time.perf_counter()

        def one_map():
            with service.RUN_LOCK:
                return service.hardware().run_map(args.scene, tx, None, None)

        report, _, q15 = service.hardware_call(one_map)
        map_ended = time.perf_counter()
        map_end_utc = utc_now()
        digest = hashlib.sha256(np.asarray(q15, np.uint32).tobytes()).hexdigest()
        expected = expected_hashes.get(spectrum_id)
        if expected is not None and digest != expected:
            raise AssertionError(f"FPGA map hash mismatch for spectrum {spectrum_id}")
        map_results.append(
            {
                "test_output_index": index,
                "spectrum_id": spectrum_id,
                "map_start_utc": map_start_utc,
                "map_end_utc": map_end_utc,
                "wall_seconds": map_ended - map_started,
                "fpga_compute_seconds": float(
                    report["timing"]["full_map_cached_pipeline_seconds"]
                ),
                "raw_q15_sha256": digest,
                "matches_saved_full_test_hash": expected is None or digest == expected,
            }
        )
        completed = index + 1
        if completed == expected_count or completed % progress_step == 0:
            print(
                f"FPGA {args.scene} {args.repeat_id}: {completed}/{expected_count} "
                f"({100.0 * completed / expected_count:.1f}%)",
                flush=True,
            )
    active_ended = time.perf_counter()
    active_end_utc = utc_now()
    clock_validation["end_check"] = check_measurement_clock(root, require_fresh=False)

    observed_ids = [row["spectrum_id"] for row in map_results]
    report = {
        "schema": "stage39-pynq-fpga-exhaustive-power-run-v1",
        "created_utc": utc_now(),
        "scene": args.scene,
        "repeat_id": args.repeat_id,
        "clock_validation": clock_validation,
        "power_boundary": "complete PYNQ-Z2 5 V Micro-USB J8 board input measured by FNIRSI FNB58",
        "resolution": [90, 360],
        "rays_per_map": 32400,
        "samples_per_ray": 64,
        "maps": len(map_results),
        "total_test_maps": expected_count,
        "spectrum_ids": spectrum_ids,
        "idle_start_utc": idle_start_utc,
        "idle_end_utc": idle_end_utc,
        "idle_wall_seconds": idle_ended - idle_started,
        "active_start_utc": active_start_utc,
        "active_end_utc": active_end_utc,
        "active_wall_seconds": active_ended - active_started,
        "seconds_per_map_wall": (active_ended - active_started) / expected_count,
        "fpga_compute_seconds_sum": sum(row["fpga_compute_seconds"] for row in map_results),
        "map_results": map_results,
        "validity": {
            "exact_full_test_index_order": observed_ids == spectrum_ids,
            "every_test_map_measured": len(map_results) == expected_count
            and len(set(observed_ids)) == expected_count,
            "saved_full_test_reference_available": bool(expected_hashes),
            "all_outputs_match_saved_full_test": bool(expected_hashes)
            and all(row["matches_saved_full_test_hash"] for row in map_results),
        },
        "fnb58_instruction": "Save the UsbMeter .cfn with a screen recording anchoring its relative axis to the UTC phase markers. Decode V/I/P and integrate power over idle/active intervals; unverified software NRG must not be used.",
    }
    if not report["validity"]["exact_full_test_index_order"] or not report["validity"][
        "every_test_map_measured"
    ]:
        raise AssertionError("full-test coverage validation failed")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(f"FPGA ACTIVE END UTC={active_end_utc} - stop and save the FNB58 .cfn log", flush=True)
    print(f"PASS: {output}", flush=True)


if __name__ == "__main__":
    main()
