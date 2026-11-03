`timescale 1ns/1ps
`default_nettype none
// 32-cycle restoring unsigned integer square root.
// Each cycle consumes two radicand bits.  The old implementation squared a
// 32-bit trial root on every cycle; this equivalent digit-by-digit form uses
// only a compare/subtract path and therefore closes timing at the Stage26 clock.
module n2_isqrt64(
  input wire clk,input wire rst_n,input wire start_i,input wire [63:0] radicand_i,
  output reg busy_o,output reg valid_o,output reg [31:0] root_o);

  reg [63:0] rad_shift_q;
  reg [65:0] remainder_q;
  reg [31:0] root_q;
  reg [5:0] count_q;

  wire [65:0] shifted_remainder = (remainder_q << 2) | rad_shift_q[63:62];
  wire [65:0] trial_divisor = {32'd0,root_q,2'b01};
  wire trial_accept = shifted_remainder >= trial_divisor;
  wire [65:0] next_remainder = trial_accept ?
                                (shifted_remainder-trial_divisor) : shifted_remainder;
  wire [31:0] next_root = {root_q[30:0],trial_accept};

  always @(posedge clk) begin
    if(!rst_n) begin
      busy_o<=0;valid_o<=0;root_o<=0;rad_shift_q<=0;
      remainder_q<=0;root_q<=0;count_q<=0;
    end else begin
      valid_o<=0;
      if(start_i&&!busy_o) begin
        rad_shift_q<=radicand_i;remainder_q<=0;root_q<=0;count_q<=0;busy_o<=1;
      end else if(busy_o) begin
        rad_shift_q<=rad_shift_q<<2;
        remainder_q<=next_remainder;
        root_q<=next_root;
        if(count_q==31) begin
          root_o<=next_root;busy_o<=0;valid_o<=1;
        end else count_q<=count_q+1'b1;
      end
    end
  end
endmodule
`default_nettype wire
