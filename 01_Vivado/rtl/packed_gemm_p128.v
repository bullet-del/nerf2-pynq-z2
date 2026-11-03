`timescale 1ns/1ps
`default_nettype none

// 128 logical signed INT8 MAC lanes implemented by 64 packed DSP48E1
// multipliers.  Consecutive output weights share the same broadcast
// activation and are recovered exactly by packed_int8x2_mul.
module packed_gemm_p128 #(
    parameter integer ACC_W = 32,
    parameter integer P = 128
)(
    input  wire                    clk,
    input  wire                    rst_n,
    input  wire                    start,
    input  wire [15:0]             k_len,
    input  wire                    in_valid,
    input  wire signed [7:0]       act_in,
    input  wire [P*8-1:0]          w_in,
    output reg                     out_valid,
    output reg [P*ACC_W-1:0]       dout,
    output wire                    busy
);
    localparam integer PAIRS = P/2;
    reg run_q;
    reg [15:0] k_q;
    reg [15:0] count_q;
    reg first_d1, first_d2, last_d1, last_d2;

    wire beat = in_valid && run_q && !start;
    wire first = count_q == 0;
    wire last = count_q == k_q - 1'b1;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            run_q <= 0;
            k_q <= 0;
            count_q <= 0;
            first_d1 <= 0; first_d2 <= 0;
            last_d1 <= 0; last_d2 <= 0;
        end else begin
            first_d1 <= beat && first;
            first_d2 <= first_d1;
            last_d1 <= beat && last;
            last_d2 <= last_d1;
            if (start) begin
                run_q <= 1'b1;
                k_q <= k_len;
                count_q <= 0;
                first_d1 <= 0; first_d2 <= 0;
                last_d1 <= 0; last_d2 <= 0;
            end else if (beat) begin
                count_q <= last ? 0 : count_q + 1'b1;
            end
        end
    end

    wire [PAIRS-1:0] pair_valid;
    wire signed [15:0] pair_product0 [0:PAIRS-1];
    wire signed [15:0] pair_product1 [0:PAIRS-1];
    genvar pair_index;
    generate
        for (pair_index = 0; pair_index < PAIRS; pair_index = pair_index + 1) begin : g_pair
            packed_int8x2_mul u_mul (
                .clk(clk), .rst_n(rst_n), .valid_i(beat),
                .activation_i(act_in),
                .weight0_i(w_in[(2*pair_index)*8 +: 8]),
                .weight1_i(w_in[(2*pair_index+1)*8 +: 8]),
                .valid_o(pair_valid[pair_index]),
                .product0_o(pair_product0[pair_index]),
                .product1_o(pair_product1[pair_index])
            );
        end
    endgenerate

    reg signed [ACC_W-1:0] accumulator [0:P-1];
    integer lane;
    reg signed [ACC_W-1:0] product_ext;
    reg signed [ACC_W-1:0] accumulated;
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            out_valid <= 0;
            dout <= 0;
            for (lane = 0; lane < P; lane = lane + 1)
                accumulator[lane] <= 0;
        end else begin
            out_valid <= 1'b0;
            if (pair_valid[0]) begin
                for (lane = 0; lane < P; lane = lane + 1) begin
                    if (lane[0])
                        product_ext = pair_product1[lane/2];
                    else
                        product_ext = pair_product0[lane/2];
                    accumulated = first_d2 ? product_ext
                                           : accumulator[lane] + product_ext;
                    accumulator[lane] <= accumulated;
                    if (last_d2)
                        dout[lane*ACC_W +: ACC_W] <= accumulated;
                end
                if (last_d2)
                    out_valid <= 1'b1;
            end
        end
    end

    assign busy = run_q | pair_valid[0] | out_valid;
endmodule

`default_nettype wire
