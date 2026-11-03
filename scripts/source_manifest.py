from __future__ import annotations

import os
from pathlib import Path


ROOT = Path(os.path.abspath(__file__)).parent.parent
MANIFEST = ROOT / "PACKAGE_SHA256.json"


def included(path: Path) -> bool:
    relative = path.relative_to(ROOT)
    parts = relative.parts
    posix = relative.as_posix()
    if not path.is_file() or path == MANIFEST:
        return False
    if ".git" in parts or "__pycache__" in parts or path.suffix == ".pyc":
        return False
    if parts[0] == "external":
        return False
    if posix == "00_Config/reproduction_config.local.json":
        return False
    if parts[0] == "work" and posix != "work/README.md":
        return False
    if parts[0] == "outputs" and posix != "outputs/README.md":
        return False
    if posix.startswith("01_Vivado/output/") and posix != "01_Vivado/output/README.md":
        return False
    if posix.startswith("06_Power_Measurement/results/"):
        return False
    return True


def source_files() -> list[Path]:
    return sorted((path for path in ROOT.rglob("*") if included(path)),
                  key=lambda path: path.relative_to(ROOT).as_posix())
