from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import shutil
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from common import (
    BOARD_PACKAGE, BOARD_TEMPLATE, DEFAULT_CONFIG, KIT_ROOT, PC_PACKAGE, PC_TEMPLATE,
    SCENES, copy_clean, input_path, load_config, sha256, utc_now, write_json,
)


def notebook() -> dict:
    def code(text: str) -> dict:
        return {"cell_type": "code", "execution_count": None, "metadata": {}, "outputs": [],
                "source": [line + "\n" for line in text.strip().splitlines()]}
    cells = [
        code('''# Stage39：验证完整板端包并建立绝对路径；重复运行不会因 Jupyter 执行状态变化而报错。
from pathlib import Path
import os, sys, subprocess
ROOT=Path('/home/xilinx/jupyter_notebooks/N2-NeRF2-Stage39-board')
assert (ROOT/'jupyter/stage39_service_launcher.py').is_file(), '找不到完整 Stage39 board 文件夹'
os.chdir(str(ROOT)); JUPYTER=str((ROOT/'jupyter').resolve())
if JUPYTER not in sys.path: sys.path.insert(0,JUPYTER)
for name in ('stage39_import_guard','stage39_service_launcher','stage39_physical_gate'): sys.modules.pop(name,None)
from stage39_import_guard import prepare_stage39_imports
protocol=prepare_stage39_imports(ROOT)
assert protocol.P==32 and Path(protocol.__file__).resolve()==(ROOT/'software/stage5/n2_stream.py').resolve()
subprocess.run([sys.executable,'software/stage39/verify_stage39_board_package.py'],check=True)
print('Stage39 root =',ROOT)'''),
        code('''# 启动独立板端服务；单元格会返回，服务端口固定为 8311。
from stage39_service_launcher import start
service=start(host='0.0.0.0',port=8311,wait_seconds=120)
print('Stage39 service =',service['url'])
assert service['health']['service_build_id']=='stage39-hardware-exact-qat-four-scene-service-v1' '''),
        code('''# 四场景各一幅完整 90×360 物理逐位门禁，共 129600 个 Q15 像素。
from stage39_physical_gate import run_selftests
physical_exact=run_selftests(); assert physical_exact['status']=='PASS',physical_exact
physical_exact'''),
        code('''# 四场景各 50 次完整图时延和稳定性门禁，共 200 次。
from stage39_physical_gate import run_benchmarks
physical_benchmark=run_benchmarks(repeats=50); assert physical_benchmark['status']=='PASS',physical_benchmark
physical_benchmark'''),
        code('''# 完成四场景总体精度后导出完整物理证据 ZIP。
from stage39_physical_gate import export_evidence
physical_export=export_evidence(); print('请下载：',physical_export['archive']); physical_export'''),
        code('''# 查看独立服务状态；PYNQ 重启后只需重新运行第 1、2 单元格。
from stage39_service_launcher import status
status()'''),
    ]
    return {"cells": cells, "metadata": {"kernelspec": {"display_name": "Python 3 (PYNQ)",
            "language": "python", "name": "python3"}, "language_info": {"name": "python", "version": "3.10"}},
            "nbformat": 4, "nbformat_minor": 5}


def metric_payload(row: dict) -> dict:
    return {
        "mae": row["mae"], "mse": row["mse"], "rmse": float(np.sqrt(row["mse"])),
        "max_abs": None, "psnr_db_data_range_1": row["psnr_db"],
        "ssim_data_range_1": row["ssim"], "pearson_r": None,
        "peak_angular_error_deg": row["peak_angular_error_deg"],
        "peak_azimuth_error_deg": row["peak_azimuth_error_deg"],
        "peak_elevation_error_deg": row["peak_elevation_error_deg"],
        "main_lobe_energy_abs_error": row["main_lobe_energy_abs_error"],
    }


def summary(values: list[float]) -> dict:
    data = np.asarray(values, np.float64)
    return {"mean": float(data.mean()), "median": float(np.median(data)),
            "minimum": float(data.min()), "maximum": float(data.max())}


