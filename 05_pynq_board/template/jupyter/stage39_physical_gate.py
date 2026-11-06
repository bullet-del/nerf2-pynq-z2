#!/usr/bin/env python3
"""Run the four-scene Stage39 physical exactness and latency gates."""
from __future__ import annotations

import hashlib
import json
import time
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCENES = ("s23", "Meetingroom", "Office1", "Office2")


def request(scene, endpoint, payload, timeout):
    url = "http://127.0.0.1:8311/scene/%s%s" % (scene, endpoint)
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body,
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def run_selftests():
    rows = []
    for scene in SCENES:
        print("[Stage39 physical] self-test", scene, flush=True)
        value = request(scene, "/api/selftest", {}, 180)
        if not value.get("physical_full_map_vs_portable_integer_bit_exact"):
            raise AssertionError(scene + " physical map is not bit exact")
        rows.append(value)
    report = {"schema": "n2-stage39-four-scene-physical-exact-gate-v1",
              "status": "PASS", "scenes": rows}
    path = ROOT / "evidence/stage39_physical_gate/selftests.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2), flush=True)
    return report


def run_benchmarks(repeats=50):
    rows = []
    for scene in SCENES:
        print("[Stage39 physical] %s benchmark 0/%d" % (scene, repeats), flush=True)
        value = request(scene, "/api/benchmark", {"repeats": int(repeats)},
                        max(300, int(repeats) * 5))["result"]
        print("[Stage39 physical] %s median=%.4fs p95=%.4fs" %
              (scene, value["median_seconds"], value["p95_seconds"]), flush=True)
        rows.append(value)
    required = all(x["stable_raw_q15_hash"] and x["matches_portable_integer"]
                   and x["median_under_1p2"] and x["p95_under_1p5"] for x in rows)
    report = {"schema": "n2-stage39-four-scene-latency-stability-gate-v1",
              "status": "PASS" if required else "FAIL", "repeats_per_scene": repeats,
              "total_maps": repeats * len(rows), "scenes": rows}
    path = ROOT / "evidence/stage39_physical_gate/benchmark_50.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    if not required: raise AssertionError(report)
    print(json.dumps(report, indent=2), flush=True)
    return report


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def export_evidence():
    """Export a small, self-verifying archive of real-board Stage39 evidence.

    The archive deliberately includes summaries and identities rather than the
    thousands of full-test map files.  The PC-side finalizer independently
    checks the full-test records retained by the App.
    """
    destination = ROOT / "evidence/stage39_physical_gate"
    required = (destination / "selftests.json", destination / "benchmark_50.json")
    for path in required:
        if not path.is_file():
            raise RuntimeError("required physical evidence is missing: %s" % path)
    selftests = json.loads(required[0].read_text(encoding="utf-8"))
    benchmark = json.loads(required[1].read_text(encoding="utf-8"))
    if selftests.get("status") != "PASS" or benchmark.get("status") != "PASS":
        raise RuntimeError("physical self-test and 50-run benchmark must both PASS")

    sources = list(required)
    environment = ROOT / "evidence/stage39/board_environment.json"
    if environment.is_file(): sources.append(environment)
    for scene in SCENES:
        benchmark_path = ROOT / "scenes" / scene / "evidence/stage39/benchmark_50.json"
        if not benchmark_path.is_file():
            raise RuntimeError("scene benchmark evidence is missing: %s" % benchmark_path)
        sources.append(benchmark_path)
        full_test = ROOT / "scenes" / scene / "evidence/stage39/all_test/result.json"
        identity = ROOT / "scenes" / scene / "evidence/stage39/all_test/identity.json"
        if full_test.is_file():
            if not identity.is_file():
                raise RuntimeError("full-test identity is missing: %s" % identity)
            sources.extend((identity, full_test))

    entries = []
    for source in sources:
        relative = source.relative_to(ROOT).as_posix()
        entries.append({"path": relative, "sha256": _sha256(source),
                        "bytes": int(source.stat().st_size)})
    manifest = {
        "schema": "n2-stage39-physical-evidence-export-v1",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "board_package": ROOT.name,
        "scenes": list(SCENES),
        "contains_full_test_summaries": {
            scene: any(x["path"] ==
                       ("scenes/%s/evidence/stage39/all_test/result.json" % scene)
                       for x in entries) for scene in SCENES},
        "files": entries,
    }
    manifest_path = destination / "physical_evidence_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8")
    archive = destination / "Stage39_physical_evidence.zip"
    temporary = archive.with_suffix(".zip.tmp")
    if temporary.exists():
        temporary.unlink()
    with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED,
                         compresslevel=6) as output:
        output.write(manifest_path, "physical_evidence_manifest.json")
        for source in sources:
            output.write(source, source.relative_to(ROOT).as_posix())
    temporary.replace(archive)

    # Read the archive back before telling the user it is ready to download.
    with zipfile.ZipFile(archive, "r") as check:
        if check.testzip() is not None:
            raise RuntimeError("physical evidence ZIP integrity check failed")
        saved = json.loads(check.read("physical_evidence_manifest.json").decode("utf-8"))
        for entry in saved["files"]:
            actual = hashlib.sha256(check.read(entry["path"])).hexdigest()
            if actual != entry["sha256"]:
                raise RuntimeError("physical evidence hash mismatch: " + entry["path"])
    report = {"ok": True, "archive": str(archive), "sha256": _sha256(archive),
              "files": len(entries), "manifest": manifest}
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    return report


__all__ = ["run_selftests", "run_benchmarks", "export_evidence"]
