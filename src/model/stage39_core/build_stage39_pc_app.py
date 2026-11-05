#!/usr/bin/env python3
"""Derive the independent Stage39 PC judge App from the frozen Stage38 UI."""
from __future__ import annotations
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "outputs/N2-NeRF2-Stage39-PC-App"
APP = PKG / "pc_app"


def replace_required(text: str, old: str, new: str) -> str:
    if old not in text:
        raise RuntimeError("required source fragment is missing: " + old[:100])
    return text.replace(old, new)


def main() -> None:
    source_core = APP / "stage38_scene_core.py"
    core = source_core.read_text(encoding="utf-8")
    replacements = {
        "Stage38 judge App": "Stage39 judge App",
        'APP_BUILD_ID = "stage38-raw-only-four-scene-app-v1"':
            'APP_BUILD_ID = "stage39-hardware-exact-qat-four-scene-app-v1"',
        'EXPECTED_BOARD_SERVICE = "stage38-raw-only-four-scene-service-v1"':
            'EXPECTED_BOARD_SERVICE = "stage39-hardware-exact-qat-four-scene-service-v1"',
        'STATIC = HERE.parent / "stage38_static"': 'STATIC = HERE.parent / "stage39_static"',
        'EVIDENCE = ROOT / "evidence/stage38_pc_app"': 'EVIDENCE = ROOT / "evidence/stage39_pc_app"',
        "published Stage38 specification": "published Stage39 specification",
        "Validate the three deliberately separate Stage38 HTTP ports.":
            "Validate the three deliberately separate Stage39 HTTP ports.",
        "Stage38 板端服务端口必须是 8311": "Stage39 板端服务端口必须是 8311",
        "不是 Stage38 板端服务": "不是 Stage39 板端服务",
        "Stage38 Notebook": "Stage39 Notebook",
        "Stage38 raw-only 版本": "Stage39 QAT raw-only 版本",
        "Stage38 raw-only 四场景共用版本": "Stage39 共用位流版本",
        "Stage38 raw INT8/Q15": "Stage39 QAT raw INT8/Q15",
        '"n2-stage38-pc-coordinated-run-v1-raw-only"':
            '"n2-stage39-pc-coordinated-run-v1-qat-raw-only"',
        'server_version = "N2Stage38PC/1.0"': 'server_version = "N2Stage39PC/1.0"',
        'print("[Stage38 PC] "': 'print("[Stage39 PC] "',
        '"n2-stage38-full-test-fpga-validation-v1-raw-only"':
            '"n2-stage39-full-test-fpga-validation-v1-qat-raw-only"',
        'print("Stage38 judge app:", url)': 'print("Stage39 judge app:", url)',
    }
    for old, new in replacements.items():
        core = replace_required(core, old, new)
    (APP / "stage39_scene_core.py").write_text(core, encoding="utf-8")

    server = (APP / "stage38_pc_server.py").read_text(encoding="utf-8")
    server = server.replace("Stage38", "Stage39").replace("stage38_scene_core.py", "stage39_scene_core.py")
    old_gate = "if path in ('/api/run','/api/all-test','/api/board-selftest') and not SPECS[self.scene]['hardware']['hardware_validated']:\n                return self.send_json(422,{'ok':False,'scene_id':self.scene,'error':self.scene+' 配套硬件模型尚未验证。当前 S23 固化权重不能用于该场景；不会返回伪造 FPGA 结果。'})"
    new_gate = "if path in ('/api/run','/api/all-test','/api/board-selftest'):\n                hw=SPECS[self.scene]['hardware']\n                if not (hw.get('factorization_compatible') and hw.get('runtime_model_sha256')):\n                    return self.send_json(422,{'ok':False,'scene_id':self.scene,'error':self.scene+' 缺少可装载的 Stage39 运行时模型。'})"
    server = replace_required(server, old_gate, new_gate)
    (APP / "stage39_pc_server.py").write_text(server, encoding="utf-8")

    static_src = APP / "stage38_static"
    static_dst = APP / "stage39_static"
    static_dst.mkdir(exist_ok=True)
    for item in static_src.iterdir():
        if item.is_file():
            (static_dst / item.name).write_bytes(item.read_bytes())
    html = (static_dst / "index.html").read_text(encoding="utf-8")
    html = html.replace("Stage38", "Stage39")
    html = html.replace("直接使用 FPGA 原始 Q15 输出", "使用训练集专用硬件精确 QAT 权重与 FPGA 原始 Q15 输出")
    html = html.replace("Stage39 单一 95 MHz 通用位流", "Stage39 硬件精确 QAT · 单一 95 MHz 通用位流")
    (static_dst / "index.html").write_text(html, encoding="utf-8")

    js_path = static_dst / "app.js"
    js = js_path.read_text(encoding="utf-8")
    js = js.replace("Stage38", "Stage39")
    js = replace_required(js,
        "let s=$(statusId);s.textContent='正在执行位流哈希、Overlay、固定Rx缓存和正式90×360硬件推理自检…';let j=await api('/api/board-selftest',{board_url:$(id).value});s.innerHTML=`<span class=\"pass\">PYNQ 硬件自检 PASS</span>　Overlay ${number(j.overlay_seconds,3)} s　完整图 ${number(j.full_map_seconds,3)} s　${j.fpga_hardware_rays.toLocaleString()}条射线　512条分布式Q15逐位一致`",
        "let s=$(statusId);s.textContent='正在执行位流哈希、运行时权重装载和完整90×360物理逐位自检…';let j=await api('/api/board-selftest',{board_url:$(id).value});let exact=j.physical_full_map_vs_portable_integer_bit_exact===true;s.innerHTML=`<span class=\"${exact?'pass':'error'}\">PYNQ 完整图逐位自检 ${exact?'PASS':'FAIL'}</span>　配置 ${number(j.configure_seconds,3)} s　完整图 ${number(j.full_map_seconds,3)} s　${Number(j.fpga_hardware_rays).toLocaleString()}条射线<br>期望 SHA-256 <code>${esc(j.expected_raw_q15_sha256||'—')}</code><br>实测 SHA-256 <code>${esc(j.actual_raw_q15_sha256||'—')}</code>`;if(!exact)throw new Error('Stage39 完整物理图与可移植整数参考不一致')")
    js = js.replace("catalog&&catalog.scene.hardware.hardware_validated", "catalog&&hardwareReady(catalog.scene)")
    js = replace_required(js,
        "const s=cat.scene,hardware=s.hardware.hardware_validated;",
        "const s=cat.scene,hardware=hardwareReady(s);")
    js = replace_required(js,
        "$('hardwareNotice').textContent=hardware?`${scene} 使用同一份 Stage39 通用位流；场景切换只装载该场景运行时权重和固定 Rx 中间缓存，不重新综合、不替换 bitstream。`:`${scene} 的 FPGA 运行时模型尚未验证。`;",
        "$('hardwareNotice').textContent=hardware?`${scene} 使用同一份 95 MHz 通用位流；只装载该场景由训练集生成的硬件精确 QAT 权重与固定 Rx 缓存。FPGA 最终结果为原始 Q15，不做输出校准。`:`${scene} 缺少 Stage39 运行时模型。`;"
    )
    js = replace_required(js,
        "let s=cat.test_ssim_summary,ref=cat.pc_timing_reference;$('stats').innerHTML=[[String(catalog.test_samples),'held-out test 样本'],[number(s.mean,4),'PC 平均 SSIM'],[number(s.median,4),'PC 中位 SSIM'],[number(ref.mean_end_to_end_seconds_per_spectrum,3)+' s','PC 参考/幅（见来源）'],[String(catalog.test_samples),'正式盲测候选']]",
        "let s=cat.test_ssim_summary,ref=cat.pc_timing_reference,sp=cat.scene;$('stats').innerHTML=[[String(catalog.test_samples),'held-out test 样本'],[number(sp.stage39_original_pc_mean_ssim,4),'原始 PC FP32 平均 SSIM'],[number(sp.stage39_hardware_integer_mean_ssim,4),'Stage39 整数参考平均 SSIM'],[number(sp.stage39_mean_ssim_gap,4),'软件门禁 SSIM 差距'],[number(ref.mean_end_to_end_seconds_per_spectrum,3)+' s','PC 参考/幅（见来源）']]"
    )
    js = "function hardwareReady(s){let h=s&&s.hardware;return !!(h&&h.factorization_compatible&&h.runtime_model_sha256)}\n" + js
    js_path.write_text(js, encoding="utf-8")

    run = r'''@echo off
setlocal
cd /d "%~dp0"
call conda run --no-capture-output -n nerf2 python software\stage39\verify_stage39_pc_package.py
if errorlevel 1 exit /b %errorlevel%
echo Stage39 App is starting at http://127.0.0.1:8310
call conda run --no-capture-output -n nerf2 python pc_app\stage39_pc_server.py --port 8310
exit /b %errorlevel%
'''
    (PKG / "run_stage39_app.bat").write_text(run, encoding="utf-8")
    print("Stage39 PC App sources derived")


if __name__ == "__main__":
    main()
