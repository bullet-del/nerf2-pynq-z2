`timescale 1ns/1ps
`default_nettype none

// Checkpoint-independent version of the Stage34 dual-ray engine.  Arithmetic,
// topology and cycle order are unchanged; all sparse data, descriptors and
// requantisation shifts are runtime writable.
module stage37_two_ray_engine(
    input wire clk, input wire rst_n,
    input wire [1:0] enc_we_i, input wire [1:0] enc_kind_i,
    input wire [2:0] enc_segment_i, input wire [63:0] enc_data_i,
    input wire start_i, input wire mode_i,
    output wire ready_o, output wire busy_o, output wire done_o,
    output reg result_valid_o, output reg [31:0] result_o,
    output wire address_mismatch_o,

    input wire weight_we_i, input wire [11:0] weight_waddr_i,
    input wire [3:0] weight_wsegment_i, input wire [63:0] weight_wdata_i,
    input wire index_we_i, input wire [11:0] index_waddr_i,
    input wire [8:0] index_wdata_i,
    input wire bias_we_i, input wire [4:0] bias_waddr_i,
    input wire [4:0] bias_wsegment_i, input wire [63:0] bias_wdata_i,
    input wire descriptor_we_i, input wire [4:0] descriptor_waddr_i,
    input wire [63:0] descriptor_wdata_i,
    input wire shift_we_i, input wire [3:0] shift_waddr_i,
    input wire [4:0] shift_wdata_i
);
    localparam [3:0] S_IDLE=0,S_ARM=1,S_PRIME=2,S_FEED=3,
                     S_WAIT_GEMM=4,S_ACT=5,S_WAIT_ACT=6,S_DONE=7;
    reg [3:0] state;
    reg [4:0] descriptor;
    reg [8:0] feed_k;
    reg rbuf,mode_q;
    reg [3:0] act_feed_tag;

    reg [511:0] enc_pts[0:1],enc_view[0:1],enc_tx[0:1];
    integer ec;
    always @(posedge clk) begin
        for(ec=0;ec<2;ec=ec+1) if(enc_we_i[ec]) begin
            case(enc_kind_i)
              0:enc_pts[ec][enc_segment_i*64 +:64]<=enc_data_i;
              1:enc_view[ec][enc_segment_i*64 +:64]<=enc_data_i;
              2:enc_tx[ec][enc_segment_i*64 +:64]<=enc_data_i;
              default:;
            endcase
        end
    end

    (* ram_style="distributed" *) reg [1023:0] abuf0[0:1][0:1];
    (* ram_style="distributed" *) reg [1023:0] abuf1[0:1][0:1];
    wire [3:0] layer;
    wire [1:0] group;
    wire [8:0] compact_k;
    wire [11:0] descriptor_wbase,descriptor_ibase;
    wire [4:0] descriptor_baddr;
    wire [7:0] descriptor_nvalid;
    wire last_group;
    wire [4:0] active_shift;
    stage37_runtime_descriptor_memory u_descriptors(
      .clk(clk),.descriptor_addr_i(descriptor),.layer_o(layer),.group_o(group),
      .compact_k_o(compact_k),.weight_base_o(descriptor_wbase),
      .index_base_o(descriptor_ibase),.bias_addr_o(descriptor_baddr),
      .outputs_valid_o(descriptor_nvalid),.last_group_o(last_group),
      .shift_layer_i(layer),.shift_o(active_shift),
      .descriptor_we_i(descriptor_we_i),.descriptor_waddr_i(descriptor_waddr_i),
      .descriptor_wdata_i(descriptor_wdata_i),.shift_we_i(shift_we_i),
      .shift_waddr_i(shift_waddr_i),.shift_wdata_i(shift_wdata_i));
    wire is_tap=(layer==8)||(layer==12);
    wire relu_en=(layer==5)||(layer==6)||(layer==7)||(layer==10)||(layer==11);

    reg [11:0] weight_addr,index_addr;
    reg [4:0] bias_addr;
    wire [1023:0] weight_word;
    wire [8:0] original_index;
    wire [2047:0] bias_word;
    stage37_runtime_model_memory u_model(
      .clk(clk),.weight_addr_i(weight_addr),.weight_o(weight_word),
      .index_addr_i(index_addr),.index_o(original_index),
      .bias_addr_i(bias_addr),.bias_o(bias_word),
      .weight_we_i(weight_we_i),.weight_waddr_i(weight_waddr_i),
      .weight_wsegment_i(weight_wsegment_i),.weight_wdata_i(weight_wdata_i),
      .index_we_i(index_we_i),.index_waddr_i(index_waddr_i),.index_wdata_i(index_wdata_i),
      .bias_we_i(bias_we_i),.bias_waddr_i(bias_waddr_i),
      .bias_wsegment_i(bias_wsegment_i),.bias_wdata_i(bias_wdata_i));

    wire [8:0] skip_index=original_index-256;
    wire [8:0] tx_index=original_index-319;
    wire [1023:0] mw0=rbuf?abuf1[0][original_index[7]]:abuf0[0][original_index[7]];
    wire [1023:0] mw1=rbuf?abuf1[1][original_index[7]]:abuf0[1][original_index[7]];
    wire [7:0] mb0=mw0[original_index[6:0]*8 +:8];
    wire [7:0] mb1=mw1[original_index[6:0]*8 +:8];
    wire [7:0] act0=(layer==5)?enc_pts[0][skip_index[5:0]*8 +:8]:
      (layer==10)?((original_index<319)?enc_view[0][skip_index[5:0]*8 +:8]:enc_tx[0][tx_index[5:0]*8 +:8]):mb0;
    wire [7:0] act1=(layer==5)?enc_pts[1][skip_index[5:0]*8 +:8]:
      (layer==10)?((original_index<319)?enc_view[1][skip_index[5:0]*8 +:8]:enc_tx[1][tx_index[5:0]*8 +:8]):mb1;

    reg feed_valid;
    reg signed [7:0] act_pipe0,act_pipe1;
    reg [1023:0] weight_pipe;
    always @(posedge clk or negedge rst_n) begin
      if(!rst_n) begin feed_valid<=0;act_pipe0<=0;act_pipe1<=0;weight_pipe<=0;end
      else begin
        feed_valid<=state==S_FEED;
        if(state==S_FEED) begin act_pipe0<=act0;act_pipe1<=act1;weight_pipe<=weight_word;end
      end
    end

    wire [1:0] gemm_valid;
    wire [4095:0] gemm_out0,gemm_out1;
    wire gemm_start=state==S_ARM;
    packed_gemm_p128 g0(.clk(clk),.rst_n(rst_n),.start(gemm_start),
      .k_len({7'd0,compact_k}),.in_valid(feed_valid),.act_in(act_pipe0),
      .w_in(weight_pipe),.out_valid(gemm_valid[0]),.dout(gemm_out0),.busy());
    packed_gemm_p128 g1(.clk(clk),.rst_n(rst_n),.start(gemm_start),
      .k_len({7'd0,compact_k}),.in_valid(feed_valid),.act_in(act_pipe1),
      .w_in(weight_pipe),.out_valid(gemm_valid[1]),.dout(gemm_out1),.busy());

    wire [1:0] act_block=act_feed_tag[1:0];
    wire [1:0] act_core=act_feed_tag[3:2];
    reg [1023:0] selected_gemm_block;
    always @* case(act_core)
      0:selected_gemm_block=gemm_out0[act_block*1024 +:1024];
      1:selected_gemm_block=gemm_out1[act_block*1024 +:1024];
      default:selected_gemm_block=gemm_out1[act_block*1024 +:1024];
    endcase
    wire [511:0] selected_bias=bias_word[act_block*512 +:512];
    wire act_out_valid;
    wire [3:0] act_out_tag;
    wire [255:0] act_out;
    activation_pipeline32 u_activation(
      .clk(clk),.rst_n(rst_n),.in_valid(state==S_ACT),.tag_i(act_feed_tag),
      .din(selected_gemm_block),.shift(active_shift),.relu_en(relu_en),
      .bias(selected_bias),.out_valid(act_out_valid),.tag_o(act_out_tag),.dout(act_out));

    always @(posedge clk or negedge rst_n) begin
      if(!rst_n) begin
        state<=S_IDLE;descriptor<=0;feed_k<=0;rbuf<=0;mode_q<=0;act_feed_tag<=0;
        weight_addr<=0;index_addr<=0;bias_addr<=0;result_o<=0;result_valid_o<=0;
      end else begin
        result_valid_o<=0;
        if(act_out_valid) begin
          if(is_tap) begin
            if(act_out_tag[1:0]==0) case(act_out_tag[3:2])
              0:result_o[15:0]<=act_out[15:0];
              1:result_o[31:16]<=act_out[15:0];
              default:;
            endcase
          end else if(rbuf) begin
            case(act_out_tag[3:2])
              0:abuf0[0][group[0]][act_out_tag[1:0]*256 +:256]<=act_out;
              1:abuf0[1][group[0]][act_out_tag[1:0]*256 +:256]<=act_out;
              default:;
            endcase
          end else begin
            case(act_out_tag[3:2])
              0:abuf1[0][group[0]][act_out_tag[1:0]*256 +:256]<=act_out;
              1:abuf1[1][group[0]][act_out_tag[1:0]*256 +:256]<=act_out;
              default:;
            endcase
          end
        end
        case(state)
          S_IDLE:if(start_i) begin
            mode_q<=mode_i;descriptor<=mode_i?10:19;rbuf<=0;
            state<=S_ARM;
          end
          S_ARM:begin
            weight_addr<=descriptor_wbase;index_addr<=descriptor_ibase;
            bias_addr<=descriptor_baddr;state<=S_PRIME;
          end
          S_PRIME:begin feed_k<=0;if(compact_k>1) begin
            weight_addr<=descriptor_wbase+1;index_addr<=descriptor_ibase+1;end
            state<=S_FEED;end
          S_FEED:if(feed_k+1>=compact_k) state<=S_WAIT_GEMM;else begin
            feed_k<=feed_k+1;weight_addr<=descriptor_wbase+feed_k+2;
            index_addr<=descriptor_ibase+feed_k+2;end
          S_WAIT_GEMM:if(&gemm_valid) begin act_feed_tag<=0;state<=S_ACT;end
          S_ACT:if(act_feed_tag==7) state<=S_WAIT_ACT;else act_feed_tag<=act_feed_tag+1;
          S_WAIT_ACT:if(act_out_valid&&act_out_tag==7) begin
            if((mode_q&&layer==8)||(!mode_q&&layer==12)) state<=S_DONE;
            else begin
              if(last_group&&!is_tap) rbuf<=~rbuf;
              descriptor<=descriptor+1;state<=S_ARM;
            end
          end
          S_DONE:begin result_valid_o<=1;state<=S_IDLE;end
          default:state<=S_IDLE;
        endcase
      end
    end
    assign ready_o=state==S_IDLE;
    assign busy_o=state!=S_IDLE;
    assign done_o=state==S_DONE;
    assign address_mismatch_o=(compact_k==0)||(descriptor_nvalid==0);
endmodule

`default_nettype wire
