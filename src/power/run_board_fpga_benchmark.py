#!/usr/bin/env python3
"""Trigger only Stage39.3's existing 50-run stability/power gate.

This is not the exhaustive test-set power comparison.  Use
stage39_fpga_fulltest_power.py for the required 1225/200/200/100-map sweeps.
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


SCENES = ("s23", "Meetingroom", "Office1", "Office2")


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def request_json(url, payload=None, timeout=180):
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"} if body is not None else {},
        method="POST" if body is not None else "GET",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--board", default="http://192.168.2.99:8311")
    parser.add_argument("--scene", choices=SCENES, required=True)
    parser.add_argument("--maps", type=int, default=50)
    parser.add_argument("--settle-seconds", type=int, default=15)
    parser.add_argument("--idle-seconds", type=int, default=60)
    parser.add_argument("--countdown", type=int, default=10)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.maps != 50:
        parser.error("Stage39.3 requires exactly 50 runs; use stage39_fpga_fulltest_power.py for exhaustive measurement")
    base = args.board.rstrip("/") + f"/scene/{args.scene}"
    health = request_json(base + "/api/health")
    if not health.get("ok"):
        raise RuntimeError(f"Stage39 service health failed: {health}")
    print(f"Board service PASS: {health['service_build_id']}")
    print("Running one FPGA self-test before measurement to load the scene/runtime...")
    selftest = request_json(base + "/api/selftest", {}, timeout=180)
    if not selftest.get("ok"):
        raise RuntimeError(f"self-test failed: {selftest}")
    print("Self-test PASS. Start FNB58 UsbMeter USB-PC/offline logging now.")
    print(f"Thermal/power settling for {args.settle_seconds}s...")
    time.sleep(args.settle_seconds)
    idle_start_utc = utc_now()
    idle_started = time.perf_counter()
    print(f"FPGA IDLE START: record this {args.idle_seconds}s interval", flush=True)
    time.sleep(args.idle_seconds)
    idle_ended = time.perf_counter()
    idle_end_utc = utc_now()
    print("FPGA IDLE END", flush=True)
    for remaining in range(args.countdown, 0, -1):
        print(f"FPGA active run starts in {remaining:2d}s", flush=True)
        time.sleep(1)
    started_utc = utc_now()
    started = time.perf_counter()
    response = request_json(base + "/api/benchmark", {"repeats": args.maps}, timeout=max(180, args.maps * 10))
    ended = time.perf_counter()
    ended_utc = utc_now()
    if not response.get("ok"):
        raise RuntimeError(f"benchmark failed: {response}")
    result = response["result"]
    if not result.get("stable_raw_q15_hash") or not result.get("matches_portable_integer"):
        raise AssertionError("FPGA benchmark outputs were not stable and bit exact")
    report = {
        "schema": "stage39-pynq-fpga-power-run-v1",
        "purpose": "Stage39.3 50-run stability/power gate; not exhaustive comparison evidence",
        "scene": args.scene,
        "maps": args.maps,
        "idle_start_utc": idle_start_utc,
        "idle_end_utc": idle_end_utc,
        "idle_wall_seconds": idle_ended - idle_started,
        "active_start_utc": started_utc,
        "active_end_utc": ended_utc,
        "active_http_wall_seconds": ended - started,
        "active_fpga_compute_seconds_sum": sum(result["times_seconds"]),
        "power_boundary": "complete PYNQ-Z2 5 V Micro-USB J8 board input measured by FNIRSI FNB58",
        "service_result": result,
        "fnb58_instruction": "Save the UsbMeter .cfn and use idle_start_utc..idle_end_utc plus active_start_utc..active_end_utc, then fill board_power_runs_template.csv.",
    }
    output = args.output or Path("results") / "board_fpga" / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{args.scene}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("FPGA ACTIVE END - stop and save the FNB58 .cfn log")
    print(f"PASS: {output}")


if __name__ == "__main__":
    main()
