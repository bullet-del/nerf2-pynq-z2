#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
EXAMPLES = ROOT / "examples"


HWH = '''<?xml version="1.0" encoding="UTF-8"?>
<EDKSYSTEM xmlns="urn:pynq-skill-test" VIVADOVERSION="2024.1">
  <SYSTEMINFO ARCH="zynq" DEVICE="7z020" NAME="fixture" PACKAGE="clg400" SPEEDGRADE="-1"/>
  <MODULES>
    <MODULE FULLNAME="/dma" INSTANCE="dma" MODTYPE="axi_dma" VLNV="xilinx.com:ip:axi_dma:7.1">
      <BUSINTERFACES><BUSINTERFACE NAME="S_AXI_LITE" TYPE="TARGET" BUSSTD="AXI"/></BUSINTERFACES>
    </MODULE>
  </MODULES>
  <MEMRANGES>
    <MEMRANGE ADDRESSBLOCK="Reg" BASEVALUE="0x40400000" HIGHVALUE="0x4040FFFF"
      INSTANCE="dma" MEMTYPE="REGISTER" MASTERBUSINTERFACE="M_AXI_GP0" SLAVEBUSINTERFACE="S_AXI_LITE"/>
  </MEMRANGES>
</EDKSYSTEM>
'''


def call(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, *args], text=True, encoding="utf-8",
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=check)


class OfflineWorkflow(unittest.TestCase):
    def test_inventory_bundle_mock_and_negative_control(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            hwh = root / "fixture.hwh"; bit = root / "fixture.bit"
            hwh.write_text(HWH, encoding="utf-8"); bit.write_bytes(b"fixture-bit")
            inventory = root / "inventory.json"
            call(str(SCRIPTS / "inspect_hwh.py"), "--hwh", str(hwh), "--bit", str(bit),
                 "--output", str(inventory))
            parsed = json.loads(inventory.read_text(encoding="utf-8"))
            self.assertEqual(parsed["dma_candidates"], ["dma"])
            self.assertEqual(parsed["address_ranges"][0]["base"], 0x40400000)

            bundle = root / "bundle"
            call(str(SCRIPTS / "generate_validation_bundle.py"), "--hwh", str(hwh),
                 "--bit", str(bit), "--output-dir", str(bundle), "--project-name", "fixture")
            (bundle / "adapter.py").write_text(
                (EXAMPLES / "numpy_double_adapter.py").read_text(encoding="utf-8"), encoding="utf-8")
            np.savez(bundle / "vectors/input.npz", input=np.arange(16, dtype=np.float32))
            np.savez(bundle / "vectors/golden.npz", output=np.arange(16, dtype=np.float32) * 2)
            contract_path = bundle / "overlay_validation.json"
            contract = json.loads(contract_path.read_text(encoding="utf-8"))
            contract["adapter_config"] = {"scale": 2.0}
            contract["test"]["warmup"] = 1; contract["test"]["repeats"] = 3
            contract_path.write_text(json.dumps(contract, indent=2) + "\n", encoding="utf-8")

            passed = root / "passed"
            call(str(SCRIPTS / "run_board_validation.py"), "--contract", str(contract_path),
                 "--output", str(passed), "--mock")
            report = json.loads((passed / "validation_report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["status"], "PASS_MOCK")
            self.assertFalse(report["physical_board_evidence"])
            self.assertEqual(report["timing"]["count"], 3)

            np.savez(bundle / "vectors/golden.npz", output=np.zeros(16, dtype=np.float32))
            failed = root / "failed"
            process = call(str(SCRIPTS / "run_board_validation.py"), "--contract", str(contract_path),
                           "--output", str(failed), "--mock", check=False)
            self.assertNotEqual(process.returncode, 0)
            negative = json.loads((failed / "validation_report.json").read_text(encoding="utf-8"))
            self.assertEqual(negative["status"], "FAIL")
            self.assertIn("golden comparison failed", negative["failure"])


if __name__ == "__main__":
    unittest.main()
