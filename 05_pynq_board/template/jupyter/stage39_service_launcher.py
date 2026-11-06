#!/usr/bin/env python3
"""Launch the Stage39 PYNQ service outside the Jupyter kernel.

The launcher owns only Stage39 processes. It never terminates another service
that happens to use the same TCP port. A failed worker is reported with its
real log tail instead of being hidden behind a generic readiness timeout.
"""
from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT / "evidence/stage39_service"
PID_PATH = STATE_DIR / "service_pid.json"
LOG_PATH = STATE_DIR / "service.log"
SUPERVISOR_STATE_PATH = STATE_DIR / "supervisor_state.json"
BUILD_ID = "stage39-service-launcher-v2-startup-diagnostics"
EXPECTED_SERVICE_BUILD_ID = "stage39-hardware-exact-qat-four-scene-service-v1"


def _health(host="127.0.0.1", port=8311, timeout=1.0):
    try:
        with urlopen("http://%s:%d/api/health" % (host, int(port)),
                     timeout=timeout) as response:
            value = json.loads(response.read().decode("utf-8"))
        return value if value.get("ok") else None
    except Exception:
        return None


def _alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except Exception:
        return False


def _command_line(pid):
    try:
        return (Path("/proc") / str(int(pid)) / "cmdline").read_bytes().replace(
            b"\0", b" ").decode("utf-8", errors="replace")
    except Exception:
        return ""


def _owned_stage39_process(pid):
    command = _command_line(pid)
    return ("stage39_board_service.py" in command or
            "stage39_service_supervisor.py" in command)


def _matching_service_pids(port=8311):
    result = []
    proc = Path("/proc")
    if not proc.is_dir():
        return result
    for entry in proc.iterdir():
        if not entry.name.isdigit() or int(entry.name) == os.getpid():
            continue
        command = _command_line(int(entry.name))
        if ("stage39_board_service.py" not in command and
                "stage39_service_supervisor.py" not in command):
            continue
        if "--port" in command and ("--port %d" % int(port)) not in command:
            continue
        result.append(int(entry.name))
    return result


def _port_open(port, timeout=.25):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(float(timeout))
    try:
        return sock.connect_ex(("127.0.0.1", int(port))) == 0
    finally:
        sock.close()


def _read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return None


def _log_tail(lines=80):
    try:
        values = LOG_PATH.read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(values[-int(lines):])
    except Exception as exc:
        return "<service log unavailable: %s>" % exc


def _stop_owned_pids(pids, timeout=6.0):
    owned = sorted(set(int(pid) for pid in pids
                       if _alive(pid) and _owned_stage39_process(pid)))
    # Terminating the supervisor also asks its worker to stop. Sending TERM to
    # both prevents an orphaned worker from retaining port 8311.
    for pid in owned:
        try:
            os.kill(pid, signal.SIGTERM)
        except Exception:
            pass
    deadline = time.monotonic() + float(timeout)
    while time.monotonic() < deadline and any(_alive(pid) for pid in owned):
        time.sleep(.1)
    for pid in owned:
        if _alive(pid):
            try:
                os.kill(pid, signal.SIGKILL)
            except Exception:
                pass
    return owned


def _startup_error(prefix, port, stop_pids=()):
    if stop_pids:
        _stop_owned_pids(stop_pids)
    PID_PATH.unlink(missing_ok=True)
    supervisor = _read_json(SUPERVISOR_STATE_PATH)
    details = [str(prefix), "", "Stage39 service.log tail:", _log_tail()]
    if supervisor is not None:
        details.extend(("", "supervisor_state:",
                        json.dumps(supervisor, ensure_ascii=False, indent=2)))
    details.extend(("", "Diagnostic files:", str(LOG_PATH),
                    str(SUPERVISOR_STATE_PATH), "port=%d" % int(port)))
    return RuntimeError("\n".join(details))


