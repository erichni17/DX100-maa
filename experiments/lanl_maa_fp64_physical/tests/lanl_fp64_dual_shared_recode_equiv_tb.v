`timescale 1ns/1ps

module lanl_fp64_dual_shared_recode_equiv_tb;
    reg clock = 1'b0;
    always #5 clock = ~clock;

    reg nReset = 1'b0;
    reg req0Valid = 1'b0;
    reg [1:0] req0Op = 2'b0;
    reg [5:0] req0Tag = 6'b0;
    reg [63:0] req0A = 64'b0;
    reg [63:0] req0B = 64'b0;
    reg req1Valid = 1'b0;
    reg [1:0] req1Op = 2'b0;
    reg [5:0] req1Tag = 6'b0;
    reg [63:0] req1A = 64'b0;
    reg [63:0] req1B = 64'b0;

    wire baseReq0Ready;
    wire baseReq1Ready;
    wire baseAddOutValid;
    wire [5:0] baseAddOutTag;
    wire [63:0] baseAddOut;
    wire [4:0] baseAddFlags;
    wire baseMulOutValid;
    wire [5:0] baseMulOutTag;
    wire [63:0] baseMulOut;
    wire [4:0] baseMulFlags;
    wire [7:0] baseDivOutValid;
    wire [47:0] baseDivOutTag;
    wire [511:0] baseDivOut;
    wire [39:0] baseDivFlags;

    wire sharedReq0Ready;
    wire sharedReq1Ready;
    wire sharedAddOutValid;
    wire [5:0] sharedAddOutTag;
    wire [63:0] sharedAddOut;
    wire [4:0] sharedAddFlags;
    wire sharedMulOutValid;
    wire [5:0] sharedMulOutTag;
    wire [63:0] sharedMulOut;
    wire [4:0] sharedMulFlags;
    wire [7:0] sharedDivOutValid;
    wire [47:0] sharedDivOutTag;
    wire [511:0] sharedDivOut;
    wire [39:0] sharedDivFlags;

    integer cycle;
    integer lane;
    integer seed = 32'h5a17c0de;

    LanlFp64Portfolio2S1A1M8D baseline(
        clock, nReset,
        req0Valid, req0Op, req0Tag, req0A, req0B, baseReq0Ready,
        req1Valid, req1Op, req1Tag, req1A, req1B, baseReq1Ready,
        baseAddOutValid, baseAddOutTag, baseAddOut, baseAddFlags,
        baseMulOutValid, baseMulOutTag, baseMulOut, baseMulFlags,
        baseDivOutValid, baseDivOutTag, baseDivOut, baseDivFlags
    );

    LanlFp64Portfolio2SSharedRecode1A1M8D shared(
        clock, nReset,
        req0Valid, req0Op, req0Tag, req0A, req0B, sharedReq0Ready,
        req1Valid, req1Op, req1Tag, req1A, req1B, sharedReq1Ready,
        sharedAddOutValid, sharedAddOutTag, sharedAddOut, sharedAddFlags,
        sharedMulOutValid, sharedMulOutTag, sharedMulOut, sharedMulFlags,
        sharedDivOutValid, sharedDivOutTag, sharedDivOut, sharedDivFlags
    );

    task fail;
        input [8*96 - 1:0] message;
        begin
            $display("FAIL cycle %0d: %0s", cycle, message);
            $finish(1);
        end
    endtask

    task compareOutputs;
        begin
            if (baseReq0Ready !== sharedReq0Ready ||
                baseReq1Ready !== sharedReq1Ready)
                fail("request readiness differs");
            if (baseAddOutValid !== sharedAddOutValid)
                fail("add valid differs");
            if (baseAddOutValid &&
                (baseAddOutTag !== sharedAddOutTag ||
                 baseAddOut !== sharedAddOut ||
                 baseAddFlags !== sharedAddFlags))
                fail("add result differs");
            if (baseMulOutValid !== sharedMulOutValid)
                fail("multiply valid differs");
            if (baseMulOutValid &&
                (baseMulOutTag !== sharedMulOutTag ||
                 baseMulOut !== sharedMulOut ||
                 baseMulFlags !== sharedMulFlags))
                fail("multiply result differs");
            if (baseDivOutValid !== sharedDivOutValid)
                fail("divider valid vector differs");
            for (lane = 0; lane < 8; lane = lane + 1) begin
                if (baseDivOutValid[lane] &&
                    (baseDivOutTag[lane*6 +: 6] !==
                         sharedDivOutTag[lane*6 +: 6] ||
                     baseDivOut[lane*64 +: 64] !==
                         sharedDivOut[lane*64 +: 64] ||
                     baseDivFlags[lane*5 +: 5] !==
                         sharedDivFlags[lane*5 +: 5]))
                    fail("divider result differs");
            end
        end
    endtask

    initial begin
        repeat (2) @(posedge clock);
        #1 nReset = 1'b1;

        for (cycle = 0; cycle < 400; cycle = cycle + 1) begin
            @(negedge clock);
            req0Valid = $random(seed);
            req0Op = $random(seed);
            req0Tag = cycle;
            req0A = {$random(seed), $random(seed)};
            req0B = {$random(seed), $random(seed)};
            req1Valid = $random(seed);
            req1Op = $random(seed);
            req1Tag = cycle + 1;
            req1A = {$random(seed), $random(seed)};
            req1B = {$random(seed), $random(seed)};
            #1 compareOutputs();
            @(posedge clock);
            #1 compareOutputs();
        end

        @(negedge clock);
        req0Valid = 1'b0;
        req1Valid = 1'b0;
        for (cycle = 400; cycle < 520; cycle = cycle + 1) begin
            @(posedge clock);
            #1 compareOutputs();
        end

        $display("LANL_FP64_DUAL_SHARED_RECODE_EQUIV_PASS");
        $finish(0);
    end
endmodule
