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

module LanlFp64Portfolio1A1M8D(
    input clock,
    input nReset,
    input reqValid,
    input [1:0] reqOp,
    input [5:0] reqTag,
    input [63:0] reqA,
    input [63:0] reqB,
    output reqReady,
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

    wire addInValid;
    wire mulInValid;
    wire [7:0] divInReady;
    reg [7:0] divInValid;
    wire [511:0] divA;
    wire [511:0] divB;
    reg [2:0] roundRobin;
    reg [2:0] selectedDivider;
    reg dividerFound;
    reg [5:0] addTag;
    reg [5:0] mulTag;
    reg [5:0] dividerTag [0:7];
    integer offset;
    integer candidate;
    integer lane;

    always @* begin
        dividerFound = 1'b0;
        selectedDivider = roundRobin;
        candidate = 0;
        for (offset = 0; offset < 8; offset = offset + 1) begin
            candidate = (roundRobin + offset) & 7;
            if (!dividerFound && divInReady[candidate]) begin
                dividerFound = 1'b1;
                selectedDivider = candidate[2:0];
            end
        end
    end

    assign reqReady = nReset &&
        ((reqOp == OpDivide) ? dividerFound : 1'b1);
    assign addInValid = reqValid && reqReady &&
        (reqOp == OpAdd || reqOp == OpSubtract);
    assign mulInValid = reqValid && reqReady && reqOp == OpMultiply;
    always @* begin
        divInValid = 8'b0;
        if (reqValid && reqReady && reqOp == OpDivide) begin
            divInValid[selectedDivider] = 1'b1;
        end
    end
    assign divA = {8{reqA}};
    assign divB = {8{reqB}};

    LanlFp64Add adder(
        clock, nReset, addInValid, reqOp == OpSubtract, reqA, reqB,
        addOutValid, addOut, addExceptionFlags
    );
    LanlFp64Mul multiplier(
        clock, nReset, mulInValid, reqA, reqB,
        mulOutValid, mulOut, mulExceptionFlags
    );
    LanlFp64DivReplicated#(8) dividers(
        clock, nReset, divInValid, divA, divB, divInReady,
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
            if (addInValid) begin
                addTag <= reqTag;
            end
            if (mulInValid) begin
                mulTag <= reqTag;
            end
            if (reqValid && reqReady && reqOp == OpDivide) begin
                dividerTag[selectedDivider] <= reqTag;
                roundRobin <= selectedDivider + 3'b1;
            end
        end
    end
endmodule

module LanlFp64Portfolio2S1A1M8D(
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
    reg [511:0] divA;
    reg [511:0] divB;
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
        divA = 512'b0;
        divB = 512'b0;
        if (req0TakesDivider) begin
            divInValid[selectedDivider0] = 1'b1;
            divA[selectedDivider0*64 +: 64] = req0A;
            divB[selectedDivider0*64 +: 64] = req0B;
        end
        if (req1TakesDivider) begin
            divInValid[selectedDivider1] = 1'b1;
            divA[selectedDivider1*64 +: 64] = req1A;
            divB[selectedDivider1*64 +: 64] = req1B;
        end
    end

    LanlFp64Add adder(
        clock, nReset, addInValid,
        req0TakesAdd ? req0Op == OpSubtract : req1Op == OpSubtract,
        req0TakesAdd ? req0A : req1A,
        req0TakesAdd ? req0B : req1B,
        addOutValid, addOut, addExceptionFlags
    );
    LanlFp64Mul multiplier(
        clock, nReset, mulInValid,
        req0TakesMultiply ? req0A : req1A,
        req0TakesMultiply ? req0B : req1B,
        mulOutValid, mulOut, mulExceptionFlags
    );
    LanlFp64DivReplicated#(8) dividers(
        clock, nReset, divInValid, divA, divB, divInReady,
        divOutValid, divOut, divExceptionFlags
    );

    assign addOutTag = addTag;
    assign mulOutTag = mulTag;
    genvar tagLane;
    generate
        for (tagLane = 0; tagLane < 8; tagLane = tagLane + 1) begin: dual_tags
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
