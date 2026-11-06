`timescale 1ns/1ps
`default_nettype none

// One checkpoint-independent memory fabric.  Sixteen 64-bit banks provide a
// 1024-bit P=128 INT8 weight word while retaining a native 64-bit runtime
// write port.  3072 sparse words cover all four scene models while remaining
// within the XC7Z020 BRAM budget.
module stage37_runtime_model_memory(
    input  wire          clk,
    input  wire [11:0]   weight_addr_i,
    output wire [1023:0] weight_o,
    input  wire [11:0]   index_addr_i,
    output reg  [8:0]    index_o,
    input  wire [4:0]    bias_addr_i,
    output wire [2047:0] bias_o,

    input  wire          weight_we_i,
    input  wire [11:0]   weight_waddr_i,
    input  wire [3:0]    weight_wsegment_i,
    input  wire [63:0]   weight_wdata_i,
    input  wire          index_we_i,
    input  wire [11:0]   index_waddr_i,
    input  wire [8:0]    index_wdata_i,
    input  wire          bias_we_i,
    input  wire [4:0]    bias_waddr_i,
    input  wire [4:0]    bias_wsegment_i,
    input  wire [63:0]   bias_wdata_i
);
    (* ram_style="block" *) reg [8:0] index_mem [0:3071];
    (* ram_style="distributed" *) reg [63:0] bias_bank [0:31][0:31];
    reg [63:0] weight_q [0:15];
    reg [63:0] bias_q [0:31];
    integer bank;

    always @(posedge clk) begin
        index_o <= index_mem[index_addr_i];
        if (index_we_i) index_mem[index_waddr_i] <= index_wdata_i;
        for (bank=0; bank<32; bank=bank+1) begin
            bias_q[bank] <= bias_bank[bank][bias_addr_i];
            if (bias_we_i && bias_wsegment_i==bank[4:0])
                bias_bank[bank][bias_waddr_i] <= bias_wdata_i;
        end
    end

    // Put each 64-bit slice in its own generated scope.  A two-dimensional
    // declaration makes Vivado treat all sixteen banks as one 3,145,728-bit
    // variable and trips Synth 8-4556's 1,000,000-bit variable limit even
    // though every physical BRAM bank is small enough.  Separate generated
    // variables preserve the same 1024-bit read word and runtime write port.
    genvar g;
    generate
      for (g=0; g<16; g=g+1) begin: G_WEIGHT_OUT
        (* ram_style="block" *) reg [63:0] memory [0:3071];
        always @(posedge clk) begin
          weight_q[g] <= memory[weight_addr_i];
          if (weight_we_i && weight_wsegment_i==g)
            memory[weight_waddr_i] <= weight_wdata_i;
        end
        assign weight_o[g*64 +: 64] = weight_q[g];
      end
      for (g=0; g<32; g=g+1) begin: G_BIAS_OUT
        assign bias_o[g*64 +: 64] = bias_q[g];
      end
    endgenerate
endmodule

`default_nettype wire
