from __future__ import annotations

import argparse
import json
import os
import shutil
from pathlib import Path

import numpy as np

from common import (
    BOARD_TEMPLATE, DEFAULT_CONFIG, KIT_ROOT, PC_TEMPLATE, SCENES, files_manifest,
    input_path, load_config, python, run, sha256, utc_now, write_json,
)


STAGE37 = KIT_ROOT / "02_model_pipeline/stage37"
STAGE39 = KIT_ROOT / "02_model_pipeline/stage39_core"
BUILDER = KIT_ROOT / "03_package_builder"


def profile_root(profile: str) -> Path:
    return KIT_ROOT / "work" / profile


def factorized(profile: str, scene: str) -> Path:
    return profile_root(profile) / "factorized" / scene / "checkpoint.tar"


def qat_root(profile: str, scene: str) -> Path:
    return profile_root(profile) / "qat" / scene


def runtime_root(profile: str, scene: str) -> Path:
    return profile_root(profile) / "runtime" / scene


def verify_qat_inputs(config: dict, profile: str, scene: str) -> None:
    """Reject stale or mixed QAT files before evaluation or packaging."""
    spec, qcfg, ccfg = config["scenes"][scene], config["qat"], config["calibration"]
    qr = qat_root(profile, scene)
    required = [
        qr / "checkpoint_qat.tar", qr / "checkpoint_qat.json",
        qr / "train_calibration.npy", qr / "train_calibration.json",
        qr / "ptq_train_only/manifest_ray.json",
        qr / "qat_export/manifest_ray.json", qr / "qat_export/tv3_ray_net.txt",
    ]
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise FileNotFoundError(
            scene + ": incomplete QAT stage; rerun the qat stage; missing: " + ", ".join(missing)
        )
    checkpoint_meta = json.loads((qr / "checkpoint_qat.json").read_text(encoding="utf-8"))
    calibration_meta = json.loads((qr / "train_calibration.json").read_text(encoding="utf-8"))
    ptq_manifest_path = qr / "ptq_train_only/manifest_ray.json"
    ptq_manifest = json.loads(ptq_manifest_path.read_text(encoding="utf-8"))
    qat_manifest = json.loads((qr / "qat_export/manifest_ray.json").read_text(encoding="utf-8"))
    checks = {
        "checkpoint scene": (checkpoint_meta.get("scene"), scene),
        "checkpoint source": (checkpoint_meta.get("source_checkpoint_sha256"),
                              sha256(factorized(profile, scene))),
        "QAT steps": (int(checkpoint_meta.get("steps", -1)),
                      2 if profile == "smoke" else int(qcfg["steps"])),
        "QAT batch": (int(checkpoint_meta.get("batch", -1)),
                      min(16, int(qcfg["batch"])) if profile == "smoke" else int(qcfg["batch"])),
        "QAT learning rate": (float(checkpoint_meta.get("learning_rate", -1.0)),
                              float(qcfg["learning_rate"])),
        "QAT seed": (int(checkpoint_meta.get("seed", -1)), int(qcfg["seed"])),
        "QAT quant manifest": (checkpoint_meta.get("quant_manifest_sha256"),
                               sha256(ptq_manifest_path)),
        "calibration scene": (calibration_meta.get("scene"), scene),
        "calibration count": (int(calibration_meta.get("count", -1)), int(ccfg["count"])),
        "calibration seed": (int(calibration_meta.get("seed", -1)), int(ccfg["seed"])),
        "activation percentile": (float(ptq_manifest.get("stage37_activation_percentile", -1.0)),
                                  float(spec["activation_percentile"])),
        "export quant contract": (qat_manifest.get("quant_contract_sha256"),
                                  sha256(ptq_manifest_path)),
        "export QAT checkpoint": (qat_manifest.get("qat_checkpoint_sha256"),
                                  sha256(qr / "checkpoint_qat.tar")),
    }
    mismatches = [f"{name}: {actual!r} != {expected!r}"
                  for name, (actual, expected) in checks.items() if actual != expected]
    if mismatches:
        raise RuntimeError(
            scene + ": stale or mixed QAT outputs; rerun the qat stage:\n" + "\n".join(mismatches)
        )


def selected_scenes(value: str) -> tuple[str, ...]:
    return SCENES if value == "all" else (value,)


def factorize(config: dict, profile: str, scene: str) -> None:
    spec = config["scenes"][scene]
    output = factorized(profile, scene)
    output.parent.mkdir(parents=True, exist_ok=True)
    if int(spec["factorization_steps"]) == 0:
        run([python(), STAGE37 / "make_s23_factorized_checkpoint.py",
             input_path(config, scene, "checkpoint"), output])
    else:
        fp = config["factorization"]
        steps = 2 if profile == "smoke" else int(spec["factorization_steps"])
        validation_maps = 1 if profile == "smoke" else int(fp["validation_maps"])
        run([python(), STAGE37 / "train_factorized_scene.py",
             "--scene", scene,
             "--reference", PC_TEMPLATE / "software/stage11/reference.py",
             "--checkpoint", input_path(config, scene, "checkpoint"),
             "--config", input_path(config, scene, "config"),
             "--datadir", input_path(config, scene, "dataset"),
             "--out", output,
             "--steps", steps, "--batch", fp["batch"], "--lr", fp["learning_rate"],
             "--seed", fp["seed"], "--validation-maps", validation_maps])
    if not output.is_file():
        raise RuntimeError(scene + ": factorized checkpoint was not generated")


