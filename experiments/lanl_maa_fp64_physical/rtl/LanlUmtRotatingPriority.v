`timescale 1ns/1ps

// Rotating priority over one-bit candidate maps. Wide token/bank/descriptor
// muxes occur only after this module returns one selected index.
module LanlUmtRotatingPriority #(
    parameter COMPUTE_TOKENS = 24
)(
    input [5:0] cursor,
    input [COMPUTE_TOKENS-1:0] grantable,
    input [COMPUTE_TOKENS-1:0] bankBlocked,
    input [COMPUTE_TOKENS-1:0] dividerBlocked,
    output reg valid,
    output reg [5:0] index,
    output reg sawBankBlocked,
    output reg sawDividerBlocked
);
    integer probe;
    integer candidate;

    always @* begin
        valid = 1'b0;
        index = 6'b0;
        sawBankBlocked = 1'b0;
        sawDividerBlocked = 1'b0;
        for (probe = 0; probe < COMPUTE_TOKENS; probe = probe + 1) begin
            candidate = cursor + probe;
            if (candidate >= COMPUTE_TOKENS)
                candidate = candidate - COMPUTE_TOKENS;
            if (!valid) begin
                if (bankBlocked[candidate])
                    sawBankBlocked = 1'b1;
                if (dividerBlocked[candidate])
                    sawDividerBlocked = 1'b1;
                if (grantable[candidate]) begin
                    valid = 1'b1;
                    index = candidate[5:0];
                end
            end
        end
    end
endmodule
