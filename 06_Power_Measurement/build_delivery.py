#!/usr/bin/env python3
"""Build and verify the self-contained Stage39 power-comparison delivery ZIP."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--delivery", type=Path, required=True)
    parser.add_argument("--zip", dest="zip_path", type=Path, required=True)
    parser.add_argument("--board-zip", type=Path, required=True)
    args = parser.parse_args()

    source = args.source.resolve()
    delivery = args.delivery.resolve()
    zip_path = args.zip_path.resolve()
    board_zip = args.board_zip.resolve()
    if not source.is_dir() or not board_zip.is_file():
        raise FileNotFoundError("source directory or Stage39 board ZIP is missing")
    delivery.mkdir(parents=True, exist_ok=True)

    source_files = [path for path in source.iterdir() if path.is_file()]
    for result_dir_name in ("lineage_audit", "pc_gpu_full_test_authoritative",
                            "board_run_jsons", "board_fnb58_raw", "board_power_audit"):
        result_dir = source / "results" / result_dir_name
        source_files.extend(path for path in result_dir.rglob("*") if path.is_file())
    expected = {path.relative_to(source).as_posix(): path for path in source_files}
    expected_names = set(expected) | {"DELIVERY_MANIFEST.json"}

    existing = {
        path.relative_to(delivery).as_posix()
        for path in delivery.rglob("*")
        if path.is_file()
    }
    unexpected = sorted(existing - expected_names)
    if unexpected:
        raise RuntimeError(f"delivery contains unexpected stale files: {unexpected}")

    for relative, path in sorted(expected.items()):
        target = delivery / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)

    file_records = {}
    for path in sorted(p for p in delivery.rglob("*") if p.is_file()):
        relative = path.relative_to(delivery).as_posix()
        if relative == "DELIVERY_MANIFEST.json":
            continue
        file_records[relative] = {"bytes": path.stat().st_size, "sha256": sha256(path)}
    manifest = {
        "schema": "stage39-power-comparison-delivery-v1",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "status": "PC_GPU_COMPLETE_BOARD_FNB58_PENDING",
        "board_package_zip_sha256": sha256(board_zip),
        "formal_pc_unique_test_maps": 1725,
        "formal_pc_measured_map_inferences": 5175,
        "primary_pc_metric": "sum of measured per-map inference-window energy",
        "files": file_records,
    }
    manifest_path = delivery / "DELIVERY_MANIFEST.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(p for p in delivery.rglob("*") if p.is_file()):
            archive.write(path, path.relative_to(delivery).as_posix())

    with zipfile.ZipFile(zip_path, "r") as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise AssertionError("ZIP contains duplicate entries")
        disk_names = sorted(
            path.relative_to(delivery).as_posix()
            for path in delivery.rglob("*")
            if path.is_file()
        )
        if sorted(names) != disk_names:
            raise AssertionError("ZIP and delivery directory file lists differ")
        archived_manifest = json.loads(archive.read("DELIVERY_MANIFEST.json").decode("utf-8"))
        if archived_manifest != manifest:
            raise AssertionError("archived manifest differs from disk manifest")
        for relative, record in manifest["files"].items():
            payload = archive.read(relative)
            if len(payload) != record["bytes"]:
                raise AssertionError(f"ZIP size mismatch: {relative}")
            if hashlib.sha256(payload).hexdigest() != record["sha256"]:
                raise AssertionError(f"ZIP SHA-256 mismatch: {relative}")

    print(
        json.dumps(
            {
                "status": "PASS",
                "files_in_zip": len(file_records) + 1,
                "zip": str(zip_path),
                "zip_bytes": zip_path.stat().st_size,
                "zip_sha256": sha256(zip_path),
                "board_zip_sha256": manifest["board_package_zip_sha256"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
