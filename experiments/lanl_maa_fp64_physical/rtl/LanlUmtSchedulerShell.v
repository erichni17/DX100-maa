`timescale 1ns/1ps

// Synthesizable standard-cell cost shell for the UMT scheduler and paired
// state store. Arithmetic units remain external. The 4x16x640 memories are
// asynchronous-read register-file models, not characterized SRAM macros.
module LanlUmtSchedulerShell #(
    parameter COMPUTE_TOKENS = 24,
    parameter FP_ISSUE_WIDTH = 1,
    parameter ENABLE_STATE_WITNESS = 0
)(
    input clock,
    input nReset,

    input admit0Valid,
    input [5:0] admit0Token,
    input [470:0] admit0State,
    output reg admit0Ready,
    input admit1Valid,
    input [5:0] admit1Token,
    input [470:0] admit1State,
    output reg admit1Ready,

    input addReady,
    input multiplyReady,
    input [7:0] dividerReady,
    input [511:0] descriptorSumArea,
    input [1791:0] descriptorCoefficients,
    output reg issue0Valid,
    output reg [5:0] issue0Token,
    output reg [5:0] issue0Operation,
    output reg [1:0] issue0Unit,
    output reg [2:0] issue0DividerLane,
    output reg [1:0] issue0Bank,
    output reg [63:0] issue0OperandA,
    output reg [63:0] issue0OperandB,
    output reg issue1Valid,
    output reg [5:0] issue1Token,
    output reg [5:0] issue1Operation,
    output reg [1:0] issue1Unit,
    output reg [2:0] issue1DividerLane,
    output reg [1:0] issue1Bank,
    output reg [63:0] issue1OperandA,
    output reg [63:0] issue1OperandB,

    input addCompletionValid,
    input [5:0] addCompletionToken,
    input [63:0] addCompletionResult,
    output reg addCompletionReady,
    input multiplyCompletionValid,
    input [5:0] multiplyCompletionToken,
    input [63:0] multiplyCompletionResult,
    output reg multiplyCompletionReady,
    input [7:0] dividerCompletionValid,
    input [47:0] dividerCompletionToken,
    input [511:0] dividerCompletionResult,
    output reg [7:0] dividerCompletionReady,

    input externalValid,
    input externalWrite,
    input [5:0] externalGroup,
    input [9:0] externalWriteMask,
    input [639:0] externalWriteData,
    output reg externalReady,
    output reg [639:0] externalReadData,

    output [31:0] configuredTokens,
    output [31:0] configuredIssueWidth,
    output [31:0] tokenLogicalBits,
    output [31:0] physicalBankBits,
    output [31:0] functionalControlBits,
    output [31:0] bankSchedulerBits,
    output [31:0] instrumentationBits,
    output [31:0] persistentBits,
    output [31:0] selectorCandidates,
    output [31:0] operandRouteBits,
    output [63:0] fpOperationsIssued,
    output [63:0] dualIssueCycles,
    output [63:0] fpIssueStallCycles,
    output [63:0] bankConflictCycles,
    output [63:0] writebackStallCycles,
    output [63:0] resultBankStallCycles,
    output [63:0] dividerNoLaneCycles,
    output [63:0] stateWitness
);
    localparam TOKEN_LOGICAL_BITS = 471;
    localparam PHYSICAL_BANK_BITS = 40960;
    localparam FUNCTIONAL_CONTROL_BITS =
        COMPUTE_TOKENS == 24 ? 656 : 657;
    localparam BANK_SCHEDULER_BITS = 283;
    localparam INSTRUMENTATION_BITS =
        COMPUTE_TOKENS == 24 ? 1169 : 1170;
    localparam PERSISTENT_BITS = PHYSICAL_BANK_BITS +
        COMPUTE_TOKENS * TOKEN_LOGICAL_BITS +
        FUNCTIONAL_CONTROL_BITS + BANK_SCHEDULER_BITS +
        INSTRUMENTATION_BITS;

    localparam [3:0] PHASE_FREE = 4'd0;
    localparam [3:0] PHASE_DENOMINATOR_ADD_PENDING = 4'd1;
    localparam [3:0] PHASE_DENOMINATOR_ADD_WAIT = 4'd2;
    localparam [3:0] PHASE_DIVIDE_PENDING = 4'd3;
    localparam [3:0] PHASE_DIVIDE_WAIT = 4'd4;
    localparam [3:0] PHASE_MULTIPLY_PENDING = 4'd5;
    localparam [3:0] PHASE_MULTIPLY_WAIT = 4'd6;
    localparam [3:0] PHASE_EDGE_ADD_PENDING = 4'd7;
    localparam [3:0] PHASE_EDGE_ADD_WAIT = 4'd8;
    localparam [3:0] PHASE_RESULT_WRITE_PENDING = 4'd9;
    localparam [1:0] UNIT_ADD = 2'd0;
    localparam [1:0] UNIT_MULTIPLY = 2'd1;
    localparam [1:0] UNIT_DIVIDE = 2'd2;

    // Token layout matches the independently counted 471-bit floor:
    // phase4, operation6, group6, corner3, destination4, readyCycle64,
    // and six 64-bit values. The operation field remains the engine operation
    // tag; the exact ten-state phase chooses arithmetic behavior.
    wire [COMPUTE_TOKENS * 471 - 1:0] tokenStateFlat;
    wire [470:0] tokenState [0:COMPUTE_TOKENS-1];
    (* keep = "true", umt_state_class = "functional" *)
        reg [FUNCTIONAL_CONTROL_BITS-1:0]
        functionalState;
    (* keep = "true", umt_state_class = "bank_scheduler" *)
        reg [BANK_SCHEDULER_BITS-1:0]
        bankSchedulerState;
    (* keep = "true", umt_state_class = "instrumentation" *)
        reg [INSTRUMENTATION_BITS-1:0]
        instrumentationState;

    reg [3:0] bank0ReadRow;
    reg [3:0] bank1ReadRow;
    reg [3:0] bank2ReadRow;
    reg [3:0] bank3ReadRow;
    wire [639:0] bank0ReadData;
    wire [639:0] bank1ReadData;
    wire [639:0] bank2ReadData;
    wire [639:0] bank3ReadData;
    wire [15:0] bank0StateParity;
    wire [15:0] bank1StateParity;
    wire [15:0] bank2StateParity;
    wire [15:0] bank3StateParity;
    reg bank0WriteValid;
    reg bank1WriteValid;
    reg bank2WriteValid;
    reg bank3WriteValid;
    reg [3:0] bank0WriteRow;
    reg [3:0] bank1WriteRow;
    reg [3:0] bank2WriteRow;
    reg [3:0] bank3WriteRow;
    reg [9:0] bank0WriteMask;
    reg [9:0] bank1WriteMask;
    reg [9:0] bank2WriteMask;
    reg [9:0] bank3WriteMask;
    reg [639:0] bank0WriteData;
    reg [639:0] bank1WriteData;
    reg [639:0] bank2WriteData;
    reg [639:0] bank3WriteData;

    wire [63:0] currentCycle = functionalState[63:0];
    wire [5:0] issueCursor = functionalState[69:64];
    wire [27:0] coefficientActive;
    wire [59:0] completionTokenBus;
    wire [639:0] completionResultBus;
    wire [23:0] writebackTokenBus;

    reg [9:0] completionValid;
    reg [5:0] completionToken [0:9];
    reg [63:0] completionResult [0:9];
    reg [9:0] completionReady;
    reg [3:0] writebackValid;
    reg [5:0] writebackToken [0:3];
    reg [3:0] writebackRow [0:3];
    reg [9:0] writebackMask [0:3];
    reg [639:0] writebackData [0:3];
    reg writebackCollision;

    reg [3:0] schedulerBankUsed;
    reg schedulerBankConflict;
    reg schedulerActive;
    reg schedulerDividerUnavailable;
    reg schedulerAddUsed;
    reg schedulerMultiplyUsed;
    reg [7:0] schedulerDividerUsed;
    reg [639:0] schedulerRow;
    reg [63:0] stateWitnessValue;
    reg [COMPUTE_TOKENS-1:0] slot0Grantable;
    reg [COMPUTE_TOKENS-1:0] slot0BankBlocked;
    reg [COMPUTE_TOKENS-1:0] slot0DividerBlocked;
    reg [COMPUTE_TOKENS-1:0] slot1Grantable;
    reg [COMPUTE_TOKENS-1:0] slot1BankBlocked;
    reg [COMPUTE_TOKENS-1:0] slot1DividerBlocked;
    wire slot0SelectedValid;
    wire [5:0] slot0SelectedIndex;
    wire slot0SawBankBlocked;
    wire slot0SawDividerBlocked;
    wire slot1SelectedValid;
    wire [5:0] slot1SelectedIndex;
    wire slot1SawBankBlocked;
    wire slot1SawDividerBlocked;
    reg [5:0] slot1Cursor;
    reg [470:0] issue0SelectedState;
    reg [470:0] issue1SelectedState;
    reg issue0NeedsBank;
    reg issue1NeedsBank;
    reg [7:0] dividerAvailableAfterSlot0;
    reg [639:0] issue0SelectedRow;
    reg [639:0] issue1SelectedRow;
    reg [470:0] tokenWorkingState;
    reg [3:0] tokenNextDestination;

    integer resetToken;
    integer completionSource;
    integer completionTagScan;
    integer completionBank;
    integer completionTagIndex;
    integer completionWord;
    integer sequentialCompletionSource;
    integer sequentialBank;
    integer schedulerProbe;
    integer schedulerIndex;
    integer schedulerLane;
    integer schedulerUnit;
    integer schedulerNeedsBank;
    integer schedulerWord;
    integer schedulerCoefficient;
    integer tokenMapIndex;
    integer slot1MapIndex;
    integer tokenUpdateIndex;
    integer tokenMapUnit;
    integer tokenMapNeedsBank;
    integer tokenMapLaneAvailable;
    integer issue0LaneFound;
    integer issue1LaneFound;
    integer issue0LaneIndex;
    integer issue1LaneIndex;
    integer issue0Coefficient;
    integer issue1Coefficient;
    integer witnessToken;
    integer writeLane;

    assign configuredTokens = COMPUTE_TOKENS;
    assign configuredIssueWidth = FP_ISSUE_WIDTH;
    assign tokenLogicalBits = TOKEN_LOGICAL_BITS;
    assign physicalBankBits = PHYSICAL_BANK_BITS;
    assign functionalControlBits = FUNCTIONAL_CONTROL_BITS;
    assign bankSchedulerBits = BANK_SCHEDULER_BITS;
    assign instrumentationBits = INSTRUMENTATION_BITS;
    assign persistentBits = PERSISTENT_BITS;
    assign selectorCandidates = COMPUTE_TOKENS * FP_ISSUE_WIDTH;
    assign operandRouteBits = 64 * FP_ISSUE_WIDTH;
    assign fpOperationsIssued = instrumentationState[63:0];
    assign dualIssueCycles = instrumentationState[127:64];
    assign fpIssueStallCycles = instrumentationState[191:128];
    assign bankConflictCycles = instrumentationState[255:192];
    assign writebackStallCycles = instrumentationState[319:256];
    assign resultBankStallCycles = instrumentationState[383:320];
    assign dividerNoLaneCycles = instrumentationState[447:384];
    assign stateWitness = stateWitnessValue;

    genvar coefficientActiveIndex;
    genvar completionBusIndex;
    genvar writebackBusIndex;
    genvar tokenEntryIndex;
    generate
        for (coefficientActiveIndex = 0; coefficientActiveIndex < 28;
             coefficientActiveIndex = coefficientActiveIndex + 1) begin:
                coefficient_active_gen
            assign coefficientActive[coefficientActiveIndex] =
                descriptorCoefficients[
                    coefficientActiveIndex * 64 +: 63] != 63'b0;
        end
        for (completionBusIndex = 0; completionBusIndex < 10;
             completionBusIndex = completionBusIndex + 1) begin:
                completion_bus_gen
            assign completionTokenBus[completionBusIndex * 6 +: 6] =
                completionToken[completionBusIndex];
            assign completionResultBus[completionBusIndex * 64 +: 64] =
                completionResult[completionBusIndex];
        end
        for (writebackBusIndex = 0; writebackBusIndex < 4;
             writebackBusIndex = writebackBusIndex + 1) begin:
                writeback_bus_gen
            assign writebackTokenBus[writebackBusIndex * 6 +: 6] =
                writebackToken[writebackBusIndex];
        end
        for (tokenEntryIndex = 0; tokenEntryIndex < COMPUTE_TOKENS;
             tokenEntryIndex = tokenEntryIndex + 1) begin: token_entry_gen
            assign tokenState[tokenEntryIndex] = tokenStateFlat[
                tokenEntryIndex * 471 +: 471];
            LanlUmtTokenEntry entry(
                .clock(clock),
                .nReset(nReset),
                .tokenIndex(tokenEntryIndex[5:0]),
                .currentCycle(currentCycle),
                .coefficientActive(coefficientActive),
                .admit0Valid(admit0Valid && admit0Ready),
                .admit0Token(admit0Token),
                .admit0State(admit0State),
                .admit1Valid(admit1Valid && admit1Ready),
                .admit1Token(admit1Token),
                .admit1State(admit1State),
                .issue0Valid(issue0Valid),
                .issue0Token(issue0Token),
                .issue1Valid(issue1Valid),
                .issue1Token(issue1Token),
                .completionReady(completionReady),
                .completionToken(completionTokenBus),
                .completionResult(completionResultBus),
                .writebackValid(writebackValid),
                .writebackToken(writebackTokenBus),
                .state(tokenStateFlat[tokenEntryIndex * 471 +: 471])
            );
        end
    endgenerate

    LanlUmtBank16x640 #(
        .ENABLE_STATE_WITNESS(ENABLE_STATE_WITNESS)) bank0Instance(
        .clock(clock), .readRow(bank0ReadRow), .readData(bank0ReadData),
        .writeValid(bank0WriteValid), .writeRow(bank0WriteRow),
        .writeMask(bank0WriteMask), .writeData(bank0WriteData),
        .stateParity(bank0StateParity));
    LanlUmtBank16x640 #(
        .ENABLE_STATE_WITNESS(ENABLE_STATE_WITNESS)) bank1Instance(
        .clock(clock), .readRow(bank1ReadRow), .readData(bank1ReadData),
        .writeValid(bank1WriteValid), .writeRow(bank1WriteRow),
        .writeMask(bank1WriteMask), .writeData(bank1WriteData),
        .stateParity(bank1StateParity));
    LanlUmtBank16x640 #(
        .ENABLE_STATE_WITNESS(ENABLE_STATE_WITNESS)) bank2Instance(
        .clock(clock), .readRow(bank2ReadRow), .readData(bank2ReadData),
        .writeValid(bank2WriteValid), .writeRow(bank2WriteRow),
        .writeMask(bank2WriteMask), .writeData(bank2WriteData),
        .stateParity(bank2StateParity));
    LanlUmtBank16x640 #(
        .ENABLE_STATE_WITNESS(ENABLE_STATE_WITNESS)) bank3Instance(
        .clock(clock), .readRow(bank3ReadRow), .readData(bank3ReadData),
        .writeValid(bank3WriteValid), .writeRow(bank3WriteRow),
        .writeMask(bank3WriteMask), .writeData(bank3WriteData),
        .stateParity(bank3StateParity));

    LanlUmtRotatingPriority #(.COMPUTE_TOKENS(COMPUTE_TOKENS))
        slot0Selector(
            .cursor(issueCursor), .grantable(slot0Grantable),
            .bankBlocked(slot0BankBlocked),
            .dividerBlocked(slot0DividerBlocked),
            .valid(slot0SelectedValid), .index(slot0SelectedIndex),
            .sawBankBlocked(slot0SawBankBlocked),
            .sawDividerBlocked(slot0SawDividerBlocked));
    LanlUmtRotatingPriority #(.COMPUTE_TOKENS(COMPUTE_TOKENS))
        slot1Selector(
            .cursor(slot1Cursor), .grantable(slot1Grantable),
            .bankBlocked(slot1BankBlocked),
            .dividerBlocked(slot1DividerBlocked),
            .valid(slot1SelectedValid), .index(slot1SelectedIndex),
            .sawBankBlocked(slot1SawBankBlocked),
            .sawDividerBlocked(slot1SawDividerBlocked));

    function integer coefficientIndex;
        input [2:0] source;
        input [3:0] destination;
        begin
            coefficientIndex = source * (15 - source) / 2 +
                destination - source - 1;
        end
    endfunction

    function [3:0] nextActiveDestination;
        input [2:0] source;
        input [3:0] startingDestination;
        integer destination;
        integer index;
        reg found;
        begin
            nextActiveDestination = 4'd8;
            found = 1'b0;
            for (destination = 0; destination < 8;
                 destination = destination + 1) begin
                index = 0;
                if (destination > source) begin
                    index = coefficientIndex(source, destination[3:0]);
                    if (!found && destination >= startingDestination &&
                        descriptorCoefficients[index * 64 +: 63] != 63'b0) begin
                        nextActiveDestination = destination[3:0];
                        found = 1'b1;
                    end
                end
            end
        end
    endfunction

    function isIssuePending;
        input [3:0] phase;
        begin
            isIssuePending =
                phase == PHASE_DENOMINATOR_ADD_PENDING ||
                phase == PHASE_DIVIDE_PENDING ||
                phase == PHASE_MULTIPLY_PENDING ||
                phase == PHASE_EDGE_ADD_PENDING;
        end
    endfunction

    function [1:0] issueUnitForPhase;
        input [3:0] phase;
        begin
            if (phase == PHASE_DIVIDE_PENDING)
                issueUnitForPhase = UNIT_DIVIDE;
            else if (phase == PHASE_MULTIPLY_PENDING)
                issueUnitForPhase = UNIT_MULTIPLY;
            else
                issueUnitForPhase = UNIT_ADD;
        end
    endfunction

    function issueNeedsBankForPhase;
        input [3:0] phase;
        begin
            issueNeedsBankForPhase = phase == PHASE_DIVIDE_PENDING ||
                phase == PHASE_EDGE_ADD_PENDING;
        end
    endfunction

    // Full parity fanout is test-only because it would contaminate routed
    // cost. Physical wrappers leave it disabled and rely on keep attributes
    // plus the post-synthesis retained-state audit.
    generate
        if (ENABLE_STATE_WITNESS != 0) begin: full_state_witness
            always @* begin
                stateWitnessValue = 64'b0;
                for (witnessToken = 0; witnessToken < COMPUTE_TOKENS;
                     witnessToken = witnessToken + 1)
                    stateWitnessValue[witnessToken] =
                        ^tokenState[witnessToken];
                stateWitnessValue[15:0] =
                    stateWitnessValue[15:0] ^ bank0StateParity;
                stateWitnessValue[31:16] =
                    stateWitnessValue[31:16] ^ bank1StateParity;
                stateWitnessValue[47:32] =
                    stateWitnessValue[47:32] ^ bank2StateParity;
                stateWitnessValue[63:48] =
                    stateWitnessValue[63:48] ^ bank3StateParity;
                stateWitnessValue[60] =
                    stateWitnessValue[60] ^ ^functionalState;
                stateWitnessValue[61] =
                    stateWitnessValue[61] ^ ^bankSchedulerState;
                stateWitnessValue[62] =
                    stateWitnessValue[62] ^ ^instrumentationState;
            end
        end else begin: bounded_state_witness
            always @* begin
                stateWitnessValue = functionalState[63:0] ^
                    instrumentationState[63:0];
            end
        end
    endgenerate

    // Two independent admissions may populate distinct free token entries.
    always @* begin
        admit0Ready = admit0Token < COMPUTE_TOKENS &&
            tokenState[admit0Token][3:0] == PHASE_FREE;
        admit1Ready = admit1Token < COMPUTE_TOKENS &&
            tokenState[admit1Token][3:0] == PHASE_FREE &&
            !(admit0Valid && admit0Ready && admit0Token == admit1Token);
    end

    // Arithmetic completions update token-local phase/data only. Explicit
    // edge/result write phases arbitrate the banks in ascending token order.
    always @* begin
        completionValid = 10'b0;
        completionValid[0] = addCompletionValid;
        completionValid[1] = multiplyCompletionValid;
        completionToken[0] = addCompletionToken;
        completionToken[1] = multiplyCompletionToken;
        completionResult[0] = addCompletionResult;
        completionResult[1] = multiplyCompletionResult;
        for (completionSource = 0; completionSource < 8;
             completionSource = completionSource + 1) begin
            completionValid[completionSource + 2] =
                dividerCompletionValid[completionSource];
            completionToken[completionSource + 2] =
                dividerCompletionToken[completionSource * 6 +: 6];
            completionResult[completionSource + 2] =
                dividerCompletionResult[completionSource * 64 +: 64];
        end
        completionReady = 10'b0;
        writebackValid = 4'b0;
        writebackCollision = 1'b0;
        for (completionBank = 0; completionBank < 4;
             completionBank = completionBank + 1) begin
            writebackToken[completionBank] = 6'b0;
            writebackRow[completionBank] = 4'b0;
            writebackMask[completionBank] = 10'b0;
            writebackData[completionBank] = 640'b0;
        end

        for (completionSource = 0; completionSource < 10;
             completionSource = completionSource + 1) begin
            completionTagIndex = completionToken[completionSource];
            if (completionValid[completionSource] &&
                completionTagIndex < COMPUTE_TOKENS &&
                tokenState[completionTagIndex][3:0] != PHASE_FREE) begin
                if (completionSource == 0 &&
                    (tokenState[completionTagIndex][3:0] ==
                         PHASE_DENOMINATOR_ADD_WAIT ||
                     tokenState[completionTagIndex][3:0] ==
                         PHASE_EDGE_ADD_WAIT))
                    completionReady[completionSource] = 1'b1;
                else if (completionSource == 1 &&
                         tokenState[completionTagIndex][3:0] ==
                             PHASE_MULTIPLY_WAIT)
                    completionReady[completionSource] = 1'b1;
                else if (completionSource >= 2 &&
                         tokenState[completionTagIndex][3:0] ==
                             PHASE_DIVIDE_WAIT)
                    completionReady[completionSource] = 1'b1;
            end
        end

        // Complete every edge-add write pass before considering result writes.
        for (completionTagScan = 0;
             completionTagScan < COMPUTE_TOKENS;
             completionTagScan = completionTagScan + 1) begin
            if (tokenState[completionTagScan][3:0] ==
                PHASE_EDGE_ADD_WAIT &&
                (tokenState[completionTagScan][86:23] <= currentCycle ||
                 (completionReady[0] &&
                  addCompletionToken == completionTagScan))) begin
                completionWord = tokenState[completionTagScan][22:19];
                completionBank = tokenState[completionTagScan][11:10];
                if (completionWord < 10) begin
                    if (!writebackValid[completionBank]) begin
                        writebackValid[completionBank] = 1'b1;
                        writebackToken[completionBank] =
                            completionTagScan[5:0];
                        writebackRow[completionBank] =
                            tokenState[completionTagScan][15:12];
                        writebackMask[completionBank][completionWord] = 1'b1;
                        writebackData[completionBank]
                            [completionWord * 64 +: 64] =
                                completionReady[0] &&
                                addCompletionToken == completionTagScan ?
                                    addCompletionResult :
                                    tokenState[completionTagScan][470:407];
                    end else begin
                        writebackCollision = 1'b1;
                    end
                end
            end
        end
        for (completionTagScan = 0;
             completionTagScan < COMPUTE_TOKENS;
             completionTagScan = completionTagScan + 1) begin
            if (tokenState[completionTagScan][3:0] ==
                PHASE_RESULT_WRITE_PENDING) begin
                completionWord = tokenState[completionTagScan][18:16];
                completionBank = tokenState[completionTagScan][11:10];
                if (completionWord < 10) begin
                    if (!writebackValid[completionBank]) begin
                        writebackValid[completionBank] = 1'b1;
                        writebackToken[completionBank] =
                            completionTagScan[5:0];
                        writebackRow[completionBank] =
                            tokenState[completionTagScan][15:12];
                        writebackMask[completionBank][completionWord] = 1'b1;
                        writebackData[completionBank]
                            [completionWord * 64 +: 64] =
                                tokenState[completionTagScan][342:279];
                    end else begin
                        writebackCollision = 1'b1;
                    end
                end
            end
        end
        addCompletionReady = completionReady[0];
        multiplyCompletionReady = completionReady[1];
        dividerCompletionReady = completionReady[9:2];
    end

    /* Superseded monolithic selector retained temporarily for review history.
    // Rotating issue selection. Slot one excludes slot zero's token, unit,
    // divider lane, and bank. Writeback reservations are visible first.
    always @* begin
        issue0Valid = 1'b0;
        issue0Token = 6'b0;
        issue0Operation = 6'b0;
        issue0Unit = 2'b0;
        issue0DividerLane = 3'b0;
        issue0Bank = 2'b0;
        issue0OperandA = 64'b0;
        issue0OperandB = 64'b0;
        issue1Valid = 1'b0;
        issue1Token = 6'b0;
        issue1Operation = 6'b0;
        issue1Unit = 2'b0;
        issue1DividerLane = 3'b0;
        issue1Bank = 2'b0;
        issue1OperandA = 64'b0;
        issue1OperandB = 64'b0;
        schedulerBankUsed = 4'b0;
        schedulerBankConflict = 1'b0;
        schedulerActive = 1'b0;
        schedulerDividerUnavailable = 1'b0;
        schedulerAddUsed = 1'b0;
        schedulerMultiplyUsed = 1'b0;
        schedulerDividerUsed = 8'b0;
        schedulerRow = 640'b0;

        for (schedulerProbe = 0; schedulerProbe < COMPUTE_TOKENS;
             schedulerProbe = schedulerProbe + 1) begin
            schedulerIndex = issueCursor + schedulerProbe;
            if (schedulerIndex >= COMPUTE_TOKENS)
                schedulerIndex = schedulerIndex - COMPUTE_TOKENS;
            if (tokenState[schedulerIndex][3:0] != PHASE_FREE &&
                !(tokenState[schedulerIndex][3:0] ==
                      PHASE_RESULT_WRITE_PENDING &&
                  writebackValid[tokenState[schedulerIndex][11:10]] &&
                  writebackToken[tokenState[schedulerIndex][11:10]] ==
                      schedulerIndex))
                schedulerActive = 1'b1;
            if (!issue0Valid &&
                isIssuePending(tokenState[schedulerIndex][3:0]) &&
                tokenState[schedulerIndex][86:23] <= currentCycle) begin
                schedulerUnit = UNIT_ADD;
                schedulerNeedsBank = 0;
                case (tokenState[schedulerIndex][3:0])
                  PHASE_DENOMINATOR_ADD_PENDING: begin
                      schedulerUnit = UNIT_ADD;
                      schedulerNeedsBank = 0;
                  end
                  PHASE_DIVIDE_PENDING: begin
                      schedulerUnit = UNIT_DIVIDE;
                      schedulerNeedsBank = 1;
                  end
                  PHASE_MULTIPLY_PENDING: begin
                      schedulerUnit = UNIT_MULTIPLY;
                      schedulerNeedsBank = 0;
                  end
                  PHASE_EDGE_ADD_PENDING: begin
                      schedulerUnit = UNIT_ADD;
                      schedulerNeedsBank = 1;
                  end
                  default: begin
                      schedulerUnit = 3;
                      schedulerNeedsBank = 0;
                  end
                endcase
                if (schedulerNeedsBank &&
                    writebackValid[tokenState[schedulerIndex][11:10]]) begin
                    schedulerBankConflict = 1'b1;
                end else if (!issue0Valid) begin
                    case (schedulerUnit)
                      UNIT_ADD: begin
                          if (addReady) begin
                              issue0Valid = 1'b1;
                              schedulerAddUsed = 1'b1;
                          end
                      end
                      UNIT_MULTIPLY: begin
                          if (multiplyReady) begin
                              issue0Valid = 1'b1;
                              schedulerMultiplyUsed = 1'b1;
                          end
                      end
                      UNIT_DIVIDE: begin
                          for (schedulerLane = 0; schedulerLane < 8;
                               schedulerLane = schedulerLane + 1) begin
                              if (!issue0Valid && dividerReady[schedulerLane]) begin
                                  issue0Valid = 1'b1;
                                  issue0DividerLane = schedulerLane[2:0];
                                  schedulerDividerUsed[schedulerLane] = 1'b1;
                              end
                          end
                          if (!issue0Valid)
                              schedulerDividerUnavailable = 1'b1;
                      end
                      default: issue0Valid = 1'b0;
                    endcase
                    if (issue0Valid) begin
                        issue0Token = schedulerIndex[5:0];
                        issue0Operation = tokenState[schedulerIndex][9:4];
                        issue0Unit = schedulerUnit[1:0];
                        issue0Bank = tokenState[schedulerIndex][11:10];
                        issue0OperandA = 64'b0;
                        issue0OperandB = 64'b0;
                        schedulerWord = tokenState[schedulerIndex][22:19];
                        schedulerCoefficient = coefficientIndex(
                            tokenState[schedulerIndex][18:16],
                            tokenState[schedulerIndex][22:19]);
                        if (schedulerNeedsBank) begin
                            case (tokenState[schedulerIndex][11:10])
                              2'd0: schedulerRow =
                                  bank0[tokenState[schedulerIndex][15:12]];
                              2'd1: schedulerRow =
                                  bank1[tokenState[schedulerIndex][15:12]];
                              2'd2: schedulerRow =
                                  bank2[tokenState[schedulerIndex][15:12]];
                              default: schedulerRow =
                                  bank3[tokenState[schedulerIndex][15:12]];
                            endcase
                            schedulerBankUsed[issue0Bank] = 1'b1;
                        end
                        case (tokenState[schedulerIndex][3:0])
                          PHASE_DENOMINATOR_ADD_PENDING: begin
                              issue0OperandA = descriptorSumArea[
                                  tokenState[schedulerIndex][18:16] * 64 +: 64];
                              issue0OperandB =
                                  tokenState[schedulerIndex][150:87];
                          end
                          PHASE_DIVIDE_PENDING: begin
                              schedulerWord =
                                  tokenState[schedulerIndex][18:16];
                              issue0OperandA = schedulerRow[
                                  schedulerWord * 64 +: 64];
                              issue0OperandB =
                                  tokenState[schedulerIndex][214:151];
                          end
                          PHASE_MULTIPLY_PENDING: begin
                              issue0OperandA = descriptorCoefficients[
                                  schedulerCoefficient * 64 +: 64];
                              issue0OperandB =
                                  tokenState[schedulerIndex][342:279];
                          end
                          PHASE_EDGE_ADD_PENDING: begin
                              issue0OperandA = schedulerRow[
                                  schedulerWord * 64 +: 64];
                              issue0OperandB =
                                  tokenState[schedulerIndex][406:343];
                          end
                        endcase
                    end
                end
            end
        end

        if (FP_ISSUE_WIDTH == 2 && issue0Valid) begin
            for (schedulerProbe = 0; schedulerProbe < COMPUTE_TOKENS;
                 schedulerProbe = schedulerProbe + 1) begin
                schedulerIndex = issue0Token + 1 + schedulerProbe;
                if (schedulerIndex >= COMPUTE_TOKENS)
                    schedulerIndex = schedulerIndex - COMPUTE_TOKENS;
                if (!issue1Valid && schedulerIndex != issue0Token &&
                    isIssuePending(tokenState[schedulerIndex][3:0]) &&
                    tokenState[schedulerIndex][86:23] <= currentCycle) begin
                    schedulerUnit = UNIT_ADD;
                    schedulerNeedsBank = 0;
                    case (tokenState[schedulerIndex][3:0])
                      PHASE_DENOMINATOR_ADD_PENDING: begin
                          schedulerUnit = UNIT_ADD;
                          schedulerNeedsBank = 0;
                      end
                      PHASE_DIVIDE_PENDING: begin
                          schedulerUnit = UNIT_DIVIDE;
                          schedulerNeedsBank = 1;
                      end
                      PHASE_MULTIPLY_PENDING: begin
                          schedulerUnit = UNIT_MULTIPLY;
                          schedulerNeedsBank = 0;
                      end
                      PHASE_EDGE_ADD_PENDING: begin
                          schedulerUnit = UNIT_ADD;
                          schedulerNeedsBank = 1;
                      end
                      default: begin
                          schedulerUnit = 3;
                          schedulerNeedsBank = 0;
                      end
                    endcase
                    if (schedulerNeedsBank &&
                        (writebackValid[tokenState[schedulerIndex][11:10]] ||
                         schedulerBankUsed[tokenState[schedulerIndex][11:10]])) begin
                        schedulerBankConflict = 1'b1;
                    end else begin
                        case (schedulerUnit)
                          UNIT_ADD: begin
                              if (addReady && !schedulerAddUsed) begin
                                  issue1Valid = 1'b1;
                                  schedulerAddUsed = 1'b1;
                              end
                          end
                          UNIT_MULTIPLY: begin
                              if (multiplyReady && !schedulerMultiplyUsed) begin
                                  issue1Valid = 1'b1;
                                  schedulerMultiplyUsed = 1'b1;
                              end
                          end
                          UNIT_DIVIDE: begin
                              for (schedulerLane = 0; schedulerLane < 8;
                                   schedulerLane = schedulerLane + 1) begin
                                  if (!issue1Valid &&
                                      dividerReady[schedulerLane] &&
                                      !schedulerDividerUsed[schedulerLane]) begin
                                      issue1Valid = 1'b1;
                                      issue1DividerLane = schedulerLane[2:0];
                                      schedulerDividerUsed[schedulerLane] = 1'b1;
                                  end
                              end
                              if (!issue1Valid)
                                  schedulerDividerUnavailable = 1'b1;
                          end
                          default: issue1Valid = 1'b0;
                        endcase
                        if (issue1Valid) begin
                            issue1Token = schedulerIndex[5:0];
                            issue1Operation = tokenState[schedulerIndex][9:4];
                            issue1Unit = schedulerUnit[1:0];
                            issue1Bank = tokenState[schedulerIndex][11:10];
                            issue1OperandA = 64'b0;
                            issue1OperandB = 64'b0;
                            schedulerWord =
                                tokenState[schedulerIndex][22:19];
                            schedulerCoefficient = coefficientIndex(
                                tokenState[schedulerIndex][18:16],
                                tokenState[schedulerIndex][22:19]);
                            if (schedulerNeedsBank) begin
                                case (tokenState[schedulerIndex][11:10])
                                  2'd0: schedulerRow =
                                      bank0[tokenState[schedulerIndex][15:12]];
                                  2'd1: schedulerRow =
                                      bank1[tokenState[schedulerIndex][15:12]];
                                  2'd2: schedulerRow =
                                      bank2[tokenState[schedulerIndex][15:12]];
                                  default: schedulerRow =
                                      bank3[tokenState[schedulerIndex][15:12]];
                                endcase
                                schedulerBankUsed[issue1Bank] = 1'b1;
                            end
                            case (tokenState[schedulerIndex][3:0])
                              PHASE_DENOMINATOR_ADD_PENDING: begin
                                  issue1OperandA = descriptorSumArea[
                                      tokenState[schedulerIndex][18:16] * 64
                                      +: 64];
                                  issue1OperandB =
                                      tokenState[schedulerIndex][150:87];
                              end
                              PHASE_DIVIDE_PENDING: begin
                                  schedulerWord =
                                      tokenState[schedulerIndex][18:16];
                                  issue1OperandA = schedulerRow[
                                      schedulerWord * 64 +: 64];
                                  issue1OperandB =
                                      tokenState[schedulerIndex][214:151];
                              end
                              PHASE_MULTIPLY_PENDING: begin
                                  issue1OperandA = descriptorCoefficients[
                                      schedulerCoefficient * 64 +: 64];
                                  issue1OperandB =
                                      tokenState[schedulerIndex][342:279];
                              end
                              PHASE_EDGE_ADD_PENDING: begin
                                  issue1OperandA = schedulerRow[
                                      schedulerWord * 64 +: 64];
                                  issue1OperandB =
                                      tokenState[schedulerIndex][406:343];
                              end
                            endcase
                        end
                    end
                end
            end
        end
    end

    */

    // Constant-index token classification feeds a narrow rotating bitmap
    // selector. No wide token or bank data is muxed inside the probe loop.
    always @* begin
        slot0Grantable = {COMPUTE_TOKENS{1'b0}};
        slot0BankBlocked = {COMPUTE_TOKENS{1'b0}};
        slot0DividerBlocked = {COMPUTE_TOKENS{1'b0}};
        schedulerActive = 1'b0;
        for (tokenMapIndex = 0; tokenMapIndex < COMPUTE_TOKENS;
             tokenMapIndex = tokenMapIndex + 1) begin
            if (tokenState[tokenMapIndex][3:0] != PHASE_FREE &&
                !(tokenState[tokenMapIndex][3:0] ==
                      PHASE_RESULT_WRITE_PENDING &&
                  writebackValid[tokenState[tokenMapIndex][11:10]] &&
                  writebackToken[tokenState[tokenMapIndex][11:10]] ==
                      tokenMapIndex))
                schedulerActive = 1'b1;
            if (isIssuePending(tokenState[tokenMapIndex][3:0]) &&
                tokenState[tokenMapIndex][86:23] <= currentCycle) begin
                if (issueNeedsBankForPhase(
                        tokenState[tokenMapIndex][3:0]) &&
                    writebackValid[tokenState[tokenMapIndex][11:10]]) begin
                    slot0BankBlocked[tokenMapIndex] = 1'b1;
                end else if (issueUnitForPhase(
                        tokenState[tokenMapIndex][3:0]) == UNIT_ADD &&
                    addReady) begin
                    slot0Grantable[tokenMapIndex] = 1'b1;
                end else if (issueUnitForPhase(
                        tokenState[tokenMapIndex][3:0]) == UNIT_MULTIPLY &&
                             multiplyReady) begin
                    slot0Grantable[tokenMapIndex] = 1'b1;
                end else if (issueUnitForPhase(
                        tokenState[tokenMapIndex][3:0]) == UNIT_DIVIDE) begin
                    if (dividerReady != 8'b0)
                        slot0Grantable[tokenMapIndex] = 1'b1;
                    else
                        slot0DividerBlocked[tokenMapIndex] = 1'b1;
                end
            end
        end
    end

    always @* begin
        issue0Valid = slot0SelectedValid;
        issue0Token = slot0SelectedIndex;
        issue0SelectedState = 471'b0;
        issue0Operation = 6'b0;
        issue0Unit = UNIT_ADD;
        issue0DividerLane = 3'b0;
        issue0Bank = 2'b0;
        issue0NeedsBank = 1'b0;
        dividerAvailableAfterSlot0 = dividerReady;
        issue0LaneFound = 0;
        slot1Cursor = issueCursor;
        if (slot0SelectedValid) begin
            issue0SelectedState = tokenState[slot0SelectedIndex];
            issue0Operation = issue0SelectedState[9:4];
            issue0Bank = issue0SelectedState[11:10];
            slot1Cursor = slot0SelectedIndex == COMPUTE_TOKENS - 1 ?
                6'b0 : slot0SelectedIndex + 1'b1;
            case (issue0SelectedState[3:0])
              PHASE_DIVIDE_PENDING: begin
                  issue0Unit = UNIT_DIVIDE;
                  issue0NeedsBank = 1'b1;
                  for (issue0LaneIndex = 0; issue0LaneIndex < 8;
                       issue0LaneIndex = issue0LaneIndex + 1) begin
                      if (dividerReady[issue0LaneIndex] &&
                          !issue0LaneFound) begin
                          issue0DividerLane = issue0LaneIndex[2:0];
                          dividerAvailableAfterSlot0[issue0LaneIndex] = 1'b0;
                          issue0LaneFound = 1;
                      end
                  end
              end
              PHASE_MULTIPLY_PENDING: issue0Unit = UNIT_MULTIPLY;
              PHASE_EDGE_ADD_PENDING: issue0NeedsBank = 1'b1;
            endcase
        end
    end

    always @* begin
        slot1Grantable = {COMPUTE_TOKENS{1'b0}};
        slot1BankBlocked = {COMPUTE_TOKENS{1'b0}};
        slot1DividerBlocked = {COMPUTE_TOKENS{1'b0}};
        for (slot1MapIndex = 0; slot1MapIndex < COMPUTE_TOKENS;
             slot1MapIndex = slot1MapIndex + 1) begin
            if (FP_ISSUE_WIDTH == 2 && slot0SelectedValid &&
                slot1MapIndex != slot0SelectedIndex &&
                isIssuePending(tokenState[slot1MapIndex][3:0]) &&
                tokenState[slot1MapIndex][86:23] <= currentCycle) begin
                if (issueNeedsBankForPhase(
                        tokenState[slot1MapIndex][3:0]) &&
                    (writebackValid[tokenState[slot1MapIndex][11:10]] ||
                     (issue0NeedsBank && issue0Bank ==
                         tokenState[slot1MapIndex][11:10]))) begin
                    slot1BankBlocked[slot1MapIndex] = 1'b1;
                end else if (issueUnitForPhase(
                        tokenState[slot1MapIndex][3:0]) == UNIT_ADD &&
                             issue0Unit != UNIT_ADD && addReady) begin
                    slot1Grantable[slot1MapIndex] = 1'b1;
                end else if (issueUnitForPhase(
                        tokenState[slot1MapIndex][3:0]) == UNIT_MULTIPLY &&
                             issue0Unit != UNIT_MULTIPLY && multiplyReady) begin
                    slot1Grantable[slot1MapIndex] = 1'b1;
                end else if (issueUnitForPhase(
                        tokenState[slot1MapIndex][3:0]) == UNIT_DIVIDE) begin
                    if (dividerAvailableAfterSlot0 != 8'b0)
                        slot1Grantable[slot1MapIndex] = 1'b1;
                    else
                        slot1DividerBlocked[slot1MapIndex] = 1'b1;
                end
            end
        end
    end

    always @* begin
        issue1Valid = FP_ISSUE_WIDTH == 2 && slot0SelectedValid &&
            slot1SelectedValid;
        issue1Token = slot1SelectedIndex;
        issue1SelectedState = 471'b0;
        issue1Operation = 6'b0;
        issue1Unit = UNIT_ADD;
        issue1DividerLane = 3'b0;
        issue1Bank = 2'b0;
        issue1NeedsBank = 1'b0;
        issue1LaneFound = 0;
        if (issue1Valid) begin
            issue1SelectedState = tokenState[slot1SelectedIndex];
            issue1Operation = issue1SelectedState[9:4];
            issue1Bank = issue1SelectedState[11:10];
            case (issue1SelectedState[3:0])
              PHASE_DIVIDE_PENDING: begin
                  issue1Unit = UNIT_DIVIDE;
                  issue1NeedsBank = 1'b1;
                  for (issue1LaneIndex = 0; issue1LaneIndex < 8;
                       issue1LaneIndex = issue1LaneIndex + 1) begin
                      if (dividerAvailableAfterSlot0[issue1LaneIndex] &&
                          !issue1LaneFound) begin
                          issue1DividerLane = issue1LaneIndex[2:0];
                          issue1LaneFound = 1;
                      end
                  end
              end
              PHASE_MULTIPLY_PENDING: issue1Unit = UNIT_MULTIPLY;
              PHASE_EDGE_ADD_PENDING: issue1NeedsBank = 1'b1;
            endcase
        end
    end

    always @* begin
        schedulerBankUsed = 4'b0;
        if (issue0Valid && issue0NeedsBank)
            schedulerBankUsed[issue0Bank] = 1'b1;
        if (issue1Valid && issue1NeedsBank)
            schedulerBankUsed[issue1Bank] = 1'b1;
        schedulerBankConflict = slot0SawBankBlocked ||
            (slot0SelectedValid && slot1SawBankBlocked);
        schedulerDividerUnavailable = slot0SawDividerBlocked ||
            (slot0SelectedValid && slot1SawDividerBlocked);
    end

    always @* begin
        bank0ReadRow = externalGroup[5:2];
        bank1ReadRow = externalGroup[5:2];
        bank2ReadRow = externalGroup[5:2];
        bank3ReadRow = externalGroup[5:2];
        if (issue0Valid && issue0NeedsBank) begin
            case (issue0Bank)
              2'd0: bank0ReadRow = issue0SelectedState[15:12];
              2'd1: bank1ReadRow = issue0SelectedState[15:12];
              2'd2: bank2ReadRow = issue0SelectedState[15:12];
              default: bank3ReadRow = issue0SelectedState[15:12];
            endcase
        end
        if (issue1Valid && issue1NeedsBank) begin
            case (issue1Bank)
              2'd0: bank0ReadRow = issue1SelectedState[15:12];
              2'd1: bank1ReadRow = issue1SelectedState[15:12];
              2'd2: bank2ReadRow = issue1SelectedState[15:12];
              default: bank3ReadRow = issue1SelectedState[15:12];
            endcase
        end
    end

    always @* begin
        issue0OperandA = 64'b0;
        issue0OperandB = 64'b0;
        issue0SelectedRow = 640'b0;
        issue0Coefficient = coefficientIndex(
            issue0SelectedState[18:16], issue0SelectedState[22:19]);
        case (issue0Bank)
          2'd0: issue0SelectedRow = bank0ReadData;
          2'd1: issue0SelectedRow = bank1ReadData;
          2'd2: issue0SelectedRow = bank2ReadData;
          default: issue0SelectedRow = bank3ReadData;
        endcase
        case (issue0SelectedState[3:0])
          PHASE_DENOMINATOR_ADD_PENDING: begin
              issue0OperandA = descriptorSumArea[
                  issue0SelectedState[18:16] * 64 +: 64];
              issue0OperandB = issue0SelectedState[150:87];
          end
          PHASE_DIVIDE_PENDING: begin
              issue0OperandA = issue0SelectedRow[
                  issue0SelectedState[18:16] * 64 +: 64];
              issue0OperandB = issue0SelectedState[214:151];
          end
          PHASE_MULTIPLY_PENDING: begin
              issue0OperandA = descriptorCoefficients[
                  issue0Coefficient * 64 +: 64];
              issue0OperandB = issue0SelectedState[342:279];
          end
          PHASE_EDGE_ADD_PENDING: begin
              issue0OperandA = issue0SelectedRow[
                  issue0SelectedState[22:19] * 64 +: 64];
              issue0OperandB = issue0SelectedState[406:343];
          end
        endcase
    end

    always @* begin
        issue1OperandA = 64'b0;
        issue1OperandB = 64'b0;
        issue1SelectedRow = 640'b0;
        issue1Coefficient = coefficientIndex(
            issue1SelectedState[18:16], issue1SelectedState[22:19]);
        case (issue1Bank)
          2'd0: issue1SelectedRow = bank0ReadData;
          2'd1: issue1SelectedRow = bank1ReadData;
          2'd2: issue1SelectedRow = bank2ReadData;
          default: issue1SelectedRow = bank3ReadData;
        endcase
        case (issue1SelectedState[3:0])
          PHASE_DENOMINATOR_ADD_PENDING: begin
              issue1OperandA = descriptorSumArea[
                  issue1SelectedState[18:16] * 64 +: 64];
              issue1OperandB = issue1SelectedState[150:87];
          end
          PHASE_DIVIDE_PENDING: begin
              issue1OperandA = issue1SelectedRow[
                  issue1SelectedState[18:16] * 64 +: 64];
              issue1OperandB = issue1SelectedState[214:151];
          end
          PHASE_MULTIPLY_PENDING: begin
              issue1OperandA = descriptorCoefficients[
                  issue1Coefficient * 64 +: 64];
              issue1OperandB = issue1SelectedState[342:279];
          end
          PHASE_EDGE_ADD_PENDING: begin
              issue1OperandA = issue1SelectedRow[
                  issue1SelectedState[22:19] * 64 +: 64];
              issue1OperandB = issue1SelectedState[406:343];
          end
        endcase
    end

    // External fill/drain has lowest priority after writeback and reads.
    always @* begin
        externalReady = externalValid &&
            !writebackValid[externalGroup[1:0]] &&
            !schedulerBankUsed[externalGroup[1:0]];
        externalReadData = 640'b0;
        if (externalReady && !externalWrite) begin
            case (externalGroup[1:0])
              2'd0: externalReadData = bank0ReadData;
              2'd1: externalReadData = bank1ReadData;
              2'd2: externalReadData = bank2ReadData;
              default: externalReadData = bank3ReadData;
            endcase
        end
    end

    always @* begin
        bank0WriteValid = writebackValid[0];
        bank1WriteValid = writebackValid[1];
        bank2WriteValid = writebackValid[2];
        bank3WriteValid = writebackValid[3];
        bank0WriteRow = writebackRow[0];
        bank1WriteRow = writebackRow[1];
        bank2WriteRow = writebackRow[2];
        bank3WriteRow = writebackRow[3];
        bank0WriteMask = writebackMask[0];
        bank1WriteMask = writebackMask[1];
        bank2WriteMask = writebackMask[2];
        bank3WriteMask = writebackMask[3];
        bank0WriteData = writebackData[0];
        bank1WriteData = writebackData[1];
        bank2WriteData = writebackData[2];
        bank3WriteData = writebackData[3];
        if (externalValid && externalReady && externalWrite) begin
            case (externalGroup[1:0])
              2'd0: begin
                  bank0WriteValid = 1'b1;
                  bank0WriteRow = externalGroup[5:2];
                  bank0WriteMask = externalWriteMask;
                  bank0WriteData = externalWriteData;
              end
              2'd1: begin
                  bank1WriteValid = 1'b1;
                  bank1WriteRow = externalGroup[5:2];
                  bank1WriteMask = externalWriteMask;
                  bank1WriteData = externalWriteData;
              end
              2'd2: begin
                  bank2WriteValid = 1'b1;
                  bank2WriteRow = externalGroup[5:2];
                  bank2WriteMask = externalWriteMask;
                  bank2WriteData = externalWriteData;
              end
              default: begin
                  bank3WriteValid = 1'b1;
                  bank3WriteRow = externalGroup[5:2];
                  bank3WriteMask = externalWriteMask;
                  bank3WriteData = externalWriteData;
              end
            endcase
        end
    end

    /* Superseded central token next-state process. State updates now live in
       the fixed-index LanlUmtTokenEntry module.
    // Decode dynamic tags to fixed token-entry write enables. Each generated
    // token register sees one complete next-state value and one write port.
    always @* begin
        tokenNextStateFlat = tokenStateFlat;
        tokenWriteEnable = {COMPUTE_TOKENS{1'b0}};
        tokenWorkingState = 471'b0;
        tokenNextDestination = 4'b0;
        for (tokenUpdateIndex = 0; tokenUpdateIndex < COMPUTE_TOKENS;
             tokenUpdateIndex = tokenUpdateIndex + 1) begin
            tokenWorkingState = tokenState[tokenUpdateIndex];
            if (admit0Valid && admit0Ready &&
                admit0Token == tokenUpdateIndex) begin
                tokenWorkingState = admit0State;
                tokenWriteEnable[tokenUpdateIndex] = 1'b1;
            end
            if (admit1Valid && admit1Ready &&
                admit1Token == tokenUpdateIndex) begin
                tokenWorkingState = admit1State;
                tokenWriteEnable[tokenUpdateIndex] = 1'b1;
            end
            if (issue0Valid && issue0Token == tokenUpdateIndex) begin
                tokenWorkingState[3:0] =
                    tokenState[tokenUpdateIndex][3:0] + 1'b1;
                if (tokenState[tokenUpdateIndex][3:0] ==
                    PHASE_EDGE_ADD_PENDING)
                    tokenWorkingState[86:23] = 64'hffffffffffffffff;
                tokenWriteEnable[tokenUpdateIndex] = 1'b1;
            end
            if (issue1Valid && issue1Token == tokenUpdateIndex) begin
                tokenWorkingState[3:0] =
                    tokenState[tokenUpdateIndex][3:0] + 1'b1;
                if (tokenState[tokenUpdateIndex][3:0] ==
                    PHASE_EDGE_ADD_PENDING)
                    tokenWorkingState[86:23] = 64'hffffffffffffffff;
                tokenWriteEnable[tokenUpdateIndex] = 1'b1;
            end
            for (sequentialCompletionSource = 0;
                 sequentialCompletionSource < 10;
                 sequentialCompletionSource =
                     sequentialCompletionSource + 1) begin
                if (completionReady[sequentialCompletionSource] &&
                    completionToken[sequentialCompletionSource] ==
                        tokenUpdateIndex) begin
                    case (tokenState[tokenUpdateIndex][3:0])
                      PHASE_DENOMINATOR_ADD_WAIT: begin
                          tokenWorkingState[214:151] =
                              completionResult[sequentialCompletionSource];
                          tokenWorkingState[86:23] = currentCycle + 1'b1;
                          tokenWorkingState[3:0] = PHASE_DIVIDE_PENDING;
                      end
                      PHASE_DIVIDE_WAIT: begin
                          tokenWorkingState[342:279] =
                              completionResult[sequentialCompletionSource];
                          tokenNextDestination = nextActiveDestination(
                              tokenState[tokenUpdateIndex][18:16],
                              tokenState[tokenUpdateIndex][18:16] + 1'b1);
                          tokenWorkingState[22:19] = tokenNextDestination;
                          tokenWorkingState[86:23] = currentCycle + 1'b1;
                          tokenWorkingState[3:0] =
                              tokenNextDestination == 4'd8 ?
                                  PHASE_RESULT_WRITE_PENDING :
                                  PHASE_MULTIPLY_PENDING;
                      end
                      PHASE_MULTIPLY_WAIT: begin
                          tokenWorkingState[406:343] =
                              completionResult[sequentialCompletionSource];
                          tokenWorkingState[86:23] = currentCycle + 1'b1;
                          tokenWorkingState[3:0] = PHASE_EDGE_ADD_PENDING;
                      end
                      PHASE_EDGE_ADD_WAIT: begin
                          tokenWorkingState[470:407] =
                              completionResult[sequentialCompletionSource];
                          tokenWorkingState[86:23] = currentCycle;
                      end
                    endcase
                    tokenWriteEnable[tokenUpdateIndex] = 1'b1;
                end
            end
            for (sequentialBank = 0; sequentialBank < 4;
                 sequentialBank = sequentialBank + 1) begin
                if (writebackValid[sequentialBank] &&
                    writebackToken[sequentialBank] == tokenUpdateIndex) begin
                    if (tokenState[tokenUpdateIndex][3:0] ==
                        PHASE_EDGE_ADD_WAIT) begin
                        tokenNextDestination = nextActiveDestination(
                            tokenState[tokenUpdateIndex][18:16],
                            tokenState[tokenUpdateIndex][22:19] + 1'b1);
                        tokenWorkingState[22:19] = tokenNextDestination;
                        tokenWorkingState[3:0] =
                            tokenNextDestination == 4'd8 ?
                                PHASE_RESULT_WRITE_PENDING :
                                PHASE_MULTIPLY_PENDING;
                    end else begin
                        tokenWorkingState = 471'b0;
                    end
                    tokenWriteEnable[tokenUpdateIndex] = 1'b1;
                end
            end
            tokenNextStateFlat[tokenUpdateIndex * 471 +: 471] =
                tokenWorkingState;
        end
    end

    */

    always @(posedge clock) begin
        if (!nReset) begin
            functionalState <= {FUNCTIONAL_CONTROL_BITS{1'b0}};
            bankSchedulerState <= {BANK_SCHEDULER_BITS{1'b0}};
            instrumentationState <= {INSTRUMENTATION_BITS{1'b0}};
        end else begin
            functionalState[63:0] <= currentCycle + 1'b1;
            if (issue1Valid)
                functionalState[69:64] <=
                    issue1Token == COMPUTE_TOKENS - 1 ? 6'b0 :
                    issue1Token + 1'b1;
            else if (issue0Valid)
                functionalState[69:64] <=
                    issue0Token == COMPUTE_TOKENS - 1 ? 6'b0 :
                    issue0Token + 1'b1;
            bankSchedulerState[3:0] <= writebackValid;
            bankSchedulerState[7:4] <= schedulerBankUsed;

            /* Superseded dynamic token-array updates.
            if (admit0Valid && admit0Ready)
                tokenState[admit0Token] <= admit0State;
            if (admit1Valid && admit1Ready)
                tokenState[admit1Token] <= admit1State;
            if (issue0Valid)
                tokenState[issue0Token][3:0] <=
                    tokenState[issue0Token][3:0] + 1'b1;
            if (issue1Valid)
                tokenState[issue1Token][3:0] <=
                    tokenState[issue1Token][3:0] + 1'b1;
            if (issue0Valid &&
                tokenState[issue0Token][3:0] == PHASE_EDGE_ADD_PENDING)
                tokenState[issue0Token][86:23] <= 64'hffffffffffffffff;
            if (issue1Valid &&
                tokenState[issue1Token][3:0] == PHASE_EDGE_ADD_PENDING)
                tokenState[issue1Token][86:23] <= 64'hffffffffffffffff;
            for (sequentialCompletionSource = 0;
                 sequentialCompletionSource < 10;
                 sequentialCompletionSource =
                     sequentialCompletionSource + 1) begin
                if (completionReady[sequentialCompletionSource]) begin
                    case (tokenState[
                        completionToken[sequentialCompletionSource]][3:0])
                      PHASE_DENOMINATOR_ADD_WAIT: begin
                          tokenState[
                              completionToken[sequentialCompletionSource]]
                              [214:151] <=
                                  completionResult[sequentialCompletionSource];
                          tokenState[
                              completionToken[sequentialCompletionSource]]
                              [86:23] <= currentCycle + 1'b1;
                          tokenState[
                              completionToken[sequentialCompletionSource]]
                              [3:0] <= PHASE_DIVIDE_PENDING;
                      end
                      PHASE_DIVIDE_WAIT: begin
                          tokenState[
                              completionToken[sequentialCompletionSource]]
                              [342:279] <=
                                  completionResult[sequentialCompletionSource];
                          tokenState[
                              completionToken[sequentialCompletionSource]]
                              [22:19] <= nextActiveDestination(
                                  tokenState[
                                      completionToken[
                                          sequentialCompletionSource]][18:16],
                                  tokenState[
                                      completionToken[
                                          sequentialCompletionSource]][18:16]
                                      + 1'b1);
                          tokenState[
                              completionToken[sequentialCompletionSource]]
                              [86:23] <= currentCycle + 1'b1;
                          if (nextActiveDestination(
                              tokenState[
                                  completionToken[
                                      sequentialCompletionSource]][18:16],
                              tokenState[
                                  completionToken[
                                      sequentialCompletionSource]][18:16]
                                  + 1'b1) == 4'd8)
                              tokenState[
                                  completionToken[
                                      sequentialCompletionSource]][3:0] <=
                                          PHASE_RESULT_WRITE_PENDING;
                          else
                              tokenState[
                                  completionToken[
                                      sequentialCompletionSource]][3:0] <=
                                          PHASE_MULTIPLY_PENDING;
                      end
                      PHASE_MULTIPLY_WAIT: begin
                          tokenState[
                              completionToken[sequentialCompletionSource]]
                              [406:343] <=
                                  completionResult[sequentialCompletionSource];
                          tokenState[
                              completionToken[sequentialCompletionSource]]
                              [86:23] <= currentCycle + 1'b1;
                          tokenState[
                              completionToken[sequentialCompletionSource]]
                              [3:0] <= PHASE_EDGE_ADD_PENDING;
                      end
                      PHASE_EDGE_ADD_WAIT: begin
                          tokenState[
                              completionToken[sequentialCompletionSource]]
                              [470:407] <=
                                  completionResult[sequentialCompletionSource];
                          tokenState[
                              completionToken[sequentialCompletionSource]]
                              [86:23] <= currentCycle;
                      end
                    endcase
                end
            end
            for (sequentialBank = 0; sequentialBank < 4;
                 sequentialBank = sequentialBank + 1) begin
                if (writebackValid[sequentialBank]) begin
                    if (tokenState[writebackToken[sequentialBank]][3:0] ==
                        PHASE_EDGE_ADD_WAIT) begin
                        tokenState[writebackToken[sequentialBank]][22:19] <=
                            nextActiveDestination(
                                tokenState[writebackToken[sequentialBank]]
                                    [18:16],
                                tokenState[writebackToken[sequentialBank]]
                                    [22:19] + 1'b1);
                        if (nextActiveDestination(
                            tokenState[writebackToken[sequentialBank]][18:16],
                            tokenState[writebackToken[sequentialBank]][22:19]
                                + 1'b1) == 4'd8)
                            tokenState[writebackToken[sequentialBank]][3:0] <=
                                PHASE_RESULT_WRITE_PENDING;
                        else
                            tokenState[writebackToken[sequentialBank]][3:0] <=
                                PHASE_MULTIPLY_PENDING;
                    end else begin
                        tokenState[writebackToken[sequentialBank]] <= 471'b0;
                    end
                end
            end
            */

            instrumentationState[63:0] <=
                instrumentationState[63:0] + issue0Valid + issue1Valid;
            if (issue0Valid && issue1Valid)
                instrumentationState[127:64] <=
                    instrumentationState[127:64] + 1'b1;
            if (schedulerActive && !issue0Valid)
                instrumentationState[191:128] <=
                    instrumentationState[191:128] + 1'b1;
            if (schedulerBankConflict)
                instrumentationState[255:192] <=
                    instrumentationState[255:192] + 1'b1;
            if (writebackCollision)
                instrumentationState[319:256] <=
                    instrumentationState[319:256] + 1'b1;
            if (schedulerBankConflict || writebackCollision)
                instrumentationState[383:320] <=
                    instrumentationState[383:320] + 1'b1;
            if (schedulerDividerUnavailable)
                instrumentationState[447:384] <=
                    instrumentationState[447:384] + 1'b1;

            /* Superseded monolithic bank-array writes.
            for (writeLane = 0; writeLane < 10;
                 writeLane = writeLane + 1) begin
                if (writebackValid[0] && writebackMask[0][writeLane])
                    bank0[writebackRow[0]][writeLane * 64 +: 64] <=
                        writebackData[0][writeLane * 64 +: 64];
                else if (externalValid && externalReady && externalWrite &&
                         externalGroup[1:0] == 2'd0 &&
                         externalWriteMask[writeLane])
                    bank0[externalGroup[5:2]][writeLane * 64 +: 64] <=
                        externalWriteData[writeLane * 64 +: 64];
                if (writebackValid[1] && writebackMask[1][writeLane])
                    bank1[writebackRow[1]][writeLane * 64 +: 64] <=
                        writebackData[1][writeLane * 64 +: 64];
                else if (externalValid && externalReady && externalWrite &&
                         externalGroup[1:0] == 2'd1 &&
                         externalWriteMask[writeLane])
                    bank1[externalGroup[5:2]][writeLane * 64 +: 64] <=
                        externalWriteData[writeLane * 64 +: 64];
                if (writebackValid[2] && writebackMask[2][writeLane])
                    bank2[writebackRow[2]][writeLane * 64 +: 64] <=
                        writebackData[2][writeLane * 64 +: 64];
                else if (externalValid && externalReady && externalWrite &&
                         externalGroup[1:0] == 2'd2 &&
                         externalWriteMask[writeLane])
                    bank2[externalGroup[5:2]][writeLane * 64 +: 64] <=
                        externalWriteData[writeLane * 64 +: 64];
                if (writebackValid[3] && writebackMask[3][writeLane])
                    bank3[writebackRow[3]][writeLane * 64 +: 64] <=
                        writebackData[3][writeLane * 64 +: 64];
                else if (externalValid && externalReady && externalWrite &&
                         externalGroup[1:0] == 2'd3 &&
                         externalWriteMask[writeLane])
                    bank3[externalGroup[5:2]][writeLane * 64 +: 64] <=
                        externalWriteData[writeLane * 64 +: 64];
            end
            */
        end
    end
endmodule

`define LANL_UMT_SHELL_WRAPPER_PORTS \
    input clock, input nReset, \
    input admit0Valid, input [5:0] admit0Token, \
    input [470:0] admit0State, output admit0Ready, \
    input admit1Valid, input [5:0] admit1Token, \
    input [470:0] admit1State, output admit1Ready, \
    input addReady, input multiplyReady, input [7:0] dividerReady, \
    input [511:0] descriptorSumArea, \
    input [1791:0] descriptorCoefficients, \
    output issue0Valid, output [5:0] issue0Token, \
    output [5:0] issue0Operation, \
    output [1:0] issue0Unit, output [2:0] issue0DividerLane, \
    output [1:0] issue0Bank, output [63:0] issue0OperandA, \
    output [63:0] issue0OperandB, output issue1Valid, \
    output [5:0] issue1Token, output [5:0] issue1Operation, \
    output [1:0] issue1Unit, \
    output [2:0] issue1DividerLane, output [1:0] issue1Bank, \
    output [63:0] issue1OperandA, output [63:0] issue1OperandB, \
    input addCompletionValid, input [5:0] addCompletionToken, \
    input [63:0] addCompletionResult, output addCompletionReady, \
    input multiplyCompletionValid, input [5:0] multiplyCompletionToken, \
    input [63:0] multiplyCompletionResult, \
    output multiplyCompletionReady, input [7:0] dividerCompletionValid, \
    input [47:0] dividerCompletionToken, \
    input [511:0] dividerCompletionResult, \
    output [7:0] dividerCompletionReady, input externalValid, \
    input externalWrite, input [5:0] externalGroup, \
    input [9:0] externalWriteMask, input [639:0] externalWriteData, \
    output externalReady, output [639:0] externalReadData, \
    output [31:0] configuredTokens, output [31:0] configuredIssueWidth, \
    output [31:0] tokenLogicalBits, output [31:0] physicalBankBits, \
    output [31:0] functionalControlBits, \
    output [31:0] bankSchedulerBits, output [31:0] instrumentationBits, \
    output [31:0] persistentBits, output [31:0] selectorCandidates, \
    output [31:0] operandRouteBits, output [63:0] fpOperationsIssued, \
    output [63:0] dualIssueCycles, output [63:0] fpIssueStallCycles, \
    output [63:0] bankConflictCycles, \
    output [63:0] writebackStallCycles, \
    output [63:0] resultBankStallCycles, \
    output [63:0] dividerNoLaneCycles, output [63:0] stateWitness

