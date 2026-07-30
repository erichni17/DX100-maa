`timescale 1ns/1ps

module lanl_maa_fp64_retirement_overlay_tb;
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
    wire [31:0] completionReadConflictCycles;
    wire [31:0] duplicateAllocationCycles;
    wire [31:0] invalidCompletionCycles;
    wire [31:0] retirementBackpressureCycles;
    wire [31:0] backendCompletionsCaptured;
    wire [31:0] backendCompletionsTransferred;
    wire [31:0] backendCompletionBackpressureCycles;
    wire protocolError;
    wire backendOverflow;
    integer cycles;

    LanlFp64Portfolio2SSharedRecode1A1M8DCompletion2WSplitRetirementOverlay64x4x2 dut(
        .clock(clock),
        .nReset(nReset),
        .configureValid(configureValid),
        .configureReady(configureReady),
        .configureOrdered(configureOrdered),
        .configureHeadTag(configureHeadTag),
        .allocate0Valid(allocate0Valid),
        .allocate0Tag(allocate0Tag),
        .allocate0Ready(allocate0Ready),
        .allocate1Valid(allocate1Valid),
        .allocate1Tag(allocate1Tag),
        .allocate1Ready(allocate1Ready),
        .req0Valid(req0Valid),
        .req0Op(req0Op),
        .req0Tag(req0Tag),
        .req0A(req0A),
        .req0B(req0B),
        .req0Ready(req0Ready),
        .req1Valid(req1Valid),
        .req1Op(req1Op),
        .req1Tag(req1Tag),
        .req1A(req1A),
        .req1B(req1B),
        .req1Ready(req1Ready),
        .retire0Valid(retire0Valid),
        .retire0Tag(retire0Tag),
        .retire0Value(retire0Value),
        .retire0Flags(retire0Flags),
        .retire1Valid(retire1Valid),
        .retire1Tag(retire1Tag),
        .retire1Value(retire1Value),
        .retire1Flags(retire1Flags),
        .retireReady(retireReady),
        .occupancy(occupancy),
        .idle(idle),
        .allocationsAccepted(allocationsAccepted),
        .issuesAccepted(issuesAccepted),
        .completionsAccepted(completionsAccepted),
        .retirementsAccepted(retirementsAccepted),
        .allocationBankConflictCycles(allocationBankConflictCycles),
        .completionBankConflictCycles(completionBankConflictCycles),
        .completionReadConflictCycles(completionReadConflictCycles),
        .duplicateAllocationCycles(duplicateAllocationCycles),
        .invalidCompletionCycles(invalidCompletionCycles),
        .retirementBackpressureCycles(retirementBackpressureCycles),
        .backendCompletionsCaptured(backendCompletionsCaptured),
        .backendCompletionsTransferred(backendCompletionsTransferred),
        .backendCompletionBackpressureCycles(
            backendCompletionBackpressureCycles),
        .protocolError(protocolError),
        .backendOverflow(backendOverflow)
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

    initial begin
        repeat (2) @(posedge clock);
        @(negedge clock);
        nReset = 1'b1;
        configureValid = 1'b1;
        configureOrdered = 1'b1;
        configureHeadTag = 6'd0;
        #1 require(configureReady, "empty integrated table must configure");
        @(posedge clock);
        #1 configureValid = 1'b0;

        @(negedge clock);
        allocate0Valid = 1'b1;
        allocate0Tag = 6'd0;
        allocate1Valid = 1'b1;
        allocate1Tag = 6'd1;
        #1 require(allocate0Ready && allocate1Ready,
                   "integrated pair must allocate");
        @(posedge clock);
        #1;
        allocate0Valid = 1'b0;
        allocate1Valid = 1'b0;

        @(negedge clock);
        req0Valid = 1'b1;
        req0Op = 2'b11;
        req0Tag = 6'd0;
        req0A = 64'h4020000000000000;
        req0B = 64'h4000000000000000;
        req1Valid = 1'b1;
        req1Op = 2'b00;
        req1Tag = 6'd1;
        req1A = 64'h3ff0000000000000;
        req1B = 64'h3ff0000000000000;
        #1 require(req0Ready && req1Ready,
                   "integrated split backend must accept pair");
        @(posedge clock);
        #1;
        req0Valid = 1'b0;
        req1Valid = 1'b0;

        cycles = 0;
        while (completionsAccepted < 1 && cycles < 20) begin
            @(posedge clock);
            #1 cycles = cycles + 1;
        end
        require(completionsAccepted == 1 && !retire0Valid,
                "younger add must remain ordered behind divide");
        cycles = 0;
        while (!(retire0Valid && retire1Valid) && cycles < 100) begin
            @(posedge clock);
            #1 cycles = cycles + 1;
        end
        require(retire0Valid && retire0Tag == 0 &&
                retire0Value == 64'h4010000000000000 && retire0Flags == 0,
                "overlay-retired divide result must be exact");
        require(retire1Valid && retire1Tag == 1 &&
                retire1Value == 64'h4000000000000000 && retire1Flags == 0,
                "overlay-retired add result must be exact");
        repeat (2) begin
            @(posedge clock);
            #1 require(retire0Valid && retire1Valid &&
                    retire0Value == 64'h4010000000000000 &&
                    retire1Value == 64'h4000000000000000,
                    "integrated overlay payload must hold under stall");
        end

        @(negedge clock);
        retireReady = 1'b1;
        @(posedge clock);
        #1 retireReady = 1'b0;
        require(idle && occupancy == 0 && allocationsAccepted == 2 &&
                issuesAccepted == 2 && completionsAccepted == 2 &&
                retirementsAccepted == 2,
                "integrated overlay accounting must close");
        require(backendCompletionsCaptured == 2 &&
                backendCompletionsTransferred == 2,
                "split backend completion accounting must close");
        require(completionReadConflictCycles == 0 &&
                invalidCompletionCycles == 0 && !protocolError &&
                !backendOverflow,
                "valid integrated overlay run must remain fail closed");
        require(retirementBackpressureCycles > 0,
                "integrated overlay must exercise retirement stall");

        $display("LANL_MAA_FP64_RETIREMENT_OVERLAY_64X4X2_SMOKE_PASS");
        $finish(0);
    end
endmodule
