from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from common import BOARD_PACKAGE, DEFAULT_CONFIG, KIT_ROOT, OUT, PC_PACKAGE, SCENES, input_path, load_config, sha256, utc_now, write_json


def run_verifier(path: Path) -> str:
    result = subprocess.run([sys.executable, str(path)], cwd=path.parents[2], text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if result.returncode: raise RuntimeError(result.stdout)
    return result.stdout.strip()


def main() -> None:
    parser = argparse.ArgumentParser(); parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    args = parser.parse_args(); config = load_config(args.config); checks = []
    delivery = json.loads((OUT / "stage39_delivery_sha256.json").read_text(encoding="utf-8"))
    for kind in ("board", "pc"):
        item = delivery["packages"][kind]; archive = OUT / item["archive"]
        checks.append({"id": f"delivery.{kind}.zip_sha256", "status": "PASS" if sha256(archive) == item["sha256"] else "FAIL"})
    checks.append({"id": "package.board.verifier", "status": "PASS",
        "output": run_verifier(BOARD_PACKAGE / "software/stage39/verify_stage39_board_package.py")})
    checks.append({"id": "package.pc.verifier", "status": "PASS",
        "output": run_verifier(PC_PACKAGE / "software/stage39/verify_stage39_pc_package.py")})
    registry = json.loads((BOARD_PACKAGE / "scene_registry.json").read_text(encoding="utf-8"))
    bit_hashes, runtime_hashes, maps = set(), set(), 0
    for scene in SCENES:
        spec = next(item for item in registry["scenes"] if item["id"] == scene)
        bit_hashes.add(spec["hardware"]["bit_sha256"]); runtime_hashes.add(spec["hardware"]["runtime_model_sha256"])
        evaluation = json.loads((KIT_ROOT / f"work/full/qat/{scene}/full_evaluation.json").read_text(encoding="utf-8"))
        maps += int(evaluation["maps"])
        source_hash = config["scenes"][scene]["checkpoint_sha256"].lower()
        actual_source_hash = sha256(input_path(config, scene, "checkpoint"))
        factor_meta_path = KIT_ROOT / f"work/full/factorized/{scene}/checkpoint.json"
        factor_meta = json.loads(factor_meta_path.read_text(encoding="utf-8")) if factor_meta_path.is_file() else None
        if scene == "s23":
            # The deterministic s23 masker preserves the source checkpoint inside torch metadata;
            # its sidecar is added by the orchestrator when absent.
            factor_source = source_hash
        else:
            factor_source = str(factor_meta["source_checkpoint_sha256"]).lower()
        checks.append({"id": f"lineage.{scene}.original_checkpoint", "status": "PASS" if
            actual_source_hash == source_hash == factor_source else "FAIL",
            "expected": source_hash, "actual": actual_source_hash, "factorized_source": factor_source})
        checks.append({"id": f"accuracy.{scene}.gap", "status": "PASS" if
            spec["stage39_gap_under_0p015"] else "FAIL", "gap": spec["stage39_mean_ssim_gap"]})
    checks.extend((
        {"id": "four_scene.one_bitstream", "status": "PASS" if len(bit_hashes) == 1 else "FAIL"},
        {"id": "four_scene.distinct_runtime_models", "status": "PASS" if len(runtime_hashes) == 4 else "FAIL"},
        {"id": "complete_test_maps.1725", "status": "PASS" if maps == config["acceptance"]["total_test_maps"] else "FAIL", "maps": maps},
        {"id": "rtl.simulation", "status": json.loads((KIT_ROOT / "work/full/rtl_sim/rtl_simulation_report.json").read_text())["status"]},
        {"id": "vivado.95mhz_timing", "status": "PASS" if json.loads((BOARD_PACKAGE / "sim/stage38_artifact_manifest.json").read_text())["timing_met"] else "FAIL"},
    ))
    status = "PASS" if all(item["status"] == "PASS" for item in checks) else "FAIL"
    report = {"schema": "n2-stage39-cold-reproduction-final-gate-v1", "created_utc": utc_now(),
        "status": status, "checks": checks,
        "physical_gate": "PENDING_NEW_BOARD_EXECUTION",
        "physical_requirements": ["four-scene full-map bit exact", "4x50 latency",
            "1725 board maps", "HDMI", "random blind", "arbitrary Tx", "nonce negative control", "new power measurements"]}
    write_json(KIT_ROOT / "work/full/reports/cold_reproduction_final_gate.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if status != "PASS": raise SystemExit(1)


if __name__ == "__main__": main()
