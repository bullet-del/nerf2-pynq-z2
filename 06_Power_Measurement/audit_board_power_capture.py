#!/usr/bin/env python3
"""Audit an exhaustive FPGA run against an independently reviewed screen recording.

Power is trapezoidally integrated with interpolated interval boundaries. The
alignment file records manually inspected video events; the tool does not infer
phase starts from changes in power. Sensitivity ranges are not confidence
intervals or a calibration certificate for the instrument.
"""
from __future__ import annotations

import argparse
import bisect
import csv
import hashlib
import json
import math
import re
from datetime import datetime, timedelta
from pathlib import Path

from convert_usbmeter_cfn import decode

COUNTS = {"s23": 1225, "Meetingroom": 200, "Office1": 200, "Office2": 100}


def parse_time(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Timezone-aware timestamps are required")
    return result


def file_sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for part in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(part)
    return digest.hexdigest()


def integrate(times, values, start, end):
    """Integrate the piecewise linear trace; never extrapolate past samples."""
    if len(times) != len(values) or len(times) < 2:
        raise ValueError("At least two paired samples are required")
    if not times[0] <= start < end <= times[-1]:
        raise ValueError(f"Interval {start}..{end} outside capture {times[0]}..{times[-1]}")

    def value_at(t):
        k = bisect.bisect_right(times, t) - 1
        if times[k] == t or k == len(times) - 1:
            return values[k]
        fraction = (t - times[k]) / (times[k + 1] - times[k])
        return values[k] + fraction * (values[k + 1] - values[k])

    knots = [start] + times[bisect.bisect_right(times, start):bisect.bisect_left(times, end)] + [end]
    y = [value_at(t) for t in knots]
    return math.fsum((b - a) * (ya + yb) * 0.5
                     for a, b, ya, yb in zip(knots, knots[1:], y, y[1:]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--cfn", type=Path, required=True)
    parser.add_argument("--alignment", type=Path, required=True)
    parser.add_argument("--previous-run", type=Path)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    run = json.loads(args.run.read_text(encoding="utf-8"))
    alignment = json.loads(args.alignment.read_text(encoding="utf-8"))
    names, rows, capture = decode(args.cfn)
    maps = run["map_results"]
    scene, repeat = run["scene"], run["repeat_id"]
    count = COUNTS[scene]
    ids = [m["spectrum_id"] for m in maps]
    clock = run["clock_validation"]
    checks = {
        "run_schema": run["schema"] == "stage39-pynq-fpga-exhaustive-power-run-v1",
        "exhaustive_map_count": run["maps"] == run["total_test_maps"] == len(maps) == count,
        "unique_test_ids": len(set(ids)) == count,
        "declared_catalog_order": ids == run["spectrum_ids"],
        "output_indexes": [m["test_output_index"] for m in maps] == list(range(count)),
        "fixed_resolution_and_samples": run["resolution"] == [90, 360]
            and run["rays_per_map"] == 32400 and run["samples_per_ray"] == 64,
        "sha256_format": all(re.fullmatch("[0-9a-f]{64}", m["raw_q15_sha256"]) for m in maps),
        "every_output_matches_board_saved_reference": all(m["matches_saved_full_test_hash"] is True for m in maps),
        "all_declared_validity_true": bool(run["validity"]) and all(v is True for v in run["validity"].values()),
        "positive_wall_and_compute_times": all(math.isfinite(m["wall_seconds"])
            and math.isfinite(m["fpga_compute_seconds"])
            and 0 < m["fpga_compute_seconds"] <= m["wall_seconds"] for m in maps),
        "map_datetime_and_monotonic_duration_agree": all(abs(
            (parse_time(m["map_end_utc"]) - parse_time(m["map_start_utc"])).total_seconds()
            - m["wall_seconds"]) < .005 for m in maps),
        "maps_in_active_window": all(parse_time(run["active_start_utc"])
            <= parse_time(m["map_start_utc"]) < parse_time(m["map_end_utc"])
            <= parse_time(run["active_end_utc"]) for m in maps),
        "map_order_and_no_overlap": all(parse_time(a["map_end_utc"])
            <= parse_time(b["map_start_utc"]) for a, b in zip(maps, maps[1:])),
        "phase_datetime_and_monotonic_duration_agree": all(abs(
            (parse_time(run[f"{state}_end_utc"]) - parse_time(run[f"{state}_start_utc"])).total_seconds()
            - run[f"{state}_wall_seconds"]) < .005 for state in ("idle", "active")),
        "compute_sum_agrees": abs(math.fsum(m["fpga_compute_seconds"] for m in maps)
            - run["fpga_compute_seconds_sum"]) < 1e-6,
        "seconds_per_map_agrees": abs(run["seconds_per_map_wall"]
            - run["active_wall_seconds"] / count) < 1e-10,
        "clock_start_and_end_pass": clock["status"] == clock["end_check"]["status"] == "PASS",
        "clock_drift_within_guard": all(abs(c["drift_since_sync_s"]) <= .2
            and c["best_request_round_trip_s"] <= .2 for c in (clock, clock["end_check"])),
        "clock_reference_same_at_start_and_end": clock["pc_reference_utc"] == clock["end_check"]["pc_reference_utc"],
        "recorded_date_matches_clock": parse_time(run["created_utc"]).date()
            == parse_time(clock["pc_reference_utc"]).date(),
        "alignment_schema_and_manual_review": alignment["schema"] == "stage39-screenrecord-time-alignment-v1"
            and alignment["reviewed"] is True,
        "alignment_cfn_sha256": alignment["cfn_sha256"] == capture["source_sha256"],
        "alignment_run_sha256": alignment["run_sha256"] == file_sha(args.run),
        "video_sha256": alignment["video_sha256"] == file_sha(alignment["video_file"]),
    }
    if scene == "Office2":
        checks["office2_test_ids_401_to_500"] = ids == [str(i) for i in range(401, 501)]
    if args.previous_run:
        previous = json.loads(args.previous_run.read_text(encoding="utf-8"))
        checks["same_100_outputs_as_preserved_previous_run"] = (
            previous["scene"] == scene and
            [(m["spectrum_id"], m["raw_q15_sha256"]) for m in previous["map_results"]]
            == [(m["spectrum_id"], m["raw_q15_sha256"]) for m in maps])
    failures = [key for key, ok in checks.items() if not ok]
    if failures:
        raise ValueError("Audit checks failed: " + ", ".join(failures))

    start_video = float(alignment["recorder_active_first_video_s"])
    stop_video = float(alignment["recorder_stopped_first_video_s"])
    fps = float(alignment["video_fps"])
    sensitivity = float(alignment["boundary_shift_sensitivity_s"])
    if fps <= 0 or not 0 < sensitivity <= 1 or stop_video <= start_video:
        raise ValueError("Invalid video timing settings")
    ref = alignment["phase_first_visible_video_s"]
    origin = parse_time(run["idle_start_utc"]) - timedelta(seconds=ref["idle_start"] - start_video)
    residuals = {}
    for field, visible in ref.items():
        if field not in {"idle_start", "idle_end", "active_start", "active_end"}:
            raise ValueError("Unknown phase marker")
        estimated_video = start_video + (parse_time(run[field + "_utc"]) - origin).total_seconds()
        residuals[field] = visible - estimated_video
    cadence_residual = (stop_video - start_video) - capture["duration_s"]
    # One sample interval may separate the last sample from the stop click.
    checks["video_cadence_matches_stored_time_axis"] = abs(cadence_residual) <= .2 + 1 / fps
    checks["independent_phase_events_agree"] = (
        set(ref) == {"idle_start", "idle_end", "active_start", "active_end"}
        and max(map(abs, residuals.values())) <= sensitivity)
    if not all(checks.values()):
        raise ValueError("Independent video/capture timing checks failed")

    times = [r["relative_time_s"] for r in rows]
    power = [r["power_W"] for r in rows]

    def window(start_utc, end_utc):
        a = (parse_time(start_utc) - origin).total_seconds()
        b = (parse_time(end_utc) - origin).total_seconds()
        duration = b - a
        energy = integrate(times, power, a, b)
        shifted = [integrate(times, power, a + delta, b + delta)
                   for delta in (i * sensitivity / 100 for i in range(-100, 101))]
        return {
            "start_utc": start_utc, "end_utc": end_utc,
            "relative_start_s": a, "relative_end_s": b, "duration_s": duration,
            "samples_inside_interval": sum(a <= t <= b for t in times),
            "energy_J": energy, "energy_Wh": energy / 3600,
            "mean_power_W": energy / duration,
            "mean_voltage_V": integrate(times, [r["voltage_V"] for r in rows], a, b) / duration,
            "mean_current_A": integrate(times, [r["current_A"] for r in rows], a, b) / duration,
            "energy_shift_sensitivity_min_J": min(shifted),
            "energy_shift_sensitivity_max_J": max(shifted),
        }

    phases = {state: window(run[state + "_start_utc"], run[state + "_end_utc"])
              for state in ("idle", "active")}
    per_map = []
    for m in maps:
        w = window(m["map_start_utc"], m["map_end_utc"])
        if w["samples_inside_interval"] < 2:
            raise ValueError(f"Insufficient recorded samples for map {m['spectrum_id']}")
        per_map.append({"test_output_index": m["test_output_index"],
            "spectrum_id": m["spectrum_id"], **w,
            "wall_seconds": m["wall_seconds"], "fpga_compute_seconds": m["fpga_compute_seconds"],
            "raw_q15_sha256": m["raw_q15_sha256"],
            "dynamic_energy_J_using_this_run_idle": w["energy_J"] - phases["idle"]["mean_power_W"] * w["duration_s"],
        })
    total_j = phases["active"]["energy_J"]
    map_sum_j = math.fsum(m["energy_J"] for m in per_map)
    gap_j = total_j - map_sum_j
    checks["all_maps_have_power_samples"] = len(per_map) == count and min(
        m["samples_inside_interval"] for m in per_map) >= 2
    checks["per_map_energy_and_gap_energy_reconcile"] = gap_j >= -1e-6
    report = {
        "schema": "stage39-aligned-fpga-power-audit-v1",
        "status": "ACCEPTED_SINGLE_RUN_WITH_SCREENRECORD_ALIGNMENT",
        "formal_R1_accepted": repeat == "R1", "formal_repeat_accepted": True,
        "scene": scene, "repeat_id": repeat,
        "json_file": str(args.run.resolve()), "json_sha256": file_sha(args.run),
        "cfn_file": str(args.cfn.resolve()), "cfn_sha256": capture["source_sha256"],
        "alignment_file": str(args.alignment.resolve()), "video_file": alignment["video_file"],
        "checks": checks, "clock_validation": clock, "meter_capture": capture,
        "nominal_meter_time_zero_utc": origin.isoformat(),
        "time_alignment": {
            "method": "Record-start UI transition plus UTC phase first-appearance events in the same continuous screen recording",
            "phase_video_residual_s": residuals, "video_minus_cfn_duration_s": cadence_residual,
            "video_duration_between_recorder_start_stop_s": stop_video - start_video,
            "boundary_shift_sensitivity_s": sensitivity,
            "precision_note": "Nominal alignment is estimated from 30 fps UI events and 10 Hz samples. Shift sensitivity is not a certified bound on absolute timing error or instrument accuracy.",
        },
        "phases": phases,
        "summary": {
            "maps": count, "active_wall_seconds": run["active_wall_seconds"],
            "seconds_per_map_wall": run["seconds_per_map_wall"],
            "active_mean_power_W": phases["active"]["mean_power_W"],
            "idle_mean_power_W": phases["idle"]["mean_power_W"],
            "active_energy_J": total_j, "total_board_energy_per_map_J": total_j / count,
            "dynamic_energy_per_map_J_using_this_run_idle": (
                total_j - phases["idle"]["mean_power_W"] * phases["active"]["duration_s"]) / count,
            "sum_per_map_call_interval_energy_J": map_sum_j,
            "active_inter_map_and_bookkeeping_energy_J": gap_j,
            "minimum_samples_per_map": min(m["samples_inside_interval"] for m in per_map),
            "maximum_samples_per_map": max(m["samples_inside_interval"] for m in per_map),
        },
        "limitations": [
            "Output hashes are checked against the saved references by the board script; this audit checks evidence consistency rather than executing physical FPGA hardware.",
            "The primary J/map includes the whole active sweep, including gaps and bookkeeping. Per-map rows cover the individual recorded call intervals and do not include inter-map gaps.",
            "FNB58 reports power at its USB measurement point, including downstream power-cable losses; these values are not isolated FPGA-chip power.",
            "10 Hz samples with screen-record alignment support approximate per-map energy; sub-millisecond power profiles and certified instrument accuracy are not established.",
            "Software NRG is inconsistent with the V*I time integral and is excluded; no correction factor is applied to raw NRG.",
            "This is one repeat of Office2/FPGA. R2/R3, the other scenes and ARM runs are still required before a complete board comparison.",
        ],
    }
    if not all(checks.values()):
        raise ValueError("Power interval audit failed")
    args.out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{scene}_FPGA_{repeat}_REMEASURE"
    targets = [args.out_dir / (stem + suffix) for suffix in ("_audit.json", "_per_map_energy.csv", "_power_rows.csv")]
    if any(p.exists() for p in targets):
        raise ValueError("Audit output already exists; preserve it and choose a new output directory")
    targets[0].write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    with targets[1].open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(per_map[0]))
        writer.writeheader()
        writer.writerows(per_map)
    fields = ["scene", "platform", "state", "repeat", "maps", "duration_s", "energy_wh",
              "avg_voltage_v", "avg_current_a", "avg_power_w", "instrument", "start_utc", "end_utc", "source_file", "notes"]
    with targets[2].open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for state, w in phases.items():
            writer.writerow({"scene": scene, "platform": "PYNQ_FPGA", "state": state,
                "repeat": int(repeat[1:] if repeat.startswith("R") else repeat), "maps": count if state == "active" else 0,
                "duration_s": w["duration_s"], "energy_wh": w["energy_Wh"],
                "avg_voltage_v": w["mean_voltage_V"], "avg_current_a": w["mean_current_A"],
                "avg_power_w": w["mean_power_W"], "instrument": "FNIRSI FNB58 USB-PC",
                "start_utc": w["start_utc"], "end_utc": w["end_utc"], "source_file": str(args.cfn.resolve()),
                "notes": "PBUS trapezoidal integration; continuous video alignment; +/-0.2s shift sensitivity evaluated; software NRG excluded; full board USB input including downstream cable losses"})
    print(json.dumps({"status": report["status"], "checks_passed": len(checks),
                      "summary": report["summary"], "outputs": list(map(str, targets))}, indent=2))


if __name__ == "__main__":
    main()
