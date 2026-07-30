`timescale 1ns/1ps

module lanl_fp64_hardfloat_tb;
    reg clock = 1'b0;
    always #5 clock = ~clock;

    reg nReset = 1'b0;
    reg inValid = 1'b0;
    reg subOp = 1'b0;
    reg [63:0] a = 64'b0;
    reg [63:0] b = 64'b0;
    reg [63:0] c = 64'b0;

    wire addOutValid;
    wire [63:0] addOut;
    wire [4:0] addFlags;
    wire mulOutValid;
    wire [63:0] mulOut;
    wire [4:0] mulFlags;
    wire fmaOutValid;
    wire [63:0] fmaOut;
    wire [4:0] fmaFlags;

    reg divInValid = 1'b0;
    reg [63:0] divA = 64'b0;
    reg [63:0] divB = 64'b0;
    wire divInReady;
    wire divOutValid;
    wire [63:0] divOut;
    wire [4:0] divFlags;

    integer divCycles;

    LanlFp64Add add(
        clock, nReset, inValid, subOp, a, b,
        addOutValid, addOut, addFlags
    );
    LanlFp64Mul mul(
        clock, nReset, inValid, a, b,
        mulOutValid, mulOut, mulFlags
    );
    LanlFp64Fma fma(
        clock, nReset, inValid, a, b, c,
        fmaOutValid, fmaOut, fmaFlags
    );
    LanlFp64Div1 divider(
        clock, nReset, divInValid, divA, divB,
        divInReady, divOutValid, divOut, divFlags
    );

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
        #1 nReset = 1'b1;

        a = 64'h3ff8000000000000;
        b = 64'h4002000000000000;
        c = 64'h3fd0000000000000;
        subOp = 1'b0;
        inValid = 1'b1;
        @(posedge clock);
        #1;
        require(addOutValid && addOut == 64'h400e000000000000,
                "1.5 + 2.25 must equal 3.75");
        require(addFlags == 5'b0, "exact add must raise no flags");
        require(mulOutValid && mulOut == 64'h400b000000000000,
                "1.5 * 2.25 must equal 3.375");
        require(mulFlags == 5'b0, "exact multiply must raise no flags");

        a = 64'h3ff8000000000000;
        b = 64'h4000000000000000;
        c = 64'h3fd0000000000000;
        @(posedge clock);
        #1;
        require(fmaOutValid && fmaOut == 64'h400a000000000000,
                "fma(1.5, 2.0, 0.25) must equal 3.25");
        require(fmaFlags == 5'b0, "exact FMA must raise no flags");

        a = 64'h4002000000000000;
        b = 64'h3ff8000000000000;
        subOp = 1'b1;
        @(posedge clock);
        #1;
        require(addOutValid && addOut == 64'h3fe8000000000000,
                "2.25 - 1.5 must equal 0.75");
        require(addFlags == 5'b0, "exact subtract must raise no flags");
        inValid = 1'b0;

        divA = 64'h401c000000000000;
        divB = 64'h4000000000000000;
        divInValid = 1'b1;
        require(divInReady, "divider must be ready while idle");
        @(posedge clock);
        #1;
        divInValid = 1'b0;
        require(!divInReady, "iterative divider must apply backpressure");

        divCycles = 0;
        while (!divOutValid && divCycles < 80) begin
            @(posedge clock);
            #1 divCycles = divCycles + 1;
        end
        require(divOutValid, "divider must complete within 80 cycles");
        require(divOut == 64'h400c000000000000,
                "7.0 / 2.0 must equal 3.5");
        require(divFlags == 5'b0, "exact divide must raise no flags");
        $display("DIV_LATENCY_AFTER_ACCEPT_CYCLES=%0d", divCycles);
        $display("LANL_FP64_HARDFLOAT_SMOKE_PASS");
        $finish(0);
    end
endmodule
