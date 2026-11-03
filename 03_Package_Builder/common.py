from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


KIT_ROOT = Path(__file__).absolute().parents[1]
TRACKED_CONFIG = KIT_ROOT / "00_Config/reproduction_config.json"
LOCAL_CONFIG = KIT_ROOT / "00_Config/reproduction_config.local.json"
DEFAULT_CONFIG = LOCAL_CONFIG if LOCAL_CONFIG.is_file() else TRACKED_CONFIG
SCENES = ("s23", "Meetingroom", "Office1", "Office2")
WORK = KIT_ROOT / "work"
OUT = KIT_ROOT / "outputs"
PC_TEMPLATE = KIT_ROOT / "04_PC_App/template"
BOARD_TEMPLATE = KIT_ROOT / "05_PYNQ_Board/template"
PC_PACKAGE = OUT / "N2-NeRF2-Stage39-PC-App"
BOARD_PACKAGE = OUT / "N2-NeRF2-Stage39-board"


def load_config(path: Path = DEFAULT_CONFIG) -> dict:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if value.get("schema") != "n2-stage39-cold-reproduction-config-v1":
        raise RuntimeError("reproduction config schema mismatch")
    if tuple(value.get("scenes", {}).keys()) != SCENES:
        raise RuntimeError("scene order must be s23, Meetingroom, Office1, Office2")
    return value


def input_path(config: dict, scene: str, key: str) -> Path:
    return Path(config["input_root"]) / config["scenes"][scene][key]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, value: object) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def run(command: list[object], *, env: dict[str, str] | None = None) -> None:
    rendered = [str(item) for item in command]
    print("[Stage39 cold] " + subprocess.list2cmdline(rendered), flush=True)
    subprocess.run(rendered, cwd=KIT_ROOT, env=env, check=True)


def require_file(path: Path, label: str | None = None) -> Path:
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"{label or 'required file'}: {path}")
    return path


def require_dir(path: Path, label: str | None = None) -> Path:
    path = Path(path)
    if not path.is_dir():
        raise FileNotFoundError(f"{label or 'required directory'}: {path}")
    return path


def copy_clean(source: Path, destination: Path) -> None:
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(source, destination)


def files_manifest(root: Path, excluded: set[str] | None = None) -> dict[str, dict[str, object]]:
    excluded = excluded or set()
    result = {}
    for path in sorted(Path(root).rglob("*"), key=lambda p: p.as_posix()):
        if path.is_file() and path.name not in excluded and "__pycache__" not in path.parts:
            result[path.relative_to(root).as_posix()] = {
                "bytes": path.stat().st_size,
                "sha256": sha256(path),
            }
    return result


def python() -> str:
    return sys.executable

