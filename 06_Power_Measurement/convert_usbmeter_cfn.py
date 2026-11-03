#!/usr/bin/env python3
"""Decode the observed UsbMeter .cfn layout, preserving raw NRG separately.

Validated against the user's 10 sps test1.cfn and Office2_R1.cfn files.
Reject unfamiliar layouts instead of guessing. The file's time axis is relative;
this converter never invents an absolute start time or inference interval.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import statistics
import struct
from pathlib import Path

CHANNELS = {0: "voltage_V", 1: "current_A", 2: "D_plus_V", 3: "D_minus_V",
            4: "power_W", 5: "software_CAP_Ah", 6: "software_NRG_Wh"}


def decode(path: Path):
    payload = path.read_bytes()
    if len(payload) < 30:
        raise ValueError("Truncated .cfn header")
    sampling_rate = struct.unpack_from("<d", payload, 0)[0]
    channel_count = struct.unpack_from("<H", payload, 20)[0]
    if not math.isfinite(sampling_rate) or not 0.2 <= sampling_rate <= 100:
        raise ValueError("Unsupported .cfn sampling-rate/header layout")
    if not 1 <= channel_count <= 7:
        raise ValueError("Unsupported .cfn channel-count/header layout")
    position, descriptors = 22, []
    for _ in range(channel_count):
        if position + 7 > len(payload):
            raise ValueError("Truncated .cfn channel descriptor")
        channel_id, color, visible = struct.unpack_from("<HIB", payload, position)
        position += 7
        if channel_id not in CHANNELS or visible not in (0, 1):
            raise ValueError("Unsupported .cfn channel descriptor")
        descriptor = {"id": channel_id, "name": CHANNELS[channel_id],
                      "color_argb": color, "visible": bool(visible)}
        if visible:
            if position + 16 > len(payload):
                raise ValueError("Truncated .cfn channel extrema")
            descriptor["stored_max"], descriptor["stored_min"] = struct.unpack_from(
                "<dd", payload, position)
            position += 16
        descriptors.append(descriptor)
    ids = [d["id"] for d in descriptors]
    if len(set(ids)) != len(ids) or not {0, 1, 4}.issubset(ids):
        raise ValueError("Record must contain distinct VBUS, IBUS and PBUS channels")
    if position + 4 > len(payload):
        raise ValueError("Truncated .cfn sample count")
    sample_count = struct.unpack_from("<I", payload, position)[0]
    position += 4
    width = 8 * (channel_count + 1)
    if sample_count < 2 or len(payload) != position + sample_count * width:
        raise ValueError("Unsupported/truncated .cfn data layout or sample count")
    names = ["relative_time_s"] + [d["name"] for d in descriptors]
    rows = []
    for index in range(sample_count):
        values = struct.unpack_from("<" + "d" * len(names), payload, position + index * width)
        if not all(math.isfinite(v) for v in values):
            raise ValueError(f"Non-finite value at sample {index}")
        rows.append(dict(zip(names, values)))
    deltas = [b["relative_time_s"] - a["relative_time_s"] for a, b in zip(rows, rows[1:])]
    if any(dt <= 0 for dt in deltas):
        raise ValueError("Relative timestamps are not strictly increasing")
    if max(abs(dt - 1 / sampling_rate) for dt in deltas) > 1e-6:
        raise ValueError("Sample times disagree with stored sampling rate")
    if any(r["voltage_V"] <= 0 or r["current_A"] < 0 or r["power_W"] < 0 for r in rows):
        raise ValueError("Invalid voltage/current/power samples")
    vi_error = max(abs(r["power_W"] - r["voltage_V"] * r["current_A"]) for r in rows)
    if vi_error > max(0.02, max(r["power_W"] for r in rows) * 0.01):
        raise ValueError("PBUS disagrees with VBUS * IBUS")
    integral_j = sum((a["power_W"] + b["power_W"]) * 0.5 * dt
                     for a, b, dt in zip(rows, rows[1:], deltas))
    statistics_out = {
        "schema": "stage39-usbmeter-cfn-decoded-v1",
        "source_file": str(path.resolve()),
        "source_sha256": hashlib.sha256(payload).hexdigest(),
        "sampling_rate_sps": sampling_rate,
        "sample_count": sample_count,
        "data_offset_bytes": position,
        "channel_descriptors": descriptors,
        "relative_start_s": rows[0]["relative_time_s"],
        "relative_end_s": rows[-1]["relative_time_s"],
        "duration_s": rows[-1]["relative_time_s"] - rows[0]["relative_time_s"],
        "stored_sample_interval_s": statistics.median(deltas),
        "min_voltage_V": min(r["voltage_V"] for r in rows),
        "max_voltage_V": max(r["voltage_V"] for r in rows),
        "min_power_W": min(r["power_W"] for r in rows),
        "max_power_W": max(r["power_W"] for r in rows),
        "max_PBUS_minus_VI_W": vi_error,
        "whole_record_power_integral_J": integral_j,
        "whole_record_power_integral_Wh": integral_j / 3600,
        "absolute_start_utc": None,
        "phase_alignment_status": "REQUIRES_INDEPENDENT_TIME_ANCHOR",
        "interpretation": "Whole-record integral includes settle/countdown/idle/tail; it is not inference energy.",
    }
    if "software_NRG_Wh" in names:
        nrg_delta = rows[-1]["software_NRG_Wh"] - rows[0]["software_NRG_Wh"]
        ratio = nrg_delta * 3600 / integral_j if integral_j else None
        statistics_out.update({
            "software_NRG_delta_Wh": nrg_delta,
            "software_NRG_to_power_integral_ratio": ratio,
            "software_NRG_consistent_with_power_integral": ratio is not None and abs(ratio - 1) <= 0.05,
            "energy_method": "Integrate PBUS against relative_time_s; do not use unverified software NRG.",
        })
    return names, rows, statistics_out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cfn", type=Path)
    parser.add_argument("--output-csv", type=Path, required=True)
    args = parser.parse_args()
    names, rows, report = decode(args.cfn)
    if args.output_csv.exists() or args.output_csv.with_suffix(".decode.json").exists():
        parser.error("Output already exists; choose another name to preserve derived evidence")
    args.output_csv.parent.mkdir(parents=True, exist_ok=True)
    with args.output_csv.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=names)
        writer.writeheader()
        writer.writerows(rows)
    args.output_csv.with_suffix(".decode.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
