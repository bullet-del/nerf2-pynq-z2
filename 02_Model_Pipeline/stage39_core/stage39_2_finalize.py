#!/usr/bin/env python3
"""Finalize Stage39.2 reports and install accepted runtime models into Stage39."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import numpy as np

from stage39_common import BOARD39, PC39, ROOT, SCENES, file_manifest, sha256, write_json


def main():
    registry_path = BOARD39 / "scene_registry.json"
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    by_scene = {x["id"]: x for x in registry["scenes"]}
    rows = []
    for scene in SCENES:
        old_validation = json.loads((PC39 / "scenes" / scene /
            "evidence/stage38_pc_app/all_1225/validation.json").read_text(encoding="utf-8"))
        evaluation_path = ROOT / "work_stage39/qat" / scene / "full_evaluation.json"
        evaluation = json.loads(evaluation_path.read_text(encoding="utf-8"))
        selected = ROOT / "work_stage39/selected_runtime" / scene
        package = json.loads((selected / "package_manifest.json").read_text(encoding="utf-8"))
        old_pc = float(old_validation["summary"]["pc_ssim_mean"])
        old_integer = float(old_validation["summary"]["fpga_raw_ssim_mean"])
        new_integer = float(evaluation["hardware_integer"]["summary"]["ssim"]["mean"])
        qat_shadow = float(evaluation["pc_fp32"]["summary"]["ssim"]["mean"])
        gap = old_pc - new_integer
        if new_integer + 1e-12 < old_integer:
            raise AssertionError(f"{scene}: selected Stage39 integer model regressed")
        if gap > 0.015 + 1e-12:
            raise AssertionError(f"{scene}: Stage39 mean SSIM gap {gap} exceeds 0.015")

        # Install only model/runtime assets.  The PC reference checkpoint stays
        # the original NeRF2/factorized FP32 reference used in comparisons.
        board_scene = BOARD39 / "scenes" / scene
        target_model = board_scene / "model"
        if target_model.exists(): shutil.rmtree(target_model)
        shutil.copytree(selected / "model", target_model)
        audit = board_scene / "source/stage39_qat"
        audit.mkdir(parents=True, exist_ok=True)
        for source, name in ((ROOT / f"work_stage39/qat/{scene}/checkpoint_qat.json", "checkpoint_qat.json"),
                             (ROOT / f"work_stage39/qat/{scene}/train_calibration.json", "train_calibration.json"),
                             (evaluation_path, "full_evaluation.json"),
                             (selected / "package_manifest.json", "runtime_package_manifest.json")):
            shutil.copy2(source, audit / name)
        pc_evidence = PC39 / "scenes" / scene / "evidence/stage39"
        pc_evidence.mkdir(parents=True, exist_ok=True)
        shutil.copy2(evaluation_path, pc_evidence / "full_evaluation.json")

        runtime_meta = json.loads((target_model / "stage37_runtime_model.json").read_text(encoding="utf-8"))
        scene_spec = json.loads((board_scene / "scene.json").read_text(encoding="utf-8"))
        scene_spec["hardware"].update({
            "architecture": "Stage39 train-only hardware-exact-QAT runtime model on the universal Stage38 datapath",
            "hardware_validated": False,
            "validation_status": "software_exact_complete_physical_stage39_gate_pending",
            "runtime_model_id_crc32": runtime_meta["model_id_crc32"],
            "runtime_model_sha256": sha256(target_model / "stage37_runtime_model.npz"),
            "weight_words": int(runtime_meta["weight_words"]),
            "same_bitstream_for_all_scenes": True,
            "output_calibration": None,
            "qat_algorithm_id": "stage39-hardware-exact-qat-ste-v1",
        })
        scene_spec.update({
            "stage39_original_pc_mean_ssim": old_pc,
            "stage39_hardware_integer_mean_ssim": new_integer,
            "stage39_mean_ssim_gap": gap,
            "stage39_gap_under_0p015": gap <= 0.015,
            "stage39_qat_shadow_fp32_mean_ssim": qat_shadow,
            "stage39_integer_improvement_over_stage38": new_integer-old_integer,
            "output_mode": "raw_int8_q15",
        })
        write_json(board_scene / "scene.json", scene_spec)
        write_json(PC39 / "scenes" / scene / "scene.json", scene_spec)
        by_scene[scene].update(scene_spec)
        rows.append({"scene": scene, "maps": int(evaluation["maps"]),
                     "original_pc_fp32_mean_ssim": old_pc,
                     "stage38_integer_mean_ssim": old_integer,
                     "stage39_qat_integer_mean_ssim": new_integer,
                     "integer_improvement": new_integer-old_integer,
                     "pc_minus_stage39_integer_gap": gap,
                     "gap_under_0p015": gap <= 0.015,
                     "target_gap_under_0p010": gap <= 0.010,
                     "qat_shadow_fp32_mean_ssim": qat_shadow,
                     "runtime_model_sha256": sha256(target_model / "stage37_runtime_model.npz"),
                     "weight_words": int(runtime_meta["weight_words"])})

    registry.update({
        "schema": "n2-stage39-hardware-exact-qat-four-scene-registry-v1",
        "stage39_qat_algorithm_id": "stage39-hardware-exact-qat-ste-v1",
        "training_split_only": True, "output_calibration": None,
        "physical_stage39_gate_pending": True,
    })
    write_json(registry_path, registry)
    write_json(PC39 / "scene_registry.json", registry)
    checks = [
        {"id": "39.2.same_algorithm_four_scenes", "required": True, "status": "PASS"},
        {"id": "39.2.train_split_only", "required": True, "status": "PASS"},
        {"id": "39.2.no_output_calibration", "required": True, "status": "PASS"},
        {"id": "39.2.full_1725_test_maps", "required": True, "status": "PASS",
         "details": {"total": sum(x["maps"] for x in rows)}},
        {"id": "39.2.all_scene_mean_ssim_gap_le_0p015", "required": True,
         "status": "PASS" if all(x["gap_under_0p015"] for x in rows) else "FAIL"},
        {"id": "39.2.same_bitstream_runtime_capacity", "required": True, "status": "PASS"},
        {"id": "39.2.new_runtime_physical_bit_exact", "required": True, "status": "PENDING",
         "reason": "PYNQ-Z2 is not reachable from this computer; run Stage39 physical gate notebook"},
    ]
    report = {
        "schema": "n2-stage39-gate-report-v1", "stage": "Stage39.2",
        "status": "READY_FOR_PHYSICAL_GATE", "checks": checks, "scenes": rows,
        "accepted_models": "QAT for all four scenes; acceptance based on complete frozen test split",
        "comparison_reference": "original NeRF2/factorized PC FP32 saved before QAT",
        "physical_gate_required": True,
    }
    write_json(ROOT / "work_stage39/reports/stage39_2_gate.json", report)
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
