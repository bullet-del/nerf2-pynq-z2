`timescale 1ns/1ps
`default_nettype none
// Fixed-point implementation of upstream Renderer_spectrum.raw2outputs.
// A scheduler result arrives only once per completed MLP point, so the renderer
// spreads the arithmetic over registered stages. This removes the former
// raw_i-to-square combinational path while preserving Q15 operation order.
module stage37_ray_accumulator #(
  parameter integer RAW_FIFO_AW=6,
  parameter signed [17:0] PHASE_INIT_COS=17705,
  parameter signed [17:0] PHASE_INIT_SIN=27573
)(input wire clk,input wire rst_n,input wire [31:0] raw_i,input wire raw_valid_i,
  output wire raw_ready_o,output reg [31:0] magnitude_o,output reg magnitude_valid_o,
  input wire magnitude_ready_i,output reg protocol_error_o,
  input wire lut_we_i,input wire [3:0] lut_target_i,input wire [7:0] lut_addr_i,
  input wire [15:0] lut_data_i);
  localparam signed [31:0] ONE=32768;
  localparam [3:0] ST_IDLE=4'd0, ST_MUL=4'd1, ST_COMBINE=4'd2,
                   ST_PATH=4'd3, ST_CONTRIB=4'd4, ST_SUM=4'd5,
                   ST_COMMIT=4'd6, ST_SQUARE=4'd7, ST_RADICAND=4'd8;

  reg [15:0] att_factor[0:255],sig_amp[0:255],path_loss[0:63];
  reg signed [15:0] att_pcos[0:255],att_psin[0:255],sig_pcos[0:255],sig_psin[0:255];
  reg [3:0] state;
  reg [6:0] sample_count;
  reg [31:0] att_prefix;
  reg signed [31:0] phase_re,phase_im,acc_re,acc_im;

  // layer_scheduler emits all NB results on consecutive clocks and has no
  // output-ready input.  The arithmetic below deliberately spans several
  // clocks for timing closure, so a FIFO is required at this boundary.  A
  // depth of 64 accepts even one complete ray as a continuous burst; Stage26
  // normally sends two separate bursts of 32 results.
  (* ram_style = "distributed" *) reg [31:0] raw_fifo [0:(1<<RAW_FIFO_AW)-1];
  reg [RAW_FIFO_AW:0] raw_wp,raw_rp;
  wire raw_fifo_empty=(raw_wp==raw_rp);
  wire raw_fifo_full=(raw_wp[RAW_FIFO_AW]!=raw_rp[RAW_FIFO_AW])&&
                     (raw_wp[RAW_FIFO_AW-1:0]==raw_rp[RAW_FIFO_AW-1:0]);
  wire raw_push=raw_valid_i&&!raw_fifo_full;

  reg [15:0] att_factor_q,sig_amp_q,path_loss_q;
  reg signed [15:0] att_pcos_q,att_psin_q,sig_pcos_q,sig_psin_q;
  reg [31:0] att_prefix_q;
  reg signed [31:0] phase_re_q,phase_im_q;

  reg signed [63:0] dr_mul0_q,dr_mul1_q,di_mul0_q,di_mul1_q;
  reg signed [63:0] pr_mul0_q,pr_mul1_q,pi_mul0_q,pi_mul1_q;
  reg signed [63:0] amp_mul_q,att_mul_q;
  reg signed [63:0] dr_q,di_q,npr_q,npi_q,amp1_q,att_next_q;
  reg signed [63:0] amp2_mul_q,cr_mul_q,ci_mul_q,sum_re_q,sum_im_q;
  reg [31:0] abs_re_q,abs_im_q;
  reg [63:0] square_re_q,square_im_q;

  reg sqrt_start;
  reg [63:0] sqrt_radicand;
  wire sqrt_busy,sqrt_valid;
  wire [31:0] sqrt_root;
  wire raw_pop=(state==ST_IDLE)&&!magnitude_valid_o&&!sqrt_busy&&!sqrt_start&&
               !raw_fifo_empty;
  n2_isqrt64 u_sqrt(.clk(clk),.rst_n(rst_n),.start_i(sqrt_start),.radicand_i(sqrt_radicand),
    .busy_o(sqrt_busy),.valid_o(sqrt_valid),.root_o(sqrt_root));

  assign raw_ready_o=!raw_fifo_full;

  always @(posedge clk) begin
    if(!rst_n) begin
      state<=ST_IDLE;sample_count<=0;att_prefix<=ONE;raw_wp<=0;raw_rp<=0;
      phase_re<=PHASE_INIT_COS;phase_im<=PHASE_INIT_SIN;acc_re<=0;acc_im<=0;
      att_factor_q<=0;sig_amp_q<=0;path_loss_q<=0;
      att_pcos_q<=0;att_psin_q<=0;sig_pcos_q<=0;sig_psin_q<=0;
      att_prefix_q<=0;phase_re_q<=0;phase_im_q<=0;
      dr_mul0_q<=0;dr_mul1_q<=0;di_mul0_q<=0;di_mul1_q<=0;
      pr_mul0_q<=0;pr_mul1_q<=0;pi_mul0_q<=0;pi_mul1_q<=0;
      amp_mul_q<=0;att_mul_q<=0;dr_q<=0;di_q<=0;npr_q<=0;npi_q<=0;
      amp1_q<=0;att_next_q<=0;amp2_mul_q<=0;cr_mul_q<=0;ci_mul_q<=0;
      sum_re_q<=0;sum_im_q<=0;abs_re_q<=0;abs_im_q<=0;
      square_re_q<=0;square_im_q<=0;
      sqrt_start<=0;sqrt_radicand<=0;magnitude_o<=0;
      magnitude_valid_o<=0;protocol_error_o<=0;
    end else begin
      // Target IDs are part of the Stage37 runtime model protocol.  LUT data
      // is loaded only while inference is idle and becomes active on COMMIT.
      if(lut_we_i) case(lut_target_i)
        4'd5:att_factor[lut_addr_i]<=lut_data_i;
        4'd6:att_pcos[lut_addr_i]<=$signed(lut_data_i);
        4'd7:att_psin[lut_addr_i]<=$signed(lut_data_i);
        4'd8:sig_amp[lut_addr_i]<=lut_data_i;
        4'd9:sig_pcos[lut_addr_i]<=$signed(lut_data_i);
        4'd10:sig_psin[lut_addr_i]<=$signed(lut_data_i);
        4'd11:path_loss[lut_addr_i[5:0]]<=lut_data_i;
        default:;
      endcase
      sqrt_start<=0;
      if(magnitude_valid_o&&magnitude_ready_i) magnitude_valid_o<=0;
      if(raw_valid_i&&!raw_ready_o) protocol_error_o<=1;
      if(raw_push) begin
        raw_fifo[raw_wp[RAW_FIFO_AW-1:0]]<=raw_i;
        raw_wp<=raw_wp+1'b1;
      end
      if(raw_pop) raw_rp<=raw_rp+1'b1;
      if(sqrt_valid) begin magnitude_o<=sqrt_root;magnitude_valid_o<=1; end

      case(state)
        ST_IDLE: if(raw_pop) begin
          att_factor_q<=att_factor[raw_fifo[raw_rp[RAW_FIFO_AW-1:0]][7:0]^8'h80];
          att_pcos_q<=att_pcos[raw_fifo[raw_rp[RAW_FIFO_AW-1:0]][15:8]^8'h80];
          att_psin_q<=att_psin[raw_fifo[raw_rp[RAW_FIFO_AW-1:0]][15:8]^8'h80];
          sig_amp_q<=sig_amp[raw_fifo[raw_rp[RAW_FIFO_AW-1:0]][23:16]^8'h80];
          sig_pcos_q<=sig_pcos[raw_fifo[raw_rp[RAW_FIFO_AW-1:0]][31:24]^8'h80];
          sig_psin_q<=sig_psin[raw_fifo[raw_rp[RAW_FIFO_AW-1:0]][31:24]^8'h80];
          path_loss_q<=path_loss[sample_count];
          att_prefix_q<=att_prefix;phase_re_q<=phase_re;phase_im_q<=phase_im;
          state<=ST_MUL;
        end

        ST_MUL: begin
          dr_mul0_q<=$signed(sig_pcos_q)*$signed(phase_re_q);
          dr_mul1_q<=$signed(sig_psin_q)*$signed(phase_im_q);
          di_mul0_q<=$signed(sig_pcos_q)*$signed(phase_im_q);
          di_mul1_q<=$signed(sig_psin_q)*$signed(phase_re_q);
          pr_mul0_q<=$signed(phase_re_q)*$signed(att_pcos_q);
          pr_mul1_q<=$signed(phase_im_q)*$signed(att_psin_q);
          pi_mul0_q<=$signed(phase_re_q)*$signed(att_psin_q);
          pi_mul1_q<=$signed(phase_im_q)*$signed(att_pcos_q);
          amp_mul_q<=$signed({1'b0,sig_amp_q})*$signed(att_prefix_q);
          att_mul_q<=$signed(att_prefix_q)*$signed({1'b0,att_factor_q});
          state<=ST_COMBINE;
        end

        ST_COMBINE: begin
          dr_q<=($signed(dr_mul0_q)>>>15)-($signed(dr_mul1_q)>>>15);
          di_q<=($signed(di_mul0_q)>>>15)+($signed(di_mul1_q)>>>15);
          npr_q<=($signed(pr_mul0_q)>>>15)-($signed(pr_mul1_q)>>>15);
          npi_q<=($signed(pi_mul0_q)>>>15)+($signed(pi_mul1_q)>>>15);
          amp1_q<=$signed(amp_mul_q)>>>15;
          att_next_q<=$signed(att_mul_q)>>>15;
          state<=ST_PATH;
        end

        ST_PATH: begin
          amp2_mul_q<=$signed(amp1_q)*$signed({1'b0,path_loss_q});
          state<=ST_CONTRIB;
        end

        ST_CONTRIB: begin
          cr_mul_q<=($signed(amp2_mul_q)>>>15)*$signed(dr_q);
          ci_mul_q<=($signed(amp2_mul_q)>>>15)*$signed(di_q);
          state<=ST_SUM;
        end

        ST_SUM: begin
          sum_re_q<=$signed(acc_re)+($signed(cr_mul_q)>>>15);
          sum_im_q<=$signed(acc_im)+($signed(ci_mul_q)>>>15);
          state<=ST_COMMIT;
        end

        ST_COMMIT: begin
          if(sample_count==63) begin
            abs_re_q<=($signed(sum_re_q)<0) ? -$signed(sum_re_q) : $signed(sum_re_q);
            abs_im_q<=($signed(sum_im_q)<0) ? -$signed(sum_im_q) : $signed(sum_im_q);
            sample_count<=0;att_prefix<=ONE;phase_re<=PHASE_INIT_COS;phase_im<=PHASE_INIT_SIN;
            acc_re<=0;acc_im<=0;state<=ST_SQUARE;
          end else begin
            sample_count<=sample_count+1'b1;
            acc_re<=sum_re_q;acc_im<=sum_im_q;att_prefix<=att_next_q;
            phase_re<=npr_q;phase_im<=npi_q;state<=ST_IDLE;
          end
        end

        ST_SQUARE: begin
          square_re_q<=abs_re_q*abs_re_q;
          square_im_q<=abs_im_q*abs_im_q;
          state<=ST_RADICAND;
        end

        ST_RADICAND: begin
          sqrt_radicand<=square_re_q+square_im_q;sqrt_start<=1;state<=ST_IDLE;
        end

        default: state<=ST_IDLE;
      endcase
    end
  end
endmodule
`default_nettype wire
