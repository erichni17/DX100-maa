`include "HardFloat_consts.vi"

module LanlFp64Add(
    input clock,
    input nReset,
    input inValid,
    input subOp,
    input [63:0] a,
    input [63:0] b,
    output reg outValid,
    output reg [63:0] out,
    output reg [4:0] exceptionFlags
);
    wire [64:0] recA;
    wire [64:0] recB;
    wire [64:0] recOut;
    wire [63:0] ieeeOut;
    wire [4:0] flags;

    fNToRecFN#(11, 53) recodeA(a, recA);
    fNToRecFN#(11, 53) recodeB(b, recB);
    addRecFN#(11, 53) add(
        `flControl_default, subOp, recA, recB, `round_near_even,
        recOut, flags
    );
    recFNToFN#(11, 53) decodeOut(recOut, ieeeOut);

    always @(posedge clock or negedge nReset) begin
        if (!nReset) begin
            outValid <= 1'b0;
            out <= 64'b0;
            exceptionFlags <= 5'b0;
        end else begin
            outValid <= inValid;
            if (inValid) begin
                out <= ieeeOut;
                exceptionFlags <= flags;
            end
        end
    end
endmodule

module LanlFp64Mul(
    input clock,
    input nReset,
    input inValid,
    input [63:0] a,
    input [63:0] b,
    output reg outValid,
    output reg [63:0] out,
    output reg [4:0] exceptionFlags
);
    wire [64:0] recA;
    wire [64:0] recB;
    wire [64:0] recOut;
    wire [63:0] ieeeOut;
    wire [4:0] flags;

    fNToRecFN#(11, 53) recodeA(a, recA);
    fNToRecFN#(11, 53) recodeB(b, recB);
    mulRecFN#(11, 53) mul(
        `flControl_default, recA, recB, `round_near_even, recOut, flags
    );
    recFNToFN#(11, 53) decodeOut(recOut, ieeeOut);

    always @(posedge clock or negedge nReset) begin
        if (!nReset) begin
            outValid <= 1'b0;
            out <= 64'b0;
            exceptionFlags <= 5'b0;
        end else begin
            outValid <= inValid;
            if (inValid) begin
                out <= ieeeOut;
                exceptionFlags <= flags;
            end
        end
    end
endmodule

module LanlFp64Fma(
    input clock,
    input nReset,
    input inValid,
    input [63:0] a,
    input [63:0] b,
    input [63:0] c,
    output reg outValid,
    output reg [63:0] out,
    output reg [4:0] exceptionFlags
);
    wire [64:0] recA;
    wire [64:0] recB;
    wire [64:0] recC;
    wire [64:0] recOut;
    wire [63:0] ieeeOut;
    wire [4:0] flags;

    fNToRecFN#(11, 53) recodeA(a, recA);
    fNToRecFN#(11, 53) recodeB(b, recB);
    fNToRecFN#(11, 53) recodeC(c, recC);
    mulAddRecFN#(11, 53) fma(
        `flControl_default, 2'b00, recA, recB, recC,
        `round_near_even, recOut, flags
    );
    recFNToFN#(11, 53) decodeOut(recOut, ieeeOut);

    always @(posedge clock or negedge nReset) begin
        if (!nReset) begin
            outValid <= 1'b0;
            out <= 64'b0;
            exceptionFlags <= 5'b0;
        end else begin
            outValid <= inValid;
            if (inValid) begin
                out <= ieeeOut;
                exceptionFlags <= flags;
            end
        end
    end
endmodule

module LanlFp64DivLane(
    input clock,
    input nReset,
    input inValid,
    input [63:0] a,
    input [63:0] b,
    output inReady,
    output outValid,
    output [63:0] out,
    output [4:0] exceptionFlags
);
    wire [64:0] recA;
    wire [64:0] recB;
    wire [64:0] recOut;
    wire sqrtOpOut;

    fNToRecFN#(11, 53) recodeA(a, recA);
    fNToRecFN#(11, 53) recodeB(b, recB);
    divSqrtRecFN_small#(11, 53, 0) divider(
        nReset,
        clock,
        `flControl_default,
        inReady,
        inValid,
        1'b0,
        recA,
        recB,
        `round_near_even,
        outValid,
        sqrtOpOut,
        recOut,
        exceptionFlags
    );
    recFNToFN#(11, 53) decodeOut(recOut, out);
endmodule

module LanlFp64DivReplicated#(parameter LANES = 1) (
    input clock,
    input nReset,
    input [LANES - 1:0] inValid,
    input [LANES*64 - 1:0] a,
    input [LANES*64 - 1:0] b,
    output [LANES - 1:0] inReady,
    output [LANES - 1:0] outValid,
    output [LANES*64 - 1:0] out,
    output [LANES*5 - 1:0] exceptionFlags
);
    genvar lane;
    generate
        for (lane = 0; lane < LANES; lane = lane + 1) begin: div_lane
            LanlFp64DivLane divider(
                clock,
                nReset,
                inValid[lane],
                a[lane*64 +: 64],
                b[lane*64 +: 64],
                inReady[lane],
                outValid[lane],
                out[lane*64 +: 64],
                exceptionFlags[lane*5 +: 5]
            );
        end
    endgenerate
endmodule

module LanlFp64Div1(
    input clock,
    input nReset,
    input inValid,
    input [63:0] a,
    input [63:0] b,
    output inReady,
    output outValid,
    output [63:0] out,
    output [4:0] exceptionFlags
);
    LanlFp64DivReplicated#(1) dividers(
        clock, nReset, inValid, a, b, inReady, outValid, out, exceptionFlags
    );
endmodule

module LanlFp64Div4(
    input clock,
    input nReset,
    input [3:0] inValid,
    input [255:0] a,
    input [255:0] b,
    output [3:0] inReady,
    output [3:0] outValid,
    output [255:0] out,
    output [19:0] exceptionFlags
);
    LanlFp64DivReplicated#(4) dividers(
        clock, nReset, inValid, a, b, inReady, outValid, out, exceptionFlags
    );
endmodule

module LanlFp64Div8(
    input clock,
    input nReset,
    input [7:0] inValid,
    input [511:0] a,
    input [511:0] b,
    output [7:0] inReady,
    output [7:0] outValid,
    output [511:0] out,
    output [39:0] exceptionFlags
);
    LanlFp64DivReplicated#(8) dividers(
        clock, nReset, inValid, a, b, inReady, outValid, out, exceptionFlags
    );
endmodule
