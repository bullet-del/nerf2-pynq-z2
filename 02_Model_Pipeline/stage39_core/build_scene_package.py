#!/usr/bin/env python3
"""One-command checkpoint-to-Stage39 runtime package compiler."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from stage39_common import BOARD39, PC39, ROOT, SCENES, canonical_sha256, file_manifest, sha256, write_json


def run(command: list[str], *, env=None) -> None:
    print("[Stage39 compiler]", " ".join(command), flush=True)
    subprocess.run(command, cwd=ROOT, env=env, check=True)


def resolve(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def runtime_payload_equal(left: Path, right: Path) -> bool:
    """Compare semantic NPZ contents; ZIP container timestamps are nondeterministic."""
    import numpy as np
    with np.load(left, allow_pickle=False) as a, np.load(right, allow_pickle=False) as b:
        if set(a.files) != set(b.files):
            return False
        return all(np.array_equal(a[name], b[name]) for name in a.files)


def build(scene: str, spec: dict[str, object], output_root: Path) -> dict[str, object]:
    checkpoint, config = resolve(str(spec["checkpoint"])), resolve(str(spec["config"]))
    dataset, calibration = resolve(str(spec["dataset"])), resolve(str(spec["calibration"]))
    for path in (checkpoint, config, dataset, calibration):
        if not path.exists():
            raise FileNotFoundError(path)
    destination = output_root / scene
    if destination.exists():
        shutil.rmtree(destination)
    export_dir, sparse_dir, model_dir = (destination / "hardware_export",
                                          destination / "sparse", destination / "model")
    for path in (export_dir, sparse_dir, model_dir):
        path.mkdir(parents=True, exist_ok=True)
    environment = dict(os.environ)
    environment["N2_SOURCE_ROOT"] = str(PC39)
    run([sys.executable, str(ROOT / "work_stage37/export_ray_stage37.py"),
         "--ckpt", str(checkpoint), "--config", str(config),
         "--calibration", str(calibration), "--out", str(export_dir),
         "--percentile", str(float(spec["activation_percentile"]))], env=environment)
    sparse_manifest = sparse_dir / "model_manifest.json"
    run([sys.executable, str(BOARD39 / "software/stage29/build_sparse_model.py"),
         "--tv", str(export_dir / "tv3_ray_net.txt"), "--rtl-dir", str(sparse_dir),
         "--report", str(sparse_manifest)])
    runtime_model = model_dir / "stage37_runtime_model.npz"
    run([sys.executable, str(ROOT / "work_stage37/build_runtime_model.py"),
         "--sparse-manifest", str(sparse_manifest), "--sparse-dir", str(sparse_dir),
         "--quant-manifest", str(export_dir / "manifest_ray.json"),
         "--fixed-renderer", str(BOARD39 / "software/stage24/fixed_renderer.py"),
         "--scene", scene, "--checkpoint", str(checkpoint), "--out", str(runtime_model)])
    run([sys.executable, str(ROOT / "work_stage37/build_scene_runtime_assets.py"),
         "--evaluator", str(ROOT / "work_stage37/evaluate_fixed_scene.py"),
         "--source-root", str(BOARD39), "--scene", scene,
         "--tv", str(export_dir / "tv3_ray_net.txt"),
         "--manifest", str(export_dir / "manifest_ray.json"),
         "--datadir", str(dataset), "--runtime-model", str(runtime_model),
         "--out", str(model_dir)])
    shutil.copy2(export_dir / "manifest_ray.json", model_dir / "quant_manifest.json")
    shutil.copy2(checkpoint, destination / "checkpoint.tar")
    shutil.copy2(config, destination / "config.yml")
    files = file_manifest(destination, excluded_names=("package_manifest.json",))
    existing = BOARD39 / "scenes" / scene / "model/stage37_runtime_model.npz"
    report = {
        "schema": "n2-stage39-runtime-package-v1", "scene": scene,
        "compiler_algorithm": "one-checkpoint-to-runtime-package-v1",
        "checkpoint_sha256": sha256(checkpoint), "config_sha256": sha256(config),
        "calibration_sha256": sha256(calibration),
        "activation_percentile": float(spec["activation_percentile"]),
        "runtime_model_sha256": sha256(runtime_model),
        "stage38_runtime_model_sha256": sha256(existing),
        "exact_stage38_reproduction": runtime_payload_equal(runtime_model, existing),
        "reproduction_comparison": "all named NPZ arrays compared with dtype/shape/value equality; container hash may differ because ZIP timestamps are not deterministic",
        "runtime_model_schema": "n2-stage37-runtime-model-v1",
        "package_files": files, "package_tree_sha256": canonical_sha256(files),
        "manual_array_or_source_edit_required": False,
    }
    write_json(destination / "package_manifest.json", report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=Path("work_stage39/config/scenes.json"))
    parser.add_argument("--scene", choices=(*SCENES, "all"), default="all")
    parser.add_argument("--output", type=Path, default=Path("work_stage39/runtime_packages"))
    args = parser.parse_args()
    configuration = json.loads(resolve(str(args.config)).read_text(encoding="utf-8"))
    if configuration.get("schema") != "n2-stage39-scene-build-config-v1":
        raise RuntimeError("scene compiler configuration schema mismatch")
    selected = SCENES if args.scene == "all" else (args.scene,)
    output = resolve(str(args.output)); output.mkdir(parents=True, exist_ok=True)
    reports = [build(scene, configuration["scenes"][scene], output) for scene in selected]
    summary = {"schema": "n2-stage39-four-scene-compiler-run-v1",
               "algorithm": configuration["algorithm"], "scenes": reports,
               "all_exact_stage38_reproductions": all(x["exact_stage38_reproduction"] for x in reports),
               "one_command_four_scenes": args.scene == "all"}
    write_json(output / "build_report.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
