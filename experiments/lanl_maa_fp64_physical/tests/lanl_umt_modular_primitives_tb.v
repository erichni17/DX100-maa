`timescale 1ns/1ps

module lanl_umt_modular_primitives_tb;
    reg clock = 1'b0;
    always #5 clock = ~clock;
    reg nReset = 1'b0;

    reg admitValid = 1'b0;
    reg [470:0] admitState = 471'b0;
    reg issueValid = 1'b0;
    reg [9:0] completionReady = 10'b0;
    reg [59:0] completionToken = 60'b0;
    reg [639:0] completionResult = 640'b0;
    reg [3:0] writebackValid = 4'b0;
    reg [23:0] writebackToken = 24'b0;
    wire [470:0] tokenState;

    reg [3:0] selectorGrantable = 4'b0;
    reg [3:0] selectorBankBlocked = 4'b0;
    reg [3:0] selectorDividerBlocked = 4'b0;
    reg [5:0] selectorCursor = 6'b0;
    wire selectorValid;
    wire [5:0] selectorIndex;
    wire selectorSawBank;
    wire selectorSawDivider;

    reg [3:0] bankReadRow = 4'b0;
    reg bankWriteValid = 1'b0;
    reg [3:0] bankWriteRow = 4'b0;
    reg [9:0] bankWriteMask = 10'b0;
    reg [639:0] bankWriteData = 640'b0;
    wire [639:0] bankReadData;

    LanlUmtTokenEntry token(
        .clock(clock), .nReset(nReset), .tokenIndex(6'd3),
        .currentCycle(64'd9), .coefficientActive(28'b0),
        .admit0Valid(admitValid), .admit0Token(6'd3),
        .admit0State(admitState), .admit1Valid(1'b0),
        .admit1Token(6'b0), .admit1State(471'b0),
        .issue0Valid(issueValid), .issue0Token(6'd3),
        .issue1Valid(1'b0), .issue1Token(6'b0),
        .completionReady(completionReady),
        .completionToken(completionToken),
        .completionResult(completionResult),
        .writebackValid(writebackValid),
        .writebackToken(writebackToken), .state(tokenState));

    LanlUmtRotatingPriority #(.COMPUTE_TOKENS(4)) selector(
        .cursor(selectorCursor), .grantable(selectorGrantable),
        .bankBlocked(selectorBankBlocked),
        .dividerBlocked(selectorDividerBlocked),
        .valid(selectorValid), .index(selectorIndex),
        .sawBankBlocked(selectorSawBank),
        .sawDividerBlocked(selectorSawDivider));

    LanlUmtBank16x640 bank(
        .clock(clock), .readRow(bankReadRow), .readData(bankReadData),
        .writeValid(bankWriteValid), .writeRow(bankWriteRow),
        .writeMask(bankWriteMask), .writeData(bankWriteData));

    task require;
        input condition;
        input [8*96 - 1:0] message;
        begin
            if (!condition) begin
                $display("FAIL: %0s", message);
                $finish(1);
            end
        end
    endtask

    initial begin
        repeat (2) @(posedge clock);
        @(negedge clock);
        nReset = 1'b1;

        selectorCursor = 6'd2;
        selectorGrantable = 4'b1010;
        selectorBankBlocked = 4'b0100;
        selectorDividerBlocked = 4'b0100;
        #1;
        require(selectorValid && selectorIndex == 3 &&
                selectorSawBank && selectorSawDivider,
                "rotating bitmap selection or pre-grant flags differ");

        admitState = 471'b0;
        admitState[3:0] = 4'd1;
        admitState[9:4] = 6'd55;
        admitValid = 1'b1;
        @(posedge clock);
        #1;
        admitValid = 1'b0;
        require(tokenState[3:0] == 1 && tokenState[9:4] == 55,
                "fixed-index token admission differs");
        issueValid = 1'b1;
        @(posedge clock);
        #1;
        issueValid = 1'b0;
        require(tokenState[3:0] == 2 && tokenState[9:4] == 55,
                "fixed-index token issue or operation preservation differs");
        completionToken[5:0] = 6'd3;
        completionResult[63:0] = 64'h1234;
        completionReady[0] = 1'b1;
        @(posedge clock);
        #1;
        completionReady = 10'b0;
        require(tokenState[3:0] == 3 &&
                tokenState[214:151] == 64'h1234,
                "token-local completion transition differs");
        writebackToken[5:0] = 6'd3;
        writebackValid[0] = 1'b1;
        @(posedge clock);
        #1;
        writebackValid = 4'b0;
        require(tokenState == 471'b0,
                "fixed-index result writeback clear differs");

        bankWriteRow = 4'd7;
        bankWriteMask = 10'b1000000001;
        bankWriteData[63:0] = 64'haaaa;
        bankWriteData[639:576] = 64'hbbbb;
        bankWriteValid = 1'b1;
        @(posedge clock);
        #1;
        bankWriteValid = 1'b0;
        bankReadRow = 4'd7;
        #1;
        require(bankReadData[63:0] == 64'haaaa &&
                bankReadData[639:576] == 64'hbbbb,
                "independent masked bank readback differs");

        $display("LANL_UMT_MODULAR_PRIMITIVES_PASS");
        $finish(0);
    end
endmodule
