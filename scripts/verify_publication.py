"""Verify the complete publication and, optionally, every local release asset."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def verify(assets_dir=None):
    subprocess.run([sys.executable, "-B", str(ROOT / "scripts/verify_kit.py")], check=True)
    manifest = read_json(ROOT / "PACKAGE_SHA256.json")
    for item in manifest["files"]:
        path = item["path"]
        if not path.isascii() or " " in path:
            raise RuntimeError("Repository path must use English without spaces: " + path)
        if item["bytes"] >= 100 * 1024 * 1024:
            raise RuntimeError("Regular GitHub file is too large: " + path)
    identity = read_json(ROOT / "data/publication_identity.json")
    if sha256(ROOT / "report/design_report.pdf") != identity["report_sha256"]:
        raise RuntimeError("Design report identity changed")
    if sha256(ROOT / "build/prebuilt/n2_stage38_raw.bit") != identity["physical_bit_sha256"]:
        raise RuntimeError("Deployed bitstream identity changed")
    physical = ROOT / "board/evidence/physical_export"
    export = read_json(physical / "physical_evidence_manifest.json")
    for item in export["files"]:
        if sha256(physical / item["path"]) != item["sha256"]:
            raise RuntimeError("Physical evidence hash mismatch: " + item["path"])
    final = read_json(ROOT / "board/evidence/physical_finalization.json")
    if final["status"] != "FULL_PASS" or not final["claims_allowed"]:
        raise RuntimeError("Archived physical finalization did not pass")
    total = 0
    for scene in ("s23", "Meetingroom", "Office1", "Office2"):
        rel = "scenes/" + scene + "/evidence/stage39/all_test/identity.json"
        if read_json(physical / rel)["bit_sha256"] != identity["physical_bit_sha256"]:
            raise RuntimeError("Physical scene/bit mismatch: " + scene)
        metrics = read_json(ROOT / ("data/reference_results/" + scene.lower() + "/validation.json"))
        rows = metrics["rows"]
        expected = {"s23": 1225, "Meetingroom": 200, "Office1": 200, "Office2": 100}[scene]
        if len(rows) != expected or len({r["spectrum_id"] for r in rows}) != expected:
            raise RuntimeError("Full test reference coverage mismatch: " + scene)
        total += len(rows)
    if total != 1725:
        raise RuntimeError("Full test count mismatch")
    if assets_dir is not None:
        assets_dir = Path(assets_dir)
        for item in read_json(ROOT / "data/release_assets.json")["assets"]:
            path = assets_dir / item["name"]
            if not path.is_file() or path.stat().st_size != item["bytes"] or sha256(path) != item["sha256"]:
                raise RuntimeError("Missing or changed release asset: " + str(path))
            if path.suffix == ".zip":
                with zipfile.ZipFile(path) as package:
                    if package.testzip():
                        raise RuntimeError("ZIP integrity failure: " + item["name"])
        with zipfile.ZipFile(assets_dir / "nerf2_inputs.zip") as package:
            for item in read_json(ROOT / "data/input_manifest.json")["files"]:
                content = package.read("nerf2_inputs/" + item["path"])
                if len(content) != item["bytes"] or hashlib.sha256(content).hexdigest() != item["sha256"]:
                    raise RuntimeError("Original input mismatch: " + item["path"])
        with zipfile.ZipFile(assets_dir / "board_package.zip") as package:
            for scene in ("s23", "Meetingroom", "Office1", "Office2"):
                rel = "scenes/" + scene + "/evidence/stage39/all_test/identity.json"
                expected = read_json(physical / rel)["runtime_model_sha256"]
                content = package.read("N2-NeRF2-Stage39-board/scenes/" + scene + "/model/stage37_runtime_model.npz")
                if hashlib.sha256(content).hexdigest() != expected:
                    raise RuntimeError("Board package/runtime mismatch: " + scene)
        with zipfile.ZipFile(assets_dir / "test_results.zip") as package:
            for scene, count in (("s23", 1225), ("Meetingroom", 200), ("Office1", 200), ("Office2", 100)):
                prefix = "scenes/" + scene + "/evidence/stage39_pc_app/carousel_fpga_cache/"
                names = [n for n in package.namelist() if n.startswith(prefix) and n.endswith(".json")]
                if len(names) != count:
                    raise RuntimeError("Actual FPGA map coverage mismatch: " + scene)
                metrics = read_json(ROOT / ("data/reference_results/" + scene.lower() + "/validation.json"))
                for row in metrics["rows"]:
                    saved = json.loads(package.read(prefix + row["spectrum_id"] + ".json"))
                    if saved["fpga_raw_q15_sha256"] != row["fpga_raw_q15_sha256"]:
                        raise RuntimeError("Saved FPGA output identity mismatch: " + scene + "/" + row["spectrum_id"])
                    if saved["source"]["map_sha256"] != row["fpga_map_npz_sha256"]:
                        raise RuntimeError("Saved FPGA map source mismatch: " + scene + "/" + row["spectrum_id"])
    print("COMPLETE PUBLICATION VERIFY PASS (1725 maps; report, physical evidence and source hashes)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assets-dir", type=Path)
    args = parser.parse_args()
    verify(args.assets_dir)
