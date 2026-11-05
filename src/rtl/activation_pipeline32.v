`timescale 1ns/1ps
`default_nettype none

// One fully pipelined 32-lane block of the checkpoint's exact requantizer.
// A tag follows the data so a single unit can service all three ray cores.
module activation_pipeline32 #(
    parameter integer ACC_W = 32,
    parameter integer LANES = 32,
    parameter integer TAG_W = 4
)(
    input  wire                         clk,
    input  wire                         rst_n,
    input  wire                         in_valid,
    input  wire [TAG_W-1:0]             tag_i,
    input  wire [LANES*ACC_W-1:0]       din,
    input  wire [4:0]                   shift,
    input  wire                         relu_en,
    input  wire [LANES*16-1:0]          bias,
    output reg                          out_valid,
    output reg  [TAG_W-1:0]             tag_o,
    output reg  [LANES*8-1:0]           dout
);
    reg valid0, valid1, valid2;
    reg [TAG_W-1:0] tag0, tag1, tag2;
    reg [4:0] shift0;
    reg relu0, relu1, relu2;
    reg signed [ACC_W-1:0] acc0 [0:LANES-1];
    reg signed [15:0] bias0 [0:LANES-1];
    reg signed [ACC_W-1:0] shifted1 [0:LANES-1];
    reg signed [15:0] bias1 [0:LANES-1];
    reg signed [ACC_W:0] sum2 [0:LANES-1];

    integer lane;
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            valid0 <= 0; valid1 <= 0; valid2 <= 0; out_valid <= 0;
            tag0 <= 0; tag1 <= 0; tag2 <= 0; tag_o <= 0;
            shift0 <= 0; relu0 <= 0; relu1 <= 0; relu2 <= 0;
            dout <= 0;
            for (lane = 0; lane < LANES; lane = lane + 1) begin
                acc0[lane] <= 0; bias0[lane] <= 0;
                shifted1[lane] <= 0; bias1[lane] <= 0; sum2[lane] <= 0;
            end
        end else begin
            valid0 <= in_valid;
            valid1 <= valid0;
            valid2 <= valid1;
            out_valid <= valid2;
            tag0 <= tag_i; tag1 <= tag0; tag2 <= tag1; tag_o <= tag2;
            shift0 <= shift;
            relu0 <= relu_en; relu1 <= relu0; relu2 <= relu1;
            if (in_valid) begin
                for (lane = 0; lane < LANES; lane = lane + 1) begin
                    acc0[lane] <= din[lane*ACC_W +: ACC_W];
                    bias0[lane] <= bias[lane*16 +: 16];
                end
            end
            if (valid0) begin
                for (lane = 0; lane < LANES; lane = lane + 1) begin
                    shifted1[lane] <= acc0[lane] >>> shift0;
                    bias1[lane] <= bias0[lane];
                end
            end
            if (valid1) begin
                for (lane = 0; lane < LANES; lane = lane + 1)
                    sum2[lane] <= shifted1[lane] + bias1[lane];
            end
            if (valid2) begin
                for (lane = 0; lane < LANES; lane = lane + 1) begin
                    // ReLU is mathematically before INT8 saturation in the
                    // exported reference.  Testing the negative saturation
                    // first would turn a large negative hidden activation
                    // into -128 and leak it into the next layer.
                    if (relu2 && sum2[lane][ACC_W])
                        dout[lane*8 +: 8] <= 8'sd0;
                    else if (sum2[lane] < -128)
                        dout[lane*8 +: 8] <= -8'sd128;
                    else if (sum2[lane] > 127)
                        dout[lane*8 +: 8] <= 8'sd127;
                    else
                        dout[lane*8 +: 8] <= sum2[lane][7:0];
                end
            end
        end
    end
endmodule

`default_nettype wire
