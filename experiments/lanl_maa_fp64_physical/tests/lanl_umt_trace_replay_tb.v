`timescale 1ns/1ps

// Simulation-only P1 transactor.  It observes hierarchical implementation
// state and emits text records; LanlUmtSchedulerShell itself has no trace
// ports and this file is not in a synthesis/cost source list.
module lanl_umt_trace_replay_tb #(
    parameter COMPUTE_TOKENS = 24,
    parameter FP_ISSUE_WIDTH = 1
);
    `include "umt_trace_fixture.vh"

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
    wire [63:0] fpOperationsIssued;
    wire [63:0] dualIssueCycles;
    wire [63:0] fpIssueStallCycles;
    wire [63:0] bankConflictCycles;
    wire [63:0] writebackStallCycles;
    wire [63:0] resultBankStallCycles;
    wire [63:0] dividerNoLaneCycles;
    wire [63:0] stateWitness;
    integer traceSerial = 0;
    integer clearGroup;

    LanlUmtSchedulerShell #(
        .COMPUTE_TOKENS(COMPUTE_TOKENS),
        .FP_ISSUE_WIDTH(FP_ISSUE_WIDTH),
        .ENABLE_STATE_WITNESS(1)
    ) dut (
        .clock(clock), .nReset(nReset),
        .admit0Valid(admit0Valid), .admit0Token(admit0Token),
        .admit0State(admit0State), .admit0Ready(admit0Ready),
        .admit1Valid(admit1Valid), .admit1Token(admit1Token),
        .admit1State(admit1State), .admit1Ready(admit1Ready),
        .addReady(addReady), .multiplyReady(multiplyReady),
        .dividerReady(dividerReady), .descriptorSumArea(descriptorSumArea),
        .descriptorCoefficients(descriptorCoefficients),
        .issue0Valid(issue0Valid), .issue0Token(issue0Token),
        .issue0Operation(issue0Operation), .issue0Unit(issue0Unit),
        .issue0DividerLane(issue0DividerLane), .issue0Bank(issue0Bank),
        .issue0OperandA(issue0OperandA), .issue0OperandB(issue0OperandB),
        .issue1Valid(issue1Valid), .issue1Token(issue1Token),
        .issue1Operation(issue1Operation), .issue1Unit(issue1Unit),
        .issue1DividerLane(issue1DividerLane), .issue1Bank(issue1Bank),
        .issue1OperandA(issue1OperandA), .issue1OperandB(issue1OperandB),
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
        .externalGroup(externalGroup), .externalWriteMask(externalWriteMask),
        .externalWriteData(externalWriteData), .externalReady(externalReady),
        .externalReadData(externalReadData),
        .fpOperationsIssued(fpOperationsIssued),
        .dualIssueCycles(dualIssueCycles), .fpIssueStallCycles(fpIssueStallCycles),
        .bankConflictCycles(bankConflictCycles),
        .writebackStallCycles(writebackStallCycles),
        .resultBankStallCycles(resultBankStallCycles),
        .dividerNoLaneCycles(dividerNoLaneCycles), .stateWitness(stateWitness)
    );

    function [470:0] makeToken;
        input [3:0] phase;
        input [5:0] operation;
        input [5:0] group;
        input [2:0] corner;
        input [3:0] destination;
        input [63:0] operandA;
        input [63:0] operandB;
        reg [470:0] value;
        begin
            value = 471'b0;
            value[3:0] = phase;
            value[9:4] = operation;
            value[15:10] = group;
            value[18:16] = corner;
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

    function [470:0] withReady;
        input [470:0] original;
        input [63:0] ready;
        reg [470:0] value;
        begin value = original; value[86:23] = ready; withReady = value; end
    endfunction

    task require;
        input condition;
        input [8*120-1:0] message;
        begin
            if (!condition) begin $display("FAIL: %0s", message); $finish(1); end
        end
    endtask

    task known64;
        input [63:0] value;
        input [8*80-1:0] name;
        begin require((^value) !== 1'bx, name); end
    endtask

    // A decision-cycle record samples after combinational arbitration and
    // before the active edge commits it.  The state fields are hierarchical
    // simulation observations only; none is a module port.
    task capture;
        input [8*24-1:0] kind;
        begin
            known64(stateWitness, "state witness contains X/Z");
            known64(fpOperationsIssued, "counter contains X/Z");
            known64(dut.token_entry_gen[0].entry.stateReg[63:0],
                    "token state contains X/Z");
            known64(dut.bank0Instance.memory[0][63:0],
                    "bank state contains X/Z");
            $display("UMT_RTL_TRACE serial=%0d cycle=%0d kind=%0s i0=%0d/%0d/%0d i1=%0d/%0d/%0d cr=%0d%0d wb=%0d ext=%0d/%h tok0=%h bank0=%h digest=%h counters=%0d,%0d,%0d,%0d,%0d,%0d,%0d",
                traceSerial, dut.currentCycle, kind,
                issue0Valid, issue0Token, issue0Unit,
                issue1Valid, issue1Token, issue1Unit,
                addCompletionReady, multiplyCompletionReady,
                dut.writebackValid, externalReady, externalReadData[63:0],
                dut.token_entry_gen[0].entry.stateReg[63:0],
                dut.bank0Instance.memory[0][63:0], stateWitness,
                fpOperationsIssued, dualIssueCycles, fpIssueStallCycles,
                bankConflictCycles, writebackStallCycles,
                resultBankStallCycles, dividerNoLaneCycles);
            // Canonical machine-readable projection of this exact pre-edge
            // decision boundary.  The legacy UMT_RTL_TRACE line above is
            // retained for P1 reviewers; consumers of an equivalence claim
            // must use this record and reject missing or malformed fields.
            // All values below are public shell interfaces except the final
            // state block, which is explicitly an observational witness.
            $display("UMT_RTL_PROJECTION {\"schema\":\"lanl-maa-umt-rtl-projection-v1\",\"serial\":%0d,\"cycle\":%0d,\"kind\":\"%0s\",\"presented\":{\"admit0\":{\"valid\":%0d,\"token\":%0d,\"state\":\"%h\"},\"admit1\":{\"valid\":%0d,\"token\":%0d,\"state\":\"%h\"},\"add_ready\":%0d,\"multiply_ready\":%0d,\"divider_ready\":\"%h\",\"descriptor_sum_area\":\"%h\",\"descriptor_coefficients\":\"%h\",\"add_completion\":{\"valid\":%0d,\"token\":%0d,\"result\":\"%h\"},\"multiply_completion\":{\"valid\":%0d,\"token\":%0d,\"result\":\"%h\"},\"divider_completion_valid\":\"%h\",\"divider_completion_token\":\"%h\",\"divider_completion_result\":\"%h\",\"external\":{\"valid\":%0d,\"write\":%0d,\"group\":%0d,\"mask\":\"%h\",\"data\":\"%h\"}},\"accepted\":{\"admit0\":%0d,\"admit1\":%0d,\"add_completion\":%0d,\"multiply_completion\":%0d,\"divider_completion\":\"%h\",\"external\":%0d},\"issues\":[{\"valid\":%0d,\"token\":%0d,\"operation\":%0d,\"unit\":%0d,\"lane\":%0d,\"bank\":%0d,\"operand_a\":\"%h\",\"operand_b\":\"%h\"},{\"valid\":%0d,\"token\":%0d,\"operation\":%0d,\"unit\":%0d,\"lane\":%0d,\"bank\":%0d,\"operand_a\":\"%h\",\"operand_b\":\"%h\"}],\"writeback\":[{\"valid\":%0d,\"token\":%0d,\"row\":%0d,\"mask\":\"%h\",\"data\":\"%h\"},{\"valid\":%0d,\"token\":%0d,\"row\":%0d,\"mask\":\"%h\",\"data\":\"%h\"},{\"valid\":%0d,\"token\":%0d,\"row\":%0d,\"mask\":\"%h\",\"data\":\"%h\"},{\"valid\":%0d,\"token\":%0d,\"row\":%0d,\"mask\":\"%h\",\"data\":\"%h\"}],\"state\":{\"digest\":\"%h\",\"issue_cursor\":%0d,\"token0\":\"%h\",\"bank0_word0\":\"%h\",\"writeback_bank_reservations\":\"%h\",\"issue_bank_reservations\":\"%h\"},\"counters\":{\"fp_operations\":%0d,\"dual_issue\":%0d,\"fp_issue_stall\":%0d,\"bank_conflict\":%0d,\"writeback_stall\":%0d,\"result_bank_stall\":%0d,\"divider_no_lane\":%0d}}",
                traceSerial, dut.currentCycle, kind,
                admit0Valid, admit0Token, admit0State,
                admit1Valid, admit1Token, admit1State,
                addReady, multiplyReady, dividerReady,
                descriptorSumArea, descriptorCoefficients,
                addCompletionValid, addCompletionToken, addCompletionResult,
                multiplyCompletionValid, multiplyCompletionToken, multiplyCompletionResult,
                dividerCompletionValid, dividerCompletionToken, dividerCompletionResult,
                externalValid, externalWrite, externalGroup, externalWriteMask, externalWriteData,
                admit0Ready, admit1Ready, addCompletionReady, multiplyCompletionReady,
                dividerCompletionReady, externalReady,
                issue0Valid, issue0Token, issue0Operation, issue0Unit,
                issue0DividerLane, issue0Bank, issue0OperandA, issue0OperandB,
                issue1Valid, issue1Token, issue1Operation, issue1Unit,
                issue1DividerLane, issue1Bank, issue1OperandA, issue1OperandB,
                dut.writebackValid[0], dut.writebackToken[0], dut.writebackRow[0], dut.writebackMask[0], dut.writebackData[0],
                dut.writebackValid[1], dut.writebackToken[1], dut.writebackRow[1], dut.writebackMask[1], dut.writebackData[1],
                dut.writebackValid[2], dut.writebackToken[2], dut.writebackRow[2], dut.writebackMask[2], dut.writebackData[2],
                dut.writebackValid[3], dut.writebackToken[3], dut.writebackRow[3], dut.writebackMask[3], dut.writebackData[3],
                stateWitness, dut.issueCursor,
                dut.token_entry_gen[0].entry.stateReg,
                dut.bank0Instance.memory[0], dut.retainedWritebackValid,
                dut.retainedSchedulerBankUsed,
                fpOperationsIssued, dualIssueCycles, fpIssueStallCycles,
                bankConflictCycles, writebackStallCycles,
                resultBankStallCycles, dividerNoLaneCycles);
            traceSerial = traceSerial + 1;
        end
    endtask

    task resetDut;
        begin
            @(negedge clock);
            nReset = 1'b0; admit0Valid = 0; admit1Valid = 0;
            addCompletionValid = 0; multiplyCompletionValid = 0;
            dividerCompletionValid = 0; externalValid = 0; externalWrite = 0;
            addReady = 1; multiplyReady = 1; dividerReady = 8'hff;
            repeat (2) @(posedge clock);
            @(negedge clock); nReset = 1'b1; #1;
            // The physical banks intentionally have no reset in the cost
            // shell.  A replay fixture therefore establishes every row via
            // the architectural external ingress before hierarchical X/Z
            // checks or digest capture.
            for (clearGroup = 0; clearGroup < 64; clearGroup = clearGroup + 1) begin
                @(negedge clock);
                externalGroup = clearGroup[5:0]; externalWriteMask = 10'h3ff;
                externalWriteData = 640'b0; externalWrite = 1; externalValid = 1;
                #1; require(externalReady, "initializing external mask rejected");
                @(posedge clock); #1;
            end
            externalValid = 0; externalWrite = 0;
        end
    endtask

    task admitPair;
        input [5:0] tag0; input [470:0] state0;
        input [5:0] tag1; input [470:0] state1;
        begin
            @(negedge clock);
            admit0Token = tag0; admit0State = state0; admit0Valid = 1;
            admit1Token = tag1; admit1State = state1; admit1Valid = 1; #1;
            require(admit0Ready && admit1Ready, "admission rejected or duplicate tag");
            @(posedge clock); #1; admit0Valid = 0; admit1Valid = 0;
        end
    endtask

    task externalStore;
        input [5:0] group; input [9:0] mask; input [639:0] data;
        begin
            @(negedge clock);
            externalGroup = group; externalWriteMask = mask;
            externalWriteData = data; externalWrite = 1; externalValid = 1; #1;
            require(externalReady, "derived external mask store was not accepted");
            @(posedge clock); #1; externalValid = 0; externalWrite = 0;
        end
    endtask

    reg [639:0] pattern;
    reg [470:0] futureToken;
    reg [63:0] addIssueCycle;
    reg [63:0] divideIssueCycle;
    initial begin
        #1;
        require(CXX_P0_TOKEN_CAPACITY == COMPUTE_TOKENS,
                "transacted C++ fixture does not match the RTL cell");
        require(CXX_P0_SHA0 != 0 && CXX_P0_SHA1 != 0 && CXX_P0_SHA2 != 0 &&
                CXX_P0_SHA3 != 0, "C++ fixture semantic digest was not pinned");
        require(CXX_P0_TAG0 < COMPUTE_TOKENS && CXX_P0_TAG1 < COMPUTE_TOKENS,
                "C++ selected token identity outside the RTL capacity");

        // Exact source word positions are represented by the external mask;
        // the reserved metadata words remain unselected.
        resetDut(); pattern = 640'b0;
        pattern[63:0] = 64'h1111222233334444;
        pattern[255:192] = 64'haaaabbbbccccdddd;
        externalStore(0, 10'b0000001001, pattern);
        @(negedge clock); externalGroup = 0; externalWrite = 0; externalValid = 1; #1;
        require(externalReady && externalReadData[63:0] == pattern[63:0] &&
                externalReadData[255:192] == pattern[255:192],
                "masked source ingress/readback differs");
        capture("masked_store"); externalValid = 0;

        // C++ selected tags enter through the actual shell ingress.  The
        // second tag is distinct in the trace fixture; direct tags below
        // additionally exercise a nonzero rotating cursor.
        resetDut();
        admitPair(6'd5, makeToken(1, CXX_P0_TAG0, 0, 0, 0, 1, 2),
                  6'd0, makeToken(0, CXX_P0_TAG1, 0, 0, 0, 0, 0));
        #1; require(issue0Valid && issue0Token == 5, "tag/cursor selection differs");
        capture("tag_cursor");

        resetDut();
        admitPair(0, makeToken(1, 17, 0, 0, 0, 1, 2),
                  1, makeToken(1, 18, 1, 0, 0, 3, 4));
        #1; require(issue0Valid && !(issue1Valid && FP_ISSUE_WIDTH == 2),
                    "same add unit must exclude slot one");
        capture("same_unit");

        resetDut();
        dividerReady = 8'b00000001;
        admitPair(0, makeToken(3, 19, 0, 0, 0, 1, 2),
                  1, makeToken(3, 20, 4, 0, 0, 3, 4));
        #1; require(issue0Valid && issue0Unit == 2 && !issue1Valid,
                    "same lane/bank must exclude slot one");
        capture("same_bank"); dividerReady = 8'hff;

        resetDut();
        admitPair(0, makeToken(9, 21, 0, 0, 0, 0, 64'h2222),
                  1, makeToken(8, 22, 0, 0, 0, 0, 64'h1111));
        #1; require(!externalReady, "writeback must reserve external bank");
        require(dut.writebackValid[0] && dut.writebackToken[0] == 1,
                "edge writeback did not win the first writeback pass");
        capture("edge_before_result");
        @(posedge clock); #1;
        require(dut.writebackValid[0] && dut.writebackToken[0] == 0 &&
                writebackStallCycles == 1,
                "result write did not follow the edge pass in tag order");

        resetDut();
        futureToken = withReady(makeToken(1, 23, 0, 0, 0, 1, 2), 64'h100);
        admitPair(0, futureToken, 1, makeToken(0, 24, 0, 0, 0, 0, 0));
        #1; require(!issue0Valid, "future-ready token issued early");
        capture("future_ready");

        resetDut(); descriptorCoefficients = 0;
        descriptorCoefficients[127:64] = 64'h8000000000000000; // -0.0
        descriptorCoefficients[191:128] = 64'h1003;
        admitPair(0, makeToken(4, 25, 0, 0, 0, 0, 0),
                  1, makeToken(0, 26, 0, 0, 0, 0, 0));
        @(negedge clock); dividerCompletionToken[5:0] = 0;
        dividerCompletionResult[63:0] = 64'h3000;
        dividerCompletionValid[0] = 1; #1;
        require(dividerCompletionReady[0], "divide completion was not ready");
        @(posedge clock); #1; dividerCompletionValid = 0;
        require(issue0Valid && issue0Unit == 1 && issue0OperandA == 64'h1003,
                "+/-0 coefficient skip differs");
        capture("zero_skip");

        // The transactor fixes add latency at exactly one decision cycle.
        // It never accepts an eager completion in the issue cycle.
        resetDut();
        admitPair(0, makeToken(1, 27, 0, 0, 0, 1, 2),
                  1, makeToken(0, 28, 0, 0, 0, 0, 0));
        #1; require(issue0Valid && issue0Token == 0 && issue0Unit == 0,
                    "add issue missing before +1 completion");
        addIssueCycle = dut.currentCycle;
        @(posedge clock); #1;
        @(negedge clock); require(dut.currentCycle == addIssueCycle + 1,
                    "add completion did not wait exactly +1 cycle");
        addCompletionToken = 0; addCompletionResult = 64'h1234;
        addCompletionValid = 1; #1; require(addCompletionReady, "matching completion rejected");
        capture("completion_ready");
        @(posedge clock); #1; addCompletionValid = 0;

        // Divide completion is similarly fixed at issue+64.  The shell
        // receives no completion before that boundary; a full trace driver
        // also withholds a reused lane for 32 cycles before presenting it to
        // another divide (the fixed II), rather than treating a ready bit as
        // an untracked combinational resource.
        resetDut();
        admitPair(0, makeToken(3, 29, 0, 0, 0, 1, 2),
                  1, makeToken(0, 30, 0, 0, 0, 0, 0));
        #1; require(issue0Valid && issue0Unit == 2 && issue0Token == 0,
                    "divide issue missing before +64 completion");
        divideIssueCycle = dut.currentCycle;
        repeat (64) @(posedge clock);
        @(negedge clock); require(dut.currentCycle == divideIssueCycle + 64,
                    "divide completion did not wait exactly +64 cycles");
        dividerCompletionToken[5:0] = 0; dividerCompletionResult[63:0] = 64'h5678;
        dividerCompletionValid[0] = 1; #1;
        require(dividerCompletionReady[0], "issue+64 divide completion rejected");
        capture("divide_plus_64");
        @(posedge clock); #1; dividerCompletionValid = 0;

        // A single externally modeled lane is not recycled until issue+32.
        // This makes the II an explicit transactor rule, rather than an
        // accidental consequence of the shell's otherwise stateless ready
        // vector.
        resetDut(); dividerReady = 8'b00000001;
        admitPair(0, makeToken(3, 31, 0, 0, 0, 1, 2),
                  1, makeToken(3, 32, 1, 0, 0, 3, 4));
        #1; require(issue0Valid && issue0Token == 0 && !issue1Valid,
                    "single divider lane admitted more than one issue");
        divideIssueCycle = dut.currentCycle;
        @(posedge clock); #1; dividerReady = 0;
        repeat (31) @(posedge clock);
        @(negedge clock); require(dut.currentCycle == divideIssueCycle + 32,
                    "divider II driver did not hold lane for 32 cycles");
        dividerReady = 8'b00000001; #1;
        require(issue0Valid && issue0Token == 1,
                "divider lane was not released at fixed II32 boundary");
        capture("divide_ii_32"); dividerReady = 8'hff;

        // A completion only advances a matching wait phase.  A stale tag is
        // rejected combinationally, proving no unconsumed completion can
        // silently mutate a free/different token.
        @(negedge clock); addCompletionToken = 7; addCompletionValid = 1; #1;
        require(!addCompletionReady, "stale completion was consumed");
        addCompletionValid = 0;
        $display("LANL_UMT_TRACE_REPLAY_PASS T%0dW%0d records=%0d",
                 COMPUTE_TOKENS, FP_ISSUE_WIDTH, traceSerial);
        $finish;
    end
endmodule
