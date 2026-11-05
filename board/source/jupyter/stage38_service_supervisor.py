#!/usr/bin/env python3
"""Supervise the persistent Stage38 PYNQ HTTP worker."""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT / "evidence/stage38_service"
STATE_PATH = STATE_DIR / "supervisor_state.json"
BUILD_ID = "stage38-service-supervisor-v1"
STOP = False
CHILD = None


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def write_state(**values):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    current = {}
    if STATE_PATH.is_file():
        try:
            current = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            current = {}
    current.update(values)
    current.update({"schema": "n2-stage38-supervisor-state-v1",
                    "build_id": BUILD_ID, "updated_utc": utc_now()})
    temporary = STATE_PATH.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(current, indent=2) + "\n", encoding="utf-8")
    os.replace(str(temporary), str(STATE_PATH))


def signal_handler(signum, _frame):
    global STOP
    STOP = True
    if CHILD is not None and CHILD.poll() is None:
        try:
            CHILD.terminate()
        except Exception:
            pass
    write_state(status="stopping", signal=int(signum))


def main():
    global CHILD
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8311)
    args = parser.parse_args()
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    restart_count = 0
    while not STOP:
        command = [sys.executable, "-u", str(ROOT / "jupyter/stage38_board_service.py"),
                   "--host", str(args.host), "--port", str(args.port)]
        print("[Stage38 supervisor] starting worker", command, flush=True)
        CHILD = subprocess.Popen(command, cwd=str(ROOT), stdin=subprocess.DEVNULL,
                                 close_fds=True)
        write_state(status="worker_running", supervisor_pid=os.getpid(),
                    worker_pid=CHILD.pid, restart_count=restart_count,
                    last_start_utc=utc_now())
        exit_code = CHILD.wait()
        CHILD = None
        if STOP:
            break
        restart_count += 1
        write_state(status="worker_exited_restart_pending", exit_code=int(exit_code),
                    restart_count=restart_count, last_exit_utc=utc_now())
        print("[Stage38 supervisor] worker exited with %d; restarting in 2s" %
              int(exit_code), flush=True)
        deadline = time.monotonic() + 2.0
        while not STOP and time.monotonic() < deadline:
            time.sleep(.1)
    write_state(status="stopped", supervisor_pid=os.getpid(),
                restart_count=restart_count)


if __name__ == "__main__":
    main()
