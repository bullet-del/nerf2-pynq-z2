#!/usr/bin/env python3
"""Shared deterministic helpers for the Stage39 release and gate scripts."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[3]
WORK = ROOT / "work_stage39"
REPORTS = WORK / "reports"
PC38 = ROOT / "outputs/N2-NeRF2-Stage38-PC-App"
BOARD38 = ROOT / "outputs/N2-NeRF2-Stage38-board"
PC39 = ROOT / "outputs/N2-NeRF2-Stage39-PC-App"
BOARD39 = ROOT / "outputs/N2-NeRF2-Stage39-board"
SCENES = ("s23", "Meetingroom", "Office1", "Office2")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256(path: Path, block_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(block_size), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_sha256(value: object) -> str:
    raw = json.dumps(value, ensure_ascii=False, allow_nan=False,
                     sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def write_json(path: Path, value: object) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent,
                                     prefix=path.name + ".", suffix=".tmp",
                                     delete=False) as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
        temporary = Path(stream.name)
    os.replace(temporary, path)


def file_manifest(base: Path, *, excluded_names: Iterable[str] = (),
                  excluded_parts: Iterable[str] = ("__pycache__",)) -> dict[str, dict[str, object]]:
    base = Path(base)
    excluded_names = set(excluded_names)
    excluded_parts = set(excluded_parts)
    result: dict[str, dict[str, object]] = {}
    for path in sorted(base.rglob("*")):
        relative = path.relative_to(base)
        if (not path.is_file() or path.name in excluded_names or path.suffix == ".pyc" or
                any(part in excluded_parts for part in relative.parts)):
            continue
        key = relative.as_posix()
        result[key] = {"bytes": path.stat().st_size, "sha256": sha256(path)}
    return result


def verify_manifest(base: Path, files: dict[str, object]) -> list[str]:
    errors: list[str] = []
    for relative, expected in files.items():
        path = Path(base) / relative
        digest = expected["sha256"] if isinstance(expected, dict) else expected
        size = expected.get("bytes") if isinstance(expected, dict) else None
        if not path.is_file():
            errors.append(f"missing: {relative}")
        elif size is not None and path.stat().st_size != int(size):
            errors.append(f"size mismatch: {relative}")
        elif sha256(path) != digest:
            errors.append(f"sha256 mismatch: {relative}")
    return errors


def gate_report(stage: str, checks: list[dict[str, object]],
                *, evidence: dict[str, object] | None = None) -> dict[str, object]:
    blocking = [check for check in checks if check.get("required", True) and
                check.get("status") != "PASS"]
    return {
        "schema": "n2-stage39-gate-report-v1",
        "stage": stage,
        "generated_utc": utc_now(),
        "status": "PASS" if not blocking else "BLOCKED",
        "checks": checks,
        "blocking_check_ids": [str(check["id"]) for check in blocking],
        "evidence": evidence or {},
    }

