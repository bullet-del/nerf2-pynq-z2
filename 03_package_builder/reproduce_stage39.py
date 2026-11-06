from __future__ import annotations

import argparse
from pathlib import Path

from common import DEFAULT_CONFIG, KIT_ROOT, load_config, python, run


BUILDER = KIT_ROOT / "03_package_builder"


def call(script: str, *arguments: object) -> None:
    run([python(), BUILDER / script, *arguments])


def preflight(config: Path) -> None:
    call("preflight.py", "--config", config)


def vivado(config_path: Path) -> None:
    config = load_config(config_path)
    run([Path(config["vivado_executable"]), "-mode", "batch", "-source",
         KIT_ROOT / "01_vivado/tcl/build_stage39.tcl"])


def models(config: Path, profile: str = "full", scene: str = "all") -> None:
    call("run_model_pipeline.py", "all", "--profile", profile, "--scene", scene, "--config", config)
    if profile == "full":
        call("measure_original_pc_reference.py", "--scene", scene, "--config", config)


def rtl(config: Path, profile: str = "full") -> None:
    call("run_rtl_simulation.py", "--profile", profile, "--config", config)


def assemble(config: Path) -> None:
    call("assemble_packages.py", "--config", config)
    call("propagate_vivado_artifact_identity.py", "--config", config)
    call("build_reports_from_source.py", "--config", config)
    call("build_delivery.py", "--package", "all")


def verify(config: Path) -> None:
    call("verify_cold_reproduction.py", "--config", config)


def main() -> None:
    parser = argparse.ArgumentParser(description="Stage39 reproducible cold-start orchestrator")
    parser.add_argument("command", choices=("preflight", "vivado", "models", "rtl", "assemble", "verify", "pc-reference", "all"))
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--profile", choices=("full", "smoke"), default="full")
    parser.add_argument("--scene", choices=("s23", "Meetingroom", "Office1", "Office2", "all"), default="all")
    args = parser.parse_args(); config = args.config.absolute()
    if args.command in ("preflight", "all"): preflight(config)
    if args.command in ("vivado", "all"): vivado(config)
    if args.command in ("models", "all"): models(config, args.profile, args.scene)
    if args.command == "pc-reference": call("measure_original_pc_reference.py", "--scene", args.scene, "--config", config)
    if args.command in ("rtl", "all"): rtl(config, args.profile)
    if args.command in ("assemble", "all"):
        if args.profile != "full": raise RuntimeError("final packages can only be assembled from the full profile")
        assemble(config)
    if args.command in ("verify", "all"): verify(config)


if __name__ == "__main__": main()
