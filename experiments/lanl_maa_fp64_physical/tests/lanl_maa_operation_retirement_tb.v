`timescale 1ns/1ps

module lanl_maa_operation_retirement_tb;
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
    reg issue0Valid = 1'b0;
    reg [5:0] issue0Tag = 6'b0;
    wire issue0Eligible;
    reg issue0Commit = 1'b0;
    reg issue1Valid = 1'b0;
    reg [5:0] issue1Tag = 6'b0;
    wire issue1Eligible;
    reg issue1Commit = 1'b0;
    reg completion0Valid = 1'b0;
    wire completion0Ready;
    reg [5:0] completion0Tag = 6'b0;
    reg [63:0] completion0Value = 64'b0;
    reg [4:0] completion0Flags = 5'b0;
    reg completion1Valid = 1'b0;
    wire completion1Ready;
    reg [5:0] completion1Tag = 6'b0;
    reg [63:0] completion1Value = 64'b0;
    reg [4:0] completion1Flags = 5'b0;
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
    wire protocolError;

    LanlMaaOperationRetirement64x4x2 dut(
        clock, nReset,
        configureValid, configureReady, configureOrdered, configureHeadTag,
        allocate0Valid, allocate0Tag, allocate0Ready,
        allocate1Valid, allocate1Tag, allocate1Ready,
        issue0Valid, issue0Tag, issue0Eligible, issue0Commit,
        issue1Valid, issue1Tag, issue1Eligible, issue1Commit,
        completion0Valid, completion0Ready, completion0Tag,
        completion0Value, completion0Flags,
        completion1Valid, completion1Ready, completion1Tag,
        completion1Value, completion1Flags,
        retire0Valid, retire0Tag, retire0Value, retire0Flags,
        retire1Valid, retire1Tag, retire1Value, retire1Flags, retireReady,
        occupancy, idle, allocationsAccepted, issuesAccepted,
        completionsAccepted, retirementsAccepted,
        allocationBankConflictCycles, completionBankConflictCycles,
        duplicateAllocationCycles, invalidCompletionCycles,
        retirementBackpressureCycles, protocolError
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
            issue0Valid = 1'b0;
            issue0Commit = 1'b0;
            issue1Valid = 1'b0;
            issue1Commit = 1'b0;
            completion0Valid = 1'b0;
            completion1Valid = 1'b0;
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
        input ready1;
        begin
            @(negedge clock);
            allocate0Valid = 1'b1;
            allocate0Tag = tag0;
            allocate1Valid = 1'b1;
            allocate1Tag = tag1;
            #1;
            require(allocate0Ready, "first free tag must allocate");
            require(allocate1Ready == ready1,
                    "second allocation readiness differs");
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
            allocate0Valid = 1'b1;
            allocate0Tag = tag;
            #1 require(allocate0Ready, "single free tag must allocate");
            @(posedge clock);
            #1 allocate0Valid = 1'b0;
        end
    endtask

    task commitIssuePair;
        input [5:0] tag0;
        input [5:0] tag1;
        begin
            @(negedge clock);
            issue0Valid = 1'b1;
            issue0Tag = tag0;
            issue0Commit = 1'b1;
            issue1Valid = 1'b1;
            issue1Tag = tag1;
            issue1Commit = 1'b1;
            #1 require(issue0Eligible && issue1Eligible,
                       "allocated pair must be issue eligible");
            @(posedge clock);
            #1;
            issue0Valid = 1'b0;
            issue0Commit = 1'b0;
            issue1Valid = 1'b0;
            issue1Commit = 1'b0;
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
        resetDut(1'b1, 6'd63);
        allocatePair(6'd63, 6'd0, 1'b1);
        commitIssuePair(6'd63, 6'd0);

        @(negedge clock);
        completion1Valid = 1'b1;
        completion1Tag = 6'd0;
        completion1Value = 64'h2222222222222222;
        completion1Flags = 5'd2;
        #1 require(completion1Ready,
                   "younger wrapped completion must be accepted");
        @(posedge clock);
        #1 completion1Valid = 1'b0;
        require(!retire0Valid,
                "ordered wrap must wait for tag 63 before tag 0");

        @(negedge clock);
        completion0Valid = 1'b1;
        completion0Tag = 6'd63;
        completion0Value = 64'h1111111111111111;
        completion0Flags = 5'd1;
        #1 require(completion0Ready,
                   "ordered wrapped head completion must be accepted");
        @(posedge clock);
        #1 completion0Valid = 1'b0;
        require(retire0Valid && retire0Tag == 63 &&
                retire0Value == 64'h1111111111111111 && retire0Flags == 1,
                "ordered wrapped head payload differs");
        require(retire1Valid && retire1Tag == 0 &&
                retire1Value == 64'h2222222222222222 && retire1Flags == 2,
                "ordered wrapped successor payload differs");
        acceptRetirement();
        require(idle && retirementsAccepted == 2,
                "ordered wrap pair must retire exactly once");

        resetDut(1'b0, 6'd2);
        allocatePair(6'd2, 6'd6, 1'b0);
        require(allocationBankConflictCycles == 1,
                "same-bank allocation must expose one conflict");
        allocateOne(6'd6);
        commitIssuePair(6'd2, 6'd6);

        @(negedge clock);
        completion0Valid = 1'b1;
        completion0Tag = 6'd2;
        completion0Value = 64'haaaaaaaaaaaaaaaa;
        completion1Valid = 1'b1;
        completion1Tag = 6'd6;
        completion1Value = 64'hbbbbbbbbbbbbbbbb;
        #1 require(completion0Ready && !completion1Ready,
                   "same-bank completion writes must serialize");
        @(posedge clock);
        #1 completion0Valid = 1'b0;
        require(completionBankConflictCycles == 1 &&
                completionsAccepted == 1,
                "completion conflict accounting must close");
        require(retire0Valid && retire0Tag == 2 &&
                retire0Value == 64'haaaaaaaaaaaaaaaa,
                "first same-bank result must be retained");
        acceptRetirement();
        completion1Valid = 1'b0;
        require(completionsAccepted == 2,
                "held result must transfer with the first retirement");
        require(retire0Valid && retire0Tag == 6 &&
                retire0Value == 64'hbbbbbbbbbbbbbbbb,
                "second same-bank result must retain identity");
        acceptRetirement();
        require(idle && completionsAccepted == 2 &&
                retirementsAccepted == 2,
                "serialized table traffic must close exactly once");

        resetDut(1'b0, 6'd0);
        @(negedge clock);
        completion0Valid = 1'b1;
        completion0Tag = 6'd7;
        completion0Value = 64'hdeadbeef;
        #1 require(!completion0Ready,
                   "unallocated completion must not be consumed");
        @(posedge clock);
        #1 completion0Valid = 1'b0;
        require(protocolError && invalidCompletionCycles == 1,
                "unallocated completion must fail closed and count");

        $display("LANL_MAA_OPERATION_RETIREMENT_64X4X2_SMOKE_PASS");
        $finish(0);
    end
endmodule
