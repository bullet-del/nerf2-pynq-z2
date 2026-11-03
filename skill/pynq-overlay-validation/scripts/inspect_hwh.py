#!/usr/bin/env python3
"""Inspect a Vivado HWH file without requiring Vivado or a PYNQ board."""
from __future__ import annotations

import argparse
import hashlib
import json
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def number(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value, 0)
    except ValueError:
        return None


def inspect_hwh(hwh: Path, bit: Path | None = None) -> dict[str, Any]:
    hwh = hwh.resolve()
    if not hwh.is_file():
        raise FileNotFoundError(hwh)
    if hwh.suffix.lower() != ".hwh":
        raise ValueError(f"expected .hwh file, got {hwh.name}")
    root = ET.parse(hwh).getroot()
    # Some Vivado releases add an XML namespace while older HWH files do not.
    # Normalize tags so the same parser works for both forms.
    for element in root.iter():
        if "}" in element.tag:
            element.tag = element.tag.split("}", 1)[1]
    system = root.find(".//SYSTEMINFO")
    modules = []
    for item in root.findall(".//MODULE"):
        interfaces = []
        for bus in item.findall("./BUSINTERFACES/BUSINTERFACE"):
            interfaces.append({
                "name": bus.attrib.get("NAME"),
                "bus_name": bus.attrib.get("BUSNAME"),
                "type": bus.attrib.get("TYPE"),
                "bus_standard": bus.attrib.get("BUSSTD"),
                "data_width": number(bus.attrib.get("DATAWIDTH")),
            })
        modules.append({
            "instance": item.attrib.get("INSTANCE") or item.attrib.get("FULLNAME", "").lstrip("/"),
            "full_name": item.attrib.get("FULLNAME"),
            "modtype": item.attrib.get("MODTYPE"),
            "vlnv": item.attrib.get("VLNV"),
            "hardware_version": item.attrib.get("HWVERSION"),
            "ip_type": item.attrib.get("IPTYPE"),
            "bus_interfaces": interfaces,
        })
    ranges = []
    for item in root.findall(".//MEMRANGE"):
        low = number(item.attrib.get("BASEVALUE"))
        high = number(item.attrib.get("HIGHVALUE"))
        ranges.append({
            "instance": item.attrib.get("INSTANCE"),
            "memory_type": item.attrib.get("MEMTYPE"),
            "address_block": item.attrib.get("ADDRESSBLOCK"),
            "base": low,
            "high": high,
            "bytes": None if low is None or high is None else high - low + 1,
            "master_interface": item.attrib.get("MASTERBUSINTERFACE"),
            "slave_interface": item.attrib.get("SLAVEBUSINTERFACE"),
        })
    interrupts = []
    for item in root.findall(".//INTERRUPT"):
        interrupts.append(dict(item.attrib))
    external_ports = [dict(item.attrib) for item in root.findall("./EXTERNALPORTS/PORT")]
    dma = [m["instance"] for m in modules
           if "dma" in str(m.get("modtype", "")).lower()
           or "dma" in str(m.get("vlnv", "")).lower()]
    custom = [m["instance"] for m in modules
              if ":module_ref:" in str(m.get("vlnv", ""))]
    warnings = []
    bit_value = None
    if bit is not None:
        bit = bit.resolve()
        if not bit.is_file():
            raise FileNotFoundError(bit)
        if bit.suffix.lower() != ".bit":
            raise ValueError(f"expected .bit file, got {bit.name}")
        if bit.stem != hwh.stem:
            warnings.append("BIT and HWH base names differ; PYNQ normally pairs files by base name")
        bit_value = {"path": str(bit), "bytes": bit.stat().st_size, "sha256": sha256(bit)}
    return {
        "schema": "pynq-hwh-inventory-v1",
        "hwh": {"path": str(hwh), "bytes": hwh.stat().st_size, "sha256": sha256(hwh)},
        "bit": bit_value,
        "hwh_xml": dict(root.attrib),
        "system": {} if system is None else dict(system.attrib),
        "module_count": len(modules),
        "modules": modules,
        "address_ranges": ranges,
        "external_port_count": len(external_ports),
        "external_ports": external_ports,
        "interrupts": interrupts,
        "dma_candidates": dma,
        "custom_module_candidates": custom,
        "warnings": warnings,
    }


def markdown(report: dict[str, Any]) -> str:
    system = report.get("system") or {}
    lines = [
        "# PYNQ Overlay HWH inventory", "",
        f"- Design: `{system.get('NAME', '')}`",
        f"- Device: `{system.get('DEVICE', '')}`",
        f"- Vivado: `{report.get('hwh_xml', {}).get('VIVADOVERSION', '')}`",
        f"- Modules: `{report.get('module_count')}`",
        f"- DMA candidates: `{', '.join(report.get('dma_candidates') or [])}`", "",
        "## IP modules", "",
        "| Instance | Type | VLNV | Bus interfaces |", "|---|---|---|---:|",
    ]
    for module in report.get("modules", []):
        lines.append(f"| `{module.get('instance')}` | `{module.get('modtype')}` | "
                     f"`{module.get('vlnv')}` | {len(module.get('bus_interfaces') or [])} |")
    lines.extend(["", "## Address ranges", "",
                  "| Instance | Type | Base | High | Bytes |", "|---|---|---:|---:|---:|"])
    for item in report.get("address_ranges", []):
        base = "" if item.get("base") is None else f"0x{item['base']:08X}"
        high = "" if item.get("high") is None else f"0x{item['high']:08X}"
        lines.append(f"| `{item.get('instance')}` | `{item.get('memory_type')}` | "
                     f"`{base}` | `{high}` | {item.get('bytes')} |")
    if report.get("warnings"):
        lines.extend(["", "## Warnings", ""])
        lines.extend(f"- {value}" for value in report["warnings"])
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a machine-readable inventory from a Vivado HWH")
    parser.add_argument("--hwh", type=Path, required=True)
    parser.add_argument("--bit", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--markdown", type=Path)
    args = parser.parse_args()
    report = inspect_hwh(args.hwh, args.bit)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    md = args.markdown or args.output.with_suffix(".md")
    md.write_text(markdown(report), encoding="utf-8")
    print(json.dumps({"status": "PASS", "modules": report["module_count"],
                      "dma_candidates": report["dma_candidates"],
                      "output": str(args.output)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
