#!/usr/bin/env python3
"""Build the accepted Stage39 model candidates for the unchanged bitstream."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

from stage39_common import BOARD39, PC39, ROOT, SCENES, file_manifest, sha256, write_json


SELECTION = {"s23": "qat", "Meetingroom": "qat",
             "Office1": "qat", "Office2": "qat"}


def run(args):
    print("[Stage39 selected runtime]", " ".join(map(str, args)), flush=True)
    subprocess.run([str(x) for x in args], cwd=ROOT, check=True)


def main():
    configuration = json.loads((ROOT / "work_stage39/config/scenes.json").read_text(encoding="utf-8"))
    out_root = ROOT / "work_stage39/selected_runtime"
    out_root.mkdir(parents=True, exist_ok=True)
    reports = []
    for scene in SCENES:
        destination = out_root / scene
        if destination.exists(): shutil.rmtree(destination)
        if SELECTION[scene] == "stage38-retained":
            shutil.copytree(ROOT / "work_stage39/runtime_packages" / scene, destination)
            report = json.loads((destination / "package_manifest.json").read_text(encoding="utf-8"))
            report.update({"selection": SELECTION[scene],
                           "selection_reason": "QAT candidate failed the predeclared improvement gate"})
            write_json(destination / "package_manifest.json", report)
            reports.append(report); continue

        spec = configuration["scenes"][scene]
        dataset = Path(spec["dataset"])
        config = ROOT / spec["config"]
        checkpoint = ROOT / "work_stage39/qat" / scene / "checkpoint_qat.tar"
        export = ROOT / "work_stage39/qat" / scene / "qat_export"
        sparse = destination / "sparse"; model_dir = destination / "model"
        sparse.mkdir(parents=True); model_dir.mkdir(parents=True)
        shutil.copytree(export, destination / "hardware_export")
        sparse_manifest = sparse / "model_manifest.json"
        run([sys.executable, BOARD39 / "software/stage29/build_sparse_model.py",
             "--tv", export / "tv3_ray_net.txt", "--rtl-dir", sparse,
             "--report", sparse_manifest])
        runtime = model_dir / "stage37_runtime_model.npz"
        run([sys.executable, ROOT / "work_stage37/build_runtime_model.py",
             "--sparse-manifest", sparse_manifest, "--sparse-dir", sparse,
             "--quant-manifest", export / "manifest_ray.json",
             "--fixed-renderer", BOARD39 / "software/stage24/fixed_renderer.py",
             "--scene", scene, "--checkpoint", checkpoint, "--out", runtime])
        run([sys.executable, ROOT / "work_stage37/build_scene_runtime_assets.py",
             "--evaluator", ROOT / "work_stage37/evaluate_fixed_scene.py",
             "--source-root", BOARD39, "--scene", scene,
             "--tv", export / "tv3_ray_net.txt", "--manifest", export / "manifest_ray.json",
             "--datadir", dataset, "--runtime-model", runtime, "--out", model_dir])
        shutil.copy2(export / "manifest_ray.json", model_dir / "quant_manifest.json")
        shutil.copy2(checkpoint, destination / "checkpoint.tar")
        shutil.copy2(config, destination / "config.yml")
        sparse_info = json.loads(sparse_manifest.read_text(encoding="utf-8"))
        runtime_info = json.loads(runtime.with_suffix(".json").read_text(encoding="utf-8"))
        report = {
            "schema": "n2-stage39-selected-runtime-package-v1", "scene": scene,
            "selection": "qat", "selection_reason": "full frozen-test acceptance",
            "checkpoint_sha256": sha256(checkpoint),
            "runtime_model_sha256": sha256(runtime),
            "weight_words": int(sparse_info["weight_words"]), "weight_capacity": 3072,
            "fits_unchanged_bitstream": int(sparse_info["weight_words"]) <= 3072,
            "tv3_sparse_vs_dense_bit_exact": bool(sparse_info["tv3_sparse_vs_dense_bit_exact"]),
            "output_calibration": None,
            "files": file_manifest(destination, excluded_names=("package_manifest.json",)),
        }
        if not report["fits_unchanged_bitstream"]:
            raise RuntimeError(f"{scene} QAT model exceeds runtime weight capacity")
        write_json(destination / "package_manifest.json", report)
        reports.append(report)
    summary = {"schema": "n2-stage39-selected-four-scene-runtime-v1",
               "selection_algorithm": "accept QAT only when frozen test SSIM improves",
               "same_bitstream": True, "scenes": reports}
    write_json(out_root / "build_report.json", summary)
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
