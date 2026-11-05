from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from common import BOARD_PACKAGE, KIT_ROOT, OUT, PC_PACKAGE, sha256, write_json


PACKAGES = (("board", BOARD_PACKAGE, "n2-stage39-board-package-manifest-v1"),
            ("pc", PC_PACKAGE, "n2-stage39-pc-package-manifest-v1"))
NOTEBOOK = "Stage39_QAT_Four_Scenes.ipynb"
BOARD_REVISION = "stage39-board-cold-repro-20261012-r1"


def notebook_source_sha(path: Path) -> str:
    notebook = json.loads(path.read_text(encoding="utf-8-sig"))
    cells = []
    for index, cell in enumerate(notebook.get("cells", [])):
        source = cell.get("source", "")
        if isinstance(source, list): source = "".join(source)
        if not isinstance(source, str) or not isinstance(cell.get("cell_type"), str):
            raise RuntimeError(f"invalid notebook cell {index}")
        cells.append({"cell_type": cell["cell_type"], "source": source})
    canonical = json.dumps({"nbformat": 4, "cells": cells}, ensure_ascii=False,
                           sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def include(kind: str, root: Path, path: Path) -> bool:
    relative = path.relative_to(root); posix = relative.as_posix()
    if not path.is_file() or "__pycache__" in relative.parts or path.suffix == ".pyc": return False
    if "evidence" in relative.parts and "evidence_templates" not in relative.parts: return False
    if kind == "pc" and (posix.startswith("pc_app/stage38_") or posix.startswith("software/stage38/")):
        return False
    return True


def selected(kind: str, root: Path) -> list[Path]:
    return sorted((path for path in root.rglob("*") if include(kind, root, path)),
                  key=lambda path: path.relative_to(root).as_posix())


def main() -> None:
    parser = argparse.ArgumentParser(); parser.add_argument("--package", choices=("all", "board", "pc"), default="all")
    args = parser.parse_args(); delivery = {"schema": "n2-stage39-cold-delivery-v1",
        "created_utc": datetime.now(timezone.utc).isoformat(), "packages": {}}
    packages = PACKAGES if args.package == "all" else tuple(value for value in PACKAGES if value[0] == args.package)
    for kind, root, schema in packages:
        if not root.is_dir(): raise FileNotFoundError(root)
        manifest_path = root / ("stage39_board_package_manifest.json" if kind == "board" else "stage39_pc_package_manifest.json")
        files = selected(kind, root)
        immutable = [path for path in files if path != manifest_path and not
                     (kind == "board" and path.relative_to(root).as_posix() == NOTEBOOK)]
        manifest = {"schema": schema, "created_utc": datetime.now(timezone.utc).isoformat(),
            "package": root.name,
            "immutable_scope": "all delivered files except this manifest, notebook runtime state and newly generated evidence",
            "files": {path.relative_to(root).as_posix(): sha256(path) for path in immutable}}
        if kind == "board":
            manifest["package_revision"] = BOARD_REVISION
            manifest["notebook_sources"] = {NOTEBOOK: notebook_source_sha(root / NOTEBOOK)}
        write_json(manifest_path, manifest)
        files = selected(kind, root); archive = OUT / f"{root.name}.zip"
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as stream:
            for index, path in enumerate(files, 1):
                stream.write(path, (Path(root.name) / path.relative_to(root)).as_posix())
                if index % 500 == 0: print(f"[{kind}] {index}/{len(files)}", flush=True)
        delivery["packages"][kind] = {"directory": root.name, "archive": archive.name,
            "files": len(files), "bytes": archive.stat().st_size, "sha256": sha256(archive),
            "manifest_sha256": sha256(manifest_path)}
    write_json(OUT / "stage39_delivery_sha256.json", delivery)
    print(json.dumps(delivery, ensure_ascii=False, indent=2))


if __name__ == "__main__": main()
