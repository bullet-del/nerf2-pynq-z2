`timescale 1ns/1ps
`default_nettype none

// Descriptor packing, bits [50:0]:
// layer[3:0], group[5:4], compact_k[14:6], wbase[26:15],
// ibase[38:27], baddr[43:39], nvalid[51:44], last_group[52].
module stage37_runtime_descriptor_memory(
  input wire clk,
  input wire [4:0] descriptor_addr_i,
  output wire [3:0] layer_o, output wire [1:0] group_o,
  output wire [8:0] compact_k_o, output wire [11:0] weight_base_o,
  output wire [11:0] index_base_o, output wire [4:0] bias_addr_o,
  output wire [7:0] outputs_valid_o, output wire last_group_o,
  input wire [3:0] shift_layer_i, output wire [4:0] shift_o,
  input wire descriptor_we_i, input wire [4:0] descriptor_waddr_i,
  input wire [63:0] descriptor_wdata_i,
  input wire shift_we_i, input wire [3:0] shift_waddr_i,
  input wire [4:0] shift_wdata_i
);
  (* ram_style="distributed" *) reg [52:0] descriptors[0:31];
  (* ram_style="distributed" *) reg [4:0] shifts[0:15];
  wire [52:0] d=descriptors[descriptor_addr_i];
  assign layer_o=d[3:0]; assign group_o=d[5:4];
  assign compact_k_o=d[14:6]; assign weight_base_o=d[26:15];
  assign index_base_o=d[38:27]; assign bias_addr_o=d[43:39];
  assign outputs_valid_o=d[51:44]; assign last_group_o=d[52];
  assign shift_o=shifts[shift_layer_i];
  always @(posedge clk) begin
    if(descriptor_we_i) descriptors[descriptor_waddr_i]<=descriptor_wdata_i[52:0];
    if(shift_we_i) shifts[shift_waddr_i]<=shift_wdata_i;
  end
endmodule

`default_nettype wire
