`timescale 1ns/1ps
`default_nettype none

// Stage37 universal multi-scene accelerator.  The bitstream contains only the
// common graph and arithmetic.  OP=8 loads a scene model, OP=9 commits it,
// OP=6 rebuilds the fixed-Rx attenuation cache, and OP=5 renders any Tx.
module n2_stage37_universal_stream_wrapper(
 input wire clk,input wire rst_n,input wire soft_rst,
 input wire [63:0] s_axis_tdata,input wire s_axis_tvalid,output wire s_axis_tready,
 output reg [63:0] m_axis_tdata,output reg m_axis_tvalid,output wire m_axis_tlast,
 input wire m_axis_tready,output wire [31:0] status_o);
 localparam [3:0] OP_EXACT=4'h4,OP_CACHED=4'h5,OP_BUILD_CACHE=4'h6,
                  OP_LOAD=4'h8,OP_COMMIT=4'h9,OP_ECHO=4'hf;
 localparam [3:0] PS_IDLE=0,PS_CONST=1,PS_SIGNAL=2,PS_PTS=3,
                  PS_ATTEN=4,PS_RENDER=5,PS_CACHED=6,
                  PS_LOAD_ADDR=7,PS_LOAD_DATA=8;
 localparam [1:0] CMD_EXACT=0,CMD_CACHED=1,CMD_BUILD_CACHE=2;
 reg [3:0] pstate;reg [1:0] command_q;
 reg [17:0] pairs_remaining;reg [5:0] beat_index;
 reg [5:0] sample_index;reg start_pulse,mode_q,bad_command,protocol_error;
 reg [27:0] load_remaining;reg [3:0] load_target;
 reg [11:0] load_addr;reg [5:0] load_segment;
 reg model_valid;reg [31:0] model_id;
 reg soft_rst_d,soft_rst_dd;
 always @(posedge clk)begin soft_rst_d<=soft_rst;soft_rst_dd<=soft_rst_d;end
 wire rst_n_i=rst_n&~soft_rst_dd;

 wire [1:0] enc_we;
 wire [1:0] enc_kind=(pstate==PS_PTS)?0:((beat_index[3:0]<8)?1:2);
 wire [2:0] enc_segment=beat_index[2:0];
 wire input_accept=s_axis_tvalid&&s_axis_tready;
 wire const_core=beat_index>=16;
 wire pts_core=beat_index>=8;
 assign enc_we=(pstate==PS_CONST&&input_accept)?(const_core?2'b10:2'b01):
               (pstate==PS_PTS&&input_accept)?(pts_core?2'b10:2'b01):2'b00;
 wire factor_ready,factor_busy,factor_done,factor_valid,address_mismatch;
 wire [31:0] factor_result;
 wire load_data_accept=(pstate==PS_LOAD_DATA)&&input_accept;
 wire load_address_valid=
   ((load_target==0)&&(load_addr<3072)&&(load_segment<16))||
   ((load_target==1)&&(load_addr<3072))||
   ((load_target==2)&&(load_addr<32)&&(load_segment<32))||
   ((load_target==3)&&(load_addr<32))||
   ((load_target==4)&&(load_addr<16))||
   ((load_target>=5)&&(load_target<=10)&&(load_addr<256))||
   ((load_target==11)&&(load_addr<64));
 wire weight_we=load_data_accept&&load_address_valid&&(load_target==0);
 wire index_we=load_data_accept&&load_address_valid&&(load_target==1);
 wire bias_we=load_data_accept&&load_address_valid&&(load_target==2);
 wire descriptor_we=load_data_accept&&load_address_valid&&(load_target==3);
 wire shift_we=load_data_accept&&load_address_valid&&(load_target==4);
 wire lut_we=load_data_accept&&load_address_valid&&(load_target>=5)&&(load_target<=11);
 stage37_two_ray_engine u_factor(
  .clk(clk),.rst_n(rst_n_i),.enc_we_i(enc_we),.enc_kind_i(enc_kind),
  .enc_segment_i(enc_segment),.enc_data_i(s_axis_tdata),.start_i(start_pulse),.mode_i(mode_q),
  .ready_o(factor_ready),.busy_o(factor_busy),.done_o(factor_done),
  .result_valid_o(factor_valid),.result_o(factor_result),.address_mismatch_o(address_mismatch),
  .weight_we_i(weight_we),.weight_waddr_i(load_addr[11:0]),
  .weight_wsegment_i(load_segment[3:0]),.weight_wdata_i(s_axis_tdata),
  .index_we_i(index_we),.index_waddr_i(load_addr[11:0]),.index_wdata_i(s_axis_tdata[8:0]),
  .bias_we_i(bias_we),.bias_waddr_i(load_addr[4:0]),
  .bias_wsegment_i(load_segment[4:0]),.bias_wdata_i(s_axis_tdata),
  .descriptor_we_i(descriptor_we),.descriptor_waddr_i(load_addr[4:0]),
  .descriptor_wdata_i(s_axis_tdata),.shift_we_i(shift_we),
  .shift_waddr_i(load_addr[3:0]),.shift_wdata_i(s_axis_tdata[4:0]));

 reg [15:0] signal0,signal1;reg raw_valid;reg [31:0] raw0;
 reg [31:0] raw1_buffer[0:63];reg replay_active;reg [5:0] replay_index;
 reg pair_magnitude_index;
 wire raw_ready;wire [31:0] magnitude;wire magnitude_valid,renderer_error;
 wire result_fifo_room;
 wire replay_valid=replay_active&&raw_ready;
 wire renderer_raw_valid=raw_valid||replay_valid;
 wire [31:0] renderer_raw=replay_active?raw1_buffer[replay_index]:raw0;
 stage37_ray_accumulator renderer0(
  .clk(clk),.rst_n(rst_n_i),.raw_i(renderer_raw),.raw_valid_i(renderer_raw_valid),
  .raw_ready_o(raw_ready),.magnitude_o(magnitude),.magnitude_valid_o(magnitude_valid),
  .magnitude_ready_i(result_fifo_room),.protocol_error_o(renderer_error),
  .lut_we_i(lut_we),.lut_target_i(load_target),.lut_addr_i(load_addr[7:0]),
  .lut_data_i(s_axis_tdata[15:0]));

 wire [3:0] op=s_axis_tdata[63:60];wire [23:0] header_rays=s_axis_tdata[23:0];
 wire echo_accept=pstate==PS_IDLE&&input_accept&&op==OP_ECHO;
 wire commit_accept=pstate==PS_IDLE&&input_accept&&op==OP_COMMIT;
 wire load_accept=pstate==PS_IDLE&&input_accept&&op==OP_LOAD&&s_axis_tdata[59:32]!=0;
 wire run_command=(op==OP_EXACT)||(op==OP_CACHED)||(op==OP_BUILD_CACHE);
 wire run_accept=pstate==PS_IDLE&&input_accept&&run_command&&model_valid&&
                 header_rays!=0&&!header_rays[0];
 assign s_axis_tready=(pstate==PS_IDLE)?1'b1:
                      ((pstate==PS_CONST||pstate==PS_PTS)?factor_ready:
                       (pstate==PS_CACHED)?raw_ready:
                       (pstate==PS_LOAD_ADDR||pstate==PS_LOAD_DATA)?1'b1:1'b0);

 wire cache_factor_push=(pstate==PS_ATTEN)&&(command_q==CMD_BUILD_CACHE)&&factor_valid;
 wire magnitude_push=magnitude_valid&&result_fifo_room&&pstate==PS_RENDER;

 always @(posedge clk or negedge rst_n_i)begin
  if(!rst_n_i)begin pstate<=PS_IDLE;command_q<=CMD_EXACT;pairs_remaining<=0;
   beat_index<=0;sample_index<=0;start_pulse<=0;mode_q<=0;bad_command<=0;
   protocol_error<=0;signal0<=0;signal1<=0;raw_valid<=0;raw0<=0;
   replay_active<=0;replay_index<=0;pair_magnitude_index<=0;
   load_remaining<=0;load_target<=0;load_addr<=0;load_segment<=0;
   model_valid<=0;model_id<=0;
  end else begin
   start_pulse<=0;raw_valid<=0;
   if(raw_valid&&!raw_ready)protocol_error<=1;
   if(cache_factor_push&&!result_fifo_room)protocol_error<=1;
   if(replay_valid)begin
    if(replay_index==63)begin replay_active<=0;replay_index<=0;end
    else replay_index<=replay_index+1;
   end
   if(magnitude_push)begin
    if(!pair_magnitude_index)begin pair_magnitude_index<=1;replay_active<=1;replay_index<=0;end
    else begin
      pair_magnitude_index<=0;
      if(pairs_remaining==1)begin pairs_remaining<=0;pstate<=PS_IDLE;end
      else begin pairs_remaining<=pairs_remaining-1;beat_index<=0;pstate<=PS_CONST;end
    end
   end
   case(pstate)
    PS_IDLE:if(input_accept)begin
      if(load_accept)begin
        load_remaining<=s_axis_tdata[59:32];model_valid<=0;pstate<=PS_LOAD_ADDR;
      end else if(commit_accept)begin
        model_id<=s_axis_tdata[31:0];model_valid<=!protocol_error;
      end else if(run_accept)begin
        pairs_remaining<=header_rays[23:1];beat_index<=0;sample_index<=0;
        command_q<=(op==OP_CACHED)?CMD_CACHED:((op==OP_BUILD_CACHE)?CMD_BUILD_CACHE:CMD_EXACT);
        pstate<=PS_CONST;
      end else if(op!=OP_ECHO)bad_command<=1;
    end
    PS_LOAD_ADDR:if(input_accept)begin
      load_target<=s_axis_tdata[63:60];load_addr<=s_axis_tdata[59:48];
      load_segment<=s_axis_tdata[47:42];pstate<=PS_LOAD_DATA;
    end
    PS_LOAD_DATA:if(input_accept)begin
      if(!load_address_valid)protocol_error<=1;
      if(load_remaining==1)begin load_remaining<=0;pstate<=PS_IDLE;end
      else begin load_remaining<=load_remaining-1;pstate<=PS_LOAD_ADDR;end
    end
    PS_CONST:if(input_accept)begin
      if(beat_index==31)begin
        beat_index<=0;sample_index<=0;
        if(command_q==CMD_BUILD_CACHE)begin mode_q<=1;pstate<=PS_PTS;end
        else begin mode_q<=0;start_pulse<=1;pstate<=PS_SIGNAL;end
      end else beat_index<=beat_index+1;
    end
    PS_SIGNAL:if(factor_valid)begin
      signal0<=factor_result[15:0];signal1<=factor_result[31:16];sample_index<=0;beat_index<=0;
      pstate<=(command_q==CMD_CACHED)?PS_CACHED:PS_PTS;
    end
    PS_PTS:if(input_accept)begin
      if(beat_index==15)begin beat_index<=0;mode_q<=1;start_pulse<=1;pstate<=PS_ATTEN;end
      else beat_index<=beat_index+1;
    end
    PS_ATTEN:if(factor_valid)begin
      if(command_q==CMD_BUILD_CACHE)begin
        if(sample_index==63)begin
          sample_index<=0;
          if(pairs_remaining==1)begin pairs_remaining<=0;pstate<=PS_IDLE;end
          else begin pairs_remaining<=pairs_remaining-1;beat_index<=0;pstate<=PS_CONST;end
        end else begin sample_index<=sample_index+1;beat_index<=0;pstate<=PS_PTS;end
      end else begin
        raw0<={signal0,factor_result[15:0]};
        raw1_buffer[sample_index]<={signal1,factor_result[31:16]};raw_valid<=1;
        if(sample_index==63)begin pair_magnitude_index<=0;pstate<=PS_RENDER;end
        else begin sample_index<=sample_index+1;beat_index<=0;pstate<=PS_PTS;end
      end
    end
    PS_CACHED:if(input_accept)begin
      raw0<={signal0,s_axis_tdata[15:0]};
      raw1_buffer[sample_index]<={signal1,s_axis_tdata[31:16]};raw_valid<=1;
      if(sample_index==63)begin pair_magnitude_index<=0;pstate<=PS_RENDER;end
      else sample_index<=sample_index+1;
    end
    PS_RENDER:;
    default:pstate<=PS_IDLE;
   endcase
  end
 end

 reg [32:0] result_fifo[0:15];reg [3:0] rq_wp,rq_rp;reg [4:0] rq_count;
 reg [23:0] output_remaining;reg m_last_q;
 assign result_fifo_room=rq_count!=16;
 wire fifo_pop=!m_axis_tvalid&&rq_count!=0&&!echo_accept&&!commit_accept;
 wire fifo_push=(cache_factor_push||magnitude_push)&&result_fifo_room;
 wire [31:0] fifo_value=cache_factor_push?factor_result:magnitude;
 always @(posedge clk or negedge rst_n_i)begin
  if(!rst_n_i)begin rq_wp<=0;rq_rp<=0;rq_count<=0;output_remaining<=0;
   m_axis_tdata<=0;m_axis_tvalid<=0;m_last_q<=0;
  end else begin
   if(m_axis_tvalid&&m_axis_tready)begin m_axis_tvalid<=0;m_last_q<=0;end
   if(run_accept)begin
     if(op==OP_BUILD_CACHE)output_remaining<={header_rays[17:1],6'b0};
     else output_remaining<=header_rays;
   end
   if(echo_accept)begin m_axis_tdata<=s_axis_tdata;m_axis_tvalid<=1;m_last_q<=1;end
   else if(commit_accept)begin
    m_axis_tdata<={4'h9,27'd0,!protocol_error,s_axis_tdata[31:0]};
    m_axis_tvalid<=1;m_last_q<=1;
   end
   else if(fifo_pop)begin m_axis_tdata<={32'd0,result_fifo[rq_rp][31:0]};m_axis_tvalid<=1;
    m_last_q<=result_fifo[rq_rp][32];rq_rp<=rq_rp+1;end
   if(fifo_push)begin result_fifo[rq_wp]<={output_remaining==1,fifo_value};rq_wp<=rq_wp+1;
    output_remaining<=output_remaining-1;end
   case({fifo_push,fifo_pop}) 2'b10:rq_count<=rq_count+1;2'b01:rq_count<=rq_count-1;
    default:rq_count<=rq_count;endcase
  end
 end
 assign m_axis_tlast=m_last_q;
 assign status_o={bad_command|protocol_error|renderer_error|(factor_busy&&address_mismatch),
  pstate==PS_IDLE,factor_busy,model_valid,protocol_error,command_q,1'b0,
  6'd0,pairs_remaining[17:0]};
endmodule
`default_nettype wire