def calibrate_ptq_qat(config: dict, profile: str, scene: str) -> None:
    spec, qr = config["scenes"][scene], qat_root(profile, scene)
    qr.mkdir(parents=True, exist_ok=True)
    source_checkpoint = factorized(profile, scene)
    if not source_checkpoint.is_file():
        raise FileNotFoundError("run factorize first: " + str(source_checkpoint))
    calibration = qr / "train_calibration.npy"
    c = config["calibration"]
    run([python(), STAGE39 / "make_train_only_calibration.py", "--scene", scene,
         "--datadir", input_path(config, scene, "dataset"), "--out", calibration,
         "--count", c["count"], "--seed", c["seed"]])
    ptq = qr / "ptq_train_only"
    environment = dict(os.environ); environment["N2_SOURCE_ROOT"] = str(PC_TEMPLATE)
    run([python(), STAGE37 / "export_ray_stage37.py", "--ckpt", source_checkpoint,
         "--config", input_path(config, scene, "config"), "--calibration", calibration,
         "--out", ptq, "--percentile", spec["activation_percentile"]], env=environment)
    q = config["qat"]
    qat_checkpoint = qr / "checkpoint_qat.tar"
    run([python(), STAGE39 / "train_hardware_exact_qat.py", "--scene", scene,
         "--reference", PC_TEMPLATE / "software/stage11/reference.py",
         "--fixed-renderer", BOARD_TEMPLATE / "software/stage24/fixed_renderer.py",
         "--checkpoint", source_checkpoint, "--config", input_path(config, scene, "config"),
         "--datadir", input_path(config, scene, "dataset"),
         "--quant-manifest", ptq / "manifest_ray.json", "--out", qat_checkpoint,
         "--steps", 2 if profile == "smoke" else q["steps"],
         "--batch", min(16, int(q["batch"])) if profile == "smoke" else q["batch"],
         "--lr", q["learning_rate"], "--seed", q["seed"]])
    qat_export = qr / "qat_export"
    run([python(), STAGE39 / "export_fixed_quant.py",
         "--reference", PC_TEMPLATE / "software/stage11/reference.py",
         "--ckpt", qat_checkpoint, "--config", input_path(config, scene, "config"),
         "--calibration", calibration, "--quant-manifest", ptq / "manifest_ray.json",
         "--out", qat_export])
    verify_qat_inputs(config, profile, scene)


def evaluate(config: dict, profile: str, scene: str) -> None:
    verify_qat_inputs(config, profile, scene)
    qr = qat_root(profile, scene)
    maps = profile_root(profile) / "maps" / scene
    limit = 1 if profile == "smoke" else 0
    common = [
        python(), BUILDER / "evaluate_stage39_cold.py", "--scene", scene,
        "--root", BOARD_TEMPLATE, "--reference", PC_TEMPLATE / "software/stage11/reference.py",
        "--checkpoint", factorized(profile, scene),
        "--config", input_path(config, scene, "config"),
        "--datadir", input_path(config, scene, "dataset"), "--limit", limit,
    ]
    run(common + ["--shadow-checkpoint", qr / "checkpoint_qat.tar",
        "--tv", qr / "qat_export/tv3_ray_net.txt",
        "--manifest", qr / "qat_export/manifest_ray.json",
        "--pc-map-dir", maps / "pc_fp32", "--integer-map-dir", maps / "qat_integer",
        "--out", qr / "full_evaluation.json"])
    run(common + ["--tv", qr / "ptq_train_only/tv3_ray_net.txt",
        "--manifest", qr / "ptq_train_only/manifest_ray.json",
        "--out", qr / "ptq_full_evaluation.json"])
    result = json.loads((qr / "full_evaluation.json").read_text(encoding="utf-8"))
    ptq = json.loads((qr / "ptq_full_evaluation.json").read_text(encoding="utf-8"))
    gate = {
        "schema": "n2-stage39-cold-scene-software-gate-v1", "scene": scene,
        "maps": result["maps"], "pc_mean_ssim": result["pc_fp32"]["summary"]["ssim"]["mean"],
        "qat_integer_mean_ssim": result["hardware_integer"]["summary"]["ssim"]["mean"],
        "ptq_integer_mean_ssim": ptq["hardware_integer"]["summary"]["ssim"]["mean"],
        "mean_ssim_gap": result["mean_ssim_gap_pc_minus_integer"],
        "maximum_mean_ssim_gap": config["acceptance"]["maximum_mean_ssim_gap"],
    }
    gate["gap_pass"] = gate["mean_ssim_gap"] <= gate["maximum_mean_ssim_gap"] + 1e-12
    gate["qat_not_worse_than_ptq"] = gate["qat_integer_mean_ssim"] + 1e-12 >= gate["ptq_integer_mean_ssim"]
    gate["status"] = "PASS" if gate["gap_pass"] and gate["qat_not_worse_than_ptq"] else "FAIL"
    write_json(qr / "software_gate.json", gate)
    if profile == "full" and gate["status"] != "PASS":
        raise RuntimeError(scene + ": Stage39 software acceptance failed: " + json.dumps(gate))


