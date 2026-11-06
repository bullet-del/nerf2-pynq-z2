from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


ROOT = Path(os.path.abspath(__file__)).parents[1]
TEMPLATE = ROOT / "00_config/reproduction_config.json"
LOCAL = ROOT / "00_config/reproduction_config.local.json"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create the ignored machine-local Stage39 reproduction configuration"
    )
    parser.add_argument("--input-root", type=Path, default=ROOT / "data/inputs",
                        help="NeRF2-main directory containing data/rfid and logs/rfid (repository lowercase layout)")
    parser.add_argument("--vivado", type=Path, required=True,
                        help="Vivado 2024.1 executable, for example vivado.bat")
    parser.add_argument("--force", action="store_true",
                        help="replace an existing local configuration")
    args = parser.parse_args()
    input_root = Path(os.path.abspath(args.input_root.expanduser()))
    vivado = Path(os.path.abspath(args.vivado.expanduser()))
    if not input_root.is_dir():
        raise FileNotFoundError(f"input root does not exist: {input_root}")
    if not (input_root / "data/rfid").is_dir() or not (input_root / "logs/rfid").is_dir():
        raise RuntimeError("input root must contain data/rfid and logs/rfid (repository lowercase layout)")
    if not vivado.is_file():
        raise FileNotFoundError(f"Vivado executable does not exist: {vivado}")
    if LOCAL.exists() and not args.force:
        raise FileExistsError(f"local configuration already exists: {LOCAL}; use --force to replace it")
    value = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    value["input_root"] = input_root.as_posix()
    value["vivado_executable"] = vivado.as_posix()
    LOCAL.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"WROTE {LOCAL}")
    print("The orchestration scripts will select this ignored local configuration automatically.")


if __name__ == "__main__":
    main()
