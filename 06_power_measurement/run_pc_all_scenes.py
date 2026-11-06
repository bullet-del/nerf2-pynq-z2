from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


SCENES = ("s23", "Meetingroom", "Office1", "Office2")
HERE = Path(__file__).resolve().parent
KIT_ROOT = HERE.parent


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the authoritative PC GPU power benchmark for all four Stage39 scenes"
    )
    parser.add_argument("--package-root", type=Path,
                        default=KIT_ROOT / "outputs/N2-NeRF2-Stage39-PC-App")
    parser.add_argument("--reference-repo-root", type=Path,
                        default=KIT_ROOT / "data/inputs")
    parser.add_argument("--partial-maps", type=int, default=0,
                        help="0 means every test map; positive values are non-formal partial runs")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--warmup-maps", type=int, default=5)
    parser.add_argument("--idle-seconds", type=int, default=30)
    parser.add_argument("--settle-seconds", type=int, default=15)
    parser.add_argument("--between-seconds", type=int, default=15)
    parser.add_argument("--output-root", type=Path,
                        default=HERE / "results/pc_gpu_full_test_authoritative")
    args = parser.parse_args()

    package_root = args.package_root.resolve()
    reference_root = args.reference_repo_root.resolve()
    if not package_root.is_dir():
        raise FileNotFoundError(f"Stage39 PC package not found: {package_root}")
    if not reference_root.is_dir():
        raise FileNotFoundError(f"authoritative NeRF2 repository not found: {reference_root}")
    if shutil.which("nvidia-smi") is None:
        raise RuntimeError("nvidia-smi was not found in PATH")
    if args.partial_maps < 0:
        raise ValueError("--partial-maps must be zero or positive")

    benchmark = HERE / "pc_gpu_benchmark.py"
    for scene in SCENES:
        command = [
            sys.executable, str(benchmark),
            "--package-root", str(package_root),
            "--reference-repo-root", str(reference_root),
            "--scene", scene,
            "--repeats", str(args.repeats),
            "--warmup-maps", str(args.warmup_maps),
            "--idle-seconds", str(args.idle_seconds),
            "--settle-seconds", str(args.settle_seconds),
            "--between-seconds", str(args.between_seconds),
            "--output-root", str(args.output_root.resolve()),
        ]
        if args.partial_maps == 0:
            command.append("--all-test-maps")
        else:
            command.extend(("--maps", str(args.partial_maps)))
        print(f"\n========== {scene} ==========", flush=True)
        subprocess.run(command, check=True)
    print(f"PASS: all four PC scenes completed\nResults: {args.output_root.resolve()}")


if __name__ == "__main__":
    main()
