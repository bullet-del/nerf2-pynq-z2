#!/usr/bin/env python3
"""Synchronize a PYNQ measurement clock to the Windows measurement PC.

PC:    python stage39_power_clock.py --serve --bind 192.168.2.1
PYNQ: python jupyter/stage39_power_clock.py --sync-server http://192.168.2.1:8318
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.request import urlopen


def boot_id():
    return Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()


def check_measurement_clock(root: Path, require_fresh: bool = True):
    marker = root / "jupyter" / "stage39_clock_sync.json"
    if not marker.is_file():
        raise RuntimeError(
            "Missing PC clock synchronization. Run jupyter/stage39_power_clock.py "
            "--sync-server http://192.168.2.1:8318 before a formal power run.")
    sync = json.loads(marker.read_text(encoding="utf-8"))
    if sync.get("schema") != "stage39-pc-board-clock-sync-v1" or sync.get("boot_id") != boot_id():
        raise RuntimeError("Clock sync belongs to another boot. Synchronize again.")
    age = time.monotonic() - float(sync["board_monotonic_after_sync"])
    if age < 0 or (require_fresh and age > 86400):
        raise RuntimeError("Clock sync is older than 24 hours. Synchronize again.")
    expected = float(sync["pc_epoch_after_sync_estimate"]) + age
    drift = time.time() - expected
    if abs(drift) > 0.2:
        raise RuntimeError(f"PYNQ clock changed after sync ({drift:+.3f}s). Synchronize again.")
    return {"status": "PASS", "source": str(marker),
            "pc_reference_utc": sync["pc_reference_utc"],
            "best_request_round_trip_s": sync["best_request_round_trip_s"],
            "sync_age_s": age, "drift_since_sync_s": drift,
            "server": sync["server"]}


class ClockHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/clock":
            self.send_error(404)
            return
        # This server exposes only the PC's wall clock and changes no files.
        epoch = time.time()
        payload = json.dumps({"schema": "stage39-pc-clock-v1", "epoch": epoch,
                              "utc": datetime.fromtimestamp(epoch, timezone.utc).isoformat()}).encode("ascii")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_):
        pass


def sync_board(server: str, marker: Path):
    if os.name != "posix" or os.geteuid() != 0:
        raise RuntimeError("Run synchronization as root in the PYNQ Jupyter Terminal.")
    probes = []
    for _ in range(5):
        started = time.monotonic()
        with urlopen(server.rstrip("/") + "/clock", timeout=5) as response:
            payload = json.load(response)
        ended = time.monotonic()
        if payload.get("schema") != "stage39-pc-clock-v1":
            raise RuntimeError("Server returned an unexpected clock response")
        epoch = float(payload["epoch"])
        if not 1790812800 <= epoch <= 4102444800:  # October 2026 .. January 2100
            raise RuntimeError("PC clock is outside the supported measurement date range")
        probes.append((ended - started, epoch, ended))
    rtt, epoch, received_monotonic = min(probes)
    if rtt > 0.2:
        raise RuntimeError(f"Clock synchronization round trip too large: {rtt:.3f}s")
    target = epoch + rtt / 2 + (time.monotonic() - received_monotonic)
    subprocess.run(["date", "-u", "--set", f"@{target:.6f}"], check=True,
                   stdout=subprocess.DEVNULL)
    observed_monotonic = time.monotonic()
    pc_estimate = epoch + rtt / 2 + observed_monotonic - received_monotonic
    observed_epoch = time.time()
    difference = observed_epoch - pc_estimate
    if abs(difference) > 0.2:
        raise RuntimeError(f"System time differs from PC after setting: {difference:+.3f}s")
    result = {"schema": "stage39-pc-board-clock-sync-v1", "server": server,
              "pc_reference_utc": datetime.fromtimestamp(pc_estimate, timezone.utc).isoformat(),
              "best_request_round_trip_s": rtt,
              "board_monotonic_after_sync": observed_monotonic,
              "pc_epoch_after_sync_estimate": pc_estimate,
              "board_epoch_after_sync": observed_epoch,
              "difference_after_sync_s": difference,
              "boot_id": boot_id()}
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print("CLOCK SYNC PASS")
    print(json.dumps(result, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--serve", action="store_true")
    mode.add_argument("--sync-server")
    parser.add_argument("--bind", default="192.168.2.1")
    parser.add_argument("--port", type=int, default=8318)
    parser.add_argument("--marker", type=Path)
    args = parser.parse_args()
    if args.serve:
        print(f"PC CLOCK SERVER: http://{args.bind}:{args.port}/clock", flush=True)
        print("Keep this window open while synchronizing the PYNQ. Ctrl+C stops it.", flush=True)
        with ThreadingHTTPServer((args.bind, args.port), ClockHandler) as server:
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
    else:
        marker = args.marker or Path(__file__).resolve().parent / "stage39_clock_sync.json"
        sync_board(args.sync_server, marker)


if __name__ == "__main__":
    main()