`define LANL_UMT_SHELL_WRAPPER_CONNECTIONS \
    .clock(clock), .nReset(nReset), \
    .admit0Valid(admit0Valid), .admit0Token(admit0Token), \
    .admit0State(admit0State), .admit0Ready(admit0Ready), \
    .admit1Valid(admit1Valid), .admit1Token(admit1Token), \
    .admit1State(admit1State), .admit1Ready(admit1Ready), \
    .addReady(addReady), .multiplyReady(multiplyReady), \
    .dividerReady(dividerReady), .descriptorSumArea(descriptorSumArea), \
    .descriptorCoefficients(descriptorCoefficients), \
    .issue0Valid(issue0Valid), \
    .issue0Token(issue0Token), .issue0Operation(issue0Operation), \
    .issue0Unit(issue0Unit), \
    .issue0DividerLane(issue0DividerLane), .issue0Bank(issue0Bank), \
    .issue0OperandA(issue0OperandA), .issue0OperandB(issue0OperandB), \
    .issue1Valid(issue1Valid), .issue1Token(issue1Token), \
    .issue1Operation(issue1Operation), .issue1Unit(issue1Unit), \
    .issue1DividerLane(issue1DividerLane), \
    .issue1Bank(issue1Bank), .issue1OperandA(issue1OperandA), \
    .issue1OperandB(issue1OperandB), \
    .addCompletionValid(addCompletionValid), \
    .addCompletionToken(addCompletionToken), \
    .addCompletionResult(addCompletionResult), \
    .addCompletionReady(addCompletionReady), \
    .multiplyCompletionValid(multiplyCompletionValid), \
    .multiplyCompletionToken(multiplyCompletionToken), \
    .multiplyCompletionResult(multiplyCompletionResult), \
    .multiplyCompletionReady(multiplyCompletionReady), \
    .dividerCompletionValid(dividerCompletionValid), \
    .dividerCompletionToken(dividerCompletionToken), \
    .dividerCompletionResult(dividerCompletionResult), \
    .dividerCompletionReady(dividerCompletionReady), \
    .externalValid(externalValid), .externalWrite(externalWrite), \
    .externalGroup(externalGroup), .externalWriteMask(externalWriteMask), \
    .externalWriteData(externalWriteData), .externalReady(externalReady), \
    .externalReadData(externalReadData), \
    .configuredTokens(configuredTokens), \
    .configuredIssueWidth(configuredIssueWidth), \
    .tokenLogicalBits(tokenLogicalBits), \
    .physicalBankBits(physicalBankBits), \
    .functionalControlBits(functionalControlBits), \
    .bankSchedulerBits(bankSchedulerBits), \
    .instrumentationBits(instrumentationBits), \
    .persistentBits(persistentBits), \
    .selectorCandidates(selectorCandidates), \
    .operandRouteBits(operandRouteBits), \
    .fpOperationsIssued(fpOperationsIssued), \
    .dualIssueCycles(dualIssueCycles), \
    .fpIssueStallCycles(fpIssueStallCycles), \
    .bankConflictCycles(bankConflictCycles), \
    .writebackStallCycles(writebackStallCycles), \
    .resultBankStallCycles(resultBankStallCycles), \
    .dividerNoLaneCycles(dividerNoLaneCycles), \
    .stateWitness(stateWitness)

module LanlUmtSchedulerShellT24W1(`LANL_UMT_SHELL_WRAPPER_PORTS);
    LanlUmtSchedulerShell #(.COMPUTE_TOKENS(24), .FP_ISSUE_WIDTH(1)) shell(
        `LANL_UMT_SHELL_WRAPPER_CONNECTIONS);
endmodule

module LanlUmtSchedulerShellT24W2(`LANL_UMT_SHELL_WRAPPER_PORTS);
    LanlUmtSchedulerShell #(.COMPUTE_TOKENS(24), .FP_ISSUE_WIDTH(2)) shell(
        `LANL_UMT_SHELL_WRAPPER_CONNECTIONS);
endmodule

module LanlUmtSchedulerShellT32W1(`LANL_UMT_SHELL_WRAPPER_PORTS);
    LanlUmtSchedulerShell #(.COMPUTE_TOKENS(32), .FP_ISSUE_WIDTH(1)) shell(
        `LANL_UMT_SHELL_WRAPPER_CONNECTIONS);
endmodule

module LanlUmtSchedulerShellT32W2(`LANL_UMT_SHELL_WRAPPER_PORTS);
    LanlUmtSchedulerShell #(.COMPUTE_TOKENS(32), .FP_ISSUE_WIDTH(2)) shell(
        `LANL_UMT_SHELL_WRAPPER_CONNECTIONS);
endmodule

`undef LANL_UMT_SHELL_WRAPPER_PORTS
`undef LANL_UMT_SHELL_WRAPPER_CONNECTIONS
