from __future__ import annotations

import argparse
import json
import platform
import shutil
import sys
from pathlib import Path

import numpy as np
from PIL import Image

from common import DEFAULT_CONFIG, KIT_ROOT, SCENES, input_path, load_config, sha256, utc_now, write_json


def indexes(path: Path) -> list[str]:
    return [str(value) for value in np.atleast_1d(np.loadtxt(path, dtype=str)).tolist()]


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate every external input before Stage39 reconstruction")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    args = parser.parse_args()
    config = load_config(args.config)
    rows = []
    failures = []
    # These are the train-only quantization/QAT settings used by the accepted
    # Stage39 four-scene package.  Failing here is much cheaper and clearer
    # than discovering an accidental parameter edit after a full test run.
    expected_model_contract = {
        "calibration_count": 8192,
        "calibration_seed": 3902,
        "qat_steps": 1800,
        "qat_batch": 192,
        "qat_learning_rate": 1e-5,
        "qat_seed": 3902,
        "activation_percentile": 99.5,
    }
    actual_model_contract = {
        "calibration_count": int(config["calibration"]["count"]),
        "calibration_seed": int(config["calibration"]["seed"]),
        "qat_steps": int(config["qat"]["steps"]),
        "qat_batch": int(config["qat"]["batch"]),
        "qat_learning_rate": float(config["qat"]["learning_rate"]),
        "qat_seed": int(config["qat"]["seed"]),
        "activation_percentile": {
            scene: float(config["scenes"][scene]["activation_percentile"])
            for scene in SCENES
        },
    }
    for key in ("calibration_count", "calibration_seed", "qat_steps", "qat_batch",
                "qat_learning_rate", "qat_seed"):
        if actual_model_contract[key] != expected_model_contract[key]:
            failures.append(
                f"fixed model contract mismatch for {key}: "
                f"{actual_model_contract[key]} != {expected_model_contract[key]}"
            )
    for scene, value in actual_model_contract["activation_percentile"].items():
        if value != expected_model_contract["activation_percentile"]:
            failures.append(
                f"{scene}: fixed activation_percentile mismatch: "
                f"{value} != {expected_model_contract['activation_percentile']}"
            )
    for scene in SCENES:
        spec = config["scenes"][scene]
        datadir = input_path(config, scene, "dataset")
        checkpoint = input_path(config, scene, "checkpoint")
        cfg = input_path(config, scene, "config")
        try:
            required = [datadir / name for name in (
                "train_index.txt", "test_index.txt", "tx_pos.csv", "gateway_info.yml"
            )] + [checkpoint, cfg]
            for path in required:
                if not path.is_file():
                    raise FileNotFoundError(path)
            train, test = indexes(datadir / "train_index.txt"), indexes(datadir / "test_index.txt")
            pngs = list((datadir / "spectrum").glob("*.png"))
            if len(pngs) != int(spec["dataset_count"]):
                raise AssertionError(f"PNG count {len(pngs)} != {spec['dataset_count']}")
            if len(train) != int(spec["train_count"]) or len(test) != int(spec["test_count"]):
                raise AssertionError("fixed train/test count mismatch")
            numeric_train, numeric_test = {int(x) for x in train}, {int(x) for x in test}
            if numeric_train & numeric_test or len(numeric_train | numeric_test) != int(spec["dataset_count"]):
                raise AssertionError("train/test split overlaps or does not cover the dataset")
            for sample_id in (test[0], test[-1]):
                with Image.open(datadir / "spectrum" / f"{sample_id}.png") as image:
                    if image.size != (360, 90):
                        raise AssertionError(f"{sample_id}.png shape is {image.size}, expected 360x90")
            actual_hash = sha256(checkpoint)
            if actual_hash.lower() != str(spec["checkpoint_sha256"]).lower():
                raise AssertionError(f"checkpoint SHA-256 mismatch: {actual_hash}")
            rows.append({
                "scene": scene, "ok": True, "dataset": str(datadir),
                "checkpoint": str(checkpoint), "checkpoint_sha256": actual_hash,
                "png_count": len(pngs), "train_count": len(train), "test_count": len(test),
            })
        except Exception as exc:
            failures.append(f"{scene}: {type(exc).__name__}: {exc}")
            rows.append({"scene": scene, "ok": False, "error": str(exc)})
    source_checks = {
        "vivado_tcl": (KIT_ROOT / "01_Vivado/tcl/build_stage39.tcl").is_file(),
        "rtl_file_count_is_9": len(list((KIT_ROOT / "01_Vivado/rtl").glob("*.v")))
            + len(list((KIT_ROOT / "01_Vivado/rtl").glob("*.sv"))) == 9,
        "pc_template": (KIT_ROOT / "04_PC_App/template/pc_app/stage39_pc_server.py").is_file(),
        "board_template": (KIT_ROOT / "05_PYNQ_Board/template/jupyter/stage39_board_service.py").is_file(),
        "factorizer": (KIT_ROOT / "02_Model_Pipeline/stage37/train_factorized_scene.py").is_file(),
        "qat": (KIT_ROOT / "02_Model_Pipeline/stage39_core/train_hardware_exact_qat.py").is_file(),
    }
    failures.extend(key for key, value in source_checks.items() if not value)
    torch_info = {"available": False}
    try:
        import torch
        torch_info = {
            "available": True, "version": torch.__version__,
            "cuda_available": bool(torch.cuda.is_available()),
            "cuda_version": torch.version.cuda,
            "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
        }
        if not torch.cuda.is_available():
            failures.append("CUDA GPU is required for factorization, QAT and full evaluation")
    except Exception as exc:
        failures.append("PyTorch import failed: " + str(exc))
        torch_info["error"] = str(exc)
    vivado = Path(config["vivado_executable"])
    if not vivado.is_file():
        failures.append("Vivado executable missing: " + str(vivado))
    report = {
        "schema": "n2-stage39-cold-preflight-v1", "created_utc": utc_now(),
        "status": "PASS" if not failures else "FAIL", "kit_root": str(KIT_ROOT),
        "python": sys.version, "platform": platform.platform(), "torch": torch_info,
        "vivado_executable": str(vivado), "source_checks": source_checks,
        "model_contract": actual_model_contract,
        "expected_model_contract": expected_model_contract,
        "scenes": rows, "failures": failures,
    }
    write_json(KIT_ROOT / "work/reports/00_preflight.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
