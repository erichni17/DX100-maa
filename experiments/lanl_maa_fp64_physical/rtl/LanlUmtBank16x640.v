`timescale 1ns/1ps

// One 16x640 single-action bank with asynchronous whole-row read and ten
// independently masked 64-bit write lanes. There is deliberately no reset.
module LanlUmtBank16x640 #(
    parameter ENABLE_STATE_WITNESS = 0
)(
    input clock,
    input [3:0] readRow,
    output [639:0] readData,
    input writeValid,
    input [3:0] writeRow,
    input [9:0] writeMask,
    input [639:0] writeData,
    output [15:0] stateParity
);
    (* keep = "true", umt_state_class = "bank" *)
        reg [639:0] memory [0:15];
    integer lane;

    assign readData = memory[readRow];

    // The full-state witness is simulation-only. Keeping the reduction tree
    // behind a constant parameter prevents it from adding read ports or
    // parity fanout to the fixed witness-disabled cost wrappers.
    genvar witnessRow;
    generate
        if (ENABLE_STATE_WITNESS != 0) begin: state_witness
            for (witnessRow = 0; witnessRow < 16;
                 witnessRow = witnessRow + 1) begin: row_parity
                assign stateParity[witnessRow] = ^memory[witnessRow];
            end
        end else begin: no_state_witness
            assign stateParity = 16'b0;
        end
    endgenerate

    always @(posedge clock) begin
        if (writeValid) begin
            for (lane = 0; lane < 10; lane = lane + 1) begin
                if (writeMask[lane])
                    memory[writeRow][lane * 64 +: 64] <=
                        writeData[lane * 64 +: 64];
            end
        end
    end
endmodule
