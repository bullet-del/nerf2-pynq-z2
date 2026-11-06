`timescale 1ns/1ps
module tb_stage39_multiscene_generated;
reg clk=0,rst_n=0,soft_rst=0;always #5 clk=~clk;reg[63:0]s_data=0;reg s_valid=0;wire s_ready;
wire[63:0]m_data;wire m_valid,m_last;reg m_ready=1;wire[31:0]status;integer i;
localparam S23_WORDS=53977; reg [63:0] s23_stream[0:S23_WORDS-1];
localparam MEETINGROOM_WORDS=67679; reg [63:0] meetingroom_stream[0:MEETINGROOM_WORDS-1];
localparam OFFICE1_WORDS=66761; reg [63:0] office1_stream[0:OFFICE1_WORDS-1];
localparam OFFICE2_WORDS=88011; reg [63:0] office2_stream[0:OFFICE2_WORDS-1];
n2_stage37_universal_stream_wrapper dut(.clk(clk),.rst_n(rst_n),.soft_rst(soft_rst),.s_axis_tdata(s_data),.s_axis_tvalid(s_valid),.s_axis_tready(s_ready),.m_axis_tdata(m_data),.m_axis_tvalid(m_valid),.m_axis_tlast(m_last),.m_axis_tready(m_ready),.status_o(status));
task send_word(input[63:0]v);begin s_data<=v;s_valid<=1;do @(posedge clk);while(!s_ready);s_valid<=0;end endtask
task reset_pl;begin soft_rst<=1;repeat(6)@(posedge clk);soft_rst<=0;repeat(6)@(posedge clk);if(status!==32'h40000000)$fatal(1,"reset");end endtask
task commit_model(input[31:0]id);begin fork begin send_word({4'h9,28'd0,id});end begin do @(posedge clk);while(!m_valid);if(m_data!=={4'h9,27'd0,1'b1,id}||!m_last)$fatal(1,"commit");end join end endtask
task load_s23; begin for(i=0;i<S23_WORDS;i=i+1)begin s_data<=s23_stream[i];s_valid<=1;do @(posedge clk);while(!s_ready);end s_valid<=0;repeat(2)@(posedge clk);end endtask
task load_meetingroom; begin for(i=0;i<MEETINGROOM_WORDS;i=i+1)begin s_data<=meetingroom_stream[i];s_valid<=1;do @(posedge clk);while(!s_ready);end s_valid<=0;repeat(2)@(posedge clk);end endtask
task load_office1; begin for(i=0;i<OFFICE1_WORDS;i=i+1)begin s_data<=office1_stream[i];s_valid<=1;do @(posedge clk);while(!s_ready);end s_valid<=0;repeat(2)@(posedge clk);end endtask
task load_office2; begin for(i=0;i<OFFICE2_WORDS;i=i+1)begin s_data<=office2_stream[i];s_valid<=1;do @(posedge clk);while(!s_ready);end s_valid<=0;repeat(2)@(posedge clk);end endtask
initial begin $readmemh("switch_s23.memh",s23_stream);
$readmemh("switch_meetingroom.memh",meetingroom_stream);
$readmemh("switch_office1.memh",office1_stream);
$readmemh("switch_office2.memh",office2_stream); repeat(6)@(posedge clk);rst_n=1;repeat(5)@(posedge clk);reset_pl();load_s23();commit_model(32'h35622a80);reset_pl();load_meetingroom();commit_model(32'hf14349e3);reset_pl();load_office1();commit_model(32'hd0dd2294);reset_pl();load_office2();commit_model(32'hb88fa9e0);reset_pl();load_s23();commit_model(32'h35622a80);
$display("STAGE39_FOUR_SCENE_SWITCH_AND_RETURN_PASS");$finish;end endmodule
