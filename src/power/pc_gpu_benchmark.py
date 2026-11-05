#!/usr/bin/env python3
"""Measure Stage39 NeRF2 FP32 inference time and NVIDIA GPU power.

Each per-map inference interval contains CPU->GPU input transfer, the NeRF2
renderer, GPU synchronization, and GPU->CPU result transfer. Model loading,
input construction, SSIM, plotting, and file output are outside each per-map
interval. A secondary continuous-sweep interval also contains the short host
input-construction gaps between maps.

Power comes from nvidia-smi. It is the NVIDIA driver GPU power domain, not the
wall-plug power of the complete laptop.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import os
import platform
import shutil
import statistics
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import torch
import yaml
from scipy.spatial.transform import Rotation


SCENES = ("s23", "Meetingroom", "Office1", "Office2")
SCENE_EXPERIMENTS = {
    "s23": "spectrum-exp1",
    "Meetingroom": "spectrum-meetingroom",
    "Office1": "spectrum-Office1",
    "Office2": "spectrum-Office2",
}
ROWS, COLS, SAMPLES = 90, 360, 64
RAYS_PER_MAP = ROWS * COLS


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def default_package_root() -> Path | None:
    env = os.environ.get("STAGE39_PC_ROOT")
    candidates = [Path(env)] if env else []
    candidates += [
        Path(__file__).resolve().parents[2] / "outputs" / "N2-NeRF2-Stage39-PC-App",
        Path.cwd() / "N2-NeRF2-Stage39-PC-App",
        Path.cwd().parent / "N2-NeRF2-Stage39-PC-App",
    ]
    for candidate in candidates:
        if (candidate / "scene_registry.json").is_file():
            return candidate.resolve()
    return None


def numeric(value: str) -> float | None:
    value = value.strip()
    if value in {"", "N/A", "[N/A]", "Not Supported"}:
        return None
    try:
        return float(value)
    except ValueError:
        return None


class NvidiaSmiSampler:
    FIELDS = (
        "driver_timestamp",
        "power_instant_w",
        "power_average_w",
        "utilization_gpu_percent",
        "temperature_c",
        "sm_clock_mhz",
    )

    def __init__(self, gpu: int, interval_ms: int):
        self.gpu = int(gpu)
        self.interval_ms = int(interval_ms)
        self.samples: list[dict] = []
        self.process: subprocess.Popen | None = None
        self.thread: threading.Thread | None = None
        self.stderr = ""

    def start(self) -> None:
        query = (
            "timestamp,power.draw.instant,power.draw.average,utilization.gpu,"
            "temperature.gpu,clocks.current.sm"
        )
        command = [
            "nvidia-smi",
            "-i",
            str(self.gpu),
            f"--query-gpu={query}",
            "--format=csv,noheader,nounits",
            "-lms",
            str(self.interval_ms),
        ]
        self.process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        self.thread = threading.Thread(target=self._read, daemon=True)
        self.thread.start()
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and not self.samples:
            if self.process.poll() is not None:
                break
            time.sleep(0.05)
        if not self.samples:
            self.stop()
            raise RuntimeError("nvidia-smi did not produce a power sample: " + self.stderr)

    def _read(self) -> None:
        assert self.process is not None and self.process.stdout is not None
        for line in self.process.stdout:
            received = time.perf_counter()
            values = [part.strip() for part in line.strip().split(",")]
            if len(values) != len(self.FIELDS):
                continue
            self.samples.append(
                {
                    "host_monotonic_s": received,
                    "host_utc": utc_now(),
                    "driver_timestamp": values[0],
                    "power_instant_w": numeric(values[1]),
                    "power_average_w": numeric(values[2]),
                    "utilization_gpu_percent": numeric(values[3]),
                    "temperature_c": numeric(values[4]),
                    "sm_clock_mhz": numeric(values[5]),
                }
            )

    def stop(self) -> None:
        if self.process is None:
            return
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=3)
        if self.thread is not None:
            self.thread.join(timeout=2)
        if self.process.stderr is not None:
            self.stderr = self.process.stderr.read().strip()


def gpu_metadata(gpu: int) -> dict:
    query = "index,name,driver_version,memory.total,power.limit"
    completed = subprocess.run(
        [
            "nvidia-smi",
            "-i",
            str(gpu),
            f"--query-gpu={query}",
            "--format=csv,noheader,nounits",
        ],
        check=True,
        text=True,
        capture_output=True,
    )
    values = [part.strip() for part in completed.stdout.strip().split(",")]
    if len(values) != 5:
        raise RuntimeError("unexpected nvidia-smi metadata: " + completed.stdout)
    return {
        "index": int(values[0]),
        "name": values[1],
        "driver_version": values[2],
        "memory_total_mib": numeric(values[3]),
        "power_limit_w": numeric(values[4]),
    }


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def prepare_nerf_imports(source_root: Path, model_name: str, renderer_name: str):
    model_path = source_root / model_name
    renderer_path = source_root / renderer_name
    for path in (model_path, renderer_path):
        if not path.is_file():
            raise FileNotFoundError(f"NeRF2 source is missing: {path}")
    sys.path.insert(0, str(source_root))
    suffix = f"{os.getpid()}_{time.time_ns()}"
    model_module = _load_module("stage39_pc_power_model_" + suffix, model_path)
    renderer_module = _load_module("stage39_pc_power_renderer_" + suffix, renderer_path)
    if not hasattr(model_module, "NeRF2") or not hasattr(renderer_module, "renderer_dict"):
        raise RuntimeError("selected NeRF2 model/renderer files do not expose the required API")
    return model_path, renderer_path, model_module.NeRF2, renderer_module.renderer_dict


def load_scene(
    package_root: Path,
    scene: str,
    device: torch.device,
    reference_repo_root: Path | None = None,
):
    scene_root = package_root / "scenes" / scene
    if reference_repo_root is None:
        source_root = package_root / "software" / "third_party" / "nerf2"
        config_path = scene_root / "pc_app" / "model" / "config.yml"
        checkpoint_path = scene_root / "pc_app" / "model" / "checkpoint.tar"
        metadata_root = scene_root / "source"
        model_name, renderer_name = "model.py", "renderer.py"
        authority = "Stage39 packaged factorized FP32 checkpoint"
    else:
        source_root = reference_repo_root
        experiment = SCENE_EXPERIMENTS[scene]
        experiment_root = reference_repo_root / "logs" / "RFID" / scene / experiment
        config_candidates = sorted(experiment_root.glob("*.yml")) + sorted(experiment_root.glob("*.yaml"))
        checkpoints = sorted((experiment_root / "ckpts").glob("*.tar"))
        if not config_candidates or not checkpoints:
            raise FileNotFoundError(f"missing config/checkpoint under {experiment_root}")
        config_path = config_candidates[0]
        checkpoint_path = checkpoints[-1]
        metadata_root = reference_repo_root / "data" / "RFID" / scene
        # This user's working tree keeps the unmodified NeRF2 forward graph in
        # model_modify0.py/renderer_modify0.py; model.py was overwritten with
        # renderer code. Prefer the actual files imported by nerf2_runner.py.
        model_name = "model_modify0.py" if (source_root / "model_modify0.py").is_file() else "model.py"
        renderer_name = "renderer_modify0.py" if (source_root / "renderer_modify0.py").is_file() else "renderer.py"
        authority = "user-authoritative latest original-training checkpoint"
    model_path, renderer_path, NeRF2, renderer_dict = prepare_nerf_imports(
        source_root, model_name, renderer_name
    )
    with config_path.open("r", encoding="utf-8") as stream:
        config = yaml.safe_load(stream)
    if int(config["render"]["n_samples"]) != SAMPLES:
        raise RuntimeError(f"{scene}: expected 64 samples/ray")
    network = NeRF2(**config["networks"]).to(device)
    checkpoint = torch.load(checkpoint_path, map_location=device)
    network.load_state_dict(checkpoint["nerf2_network_state_dict"])
    network.eval()
    renderer_class = renderer_dict[config["render"]["mode"]]
    renderer = renderer_class(networks_fn=network, **config["render"])
    return {
        "source_root": source_root,
        "scene_root": scene_root,
        "metadata_root": metadata_root,
        "config": config,
        "config_path": config_path,
        "checkpoint_path": checkpoint_path,
        "model_path": model_path,
        "renderer_path": renderer_path,
        "authority": authority,
        "network": network,
        "renderer": renderer,
    }


def create_input_context(metadata_root: Path) -> dict:
    with (metadata_root / "gateway_info.yml").open("r", encoding="utf-8") as stream:
        gateway = yaml.safe_load(stream)["gateway1"]
    test_indices = np.loadtxt(metadata_root / "test_index.txt", dtype=str, ndmin=1)
    tx_positions = np.loadtxt(metadata_root / "tx_pos.csv", delimiter=",", skiprows=1, ndmin=2)

    azimuth = torch.linspace(1, COLS, COLS, dtype=torch.float32) / 180.0 * np.pi
    elevation = torch.linspace(1, ROWS, ROWS, dtype=torch.float32) / 180.0 * np.pi
    azimuth = torch.tile(azimuth, (ROWS,))
    elevation = torch.repeat_interleave(elevation, COLS)
    directions = torch.stack(
        (
            torch.cos(elevation) * torch.cos(azimuth),
            torch.cos(elevation) * torch.sin(azimuth),
            torch.sin(elevation),
        ),
        dim=0,
    )
    rotation = torch.from_numpy(
        Rotation.from_quat(gateway["orientation"]).as_matrix()
    ).float()
    directions = (rotation @ directions).T.contiguous()
    origin = torch.tensor(gateway["position"], dtype=torch.float32).repeat(RAYS_PER_MAP, 1)

    return {
        "test_indices": [str(value) for value in test_indices.tolist()],
        "tx_positions": tx_positions,
        "ray_origin": origin,
        "ray_directions": directions,
    }


def create_map_input(context: dict, spectrum_id: str) -> torch.Tensor:
    row = int(str(spectrum_id)) - 1
    tx_positions = context["tx_positions"]
    if row < 0 or row >= len(tx_positions):
        raise IndexError(f"test spectrum ID {spectrum_id} has no transmitter row")
    tx = torch.tensor(tx_positions[row], dtype=torch.float32).repeat(RAYS_PER_MAP, 1)
    return torch.cat(
        (context["ray_origin"], context["ray_directions"], tx), dim=1
    ).contiguous()


def run_maps(
    renderer,
    context: dict,
    spectrum_ids: list[str],
    batch_size: int,
    device: torch.device,
    progress_label: str | None = None,
) -> dict:
    map_results: list[dict] = []
    torch.cuda.synchronize(device)
    phase_started = time.perf_counter()
    progress_step = max(1, len(spectrum_ids) // 20)
    with torch.no_grad():
        for map_index, spectrum_id in enumerate(spectrum_ids):
            # Input construction is deliberately outside the per-map inference
            # interval and only one 90x360 tensor is held in RAM at a time.
            map_input = create_map_input(context, spectrum_id)
            events: list[tuple[torch.cuda.Event, torch.cuda.Event]] = []
            output_chunks: list[torch.Tensor] = []
            map_started = time.perf_counter()
            for offset in range(0, RAYS_PER_MAP, batch_size):
                batch = map_input[offset : offset + batch_size].to(device)
                rays_o, rays_d, tx_o = batch[:, :3], batch[:, 3:6], batch[:, 6:9]
                begin = torch.cuda.Event(enable_timing=True)
                end = torch.cuda.Event(enable_timing=True)
                begin.record()
                prediction = renderer.render_ss(tx_o, rays_o, rays_d)
                end.record()
                copied = prediction.detach().cpu()
                output_chunks.append(copied)
                events.append((begin, end))
            torch.cuda.synchronize(device)
            map_ended = time.perf_counter()
            output = torch.cat(output_chunks).numpy().astype("<f4", copy=False)
            map_results.append(
                {
                    "test_ordinal": map_index,
                    "spectrum_id": spectrum_id,
                    "start_monotonic_s": map_started,
                    "end_monotonic_s": map_ended,
                    "duration_s": map_ended - map_started,
                    "kernel_duration_s": sum(
                        begin.elapsed_time(end) for begin, end in events
                    )
                    / 1000.0,
                    "output_sha256": hashlib.sha256(output.tobytes()).hexdigest(),
                    "output_min": float(output.min()),
                    "output_max": float(output.max()),
                }
            )
            completed = map_index + 1
            if progress_label and (completed == len(spectrum_ids) or completed % progress_step == 0):
                print(
                    f"  {progress_label}: {completed}/{len(spectrum_ids)} maps "
                    f"({100.0 * completed / len(spectrum_ids):.1f}%)",
                    flush=True,
                )
    torch.cuda.synchronize(device)
    phase_ended = time.perf_counter()
    return {
        "phase_start_monotonic_s": phase_started,
        "phase_end_monotonic_s": phase_ended,
        "duration_s": sum(row["duration_s"] for row in map_results),
        "kernel_duration_s": sum(row["kernel_duration_s"] for row in map_results),
        "map_results": map_results,
    }


def phase_summary(
    samples: list[dict], start: float, end: float, minimum_samples: int = 3
) -> dict:
    selected = [
        row
        for row in samples
        if start <= row["host_monotonic_s"] <= end and row["power_instant_w"] is not None
    ]
    if len(selected) < minimum_samples:
        raise RuntimeError(
            f"only {len(selected)} NVIDIA power samples in a {end - start:.3f}s phase; "
            f"need {minimum_samples}"
        )
    instant = [row["power_instant_w"] for row in selected]
    average = [row["power_average_w"] for row in selected if row["power_average_w"] is not None]
    utilization = [row["utilization_gpu_percent"] for row in selected if row["utilization_gpu_percent"] is not None]
    temperature = [row["temperature_c"] for row in selected if row["temperature_c"] is not None]
    duration = end - start
    mean_power = statistics.fmean(instant)
    return {
        "duration_s": duration,
        "sample_count": len(selected),
        "instant_power_mean_w": mean_power,
        "instant_power_median_w": statistics.median(instant),
        "instant_power_min_w": min(instant),
        "instant_power_max_w": max(instant),
        "driver_average_power_mean_w": statistics.fmean(average) if average else None,
        "gpu_utilization_mean_percent": statistics.fmean(utilization) if utilization else None,
        "temperature_start_c": temperature[0] if temperature else None,
        "temperature_end_c": temperature[-1] if temperature else None,
        "estimated_energy_j": mean_power * duration,
    }


def save_raw_samples(path: Path, samples: list[dict], phases: list[dict]) -> None:
    fieldnames = [
        "host_utc",
        "host_monotonic_s",
        "driver_timestamp",
        "phase",
        "power_instant_w",
        "power_average_w",
        "utilization_gpu_percent",
        "temperature_c",
        "sm_clock_mhz",
    ]
    with path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        for sample in samples:
            phase_name = "unassigned"
            for phase in phases:
                if phase["start"] <= sample["host_monotonic_s"] <= phase["end"]:
                    phase_name = phase["name"]
                    break
            row = dict(sample)
            row["phase"] = phase_name
            writer.writerow({key: row.get(key) for key in fieldnames})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package-root", type=Path, default=default_package_root())
    parser.add_argument(
        "--reference-repo-root",
        type=Path,
        help="load each scene's lexically latest .tar checkpoint and its config/data from this NeRF2 tree",
    )
    parser.add_argument("--scene", choices=SCENES, required=True)
    parser.add_argument("--maps", type=int, default=50)
    parser.add_argument(
        "--all-test-maps",
        action="store_true",
        help="measure every spectrum ID listed in the scene's test_index.txt",
    )
    parser.add_argument("--warmup-maps", type=int, default=5)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--idle-seconds", type=float, default=30.0)
    parser.add_argument("--settle-seconds", type=float, default=15.0)
    parser.add_argument("--between-seconds", type=float, default=15.0)
    parser.add_argument("--sample-ms", type=int, default=50)
    parser.add_argument("--gpu", type=int, default=0)
    parser.add_argument("--batch-size", type=int, default=None)
    parser.add_argument("--start-map", type=int, default=0)
    parser.add_argument("--output-root", type=Path, default=Path("results") / "pc_gpu")
    args = parser.parse_args()

    if args.package_root is None:
        parser.error("Stage39 PC package not found; pass --package-root")
    package_root = args.package_root.resolve()
    reference_repo_root = args.reference_repo_root.resolve() if args.reference_repo_root else None
    if reference_repo_root is not None and not (reference_repo_root / "logs" / "RFID").is_dir():
        parser.error(f"invalid reference repository: {reference_repo_root}")
    if args.maps < 1 or args.warmup_maps < 1 or args.repeats < 1:
        parser.error("maps, warmup-maps, and repeats must be positive")
    if args.idle_seconds < 10:
        parser.error("idle-seconds must be at least 10 seconds")
    if args.sample_ms < 50 or args.sample_ms > 1000:
        parser.error("sample-ms must be between 50 and 1000")
    if not shutil.which("nvidia-smi"):
        parser.error("nvidia-smi was not found in PATH")
    if not torch.cuda.is_available():
        parser.error("PyTorch CUDA is unavailable in this Python environment")

    torch.cuda.set_device(args.gpu)
    device = torch.device(f"cuda:{args.gpu}")
    metadata = gpu_metadata(args.gpu)
    print(f"GPU: {metadata['name']} | driver {metadata['driver_version']}", flush=True)
    print(f"Loading Stage39 {args.scene} FP32 reference model...", flush=True)
    scene_data = load_scene(package_root, args.scene, device, reference_repo_root)
    print(
        f"Checkpoint authority: {scene_data['authority']} | {scene_data['checkpoint_path'].name}",
        flush=True,
    )
    batch_size = args.batch_size or int(scene_data["config"]["train"]["batch_size"])
    input_context = create_input_context(scene_data["metadata_root"])
    all_test_ids = input_context["test_indices"]
    if args.start_map < 0 or args.start_map >= len(all_test_ids):
        parser.error(f"start-map must be between 0 and {len(all_test_ids) - 1}")
    map_count = len(all_test_ids) - args.start_map if args.all_test_maps else args.maps
    spectrum_ids = all_test_ids[args.start_map : args.start_map + map_count]
    if len(spectrum_ids) != map_count:
        parser.error(
            f"requested maps {args.start_map}..{args.start_map + map_count - 1}, "
            f"but only {len(all_test_ids)} test maps exist"
        )
    warmup_ids = all_test_ids[: min(args.warmup_maps, len(all_test_ids))]
    exact_full_test_set = args.start_map == 0 and spectrum_ids == all_test_ids
    print(
        f"Selected {map_count}/{len(all_test_ids)} complete 90x360 test maps; "
        "one input map will be built at a time outside its measured interval.",
        flush=True,
    )

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output = args.output_root.resolve() / f"{stamp}_{args.scene}"
    output.mkdir(parents=True, exist_ok=False)
    sampler = NvidiaSmiSampler(args.gpu, args.sample_ms)
    phases: list[dict] = []
    repeat_results: list[dict] = []
    try:
        sampler.start()
        print(f"Warm-up: {len(warmup_ids)} maps (excluded)...", flush=True)
        warmup_start = time.perf_counter()
        run_maps(scene_data["renderer"], input_context, warmup_ids, batch_size, device)
        warmup_end = time.perf_counter()
        phases.append({"name": "warmup", "start": warmup_start, "end": warmup_end})

        print(f"Settling for {args.settle_seconds:.0f}s...", flush=True)
        time.sleep(args.settle_seconds)
        print(f"GPU idle baseline: {args.idle_seconds:.0f}s...", flush=True)
        idle_start = time.perf_counter()
        time.sleep(args.idle_seconds)
        idle_end = time.perf_counter()
        phases.append({"name": "idle", "start": idle_start, "end": idle_end})

        for repeat in range(1, args.repeats + 1):
            if repeat > 1:
                print(f"Inter-repeat idle: {args.between_seconds:.0f}s...", flush=True)
                cool_start = time.perf_counter()
                time.sleep(args.between_seconds)
                cool_end = time.perf_counter()
                phases.append({"name": f"between_{repeat - 1}", "start": cool_start, "end": cool_end})
            print(f"Active repeat {repeat}/{args.repeats}: {map_count} maps...", flush=True)
            timing = run_maps(
                scene_data["renderer"],
                input_context,
                spectrum_ids,
                batch_size,
                device,
                progress_label=f"repeat {repeat}",
            )
            active_start = timing["phase_start_monotonic_s"]
            active_end = timing["phase_end_monotonic_s"]
            name = f"active_{repeat}"
            phases.append({"name": name, "start": active_start, "end": active_end})
            timing.update({"repeat": repeat, "phase": name})
            repeat_results.append(timing)
            with (output / f"per_map_timing_repeat_{repeat}.partial.jsonl").open(
                "w", encoding="utf-8"
            ) as stream:
                for row in timing["map_results"]:
                    stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
            print(
                f"  {timing['duration_s'] / map_count:.6f} s/map, "
                f"kernel {timing['kernel_duration_s'] / map_count:.6f} s/map",
                flush=True,
            )
    finally:
        sampler.stop()

    idle = phase_summary(sampler.samples, idle_start, idle_end)
    per_map_rows: list[dict] = []
    for result, phase in zip(repeat_results, [p for p in phases if p["name"].startswith("active_")]):
        power = phase_summary(sampler.samples, phase["start"], phase["end"])
        active_energy = power["estimated_energy_j"]
        dynamic_energy = max(
            0.0,
            (power["instant_power_mean_w"] - idle["instant_power_mean_w"]) * power["duration_s"],
        )
        result.update(
            {
                "maps": map_count,
                "seconds_per_map": result["duration_s"] / map_count,
                "kernel_seconds_per_map": result["kernel_duration_s"] / map_count,
                "power": power,
                "total_gpu_energy_j": active_energy,
                "gpu_energy_per_map_j": active_energy / map_count,
                "dynamic_gpu_energy_j": dynamic_energy,
                "dynamic_gpu_energy_per_map_j": dynamic_energy / map_count,
            }
        )
        for map_result in result["map_results"]:
            map_power = phase_summary(
                sampler.samples,
                map_result["start_monotonic_s"],
                map_result["end_monotonic_s"],
                minimum_samples=1,
            )
            map_result["power"] = map_power
            map_result["total_gpu_energy_j"] = map_power["estimated_energy_j"]
            map_result["dynamic_gpu_energy_j"] = max(
                0.0,
                (map_power["instant_power_mean_w"] - idle["instant_power_mean_w"])
                * map_result["duration_s"],
            )
            per_map_rows.append(
                {
                    "scene": args.scene,
                    "repeat": result["repeat"],
                    **map_result,
                }
            )
        map_interval_total_energy = sum(
            row["total_gpu_energy_j"] for row in result["map_results"]
        )
        map_interval_dynamic_energy = sum(
            row["dynamic_gpu_energy_j"] for row in result["map_results"]
        )
        map_interval_duration = sum(row["duration_s"] for row in result["map_results"])
        result.update(
            {
                "continuous_sweep_power": result["power"],
                "continuous_sweep_total_gpu_energy_j": result["total_gpu_energy_j"],
                "continuous_sweep_gpu_energy_per_map_j": result["gpu_energy_per_map_j"],
                "continuous_sweep_dynamic_gpu_energy_j": result["dynamic_gpu_energy_j"],
                "continuous_sweep_dynamic_gpu_energy_per_map_j": result[
                    "dynamic_gpu_energy_per_map_j"
                ],
                "map_interval_active_power_w": map_interval_total_energy
                / map_interval_duration,
                "total_gpu_energy_j": map_interval_total_energy,
                "gpu_energy_per_map_j": map_interval_total_energy / map_count,
                "dynamic_gpu_energy_j": map_interval_dynamic_energy,
                "dynamic_gpu_energy_per_map_j": map_interval_dynamic_energy / map_count,
            }
        )

    coverage_by_repeat = [
        [row["spectrum_id"] for row in result["map_results"]]
        for result in repeat_results
    ]
    hashes_by_spectrum: dict[str, set[str]] = {spectrum_id: set() for spectrum_id in spectrum_ids}
    for row in per_map_rows:
        hashes_by_spectrum[row["spectrum_id"]].add(row["output_sha256"])
    exact_coverage_every_repeat = all(ids == spectrum_ids for ids in coverage_by_repeat)
    all_maps_have_power_samples = all(row["power"]["sample_count"] >= 1 for row in per_map_rows)
    deterministic_outputs = all(len(values) == 1 for values in hashes_by_spectrum.values())

    summary = {
        "schema": "stage39-pc-gpu-power-benchmark-v2",
        "created_utc": utc_now(),
        "scene": args.scene,
        "workload": {
            "maps_per_repeat": map_count,
            "total_test_maps": len(all_test_ids),
            "exact_full_test_set": exact_full_test_set,
            "resolution": [ROWS, COLS],
            "rays_per_map": RAYS_PER_MAP,
            "samples_per_ray": SAMPLES,
            "batch_size": batch_size,
            "spectrum_ids": spectrum_ids,
            "primary_per_map_interval_included": [
                "CPU-to-GPU transfer",
                "NeRF2 FP32 render_ss",
                "GPU synchronization",
                "GPU-to-CPU output transfer",
            ],
            "secondary_continuous_sweep_additionally_includes": [
                "short host input-construction gaps between maps"
            ],
            "excluded_from_primary_per_map_interval": [
                "host input construction",
                "model loading",
                "SSIM",
                "plotting",
                "PNG/file output",
            ],
            "excluded_from_both_primary_and_secondary": [
                "model loading",
                "SSIM",
                "plotting",
                "PNG/file output",
            ],
        },
        "power_boundary": "NVIDIA driver-reported GPU power domain; excludes CPU, RAM, display, PSU loss, and the rest of the laptop",
        "instrument": {
            "source": "nvidia-smi",
            "sample_interval_ms": args.sample_ms,
            "primary_power_field": "power.draw.instant",
            "secondary_power_field": "power.draw.average",
        },
        "gpu": metadata,
        "host": {
            "platform": platform.platform(),
            "python": sys.version,
            "torch": torch.__version__,
            "cuda_runtime": torch.version.cuda,
        },
        "provenance": {
            "package_root": str(package_root),
            "reference_repo_root": str(reference_repo_root) if reference_repo_root else None,
            "checkpoint_authority": scene_data["authority"],
            "model_source": str(scene_data["model_path"]),
            "model_source_sha256": sha256(scene_data["model_path"]),
            "renderer_source": str(scene_data["renderer_path"]),
            "renderer_source_sha256": sha256(scene_data["renderer_path"]),
            "config": str(scene_data["config_path"]),
            "config_sha256": sha256(scene_data["config_path"]),
            "checkpoint": str(scene_data["checkpoint_path"]),
            "checkpoint_sha256": sha256(scene_data["checkpoint_path"]),
        },
        "idle": idle,
        "repeats": repeat_results,
        "aggregate": {
            "median_seconds_per_map": statistics.median(r["seconds_per_map"] for r in repeat_results),
            "median_kernel_seconds_per_map": statistics.median(r["kernel_seconds_per_map"] for r in repeat_results),
            "median_active_power_w": statistics.median(
                r["map_interval_active_power_w"] for r in repeat_results
            ),
            "median_gpu_energy_per_map_j": statistics.median(r["gpu_energy_per_map_j"] for r in repeat_results),
            "median_dynamic_gpu_energy_per_map_j": statistics.median(r["dynamic_gpu_energy_per_map_j"] for r in repeat_results),
            "median_continuous_sweep_active_power_w": statistics.median(
                r["continuous_sweep_power"]["instant_power_mean_w"] for r in repeat_results
            ),
            "median_continuous_sweep_gpu_energy_per_map_j": statistics.median(
                r["continuous_sweep_gpu_energy_per_map_j"] for r in repeat_results
            ),
            "median_continuous_sweep_dynamic_gpu_energy_per_map_j": statistics.median(
                r["continuous_sweep_dynamic_gpu_energy_per_map_j"]
                for r in repeat_results
            ),
        },
        "validity": {
            "exact_test_index_order_every_repeat": exact_coverage_every_repeat,
            "every_selected_test_map_measured_every_repeat": exact_coverage_every_repeat
            and all(len(ids) == map_count for ids in coverage_by_repeat),
            "full_test_set_measured": exact_full_test_set and exact_coverage_every_repeat,
            "all_maps_have_power_samples": all_maps_have_power_samples,
            "deterministic_output_hashes_across_repeats": deterministic_outputs,
            "minimum_power_samples_per_map": min(
                row["power"]["sample_count"] for row in per_map_rows
            ),
            "minimum_active_samples_per_repeat": min(r["power"]["sample_count"] for r in repeat_results),
        },
    }

    save_raw_samples(output / "nvidia_smi_raw.csv", sampler.samples, phases)
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    with (output / "runs.csv").open("w", newline="", encoding="utf-8-sig") as stream:
        fields = [
            "scene",
            "repeat",
            "maps",
            "duration_s",
            "seconds_per_map",
            "kernel_seconds_per_map",
            "idle_power_w",
            "active_power_w",
            "continuous_sweep_active_power_w",
            "gpu_energy_per_map_j",
            "continuous_sweep_gpu_energy_per_map_j",
            "dynamic_gpu_energy_per_map_j",
            "power_samples",
            "full_test_set",
        ]
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for result in repeat_results:
            writer.writerow(
                {
                    "scene": args.scene,
                    "repeat": result["repeat"],
                    "maps": map_count,
                    "duration_s": result["duration_s"],
                    "seconds_per_map": result["seconds_per_map"],
                    "kernel_seconds_per_map": result["kernel_seconds_per_map"],
                    "idle_power_w": idle["instant_power_mean_w"],
                    "active_power_w": result["map_interval_active_power_w"],
                    "continuous_sweep_active_power_w": result["continuous_sweep_power"][
                        "instant_power_mean_w"
                    ],
                    "gpu_energy_per_map_j": result["gpu_energy_per_map_j"],
                    "continuous_sweep_gpu_energy_per_map_j": result[
                        "continuous_sweep_gpu_energy_per_map_j"
                    ],
                    "dynamic_gpu_energy_per_map_j": result["dynamic_gpu_energy_per_map_j"],
                    "power_samples": result["power"]["sample_count"],
                    "full_test_set": exact_full_test_set,
                }
            )
    per_map_fields = [
        "scene",
        "repeat",
        "test_ordinal",
        "spectrum_id",
        "duration_s",
        "kernel_duration_s",
        "power_samples",
        "idle_power_w",
        "active_power_w",
        "total_gpu_energy_j",
        "dynamic_gpu_energy_j",
        "gpu_utilization_mean_percent",
        "temperature_start_c",
        "temperature_end_c",
        "output_sha256",
        "output_min",
        "output_max",
    ]
    with (output / "per_map_runs.csv").open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=per_map_fields)
        writer.writeheader()
        for row in per_map_rows:
            power = row["power"]
            writer.writerow(
                {
                    "scene": row["scene"],
                    "repeat": row["repeat"],
                    "test_ordinal": row["test_ordinal"],
                    "spectrum_id": row["spectrum_id"],
                    "duration_s": row["duration_s"],
                    "kernel_duration_s": row["kernel_duration_s"],
                    "power_samples": power["sample_count"],
                    "idle_power_w": idle["instant_power_mean_w"],
                    "active_power_w": power["instant_power_mean_w"],
                    "total_gpu_energy_j": row["total_gpu_energy_j"],
                    "dynamic_gpu_energy_j": row["dynamic_gpu_energy_j"],
                    "gpu_utilization_mean_percent": power["gpu_utilization_mean_percent"],
                    "temperature_start_c": power["temperature_start_c"],
                    "temperature_end_c": power["temperature_end_c"],
                    "output_sha256": row["output_sha256"],
                    "output_min": row["output_min"],
                    "output_max": row["output_max"],
                }
            )
    for partial in output.glob("per_map_timing_repeat_*.partial.jsonl"):
        partial.unlink()
    print("PASS: PC GPU benchmark completed", flush=True)
    print(f"Result directory: {output}", flush=True)
    print(json.dumps(summary["aggregate"], ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