def start(host="0.0.0.0", port=8311, wait_seconds=120.0):
    """Start Stage39 and wait until `/api/health` answers.

    PYNQ device discovery after a cold boot can exceed 30 seconds, so the
    readiness window is 120 seconds. Repeated worker crashes are detected
    early and their traceback is returned directly to the notebook.
    """
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    current = _health(port=port)
    if current is not None:
        actual = current.get("service_build_id")
        if actual != EXPECTED_SERVICE_BUILD_ID:
            raise RuntimeError(
                "port %d is occupied by service %r, expected %r. Stop the old "
                "Stage37/Stage39 service or reboot PYNQ once, then run this cell "
                "again. The launcher did not terminate another service." %
                (int(port), actual, EXPECTED_SERVICE_BUILD_ID))
        return {"ok": True, "already_running": True,
                "url": "http://0.0.0.0:%d" % int(port), "health": current,
                "log": str(LOG_PATH)}

    # A previous failed Stage39 attempt may have left its supervisor in a
    # restart loop. Replace only processes positively identified as Stage39.
    stale_pids = _matching_service_pids(port)
    if PID_PATH.is_file():
        state = _read_json(PID_PATH) or {}
        pid = state.get("pid")
        if pid and _alive(pid):
            if not _owned_stage39_process(pid):
                raise RuntimeError(
                    "Stage39 PID file points to a different live process; it was "
                    "not stopped. Reboot PYNQ once before retrying: %s" % PID_PATH)
            stale_pids.append(int(pid))
    if stale_pids:
        _stop_owned_pids(stale_pids)
    PID_PATH.unlink(missing_ok=True)
    # Do not let a restart counter from an earlier failed attempt make the new
    # supervisor look as though it has already crashed repeatedly.
    SUPERVISOR_STATE_PATH.unlink(missing_ok=True)

    # If the port is still busy, its owner is not Stage39 and must not be killed
    # by this launcher.
    if _port_open(port):
        raise RuntimeError(
            "port %d is already occupied but does not provide a valid Stage39 "
            "health response. Stop the old board service or reboot PYNQ once, "
            "then run this cell again." % int(port))

    command = [sys.executable, "-u",
               str(ROOT / "jupyter/stage39_service_supervisor.py"),
               "--host", str(host), "--port", str(int(port))]
    # A clean log prevents an old traceback from being mistaken for this run.
    log = LOG_PATH.open("wb", buffering=0)
    try:
        process = subprocess.Popen(
            command, cwd=str(ROOT), stdin=subprocess.DEVNULL, stdout=log,
            stderr=subprocess.STDOUT, start_new_session=True, close_fds=True)
    finally:
        log.close()
    state = {"schema": "n2-stage39-service-process-v1",
             "launcher_build_id": BUILD_ID, "pid": int(process.pid),
             "port": int(port), "command": command,
             "started_unix": time.time(), "log": str(LOG_PATH)}
    PID_PATH.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")

    deadline = time.monotonic() + float(wait_seconds)
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise _startup_error(
                "Stage39 supervisor exited during startup (exit code %s)." %
                process.returncode, port)
        value = _health(port=port)
        if value is not None:
            if value.get("service_build_id") != EXPECTED_SERVICE_BUILD_ID:
                raise _startup_error(
                    "An unexpected service answered on port %d." % int(port),
                    port, (process.pid,))
            return {"ok": True, "already_running": False,
                    "pid": int(process.pid),
                    "url": "http://0.0.0.0:%d" % int(port), "health": value,
                    "log": str(LOG_PATH)}
        supervisor = _read_json(SUPERVISOR_STATE_PATH) or {}
        # Three worker exits indicate a deterministic startup failure.
        if int(supervisor.get("restart_count", 0) or 0) >= 3:
            raise _startup_error(
                "Stage39 worker repeatedly exited before becoming ready.", port,
                (process.pid,))
        time.sleep(.25)

    raise _startup_error(
        "Stage39 did not become ready within %.1f seconds." %
        float(wait_seconds), port, (process.pid,))


def status(port=8311):
    health = _health(port=port)
    process = None
    if PID_PATH.is_file():
        process = _read_json(PID_PATH) or {"invalid_pid_file": True}
        if "pid" in process:
            process["alive"] = _alive(process.get("pid"))
    supervisor = _read_json(SUPERVISOR_STATE_PATH)
    return {"ok": health is not None and
            health.get("service_build_id") == EXPECTED_SERVICE_BUILD_ID,
            "health": health, "process": process, "supervisor": supervisor,
            "log": str(LOG_PATH), "log_tail": _log_tail(30)}


def stop(port=8311, timeout=6.0):
    pids = _matching_service_pids(port)
    if PID_PATH.is_file():
        state = _read_json(PID_PATH) or {}
        if state.get("pid"):
            pids.append(int(state["pid"]))
    pids = sorted(set(pids))
    if not pids:
        PID_PATH.unlink(missing_ok=True)
        return {"ok": True, "stopped": False,
                "reason": "no Stage39 service process"}
    health = _health(port=port)
    if health is not None and health.get("service_build_id") != EXPECTED_SERVICE_BUILD_ID:
        raise RuntimeError("port %d belongs to another service; it was not stopped" %
                           int(port))
    stopped = _stop_owned_pids(pids, timeout=timeout)
    PID_PATH.unlink(missing_ok=True)
    return {"ok": True, "stopped": not any(_alive(pid) for pid in stopped),
            "pids": stopped}


__all__ = ["BUILD_ID", "EXPECTED_SERVICE_BUILD_ID", "start", "status", "stop"]

