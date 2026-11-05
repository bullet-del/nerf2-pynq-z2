#!/usr/bin/env python3
"""Freeze Stage38 and create an independent Stage39 working package.

The script is idempotent.  It never writes inside a Stage37/Stage38 package.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path

from stage39_common import (BOARD38, BOARD39, PC38, PC39, REPORTS, ROOT, SCENES,
                            WORK, canonical_sha256, file_manifest, gate_report,
                            sha256, utc_now, verify_manifest, write_json)


DATA_ROOT = Path(r"D:\university\信道图谱项目\NeRF2学习与复现资料\NeRF2-main\data\RFID")
LOG_ROOT = Path(r"D:\university\信道图谱项目\NeRF2学习与复现资料\NeRF2-main\logs\RFID")
LOG_SUBDIR = {
    "s23": "spectrum-exp1",
    "Meetingroom": "spectrum-meetingroom",
    "Office1": "spectrum-Office1",
    "Office2": "spectrum-Office2",
}


def run_verifier(path: Path) -> dict[str, object]:
    value = subprocess.run(["python", str(path)], cwd=ROOT, text=True,
                           capture_output=True, encoding="utf-8", errors="replace")
    return {"command": ["python", str(path.relative_to(ROOT))],
            "returncode": value.returncode,
            "stdout": value.stdout.strip(), "stderr": value.stderr.strip()}


def split_ids(path: Path) -> list[str]:
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def dataset_entry(scene: str) -> dict[str, object]:
    source = DATA_ROOT / scene
    if not source.is_dir():
        raise FileNotFoundError(source)
    train_path, test_path = source / "train_index.txt", source / "test_index.txt"
    train, test = split_ids(train_path), split_ids(test_path)
    if set(train) & set(test):
        raise RuntimeError(f"{scene}: train/test overlap")
    metadata = {}
    for name in ("train_index.txt", "test_index.txt", "tx_pos.csv", "gateway_info.yml"):
        path = source / name
        metadata[name] = {"bytes": path.stat().st_size, "sha256": sha256(path)}
    spectra = []
    for path in sorted((source / "spectrum").glob("*.png")):
        spectra.append((path.name, path.stat().st_size, sha256(path)))
    checkpoint = LOG_ROOT / scene / LOG_SUBDIR[scene] / "ckpts" / (
        "010000.tar" if scene == "s23" else "005000.tar")
    config_candidates = sorted((LOG_ROOT / scene / LOG_SUBDIR[scene]).glob("*.yml"))
    packaged_config = BOARD38 / "scenes" / scene / "pc_app/model/config.yml"
    config = config_candidates[0] if config_candidates else packaged_config
    return {
        "scene": scene,
        "source_directory": str(source),
        "train_count": len(train), "test_count": len(test),
        "dataset_count": len(train) + len(test),
        "train_test_disjoint": True,
        "split_metadata": metadata,
        "spectrum_png_count": len(spectra),
        "spectrum_tree_sha256": canonical_sha256(spectra),
        "upstream_checkpoint": str(checkpoint),
        "upstream_checkpoint_sha256": sha256(checkpoint),
        "packaged_factorized_checkpoint": str(
            (BOARD38 / "scenes" / scene / "pc_app/model/checkpoint.tar").relative_to(ROOT)),
        "packaged_factorized_checkpoint_sha256": sha256(
            BOARD38 / "scenes" / scene / "pc_app/model/checkpoint.tar"),
        "config": str(config), "config_sha256": sha256(config),
    }


def clone_once(source: Path, destination: Path) -> None:
    if destination.exists():
        return
    shutil.copytree(source, destination, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh-manifest", action="store_true")
    args = parser.parse_args()
    WORK.mkdir(parents=True, exist_ok=True)
    baseline_path = WORK / "immutable/stage38_baseline_manifest.json"
    if baseline_path.exists() and not args.refresh_manifest:
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    else:
        board_files = file_manifest(BOARD38)
        pc_files = file_manifest(PC38)
        baseline = {
            "schema": "n2-stage39-stage38-frozen-baseline-v1",
            "created_utc": utc_now(),
            "stage38_board": {"root": str(BOARD38.relative_to(ROOT)), "files": board_files,
                              "tree_sha256": canonical_sha256(board_files)},
            "stage38_pc": {"root": str(PC38.relative_to(ROOT)), "files": pc_files,
                           "tree_sha256": canonical_sha256(pc_files)},
            "datasets": [dataset_entry(scene) for scene in SCENES],
            "bitstream_sha256": sha256(BOARD38 / "sim/n2_stage38_raw.bit"),
            "hwh_sha256": sha256(BOARD38 / "sim/n2_stage38_raw.hwh"),
        }
        write_json(baseline_path, baseline)

    board_errors = verify_manifest(BOARD38, baseline["stage38_board"]["files"])
    pc_errors = verify_manifest(PC38, baseline["stage38_pc"]["files"])
    board_verify = run_verifier(BOARD38 / "software/stage38/verify_stage38_board_package.py")
    pc_verify = run_verifier(PC38 / "software/stage38/verify_stage38_pc_package.py")
    clone_once(BOARD38, BOARD39)
    clone_once(PC38, PC39)
    checks = [
        {"id": "39.0.stage38_board_immutable", "status": "PASS" if not board_errors else "FAIL",
         "required": True, "details": board_errors[:20]},
        {"id": "39.0.stage38_pc_immutable", "status": "PASS" if not pc_errors else "FAIL",
         "required": True, "details": pc_errors[:20]},
        {"id": "39.0.stage38_board_clean_start_verify",
         "status": "PASS" if board_verify["returncode"] == 0 else "FAIL", "required": True,
         "details": board_verify},
        {"id": "39.0.stage38_pc_clean_start_verify",
         "status": "PASS" if pc_verify["returncode"] == 0 else "FAIL", "required": True,
         "details": pc_verify},
        {"id": "39.0.stage39_independent_directories",
         "status": "PASS" if BOARD39.is_dir() and PC39.is_dir() and
                    BOARD39.resolve() != BOARD38.resolve() and PC39.resolve() != PC38.resolve()
                    else "FAIL", "required": True},
        {"id": "39.0.four_fixed_disjoint_splits",
         "status": "PASS" if len(baseline.get("datasets", [])) == 4 and
                    all(x["train_test_disjoint"] for x in baseline["datasets"]) else "FAIL",
         "required": True},
        {"id": "39.0.traceable_checkpoint_bitstream_code_data",
         "status": "PASS" if baseline.get("bitstream_sha256") and
                    all(x.get("upstream_checkpoint_sha256") and x.get("spectrum_tree_sha256")
                        for x in baseline.get("datasets", [])) else "FAIL", "required": True},
    ]
    report = gate_report("Stage39.0", checks, evidence={
        "baseline_manifest": str(baseline_path.relative_to(ROOT)),
        "stage39_board": str(BOARD39.relative_to(ROOT)),
        "stage39_pc": str(PC39.relative_to(ROOT)),
        "stage38_board_tree_sha256": baseline["stage38_board"]["tree_sha256"],
        "stage38_pc_tree_sha256": baseline["stage38_pc"]["tree_sha256"],
    })
    write_json(REPORTS / "stage39_0_gate.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["status"] != "PASS":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
