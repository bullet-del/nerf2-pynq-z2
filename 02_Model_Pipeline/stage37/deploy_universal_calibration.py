#!/usr/bin/env python3
"""Deploy the one audited Stage37 calibration format to all scene packages."""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import numpy as np

WORK = Path(__file__).resolve().parent
ROOT = WORK.parent
BOARD = ROOT / "outputs/N2-NeRF2-Stage37-board"
PC = ROOT / "outputs/N2-NeRF2-Stage37-PC-App"
SCENES = ("s23", "Meetingroom", "Office1", "Office2")
SCHEMA = "n2-stage37-universal-train-only-monotone-q15-lut-v2"
ALGORITHM = "deterministic-fp32-teacher-train-validation-weighted-pava-blend-v1"


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, value):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n",
                          encoding="utf-8")


def main():
    bit_sha = json.loads((BOARD / "sim/stage37_artifact_manifest.json").read_text(
        encoding="utf-8"))["bit_sha256"]
    audit = json.loads((WORK / "stage37_multiscene_audit.json").read_text(encoding="utf-8"))
    by_scene = {row["scene_id"]: row for row in audit["scenes"]}
    for scene in SCENES:
        source_json = WORK / "metrics" / (scene + "_universal_teacher_lut.json")
        source_npy = source_json.with_suffix(".npy")
        value = json.loads(source_json.read_text(encoding="utf-8"))
        lut = np.load(source_npy, allow_pickle=False)
        if value.get("schema") != SCHEMA or value.get("algorithm") != ALGORITHM:
            raise RuntimeError(scene + ": calibration schema/algorithm mismatch")
        if lut.dtype != np.uint16 or lut.shape != (65536,):
            raise RuntimeError(scene + ": calibration LUT must be uint16[65536]")
        if np.any(np.diff(lut.astype(np.int64)) < 0):
            raise RuntimeError(scene + ": calibration LUT is not monotone")
        scene_pc = PC / "scenes" / scene
        checkpoint = scene_pc / json.loads((scene_pc / "scene.json").read_text(
            encoding="utf-8"))["checkpoint"]
        quant_manifest = WORK / "scene_runtime" / scene / "quant_manifest.json"
        value.update({
            "scene_id": scene,
            "checkpoint_sha256": sha(checkpoint),
            "quant_manifest_sha256": sha(quant_manifest),
            "generator": "work_stage37/fit_lut_calibration.py",
            "generator_sha256": sha(WORK / "fit_lut_calibration.py"),
            "test_metrics_are_audit_only": True,
            "runtime_cost": "one uint16 lookup per output pixel",
        })
        if abs(float(value["mean_ssim_gap"])) >= 0.015:
            raise RuntimeError(scene + ": absolute mean SSIM gap is not below 0.015")
        targets = (
            WORK / "scene_runtime" / scene,
            BOARD / "scenes" / scene / "model",
            BOARD / "scenes" / scene / "data/stage33",
            PC / "scenes" / scene / "data/stage33",
        )
        for target in targets:
            target.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_npy, target / "output_calibration.npy")
            write_json(target / "output_calibration.json", value)
        row = by_scene[scene]
        row["fpga_train_only_calibrated_mean_ssim"] = value["calibrated_mean_ssim"]
        row["factorized_pc_fp32_mean_ssim"] = value["fp32_mean_ssim"]
        row["mean_ssim_gap"] = value["mean_ssim_gap"]
        row["gap_under_0p015"] = True
        row["calibration_schema"] = SCHEMA
        row["calibration_algorithm"] = ALGORITHM
        row["calibration_lut_sha256"] = value["lut_sha256"]
        scene_paths = (PC / "scenes" / scene / "scene.json",
                       BOARD / "scenes" / scene / "scene.json")
        for scene_path in scene_paths:
            scene_value = json.loads(scene_path.read_text(encoding="utf-8"))
            scene_value["fpga_calibrated_mean_ssim"] = value["calibrated_mean_ssim"]
            scene_value["factorized_pc_mean_ssim"] = value["fp32_mean_ssim"]
            scene_value["measured_or_reference_mean_ssim_gap"] = value["mean_ssim_gap"]
            scene_value["output_calibration"] = {
                "schema": SCHEMA, "algorithm": ALGORITHM,
                "fit_target": "factorized-fp32-checkpoint-teacher",
                "training_ground_truth_used_for_fitting": False,
                "test_split_used_for_fitting": False,
                "lut_sha256": value["lut_sha256"],
            }
            scene_value["hardware"]["bit_sha256"] = bit_sha
            write_json(scene_path, scene_value)
    audit["calibration_policy"] = {
        "schema": SCHEMA, "algorithm": ALGORITHM,
        "same_algorithm_all_scenes": True,
        "runtime_operation": "one uint16 LUT lookup per output pixel",
        "training_ground_truth_used_for_fitting": False,
        "test_split_used_for_fitting": False,
    }
    audit["all_scenes_pass"] = all(row["gap_under_0p015"] for row in audit["scenes"])
    write_json(WORK / "stage37_multiscene_audit.json", audit)
    print(json.dumps({scene: {
        "raw": by_scene[scene]["fpga_fixed_raw_mean_ssim"],
        "calibrated": by_scene[scene]["fpga_train_only_calibrated_mean_ssim"],
        "pc": by_scene[scene]["factorized_pc_fp32_mean_ssim"],
        "absolute_gap": by_scene[scene]["mean_ssim_gap"],
    } for scene in SCENES}, indent=2))


if __name__ == "__main__":
    main()
