`timescale 1ns/1ps

module lanl_umt_state_witness_tb;
    reg clock = 1'b0;
    always #5 clock = ~clock;

    reg nReset = 1'b0;
    reg admit0Valid = 1'b0;
    reg [5:0] admit0Token = 6'b0;
    reg [470:0] admit0State = 471'b0;
    wire admit0Ready;
    reg externalValid = 1'b0;
    reg externalWrite = 1'b0;
    reg [5:0] externalGroup = 6'b0;
    reg [9:0] externalWriteMask = 10'b0;
    reg [639:0] externalWriteData = 640'b0;
    wire externalReady;
    wire [63:0] stateWitness;

    integer bankIndex;
    integer rowIndex;
    reg [639:0] writeData;
    reg [63:0] expectedWitness;

    LanlUmtSchedulerShell #(
        .COMPUTE_TOKENS(24),
        .FP_ISSUE_WIDTH(1),
        .ENABLE_STATE_WITNESS(1)
    ) dut(
        .clock(clock), .nReset(nReset),
        .admit0Valid(admit0Valid), .admit0Token(admit0Token),
        .admit0State(admit0State), .admit0Ready(admit0Ready),
        .admit1Valid(1'b0), .admit1Token(6'b0),
        .admit1State(471'b0),
        .addReady(1'b1), .multiplyReady(1'b1),
        .dividerReady(8'hff), .descriptorSumArea(512'b0),
        .descriptorCoefficients(1792'b0),
        .addCompletionValid(1'b0), .addCompletionToken(6'b0),
        .addCompletionResult(64'b0),
        .multiplyCompletionValid(1'b0),
        .multiplyCompletionToken(6'b0),
        .multiplyCompletionResult(64'b0),
        .dividerCompletionValid(8'b0),
        .dividerCompletionToken(48'b0),
        .dividerCompletionResult(512'b0),
        .externalValid(externalValid), .externalWrite(externalWrite),
        .externalGroup(externalGroup),
        .externalWriteMask(externalWriteMask),
        .externalWriteData(externalWriteData),
        .externalReady(externalReady), .stateWitness(stateWitness)
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

    task writeBank;
        input [1:0] bank;
        input [3:0] row;
        input [9:0] mask;
        input [639:0] data;
        begin
            @(negedge clock);
            externalGroup = {row, bank};
            externalWriteMask = mask;
            externalWriteData = data;
            externalWrite = 1'b1;
            externalValid = 1'b1;
            #1;
            require(externalReady,
                    "witness bank initialization/write was not accepted");
            @(posedge clock);
            #1;
            externalValid = 1'b0;
            externalWrite = 1'b0;
            externalWriteMask = 10'b0;
            externalWriteData = 640'b0;
        end
    endtask

    initial begin
        // Freeze the three parent control classes so their parity can be
        // driven independently of the normal cycle counters.
        force dut.functionalState = 656'b0;
        force dut.bankSchedulerState = 283'b0;
        force dut.instrumentationState = 1169'b0;

        repeat (2) @(posedge clock);
        @(negedge clock);
        nReset = 1'b1;

        // Banks deliberately have no reset. Make all 64 rows known before
        // examining the composite witness.
        for (bankIndex = 0; bankIndex < 4;
             bankIndex = bankIndex + 1) begin
            for (rowIndex = 0; rowIndex < 16;
                 rowIndex = rowIndex + 1)
                writeBank(bankIndex[1:0], rowIndex[3:0],
                          10'h3ff, 640'b0);
        end
        #1;
        require(dut.bank0StateParity == 16'b0 &&
                dut.bank1StateParity == 16'b0 &&
                dut.bank2StateParity == 16'b0 &&
                dut.bank3StateParity == 16'b0,
                "zeroed bank row parities differ");
        require(stateWitness === 64'b0,
                "zero token/bank/control witness differs");

        // An odd token-5 state maps to bit 5.
        @(negedge clock);
        admit0Token = 6'd5;
        admit0State = 471'b0;
        admit0State[100] = 1'b1;
        admit0Valid = 1'b1;
        #1;
        require(admit0Ready, "token-5 witness admission was not accepted");
        @(posedge clock);
        #1;
        admit0Valid = 1'b0;
        expectedWitness = 64'b0;
        expectedWitness[5] = 1'b1;
        require(stateWitness === expectedWitness,
                "token parity was not mapped by token index");

        // Each write masks exactly one odd-parity lane. A second odd bit is
        // placed in an unmasked lane and must not affect row parity.
        writeData = 640'b0;
        writeData[0] = 1'b1;
        writeData[64] = 1'b1;
        writeBank(2'd0, 4'd5, 10'b0000000001, writeData);
        require(dut.bank0StateParity == 16'h0020,
                "bank-0 row-5 parity export differs");
        require(stateWitness === 64'b0,
                "bank-0 row parity did not XOR token-5 parity");

        writeData = 640'b0;
        writeData[128] = 1'b1;
        writeData[192] = 1'b1;
        writeBank(2'd1, 4'd4, 10'b0000000100, writeData);
        expectedWitness[20] = 1'b1;
        expectedWitness[5] = 1'b0;
        require(dut.bank1StateParity == 16'h0010 &&
                stateWitness === expectedWitness,
                "bank-1 row parity did not map to bits 16-31");

        writeData = 640'b0;
        writeData[256] = 1'b1;
        writeData[320] = 1'b1;
        writeBank(2'd2, 4'd6, 10'b0000010000, writeData);
        expectedWitness[38] = 1'b1;
        require(dut.bank2StateParity == 16'h0040 &&
                stateWitness === expectedWitness,
                "bank-2 row parity did not map to bits 32-47");

        writeData = 640'b0;
        writeData[384] = 1'b1;
        writeData[448] = 1'b1;
        writeBank(2'd3, 4'd12, 10'b0001000000, writeData);
        expectedWitness[60] = 1'b1;
        require(stateWitness === expectedWitness,
                "bank-3 row-12 parity did not map to bit 60");
        force dut.functionalState = {{655{1'b0}}, 1'b1};
        #1;
        expectedWitness[60] = 1'b0;
        require(stateWitness === expectedWitness,
                "functional parity did not XOR bit 60");

        writeData = 640'b0;
        writeData[512] = 1'b1;
        writeData[576] = 1'b1;
        writeBank(2'd3, 4'd13, 10'b0100000000, writeData);
        expectedWitness[61] = 1'b1;
        require(stateWitness === expectedWitness,
                "bank-3 row-13 parity did not map to bit 61");
        force dut.bankSchedulerState = {{282{1'b0}}, 1'b1};
        #1;
        expectedWitness[61] = 1'b0;
        require(stateWitness === expectedWitness,
                "bank-scheduler parity did not XOR bit 61");

        writeData = 640'b0;
        writeData[576] = 1'b1;
        writeData[0] = 1'b1;
        writeBank(2'd3, 4'd14, 10'b1000000000, writeData);
        expectedWitness[62] = 1'b1;
        require(dut.bank3StateParity == 16'h7000 &&
                stateWitness === expectedWitness,
                "bank-3 row-14 parity did not map to bit 62");
        force dut.instrumentationState = {{1168{1'b0}}, 1'b1};
        #1;
        expectedWitness[62] = 1'b0;
        require(stateWitness === expectedWitness,
                "instrumentation parity did not XOR bit 62");

        require(stateWitness === ((64'b1 << 20) | (64'b1 << 38)),
                "final composite token/bank/control witness differs");
        $display("LANL_UMT_STATE_WITNESS_PASS");
        $finish(0);
    end
endmodule
