`timescale 1ns/1ps
module tb_stage37_multiscene_switch;
 reg clk=0,rst_n=0,soft_rst=0; always #5 clk=~clk;
 reg [63:0] s_data=0; reg s_valid=0; wire s_ready;
 wire [63:0] m_data; wire m_valid,m_last; reg m_ready=1;
 wire [31:0] status;

 localparam S23_WORDS=53671, MEETING_WORDS=67645,
            OFFICE1_WORDS=66659, OFFICE2_WORDS=88011;
 reg [63:0] s23_stream[0:S23_WORDS-1];
 reg [63:0] meeting_stream[0:MEETING_WORDS-1];
 reg [63:0] office1_stream[0:OFFICE1_WORDS-1];
 reg [63:0] office2_stream[0:OFFICE2_WORDS-1];
 integer i;

 n2_stage37_universal_stream_wrapper dut(
  .clk(clk),.rst_n(rst_n),.soft_rst(soft_rst),
  .s_axis_tdata(s_data),.s_axis_tvalid(s_valid),.s_axis_tready(s_ready),
  .m_axis_tdata(m_data),.m_axis_tvalid(m_valid),.m_axis_tlast(m_last),
  .m_axis_tready(m_ready),.status_o(status));

 task send_word(input [63:0] value); begin
  s_data<=value; s_valid<=1; do @(posedge clk); while(!s_ready); s_valid<=0;
 end endtask

 task reset_pl; begin
  soft_rst<=1; repeat(6) @(posedge clk); soft_rst<=0; repeat(6) @(posedge clk);
  if(status!==32'h40000000) $fatal(1,"reset did not return clean idle: %08x",status);
 end endtask

 task commit_model(input [31:0] model_id); begin
  fork
   begin send_word({4'h9,28'd0,model_id}); end
   begin
    do @(posedge clk); while(!m_valid);
    if(m_data!=={4'h9,27'd0,1'b1,model_id} || !m_last)
      $fatal(1,"commit ACK mismatch: got=%016x last=%b",m_data,m_last);
   end
  join
  repeat(2) @(posedge clk);
  if(status[31] || !status[30] || !status[28])
    $fatal(1,"commit status invalid: %08x",status);
 end endtask

 task load_s23; begin
  for(i=0;i<S23_WORDS;i=i+1) begin
   s_data<=s23_stream[i]; s_valid<=1; do @(posedge clk); while(!s_ready);
  end
  s_valid<=0; repeat(2) @(posedge clk);
  if(status[31] || !status[30] || status[28]) $fatal(1,"s23 load status=%08x",status);
 end endtask

 task load_meeting; begin
  for(i=0;i<MEETING_WORDS;i=i+1) begin
   s_data<=meeting_stream[i]; s_valid<=1; do @(posedge clk); while(!s_ready);
  end
  s_valid<=0; repeat(2) @(posedge clk);
  if(status[31] || !status[30] || status[28]) $fatal(1,"Meetingroom load status=%08x",status);
 end endtask

 task load_office1; begin
  for(i=0;i<OFFICE1_WORDS;i=i+1) begin
   s_data<=office1_stream[i]; s_valid<=1; do @(posedge clk); while(!s_ready);
  end
  s_valid<=0; repeat(2) @(posedge clk);
  if(status[31] || !status[30] || status[28]) $fatal(1,"Office1 load status=%08x",status);
 end endtask

 task load_office2; begin
  for(i=0;i<OFFICE2_WORDS;i=i+1) begin
   s_data<=office2_stream[i]; s_valid<=1; do @(posedge clk); while(!s_ready);
  end
  s_valid<=0; repeat(2) @(posedge clk);
  if(status[31] || !status[30] || status[28]) $fatal(1,"Office2 load status=%08x",status);
 end endtask

 initial begin
  $readmemh("switch_s23.memh",s23_stream);
  $readmemh("switch_meetingroom.memh",meeting_stream);
  $readmemh("switch_office1.memh",office1_stream);
  $readmemh("switch_office2.memh",office2_stream);
  repeat(6) @(posedge clk); rst_n=1; repeat(5) @(posedge clk);

  load_s23(); commit_model(32'h63e8f22a);
  $display("SWITCH_PASS s23 status=%08x",status);
  reset_pl(); load_meeting(); commit_model(32'h3c5a4804);
  $display("SWITCH_PASS Meetingroom status=%08x",status);
  reset_pl(); load_office1(); commit_model(32'h7b4efd1e);
  $display("SWITCH_PASS Office1 status=%08x",status);
  reset_pl(); load_office2(); commit_model(32'hf99343c6);
  $display("SWITCH_PASS Office2 status=%08x",status);
  reset_pl(); load_s23(); commit_model(32'h63e8f22a);
  $display("STAGE37_FOUR_SCENE_SWITCH_AND_RETURN_PASS status=%08x",status);
  $finish;
 end
endmodule
