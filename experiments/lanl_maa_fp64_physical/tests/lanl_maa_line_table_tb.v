`timescale 1ns/1ps

module lanl_maa_line_table_tb;
    reg clock = 1'b0;
    always #5 clock = ~clock;

    reg nReset = 1'b0;
    reg issue0Valid = 1'b0;
    reg [41:0] issue0Line = 42'b0;
    reg [5:0] issue0Slot = 6'b0;
    wire issue0Ready;
    wire issue0Merged;
    reg issue1Valid = 1'b0;
    reg [41:0] issue1Line = 42'b0;
    reg [5:0] issue1Slot = 6'b0;
    wire issue1Ready;
    wire issue1Merged;
    wire requestValid;
    reg requestReady = 1'b0;
    wire [41:0] requestLine;
    wire [20:0] requestToken;
    reg responseValid = 1'b0;
    reg [20:0] responseToken = 21'b0;
    wire responseReady;
    wire staleResponse;
    wire completionValid;
    reg completionReady = 1'b0;
    wire [5:0] completionSlot;
    wire [31:0] acceptedSlots;
    wire [31:0] mergedSlots;
    wire [31:0] bankConflictCycles;
    wire [31:0] tableWouldBlockCycles;
    wire [31:0] addressBusyCycles;
    wire [31:0] duplicateIssueCycles;
    wire [31:0] lineRequests;
    wire [31:0] staleResponses;
    wire [31:0] completionAcks;
    reg [20:0] savedToken;
    integer line;

    LanlMaaLineTable32x4 dut(
        .clock(clock),
        .nReset(nReset),
        .issue0Valid(issue0Valid),
        .issue0Line(issue0Line),
        .issue0Slot(issue0Slot),
        .issue0Ready(issue0Ready),
        .issue0Merged(issue0Merged),
        .issue1Valid(issue1Valid),
        .issue1Line(issue1Line),
        .issue1Slot(issue1Slot),
        .issue1Ready(issue1Ready),
        .issue1Merged(issue1Merged),
        .requestValid(requestValid),
        .requestReady(requestReady),
        .requestLine(requestLine),
        .requestToken(requestToken),
        .responseValid(responseValid),
        .responseToken(responseToken),
        .responseReady(responseReady),
        .staleResponse(staleResponse),
        .completionValid(completionValid),
        .completionReady(completionReady),
        .completionSlot(completionSlot),
        .acceptedSlots(acceptedSlots),
        .mergedSlots(mergedSlots),
        .bankConflictCycles(bankConflictCycles),
        .tableWouldBlockCycles(tableWouldBlockCycles),
        .addressBusyCycles(addressBusyCycles),
        .duplicateIssueCycles(duplicateIssueCycles),
        .lineRequests(lineRequests),
        .staleResponses(staleResponses),
        .completionAcks(completionAcks)
    );

    task require;
        input condition;
        input [8*112 - 1:0] message;
        begin
            if (!condition) begin
                $display("FAIL: %0s", message);
                $finish(1);
            end
        end
    endtask

    task resetDut;
        begin
            @(negedge clock);
            nReset = 1'b0;
            issue0Valid = 1'b0;
            issue1Valid = 1'b0;
            requestReady = 1'b0;
            responseValid = 1'b0;
            completionReady = 1'b0;
            repeat (2) @(posedge clock);
            @(negedge clock);
            nReset = 1'b1;
        end
    endtask

    task issuePair;
        input valid0;
        input [41:0] line0;
        input [5:0] slot0;
        input expectReady0;
        input expectMerged0;
        input valid1;
        input [41:0] line1;
        input [5:0] slot1;
        input expectReady1;
        input expectMerged1;
        begin
            @(negedge clock);
            issue0Valid = valid0;
            issue0Line = line0;
            issue0Slot = slot0;
            issue1Valid = valid1;
            issue1Line = line1;
            issue1Slot = slot1;
            #1;
            if ((valid0 && (issue0Ready !== expectReady0 ||
                            issue0Merged !== expectMerged0)) ||
                (valid1 && (issue1Ready !== expectReady1 ||
                            issue1Merged !== expectMerged1))) begin
                $display(
                    "ISSUE_MISMATCH t=%0t v0=%b line0=%0d slot0=%0d ready0=%b/%b merged0=%b/%b v1=%b line1=%0d slot1=%0d ready1=%b/%b merged1=%b/%b",
                    $time, valid0, line0, slot0, issue0Ready, expectReady0,
                    issue0Merged, expectMerged0, valid1, line1, slot1,
                    issue1Ready, expectReady1, issue1Merged, expectMerged1);
            end
            require(!valid0 || issue0Ready == expectReady0,
                    "slot 0 readiness differs");
            require(!valid0 || issue0Merged == expectMerged0,
                    "slot 0 merge indication differs");
            require(!valid1 || issue1Ready == expectReady1,
                    "slot 1 readiness differs");
            require(!valid1 || issue1Merged == expectMerged1,
                    "slot 1 merge indication differs");
            @(posedge clock);
            #1;
            issue0Valid = 1'b0;
            issue1Valid = 1'b0;
        end
    endtask

    task acceptRequest;
        input [41:0] expectedLine;
        begin
            @(negedge clock);
            require(requestValid, "line request must be retained");
            require(requestLine == expectedLine,
                    "line request address differs");
            savedToken = requestToken;
            requestReady = 1'b1;
            @(posedge clock);
            #1;
            requestReady = 1'b0;
        end
    endtask

    task returnResponse;
        input [20:0] token;
        begin
            @(negedge clock);
            responseToken = token;
            responseValid = 1'b1;
            require(responseReady, "response path must remain ready");
            @(posedge clock);
            #1;
            responseValid = 1'b0;
        end
    endtask

    task acceptCompletion;
        input [5:0] expectedSlot;
        begin
            @(negedge clock);
            require(completionValid, "completion must be retained");
            require(completionSlot == expectedSlot,
                    "completion slot differs");
            completionReady = 1'b1;
            @(posedge clock);
            #1;
            completionReady = 1'b0;
        end
    endtask

    initial begin
        resetDut();

        issuePair(1'b1, 42'd9, 6'd0, 1'b1, 1'b0,
                  1'b1, 42'd9, 6'd1, 1'b1, 1'b1);
        require(acceptedSlots == 2 && mergedSlots == 1,
                "same-line pair accounting differs");
        require(requestValid && requestLine == 42'd9,
                "same-line pair must allocate one request");
        savedToken = requestToken;
        repeat (2) begin
            @(posedge clock);
            #1;
            require(requestValid && requestLine == 42'd9 &&
                    requestToken == savedToken,
                    "stalled request identity must remain stable");
        end
        acceptRequest(42'd9);
        require(lineRequests == 1, "one physical request must issue");

        issuePair(1'b1, 42'd9, 6'd2, 1'b1, 1'b1,
                  1'b0, 42'd0, 6'd0, 1'b1, 1'b0);
        require(acceptedSlots == 3 && mergedSlots == 2,
                "in-flight merge accounting differs");

        issuePair(1'b1, 42'd9, 6'd0, 1'b0, 1'b0,
                  1'b0, 42'd0, 6'd0, 1'b1, 1'b0);
        require(duplicateIssueCycles == 1 && tableWouldBlockCycles == 0,
                "duplicate waiter must not masquerade as table pressure");

        returnResponse(savedToken);
        require(completionValid && completionSlot == 6'd0,
                "response must expose the first waiter");

        issuePair(1'b1, 42'd9, 6'd3, 1'b0, 1'b0,
                  1'b0, 42'd0, 6'd0, 1'b1, 1'b0);
        require(addressBusyCycles == 1,
                "draining line must report address pressure");

        repeat (2) begin
            @(posedge clock);
            #1;
            require(completionValid && completionSlot == 6'd0,
                    "stalled completion identity must remain stable");
        end
        acceptCompletion(6'd0);
        acceptCompletion(6'd1);
        acceptCompletion(6'd2);
        require(completionAcks == 3,
                "every same-line waiter must acknowledge");
        require(!completionValid, "entry must free after its final waiter");

        returnResponse(savedToken);
        require(staleResponse && staleResponses == 1,
                "late response must fail closed");

        resetDut();
        issuePair(1'b1, 42'd0, 6'd0, 1'b1, 1'b0,
                  1'b1, 42'd4, 6'd1, 1'b0, 1'b0);
        require(bankConflictCycles == 1 && acceptedSlots == 1,
                "distinct same-bank lines must serialize");
        issuePair(1'b0, 42'd0, 6'd0, 1'b1, 1'b0,
                  1'b1, 42'd1, 6'd1, 1'b1, 1'b0);
        require(acceptedSlots == 2,
                "slot 1 must use an idle distinct bank");

        resetDut();
        for (line = 0; line < 8; line = line + 1) begin
            issuePair(1'b1, line * 4, line, 1'b1, 1'b0,
                      1'b0, 42'd0, 6'd0, 1'b1, 1'b0);
        end
        issuePair(1'b1, 42'd32, 6'd8, 1'b0, 1'b0,
                  1'b0, 42'd0, 6'd0, 1'b1, 1'b0);
        require(acceptedSlots == 8 && tableWouldBlockCycles == 1,
                "full target bank must not borrow another bank");

        $display("LANL_MAA_LINE_TABLE_32X4_SMOKE_PASS");
        $finish(0);
    end
endmodule
