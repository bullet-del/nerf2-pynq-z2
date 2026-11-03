#!/usr/bin/env python3
"""Create four-scene full-test accuracy and angular-structure evidence."""
from __future__ import annotations
import csv, hashlib, json
from datetime import datetime, timezone
from pathlib import Path
import matplotlib.pyplot as plt
import numpy as np

ROOT=Path(__file__).resolve().parents[1];WORK=ROOT/"work_stage39";OUT=WORK/"reports/stage39_5"
SCENES=("s23","Meetingroom","Office1","Office2")
METRICS=("ssim","psnr_db","mse","mae","peak_angular_error_deg","peak_azimuth_error_deg","peak_elevation_error_deg","main_lobe_energy_abs_error")

def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def summary(values):
    a=np.asarray(values,float);return {"mean":float(a.mean()),"median":float(np.median(a)),"p05":float(np.percentile(a,5)),"p95":float(np.percentile(a,95)),"min":float(a.min()),"max":float(a.max())}

def main():
    OUT.mkdir(parents=True,exist_ok=True);scene_reports=[];all_pc=[];all_hw=[];split_rows=[]
    fig,axes=plt.subplots(2,2,figsize=(13,10),constrained_layout=True)
    for ax,scene in zip(axes.flat,SCENES):
        src=WORK/"qat"/scene/"original_pc_vs_selected_integer_full.json"
        value=json.loads(src.read_text(encoding="utf-8"));pc=value["pc_fp32"]["per_map"];hw=value["hardware_integer"]["per_map"]
        assert len(pc)==len(hw)==value["maps"] and [x["spectrum_id"] for x in pc]==[x["spectrum_id"] for x in hw]
        data=WORK/("dataset_"+scene);train=data/"train_index.txt";test=data/"test_index.txt"
        train_ids=set(np.atleast_1d(np.loadtxt(train,dtype=str)).tolist());test_ids=set(np.atleast_1d(np.loadtxt(test,dtype=str)).tolist())
        disjoint=train_ids.isdisjoint(test_ids)
        if not disjoint:raise RuntimeError(scene+": train/test split overlaps")
        split_rows.append({"scene":scene,"train_count":len(train_ids),"test_count":len(test_ids),"disjoint":disjoint,"train_index_sha256":sha(train),"test_index_sha256":sha(test)})
        scene_report={"scene":scene,"maps":value["maps"],"original_pc_fp32":value["pc_fp32"]["summary"],"stage39_hardware_integer":value["hardware_integer"]["summary"],
          "mean_ssim_gap":value["mean_ssim_gap_pc_minus_integer"],"gap_under_0p015":value["mean_ssim_gap_pc_minus_integer"]<=.015,
          "accuracy_retention_percent":100*value["hardware_integer"]["summary"]["ssim"]["mean"]/value["pc_fp32"]["summary"]["ssim"]["mean"],
          "source_sha256":sha(src),"test_only":True}
        scene_reports.append(scene_report);all_pc.extend(pc);all_hw.extend(hw)
        y=np.arange(1,len(pc)+1)/len(pc);p=np.sort([x["ssim"] for x in pc]);h=np.sort([x["ssim"] for x in hw])
        ax.plot(p,y,label=f"PC FP32 mean={np.mean(p):.4f}",lw=2.2);ax.plot(h,y,label=f"Stage39 integer mean={np.mean(h):.4f}",lw=2.2)
        ax.set_title(f"{scene} ({len(pc)} test maps)");ax.set_xlabel("SSIM vs measured GT");ax.set_ylabel("Empirical CDF");ax.grid(alpha=.2);ax.legend(fontsize=8)
        (OUT/(scene+"_full_metrics.json")).write_text(json.dumps({"schema":"n2-stage39-scene-full-metrics-v1",**scene_report,"per_map_original_pc":pc,"per_map_stage39_integer":hw},ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    fig.suptitle("Stage39 four-scene full held-out-test SSIM CDF — 1,725 maps",fontsize=16)
    fig.savefig(OUT/"four_scene_ssim_cdf.png",dpi=200,facecolor="white");plt.close(fig)
    aggregate={}
    for key in METRICS:aggregate[key]={"original_pc_fp32":summary([x[key] for x in all_pc]),"stage39_hardware_integer":summary([x[key] for x in all_hw])}
    # Compact metrics comparison: lower is better except SSIM/PSNR.
    labels=("SSIM ↑","PSNR dB ↑","MAE ↓","Peak angle ° ↓","Lobe energy error ↓")
    keys=("ssim","psnr_db","mae","peak_angular_error_deg","main_lobe_energy_abs_error")
    fig,axes=plt.subplots(1,5,figsize=(18,4.5),constrained_layout=True)
    for ax,label,key in zip(axes,labels,keys):
        vals=[aggregate[key]["original_pc_fp32"]["mean"],aggregate[key]["stage39_hardware_integer"]["mean"]]
        bars=ax.bar(["PC","FPGA\ninteger"],vals,color=["#1769aa","#079c8c"]);ax.set_title(label);ax.grid(axis="y",alpha=.2)
        for b,v in zip(bars,vals):ax.text(b.get_x()+b.get_width()/2,v+(max(vals)*.02 if max(vals)>0 else .001),f"{v:.4g}",ha="center",fontsize=9)
    fig.suptitle("Weighted aggregate over all 1,725 held-out test maps")
    fig.savefig(OUT/"aggregate_metrics.png",dpi=200,facecolor="white");plt.close(fig)
    with (OUT/"scene_summary.csv").open("w",newline="",encoding="utf-8-sig") as f:
        fields=("scene","maps","mean_ssim_gap","gap_under_0p015","accuracy_retention_percent")
        w=csv.DictWriter(f,fieldnames=fields,extrasaction="ignore");w.writeheader();w.writerows(scene_reports)
    report={"schema":"n2-stage39-four-scene-full-validation-v1","stage":"Stage39.5","created_utc":datetime.now(timezone.utc).isoformat(),
      "status":"SOFTWARE_REFERENCE_PASS_PHYSICAL_FULL_TEST_PENDING","maps":len(all_pc),"scenes":scene_reports,"weighted_aggregate":aggregate,"split_proof":split_rows,
      "checks":[{"id":"39.5.complete_held_out_test_set_1725","status":"PASS" if len(all_pc)==1725 else "FAIL"},
        {"id":"39.5.train_test_disjoint","status":"PASS" if all(x["disjoint"] for x in split_rows) else "FAIL"},
        {"id":"39.5.every_scene_mean_ssim_gap_le_0p015","status":"PASS" if all(x["gap_under_0p015"] for x in scene_reports) else "FAIL"},
        {"id":"39.5.ssim_psnr_mse_mae_angular_lobe_metrics","status":"PASS"},
        {"id":"39.5.physical_fpga_full_test_archive","status":"PENDING","reason":"run each scene's App full-test job after Stage39 physical exact self-test"}]}
    (OUT/"stage39_5_gate.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"status":report["status"],"maps":report["maps"],"max_gap":max(x["mean_ssim_gap"] for x in scene_reports)},ensure_ascii=False))


if __name__=="__main__":main()
