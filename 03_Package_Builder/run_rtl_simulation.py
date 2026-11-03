from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

import numpy as np

from common import DEFAULT_CONFIG, KIT_ROOT, SCENES, load_config, python, run, sha256, utc_now, write_json


def address(target: int, location: int, segment: int = 0) -> np.uint64:
    return np.uint64((target << 60) | (location << 48) | (segment << 42))


def load_packet(runtime_model: Path) -> tuple[np.ndarray, int]:
    lut_keys = ((5, "att_factor_u16"), (6, "att_phase_cos_i16"),
                (7, "att_phase_sin_i16"), (8, "sig_amp_u16"),
                (9, "sig_phase_cos_i16"), (10, "sig_phase_sin_i16"),
                (11, "path_loss_u16"))
    with np.load(runtime_model, allow_pickle=False) as value:
        weights = np.ascontiguousarray(value["weights_u8"], dtype=np.uint8)
        indices = np.asarray(value["indices_u16"], dtype=np.uint16)
        biases = np.ascontiguousarray(value["biases_i16"], dtype="<i2")
        descriptors = np.asarray(value["descriptors_u64"], dtype=np.uint64)
        shifts = np.asarray(value["shifts_u8"], dtype=np.uint8)
        model_id = int(np.asarray(value["model_id"]).item())
        records: list[np.uint64] = []
        for location, row in enumerate(weights.view("<u8").reshape(len(weights), 16)):
            for segment, item in enumerate(row): records.extend((address(0, location, segment), np.uint64(item)))
        for location, item in enumerate(indices): records.extend((address(1, location), np.uint64(item)))
        for location, row in enumerate(biases.view("<u8").reshape(len(biases), 32)):
            for segment, item in enumerate(row): records.extend((address(2, location, segment), np.uint64(item)))
        for location, item in enumerate(descriptors): records.extend((address(3, location), np.uint64(item)))
        for location, item in enumerate(shifts): records.extend((address(4, location), np.uint64(item)))
        for target, key in lut_keys:
            for location, item in enumerate(np.ascontiguousarray(value[key]).view(np.uint16)):
                records.extend((address(target, location), np.uint64(item)))
    count = len(records) // 2
    packet = np.empty(1 + len(records), np.uint64)
    packet[0] = np.uint64((8 << 60) | (count << 32) | model_id)
    packet[1:] = records
    return packet, model_id


def memh(path: Path, values: np.ndarray) -> None:
    path.write_text("\n".join(f"{int(item):016x}" for item in values) + "\n", encoding="ascii")


