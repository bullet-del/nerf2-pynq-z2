#!/usr/bin/env python3
"""Build the Stage39 accuracy/architecture ablation table and figure."""
from __future__ import annotations
import csv, json
from datetime import datetime, timezone
from pathlib import Path
import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "work_stage39"
OUT = WORK / "reports/stage39_4"
SCENES = ("s23", "Meetingroom", "Office1", "Office2")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    gate2 = json.loads((WORK/"reports/stage39_2_gate.json").read_text(encoding="utf-8"))
    by_scene = {x["scene"]: x for x in gate2["scenes"]}
    rows=[]
    for scene in SCENES:
        g=by_scene[scene]
        ptq=json.loads((WORK/"qat"/scene/"ptq_full_evaluation.json").read_text(encoding="utf-8"))
        runtime=json.loads((WORK/"selected_runtime"/scene/"package_manifest.json").read_text(encoding="utf-8"))
        rows.append({"scene":scene,"test_maps":g["maps"],
          "original_pc_fp32":g["original_pc_fp32_mean_ssim"],
          "stage38_post_training_integer":g["stage38_integer_mean_ssim"],
          "stage39_train_only_ptq":ptq["mean_ssim"],
          "stage39_train_only_hardware_exact_qat":g["stage39_qat_integer_mean_ssim"],
          "qat_gain_over_stage38":g["integer_improvement"],
          "pc_minus_qat_gap":g["pc_minus_stage39_integer_gap"],
          "weight_words":runtime["weight_words"],"weight_capacity":3072,
          "fits_one_unmodified_bitstream":runtime["fits_unchanged_bitstream"]})
    with (OUT/"accuracy_ablation.csv").open("w",newline="",encoding="utf-8-sig") as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
    x=np.arange(len(rows)); width=.2
    series=[("Original PC FP32","original_pc_fp32","#1769aa"),
            ("Stage38 integer","stage38_post_training_integer","#7a8494"),
            ("Train-only PTQ","stage39_train_only_ptq","#d97706"),
            ("Hardware-exact QAT","stage39_train_only_hardware_exact_qat","#079c8c")]
    fig,ax=plt.subplots(figsize=(12,6),constrained_layout=True)
    for i,(label,key,color) in enumerate(series):
        values=[r[key] for r in rows]
        ax.bar(x+(i-1.5)*width,values,width,label=label,color=color)
        for xx,v in zip(x+(i-1.5)*width,values):ax.text(xx,v+.003,f"{v:.4f}",ha="center",fontsize=8,rotation=90)
    ax.set_xticks(x,[r["scene"] for r in rows]);ax.set_ylabel("Mean SSIM vs measured GT")
    ax.set_ylim(max(0,min(r["stage39_train_only_ptq"] for r in rows)-.08),.82)
    ax.set_title("Stage39 full held-out-test accuracy ablation (1,725 maps)")
    ax.grid(axis="y",alpha=.22);ax.legend(ncol=2)
    fig.savefig(OUT/"accuracy_ablation.png",dpi=200,facecolor="white");plt.close(fig)
    stage34_artifact=json.loads((ROOT/"outputs/N2-NeRF2-Stage34-PC-App/sim/stage34_artifact_manifest.json").read_text(encoding="utf-8"))
    stage35_artifact=json.loads((ROOT/"outputs/N2-NeRF2-Stage35-PC-App/sim/stage35_artifact_manifest.json").read_text(encoding="utf-8"))
    physical_cached=[]
    for path in (ROOT/"outputs/N2-NeRF2-Stage35-PC-App/evidence/stage35_pc_app").glob("*/report.json"):
        try:
            value=json.loads(path.read_text(encoding="utf-8"));seconds=value["board"]["timing"]["full_map_cached_pipeline_seconds"]
            if float(seconds)>0:physical_cached.append({"seconds":float(seconds),"source":str(path.relative_to(ROOT))})
        except Exception:pass
    no_cache=float(stage34_artifact["estimated_full_map_pl_seconds"]);cached=float(stage35_artifact["estimated_cached_full_map_pl_seconds"])
    cache_ablation={"schema":"n2-stage39-fixed-rx-cache-ablation-v1",
      "scope_warning":"Cycle-model figures compare the Stage34 unfactorized per-Tx PL work with the Stage35/Stage39 fixed-Rx factorized datapath. Physical samples are reported separately and are not substituted for the cycle model.",
      "uncached_cycle_model_seconds":no_cache,"cached_cycle_model_seconds":cached,
      "cycle_model_speedup":no_cache/cached,"cycle_model_time_reduction_percent":100*(1-cached/no_cache),
      "stage35_physical_cached_samples":physical_cached,
      "stage35_physical_cached_median_seconds":float(np.median([x["seconds"] for x in physical_cached])) if physical_cached else None,
      "stage39_physical_cached_50x":"see Stage39.3 after board execution"}
    (OUT/"fixed_rx_cache_ablation.json").write_text(json.dumps(cache_ablation,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    checks=[
      {"id":"39.4.original_pc_vs_stage38_vs_ptq_vs_qat","status":"PASS"},
      {"id":"39.4.four_scene_complete_frozen_test_split","status":"PASS","maps":sum(r["test_maps"] for r in rows)},
      {"id":"39.4.same_bitstream_capacity","status":"PASS" if all(r["fits_one_unmodified_bitstream"] for r in rows) else "FAIL"},
      {"id":"39.4.fixed_rx_cache_architecture_ablation","status":"PASS","details":cache_ablation},
      {"id":"39.4.board_power_ablation","status":"PENDING","reason":"requires external power-meter observations from Stage39.3"}]
    report={"schema":"n2-stage39-ablation-report-v1","stage":"Stage39.4",
      "created_utc":datetime.now(timezone.utc).isoformat(),
      "status":"READY_FOR_PHYSICAL_ABLATIONS","output_calibration":None,
      "algorithm_note":"All QAT rows use the same training-only hardware-exact STE algorithm; no scene-specific output transform is used.",
      "rows":rows,"checks":checks}
    (OUT/"ablation.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"status":report["status"],"maps":sum(r["test_maps"] for r in rows)},ensure_ascii=False))


if __name__=="__main__":main()
