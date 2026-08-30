`timescale 1ns/1ps

// One 16x640 single-action bank with asynchronous whole-row read and ten
// independently masked 64-bit write lanes. There is deliberately no reset.
module LanlUmtBank16x640(
    input clock,
    input [3:0] readRow,
    output [639:0] readData,
    input writeValid,
    input [3:0] writeRow,
    input [9:0] writeMask,
    input [639:0] writeData
);
    (* keep = "true", umt_state_class = "bank" *)
        reg [639:0] memory [0:15];
    integer lane;

    assign readData = memory[readRow];

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