def universal_tb(load_words: int, model_id: int, expected: list[int]) -> str:
    return f'''`timescale 1ns/1ps
module tb_stage39_universal_generated;
reg clk=0,rst_n=0,soft_rst=0; always #5 clk=~clk;
reg [63:0] s_data=0; reg s_valid=0; wire s_ready; wire [63:0] m_data;
wire m_valid,m_last; reg m_ready=1; wire [31:0] status;
localparam LOAD_WORDS={load_words},BUILD_WORDS=1057,CACHED_WORDS=97;
reg [63:0] load_stream[0:LOAD_WORDS-1],build_stream[0:BUILD_WORDS-1],cached_stream[0:CACHED_WORDS-1];
reg [63:0] expected_cache[0:63]; integer i,received,cycles;
n2_stage37_universal_stream_wrapper dut(.clk(clk),.rst_n(rst_n),.soft_rst(soft_rst),
 .s_axis_tdata(s_data),.s_axis_tvalid(s_valid),.s_axis_tready(s_ready),
 .m_axis_tdata(m_data),.m_axis_tvalid(m_valid),.m_axis_tlast(m_last),.m_axis_tready(m_ready),.status_o(status));
task send_word(input [63:0] v); begin s_data<=v;s_valid<=1;do @(posedge clk);while(!s_ready);s_valid<=0;end endtask
task send_load; begin for(i=0;i<LOAD_WORDS;i=i+1) begin s_data<=load_stream[i];s_valid<=1;do @(posedge clk);while(!s_ready);end s_valid<=0;end endtask
task send_build; begin for(i=0;i<BUILD_WORDS;i=i+1) begin s_data<=build_stream[i];s_valid<=1;do @(posedge clk);while(!s_ready);end s_valid<=0;end endtask
task send_cached; begin for(i=0;i<CACHED_WORDS;i=i+1) begin s_data<=cached_stream[i];s_valid<=1;do @(posedge clk);while(!s_ready);end s_valid<=0;end endtask
initial begin
 $readmemh("s23_runtime_load.memh",load_stream);$readmemh("two_ray_cache_build.memh",build_stream);
 $readmemh("two_ray_cached_run.memh",cached_stream);$readmemh("two_ray_expected_cache.memh",expected_cache);
 repeat(6)@(posedge clk);rst_n=1;repeat(4)@(posedge clk);send_load();repeat(2)@(posedge clk);
 if(status[31]||!status[30]||status[28])$fatal(1,"load status=%08x",status);
 fork begin send_word(64'h90000000{model_id:08x});end begin do @(posedge clk);while(!m_valid);
  if(m_data!==64'h90000001{model_id:08x}||!m_last)$fatal(1,"commit ack=%x",m_data);end join
 received=0;fork begin send_build();end begin while(received<64)begin @(posedge clk);if(m_valid&&m_ready)begin
  if(m_data!==expected_cache[received])$fatal(1,"cache mismatch %0d",received);received=received+1;end end end join
 received=0;cycles=0;fork begin send_cached();end begin while(received<2&&cycles<200000)begin @(posedge clk);cycles=cycles+1;
  if(m_valid&&m_ready)begin if((received==0&&m_data[31:0]!==32'd{expected[0]})||(received==1&&m_data[31:0]!==32'd{expected[1]}))$fatal(1,"ray mismatch");received=received+1;end end end join
 if(received!=2)$fatal(1,"timeout");$display("STAGE39_RUNTIME_LOAD_CACHE_AND_INFERENCE_PASS");$finish;
end endmodule
'''