def build_runtime(config: dict, profile: str, scene: str) -> None:
    verify_qat_inputs(config, profile, scene)
    qr, destination = qat_root(profile, scene), runtime_root(profile, scene)
    if destination.exists():
        shutil.rmtree(destination)
    export_dir = destination / "hardware_export"
    sparse_dir = destination / "sparse"
    model_dir = destination / "model"
    for path in (export_dir, sparse_dir, model_dir):
        path.mkdir(parents=True, exist_ok=True)
    for name in ("tv3_ray_net.txt", "manifest_ray.json", "calibration_reference.npz"):
        shutil.copy2(qr / "qat_export" / name, export_dir / name)
    sparse_manifest = sparse_dir / "model_manifest.json"
    run([python(), BOARD_TEMPLATE / "software/stage29/build_sparse_model.py",
         "--tv", export_dir / "tv3_ray_net.txt", "--rtl-dir", sparse_dir,
         "--report", sparse_manifest])
    runtime_model = model_dir / "stage37_runtime_model.npz"
    run([python(), STAGE37 / "build_runtime_model.py",
         "--sparse-manifest", sparse_manifest, "--sparse-dir", sparse_dir,
         "--quant-manifest", export_dir / "manifest_ray.json",
         "--fixed-renderer", BOARD_TEMPLATE / "software/stage24/fixed_renderer.py",
         "--scene", scene, "--checkpoint", qr / "checkpoint_qat.tar", "--out", runtime_model])
    run([python(), STAGE37 / "build_scene_runtime_assets.py",
         "--evaluator", STAGE37 / "evaluate_fixed_scene.py", "--source-root", BOARD_TEMPLATE,
         "--scene", scene, "--tv", export_dir / "tv3_ray_net.txt",
         "--manifest", export_dir / "manifest_ray.json",
         "--datadir", input_path(config, scene, "dataset"),
         "--runtime-model", runtime_model, "--out", model_dir])
    shutil.copy2(export_dir / "manifest_ray.json", model_dir / "quant_manifest.json")
    shutil.copy2(qr / "checkpoint_qat.tar", destination / "checkpoint_qat.tar")
    shutil.copy2(input_path(config, scene, "config"), destination / "config.yml")
    runtime_meta = json.loads((model_dir / "stage37_runtime_model.json").read_text(encoding="utf-8"))
    manifest = {
        "schema": "n2-stage39-selected-runtime-package-v1", "scene": scene,
        "selection": "hardware-exact-qat", "selection_reason": "complete frozen test gate",
        "checkpoint_sha256": sha256(qr / "checkpoint_qat.tar"),
        "runtime_model_sha256": sha256(runtime_model),
        "weight_words": int(runtime_meta["weight_words"]),
        "weight_capacity": int(config["acceptance"]["weight_capacity_words"]),
        "fits_unchanged_bitstream": int(runtime_meta["weight_words"]) <= int(config["acceptance"]["weight_capacity_words"]),
        "output_calibration": None, "created_utc": utc_now(),
        "files": files_manifest(destination, {"package_manifest.json"}),
    }
    write_json(destination / "package_manifest.json", manifest)
    if not manifest["fits_unchanged_bitstream"]:
        raise RuntimeError(scene + ": runtime model exceeds 3072-word bitstream capacity")


def run_stage(stage: str, config: dict, profile: str, scene: str) -> None:
    if stage == "factorize": factorize(config, profile, scene)
    elif stage == "qat": calibrate_ptq_qat(config, profile, scene)
    elif stage == "evaluate": evaluate(config, profile, scene)
    elif stage == "runtime": build_runtime(config, profile, scene)
    else: raise ValueError(stage)


def main() -> None:
    parser = argparse.ArgumentParser(description="Cold checkpoint-to-runtime Stage39 model pipeline")
    parser.add_argument("stage", choices=("factorize", "qat", "evaluate", "runtime", "all"))
    parser.add_argument("--scene", choices=(*SCENES, "all"), default="all")
    parser.add_argument("--profile", choices=("full", "smoke"), default="full")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    args = parser.parse_args(); config = load_config(args.config)
    stages = ("factorize", "qat", "evaluate", "runtime") if args.stage == "all" else (args.stage,)
    for stage in stages:
        for scene in selected_scenes(args.scene):
            run_stage(stage, config, args.profile, scene)
    print(json.dumps({"ok": True, "profile": args.profile, "stages": stages,
                      "scenes": selected_scenes(args.scene)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
