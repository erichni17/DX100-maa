`timescale 1ns/1ps

module lanl_fp64_portfolio_tb;
    reg clock = 1'b0;
    always #5 clock = ~clock;

    reg nReset = 1'b0;
    reg reqValid = 1'b0;
    reg [1:0] reqOp = 2'b0;
    reg [5:0] reqTag = 6'b0;
    reg [63:0] reqA = 64'b0;
    reg [63:0] reqB = 64'b0;
    wire reqReady;
    wire addOutValid;
    wire [5:0] addOutTag;
    wire [63:0] addOut;
    wire [4:0] addFlags;
    wire mulOutValid;
    wire [5:0] mulOutTag;
    wire [63:0] mulOut;
    wire [4:0] mulFlags;
    wire [7:0] divOutValid;
    wire [47:0] divOutTag;
    wire [511:0] divOut;
    wire [39:0] divFlags;
    reg [7:0] seenDividerTags = 8'b0;
    integer issued;
    integer completed;
    integer lane;
    integer cycles;

    LanlFp64Portfolio1A1M8D dut(
        clock, nReset, reqValid, reqOp, reqTag, reqA, reqB, reqReady,
        addOutValid, addOutTag, addOut, addFlags,
        mulOutValid, mulOutTag, mulOut, mulFlags,
        divOutValid, divOutTag, divOut, divFlags
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

    task drive;
        input [1:0] operation;
        input [5:0] tag;
        input [63:0] a;
        input [63:0] b;
        begin
            @(negedge clock);
            reqOp = operation;
            reqTag = tag;
            reqA = a;
            reqB = b;
            reqValid = 1'b1;
            require(reqReady, "selected operation must be ready");
            @(posedge clock);
            #1;
            reqValid = 1'b0;
        end
    endtask

    initial begin
        repeat (2) @(posedge clock);
        #1 nReset = 1'b1;

        drive(2'b00, 6'd1, 64'h3ff8000000000000,
              64'h4002000000000000);
        require(addOutValid && addOutTag == 6'd1,
                "add completion must preserve its tag");
        require(addOut == 64'h400e000000000000 && addFlags == 5'b0,
                "portfolio add must produce 3.75");

        drive(2'b10, 6'd2, 64'h3ff8000000000000,
              64'h4002000000000000);
        require(mulOutValid && mulOutTag == 6'd2,
                "multiply completion must preserve its tag");
        require(mulOut == 64'h400b000000000000 && mulFlags == 5'b0,
                "portfolio multiply must produce 3.375");

        for (issued = 0; issued < 8; issued = issued + 1) begin
            drive(2'b11, issued + 8, 64'h401c000000000000,
                  64'h4000000000000000);
        end
        @(negedge clock);
        reqOp = 2'b11;
        reqValid = 1'b1;
        require(!reqReady, "ninth divide must see backpressure");
        reqOp = 2'b01;
        #1;
        require(reqReady, "add/subtract must remain available while dividers are full");
        reqTag = 6'd3;
        reqA = 64'h4002000000000000;
        reqB = 64'h3ff8000000000000;
        @(posedge clock);
        #1 reqValid = 1'b0;
        require(addOutValid && addOutTag == 6'd3,
                "subtract completion must preserve its tag");
        require(addOut == 64'h3fe8000000000000 && addFlags == 5'b0,
                "portfolio subtract must produce 0.75");

        completed = 0;
        cycles = 0;
        while (completed < 8 && cycles < 100) begin
            @(posedge clock);
            #1;
            cycles = cycles + 1;
            for (lane = 0; lane < 8; lane = lane + 1) begin
                if (divOutValid[lane]) begin
                    require(divOut[lane*64 +: 64] == 64'h400c000000000000,
                            "portfolio divide must produce 3.5");
                    require(divFlags[lane*5 +: 5] == 5'b0,
                            "exact portfolio divide must raise no flags");
                    require(divOutTag[lane*6 +: 6] >= 6'd8 &&
                            divOutTag[lane*6 +: 6] < 6'd16,
                            "divider completion tag must be in the issued range");
                    require(!seenDividerTags[divOutTag[lane*6 +: 6] - 8],
                            "divider completion tag must be unique");
                    seenDividerTags[divOutTag[lane*6 +: 6] - 8] = 1'b1;
                    completed = completed + 1;
                end
            end
        end
        require(completed == 8 && seenDividerTags == 8'hff,
                "all eight tagged divides must complete");
        $display("LANL_FP64_PORTFOLIO_1A1M8D_SMOKE_PASS");
        $finish(0);
    end
endmodule
