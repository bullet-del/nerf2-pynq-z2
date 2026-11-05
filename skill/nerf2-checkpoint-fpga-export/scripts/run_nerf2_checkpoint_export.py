#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


CAPACITY_WORDS = 3072
FORMAL_DEFAULTS = {
    "factorization_steps": 3000,
    "factorization_batch": 512,
    "factorization_lr": 2e-4,
    "factorization_validation_maps": 20,
    "calibration_count": 8192,
    "activation_percentile": 99.5,
    "qat_steps": 1800,
    "qat_batch": 192,
    "qat_lr": 1e-5,
    "evaluation_limit": 0,
}
EXPECTED_SHAPES = {
    "attenuation_linears.0.weight": (256, 63),
    "attenuation_linears.5.weight": (256, 319),
    "attenuation_linears.6.weight": (256, 256),
    "attenuation_linears.7.weight": (256, 256),
    "attenuation_output.weight": (2, 256),
    "feature_layer.weight": (256, 256),
    "signal_linears.0.weight": (256, 382),
    "signal_linears.1.weight": (128, 256),
    "signal_output.weight": (2, 128),
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n", encoding="utf-8")


def absolute(path: Path, base: Path) -> Path:
    value = path.expanduser()
    if not value.is_absolute():
        value = base / value
    return Path(os.path.abspath(value))


def load_indexes(path: Path) -> list[str]:
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def validate_inputs(args: argparse.Namespace, project: Path) -> dict[str, Any]:
    required_project = [
        project / "src/model/stage37/train_factorized_scene.py",
        project / "src/model/stage37/export_ray_stage37.py",
        project / "src/model/stage37/build_runtime_model.py",
        project / "src/model/stage37/build_scene_runtime_assets.py",
        project / "src/model/stage39_core/make_train_only_calibration.py",
        project / "src/model/stage39_core/train_hardware_exact_qat.py",
        project / "src/model/stage39_core/export_fixed_quant.py",
        project / "build/pipeline/evaluate_stage39_cold.py",
        project / "src/host/software/stage11/reference.py",
        project / "board/source/software/stage24/fixed_renderer.py",
        project / "board/source/software/stage29/build_sparse_model.py",
    ]
    required_input = [
        args.checkpoint, args.config,
        args.dataset / "train_index.txt", args.dataset / "test_index.txt",
        args.dataset / "tx_pos.csv", args.dataset / "gateway_info.yml",
    ]
    missing = [str(path) for path in required_project + required_input if not path.is_file()]
    if missing:
        raise FileNotFoundError("missing required files: " + ", ".join(missing))
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", args.scene):
        raise ValueError("scene must contain only letters, digits, dot, underscore, or hyphen")
    train = load_indexes(args.dataset / "train_index.txt")
    test = load_indexes(args.dataset / "test_index.txt")
    if not train or not test:
        raise ValueError("train_index.txt and test_index.txt must both be nonempty")
    if len(set(train)) != len(train) or len(set(test)) != len(test):
        raise ValueError("train_index.txt and test_index.txt must not contain duplicate IDs")
    overlap = sorted(set(train) & set(test))
    if overlap:
        raise ValueError("train/test split overlap: " + ", ".join(overlap[:10]))
    try:
        numeric_ids = [int(value) for value in train + test]
    except ValueError as exc:
        raise ValueError("train/test IDs must be positive base-10 integers") from exc
    if min(numeric_ids) < 1:
        raise ValueError("train/test IDs must be one-based positive integers")
    samples = sorted((args.dataset / "spectrum").glob("*.png"))
    if not samples:
        raise FileNotFoundError(args.dataset / "spectrum/*.png")
    missing_spectra = [value for value in train + test
                       if not (args.dataset / "spectrum" / f"{value}.png").is_file()]
    if missing_spectra:
        raise FileNotFoundError("missing split spectrum PNGs: " + ", ".join(missing_spectra[:10]))
    with (args.dataset / "tx_pos.csv").open("r", encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.reader(stream))
    if len(rows) < 2 or len(rows[0]) != 3 or any(len(row) != 3 for row in rows[1:]):
        raise ValueError("tx_pos.csv must contain a three-column header and three values per transmitter")
    if max(numeric_ids) > len(rows) - 1:
        raise ValueError("a split ID exceeds the number of rows in tx_pos.csv")
    import yaml
    config = yaml.safe_load(args.config.read_text(encoding="utf-8"))
    expected_config = {
        "networks.D": (config.get("networks") or {}).get("D"),
        "networks.W": (config.get("networks") or {}).get("W"),
        "networks.skips": (config.get("networks") or {}).get("skips"),
        "networks.multires": (config.get("networks") or {}).get("multires"),
        "networks.input_dims": (config.get("networks") or {}).get("input_dims"),
        "networks.is_embeded": (config.get("networks") or {}).get("is_embeded"),
        "render.n_samples": (config.get("render") or {}).get("n_samples"),
        "render.near": (config.get("render") or {}).get("near"),
        "render.far": (config.get("render") or {}).get("far"),
    }
    required_config = {
        "networks.D": 8, "networks.W": 256, "networks.skips": [4],
        "networks.multires": {"pts": 10, "view": 10, "tx": 10},
        "networks.input_dims": {"pts": 3, "view": 3, "tx": 3},
        "networks.is_embeded": {"pts": True, "view": True, "tx": True},
        "render.n_samples": 64, "render.near": 0, "render.far": 5,
    }
    mismatches = [f"{key}={expected_config[key]!r}, expected {expected!r}"
                  for key, expected in required_config.items() if expected_config[key] != expected]
    if mismatches:
        raise ValueError("config is not Stage39-compatible: " + "; ".join(mismatches))
    gateway = yaml.safe_load((args.dataset / "gateway_info.yml").read_text(encoding="utf-8"))
    gateway1 = (gateway or {}).get("gateway1") or {}
    if len(gateway1.get("position") or []) != 3 or len(gateway1.get("orientation") or []) != 4:
        raise ValueError("gateway_info.yml must define gateway1 position[3] and orientation[4]")
    from PIL import Image
    for spectrum_id in dict.fromkeys(train + test):
        with Image.open(args.dataset / "spectrum" / f"{spectrum_id}.png") as image:
            if image.size != (360, 90):
                raise ValueError(f"spectrum/{spectrum_id}.png is {image.size}; expected 360x90")
    return {
        "checkpoint_sha256": sha256(args.checkpoint),
        "config_sha256": sha256(args.config),
        "train_count": len(train),
        "test_count": len(test),
        "spectrum_png_count": len(samples),
        "sample_resolution": [90, 360],
    }


def validate_checkpoint(path: Path) -> dict[str, Any]:
    import torch
    try:
        # PyTorch 2.6 changed the default of weights_only.  NeRF2 checkpoints
        # contain a training dictionary, so explicitly request the historical
        # behavior.  Only locally produced or otherwise trusted checkpoints
        # should be supplied to this pipeline.
        value = torch.load(path, map_location="cpu", weights_only=False)
    except TypeError:  # PyTorch releases before the weights_only argument.
        value = torch.load(path, map_location="cpu")
    state = value.get("nerf2_network_state_dict")
    if not isinstance(state, dict):
        raise ValueError("checkpoint lacks nerf2_network_state_dict")
    actual: dict[str, list[int]] = {}
    failures = []
    for name, expected in EXPECTED_SHAPES.items():
        if name not in state:
            failures.append(name + " is missing")
            continue
        shape = tuple(int(x) for x in state[name].shape)
        actual[name] = list(shape)
        if shape != expected:
            failures.append(f"{name}: {shape} != {expected}")
    if failures:
        raise ValueError("checkpoint is not Stage39-architecture compatible: " + "; ".join(failures))
    att = state["attenuation_linears.5.weight"][:, 63:]
    sig = state["signal_linears.0.weight"][:, :256]
    return {
        "expected_shapes_pass": True,
        "tensor_shapes": actual,
        "source_removed_block_norms": {
            "attenuation_l2": float(torch.linalg.vector_norm(att)),
            "attenuation_max_abs": float(att.abs().max()),
            "signal_l2": float(torch.linalg.vector_norm(sig)),
            "signal_max_abs": float(sig.abs().max()),
        },
    }


def stream(argv: list[str], cwd: Path, log_path: Path, env: dict[str, str] | None = None) -> int:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    child_env = dict(os.environ) if env is None else dict(env)
    child_env["PYTHONIOENCODING"] = "utf-8"
    with log_path.open("w", encoding="utf-8", newline="") as log:
        process = subprocess.Popen(
            argv, cwd=cwd, env=child_env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", shell=False,
        )
        assert process.stdout is not None
        for line in process.stdout:
            print(line, end="")
            log.write(line)
        return process.wait()


def markdown(report: dict[str, Any]) -> str:
    accuracy = report.get("accuracy") or {}
    runtime = report.get("runtime") or {}
    lines = [
        "# NeRF2 checkpoint FPGA export report", "",
        f"- Scene: `{report['scene']}`",
        f"- Status: **{report['status']}**",
        f"- Formal defaults used: `{str(report.get('formal_defaults_used')).lower()}`",
        f"- Started: {report['started_utc']}",
        f"- Finished: {report['finished_utc']}",
        f"- Runtime NPZ: `{runtime.get('npz', '')}`",
        f"- Current bitstream compatible: `{str(runtime.get('current_bitstream_compatible')).lower()}`",
        "", "## Descriptive accuracy", "",
        "These values are reported without an SSIM acceptance threshold.", "",
        f"- Factorized FP32 mean SSIM: `{accuracy.get('factorized_fp32_mean_ssim')}`",
        f"- Hardware-integer mean SSIM: `{accuracy.get('hardware_integer_mean_ssim')}`",
        f"- FP32 minus integer mean SSIM: `{accuracy.get('mean_ssim_gap')}`",
        f"- Factorized FP32 mean PSNR (dB): `{accuracy.get('factorized_fp32_mean_psnr_db')}`",
        f"- Hardware-integer mean PSNR (dB): `{accuracy.get('hardware_integer_mean_psnr_db')}`",
        f"- Factorized FP32 mean MSE: `{accuracy.get('factorized_fp32_mean_mse')}`",
        f"- Hardware-integer mean MSE: `{accuracy.get('hardware_integer_mean_mse')}`",
        f"- Factorized FP32 mean MAE: `{accuracy.get('factorized_fp32_mean_mae')}`",
        f"- Hardware-integer mean MAE: `{accuracy.get('hardware_integer_mean_mae')}`",
        "", "## Runtime capacity", "",
        f"- Required 1024-bit weight words: `{runtime.get('weight_words')}`",
        f"- Current bitstream capacity: `{CAPACITY_WORDS}`",
        "- This is PL runtime parameter memory, not SD-card storage.",
        "", "## Steps", "",
        "| Step | Status | Seconds | Log |", "|---|---:|---:|---|",
    ]
    for step in report.get("steps", []):
        lines.append(f"| `{step['id']}` | {step['status']} | {step['duration_s']:.3f} | `{step.get('log', '')}` |")
    if report.get("failure"):
        lines.extend(["", "## First failure", "", report["failure"]])
    lines.extend([
        "", "## Evidence boundary", "",
        "This report proves software conversion and descriptive evaluation only. Physical board loading, bit-exact execution, latency, HDMI, and power require separate evidence.",
    ])
    return "\n".join(lines) + "\n"


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Convert one compatible NeRF2 checkpoint to a Stage39 FPGA runtime model")
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--scene", required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--factorization-steps", type=int, default=3000)
    parser.add_argument("--factorization-batch", type=int, default=512)
    parser.add_argument("--factorization-lr", type=float, default=2e-4)
    parser.add_argument("--factorization-validation-maps", type=int, default=20)
    parser.add_argument("--calibration-count", type=int, default=8192)
    parser.add_argument("--activation-percentile", type=float, default=99.5)
    parser.add_argument("--qat-steps", type=int, default=1800)
    parser.add_argument("--qat-batch", type=int, default=192)
    parser.add_argument("--qat-lr", type=float, default=1e-5)
    parser.add_argument("--evaluation-limit", type=int, default=0, help="0 means complete test split")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    project = absolute(args.project_root, Path.cwd())
    args.checkpoint = absolute(args.checkpoint, project)
    args.config = absolute(args.config, project)
    args.dataset = absolute(args.dataset, project)
    output = absolute(args.output_dir, project)
    if args.factorization_steps < 1 or args.qat_steps < 1:
        raise ValueError("factorization-steps and qat-steps must be positive")
    if args.factorization_batch < 1 or args.qat_batch < 1:
        raise ValueError("factorization-batch and qat-batch must be positive")
    if args.factorization_validation_maps < 1:
        raise ValueError("factorization-validation-maps must be positive")
    if args.calibration_count < 1024:
        raise ValueError("calibration-count must be at least 1024")
    if not 0.0 < args.activation_percentile <= 100.0:
        raise ValueError("activation-percentile must be in (0, 100]")
    if args.evaluation_limit < 0:
        raise ValueError("evaluation-limit must be zero or positive")
    if output in {project, project.parent, Path(output.anchor)}:
        raise ValueError("output-dir must be a dedicated child directory")
    if output.exists() and any(output.iterdir()) and not args.dry_run:
        raise FileExistsError("output-dir is not empty; choose a new directory to avoid mixed artifacts")
    output.mkdir(parents=True, exist_ok=True)

    started = utc_now()
    inputs = validate_inputs(args, project)
    checkpoint_contract = validate_checkpoint(args.checkpoint)
    formal_defaults_used = all(getattr(args, key) == value for key, value in FORMAL_DEFAULTS.items())
    py = sys.executable
    stage37 = project / "src/model/stage37"
    stage39 = project / "src/model/stage39_core"
    builder = project / "build/pipeline"
    pc = project / "src/host"
    board = project / "board/source"
    reference = pc / "software/stage11/reference.py"
    fixed_renderer = board / "software/stage24/fixed_renderer.py"

    factorized = output / "factorized/checkpoint.tar"
    qat = output / "qat"
    calibration = qat / "train_calibration.npy"
    ptq = qat / "ptq_train_only"
    qat_checkpoint = qat / "checkpoint_qat.tar"
    qat_export = qat / "qat_export"
    evaluation = output / "evaluation/full_evaluation.json"
    export_dir = output / "runtime/hardware_export"
    sparse_dir = output / "runtime/sparse"
    model_dir = output / "runtime/model"
    sparse_manifest = sparse_dir / "model_manifest.json"
    runtime_npz = model_dir / "stage37_runtime_model.npz"

    environment = dict(os.environ)
    environment["N2_SOURCE_ROOT"] = str(pc)
    plan: list[tuple[str, list[str], dict[str, str] | None]] = [
        ("constrained-adaptation", [py, str(stage37 / "train_factorized_scene.py"), "--scene", args.scene,
         "--reference", str(reference), "--checkpoint", str(args.checkpoint), "--config", str(args.config),
         "--datadir", str(args.dataset), "--out", str(factorized), "--steps", str(args.factorization_steps),
         "--batch", str(args.factorization_batch), "--lr", str(args.factorization_lr), "--seed", "20260905",
         "--validation-maps", str(args.factorization_validation_maps)], None),
        ("train-only-calibration", [py, str(stage39 / "make_train_only_calibration.py"), "--scene", args.scene,
         "--datadir", str(args.dataset), "--out", str(calibration), "--count", str(args.calibration_count),
         "--seed", "3902"], None),
        ("ptq-contract", [py, str(stage37 / "export_ray_stage37.py"), "--ckpt", str(factorized),
         "--config", str(args.config), "--calibration", str(calibration), "--out", str(ptq),
         "--percentile", str(args.activation_percentile)], environment),
        ("hardware-exact-qat", [py, str(stage39 / "train_hardware_exact_qat.py"), "--scene", args.scene,
         "--reference", str(reference), "--fixed-renderer", str(fixed_renderer), "--checkpoint", str(factorized),
         "--config", str(args.config), "--datadir", str(args.dataset), "--quant-manifest", str(ptq / "manifest_ray.json"),
         "--out", str(qat_checkpoint), "--steps", str(args.qat_steps), "--batch", str(args.qat_batch),
         "--lr", str(args.qat_lr), "--seed", "3902"], None),
        ("fixed-quant-export", [py, str(stage39 / "export_fixed_quant.py"), "--reference", str(reference),
         "--ckpt", str(qat_checkpoint), "--config", str(args.config), "--calibration", str(calibration),
         "--quant-manifest", str(ptq / "manifest_ray.json"), "--out", str(qat_export)], None),
        ("descriptive-full-test", [py, str(builder / "evaluate_stage39_cold.py"), "--scene", args.scene,
         "--root", str(board), "--reference", str(reference), "--checkpoint", str(factorized),
         "--shadow-checkpoint", str(qat_checkpoint), "--config", str(args.config),
         "--tv", str(qat_export / "tv3_ray_net.txt"), "--manifest", str(qat_export / "manifest_ray.json"),
         "--datadir", str(args.dataset), "--limit", str(args.evaluation_limit),
         "--pc-map-dir", str(output / "maps/pc_fp32"), "--integer-map-dir", str(output / "maps/qat_integer"),
         "--out", str(evaluation)], None),
    ]

    if args.dry_run:
        planned_steps = [
            {"id": name, "status": "PLANNED", "duration_s": 0.0, "log": "", "argv": argv}
            for name, argv, _ in plan
        ]
        planned_steps.extend([
            {"id": "exact-sparse-export", "status": "PLANNED", "duration_s": 0.0, "log": "", "argv": []},
            {"id": "runtime-model-pack", "status": "PLANNED", "duration_s": 0.0, "log": "", "argv": []},
            {"id": "fixed-rx-runtime-assets", "status": "PLANNED_IF_CAPACITY_FITS",
             "duration_s": 0.0, "log": "", "argv": []},
        ])
        report = {
            "schema": "n2-generic-checkpoint-fpga-export-report-v1", "scene": args.scene,
            "status": "PLANNED", "started_utc": started, "finished_utc": utc_now(),
            "dry_run": True, "inputs": inputs, "checkpoint_contract": checkpoint_contract,
            "configuration": vars_for_report(args),
            "steps": planned_steps,
            "formal_defaults_used": formal_defaults_used,
            "accuracy": None, "runtime": None, "failure": None,
        }
        write_json(output / "deployment_report.json", report)
        (output / "deployment_report.md").write_text(markdown(report), encoding="utf-8")
        print(output / "deployment_report.json")
        return

    steps: list[dict[str, Any]] = []
    failure: str | None = None

    def execute(step_id: str, argv: list[str], env: dict[str, str] | None = None) -> bool:
        nonlocal failure
        log = output / "logs" / f"{len(steps)+1:02d}_{step_id}.log"
        print(f"\n[{step_id}] {subprocess.list2cmdline(argv)}", flush=True)
        begin = time.perf_counter()
        code = stream(argv, project, log, env)
        elapsed = time.perf_counter() - begin
        status = "PASS" if code == 0 else "FAIL"
        steps.append({"id": step_id, "status": status, "duration_s": elapsed,
                      "return_code": code, "log": str(log.relative_to(output)), "argv": argv})
        if code:
            failure = f"Step {step_id} failed with return code {code}; see {log}."
        return code == 0

    for step_id, argv, env in plan:
        if not execute(step_id, argv, env):
            break

    runtime: dict[str, Any] | None = None
    if failure is None:
        for path in (export_dir, sparse_dir, model_dir):
            path.mkdir(parents=True, exist_ok=True)
        for name in ("tv3_ray_net.txt", "manifest_ray.json", "calibration_reference.npz"):
            shutil.copy2(qat_export / name, export_dir / name)
        runtime_plan = [
            ("exact-sparse-export", [py, str(board / "software/stage29/build_sparse_model.py"),
             "--tv", str(export_dir / "tv3_ray_net.txt"), "--rtl-dir", str(sparse_dir),
             "--report", str(sparse_manifest)]),
            ("runtime-model-pack", [py, str(stage37 / "build_runtime_model.py"),
             "--sparse-manifest", str(sparse_manifest), "--sparse-dir", str(sparse_dir),
             "--quant-manifest", str(export_dir / "manifest_ray.json"), "--fixed-renderer", str(fixed_renderer),
             "--scene", args.scene, "--checkpoint", str(qat_checkpoint), "--out", str(runtime_npz),
             "--allow-oversize"]),
        ]
        for step_id, argv in runtime_plan:
            if not execute(step_id, argv):
                break

    if failure is None:
        runtime_meta_path = model_dir / "stage37_runtime_model.json"
        runtime_meta = json.loads(runtime_meta_path.read_text(encoding="utf-8"))
        words = int(runtime_meta["weight_words"])
        compatible = words <= CAPACITY_WORDS
        runtime = {
            "npz": str(runtime_npz.relative_to(output)),
            "npz_sha256": sha256(runtime_npz),
            "metadata": str(runtime_meta_path.relative_to(output)),
            "weight_words": words,
            "weight_capacity_words": CAPACITY_WORDS,
            "current_bitstream_compatible": compatible,
            "capacity_is_pl_memory_not_sd_card": True,
        }
        if compatible:
            execute("fixed-rx-runtime-assets", [py, str(stage37 / "build_scene_runtime_assets.py"),
                "--evaluator", str(stage37 / "evaluate_fixed_scene.py"), "--source-root", str(board),
                "--scene", args.scene, "--tv", str(export_dir / "tv3_ray_net.txt"),
                "--manifest", str(export_dir / "manifest_ray.json"), "--datadir", str(args.dataset),
                "--runtime-model", str(runtime_npz), "--out", str(model_dir)])
        else:
            steps.append({"id": "fixed-rx-runtime-assets", "status": "SKIPPED_OVERSIZE",
                          "duration_s": 0.0, "return_code": None, "log": "", "argv": []})

    accuracy = None
    if evaluation.is_file():
        value = json.loads(evaluation.read_text(encoding="utf-8"))
        accuracy = {
            "maps": int(value["maps"]),
            "factorized_fp32_mean_ssim": value["pc_fp32"]["summary"]["ssim"]["mean"],
            "hardware_integer_mean_ssim": value["hardware_integer"]["summary"]["ssim"]["mean"],
            "mean_ssim_gap": value["mean_ssim_gap_pc_minus_integer"],
            "factorized_fp32_mean_psnr_db": value["pc_fp32"]["summary"]["psnr_db"]["mean"],
            "hardware_integer_mean_psnr_db": value["hardware_integer"]["summary"]["psnr_db"]["mean"],
            "factorized_fp32_mean_mse": value["pc_fp32"]["summary"]["mse"]["mean"],
            "hardware_integer_mean_mse": value["hardware_integer"]["summary"]["mse"]["mean"],
            "factorized_fp32_mean_mae": value["pc_fp32"]["summary"]["mae"]["mean"],
            "hardware_integer_mean_mae": value["hardware_integer"]["summary"]["mae"]["mean"],
            "factorized_fp32_summary": value["pc_fp32"]["summary"],
            "hardware_integer_summary": value["hardware_integer"]["summary"],
            "acceptance_threshold": None,
            "informational_only": True,
        }

    if failure:
        status = "FAILED"
    elif runtime and runtime["current_bitstream_compatible"]:
        status = ("READY_FOR_CURRENT_BITSTREAM" if formal_defaults_used
                  else "NONDEFAULT_RUN_RUNTIME_MODEL_CREATED_COMPATIBLE")
    else:
        status = "RUNTIME_MODEL_CREATED_OVERSIZE"
    report = {
        "schema": "n2-generic-checkpoint-fpga-export-report-v1", "scene": args.scene,
        "status": status, "started_utc": started, "finished_utc": utc_now(), "dry_run": False,
        "inputs": inputs, "checkpoint_contract": checkpoint_contract,
        "configuration": vars_for_report(args), "steps": steps,
        "accuracy": accuracy, "runtime": runtime, "failure": failure,
        "formal_defaults_used": formal_defaults_used,
        "evidence_boundary": "software conversion only; physical board evidence is separate",
    }
    write_json(output / "deployment_report.json", report)
    (output / "deployment_report.md").write_text(markdown(report), encoding="utf-8")
    hash_rows = []
    for path in sorted(output.rglob("*")):
        if path.is_file() and path.name != "SHA256SUMS.txt":
            hash_rows.append(f"{sha256(path)}  {path.relative_to(output).as_posix()}")
    (output / "SHA256SUMS.txt").write_text("\n".join(hash_rows) + "\n", encoding="utf-8")
    print(f"\nStatus: {status}\nReport: {output / 'deployment_report.json'}")
    raise SystemExit(1 if failure else 0)


def vars_for_report(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "factorization_steps": args.factorization_steps,
        "factorization_batch": args.factorization_batch,
        "factorization_learning_rate": args.factorization_lr,
        "factorization_validation_maps": args.factorization_validation_maps,
        "calibration_count": args.calibration_count,
        "activation_percentile": args.activation_percentile,
        "qat_steps": args.qat_steps,
        "qat_batch": args.qat_batch,
        "qat_learning_rate": args.qat_lr,
        "evaluation_limit": args.evaluation_limit,
        "accuracy_gate": None,
        "current_bitstream_capacity_words": CAPACITY_WORDS,
    }


if __name__ == "__main__":
    main()
