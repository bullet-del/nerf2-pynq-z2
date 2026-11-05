#!/usr/bin/env python3
"""Verify real-board Stage39 evidence and issue the final physical gate report."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import statistics
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath


ROOT = Path(__file__).resolve().parents[2]
SCENES = ("s23", "Meetingroom", "Office1", "Office2")


def sha256_bytes(value):
    return hashlib.sha256(value).hexdigest()


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def check(condition, message):
    if not condition:
        raise ValueError(message)


def load_physical_export(path):
    path = Path(path)
    check(path.is_file(), "physical evidence ZIP does not exist: %s" % path)
    with zipfile.ZipFile(path, "r") as archive:
        check(archive.testzip() is None, "physical evidence ZIP has a corrupt member")
        names = archive.namelist()
        check(len(names) == len(set(names)), "physical evidence ZIP has duplicate names")
        check("physical_evidence_manifest.json" in names,
              "physical evidence manifest is missing")
        manifest = json.loads(archive.read("physical_evidence_manifest.json").decode("utf-8"))
        check(manifest.get("schema") == "n2-stage39-physical-evidence-export-v1",
              "physical evidence manifest schema mismatch")
        declared = {}
        for entry in manifest.get("files", []):
            member = str(entry.get("path", ""))
            pure = PurePosixPath(member)
            check(member and not pure.is_absolute() and ".." not in pure.parts,
                  "unsafe evidence member path: %s" % member)
            check(member not in declared, "duplicate evidence member: %s" % member)
            check(member in names, "declared evidence member is missing: %s" % member)
            payload = archive.read(member)
            check(len(payload) == int(entry.get("bytes", -1)),
                  "evidence size mismatch: %s" % member)
            check(sha256_bytes(payload) == entry.get("sha256"),
                  "evidence SHA-256 mismatch: %s" % member)
            declared[member] = json.loads(payload.decode("utf-8"))
    return manifest, declared


def validate_selftests(files, registry):
    name = "evidence/stage39_physical_gate/selftests.json"
    check(name in files, "four-scene self-test report is missing")
    value = files[name]
    check(value.get("status") == "PASS", "four-scene physical self-test did not PASS")
    rows = {row.get("scene_id"): row for row in value.get("scenes", [])}
    check(set(rows) == set(SCENES), "self-test scene set is incomplete")
    output = []
    for scene in SCENES:
        row = rows[scene]
        check(row.get("physical_full_map_vs_portable_integer_bit_exact") is True,
              scene + ": physical full map is not bit exact")
        check(row.get("fpga_hardware_rays") == 32400,
              scene + ": self-test is not one complete 90x360 map")
        check(row.get("actual_raw_q15_sha256") == row.get("expected_raw_q15_sha256"),
              scene + ": physical and portable Q15 hashes differ")
        expected = registry[scene]["hardware"]["runtime_model_sha256"]
        check(row.get("runtime_model_sha256") == expected,
              scene + ": self-test runtime model identity differs")
        output.append({"scene": scene, "bit_exact": True,
                       "full_map_seconds": float(row["full_map_seconds"]),
                       "raw_q15_sha256": row["actual_raw_q15_sha256"]})
    return output


def validate_benchmarks(files):
    name = "evidence/stage39_physical_gate/benchmark_50.json"
    check(name in files, "four-scene 50-run benchmark is missing")
    value = files[name]
    check(value.get("status") == "PASS", "four-scene benchmark did not PASS")
    check(value.get("repeats_per_scene") == 50, "benchmark must contain 50 runs per scene")
    rows = {row.get("scene_id"): row for row in value.get("scenes", [])}
    check(set(rows) == set(SCENES), "benchmark scene set is incomplete")
    output = []
    for scene in SCENES:
        row = rows[scene]
        scene_name = "scenes/%s/evidence/stage39/benchmark_50.json" % scene
        check(scene_name in files, scene + ": individual benchmark report is missing")
        check(files[scene_name] == row,
              scene + ": aggregate and individual benchmark reports differ")
        check(row.get("repeats") == 50 and len(row.get("times_seconds", [])) == 50,
              scene + ": benchmark does not contain 50 timings")
        check(row.get("stable_raw_q15_hash") is True and
              row.get("matches_portable_integer") is True,
              scene + ": repeated maps are not stable and exact")
        median = float(row["median_seconds"]); p95 = float(row["p95_seconds"])
        check(median <= 1.2, scene + ": median exceeds 1.2 seconds")
        check(p95 <= 1.5, scene + ": P95 exceeds 1.5 seconds")
        output.append({"scene": scene, "runs": 50, "median_seconds": median,
                       "p95_seconds": p95, "minimum_seconds": float(row["minimum_seconds"]),
                       "maximum_seconds": float(row["maximum_seconds"])})
    return output


def validate_exported_full_tests(files, registry):
    output = []
    for scene in SCENES:
        prefix = "scenes/%s/evidence/stage39/all_test/" % scene
        result_name = prefix + "result.json"; identity_name = prefix + "identity.json"
        check(result_name in files and identity_name in files,
              scene + ": export again after its complete FPGA test-set run")
        result = files[result_name]; identity = files[identity_name]
        spec = registry[scene]; hardware = spec["hardware"]
        expected_catalog = hashlib.sha256(
            (ROOT / "scenes" / scene / "data/stage32/all_test_catalog.json").read_bytes()
        ).hexdigest()
        check(identity.get("schema") == "n2-stage39-all-test-identity-v1",
              scene + ": full-test identity schema mismatch")
        check(result.get("schema") == "n2-stage39-full-test-fpga-validation-v1-qat-raw-only",
              scene + ": full-test result schema mismatch")
        check(result.get("sample_count") == spec["test_count"] and
              len(result.get("rows", [])) == spec["test_count"],
              scene + ": full-test sample count mismatch")
        check(result.get("output_mode") == "raw_int8_q15",
              scene + ": full-test output mode is not raw INT8/Q15")
        for key, expected in (("bit_sha256", hardware["bit_sha256"]),
                              ("runtime_model_sha256", hardware["runtime_model_sha256"]),
                              ("test_catalog_sha256", expected_catalog)):
            check(result.get(key) == expected and identity.get(key) == expected,
                  scene + ": full-test %s mismatch" % key)
        spectrum_ids = []
        for index, row in enumerate(result["rows"]):
            check(row.get("test_output_index") == index,
                  scene + ": non-contiguous full-test row index")
            for key in ("bit_sha256", "runtime_model_sha256", "test_catalog_sha256"):
                check(row.get(key) == identity[key],
                      scene + ": row-level %s mismatch" % key)
            spectrum_ids.append(str(row.get("spectrum_id", "")))
        check(all(spectrum_ids) and len(set(spectrum_ids)) == len(spectrum_ids),
              scene + ": full-test spectrum identities are missing or duplicated")
        output.append({"scene": scene, "samples": result["sample_count"],
                       "pc_ssim_mean": float(result["summary"]["pc_ssim_mean"]),
                       "fpga_raw_ssim_mean": float(result["summary"]["fpga_raw_ssim_mean"]),
                       "mean_ssim_gap": float(result["summary"]["pc_minus_fpga_raw_mean_ssim"])})
    return output


def validate_pc_full_tests(registry):
    output = []
    for scene in SCENES:
        path = ROOT / "scenes" / scene / "evidence/stage39_pc_app/all_1225/validation.json"
        check(path.is_file(), scene + ": PC App full-test validation is missing")
        value = load_json(path); spec = registry[scene]; hardware = spec["hardware"]
        check(value.get("schema") == "n2-stage39-full-test-fpga-validation-v1-qat-raw-only",
              scene + ": PC App validation schema mismatch")
        check(value.get("sample_count") == spec["test_count"] and
              len(value.get("rows", [])) == spec["test_count"],
              scene + ": PC App validation is incomplete")
        check(value.get("output_mode") == "raw_int8_q15",
              scene + ": PC App validation output mode mismatch")
        check(value.get("bit_sha256") == hardware["bit_sha256"],
              scene + ": PC App validation bitstream mismatch")
        check(value.get("runtime_model_sha256") == hardware["runtime_model_sha256"],
              scene + ": PC App validation runtime model mismatch")
        expected_catalog = hashlib.sha256(
            (ROOT / "scenes" / scene / "data/stage32/all_test_catalog.json").read_bytes()
        ).hexdigest()
        check(value.get("test_catalog_sha256") == expected_catalog,
              scene + ": PC App validation test catalog mismatch")
        for index, row in enumerate(value["rows"]):
            check(row.get("test_output_index") == index,
                  scene + ": PC App row ordering mismatch")
            check(row.get("bit_sha256") == hardware["bit_sha256"] and
                  row.get("runtime_model_sha256") == hardware["runtime_model_sha256"] and
                  row.get("test_catalog_sha256") == expected_catalog,
                  scene + ": PC App row identity mismatch")
        pc = float(value["summary"]["pc_ssim_mean"])
        fpga = float(value["summary"]["fpga_raw_ssim_mean"])
        gap = abs(pc - fpga)
        check(gap < 0.015, scene + ": physical FPGA/PC mean SSIM gap is not below 0.015")
        output.append({"scene": scene, "samples": value["sample_count"],
                       "pc_ssim_mean": pc, "fpga_raw_ssim_mean": fpga,
                       "absolute_mean_ssim_gap": gap, "gap_under_0p015": True})
    return output


def validate_power(path, benchmarks):
    path = Path(path)
    check(path.is_file(), "external power-meter CSV does not exist: %s" % path)
    grouped = {(scene, state): [] for scene in SCENES for state in ("idle", "inference")}
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        required = {"scene", "state", "repeat", "voltage_v", "current_a", "power_w",
                    "instrument", "timestamp_utc", "notes"}
        check(set(reader.fieldnames or ()) >= required, "power CSV header is incomplete")
        for row in reader:
            if not any(str(v).strip() for v in row.values()):
                continue
            key = (row["scene"].strip(), row["state"].strip().lower())
            check(key in grouped, "unknown power scene/state: %r" % (key,))
            voltage = float(row["voltage_v"]); current = float(row["current_a"])
            power = float(row["power_w"])
            check(voltage > 0 and current >= 0 and power > 0,
                  "power values must be positive")
            tolerance = max(0.10, abs(power) * 0.12)
            check(abs(voltage * current - power) <= tolerance,
                  "power_w differs materially from voltage_v * current_a")
            check(row["instrument"].strip() and row["timestamp_utc"].strip(),
                  "instrument and timestamp_utc are required")
            grouped[key].append(power)
    benchmark_by_scene = {row["scene"]: row for row in benchmarks}
    output = []
    for scene in SCENES:
        idle = grouped[(scene, "idle")]; active = grouped[(scene, "inference")]
        check(len(idle) >= 10 and len(active) >= 10,
              scene + ": need at least 10 idle and 10 inference power samples")
        idle_median = float(statistics.median(idle)); active_median = float(statistics.median(active))
        seconds = benchmark_by_scene[scene]["median_seconds"]
        output.append({"scene": scene, "idle_samples": len(idle),
                       "inference_samples": len(active), "idle_median_w": idle_median,
                       "inference_median_w": active_median,
                       "median_map_energy_j": active_median * seconds,
                       "median_dynamic_energy_j": max(0.0, active_median - idle_median) * seconds})
    return output


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--physical-export", required=True,
                        help="Stage39_physical_evidence.zip downloaded from PYNQ")
    parser.add_argument("--power-csv", required=True,
                        help="real external board-input power observations")
    parser.add_argument("--live-rehearsal-pass", action="store_true",
                        help="set only after HDMI and judge-nonce rehearsal was observed")
    args = parser.parse_args()
    registry_value = load_json(ROOT / "scene_registry.json")
    registry = {row["id"]: row for row in registry_value["scenes"]}
    check(set(registry) == set(SCENES), "Stage39 registry scene set mismatch")
    check(len({registry[x]["hardware"]["bit_sha256"] for x in SCENES}) == 1,
          "the four scenes do not use one common bitstream")
    checks = []; details = {}
    try:
        manifest, files = load_physical_export(args.physical_export)
        details["physical_export_manifest"] = manifest
        checks.append({"id": "physical_export_integrity", "status": "PASS"})
        details["selftests"] = validate_selftests(files, registry)
        checks.append({"id": "four_scene_physical_bit_exact", "status": "PASS"})
        details["benchmarks"] = validate_benchmarks(files)
        checks.append({"id": "four_scene_50x_latency_stability", "status": "PASS"})
        details["exported_full_tests"] = validate_exported_full_tests(files, registry)
        checks.append({"id": "four_scene_physical_full_test_export", "status": "PASS"})
        details["pc_app_full_tests"] = validate_pc_full_tests(registry)
        checks.append({"id": "four_scene_physical_accuracy_gap", "status": "PASS"})
        exported = {row["scene"]: row for row in details["exported_full_tests"]}
        local = {row["scene"]: row for row in details["pc_app_full_tests"]}
        for scene in SCENES:
            check(abs(exported[scene]["pc_ssim_mean"] - local[scene]["pc_ssim_mean"]) < 1e-12 and
                  abs(exported[scene]["fpga_raw_ssim_mean"] - local[scene]["fpga_raw_ssim_mean"]) < 1e-12,
                  scene + ": board export and PC App full-test summaries differ")
        checks.append({"id": "board_export_matches_pc_app_archive", "status": "PASS"})
        details["power"] = validate_power(args.power_csv, details["benchmarks"])
        checks.append({"id": "external_board_input_power", "status": "PASS"})
    except Exception as exc:
        checks.append({"id": "physical_evidence_validation", "status": "FAIL",
                       "error": type(exc).__name__ + ": " + str(exc)})
    checks.append({"id": "live_hdmi_and_judge_rehearsal",
                   "status": "PASS" if args.live_rehearsal_pass else "PENDING",
                   "reason": None if args.live_rehearsal_pass else
                   "rerun with --live-rehearsal-pass only after a witnessed rehearsal"})
    full_pass = all(row["status"] == "PASS" for row in checks)
    report = {"schema": "n2-stage39-physical-finalization-v1",
              "created_utc": datetime.now(timezone.utc).isoformat(),
              "status": "FULL_PASS" if full_pass else "INCOMPLETE_OR_FAILED",
              "checks": checks, "details": details,
              "claims_allowed": full_pass,
              "claim_rule": "Only FULL_PASS permits final Stage39 physical timing, power, accuracy, and live-demo claims."}
    destination = ROOT / "stage39_reports/stage39_physical_finalization.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print("report =", destination)
    return 0 if full_pass else 2


if __name__ == "__main__":
    sys.exit(main())
