from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from common import BOARD_PACKAGE, DEFAULT_CONFIG, KIT_ROOT, PC_PACKAGE, SCENES, load_config, sha256, utc_now, write_json


def parse_summary(path: Path) -> dict[str, str]:
    result = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line:
            key, value = line.split("=", 1); result[key.strip()] = value.strip()
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Copy fresh BIT/HWH and propagate their identity everywhere")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--profile", choices=("full", "smoke"), default="full")
    args = parser.parse_args(); config = load_config(args.config)
    vivado = KIT_ROOT / "01_vivado/output"
    bit, hwh = vivado / "n2_stage38_raw.bit", vivado / "n2_stage38_raw.hwh"
    summary_path = vivado / "stage37_summary.txt"
    for path in (bit, hwh, summary_path):
        if not path.is_file(): raise FileNotFoundError("run Vivado build first: " + str(path))
    summary = parse_summary(summary_path)
    if float(summary.get("frequency_mhz", 0)) != 95.0 or summary.get("timing_met") != "1":
        raise RuntimeError("Vivado 95 MHz timing did not pass")
    rtl_report_path = KIT_ROOT / "work" / args.profile / "rtl_sim/rtl_simulation_report.json"
    rtl = json.loads(rtl_report_path.read_text(encoding="utf-8"))
    if rtl.get("status") != "PASS": raise RuntimeError("RTL simulation gate did not pass")
    sim = BOARD_PACKAGE / "sim"; sim.mkdir(exist_ok=True)
    shutil.copy2(bit, sim / bit.name); shutil.copy2(hwh, sim / hwh.name)
    reports = sim / "reports"; reports.mkdir()
    for name in ("stage37_summary.txt", "stage37_timing_routed.rpt",
                 "stage37_utilization.rpt", "stage37_hierarchical_utilization.rpt"):
        source = vivado / name
        if source.is_file(): shutil.copy2(source, reports / name)
    shutil.copy2(rtl_report_path, reports / rtl_report_path.name)
    runtime_models = {}
    bit_hash = sha256(sim / bit.name); hwh_hash = sha256(sim / hwh.name)
    for scene in SCENES:
        model_meta = json.loads((BOARD_PACKAGE / "scenes" / scene /
            "model/stage37_runtime_model.json").read_text(encoding="utf-8"))
        runtime_models[scene] = {"model_id_crc32": model_meta["model_id_crc32"],
            "runtime_model_sha256": sha256(BOARD_PACKAGE / "scenes" / scene /
                                             "model/stage37_runtime_model.npz"),
            "weight_words": int(model_meta["weight_words"])}
        for package in (BOARD_PACKAGE, PC_PACKAGE):
            scene_path = package / "scenes" / scene / "scene.json"
            value = json.loads(scene_path.read_text(encoding="utf-8"))
            value["hardware"]["bit_sha256"] = bit_hash
            write_json(scene_path, value)
    artifact = {"schema": "n2-stage38-artifacts-v1", "build_id": "stage38-raw-only-universal-f95-v1",
        "created_utc": utc_now(), "source_tcl_build_id": summary.get("build_id"),
        "part": summary.get("part"), "frequency_mhz": float(summary["frequency_mhz"]),
        "wns_ns": float(summary["wns_ns"]), "whs_ns": float(summary["whs_ns"]),
        "timing_met": True, "bit_sha256": bit_hash, "hwh_sha256": hwh_hash,
        "single_bitstream_four_scenes": True, "scene_switch_changes_bitstream": False,
        "runtime_models": runtime_models,
        "four_scene_switch_and_return_rtl_verified": rtl["four_scene_switch_and_return"],
        "runtime_model_load_rtl_bit_exact": rtl["runtime_load_cache_inference_bit_exact"],
        "cache_build_rtl_bit_exact": rtl["runtime_load_cache_inference_bit_exact"],
        "cached_run_rtl_bit_exact": rtl["runtime_load_cache_inference_bit_exact"],
        "full_ray_renderer_rtl_bit_exact": rtl["runtime_load_cache_inference_bit_exact"],
        "activation_relu_before_int8_saturation": True, "xsim_expected_q15": rtl["expected_q15"],
        "output_mode": "raw_int8_q15", "output_conversion": "raw_q15 / 32768",
        "derived_from_timing_qualified_physical_design": True}
    write_json(sim / "stage38_artifact_manifest.json", artifact)
    for package in (BOARD_PACKAGE, PC_PACKAGE):
        registry_path = package / "scene_registry.json"
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        for item in registry["scenes"]: item["hardware"]["bit_sha256"] = bit_hash
        write_json(registry_path, registry)
        write_json(package / "HARDWARE_PRESERVATION.json", {
            "schema": "n2-stage39-single-bitstream-raw-only-hardware-contract-v1",
            "bitstream": "sim/n2_stage38_raw.bit", "bitstream_count": 1, "bit_sha256": bit_hash,
            "scenes": list(SCENES), "scene_switch": "runtime sparse INT8 weights, LUTs and fixed-Rx cache",
            "output_mode": "raw_int8_q15", "output_conversion": "raw_q15 / 32768",
            "resynthesis_on_scene_switch": False, "bitstream_replacement_on_scene_switch": False,
            "final_heatmap_cache_used": False, "hardware_rays_per_map": 32400, "samples_per_ray": 64})
    print(json.dumps(artifact, ensure_ascii=False, indent=2))


if __name__ == "__main__": main()
