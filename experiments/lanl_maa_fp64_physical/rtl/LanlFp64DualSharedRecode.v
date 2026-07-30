`include "HardFloat_consts.vi"

module LanlFp64AddRecoded(
    input clock,
    input nReset,
    input inValid,
    input subOp,
    input [64:0] recA,
    input [64:0] recB,
    output reg outValid,
    output reg [63:0] out,
    output reg [4:0] exceptionFlags
);
    wire [64:0] recOut;
    wire [63:0] ieeeOut;
    wire [4:0] flags;

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

module LanlFp64MulRecoded(
    input clock,
    input nReset,
    input inValid,
    input [64:0] recA,
    input [64:0] recB,
    output reg outValid,
    output reg [63:0] out,
    output reg [4:0] exceptionFlags
);
    wire [64:0] recOut;
    wire [63:0] ieeeOut;
    wire [4:0] flags;

    mulRecFN#(11, 53) multiply(
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

module LanlFp64DivLaneRecoded(
    input clock,
    input nReset,
    input inValid,
    input [64:0] recA,
    input [64:0] recB,
    output inReady,
    output outValid,
    output [63:0] out,
    output [4:0] exceptionFlags
);
    wire [64:0] recOut;
    wire sqrtOpOut;

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

module LanlFp64DivReplicatedRecoded#(parameter LANES = 1) (
    input clock,
    input nReset,
    input [LANES - 1:0] inValid,
    input [LANES*65 - 1:0] recA,
    input [LANES*65 - 1:0] recB,
    output [LANES - 1:0] inReady,
    output [LANES - 1:0] outValid,
    output [LANES*64 - 1:0] out,
    output [LANES*5 - 1:0] exceptionFlags
);
    genvar lane;
    generate
        for (lane = 0; lane < LANES; lane = lane + 1) begin: div_lane
            LanlFp64DivLaneRecoded divider(
                clock,
                nReset,
                inValid[lane],
                recA[lane*65 +: 65],
                recB[lane*65 +: 65],
                inReady[lane],
                outValid[lane],
                out[lane*64 +: 64],
                exceptionFlags[lane*5 +: 5]
            );
        end
    endgenerate
endmodule

module LanlFp64Portfolio2SSharedRecode1A1M8D(
    input clock,
    input nReset,
    input req0Valid,
    input [1:0] req0Op,
    input [5:0] req0Tag,
    input [63:0] req0A,
    input [63:0] req0B,
    output req0Ready,
    input req1Valid,
    input [1:0] req1Op,
    input [5:0] req1Tag,
    input [63:0] req1A,
    input [63:0] req1B,
    output req1Ready,
    output addOutValid,
    output [5:0] addOutTag,
    output [63:0] addOut,
    output [4:0] addExceptionFlags,
    output mulOutValid,
    output [5:0] mulOutTag,
    output [63:0] mulOut,
    output [4:0] mulExceptionFlags,
    output [7:0] divOutValid,
    output [47:0] divOutTag,
    output [511:0] divOut,
    output [39:0] divExceptionFlags
);
    localparam [1:0] OpAdd = 2'b00;
    localparam [1:0] OpSubtract = 2'b01;
    localparam [1:0] OpMultiply = 2'b10;
    localparam [1:0] OpDivide = 2'b11;

    wire [64:0] req0RecA;
    wire [64:0] req0RecB;
    wire [64:0] req1RecA;
    wire [64:0] req1RecB;
    wire req0AddKind;
    wire req1AddKind;
    wire req0TakesAdd;
    wire req1TakesAdd;
    wire req0TakesMultiply;
    wire req1TakesMultiply;
    wire req0TakesDivider;
    wire req1TakesDivider;
    wire addInValid;
    wire mulInValid;
    wire [7:0] divInReady;
    reg [7:0] divInValid;
    reg [519:0] divRecA;
    reg [519:0] divRecB;
    reg [2:0] roundRobin;
    reg [2:0] selectedDivider0;
    reg [2:0] selectedDivider1;
    reg divider0Found;
    reg divider1Found;
    reg [5:0] addTag;
    reg [5:0] mulTag;
    reg [5:0] dividerTag [0:7];
    integer offset0;
    integer candidate0;
    integer offset1;
    integer candidate1;
    integer secondStart;
    integer lane;

    fNToRecFN#(11, 53) recode0A(req0A, req0RecA);
    fNToRecFN#(11, 53) recode0B(req0B, req0RecB);
    fNToRecFN#(11, 53) recode1A(req1A, req1RecA);
    fNToRecFN#(11, 53) recode1B(req1B, req1RecB);

    always @* begin
        divider0Found = 1'b0;
        selectedDivider0 = roundRobin;
        candidate0 = 0;
        for (offset0 = 0; offset0 < 8; offset0 = offset0 + 1) begin
            candidate0 = (roundRobin + offset0) & 7;
            if (!divider0Found && divInReady[candidate0]) begin
                divider0Found = 1'b1;
                selectedDivider0 = candidate0[2:0];
            end
        end
    end

    assign req0AddKind = req0Op == OpAdd || req0Op == OpSubtract;
    assign req1AddKind = req1Op == OpAdd || req1Op == OpSubtract;
    assign req0Ready = nReset &&
        ((req0Op == OpDivide) ? divider0Found : 1'b1);
    assign req0TakesAdd = req0Valid && req0Ready && req0AddKind;
    assign req0TakesMultiply = req0Valid && req0Ready &&
        req0Op == OpMultiply;
    assign req0TakesDivider = req0Valid && req0Ready &&
        req0Op == OpDivide;

    always @* begin
        divider1Found = 1'b0;
        selectedDivider1 = roundRobin;
        secondStart = roundRobin;
        if (req0TakesDivider) begin
            secondStart = (selectedDivider0 + 1) & 7;
        end
        candidate1 = 0;
        for (offset1 = 0; offset1 < 8; offset1 = offset1 + 1) begin
            candidate1 = (secondStart + offset1) & 7;
            if (!divider1Found && divInReady[candidate1] &&
                !(req0TakesDivider && candidate1 == selectedDivider0)) begin
                divider1Found = 1'b1;
                selectedDivider1 = candidate1[2:0];
            end
        end
    end

    assign req1Ready = nReset &&
        ((req1Op == OpDivide) ? divider1Found :
         (req1AddKind ? !req0TakesAdd : !req0TakesMultiply));
    assign req1TakesAdd = req1Valid && req1Ready && req1AddKind;
    assign req1TakesMultiply = req1Valid && req1Ready &&
        req1Op == OpMultiply;
    assign req1TakesDivider = req1Valid && req1Ready &&
        req1Op == OpDivide;
    assign addInValid = req0TakesAdd || req1TakesAdd;
    assign mulInValid = req0TakesMultiply || req1TakesMultiply;

    always @* begin
        divInValid = 8'b0;
        divRecA = 520'b0;
        divRecB = 520'b0;
        if (req0TakesDivider) begin
            divInValid[selectedDivider0] = 1'b1;
            divRecA[selectedDivider0*65 +: 65] = req0RecA;
            divRecB[selectedDivider0*65 +: 65] = req0RecB;
        end
        if (req1TakesDivider) begin
            divInValid[selectedDivider1] = 1'b1;
            divRecA[selectedDivider1*65 +: 65] = req1RecA;
            divRecB[selectedDivider1*65 +: 65] = req1RecB;
        end
    end

    LanlFp64AddRecoded adder(
        clock, nReset, addInValid,
        req0TakesAdd ? req0Op == OpSubtract : req1Op == OpSubtract,
        req0TakesAdd ? req0RecA : req1RecA,
        req0TakesAdd ? req0RecB : req1RecB,
        addOutValid, addOut, addExceptionFlags
    );
    LanlFp64MulRecoded multiplier(
        clock, nReset, mulInValid,
        req0TakesMultiply ? req0RecA : req1RecA,
        req0TakesMultiply ? req0RecB : req1RecB,
        mulOutValid, mulOut, mulExceptionFlags
    );
    LanlFp64DivReplicatedRecoded#(8) dividers(
        clock, nReset, divInValid, divRecA, divRecB, divInReady,
        divOutValid, divOut, divExceptionFlags
    );

    assign addOutTag = addTag;
    assign mulOutTag = mulTag;
    genvar tagLane;
    generate
        for (tagLane = 0; tagLane < 8; tagLane = tagLane + 1) begin: tags
            assign divOutTag[tagLane*6 +: 6] = dividerTag[tagLane];
        end
    endgenerate

    always @(posedge clock or negedge nReset) begin
        if (!nReset) begin
            roundRobin <= 3'b0;
            addTag <= 6'b0;
            mulTag <= 6'b0;
            for (lane = 0; lane < 8; lane = lane + 1) begin
                dividerTag[lane] <= 6'b0;
            end
        end else begin
            if (req0TakesAdd) begin
                addTag <= req0Tag;
            end else if (req1TakesAdd) begin
                addTag <= req1Tag;
            end
            if (req0TakesMultiply) begin
                mulTag <= req0Tag;
            end else if (req1TakesMultiply) begin
                mulTag <= req1Tag;
            end
            if (req0TakesDivider) begin
                dividerTag[selectedDivider0] <= req0Tag;
            end
            if (req1TakesDivider) begin
                dividerTag[selectedDivider1] <= req1Tag;
                roundRobin <= selectedDivider1 + 3'b1;
            end else if (req0TakesDivider) begin
                roundRobin <= selectedDivider0 + 3'b1;
            end
        end
    end
endmodule