def build_catalog(config: dict, scene: str, scene_root: Path, evaluation: dict,
                  profile: str) -> tuple[dict, dict]:
    spec = config["scenes"][scene]; datadir = input_path(config, scene, "dataset")
    positions = pd.read_csv(datadir / "tx_pos.csv").values.astype(np.float32)
    rows = evaluation["pc_fp32"]["per_map"]
    expected_maps = int(spec["test_count"]) if profile == "full" else 1
    if len(rows) != expected_maps:
        raise RuntimeError(f"{scene}: full evaluation has {len(rows)} maps")
    gt_dir = scene_root / "data/stage32/gt"; pc_dir = scene_root / "data/pc_maps"
    gt_dir.mkdir(parents=True, exist_ok=True); pc_dir.mkdir(parents=True, exist_ok=True)
    samples = []
    source_maps = KIT_ROOT / "work" / profile / "maps" / scene / "pc_fp32"
    for output_index, row in enumerate(rows):
        spectrum_id = str(row["spectrum_id"]); tx = positions[int(spectrum_id)-1]
        gt_source = datadir / "spectrum" / f"{spectrum_id}.png"; gt_target = gt_dir / gt_source.name
        shutil.copy2(gt_source, gt_target)
        map_source = source_maps / f"{spectrum_id}.npy"; map_target = pc_dir / map_source.name
        shutil.copy2(map_source, map_target)
        map_meta_source = map_source.with_suffix(".json")
        if map_meta_source.is_file(): shutil.copy2(map_meta_source, pc_dir / map_meta_source.name)
        item = {
            "test_output_index": output_index, "spectrum_id": spectrum_id,
            "tx": tx.astype(float).tolist(), "pc_ssim_vs_gt": float(row["ssim"]),
            "gt_png": f"data/stage32/gt/{spectrum_id}.png", "gt_sha256": sha256(gt_target),
            "gt_bytes": gt_target.stat().st_size, "pc_fp32_npy": f"data/pc_maps/{spectrum_id}.npy",
            "pc_fp32_sha256": sha256(map_target), "pc_seconds_builder": float(row["seconds"]),
            "pc_metrics": metric_payload(row), "split": "test",
        }
        samples.append(item)
    scores = np.asarray([item["pc_ssim_vs_gt"] for item in samples])
    ordered = np.argsort(scores)
    candidate_indexes = sorted(set(int(ordered[index]) for index in
        np.rint(np.linspace(0, len(ordered)-1, min(35, len(ordered)))).astype(int)))
    candidates = []
    for candidate_index, sample_index in enumerate(candidate_indexes):
        item = dict(samples[sample_index]); item["candidate_index"] = candidate_index
        item["official_ssim"] = item["pc_ssim_vs_gt"]; candidates.append(item)
    representatives = {}
    candidate_scores = np.asarray([item["pc_ssim_vs_gt"] for item in candidates])
    for label, percentile in (("P10", 10), ("P50", 50), ("P90", 90), ("MAX", 100)):
        target = np.percentile(scores, percentile)
        representatives[label] = candidates[int(np.argmin(np.abs(candidate_scores-target)))]["spectrum_id"]
    gateway = yaml.safe_load((datadir / "gateway_info.yml").read_text(encoding="utf-8"))["gateway1"]
    hashes = {"test_index_sha256": sha256(datadir / "test_index.txt"),
              "tx_pos_sha256": sha256(datadir / "tx_pos.csv"),
              "checkpoint_sha256": sha256(KIT_ROOT / "work" / profile / "factorized" / scene / "checkpoint.tar"),
              "config_sha256": sha256(input_path(config, scene, "config"))}
    catalog = {"schema": "n2-stage39-cold-factorized-scene-catalog-v1", "scene_id": scene,
        "test_samples": len(samples), "candidate_count": len(candidates),
        "test_ssim_summary": summary(scores.tolist()), "representatives": representatives,
        "gateway_position": gateway["position"], "gateway_orientation_xyzw": gateway["orientation"],
        "source_sha256": hashes, "candidates": candidates}
    all_catalog = {"schema": "n2-stage39-cold-all-test-catalog-v1", "scene_id": scene,
        "sample_count": len(samples), "split": "test", "resolution": [90, 360],
        "source_sha256": hashes, "pc_ssim_summary": summary(scores.tolist()), "samples": samples}
    write_json(scene_root / "data/stage31/catalog.json", catalog)
    write_json(scene_root / "data/stage32/all_test_catalog.json", all_catalog)
    write_json(scene_root / "data/stage33/pc_timing_reference.json", {
        "scene_id": scene, "mean_end_to_end_seconds_per_spectrum": float(np.mean([r["seconds"] for r in rows])),
        "sample_count": len(rows), "source": "cold reproduction full checkpoint replay on this GPU",
        "scope": "FP32 all 32400 rays plus device-to-host transfer; no plotting", "device": "cuda"})
    return catalog, all_catalog


def load_fixed_class():
    path = KIT_ROOT / "02_model_pipeline/stage37/evaluate_fixed_scene.py"
    module_spec = importlib.util.spec_from_file_location("stage39_cold_contract_fixed", path)
    module = importlib.util.module_from_spec(module_spec); assert module_spec.loader is not None
    sys.modules[module_spec.name] = module; module_spec.loader.exec_module(module)
    return module.Fixed


