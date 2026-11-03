#!/usr/bin/env python3
"""Audit the exact four-checkpoint lineage used by the Stage39 board package."""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


SCENES = ("s23", "Meetingroom", "Office1", "Office2")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def latest_checkpoint(checkpoint: Path) -> Path:
    candidates = sorted(checkpoint.parent.glob("*.tar"))
    if not candidates:
        raise FileNotFoundError(f"no .tar checkpoints under {checkpoint.parent}")
    return candidates[-1].resolve()


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-manifest", type=Path, required=True)
    parser.add_argument("--board-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()

    baseline_path = args.baseline_manifest.resolve()
    board_root = args.board_root.resolve()
    output_root = args.output_root.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    baseline = load_json(baseline_path)
    entries = {row["scene"]: row for row in baseline["datasets"]}
    results = []

    for scene in SCENES:
        source = entries[scene]
        upstream = Path(source["upstream_checkpoint"]).resolve()
        packaged = board_root / "scenes" / scene / "pc_app" / "model" / "checkpoint.tar"
        quant_path = board_root / "scenes" / scene / "model" / "quant_manifest.json"
        qat_path = board_root / "scenes" / scene / "source" / "stage39_qat" / "checkpoint_qat.json"
        runtime_manifest_path = (
            board_root / "scenes" / scene / "source" / "stage39_qat" / "runtime_package_manifest.json"
        )
        runtime_model = board_root / "scenes" / scene / "model" / "stage37_runtime_model.npz"
        full_evaluation_path = (
            board_root / "scenes" / scene / "source" / "stage39_qat" / "full_evaluation.json"
        )

        required = [upstream, packaged, quant_path, qat_path, runtime_manifest_path, runtime_model, full_evaluation_path]
        for path in required:
            if not path.is_file():
                raise FileNotFoundError(path)

        quant = load_json(quant_path)
        qat = load_json(qat_path)
        runtime = load_json(runtime_manifest_path)
        evaluation = load_json(full_evaluation_path)
        latest = latest_checkpoint(upstream)
        actual = {
            "upstream": sha256(upstream),
            "packaged_factorized": sha256(packaged),
            "runtime_model": sha256(runtime_model),
        }
        checks = {
            "manifest_upstream_is_latest_checkpoint": upstream == latest,
            "upstream_file_matches_baseline_manifest": actual["upstream"]
            == source["upstream_checkpoint_sha256"],
            "packaged_factorized_file_matches_baseline_manifest": actual["packaged_factorized"]
            == source["packaged_factorized_checkpoint_sha256"],
            "quant_export_declares_factorized_source": quant["checkpoint_sha256"]
            == actual["packaged_factorized"],
            "qat_declares_factorized_source": qat["source_checkpoint_sha256"]
            == actual["packaged_factorized"],
            "quant_export_and_qat_agree_on_qat_checkpoint": quant["qat_checkpoint_sha256"]
            == qat["checkpoint_sha256"],
            "runtime_package_declares_qat_checkpoint": runtime["checkpoint_sha256"]
            == qat["checkpoint_sha256"],
            "runtime_model_file_matches_runtime_manifest": actual["runtime_model"]
            == runtime["runtime_model_sha256"],
            "runtime_fits_unchanged_bitstream": runtime["fits_unchanged_bitstream"] is True,
            "runtime_selected_qat": runtime["selection"] == "qat",
            "frozen_full_test_evaluation_count_matches": int(evaluation["maps"])
            == int(source["test_count"]),
            "frozen_full_test_evaluation_was_test_only": evaluation[
                "test_only_after_checkpoint_frozen"
            ]
            is True,
        }
        results.append(
            {
                "scene": scene,
                "pass": all(checks.values()),
                "test_count": int(source["test_count"]),
                "paths": {
                    "authoritative_latest_checkpoint": str(upstream),
                    "packaged_factorized_checkpoint": str(packaged),
                    "qat_manifest": str(qat_path),
                    "runtime_model": str(runtime_model),
                    "full_test_evaluation": str(full_evaluation_path),
                },
                "sha256": {
                    "authoritative_latest_checkpoint": actual["upstream"],
                    "packaged_factorized_checkpoint": actual["packaged_factorized"],
                    "qat_checkpoint": qat["checkpoint_sha256"],
                    "runtime_model": actual["runtime_model"],
                },
                "checks": checks,
            }
        )

    report = {
        "schema": "stage39-four-scene-checkpoint-lineage-audit-v1",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "pass": all(row["pass"] for row in results),
        "baseline_manifest": str(baseline_path),
        "baseline_manifest_sha256": sha256(baseline_path),
        "board_root": str(board_root),
        "scenes": results,
    }
    json_path = output_root / "stage39_checkpoint_lineage_audit.json"
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )

    lines = [
        "# Stage39 四场景 checkpoint 溯源审计",
        "",
        f"结论：**{'PASS' if report['pass'] else 'FAIL'}**。",
        "",
        "下表逐场景验证：本机训练目录最后一个 checkpoint → Stage38 因子分解 FP32 checkpoint → Stage39 QAT checkpoint → 板端运行时模型；并核对冻结后的完整测试集评估数量。",
        "",
        "| 场景 | test 图数 | 权威 checkpoint | 权威 SHA256 | 因子分解 SHA256 | QAT SHA256 | 板端模型 SHA256 | 结果 |",
        "|---|---:|---|---|---|---|---|---|",
    ]
    for row in results:
        hashes = row["sha256"]
        checkpoint_name = Path(row["paths"]["authoritative_latest_checkpoint"]).name
        lines.append(
            f"| {row['scene']} | {row['test_count']} | {checkpoint_name} | "
            f"`{hashes['authoritative_latest_checkpoint']}` | "
            f"`{hashes['packaged_factorized_checkpoint']}` | "
            f"`{hashes['qat_checkpoint']}` | `{hashes['runtime_model']}` | "
            f"{'PASS' if row['pass'] else 'FAIL'} |"
        )
    lines += [
        "",
        "`PASS` 只有在所有文件哈希、清单间引用、最后 checkpoint 选择、QAT 选择、板端模型哈希和完整冻结测试集数量全部一致时才成立。",
        "",
        f"机器可读报告：`{json_path.name}`",
    ]
    md_path = output_root / "Stage39四场景checkpoint溯源审计.md"
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"pass": report["pass"], "json": str(json_path), "markdown": str(md_path)}, ensure_ascii=False))
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
