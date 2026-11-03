#!/usr/bin/env python3
"""Measure one exhaustive Stage39 ARM test-set sweep on PYNQ-Z2.

Copy this file into Stage39-board/jupyter and stop the HTTP service before
running it. The same universal bitstream remains loaded while the independent
NumPy INT8/Q15 ARM reference evaluates every entry in the all-test catalog.
Run three separate sweeps (R1/R2/R3) with separate FNB58 logs.
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
ALL_RAYS = np.arange(90 * 360, dtype=np.int64)


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package-root", type=Path)
    parser.add_argument("--scene", choices=SCENES, required=True)
    parser.add_argument("--repeat-id", required=True)
    parser.add_argument("--warmup-rays", type=int, default=32)
    parser.add_argument("--settle-seconds", type=int, default=15)
    parser.add_argument("--idle-seconds", type=int, default=60)
    parser.add_argument("--countdown", type=int, default=10)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    here = Path(__file__).resolve()
    root = (args.package_root or here.parents[1]).resolve()
    if not (root / "scene_registry.json").is_file():
        parser.error(f"Stage39 board package not found: {root}")
    if args.warmup_rays < 1 or args.countdown < 0:
        parser.error("warmup-rays must be positive; countdown cannot be negative")
    if args.idle_seconds < 30 or args.settle_seconds < 0:
        parser.error("idle-seconds must be at least 30; settle-seconds cannot be negative")

    output = args.output or (
        root / "scenes" / args.scene / "evidence" / "stage39" / "power"
        / f"arm_fulltest_{args.repeat_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
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
    spectrum_ids = [str(item["spectrum_id"]) for item in samples]
    if len(samples) != expected_count or len(set(spectrum_ids)) != expected_count:
        raise AssertionError("all-test catalog count/uniqueness validation failed")

    saved_result_path = scene_root / "evidence" / "stage39" / "all_test" / "result.json"
    if not saved_result_path.is_file():
        raise FileNotFoundError(
            f"{saved_result_path} is missing; complete the Stage39 FPGA all-test task first"
        )
    saved = json.loads(saved_result_path.read_text(encoding="utf-8"))
    identity = service.batch_identity(args.scene)
    if saved.get("schema") != "n2-stage39-full-test-fpga-validation-v1-qat-raw-only":
        raise AssertionError("saved full-test result has the wrong Stage39 schema")
    if any(
        saved.get(key) != identity[key]
        for key in ("bit_sha256", "runtime_model_sha256", "test_catalog_sha256")
    ):
        raise AssertionError("saved FPGA result belongs to a different bitstream/model/catalog")
    expected_hashes = {
        str(row["spectrum_id"]): row["fpga_raw_q15_sha256"]
        for row in saved.get("rows", [])
    }
    if set(expected_hashes) != set(spectrum_ids):
        raise AssertionError("saved FPGA full-test hashes do not match the active test catalog")

    print("Initializing PYNQ and loading the Stage39 universal bitstream...", flush=True)
    service.hardware_call(service.initialize_pynq_device, False)

    def prepare():
        with service.RUN_LOCK:
            service.hardware().configure(args.scene, True)
            tx = np.asarray(samples[0]["tx"], np.float32)
            return service.hardware().arm_indices(
                args.scene, tx, ALL_RAYS[: args.warmup_rays]
            )

    warm, warm_seconds = service.hardware_call(prepare)
    print(
        f"Warm-up PASS: {len(warm)} rays in {warm_seconds:.3f}s. "
        "Start a new FNB58 UsbMeter USB-PC/offline log now.",
        flush=True,
    )
    time.sleep(args.settle_seconds)
    idle_start_utc = utc_now()
    idle_started = time.perf_counter()
    print(f"ARM IDLE START UTC={idle_start_utc}: record this {args.idle_seconds}s interval", flush=True)
    time.sleep(args.idle_seconds)
    idle_ended = time.perf_counter()
    idle_end_utc = utc_now()
    print(f"ARM IDLE END UTC={idle_end_utc}", flush=True)
    for remaining in range(args.countdown, 0, -1):
        print(f"ARM full-test sweep starts in {remaining:2d}s", flush=True)
        time.sleep(1)

    map_results = []
    progress_step = max(1, expected_count // 20)
    active_start_utc = utc_now()
    active_started = time.perf_counter()
    print(f"ARM ACTIVE START UTC={active_start_utc}", flush=True)
    for index, item in enumerate(samples):
        spectrum_id = str(item["spectrum_id"])
        tx = np.asarray(item["tx"], np.float32)
        map_start_utc = utc_now()
        map_started = time.perf_counter()

        def one_map():
            with service.RUN_LOCK:
                return service.hardware().arm_indices(args.scene, tx, ALL_RAYS)

        result, compute_seconds = service.hardware_call(one_map)
        map_ended = time.perf_counter()
        map_end_utc = utc_now()
        digest = hashlib.sha256(np.asarray(result, np.uint32).tobytes()).hexdigest()
        if digest != expected_hashes[spectrum_id]:
            raise AssertionError(
                f"ARM/FPGA full-map hash mismatch for spectrum {spectrum_id}: {digest}"
            )
        map_results.append(
            {
                "test_output_index": index,
                "spectrum_id": spectrum_id,
                "map_start_utc": map_start_utc,
                "map_end_utc": map_end_utc,
                "wall_seconds": map_ended - map_started,
                "arm_compute_seconds": float(compute_seconds),
                "raw_q15_sha256": digest,
                "matches_fpga_full_test_hash": True,
            }
        )
        completed = index + 1
        if completed == expected_count or completed % progress_step == 0:
            print(
                f"ARM {args.scene} {args.repeat_id}: {completed}/{expected_count} "
                f"({100.0 * completed / expected_count:.1f}%)",
                flush=True,
            )
    active_ended = time.perf_counter()
    active_end_utc = utc_now()
    clock_validation["end_check"] = check_measurement_clock(root, require_fresh=False)

    observed_ids = [row["spectrum_id"] for row in map_results]
    report = {
        "schema": "stage39-pynq-arm-exhaustive-power-run-v1",
        "created_utc": utc_now(),
        "scene": args.scene,
        "repeat_id": args.repeat_id,
        "clock_validation": clock_validation,
        "implementation": "Stage37ARMReference independent NumPy sparse INT8/Q15 on Zynq ARM",
        "power_boundary": "complete PYNQ-Z2 5 V Micro-USB J8 board input; same universal PL bitstream loaded and idle",
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
        "arm_compute_seconds_sum": sum(row["arm_compute_seconds"] for row in map_results),
        "map_results": map_results,
        "validity": {
            "exact_full_test_index_order": observed_ids == spectrum_ids,
            "every_test_map_measured": len(map_results) == expected_count
            and len(set(observed_ids)) == expected_count,
            "all_maps_bit_exact_to_fpga": all(
                row["matches_fpga_full_test_hash"] for row in map_results
            ),
        },
        "fnb58_instruction": "Save the UsbMeter .cfn with a screen recording anchoring its relative axis to the UTC phase markers. Decode V/I/P and integrate power over idle/active intervals; unverified software NRG must not be used.",
    }
    if not all(report["validity"].values()):
        raise AssertionError("ARM exhaustive coverage/bit-exact validation failed")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(f"ARM ACTIVE END UTC={active_end_utc} - stop and save the FNB58 .cfn log", flush=True)
    print(f"PASS: {output}", flush=True)


if __name__ == "__main__":
    main()
