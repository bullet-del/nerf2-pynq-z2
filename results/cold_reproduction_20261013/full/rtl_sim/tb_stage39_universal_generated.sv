`timescale 1ns/1ps
module tb_stage39_universal_generated;
reg clk=0,rst_n=0,soft_rst=0; always #5 clk=~clk;
reg [63:0] s_data=0; reg s_valid=0; wire s_ready; wire [63:0] m_data;
wire m_valid,m_last; reg m_ready=1; wire [31:0] status;
localparam LOAD_WORDS=53977,BUILD_WORDS=1057,CACHED_WORDS=97;
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
 fork begin send_word(64'h9000000035622a80);end begin do @(posedge clk);while(!m_valid);
  if(m_data!==64'h9000000135622a80||!m_last)$fatal(1,"commit ack=%x",m_data);end join
 received=0;fork begin send_build();end begin while(received<64)begin @(posedge clk);if(m_valid&&m_ready)begin
  if(m_data!==expected_cache[received])$fatal(1,"cache mismatch %0d",received);received=received+1;end end end join
 received=0;cycles=0;fork begin send_cached();end begin while(received<2&&cycles<200000)begin @(posedge clk);cycles=cycles+1;
  if(m_valid&&m_ready)begin if((received==0&&m_data[31:0]!==32'd5351)||(received==1&&m_data[31:0]!==32'd5351))$fatal(1,"ray mismatch");received=received+1;end end end join
 if(received!=2)$fatal(1,"timeout");$display("STAGE39_RUNTIME_LOAD_CACHE_AND_INFERENCE_PASS");$finish;
end endmodule