def build_contract(config: dict, scene: str) -> dict:
    Fixed = load_fixed_class(); datadir = input_path(config, scene, "dataset")
    scene_root = BOARD_PACKAGE / "scenes" / scene
    model = Fixed(BOARD_PACKAGE, scene_root / "hardware_export/tv3_ray_net.txt",
                  scene_root / "hardware_export/manifest_ray.json", datadir)
    spectrum_id = str(np.atleast_1d(np.loadtxt(datadir / "test_index.txt", dtype=str))[0])
    tx = pd.read_csv(datadir / "tx_pos.csv").values.astype(np.float32)[int(spectrum_id)-1]
    value = model.render(tx); q15 = np.rint(value.reshape(-1) * 32768.0).astype(np.uint32)
    contract = {"schema": "n2-stage39-physical-selftest-contract-v1", "scene": scene,
        "spectrum_id": spectrum_id, "ray_count": 32400, "samples_per_ray": 64,
        "raw_q15_sha256": hashlib.sha256(q15.tobytes()).hexdigest(),
        "runtime_model_sha256": sha256(scene_root / "model/stage37_runtime_model.npz"),
        "integer_reference": "portable sparse INT8/Q15 model regenerated from the selected QAT checkpoint"}
    write_json(scene_root / "model/stage39_physical_selftest.json", contract)
    write_json(PC_PACKAGE / "scenes" / scene / "evidence/stage39/physical_selftest_contract.json", contract)
    return contract


def scene_spec(config: dict, scene: str, catalog: dict, evaluation: dict,
               runtime_manifest: dict, profile: str) -> dict:
    datadir = input_path(config, scene, "dataset")
    tx = pd.read_csv(datadir / "tx_pos.csv").values.astype(np.float32)
    gateway = yaml.safe_load((datadir / "gateway_info.yml").read_text(encoding="utf-8"))["gateway1"]
    pc_mean = float(evaluation["pc_fp32"]["summary"]["ssim"]["mean"])
    integer_mean = float(evaluation["hardware_integer"]["summary"]["ssim"]["mean"])
    gap = pc_mean - integer_mean
    return {"id": scene, "label": scene, "test_count": int(config["scenes"][scene]["test_count"]),
        "train_count": int(config["scenes"][scene]["train_count"]),
        "dataset_count": int(config["scenes"][scene]["dataset_count"]),
        "tx_bounds_m": {axis: [float(tx[:, i].min()), float(tx[:, i].max())]
                        for i, axis in enumerate("xyz")},
        "rx_position_m": gateway["position"], "rx_orientation_xyzw": gateway["orientation"],
        "checkpoint": "pc_app/model/checkpoint.tar", "config": "pc_app/model/config.yml",
        "checkpoint_sha256": sha256(KIT_ROOT / "work" / profile / "factorized" / scene / "checkpoint.tar"),
        "hardware": {"architecture": "Stage39 train-only hardware-exact-QAT runtime model on the universal datapath",
            "factorization_compatible": True, "hardware_validated": False,
            "status": "one_common_bitstream_runtime_model", "runtime_model_id_crc32": None,
            "runtime_model_sha256": runtime_manifest["runtime_model_sha256"],
            "weight_words": runtime_manifest["weight_words"], "weight_capacity": runtime_manifest["weight_capacity"],
            "same_bitstream_for_all_scenes": True, "bit_sha256": "PENDING_VIVADO_PROPAGATION",
            "validation_status": "software_exact_complete_physical_stage39_gate_pending",
            "output_calibration": None, "qat_algorithm_id": config["qat"]["algorithm_id"]},
        "latency_limit_seconds": config["acceptance"]["latency_median_limit_seconds"],
        "maximum_mean_ssim_gap": config["acceptance"]["maximum_mean_ssim_gap"],
        "gt_source": "Original dataset spectrum PNG; no GT synthesized by the app",
        "pc_ssim_mean": pc_mean, "factorized_pc_mean_ssim": pc_mean,
        "fpga_raw_mean_ssim": integer_mean, "fpga_raw_mean_ssim_gap": gap,
        "raw_mean_ssim_gap_under_limit": gap <= config["acceptance"]["maximum_mean_ssim_gap"],
        "output_mode": "raw_int8_q15", "stage39_original_pc_mean_ssim": pc_mean,
        "stage39_hardware_integer_mean_ssim": integer_mean, "stage39_mean_ssim_gap": gap,
        "stage39_gap_under_0p015": gap <= 0.015,
        "stage39_qat_shadow_fp32_mean_ssim": float(evaluation["qat_shadow_fp32"]["summary"]["ssim"]["mean"]),
        "stage39_integer_improvement_over_ptq": None}