def multiscene_tb(words: dict[str, int], ids: dict[str, int]) -> str:
    declarations = "\n".join(
        f"localparam {scene.upper()}_WORDS={words[scene]}; reg [63:0] {scene.lower()}_stream[0:{scene.upper()}_WORDS-1];"
        for scene in SCENES)
    reads = "\n".join(f'$readmemh("switch_{scene.lower()}.memh",{scene.lower()}_stream);' for scene in SCENES)
    tasks = []
    for scene in SCENES:
        low, up = scene.lower(), scene.upper()
        tasks.append(f"task load_{low}; begin for(i=0;i<{up}_WORDS;i=i+1)begin s_data<={low}_stream[i];s_valid<=1;do @(posedge clk);while(!s_ready);end s_valid<=0;repeat(2)@(posedge clk);end endtask")
    sequence = []
    for scene in (*SCENES, "s23"):
        sequence.append(f"reset_pl();load_{scene.lower()}();commit_model(32'h{ids[scene]:08x});")
    return f'''`timescale 1ns/1ps
module tb_stage39_multiscene_generated;
reg clk=0,rst_n=0,soft_rst=0;always #5 clk=~clk;reg[63:0]s_data=0;reg s_valid=0;wire s_ready;
wire[63:0]m_data;wire m_valid,m_last;reg m_ready=1;wire[31:0]status;integer i;
{declarations}
n2_stage37_universal_stream_wrapper dut(.clk(clk),.rst_n(rst_n),.soft_rst(soft_rst),.s_axis_tdata(s_data),.s_axis_tvalid(s_valid),.s_axis_tready(s_ready),.m_axis_tdata(m_data),.m_axis_tvalid(m_valid),.m_axis_tlast(m_last),.m_axis_tready(m_ready),.status_o(status));
task send_word(input[63:0]v);begin s_data<=v;s_valid<=1;do @(posedge clk);while(!s_ready);s_valid<=0;end endtask
task reset_pl;begin soft_rst<=1;repeat(6)@(posedge clk);soft_rst<=0;repeat(6)@(posedge clk);if(status!==32'h40000000)$fatal(1,"reset");end endtask
task commit_model(input[31:0]id);begin fork begin send_word({{4'h9,28'd0,id}});end begin do @(posedge clk);while(!m_valid);if(m_data!=={{4'h9,27'd0,1'b1,id}}||!m_last)$fatal(1,"commit");end join end endtask
{chr(10).join(tasks)}
initial begin {reads} repeat(6)@(posedge clk);rst_n=1;repeat(5)@(posedge clk);{''.join(sequence)}
$display("STAGE39_FOUR_SCENE_SWITCH_AND_RETURN_PASS");$finish;end endmodule
'''


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--profile", choices=("full", "smoke"), default="full")
    args = parser.parse_args(); config = load_config(args.config)
    base = KIT_ROOT / "work" / args.profile / "rtl_sim"
    if base.exists(): shutil.rmtree(base)
    (base / "rtl").mkdir(parents=True)
    for path in (KIT_ROOT / "01_Vivado/rtl").glob("*"):
        if path.is_file(): shutil.copy2(path, base / "rtl" / path.name)
    packets, ids, words = {}, {}, {}
    for scene in SCENES:
        model = KIT_ROOT / "work" / args.profile / "runtime" / scene / "model/stage37_runtime_model.npz"
        if not model.is_file(): raise FileNotFoundError("run runtime stage first: " + str(model))
        packet, model_id = load_packet(model); packets[scene] = packet; ids[scene] = model_id; words[scene] = len(packet)
        memh(base / f"switch_{scene.lower()}.memh", packet)
    memh(base / "s23_runtime_load.memh", packets["s23"])
    run([python(), KIT_ROOT / "02_Model_Pipeline/stage37/build_sim_vectors.py",
         "--source-root", KIT_ROOT / "05_PYNQ_Board/template",
         "--tv", KIT_ROOT / f"work/{args.profile}/runtime/s23/hardware_export/tv3_ray_net.txt",
         "--manifest", KIT_ROOT / f"work/{args.profile}/runtime/s23/hardware_export/manifest_ray.json",
         "--base-vector", KIT_ROOT / "01_Vivado/sim/vectors/two_ray_cache_build.memh",
         "--out", base])
    expected = json.loads((base / "two_ray_stage37.json").read_text(encoding="utf-8"))["expected_q15"]
    (base / "tb_stage39_universal_generated.sv").write_text(universal_tb(words["s23"], ids["s23"], expected), encoding="utf-8")
    (base / "tb_stage39_multiscene_generated.sv").write_text(multiscene_tb(words, ids), encoding="utf-8")
    vivado = Path(config["vivado_executable"])
    run([vivado, "-mode", "batch", "-source", KIT_ROOT / "01_Vivado/tcl/run_stage39_rtl_tests.tcl", "-tclargs", base])
    universal = (base / "universal.log").read_text(encoding="utf-8", errors="replace")
    multiscene = (base / "multiscene.log").read_text(encoding="utf-8", errors="replace")
    report = {
        "schema": "n2-stage39-cold-rtl-simulation-v1", "created_utc": utc_now(), "status": "PASS",
        "runtime_load_cache_inference_bit_exact": "STAGE39_RUNTIME_LOAD_CACHE_AND_INFERENCE_PASS" in universal,
        "four_scene_switch_and_return": "STAGE39_FOUR_SCENE_SWITCH_AND_RETURN_PASS" in multiscene,
        "model_ids": {scene: f"{ids[scene]:08x}" for scene in SCENES}, "load_words": words,
        "expected_q15": expected,
        "logs": {"universal_sha256": sha256(base / "universal.log"), "multiscene_sha256": sha256(base / "multiscene.log")},
    }
    if not all((report["runtime_load_cache_inference_bit_exact"], report["four_scene_switch_and_return"])):
        raise RuntimeError("RTL simulation PASS tokens missing")
    write_json(base / "rtl_simulation_report.json", report)
    print(json.dumps(report, indent=2))


if __name__ == "__main__": main()
