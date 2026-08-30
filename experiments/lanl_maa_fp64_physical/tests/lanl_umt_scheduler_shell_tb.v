`timescale 1ns/1ps

module lanl_umt_scheduler_shell_tb;
    reg clock = 1'b0;
    always #5 clock = ~clock;

    reg nReset = 1'b0;
    reg admit0Valid = 1'b0;
    reg [5:0] admit0Token = 6'b0;
    reg [470:0] admit0State = 471'b0;
    wire admit0Ready;
    reg admit1Valid = 1'b0;
    reg [5:0] admit1Token = 6'b0;
    reg [470:0] admit1State = 471'b0;
    wire admit1Ready;
    reg addReady = 1'b1;
    reg multiplyReady = 1'b1;
    reg [7:0] dividerReady = 8'hff;
    reg [511:0] descriptorSumArea = 512'b0;
    reg [1791:0] descriptorCoefficients = 1792'b0;
    wire issue0Valid;
    wire [5:0] issue0Token;
    wire [5:0] issue0Operation;
    wire [1:0] issue0Unit;
    wire [2:0] issue0DividerLane;
    wire [1:0] issue0Bank;
    wire [63:0] issue0OperandA;
    wire [63:0] issue0OperandB;
    wire issue1Valid;
    wire [5:0] issue1Token;
    wire [5:0] issue1Operation;
    wire [1:0] issue1Unit;
    wire [2:0] issue1DividerLane;
    wire [1:0] issue1Bank;
    wire [63:0] issue1OperandA;
    wire [63:0] issue1OperandB;
    reg addCompletionValid = 1'b0;
    reg [5:0] addCompletionToken = 6'b0;
    reg [63:0] addCompletionResult = 64'b0;
    wire addCompletionReady;
    reg multiplyCompletionValid = 1'b0;
    reg [5:0] multiplyCompletionToken = 6'b0;
    reg [63:0] multiplyCompletionResult = 64'b0;
    wire multiplyCompletionReady;
    reg [7:0] dividerCompletionValid = 8'b0;
    reg [47:0] dividerCompletionToken = 48'b0;
    reg [511:0] dividerCompletionResult = 512'b0;
    wire [7:0] dividerCompletionReady;
    reg externalValid = 1'b0;
    reg externalWrite = 1'b0;
    reg [5:0] externalGroup = 6'b0;
    reg [9:0] externalWriteMask = 10'b0;
    reg [639:0] externalWriteData = 640'b0;
    wire externalReady;
    wire [639:0] externalReadData;
    wire [31:0] t24w2Tokens;
    wire [31:0] t24w2Width;
    wire [31:0] t24w2TokenBits;
    wire [31:0] t24w2PhysicalBits;
    wire [31:0] t24w2FunctionalBits;
    wire [31:0] t24w2BankBits;
    wire [31:0] t24w2InstrumentBits;
    wire [31:0] t24w2PersistentBits;
    wire [31:0] t24w2Candidates;
    wire [31:0] t24w2RouteBits;
    wire [63:0] fpOperationsIssued;
    wire [63:0] dualIssueCycles;
    wire [63:0] fpIssueStallCycles;
    wire [63:0] bankConflictCycles;
    wire [63:0] writebackStallCycles;
    wire [63:0] resultBankStallCycles;
    wire [63:0] dividerNoLaneCycles;

    reg w1Admit0Valid = 1'b0;
    reg [5:0] w1Admit0Token = 6'b0;
    reg [470:0] w1Admit0State = 471'b0;
    wire w1Admit0Ready;
    reg w1Admit1Valid = 1'b0;
    reg [5:0] w1Admit1Token = 6'b0;
    reg [470:0] w1Admit1State = 471'b0;
    wire w1Admit1Ready;
    wire w1Issue0Valid;
    wire [5:0] w1Issue0Token;
    wire w1Issue1Valid;
    wire [31:0] t24w1Tokens;
    wire [31:0] t24w1Width;
    wire [31:0] t24w1PersistentBits;
    wire [31:0] t24w1Candidates;
    wire [31:0] t24w1RouteBits;
    wire [63:0] w1FpOperationsIssued;
    wire [63:0] w1DualIssueCycles;
    wire [63:0] w1BankConflictCycles;

    wire [31:0] t32w1Tokens;
    wire [31:0] t32w1Width;
    wire [31:0] t32w1TokenBits;
    wire [31:0] t32w1PhysicalBits;
    wire [31:0] t32w1FunctionalBits;
    wire [31:0] t32w1BankBits;
    wire [31:0] t32w1InstrumentBits;
    wire [31:0] t32w1PersistentBits;
    wire [31:0] t32w1Candidates;
    wire [31:0] t32w1RouteBits;
    wire t32w1Issue0Valid;
    wire t32w1Issue1Valid;
    wire [63:0] t32w1DualIssueCycles;
    wire [31:0] t32w2Tokens;
    wire [31:0] t32w2Width;
    wire [31:0] t32w2PersistentBits;
    wire [31:0] t32w2Candidates;
    wire [31:0] t32w2RouteBits;
    wire t32w2Issue0Valid;
    wire t32w2Issue1Valid;
    wire [63:0] t32w2DualIssueCycles;

    LanlUmtSchedulerShellT24W2 dut(
        .clock(clock), .nReset(nReset),
        .admit0Valid(admit0Valid), .admit0Token(admit0Token),
        .admit0State(admit0State), .admit0Ready(admit0Ready),
        .admit1Valid(admit1Valid), .admit1Token(admit1Token),
        .admit1State(admit1State), .admit1Ready(admit1Ready),
        .addReady(addReady), .multiplyReady(multiplyReady),
        .dividerReady(dividerReady),
        .descriptorSumArea(descriptorSumArea),
        .descriptorCoefficients(descriptorCoefficients),
        .issue0Valid(issue0Valid),
        .issue0Token(issue0Token), .issue0Operation(issue0Operation),
        .issue0Unit(issue0Unit),
        .issue0DividerLane(issue0DividerLane), .issue0Bank(issue0Bank),
        .issue0OperandA(issue0OperandA), .issue0OperandB(issue0OperandB),
        .issue1Valid(issue1Valid), .issue1Token(issue1Token),
        .issue1Operation(issue1Operation),
        .issue1Unit(issue1Unit), .issue1DividerLane(issue1DividerLane),
        .issue1Bank(issue1Bank), .issue1OperandA(issue1OperandA),
        .issue1OperandB(issue1OperandB),
        .addCompletionValid(addCompletionValid),
        .addCompletionToken(addCompletionToken),
        .addCompletionResult(addCompletionResult),
        .addCompletionReady(addCompletionReady),
        .multiplyCompletionValid(multiplyCompletionValid),
        .multiplyCompletionToken(multiplyCompletionToken),
        .multiplyCompletionResult(multiplyCompletionResult),
        .multiplyCompletionReady(multiplyCompletionReady),
        .dividerCompletionValid(dividerCompletionValid),
        .dividerCompletionToken(dividerCompletionToken),
        .dividerCompletionResult(dividerCompletionResult),
        .dividerCompletionReady(dividerCompletionReady),
        .externalValid(externalValid), .externalWrite(externalWrite),
        .externalGroup(externalGroup),
        .externalWriteMask(externalWriteMask),
        .externalWriteData(externalWriteData),
        .externalReady(externalReady), .externalReadData(externalReadData),
        .configuredTokens(t24w2Tokens),
        .configuredIssueWidth(t24w2Width),
        .tokenLogicalBits(t24w2TokenBits),
        .physicalBankBits(t24w2PhysicalBits),
        .functionalControlBits(t24w2FunctionalBits),
        .bankSchedulerBits(t24w2BankBits),
        .instrumentationBits(t24w2InstrumentBits),
        .persistentBits(t24w2PersistentBits),
        .selectorCandidates(t24w2Candidates),
        .operandRouteBits(t24w2RouteBits),
        .fpOperationsIssued(fpOperationsIssued),
        .dualIssueCycles(dualIssueCycles),
        .fpIssueStallCycles(fpIssueStallCycles),
        .bankConflictCycles(bankConflictCycles),
        .writebackStallCycles(writebackStallCycles),
        .resultBankStallCycles(resultBankStallCycles),
        .dividerNoLaneCycles(dividerNoLaneCycles)
    );

    LanlUmtSchedulerShellT24W1 w1(
        .clock(clock), .nReset(nReset),
        .admit0Valid(w1Admit0Valid), .admit0Token(w1Admit0Token),
        .admit0State(w1Admit0State), .admit0Ready(w1Admit0Ready),
        .admit1Valid(w1Admit1Valid), .admit1Token(w1Admit1Token),
        .admit1State(w1Admit1State), .admit1Ready(w1Admit1Ready),
        .addReady(1'b1), .multiplyReady(1'b1), .dividerReady(8'hff),
        .descriptorSumArea(descriptorSumArea),
        .descriptorCoefficients(descriptorCoefficients),
        .issue0Valid(w1Issue0Valid), .issue0Token(w1Issue0Token),
        .issue1Valid(w1Issue1Valid), .addCompletionValid(1'b0),
        .addCompletionToken(6'b0), .addCompletionResult(64'b0),
        .multiplyCompletionValid(1'b0), .multiplyCompletionToken(6'b0),
        .multiplyCompletionResult(64'b0),
        .dividerCompletionValid(8'b0), .dividerCompletionToken(48'b0),
        .dividerCompletionResult(512'b0), .externalValid(1'b0),
        .externalWrite(1'b0), .externalGroup(6'b0),
        .externalWriteMask(10'b0), .externalWriteData(640'b0),
        .configuredTokens(t24w1Tokens), .configuredIssueWidth(t24w1Width),
        .persistentBits(t24w1PersistentBits),
        .selectorCandidates(t24w1Candidates),
        .operandRouteBits(t24w1RouteBits),
        .fpOperationsIssued(w1FpOperationsIssued),
        .dualIssueCycles(w1DualIssueCycles),
        .bankConflictCycles(w1BankConflictCycles)
    );

    // The T32 wrappers are elaborated and their fixed identities are checked.
    LanlUmtSchedulerShellT32W1 t32w1(
        .clock(clock), .nReset(nReset),
        .admit0Valid(w1Admit0Valid), .admit0Token(w1Admit0Token),
        .admit0State(w1Admit0State), .admit1Valid(w1Admit1Valid),
        .admit1Token(w1Admit1Token), .admit1State(w1Admit1State),
        .addReady(1'b1), .multiplyReady(1'b1), .dividerReady(8'hff),
        .descriptorSumArea(descriptorSumArea),
        .descriptorCoefficients(descriptorCoefficients),
        .issue0Valid(t32w1Issue0Valid), .issue1Valid(t32w1Issue1Valid),
        .addCompletionValid(1'b0), .addCompletionToken(6'b0),
        .addCompletionResult(64'b0), .multiplyCompletionValid(1'b0),
        .multiplyCompletionToken(6'b0), .multiplyCompletionResult(64'b0),
        .dividerCompletionValid(8'b0), .dividerCompletionToken(48'b0),
        .dividerCompletionResult(512'b0), .externalValid(1'b0),
        .externalWrite(1'b0), .externalGroup(6'b0),
        .externalWriteMask(10'b0), .externalWriteData(640'b0),
        .configuredTokens(t32w1Tokens), .configuredIssueWidth(t32w1Width),
        .tokenLogicalBits(t32w1TokenBits),
        .physicalBankBits(t32w1PhysicalBits),
        .functionalControlBits(t32w1FunctionalBits),
        .bankSchedulerBits(t32w1BankBits),
        .instrumentationBits(t32w1InstrumentBits),
        .persistentBits(t32w1PersistentBits),
        .selectorCandidates(t32w1Candidates),
        .operandRouteBits(t32w1RouteBits),
        .dualIssueCycles(t32w1DualIssueCycles)
    );

    LanlUmtSchedulerShellT32W2 t32w2(
        .clock(clock), .nReset(nReset),
        .admit0Valid(admit0Valid), .admit0Token(admit0Token),
        .admit0State(admit0State), .admit1Valid(admit1Valid),
        .admit1Token(admit1Token), .admit1State(admit1State),
        .addReady(addReady), .multiplyReady(multiplyReady),
        .dividerReady(dividerReady),
        .descriptorSumArea(descriptorSumArea),
        .descriptorCoefficients(descriptorCoefficients),
        .issue0Valid(t32w2Issue0Valid), .issue1Valid(t32w2Issue1Valid),
        .addCompletionValid(addCompletionValid),
        .addCompletionToken(addCompletionToken),
        .addCompletionResult(addCompletionResult),
        .multiplyCompletionValid(multiplyCompletionValid),
        .multiplyCompletionToken(multiplyCompletionToken),
        .multiplyCompletionResult(multiplyCompletionResult),
        .dividerCompletionValid(dividerCompletionValid),
        .dividerCompletionToken(dividerCompletionToken),
        .dividerCompletionResult(dividerCompletionResult),
        .externalValid(externalValid), .externalWrite(externalWrite),
        .externalGroup(externalGroup),
        .externalWriteMask(externalWriteMask),
        .externalWriteData(externalWriteData),
        .configuredTokens(t32w2Tokens), .configuredIssueWidth(t32w2Width),
        .persistentBits(t32w2PersistentBits),
        .selectorCandidates(t32w2Candidates),
        .operandRouteBits(t32w2RouteBits),
        .dualIssueCycles(t32w2DualIssueCycles)
    );

    function [470:0] makeToken;
        input [3:0] phase;
        input [5:0] operation;
        input [5:0] group;
        input [3:0] destination;
        input [63:0] operandA;
        input [63:0] operandB;
        reg [470:0] value;
        begin
            value = 471'b0;
            value[3:0] = phase;
            value[9:4] = operation;
            value[15:10] = group;
            value[22:19] = destination;
            value[86:23] = 64'b0;
            value[150:87] = operandA;
            value[214:151] = operandB;
            value[278:215] = operandA;
            value[342:279] = operandB;
            value[406:343] = operandB;
            value[470:407] = operandB;
            makeToken = value;
        end
    endfunction

    function [470:0] withCornerReady;
        input [470:0] original;
        input [2:0] corner;
        input [63:0] readyCycle;
        reg [470:0] value;
        begin
            value = original;
            value[18:16] = corner;
            value[86:23] = readyCycle;
            withCornerReady = value;
        end
    endfunction

    task require;
        input condition;
        input [8*120 - 1:0] message;
        begin
            if (!condition) begin
                $display("FAIL: %0s", message);
                $finish(1);
            end
        end
    endtask

    task resetDuts;
        begin
            @(negedge clock);
            nReset = 1'b0;
            admit0Valid = 1'b0;
            admit1Valid = 1'b0;
            w1Admit0Valid = 1'b0;
            w1Admit1Valid = 1'b0;
            addCompletionValid = 1'b0;
            multiplyCompletionValid = 1'b0;
            dividerCompletionValid = 8'b0;
            externalValid = 1'b0;
            repeat (2) @(posedge clock);
            @(negedge clock);
            nReset = 1'b1;
            #1;
        end
    endtask

    task admitW2TaggedPair;
        input [5:0] token0;
        input [470:0] state0;
        input [5:0] token1;
        input [470:0] state1;
        begin
            @(negedge clock);
            admit0Token = token0;
            admit0State = state0;
            admit0Valid = 1'b1;
            admit1Token = token1;
            admit1State = state1;
            admit1Valid = 1'b1;
            #1;
            require(admit0Ready && admit1Ready,
                    "two distinct free tokens must admit");
            @(posedge clock);
            #1;
            admit0Valid = 1'b0;
            admit1Valid = 1'b0;
        end
    endtask

    task admitW2Pair;
        input [470:0] state0;
        input [470:0] state1;
        begin
            admitW2TaggedPair(6'd0, state0, 6'd1, state1);
        end
    endtask

    task writeExternal;
        input [5:0] group;
        input [9:0] mask;
        input [639:0] data;
        begin
            @(negedge clock);
            externalGroup = group;
            externalWriteMask = mask;
            externalWriteData = data;
            externalWrite = 1'b1;
            externalValid = 1'b1;
            #1;
            require(externalReady, "uncontended external write must grant");
            @(posedge clock);
            #1;
            externalValid = 1'b0;
            externalWrite = 1'b0;
        end
    endtask

    reg [639:0] bankPattern;

    initial begin
        #1;
        require(t24w1Tokens == 24 && t24w1Width == 1 &&
                t24w1PersistentBits == 54372 &&
                t24w1Candidates == 24 && t24w1RouteBits == 64,
                "T24W1 wrapper dimensions differ");
        require(t24w2Tokens == 24 && t24w2Width == 2 &&
                t24w2TokenBits == 471 && t24w2PhysicalBits == 40960 &&
                t24w2FunctionalBits == 656 && t24w2BankBits == 283 &&
                t24w2InstrumentBits == 1169 &&
                t24w2PersistentBits == 54372 &&
                t24w2Candidates == 48 && t24w2RouteBits == 128,
                "T24W2 wrapper dimensions differ");
        require(t32w1Tokens == 32 && t32w1Width == 1 &&
                t32w1TokenBits == 471 && t32w1PhysicalBits == 40960 &&
                t32w1FunctionalBits == 657 && t32w1BankBits == 283 &&
                t32w1InstrumentBits == 1170 &&
                t32w1PersistentBits == 58142 &&
                t32w1Candidates == 32 && t32w1RouteBits == 64,
                "T32W1 wrapper dimensions differ");
        require(t32w2Tokens == 32 && t32w2Width == 2 &&
                t32w2PersistentBits == 58142 &&
                t32w2Candidates == 64 && t32w2RouteBits == 128,
                "T32W2 wrapper dimensions differ");

        resetDuts();
        bankPattern = 640'b0;
        bankPattern[63:0] = 64'h1111222233334444;
        bankPattern[639:576] = 64'haaaabbbbccccdddd;
        writeExternal(6'd0, 10'b1000000001, bankPattern);
        @(negedge clock);
        externalGroup = 6'd0;
        externalWrite = 1'b0;
        externalValid = 1'b1;
        #1;
        require(externalReady &&
                externalReadData[63:0] == 64'h1111222233334444 &&
                externalReadData[639:576] == 64'haaaabbbbccccdddd,
                "masked asynchronous bank readback differs");
        externalValid = 1'b0;

        resetDuts();
        descriptorSumArea[63:0] = 64'h1111000011110000;
        descriptorCoefficients[63:0] = 64'h2222000022220000;
        admitW2Pair(
            makeToken(4'd1, 6'd42, 6'd0, 4'd0, 64'h10, 64'h20),
            makeToken(4'd5, 6'd43, 6'd0, 4'd1, 64'h30, 64'h40));
        #1;
        require(issue0Valid && issue1Valid && issue0Unit == 0 &&
                issue1Unit == 1 && issue0Token == 0 && issue1Token == 1,
                "W2 must issue distinct add and multiply units");
        require(issue0Operation == 42 && issue1Operation == 43,
                "engine operation tags must pass through unchanged");
        require(t32w2Issue0Valid && t32w2Issue1Valid,
                "T32W2 dynamic dual issue differs");
        require(issue0OperandA == 64'h1111000011110000 &&
                issue0OperandB == 64'h10 &&
                issue1OperandA == 64'h2222000022220000 &&
                issue1OperandB == 64'h40,
                "descriptor/token operand muxes differ");
        @(posedge clock);
        #1;
        require(fpOperationsIssued == 2 && dualIssueCycles == 1 &&
                t32w2DualIssueCycles == 1,
                "W2 dual-issue accounting differs");

        resetDuts();
        admitW2TaggedPair(
            6'd5,
            makeToken(4'd1, 6'd17, 6'd0, 4'd0, 64'h1, 64'h2),
            6'd0,
            makeToken(4'd0, 6'd18, 6'd0, 4'd0, 64'h0, 64'h0));
        #1;
        require(issue0Valid && issue0Token == 5,
                "rotating selector must find a nonzero first token");
        @(posedge clock);
        #1;
        admitW2TaggedPair(
            6'd2,
            makeToken(4'd1, 6'd19, 6'd0, 4'd0, 64'h3, 64'h4),
            6'd6,
            makeToken(4'd5, 6'd20, 6'd1, 4'd1, 64'h5, 64'h6));
        #1;
        require(issue0Valid && issue1Valid &&
                issue0Token == 6 && issue1Token == 2,
                "slot one must rescan from the updated wrapping cursor");

        resetDuts();
        @(negedge clock);
        w1Admit0Token = 0;
        w1Admit0State =
            makeToken(4'd1, 6'd21, 6'd0, 4'd0, 64'h1, 64'h2);
        w1Admit0Valid = 1'b1;
        w1Admit1Token = 1;
        w1Admit1State =
            makeToken(4'd5, 6'd22, 6'd1, 4'd1, 64'h3, 64'h4);
        w1Admit1Valid = 1'b1;
        #1;
        require(w1Admit0Ready && w1Admit1Ready,
                "W1 admissions must remain width independent");
        @(posedge clock);
        #1;
        w1Admit0Valid = 1'b0;
        w1Admit1Valid = 1'b0;
        require(w1Issue0Valid && !w1Issue1Valid,
                "W1 must expose only issue slot zero");
        require(t32w1Issue0Valid && !t32w1Issue1Valid,
                "T32W1 dynamic issue-width exclusion differs");
        @(posedge clock);
        #1;
        require(w1FpOperationsIssued == 1 && w1DualIssueCycles == 0 &&
                t32w1DualIssueCycles == 0,
                "W1 dual issue must remain exactly zero");

        resetDuts();
        admitW2Pair(
            makeToken(4'd1, 6'd23, 6'd0, 4'd0, 64'h1, 64'h2),
            makeToken(4'd1, 6'd24, 6'd1, 4'd0, 64'h3, 64'h4));
        #1;
        require(issue0Valid && !issue1Valid,
                "same add unit must serialize W2 slot one");

        resetDuts();
        admitW2Pair(
            makeToken(4'd3, 6'd25, 6'd0, 4'd0, 64'h1, 64'h2),
            makeToken(4'd7, 6'd26, 6'd4, 4'd0, 64'h3, 64'h4));
        #1;
        require(issue0Valid && issue0Unit == 2 && !issue1Valid,
                "same-bank divide and edge add must serialize");
        @(posedge clock);
        #1;
        require(bankConflictCycles == 1,
                "same-bank scheduler conflict must count once");

        resetDuts();
        admitW2Pair(
            makeToken(4'd8, 6'd27, 6'd0, 4'd0,
                      64'h0, 64'ha0a0a0a0a0a0a0a0),
            makeToken(4'd8, 6'd28, 6'd0, 4'd0,
                      64'h0, 64'hb1b1b1b1b1b1b1b1));
        externalGroup = 6'd0;
        externalWrite = 1'b0;
        externalValid = 1'b1;
        #1;
        require(!externalReady,
                "pending writeback must reserve its bank");
        @(posedge clock);
        #1;
        require(writebackStallCycles == 1 && resultBankStallCycles == 1,
                "writeback collision accounting differs");
        require(!externalReady,
                "higher-token writeback must remain pending");
        @(posedge clock);
        #1;
        require(externalReady &&
                externalReadData[63:0] == 64'hb1b1b1b1b1b1b1b1,
                "ascending-token writeback order differs");
        externalValid = 1'b0;

        resetDuts();
        admitW2Pair(
            makeToken(4'd9, 6'd36, 6'd0, 4'd0,
                      64'h0, 64'hc2c2c2c2c2c2c2c2),
            makeToken(4'd8, 6'd37, 6'd0, 4'd0,
                      64'h0, 64'hd3d3d3d3d3d3d3d3));
        @(posedge clock);
        #1;
        require(writebackStallCycles == 1,
                "cross-phase writeback collision must count once");
        @(posedge clock);
        #1;
        externalGroup = 6'd0;
        externalWrite = 1'b0;
        externalValid = 1'b1;
        #1;
        require(externalReady &&
                externalReadData[63:0] == 64'hc2c2c2c2c2c2c2c2,
                "edge-write pass must precede lower-tag result write");
        externalValid = 1'b0;

        resetDuts();
        admitW2Pair(
            makeToken(4'd9, 6'd46, 6'd0, 4'd0, 64'h0, 64'h6000),
            makeToken(4'd0, 6'd47, 6'd0, 4'd0, 64'h0, 64'h0));
        @(posedge clock);
        #1;
        require(fpIssueStallCycles == 0,
                "lone final result drain must not count an issue stall");

        resetDuts();
        @(negedge clock);
        dut.shell.tokenState[0] =
            makeToken(4'd9, 6'd48, 6'd0, 4'd0, 64'h0, 64'h6100);
        dut.shell.tokenState[1] =
            makeToken(4'd9, 6'd49, 6'd1, 4'd0, 64'h0, 64'h6101);
        dut.shell.tokenState[2] =
            makeToken(4'd9, 6'd50, 6'd2, 4'd0, 64'h0, 64'h6102);
        dut.shell.tokenState[3] =
            makeToken(4'd9, 6'd51, 6'd3, 4'd0, 64'h0, 64'h6103);
        #1;
        @(posedge clock);
        #1;
        require(fpIssueStallCycles == 0 &&
                dut.shell.tokenState[0][3:0] == 0 &&
                dut.shell.tokenState[1][3:0] == 0 &&
                dut.shell.tokenState[2][3:0] == 0 &&
                dut.shell.tokenState[3][3:0] == 0,
                "all-bank final result drain must not count a stall");

        resetDuts();
        admitW2Pair(
            makeToken(4'd9, 6'd29, 6'd0, 4'd0, 64'h0, 64'h5555),
            makeToken(4'd3, 6'd30, 6'd4, 4'd0, 64'h0, 64'h0));
        externalGroup = 6'd0;
        externalWrite = 1'b0;
        externalValid = 1'b1;
        #1;
        require(!issue0Valid && !externalReady,
                "writeback must precede scheduler read and external access");
        @(posedge clock);
        #1;
        externalValid = 1'b0;
        require(bankConflictCycles == 1 && resultBankStallCycles == 1,
                "writeback-first scheduler collision must count once");
        #1;
        require(issue0Valid && issue0Token == 1,
                "blocked scheduler token must issue after writeback");

        resetDuts();
        @(negedge clock);
        w1Admit0Token = 2;
        w1Admit0State =
            makeToken(4'd8, 6'd38, 6'd0, 4'd0, 64'h0, 64'h11);
        w1Admit0Valid = 1'b1;
        w1Admit1Token = 3;
        w1Admit1State =
            makeToken(4'd8, 6'd39, 6'd0, 4'd0, 64'h0, 64'h22);
        w1Admit1Valid = 1'b1;
        @(posedge clock);
        #1;
        @(negedge clock);
        w1Admit0Token = 0;
        w1Admit0State =
            makeToken(4'd1, 6'd40, 6'd0, 4'd0, 64'h1, 64'h2);
        w1Admit1Token = 1;
        w1Admit1State =
            makeToken(4'd7, 6'd41, 6'd0, 4'd0, 64'h3, 64'h4);
        #1;
        require(w1Admit0Ready && w1Admit1Ready,
                "post-write admissions must target free tags");
        @(posedge clock);
        #1;
        w1Admit0Valid = 1'b0;
        w1Admit1Valid = 1'b0;
        require(w1Issue0Valid && w1Issue0Token == 0,
                "W1 slot zero must grant the first candidate");
        @(posedge clock);
        #1;
        require(w1BankConflictCycles == 0,
                "W1 must not scan/count candidates after slot-zero grant");

        resetDuts();
        dividerReady = 8'b0;
        admitW2Pair(
            makeToken(4'd2, 6'd31, 6'd0, 4'd0, 64'h7, 64'h8),
            makeToken(4'd0, 6'd32, 6'd0, 4'd0, 64'h0, 64'h0));
        @(negedge clock);
        addCompletionToken = 0;
        addCompletionResult = 64'h123456789abcdef0;
        addCompletionValid = 1'b1;
        externalGroup = 6'd0;
        externalValid = 1'b1;
        #1;
        require(addCompletionReady && externalReady,
                "token-local completion must not reserve a bank");
        @(posedge clock);
        #1;
        addCompletionValid = 1'b0;
        externalValid = 1'b0;
        require(!issue0Valid,
                "divide must wait while all lanes are unavailable");
        @(posedge clock);
        #1;
        require(dividerNoLaneCycles == 1 && fpIssueStallCycles == 2,
                "divider-no-lane and active zero-issue accounting differ");
        dividerReady = 8'b00001000;
        #1;
        require(issue0Valid && issue0Unit == 2 &&
                issue0DividerLane == 3 &&
                issue0OperandB == 64'h123456789abcdef0,
                "completion-updated divide request differs");
        dividerReady = 8'hff;

        resetDuts();
        descriptorCoefficients = 1792'b0;
        descriptorCoefficients[127:64] = 64'h8000000000000000;
        descriptorCoefficients[191:128] = 64'h1003;
        descriptorCoefficients[255:192] = 64'h1004;
        bankPattern = 640'b0;
        bankPattern[255:192] = 64'h2003;
        bankPattern[319:256] = 64'h2004;
        writeExternal(6'd0, 10'b0000011000, bankPattern);
        admitW2Pair(
            makeToken(4'd4, 6'd44, 6'd0, 4'd0, 64'h0, 64'h0),
            makeToken(4'd0, 6'd45, 6'd0, 4'd0, 64'h0, 64'h0));
        @(negedge clock);
        dividerCompletionToken[5:0] = 0;
        dividerCompletionResult[63:0] = 64'h3000;
        dividerCompletionValid[0] = 1'b1;
        #1;
        require(dividerCompletionReady[0],
                "divide-wait completion must be accepted");
        @(posedge clock);
        #1;
        dividerCompletionValid = 8'b0;
        require(issue0Valid && issue0Unit == 1 && issue0Operation == 44 &&
                issue0OperandA == 64'h1003 &&
                issue0OperandB == 64'h3000,
                "+0/-0 skip or first multiply request differs");
        @(posedge clock);
        #1;
        @(negedge clock);
        multiplyCompletionToken = 0;
        multiplyCompletionResult = 64'h4001;
        multiplyCompletionValid = 1'b1;
        #1;
        require(multiplyCompletionReady,
                "multiply-wait completion must be accepted");
        @(posedge clock);
        #1;
        multiplyCompletionValid = 1'b0;
        require(issue0Valid && issue0Unit == 0 &&
                issue0OperandA == 64'h2003 &&
                issue0OperandB == 64'h4001,
                "first edge-add request differs");
        @(posedge clock);
        #1;
        @(negedge clock);
        externalGroup = 6'd0;
        externalWrite = 1'b0;
        externalValid = 1'b1;
        #1;
        require(externalReady &&
                externalReadData[255:192] == 64'h2003,
                "delayed edge completion wrote stale data");
        @(posedge clock);
        #1;
        require(externalReady &&
                externalReadData[255:192] == 64'h2003,
                "edge wait must remain bank-idle before completion");
        @(negedge clock);
        addCompletionToken = 0;
        addCompletionResult = 64'h5001;
        addCompletionValid = 1'b1;
        #1;
        require(addCompletionReady && !externalReady,
                "completion-qualified edge write must reserve exact cycle");
        @(posedge clock);
        #1;
        addCompletionValid = 1'b0;
        externalValid = 1'b0;
        require(issue0Valid && issue0Unit == 1 &&
                issue0OperandA == 64'h1004 &&
                issue0OperandB == 64'h3000,
                "inactive edge skip or repeated multiply differs");
        @(posedge clock);
        #1;
        @(negedge clock);
        multiplyCompletionResult = 64'h4003;
        multiplyCompletionValid = 1'b1;
        @(posedge clock);
        #1;
        multiplyCompletionValid = 1'b0;
        require(issue0Valid && issue0Unit == 0 &&
                issue0OperandA == 64'h2004 &&
                issue0OperandB == 64'h4003,
                "second edge-add request differs");
        @(posedge clock);
        #1;
        @(negedge clock);
        addCompletionResult = 64'h5003;
        addCompletionValid = 1'b1;
        @(posedge clock);
        #1;
        addCompletionValid = 1'b0;
        @(posedge clock);
        #1;
        externalGroup = 6'd0;
        externalWrite = 1'b0;
        externalValid = 1'b1;
        #1;
        require(externalReady &&
                externalReadData[63:0] == 64'h3000 &&
                externalReadData[255:192] == 64'h5001 &&
                externalReadData[319:256] == 64'h5003,
                "repeated edge writes or reachable result write differ");
        externalValid = 1'b0;

        resetDuts();
        admitW2Pair(
            withCornerReady(
                makeToken(4'd1, 6'd33, 6'd0, 4'd0, 64'h9, 64'ha),
                3'd0, 64'd5),
            makeToken(4'd0, 6'd0, 6'd0, 4'd0, 64'h0, 64'h0));
        require(!issue0Valid,
                "future-ready token must not issue at admission");
        while (dut.shell.currentCycle < 64'd5) begin
            require(!issue0Valid,
                    "future-ready token issued before its cycle");
            @(posedge clock);
            #1;
        end
        require(issue0Valid && issue0Token == 0,
                "token must issue on its declared ready cycle");

        resetDuts();
        admitW2Pair(
            makeToken(4'd3, 6'd34, 6'd0, 4'd0, 64'h1, 64'h2),
            makeToken(4'd3, 6'd35, 6'd1, 4'd0, 64'h3, 64'h4));
        #1;
        require(issue0Valid && issue1Valid && issue0Unit == 2 &&
                issue1Unit == 2 && issue0DividerLane == 0 &&
                issue1DividerLane == 1,
                "W2 divides must select distinct ready lanes and banks");

        $display("LANL_UMT_SCHEDULER_SHELL_DIRECTED_PASS");
        $finish(0);
    end
endmodule
