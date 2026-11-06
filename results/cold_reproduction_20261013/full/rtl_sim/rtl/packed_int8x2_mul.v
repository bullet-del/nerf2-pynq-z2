`timescale 1ns/1ps
`default_nettype none

// Two exact signed INT8 products using one shared-activation 25x18 multiply.
// The 16-bit gap prevents the two raw products from overlapping.  The lower
// unsigned-byte representation and arithmetic high-word carry are corrected
// after the DSP product register.
module packed_int8x2_mul (
    input  wire               clk,
    input  wire               rst_n,
    input  wire               valid_i,
    input  wire signed [7:0]  activation_i,
    input  wire signed [7:0]  weight0_i,
    input  wire signed [7:0]  weight1_i,
    output reg                valid_o,
    output reg signed [15:0]  product0_o,
    output reg signed [15:0]  product1_o
);
    wire signed [24:0] upper_weight =
        $signed({weight1_i[7], weight1_i, 16'b0});
    wire signed [24:0] lower_weight =
        $signed({17'b0, weight0_i[7:0]});
    wire signed [24:0] packed_weight = upper_weight + lower_weight;
    wire signed [17:0] activation_ext =
        $signed({{10{activation_i[7]}}, activation_i});

    (* use_dsp = "yes" *)
    wire signed [42:0] packed_product = packed_weight * activation_ext;

    reg signed [42:0] product_q;
    reg signed [7:0] activation_q;
    reg weight0_negative_q;
    reg valid_q;

    wire signed [16:0] low_raw =
        $signed({product_q[15], product_q[15:0]});
    wire signed [16:0] activation_times_256 =
        $signed({{9{activation_q[7]}}, activation_q}) <<< 8;
    wire signed [16:0] product0_corrected =
        low_raw - (weight0_negative_q ? activation_times_256 : 17'sd0);
    wire signed [26:0] high_raw = $signed(product_q[42:16]);
    wire signed [26:0] product1_corrected =
        high_raw + (product_q[15] ? 27'sd1 : 27'sd0);

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            product_q <= 43'sd0;
            activation_q <= 8'sd0;
            weight0_negative_q <= 1'b0;
            valid_q <= 1'b0;
            valid_o <= 1'b0;
            product0_o <= 16'sd0;
            product1_o <= 16'sd0;
        end else begin
            product_q <= packed_product;
            activation_q <= activation_i;
            weight0_negative_q <= weight0_i[7];
            valid_q <= valid_i;
            valid_o <= valid_q;
            if (valid_q) begin
                product0_o <= product0_corrected[15:0];
                product1_o <= product1_corrected[15:0];
            end
        end
    end
endmodule

`default_nettype wire