def main() -> None:
    parser = argparse.ArgumentParser(description="Create fresh Stage39 PC and PYNQ packages from cold outputs")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--profile", choices=("full", "smoke"), default="full",
                        help="smoke is integration testing only and cannot be delivered")
    args = parser.parse_args(); config = load_config(args.config); profile = args.profile
    copy_clean(PC_TEMPLATE, PC_PACKAGE); copy_clean(BOARD_TEMPLATE, BOARD_PACKAGE)
    (PC_PACKAGE / "scenes").mkdir(); (BOARD_PACKAGE / "scenes").mkdir()
    specs, contracts = [], []
    for scene in SCENES:
        qr = KIT_ROOT / "work" / profile / "qat" / scene
        runtime = KIT_ROOT / "work" / profile / "runtime" / scene
        evaluation = json.loads((qr / "full_evaluation.json").read_text(encoding="utf-8"))
        ptq = json.loads((qr / "ptq_full_evaluation.json").read_text(encoding="utf-8"))
        runtime_manifest = json.loads((runtime / "package_manifest.json").read_text(encoding="utf-8"))
        pc_scene = PC_PACKAGE / "scenes" / scene; board_scene = BOARD_PACKAGE / "scenes" / scene
        pc_scene.mkdir(); board_scene.mkdir()
        catalog, all_catalog = build_catalog(config, scene, pc_scene, evaluation, profile)
        shutil.copytree(pc_scene / "data", board_scene / "data")
        for root in (pc_scene, board_scene):
            model = root / "pc_app/model"; model.mkdir(parents=True)
            shutil.copy2(KIT_ROOT / "work" / profile / "factorized" / scene / "checkpoint.tar",
                         model / "checkpoint.tar")
            shutil.copy2(input_path(config, scene, "config"), model / "config.yml")
            source = root / "source"; source.mkdir()
            for name in ("gateway_info.yml", "train_index.txt", "test_index.txt", "tx_pos.csv"):
                shutil.copy2(input_path(config, scene, "dataset") / name, source / name)
            audit = root / "source/stage39_qat"; audit.mkdir()
            for path in (qr / "checkpoint_qat.json", qr / "train_calibration.json",
                         qr / "full_evaluation.json", qr / "ptq_full_evaluation.json",
                         qr / "software_gate.json", runtime / "package_manifest.json"):
                shutil.copy2(path, audit / path.name)
            hw = root / "hardware_export"; hw.mkdir()
            for name in ("tv3_ray_net.txt", "manifest_ray.json", "calibration_reference.npz"):
                shutil.copy2(runtime / "hardware_export" / name, hw / name)
            write_json(hw / "audit.json", {"schema": "n2-stage39-hardware-export-audit-v1",
                "scene": scene, "checkpoint_sha256": sha256(qr / "checkpoint_qat.tar"),
                "manifest_sha256": sha256(hw / "manifest_ray.json"), "output_calibration": None})
        shutil.copytree(runtime / "model", board_scene / "model")
        spec = scene_spec(config, scene, catalog, evaluation, runtime_manifest, profile)
        spec["stage39_integer_improvement_over_ptq"] = (
            spec["stage39_hardware_integer_mean_ssim"] -
            float(ptq["hardware_integer"]["summary"]["ssim"]["mean"]))
        runtime_meta = json.loads((board_scene / "model/stage37_runtime_model.json").read_text(encoding="utf-8"))
        spec["hardware"]["runtime_model_id_crc32"] = runtime_meta["model_id_crc32"]
        write_json(pc_scene / "scene.json", spec); write_json(board_scene / "scene.json", spec)
        specs.append(spec)
    registry = {"schema": "n2-stage39-hardware-exact-qat-four-scene-registry-v1", "scenes": specs,
        "default_scene": "s23", "hardware_contract": {"bitstream_count": 1,
            "scene_switch": "runtime weights, LUTs and fixed-Rx cache only; no resynthesis or bitstream replacement",
            "latency_limit_seconds": config["acceptance"]["latency_median_limit_seconds"]},
        "output_mode": "raw_int8_q15", "stage39_qat_algorithm_id": config["qat"]["algorithm_id"],
        "training_split_only": True, "output_calibration": None, "physical_stage39_gate_pending": True}
    write_json(PC_PACKAGE / "scene_registry.json", registry); write_json(BOARD_PACKAGE / "scene_registry.json", registry)
    (BOARD_PACKAGE / "Stage39_QAT_Four_Scenes.ipynb").write_text(
        json.dumps(notebook(), ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    for scene in SCENES: contracts.append(build_contract(config, scene))
    write_json(KIT_ROOT / "work" / profile / "reports/physical_selftest_contracts.json", {
        "schema": "n2-stage39-four-scene-physical-selftest-contracts-v1",
        "profile": profile, "scenes": contracts})
    print(json.dumps({"ok": True, "pc_package": str(PC_PACKAGE), "board_package": str(BOARD_PACKAGE),
                      "scenes": len(specs), "contracts": len(contracts)}, ensure_ascii=False, indent=2))


if __name__ == "__main__": main()
