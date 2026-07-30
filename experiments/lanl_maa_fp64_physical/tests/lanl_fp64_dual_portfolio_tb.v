`timescale 1ns/1ps

module lanl_fp64_dual_portfolio_tb;
    reg clock = 1'b0;
    always #5 clock = ~clock;

    reg nReset = 1'b0;
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
    integer pair;
    integer completed;
    integer lane;
    integer cycles;

`ifdef LANL_FP64_DUAL_SHARED_RECODE
    LanlFp64Portfolio2SSharedRecode1A1M8D dut(
`else
    LanlFp64Portfolio2S1A1M8D dut(
`endif
        clock, nReset,
        req0Valid, req0Op, req0Tag, req0A, req0B, req0Ready,
        req1Valid, req1Op, req1Tag, req1A, req1B, req1Ready,
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

    task drivePair;
        input [1:0] operation0;
        input [5:0] tag0;
        input [63:0] a0;
        input [63:0] b0;
        input [1:0] operation1;
        input [5:0] tag1;
        input [63:0] a1;
        input [63:0] b1;
        begin
            @(negedge clock);
            req0Op = operation0;
            req0Tag = tag0;
            req0A = a0;
            req0B = b0;
            req0Valid = 1'b1;
            req1Op = operation1;
            req1Tag = tag1;
            req1A = a1;
            req1B = b1;
            req1Valid = 1'b1;
            #1;
            require(req0Ready && req1Ready,
                    "both nonconflicting slots must be ready");
            @(posedge clock);
            #1;
            req0Valid = 1'b0;
            req1Valid = 1'b0;
        end
    endtask

    initial begin
        repeat (2) @(posedge clock);
        #1 nReset = 1'b1;

        drivePair(
            2'b00, 6'd1, 64'h3ff8000000000000, 64'h4002000000000000,
            2'b10, 6'd2, 64'h3ff8000000000000, 64'h4002000000000000
        );
        require(addOutValid && addOutTag == 6'd1,
                "dual add completion must preserve slot-0 tag");
        require(addOut == 64'h400e000000000000 && addFlags == 5'b0,
                "dual add must produce 3.75");
        require(mulOutValid && mulOutTag == 6'd2,
                "dual multiply completion must preserve slot-1 tag");
        require(mulOut == 64'h400b000000000000 && mulFlags == 5'b0,
                "dual multiply must produce 3.375");

        @(negedge clock);
        req0Op = 2'b00;
        req0Tag = 6'd3;
        req0A = 64'h3ff0000000000000;
        req0B = 64'h3ff0000000000000;
        req0Valid = 1'b1;
        req1Op = 2'b01;
        req1Tag = 6'd4;
        req1A = 64'h4008000000000000;
        req1B = 64'h3ff0000000000000;
        req1Valid = 1'b1;
        #1;
        require(req0Ready && !req1Ready,
                "slot 0 must win a same-adder conflict");
        @(posedge clock);
        #1;
        req0Valid = 1'b0;
        req1Valid = 1'b0;
        require(addOutValid && addOutTag == 6'd3 &&
                addOut == 64'h4000000000000000,
                "only the accepted conflicting add may complete");

        drivePair(
            2'b01, 6'd4, 64'h4008000000000000, 64'h3ff0000000000000,
            2'b10, 6'd5, 64'h4000000000000000, 64'h4000000000000000
        );
        require(addOutValid && addOutTag == 6'd4 &&
                addOut == 64'h4000000000000000,
                "reissued subtract must complete");
        require(mulOutValid && mulOutTag == 6'd5 &&
                mulOut == 64'h4010000000000000,
                "paired multiply must complete");

        for (pair = 0; pair < 4; pair = pair + 1) begin
            drivePair(
                2'b11, pair*2 + 8, 64'h401c000000000000,
                64'h4000000000000000,
                2'b11, pair*2 + 9, 64'h4020000000000000,
                64'h4000000000000000
            );
        end
        @(negedge clock);
        req0Op = 2'b11;
        req1Op = 2'b11;
        req0Valid = 1'b1;
        req1Valid = 1'b1;
        #1;
        require(!req0Ready && !req1Ready,
                "both divide slots must backpressure when all lanes are full");
        req0Valid = 1'b0;
        req1Valid = 1'b0;

        completed = 0;
        cycles = 0;
        while (completed < 8 && cycles < 100) begin
            @(posedge clock);
            #1;
            cycles = cycles + 1;
            for (lane = 0; lane < 8; lane = lane + 1) begin
                if (divOutValid[lane]) begin
                    require(divOutTag[lane*6 +: 6] >= 6'd8 &&
                            divOutTag[lane*6 +: 6] < 6'd16,
                            "dual divider tag must be in the issued range");
                    require(!seenDividerTags[divOutTag[lane*6 +: 6] - 8],
                            "dual divider completion tag must be unique");
                    if ((divOutTag[lane*6 +: 6] & 1) == 0) begin
                        require(divOut[lane*64 +: 64] ==
                                64'h400c000000000000,
                                "even dual divide must produce 3.5");
                    end else begin
                        require(divOut[lane*64 +: 64] ==
                                64'h4010000000000000,
                                "odd dual divide must produce 4.0");
                    end
                    require(divFlags[lane*5 +: 5] == 5'b0,
                            "exact dual divide must raise no flags");
                    seenDividerTags[divOutTag[lane*6 +: 6] - 8] = 1'b1;
                    completed = completed + 1;
                end
            end
        end
        require(completed == 8 && seenDividerTags == 8'hff,
                "all eight dual-issued divides must complete");
`ifdef LANL_FP64_DUAL_SHARED_RECODE
        $display("LANL_FP64_PORTFOLIO_2S_SHARED_RECODE_1A1M8D_SMOKE_PASS");
`else
        $display("LANL_FP64_PORTFOLIO_2S1A1M8D_SMOKE_PASS");
`endif
        $finish(0);
    end
endmodule
