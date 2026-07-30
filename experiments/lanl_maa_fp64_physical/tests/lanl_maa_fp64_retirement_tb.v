`timescale 1ns/1ps

module lanl_maa_fp64_retirement_tb;
    reg clock = 1'b0;
    always #5 clock = ~clock;

    reg nReset = 1'b0;
    reg configureValid = 1'b0;
    wire configureReady;
    reg configureOrdered = 1'b1;
    reg [5:0] configureHeadTag = 6'b0;
    reg allocate0Valid = 1'b0;
    reg [5:0] allocate0Tag = 6'b0;
    wire allocate0Ready;
    reg allocate1Valid = 1'b0;
    reg [5:0] allocate1Tag = 6'b0;
    wire allocate1Ready;
    reg req0Valid = 1'b0;
    reg [1:0] req0Op = 2'b0;
    reg [5:0] req0Tag = 6'b0;
    reg [63:0] req0A = 64'b0;
    reg [63:0] req0B = 64'b0;
    wire req0Ready;
    reg req1Valid = 1'b0;
    reg [1:0] req1Op = 2'b0;
    reg [5:0] req1Tag = 6'b0;
    reg [63:0] req1A = 64'b0;
    reg [63:0] req1B = 64'b0;
    wire req1Ready;
    wire retire0Valid;
    wire [5:0] retire0Tag;
    wire [63:0] retire0Value;
    wire [4:0] retire0Flags;
    wire retire1Valid;
    wire [5:0] retire1Tag;
    wire [63:0] retire1Value;
    wire [4:0] retire1Flags;
    reg retireReady = 1'b0;
    wire [6:0] occupancy;
    wire idle;
    wire [31:0] allocationsAccepted;
    wire [31:0] issuesAccepted;
    wire [31:0] completionsAccepted;
    wire [31:0] retirementsAccepted;
    wire [31:0] allocationBankConflictCycles;
    wire [31:0] completionBankConflictCycles;
    wire [31:0] duplicateAllocationCycles;
    wire [31:0] invalidCompletionCycles;
    wire [31:0] retirementBackpressureCycles;
    wire [31:0] backendCompletionsCaptured;
    wire [31:0] backendCompletionsTransferred;
    wire [31:0] backendCompletionBackpressureCycles;
    wire protocolError;
    wire backendOverflow;
    reg [5:0] heldTag0;
    reg [5:0] heldTag1;
    integer cycles;

    LanlFp64Portfolio2SSharedRecode1A1M8DCompletion2WSplitRetirement64x4x2 dut(
        clock, nReset,
        configureValid, configureReady, configureOrdered, configureHeadTag,
        allocate0Valid, allocate0Tag, allocate0Ready,
        allocate1Valid, allocate1Tag, allocate1Ready,
        req0Valid, req0Op, req0Tag, req0A, req0B, req0Ready,
        req1Valid, req1Op, req1Tag, req1A, req1B, req1Ready,
        retire0Valid, retire0Tag, retire0Value, retire0Flags,
        retire1Valid, retire1Tag, retire1Value, retire1Flags, retireReady,
        occupancy, idle, allocationsAccepted, issuesAccepted,
        completionsAccepted, retirementsAccepted,
        allocationBankConflictCycles, completionBankConflictCycles,
        duplicateAllocationCycles,
        invalidCompletionCycles, retirementBackpressureCycles,
        backendCompletionsCaptured, backendCompletionsTransferred,
        backendCompletionBackpressureCycles, protocolError, backendOverflow
    );

    task require;
        input condition;
        input [8*112 - 1:0] message;
        begin
            if (!condition) begin
                $fatal(1, "FAIL: %0s", message);
            end
        end
    endtask

    task resetDut;
        input ordered;
        input [5:0] headTag;
        begin
            @(negedge clock);
            nReset = 1'b0;
            configureValid = 1'b0;
            allocate0Valid = 1'b0;
            allocate1Valid = 1'b0;
            req0Valid = 1'b0;
            req1Valid = 1'b0;
            retireReady = 1'b0;
            repeat (2) @(posedge clock);
            @(negedge clock);
            nReset = 1'b1;
            configureOrdered = ordered;
            configureHeadTag = headTag;
            configureValid = 1'b1;
            #1 require(configureReady, "empty table must configure");
            @(posedge clock);
            #1 configureValid = 1'b0;
        end
    endtask

    task allocatePair;
        input [5:0] tag0;
        input [5:0] tag1;
        input expectReady0;
        input expectReady1;
        begin
            @(negedge clock);
            allocate0Tag = tag0;
            allocate1Tag = tag1;
            allocate0Valid = 1'b1;
            allocate1Valid = 1'b1;
            #1;
            require(allocate0Ready == expectReady0,
                    "allocation slot zero readiness differs");
            require(allocate1Ready == expectReady1,
                    "allocation slot one readiness differs");
            @(posedge clock);
            #1;
            allocate0Valid = 1'b0;
            allocate1Valid = 1'b0;
        end
    endtask

    task allocateOne;
        input [5:0] tag;
        begin
            @(negedge clock);
            allocate0Tag = tag;
            allocate0Valid = 1'b1;
            #1 require(allocate0Ready, "single free tag must allocate");
            @(posedge clock);
            #1 allocate0Valid = 1'b0;
        end
    endtask

    task issuePair;
        input [1:0] op0;
        input [5:0] tag0;
        input [63:0] a0;
        input [63:0] b0;
        input [1:0] op1;
        input [5:0] tag1;
        input [63:0] a1;
        input [63:0] b1;
        begin
            @(negedge clock);
            req0Valid = 1'b1;
            req0Op = op0;
            req0Tag = tag0;
            req0A = a0;
            req0B = b0;
            req1Valid = 1'b1;
            req1Op = op1;
            req1Tag = tag1;
            req1A = a1;
            req1B = b1;
            #1;
            if (!(req0Ready && req1Ready)) begin
                $display(
                    "ISSUE_PAIR_NOT_READY tag0=%0d ready0=%b eligible0=%b backend0=%b tag1=%0d ready1=%b eligible1=%b backend1=%b",
                    tag0, req0Ready, dut.issue0Eligible,
                    dut.backendReq0Ready, tag1, req1Ready,
                    dut.issue1Eligible, dut.backendReq1Ready);
            end
            require(req0Ready && req1Ready,
                       "allocated nonconflicting pair must issue");
            @(posedge clock);
            #1;
            req0Valid = 1'b0;
            req1Valid = 1'b0;
        end
    endtask

    task acceptRetirement;
        begin
            @(negedge clock);
            retireReady = 1'b1;
            @(posedge clock);
            #1 retireReady = 1'b0;
        end
    endtask

    initial begin
        resetDut(1'b1, 6'd0);
        allocatePair(6'd0, 6'd1, 1'b1, 1'b1);
        require(occupancy == 2 && allocationsAccepted == 2,
                "ordered pair allocation must close");
        issuePair(
            2'b11, 6'd0, 64'h4020000000000000,
            64'h4000000000000000,
            2'b00, 6'd1, 64'h3ff0000000000000,
            64'h3ff0000000000000
        );
        require(issuesAccepted == 2,
                "ordered pair issue accounting must close");

        cycles = 0;
        while (completionsAccepted < 1 && cycles < 20) begin
            @(posedge clock);
            #1 cycles = cycles + 1;
        end
        require(completionsAccepted == 1 && !retire0Valid,
                "younger add must wait for ordered divider head");
        cycles = 0;
        while (!(retire0Valid && retire1Valid) && cycles < 100) begin
            @(posedge clock);
            #1 cycles = cycles + 1;
        end
        require(retire0Valid && retire0Tag == 0 &&
                retire0Value == 64'h4010000000000000 && retire0Flags == 0,
                "ordered head divide must be exact");
        require(retire1Valid && retire1Tag == 1 &&
                retire1Value == 64'h4000000000000000 && retire1Flags == 0,
                "ordered younger add must be exact");
        heldTag0 = retire0Tag;
        heldTag1 = retire1Tag;
        repeat (2) begin
            @(posedge clock);
            #1 require(retire0Valid && retire1Valid &&
                    retire0Tag == heldTag0 && retire1Tag == heldTag1,
                    "stalled ordered retirement identity must remain stable");
        end
        acceptRetirement();
        require(idle && occupancy == 0 && retirementsAccepted == 2,
                "ordered pair must retire atomically and free both tags");
        require(retirementBackpressureCycles > 0,
                "ordered test must exercise retirement backpressure");

        resetDut(1'b0, 6'd2);
        allocatePair(6'd2, 6'd6, 1'b1, 1'b0);
        require(allocationBankConflictCycles == 1 && occupancy == 1,
                "same-bank allocation pair must serialize");
        allocateOne(6'd6);

        @(negedge clock);
        allocate0Tag = 6'd2;
        allocate0Valid = 1'b1;
        #1 require(!allocate0Ready,
                   "duplicate allocated tag must be refused");
        @(posedge clock);
        #1 allocate0Valid = 1'b0;
        require(duplicateAllocationCycles == 1,
                "duplicate allocation must be counted separately");

        @(negedge clock);
        req0Valid = 1'b1;
        req0Op = 2'b00;
        req0Tag = 6'd9;
        req0A = 64'h3ff0000000000000;
        req0B = 64'h3ff0000000000000;
        #1 require(!req0Ready,
                   "unallocated arithmetic tag must be refused");
        req0Valid = 1'b0;

        issuePair(
            2'b00, 6'd2, 64'h3ff0000000000000,
            64'h3ff0000000000000,
            2'b10, 6'd6, 64'h4000000000000000,
            64'h4008000000000000
        );
        @(negedge clock);
        req0Valid = 1'b1;
        req0Op = 2'b00;
        req0Tag = 6'd2;
        #1 require(!req0Ready,
                   "already-issued operation must not issue twice");
        req0Valid = 1'b0;

        cycles = 0;
        while (completionsAccepted < 1 && cycles < 20) begin
            @(posedge clock);
            #1 cycles = cycles + 1;
        end
        require(completionsAccepted == 1,
                "same-bank completion pair must initially write one result");
        require(completionBankConflictCycles > 0,
                "same-bank completion pair must expose serialization");

        require(retire0Valid && !retire1Valid,
                "one completed tag per bank may retire each cycle");
        require(retire0Tag == 6'd2 &&
                retire0Value == 64'h4000000000000000,
                "unordered add result must be exact");
        repeat (2) begin
            @(posedge clock);
            #1 require(retire0Valid && retire0Tag == 6'd2,
                    "stalled unordered retirement must remain stable");
        end
        require(backendCompletionBackpressureCycles > 0,
                "serialized completion must backpressure the split backend");
        acceptRetirement();
        cycles = 0;
        while (completionsAccepted < 2 && cycles < 10) begin
            @(posedge clock);
            #1 cycles = cycles + 1;
        end
        require(completionsAccepted == 2,
                "held same-bank completion must transfer after retirement");
        require(retire0Valid && retire0Tag == 6'd6 &&
                retire0Value == 64'h4018000000000000,
                "serialized multiply result must retire exactly once");
        acceptRetirement();
        require(idle && occupancy == 0 && retirementsAccepted == 2,
                "unordered serialized pair must free both tags");
        require(backendCompletionsCaptured == 2 &&
                backendCompletionsTransferred == 2,
                "backend capture and transfer accounting must close");
        require(invalidCompletionCycles == 0 && !protocolError,
                "valid integration must not raise protocol errors");
        require(!backendOverflow,
                "bounded integration must not overflow completion storage");

        $display("LANL_MAA_FP64_RETIREMENT_64X4X2_SMOKE_PASS");
        $finish(0);
    end
endmodule
