#!/usr/bin/env python3
from __future__ import annotations

import json
import hashlib
from pathlib import Path

import numpy as np

from stage39_common import BOARD39, PC39, REPORTS, ROOT, SCENES, gate_report, write_json
from stage39_integer_reference import IntegerReference


def challenge_report(scene: str) -> Path:
    candidates = sorted((PC39 / "scenes" / scene / "evidence/stage38_pc_app").glob("*/report.json"))
    for path in reversed(candidates):
        value = json.loads(path.read_text(encoding="utf-8"))
        challenge = value.get("board", {}).get("random_ray_challenge")
        if challenge and challenge.get("arm", {}).get("arm_vs_fpga_bit_exact"):
            return path
    raise FileNotFoundError(scene + ": no saved random-ray transcript")


def full_map_hash_audit(scene: str, reference: IntegerReference) -> dict[str, object]:
    validation_path = (PC39 / "scenes" / scene /
                       "evidence/stage38_pc_app/all_1225/validation.json")
    validation = json.loads(validation_path.read_text(encoding="utf-8"))
    row = validation["rows"][0]
    indices = np.arange(32400, dtype=np.int64)
    actual, seconds = reference.run_indices(np.asarray(row["tx"], np.float32), indices)
    digest = hashlib.sha256(actual.reshape(90, 360).tobytes()).hexdigest()
    return {"scene": scene, "report": str(validation_path.relative_to(ROOT)),
            "spectrum_id": row["spectrum_id"], "ray_count": 32400,
            "expected_raw_q15_sha256": row["fpga_raw_q15_sha256"],
            "pc_integer_raw_q15_sha256": digest,
            "bit_exact": digest == row["fpga_raw_q15_sha256"],
            "mismatch_count": None, "max_abs_q15_delta": None,
            "pc_integer_seconds": seconds,
            "comparison": "complete 90x360 PC integer map SHA-256 vs saved physical FPGA raw-Q15 SHA-256"}


def main() -> None:
    build = json.loads((ROOT / "work_stage39/runtime_packages/build_report.json").read_text(encoding="utf-8"))
    layouts, audits = [], []
    for scene in SCENES:
        package = ROOT / "work_stage39/runtime_packages" / scene
        manifest = json.loads((package / "package_manifest.json").read_text(encoding="utf-8"))
        layouts.append(sorted(manifest["package_files"].keys()))
        reference = IntegerReference(BOARD39 / "scenes" / scene)
        try:
            report_path = challenge_report(scene)
        except FileNotFoundError:
            audits.append(full_map_hash_audit(scene, reference))
        else:
            report = json.loads(report_path.read_text(encoding="utf-8"))
            board = report["board"]; challenge = board["random_ray_challenge"]
            actual, seconds = reference.run_indices(np.asarray(board["tx"], np.float32),
                                                    np.asarray(challenge["ray_indices"], np.int64))
            expected = np.asarray(challenge["rerun_q15"], np.uint32)
            audits.append({"scene": scene, "report": str(report_path.relative_to(ROOT)),
                           "ray_count": len(actual), "bit_exact": bool(np.array_equal(actual, expected)),
                           "mismatch_count": int(np.count_nonzero(actual != expected)),
                           "max_abs_q15_delta": int(np.max(np.abs(actual.astype(np.int64)-expected.astype(np.int64)))),
                           "pc_integer_seconds": seconds,
                           "comparison": "selected PC integer Q15 values vs saved physical FPGA random-ray values"})
    checks = [
        {"id": "39.1.four_packages_generated", "required": True,
         "status": "PASS" if len(build.get("scenes", [])) == 4 else "FAIL"},
        {"id": "39.1.identical_package_structure", "required": True,
         "status": "PASS" if all(value == layouts[0] for value in layouts[1:]) else "FAIL"},
        {"id": "39.1.exact_baseline_reproduction", "required": True,
         "status": "PASS" if build.get("all_exact_stage38_reproductions") else "FAIL"},
        {"id": "39.1.pc_integer_vs_physical_fpga_random_rays", "required": True,
         "status": "PASS" if all(value["bit_exact"] for value in audits) else "FAIL",
         "details": audits},
        {"id": "39.1.no_manual_array_or_source_edit", "required": True,
         "status": "PASS" if all(not value["manual_array_or_source_edit_required"]
                                  for value in build["scenes"]) else "FAIL"},
        {"id": "39.1.manifest_and_hashes_generated", "required": True,
         "status": "PASS" if all(value.get("package_tree_sha256") and value.get("package_files")
                                  for value in build["scenes"]) else "FAIL"},
    ]
    value = gate_report("Stage39.1", checks, evidence={
        "compiler": "work_stage39/build_scene_package.py",
        "configuration": "work_stage39/config/scenes.json",
        "build_report": "work_stage39/runtime_packages/build_report.json",
        "integer_reference": "work_stage39/stage39_integer_reference.py",
    })
    write_json(REPORTS / "stage39_1_gate.json", value)
    print(json.dumps(value, ensure_ascii=False, indent=2))
    if value["status"] != "PASS":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
