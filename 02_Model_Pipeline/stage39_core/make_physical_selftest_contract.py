#!/usr/bin/env python3
"""Generate independent full-map Q15 hashes for the Stage39 board self-test."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from stage39_common import BOARD39, PC39, ROOT, SCENES, sha256, write_json


def load_fixed():
    path = ROOT / "work_stage37/evaluate_fixed_scene.py"
    spec = importlib.util.spec_from_file_location("stage39_contract_fixed", path)
    module = importlib.util.module_from_spec(spec); assert spec.loader is not None
    sys.modules[spec.name] = module; spec.loader.exec_module(module); return module.Fixed


def main():
    Fixed = load_fixed()
    configuration = json.loads((ROOT / "work_stage39/config/scenes.json").read_text(encoding="utf-8"))
    contracts = []
    for scene in SCENES:
        spec = configuration["scenes"][scene]
        datadir = Path(spec["dataset"])
        selected = ROOT / "work_stage39/selected_runtime" / scene
        model = Fixed(BOARD39, selected / "hardware_export/tv3_ray_net.txt",
                      selected / "hardware_export/manifest_ray.json", datadir)
        spectrum_id = str(np.atleast_1d(np.loadtxt(datadir / "test_index.txt", dtype=str))[0])
        positions = pd.read_csv(datadir / "tx_pos.csv").values.astype(np.float32)
        value = model.render(positions[int(spectrum_id)-1])
        q15 = np.rint(value.reshape(-1) * 32768.0).astype(np.uint32)
        contract = {
            "schema": "n2-stage39-physical-selftest-contract-v1", "scene": scene,
            "spectrum_id": spectrum_id, "ray_count": 32400, "samples_per_ray": 64,
            "raw_q15_sha256": __import__("hashlib").sha256(q15.tobytes()).hexdigest(),
            "runtime_model_sha256": sha256(BOARD39 / "scenes" / scene /
                                             "model/stage37_runtime_model.npz"),
            "integer_reference": "portable PC sparse INT8/Q15 model",
        }
        path = BOARD39 / "scenes" / scene / "model/stage39_physical_selftest.json"
        write_json(path, contract)
        shutil_path = PC39 / "scenes" / scene / "evidence/stage39/physical_selftest_contract.json"
        write_json(shutil_path, contract)
        contracts.append(contract)
        del model
    write_json(ROOT / "work_stage39/reports/stage39_physical_selftest_contracts.json",
               {"schema": "n2-stage39-four-scene-physical-selftest-contracts-v1",
                "scenes": contracts})
    print(json.dumps(contracts, indent=2))


if __name__ == "__main__":
    main()
