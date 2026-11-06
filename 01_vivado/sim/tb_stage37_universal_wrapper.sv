`timescale 1ns/1ps
module tb_stage37_universal_wrapper;
 reg clk=0,rst_n=0,soft_rst=0;always #5 clk=~clk;
 reg [63:0] s_data=0;reg s_valid=0;wire s_ready;wire [63:0] m_data;
 wire m_valid,m_last;reg m_ready=1;wire [31:0] status;
 localparam LOAD_WORDS=53671,BUILD_WORDS=1057,CACHED_WORDS=97;
 reg [63:0] load_stream[0:LOAD_WORDS-1];
 reg [63:0] build_stream[0:BUILD_WORDS-1];
 reg [63:0] cached_stream[0:CACHED_WORDS-1];
 reg [63:0] expected_cache[0:63];
 integer i,received,cycles;


 n2_stage37_universal_stream_wrapper dut(
  .clk(clk),.rst_n(rst_n),.soft_rst(soft_rst),.s_axis_tdata(s_data),.s_axis_tvalid(s_valid),
  .s_axis_tready(s_ready),.m_axis_tdata(m_data),.m_axis_tvalid(m_valid),
  .m_axis_tlast(m_last),.m_axis_tready(m_ready),.status_o(status));

 task send_word(input [63:0] value);begin
   s_data<=value;s_valid<=1;do @(posedge clk);while(!s_ready);s_valid<=0;
 end endtask
 task send_load;begin
   for(i=0;i<LOAD_WORDS;i=i+1)begin s_data<=load_stream[i];s_valid<=1;
    do @(posedge clk);while(!s_ready);end s_valid<=0;
 end endtask
 task send_build;begin
   for(i=0;i<BUILD_WORDS;i=i+1)begin s_data<=build_stream[i];s_valid<=1;
    do @(posedge clk);while(!s_ready);end s_valid<=0;
 end endtask
 task send_cached;begin
   for(i=0;i<CACHED_WORDS;i=i+1)begin s_data<=cached_stream[i];s_valid<=1;
    do @(posedge clk);while(!s_ready);end s_valid<=0;
 end endtask

 initial begin
  $readmemh("s23_runtime_load.memh",load_stream);
  $readmemh("two_ray_cache_build.memh",build_stream);
  $readmemh("two_ray_cached_run.memh",cached_stream);
  $readmemh("two_ray_expected_cache.memh",expected_cache);
  repeat(6)@(posedge clk);rst_n=1;repeat(4)@(posedge clk);
  send_load();repeat(2)@(posedge clk);
  if(status[31]||!status[30]||status[28])$fatal(1,"load status=%08x",status);
  fork
   begin send_word(64'h9000000063e8f22a);end
   begin
    do @(posedge clk);while(!m_valid);
    if(m_data!==64'h9000000163e8f22a||!m_last)$fatal(1,"commit ack=%x last=%b",m_data,m_last);
   end
  join
  repeat(2)@(posedge clk);
  if(status[31]||!status[30]||!status[28])$fatal(1,"commit status=%08x",status);
  $display("STAGE37_RUNTIME_MODEL_LOAD_PASS status=%08x",status);

  received=0;fork
   begin send_build();end
   begin
    while(received<64)begin @(posedge clk);if(m_valid&&m_ready)begin
     if(m_data!==expected_cache[received])$fatal(1,"cache %0d got=%x exp=%x",received,m_data,expected_cache[received]);
     if(m_last!==(received==63))$fatal(1,"cache TLAST mismatch %0d",received);
     received=received+1;
    end end
   end
  join
  if(status[31]||!status[30])$fatal(1,"cache-build status=%08x",status);
  $display("STAGE37_CACHE_BUILD_BIT_EXACT_PASS samples=64");

  received=0;cycles=0;fork
   begin send_cached();end
   begin
    while(received<2&&cycles<200000)begin @(posedge clk);cycles=cycles+1;
     if(m_valid&&m_ready)begin
      if(m_data[31:0]!==32'd4582)$fatal(1,"cached ray %0d got=%0d",received,m_data[31:0]);
      if(m_last!==(received==1))$fatal(1,"cached TLAST mismatch %0d",received);
      received=received+1;
     end
    end
   end
  join
  if(received!=2)$fatal(1,"cached run timeout status=%08x",status);
  if(status[31]||!status[30])$fatal(1,"cached-run status=%08x",status);
  $display("STAGE37_UNIVERSAL_RUNTIME_LOAD_AND_INFERENCE_BIT_EXACT_PASS rays=2 magnitude=4582 cycles=%0d",cycles);
  $finish;
 end
endmodule
