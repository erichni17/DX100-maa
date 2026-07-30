`timescale 1ns/1ps

// Control for reusing 69 dead operation-payload bits as the result and
// exception-flag slot after an arithmetic request has been captured.  The
// four payload banks each have one read/write port.  A retirement read wins
// over a completion write to the same bank; distinct banks may proceed in
// parallel.  Allocation is a metadata commit after the upstream payload
// initializer has completed, so it consumes no payload port here.
module LanlMaaOperationRetirementOverlay64x4x2(
    input clock,
    input nReset,

    input configureValid,
    output configureReady,
    input configureOrdered,
    input [5:0] configureHeadTag,

    input allocate0Valid,
    input [5:0] allocate0Tag,
    output reg allocate0Ready,
    input allocate1Valid,
    input [5:0] allocate1Tag,
    output reg allocate1Ready,

    input issue0Valid,
    input [5:0] issue0Tag,
    output reg issue0Eligible,
    input issue0Commit,
    input issue1Valid,
    input [5:0] issue1Tag,
    output reg issue1Eligible,
    input issue1Commit,

    input completion0Valid,
    output reg completion0Ready,
    input [5:0] completion0Tag,
    input [63:0] completion0Value,
    input [4:0] completion0Flags,
    input completion1Valid,
    output reg completion1Ready,
    input [5:0] completion1Tag,
    input [63:0] completion1Value,
    input [4:0] completion1Flags,

    output reg [3:0] payloadReadValid,
    output reg [3:0] payloadBank0ReadWay,
    output reg [3:0] payloadBank1ReadWay,
    output reg [3:0] payloadBank2ReadWay,
    output reg [3:0] payloadBank3ReadWay,
    input [68:0] payloadBank0ReadData,
    input [68:0] payloadBank1ReadData,
    input [68:0] payloadBank2ReadData,
    input [68:0] payloadBank3ReadData,

    output reg [3:0] payloadWriteValid,
    output reg [3:0] payloadBank0WriteWay,
    output reg [3:0] payloadBank1WriteWay,
    output reg [3:0] payloadBank2WriteWay,
    output reg [3:0] payloadBank3WriteWay,
    output reg [68:0] payloadBank0WriteData,
    output reg [68:0] payloadBank1WriteData,
    output reg [68:0] payloadBank2WriteData,
    output reg [68:0] payloadBank3WriteData,

    output reg retire0Valid,
    output reg [5:0] retire0Tag,
    output reg [63:0] retire0Value,
    output reg [4:0] retire0Flags,
    output reg retire1Valid,
    output reg [5:0] retire1Tag,
    output reg [63:0] retire1Value,
    output reg [4:0] retire1Flags,
    input retireReady,

    output [6:0] occupancy,
    output idle,
    output reg [31:0] allocationsAccepted,
    output reg [31:0] issuesAccepted,
    output reg [31:0] completionsAccepted,
    output reg [31:0] retirementsAccepted,
    output reg [31:0] allocationBankConflictCycles,
    output reg [31:0] completionBankConflictCycles,
    output reg [31:0] completionReadConflictCycles,
    output reg [31:0] duplicateAllocationCycles,
    output reg [31:0] invalidCompletionCycles,
    output reg [31:0] retirementBackpressureCycles,
    output reg protocolError
);
    reg epochActive;
    reg orderedMode;
    reg [5:0] orderedHead;
    reg [1:0] unorderedBankHead;
    reg [6:0] occupiedCount;
    reg [63:0] allocated;
    reg [63:0] issued;
    reg [63:0] completed;

    reg [3:0] bankHasCompletion;
    reg [5:0] bankCompletionTag [0:3];
    reg unorderedFirstFound;
    reg unorderedSecondFound;
    reg [1:0] unorderedFirstBank;
    reg [1:0] unorderedSecondBank;

    wire configureAccepted = configureValid && configureReady;
    wire allocate0Accepted = allocate0Valid && allocate0Ready;
    wire allocate1Accepted = allocate1Valid && allocate1Ready;
    wire issue0Accepted = issue0Valid && issue0Commit && issue0Eligible;
    wire issue1Accepted = issue1Valid && issue1Commit && issue1Eligible;
    wire completion0Accepted = completion0Valid && completion0Ready;
    wire completion1Accepted = completion1Valid && completion1Ready;
    wire retire0Accepted = retire0Valid && retireReady;
    wire retire1Accepted = retire1Valid && retireReady;
    wire retirementStalled =
        (retire0Valid || retire1Valid) && !retireReady;
    wire completion0BaseEligible =
        epochActive && allocated[completion0Tag] &&
        issued[completion0Tag] && !completed[completion0Tag];
    wire completion1BaseEligible =
        epochActive && allocated[completion1Tag] &&
        issued[completion1Tag] && !completed[completion1Tag];
    wire completionSameTag =
        completion0Valid && completion1Valid &&
        completion0Tag == completion1Tag;
    wire completionBankConflict =
        completion0Valid && completion1Valid &&
        completion0BaseEligible && completion1BaseEligible &&
        completion0Tag[1:0] == completion1Tag[1:0];
    wire completion0ReadConflict =
        completion0Valid && completion0BaseEligible &&
        payloadReadValid[completion0Tag[1:0]];
    wire completion1ReadConflict =
        completion1Valid && completion1BaseEligible &&
        payloadReadValid[completion1Tag[1:0]];
    wire allocationBankConflict =
        allocate0Valid && allocate1Valid && allocate0Ready &&
        !allocated[allocate1Tag] &&
        allocate0Tag[1:0] == allocate1Tag[1:0];

    integer bank;
    integer way;
    integer tagIndex;
    integer bankOffset;
    integer candidateBank;
    integer acceptedAllocations;
    integer acceptedRetirements;

    assign configureReady = occupiedCount == 0;
    assign occupancy = occupiedCount;
    assign idle = occupiedCount == 0;

    always @* begin
        allocate0Ready = epochActive && !allocated[allocate0Tag];
        allocate1Ready = epochActive && !allocated[allocate1Tag];
        if (allocate0Valid && allocate0Ready &&
            (allocate0Tag == allocate1Tag ||
             allocate0Tag[1:0] == allocate1Tag[1:0])) begin
            allocate1Ready = 1'b0;
        end

        issue0Eligible = epochActive && allocated[issue0Tag] &&
            !issued[issue0Tag] && !completed[issue0Tag];
        issue1Eligible = epochActive && allocated[issue1Tag] &&
            !issued[issue1Tag] && !completed[issue1Tag];
        if (issue0Valid && issue0Eligible && issue0Tag == issue1Tag) begin
            issue1Eligible = 1'b0;
        end

        completion0Ready = !retirementStalled &&
            completion0BaseEligible && !completion0ReadConflict;
        completion1Ready = !retirementStalled &&
            completion1BaseEligible && !completion1ReadConflict;
        if (completion0Valid && completion0Ready &&
            completion0Tag[1:0] == completion1Tag[1:0]) begin
            completion1Ready = 1'b0;
        end
    end

    always @* begin
        bankHasCompletion = 4'b0;
        for (bank = 0; bank < 4; bank = bank + 1) begin
            bankCompletionTag[bank] = bank[1:0];
            for (way = 0; way < 16; way = way + 1) begin
                tagIndex = way * 4 + bank;
                if (!bankHasCompletion[bank] && completed[tagIndex]) begin
                    bankHasCompletion[bank] = 1'b1;
                    bankCompletionTag[bank] = tagIndex[5:0];
                end
            end
        end

        unorderedFirstFound = 1'b0;
        unorderedSecondFound = 1'b0;
        unorderedFirstBank = 2'b0;
        unorderedSecondBank = 2'b0;
        candidateBank = 0;
        for (bankOffset = 0; bankOffset < 4;
             bankOffset = bankOffset + 1) begin
            candidateBank = unorderedBankHead + bankOffset;
            if (candidateBank >= 4) begin
                candidateBank = candidateBank - 4;
            end
            if (!unorderedFirstFound &&
                bankHasCompletion[candidateBank]) begin
                unorderedFirstFound = 1'b1;
                unorderedFirstBank = candidateBank[1:0];
            end else if (unorderedFirstFound && !unorderedSecondFound &&
                         bankHasCompletion[candidateBank]) begin
                unorderedSecondFound = 1'b1;
                unorderedSecondBank = candidateBank[1:0];
            end
        end

        retire0Valid = 1'b0;
        retire0Tag = 6'b0;
        retire1Valid = 1'b0;
        retire1Tag = 6'b0;
        if (orderedMode) begin
            retire0Valid = completed[orderedHead];
            retire0Tag = orderedHead;
            retire1Tag = orderedHead + 1'b1;
            retire1Valid = retire0Valid && completed[retire1Tag];
        end else begin
            retire0Valid = unorderedFirstFound;
            retire0Tag = bankCompletionTag[unorderedFirstBank];
            retire1Valid = unorderedSecondFound;
            retire1Tag = bankCompletionTag[unorderedSecondBank];
        end

        payloadReadValid = 4'b0;
        payloadBank0ReadWay = 4'b0;
        payloadBank1ReadWay = 4'b0;
        payloadBank2ReadWay = 4'b0;
        payloadBank3ReadWay = 4'b0;
        retire0Value = 64'b0;
        retire0Flags = 5'b0;
        retire1Value = 64'b0;
        retire1Flags = 5'b0;
        if (retire0Valid) begin
            payloadReadValid[retire0Tag[1:0]] = 1'b1;
            case (retire0Tag[1:0])
              2'd0: begin
                  payloadBank0ReadWay = retire0Tag[5:2];
                  retire0Value = payloadBank0ReadData[68:5];
                  retire0Flags = payloadBank0ReadData[4:0];
              end
              2'd1: begin
                  payloadBank1ReadWay = retire0Tag[5:2];
                  retire0Value = payloadBank1ReadData[68:5];
                  retire0Flags = payloadBank1ReadData[4:0];
              end
              2'd2: begin
                  payloadBank2ReadWay = retire0Tag[5:2];
                  retire0Value = payloadBank2ReadData[68:5];
                  retire0Flags = payloadBank2ReadData[4:0];
              end
              default: begin
                  payloadBank3ReadWay = retire0Tag[5:2];
                  retire0Value = payloadBank3ReadData[68:5];
                  retire0Flags = payloadBank3ReadData[4:0];
              end
            endcase
        end
        if (retire1Valid) begin
            payloadReadValid[retire1Tag[1:0]] = 1'b1;
            case (retire1Tag[1:0])
              2'd0: begin
                  payloadBank0ReadWay = retire1Tag[5:2];
                  retire1Value = payloadBank0ReadData[68:5];
                  retire1Flags = payloadBank0ReadData[4:0];
              end
              2'd1: begin
                  payloadBank1ReadWay = retire1Tag[5:2];
                  retire1Value = payloadBank1ReadData[68:5];
                  retire1Flags = payloadBank1ReadData[4:0];
              end
              2'd2: begin
                  payloadBank2ReadWay = retire1Tag[5:2];
                  retire1Value = payloadBank2ReadData[68:5];
                  retire1Flags = payloadBank2ReadData[4:0];
              end
              default: begin
                  payloadBank3ReadWay = retire1Tag[5:2];
                  retire1Value = payloadBank3ReadData[68:5];
                  retire1Flags = payloadBank3ReadData[4:0];
              end
            endcase
        end
    end

    always @* begin
        payloadWriteValid = 4'b0;
        payloadBank0WriteWay = 4'b0;
        payloadBank1WriteWay = 4'b0;
        payloadBank2WriteWay = 4'b0;
        payloadBank3WriteWay = 4'b0;
        payloadBank0WriteData = 69'b0;
        payloadBank1WriteData = 69'b0;
        payloadBank2WriteData = 69'b0;
        payloadBank3WriteData = 69'b0;
        if (completion0Accepted) begin
            payloadWriteValid[completion0Tag[1:0]] = 1'b1;
            case (completion0Tag[1:0])
              2'd0: begin
                  payloadBank0WriteWay = completion0Tag[5:2];
                  payloadBank0WriteData =
                      {completion0Value, completion0Flags};
              end
              2'd1: begin
                  payloadBank1WriteWay = completion0Tag[5:2];
                  payloadBank1WriteData =
                      {completion0Value, completion0Flags};
              end
              2'd2: begin
                  payloadBank2WriteWay = completion0Tag[5:2];
                  payloadBank2WriteData =
                      {completion0Value, completion0Flags};
              end
              default: begin
                  payloadBank3WriteWay = completion0Tag[5:2];
                  payloadBank3WriteData =
                      {completion0Value, completion0Flags};
              end
            endcase
        end
        if (completion1Accepted) begin
            payloadWriteValid[completion1Tag[1:0]] = 1'b1;
            case (completion1Tag[1:0])
              2'd0: begin
                  payloadBank0WriteWay = completion1Tag[5:2];
                  payloadBank0WriteData =
                      {completion1Value, completion1Flags};
              end
              2'd1: begin
                  payloadBank1WriteWay = completion1Tag[5:2];
                  payloadBank1WriteData =
                      {completion1Value, completion1Flags};
              end
              2'd2: begin
                  payloadBank2WriteWay = completion1Tag[5:2];
                  payloadBank2WriteData =
                      {completion1Value, completion1Flags};
              end
              default: begin
                  payloadBank3WriteWay = completion1Tag[5:2];
                  payloadBank3WriteData =
                      {completion1Value, completion1Flags};
              end
            endcase
        end
    end

    always @(posedge clock or negedge nReset) begin
        if (!nReset) begin
            epochActive <= 1'b0;
            orderedMode <= 1'b1;
            orderedHead <= 6'b0;
            unorderedBankHead <= 2'b0;
            occupiedCount <= 7'b0;
            allocated <= 64'b0;
            issued <= 64'b0;
            completed <= 64'b0;
            allocationsAccepted <= 32'b0;
            issuesAccepted <= 32'b0;
            completionsAccepted <= 32'b0;
            retirementsAccepted <= 32'b0;
            allocationBankConflictCycles <= 32'b0;
            completionBankConflictCycles <= 32'b0;
            completionReadConflictCycles <= 32'b0;
            duplicateAllocationCycles <= 32'b0;
            invalidCompletionCycles <= 32'b0;
            retirementBackpressureCycles <= 32'b0;
            protocolError <= 1'b0;
        end else begin
            if (configureAccepted) begin
                epochActive <= 1'b1;
                orderedMode <= configureOrdered;
                orderedHead <= configureHeadTag;
                unorderedBankHead <= configureHeadTag[1:0];
            end
            if (allocate0Accepted) begin
                allocated[allocate0Tag] <= 1'b1;
                issued[allocate0Tag] <= 1'b0;
                completed[allocate0Tag] <= 1'b0;
            end
            if (allocate1Accepted) begin
                allocated[allocate1Tag] <= 1'b1;
                issued[allocate1Tag] <= 1'b0;
                completed[allocate1Tag] <= 1'b0;
            end
            if (issue0Accepted) begin
                issued[issue0Tag] <= 1'b1;
            end
            if (issue1Accepted) begin
                issued[issue1Tag] <= 1'b1;
            end
            if ((issue0Commit && (!issue0Valid || !issue0Eligible)) ||
                (issue1Commit && (!issue1Valid || !issue1Eligible))) begin
                protocolError <= 1'b1;
            end
            if (completion0Accepted) begin
                completed[completion0Tag] <= 1'b1;
            end
            if (completion1Accepted) begin
                completed[completion1Tag] <= 1'b1;
            end
            if (completionSameTag) begin
                protocolError <= 1'b1;
            end
            if (retire0Accepted) begin
                allocated[retire0Tag] <= 1'b0;
                issued[retire0Tag] <= 1'b0;
                completed[retire0Tag] <= 1'b0;
            end
            if (retire1Accepted) begin
                allocated[retire1Tag] <= 1'b0;
                issued[retire1Tag] <= 1'b0;
                completed[retire1Tag] <= 1'b0;
            end
            if (retireReady && (retire0Valid || retire1Valid)) begin
                if (orderedMode) begin
                    orderedHead <= orderedHead +
                        retire0Valid + retire1Valid;
                end else if (retire1Valid) begin
                    unorderedBankHead <= unorderedSecondBank + 1'b1;
                end else begin
                    unorderedBankHead <= unorderedFirstBank + 1'b1;
                end
            end

            acceptedAllocations = allocate0Accepted + allocate1Accepted;
            acceptedRetirements = retire0Accepted + retire1Accepted;
            case ({acceptedAllocations != 0, acceptedRetirements != 0})
              2'b10: occupiedCount <=
                  occupiedCount + acceptedAllocations;
              2'b01: occupiedCount <=
                  occupiedCount - acceptedRetirements;
              2'b11: occupiedCount <= occupiedCount +
                  acceptedAllocations - acceptedRetirements;
              default: occupiedCount <= occupiedCount;
            endcase

            if (acceptedAllocations != 0) begin
                allocationsAccepted <= allocationsAccepted +
                    acceptedAllocations;
            end
            if (issue0Accepted || issue1Accepted) begin
                issuesAccepted <= issuesAccepted +
                    issue0Accepted + issue1Accepted;
            end
            if (completion0Accepted || completion1Accepted) begin
                completionsAccepted <= completionsAccepted +
                    completion0Accepted + completion1Accepted;
            end
            if (acceptedRetirements != 0) begin
                retirementsAccepted <= retirementsAccepted +
                    acceptedRetirements;
            end
            if (allocationBankConflict) begin
                allocationBankConflictCycles <=
                    allocationBankConflictCycles + 1'b1;
            end
            if (completionBankConflict && !completionSameTag) begin
                completionBankConflictCycles <=
                    completionBankConflictCycles + 1'b1;
            end
            if (completion0ReadConflict || completion1ReadConflict) begin
                completionReadConflictCycles <=
                    completionReadConflictCycles + 1'b1;
            end
            if ((allocate0Valid && allocated[allocate0Tag]) ||
                (allocate1Valid && allocated[allocate1Tag]) ||
                (allocate0Valid && allocate1Valid &&
                 allocate0Tag == allocate1Tag)) begin
                duplicateAllocationCycles <=
                    duplicateAllocationCycles + 1'b1;
            end
            if ((completion0Valid && !completion0BaseEligible) ||
                (completion1Valid && !completion1BaseEligible)) begin
                invalidCompletionCycles <=
                    invalidCompletionCycles + 1'b1;
                protocolError <= 1'b1;
            end
            if (retirementStalled) begin
                retirementBackpressureCycles <=
                    retirementBackpressureCycles + 1'b1;
            end
        end
    end
endmodule

// Simulation-only payload model.  The data arrays deliberately have no reset;
// only a completed entry may be read.  They model four 16x69 slices within the
// already-budgeted 64x256 operation window and are excluded from control-only
// physical accounting.
module LanlMaaOperationPayloadOverlayModel64x4(
    input clock,
    input [3:0] readValid,
    input [3:0] bank0ReadWay,
    input [3:0] bank1ReadWay,
    input [3:0] bank2ReadWay,
    input [3:0] bank3ReadWay,
    output [68:0] bank0ReadData,
    output [68:0] bank1ReadData,
    output [68:0] bank2ReadData,
    output [68:0] bank3ReadData,
    input [3:0] writeValid,
    input [3:0] bank0WriteWay,
    input [3:0] bank1WriteWay,
    input [3:0] bank2WriteWay,
    input [3:0] bank3WriteWay,
    input [68:0] bank0WriteData,
    input [68:0] bank1WriteData,
    input [68:0] bank2WriteData,
    input [68:0] bank3WriteData
);
    reg [68:0] bank0 [0:15];
    reg [68:0] bank1 [0:15];
    reg [68:0] bank2 [0:15];
    reg [68:0] bank3 [0:15];

    assign bank0ReadData = readValid[0] ? bank0[bank0ReadWay] : 69'b0;
    assign bank1ReadData = readValid[1] ? bank1[bank1ReadWay] : 69'b0;
    assign bank2ReadData = readValid[2] ? bank2[bank2ReadWay] : 69'b0;
    assign bank3ReadData = readValid[3] ? bank3[bank3ReadWay] : 69'b0;

    always @(posedge clock) begin
        if (writeValid[0]) begin
            bank0[bank0WriteWay] <= bank0WriteData;
        end
        if (writeValid[1]) begin
            bank1[bank1WriteWay] <= bank1WriteData;
        end
        if (writeValid[2]) begin
            bank2[bank2WriteWay] <= bank2WriteData;
        end
        if (writeValid[3]) begin
            bank3[bank3WriteWay] <= bank3WriteData;
        end
    end
endmodule

module LanlMaaOperationRetirementOverlayModel64x4x2(
    input clock,
    input nReset,
    input configureValid,
    output configureReady,
    input configureOrdered,
    input [5:0] configureHeadTag,
    input allocate0Valid,
    input [5:0] allocate0Tag,
    output allocate0Ready,
    input allocate1Valid,
    input [5:0] allocate1Tag,
    output allocate1Ready,
    input issue0Valid,
    input [5:0] issue0Tag,
    output issue0Eligible,
    input issue0Commit,
    input issue1Valid,
    input [5:0] issue1Tag,
    output issue1Eligible,
    input issue1Commit,
    input completion0Valid,
    output completion0Ready,
    input [5:0] completion0Tag,
    input [63:0] completion0Value,
    input [4:0] completion0Flags,
    input completion1Valid,
    output completion1Ready,
    input [5:0] completion1Tag,
    input [63:0] completion1Value,
    input [4:0] completion1Flags,
    output retire0Valid,
    output [5:0] retire0Tag,
    output [63:0] retire0Value,
    output [4:0] retire0Flags,
    output retire1Valid,
    output [5:0] retire1Tag,
    output [63:0] retire1Value,
    output [4:0] retire1Flags,
    input retireReady,
    output [6:0] occupancy,
    output idle,
    output [31:0] allocationsAccepted,
    output [31:0] issuesAccepted,
    output [31:0] completionsAccepted,
    output [31:0] retirementsAccepted,
    output [31:0] allocationBankConflictCycles,
    output [31:0] completionBankConflictCycles,
    output [31:0] completionReadConflictCycles,
    output [31:0] duplicateAllocationCycles,
    output [31:0] invalidCompletionCycles,
    output [31:0] retirementBackpressureCycles,
    output protocolError
);
    wire [3:0] payloadReadValid;
    wire [3:0] payloadBank0ReadWay;
    wire [3:0] payloadBank1ReadWay;
    wire [3:0] payloadBank2ReadWay;
    wire [3:0] payloadBank3ReadWay;
    wire [68:0] payloadBank0ReadData;
    wire [68:0] payloadBank1ReadData;
    wire [68:0] payloadBank2ReadData;
    wire [68:0] payloadBank3ReadData;
    wire [3:0] payloadWriteValid;
    wire [3:0] payloadBank0WriteWay;
    wire [3:0] payloadBank1WriteWay;
    wire [3:0] payloadBank2WriteWay;
    wire [3:0] payloadBank3WriteWay;
    wire [68:0] payloadBank0WriteData;
    wire [68:0] payloadBank1WriteData;
    wire [68:0] payloadBank2WriteData;
    wire [68:0] payloadBank3WriteData;

    LanlMaaOperationRetirementOverlay64x4x2 control(
        .clock(clock),
        .nReset(nReset),
        .configureValid(configureValid),
        .configureReady(configureReady),
        .configureOrdered(configureOrdered),
        .configureHeadTag(configureHeadTag),
        .allocate0Valid(allocate0Valid),
        .allocate0Tag(allocate0Tag),
        .allocate0Ready(allocate0Ready),
        .allocate1Valid(allocate1Valid),
        .allocate1Tag(allocate1Tag),
        .allocate1Ready(allocate1Ready),
        .issue0Valid(issue0Valid),
        .issue0Tag(issue0Tag),
        .issue0Eligible(issue0Eligible),
        .issue0Commit(issue0Commit),
        .issue1Valid(issue1Valid),
        .issue1Tag(issue1Tag),
        .issue1Eligible(issue1Eligible),
        .issue1Commit(issue1Commit),
        .completion0Valid(completion0Valid),
        .completion0Ready(completion0Ready),
        .completion0Tag(completion0Tag),
        .completion0Value(completion0Value),
        .completion0Flags(completion0Flags),
        .completion1Valid(completion1Valid),
        .completion1Ready(completion1Ready),
        .completion1Tag(completion1Tag),
        .completion1Value(completion1Value),
        .completion1Flags(completion1Flags),
        .payloadReadValid(payloadReadValid),
        .payloadBank0ReadWay(payloadBank0ReadWay),
        .payloadBank1ReadWay(payloadBank1ReadWay),
        .payloadBank2ReadWay(payloadBank2ReadWay),
        .payloadBank3ReadWay(payloadBank3ReadWay),
        .payloadBank0ReadData(payloadBank0ReadData),
        .payloadBank1ReadData(payloadBank1ReadData),
        .payloadBank2ReadData(payloadBank2ReadData),
        .payloadBank3ReadData(payloadBank3ReadData),
        .payloadWriteValid(payloadWriteValid),
        .payloadBank0WriteWay(payloadBank0WriteWay),
        .payloadBank1WriteWay(payloadBank1WriteWay),
        .payloadBank2WriteWay(payloadBank2WriteWay),
        .payloadBank3WriteWay(payloadBank3WriteWay),
        .payloadBank0WriteData(payloadBank0WriteData),
        .payloadBank1WriteData(payloadBank1WriteData),
        .payloadBank2WriteData(payloadBank2WriteData),
        .payloadBank3WriteData(payloadBank3WriteData),
        .retire0Valid(retire0Valid),
        .retire0Tag(retire0Tag),
        .retire0Value(retire0Value),
        .retire0Flags(retire0Flags),
        .retire1Valid(retire1Valid),
        .retire1Tag(retire1Tag),
        .retire1Value(retire1Value),
        .retire1Flags(retire1Flags),
        .retireReady(retireReady),
        .occupancy(occupancy),
        .idle(idle),
        .allocationsAccepted(allocationsAccepted),
        .issuesAccepted(issuesAccepted),
        .completionsAccepted(completionsAccepted),
        .retirementsAccepted(retirementsAccepted),
        .allocationBankConflictCycles(allocationBankConflictCycles),
        .completionBankConflictCycles(completionBankConflictCycles),
        .completionReadConflictCycles(completionReadConflictCycles),
        .duplicateAllocationCycles(duplicateAllocationCycles),
        .invalidCompletionCycles(invalidCompletionCycles),
        .retirementBackpressureCycles(retirementBackpressureCycles),
        .protocolError(protocolError)
    );

    LanlMaaOperationPayloadOverlayModel64x4 payload(
        .clock(clock),
        .readValid(payloadReadValid),
        .bank0ReadWay(payloadBank0ReadWay),
        .bank1ReadWay(payloadBank1ReadWay),
        .bank2ReadWay(payloadBank2ReadWay),
        .bank3ReadWay(payloadBank3ReadWay),
        .bank0ReadData(payloadBank0ReadData),
        .bank1ReadData(payloadBank1ReadData),
        .bank2ReadData(payloadBank2ReadData),
        .bank3ReadData(payloadBank3ReadData),
        .writeValid(payloadWriteValid),
        .bank0WriteWay(payloadBank0WriteWay),
        .bank1WriteWay(payloadBank1WriteWay),
        .bank2WriteWay(payloadBank2WriteWay),
        .bank3WriteWay(payloadBank3WriteWay),
        .bank0WriteData(payloadBank0WriteData),
        .bank1WriteData(payloadBank1WriteData),
        .bank2WriteData(payloadBank2WriteData),
        .bank3WriteData(payloadBank3WriteData)
    );
endmodule

module LanlFp64Portfolio2SSharedRecode1A1M8DCompletion2WSplitRetirementOverlay64x4x2(
    input clock,
    input nReset,
    input configureValid,
    output configureReady,
    input configureOrdered,
    input [5:0] configureHeadTag,
    input allocate0Valid,
    input [5:0] allocate0Tag,
    output allocate0Ready,
    input allocate1Valid,
    input [5:0] allocate1Tag,
    output allocate1Ready,
    input req0Valid,
    input [1:0] req0Op,
    input [5:0] req0Tag,
    input [63:0] req0A,
    input [63:0] req0B,
    output req0Ready,
    input req1Valid,
    input [1:0] req1Op,
    input [5:0] req1Tag,
    input [63:0] req1A,
    input [63:0] req1B,
    output req1Ready,
    output retire0Valid,
    output [5:0] retire0Tag,
    output [63:0] retire0Value,
    output [4:0] retire0Flags,
    output retire1Valid,
    output [5:0] retire1Tag,
    output [63:0] retire1Value,
    output [4:0] retire1Flags,
    input retireReady,
    output [6:0] occupancy,
    output idle,
    output [31:0] allocationsAccepted,
    output [31:0] issuesAccepted,
    output [31:0] completionsAccepted,
    output [31:0] retirementsAccepted,
    output [31:0] allocationBankConflictCycles,
    output [31:0] completionBankConflictCycles,
    output [31:0] completionReadConflictCycles,
    output [31:0] duplicateAllocationCycles,
    output [31:0] invalidCompletionCycles,
    output [31:0] retirementBackpressureCycles,
    output [31:0] backendCompletionsCaptured,
    output [31:0] backendCompletionsTransferred,
    output [31:0] backendCompletionBackpressureCycles,
    output protocolError,
    output backendOverflow
);
    wire issue0Eligible;
    wire issue1Eligible;
    wire backendReq0Ready;
    wire backendReq1Ready;
    wire backendCompletion0Valid;
    wire backendCompletion0Ready;
    wire [5:0] backendCompletion0Tag;
    wire [63:0] backendCompletion0Value;
    wire [4:0] backendCompletion0Flags;
    wire backendCompletion1Valid;
    wire backendCompletion1Ready;
    wire [5:0] backendCompletion1Tag;
    wire [63:0] backendCompletion1Value;
    wire [4:0] backendCompletion1Flags;
    wire issue0Commit = req0Valid && req0Ready;
    wire issue1Commit = req1Valid && req1Ready;

    assign req0Ready = issue0Eligible && backendReq0Ready;
    assign req1Ready = issue1Eligible && backendReq1Ready;

    LanlFp64Portfolio2SSharedRecode1A1M8DCompletion2WSplit backend(
        clock, nReset,
        req0Valid && issue0Eligible,
        req0Op, req0Tag, req0A, req0B, backendReq0Ready,
        req1Valid && issue1Eligible,
        req1Op, req1Tag, req1A, req1B, backendReq1Ready,
        backendCompletion0Valid, backendCompletion0Ready,
        backendCompletion0Tag, backendCompletion0Value,
        backendCompletion0Flags,
        backendCompletion1Valid, backendCompletion1Ready,
        backendCompletion1Tag, backendCompletion1Value,
        backendCompletion1Flags,
        backendCompletionsCaptured, backendCompletionsTransferred,
        backendCompletionBackpressureCycles, backendOverflow
    );

    LanlMaaOperationRetirementOverlayModel64x4x2 retirement(
        .clock(clock),
        .nReset(nReset),
        .configureValid(configureValid),
        .configureReady(configureReady),
        .configureOrdered(configureOrdered),
        .configureHeadTag(configureHeadTag),
        .allocate0Valid(allocate0Valid),
        .allocate0Tag(allocate0Tag),
        .allocate0Ready(allocate0Ready),
        .allocate1Valid(allocate1Valid),
        .allocate1Tag(allocate1Tag),
        .allocate1Ready(allocate1Ready),
        .issue0Valid(req0Valid),
        .issue0Tag(req0Tag),
        .issue0Eligible(issue0Eligible),
        .issue0Commit(issue0Commit),
        .issue1Valid(req1Valid),
        .issue1Tag(req1Tag),
        .issue1Eligible(issue1Eligible),
        .issue1Commit(issue1Commit),
        .completion0Valid(backendCompletion0Valid),
        .completion0Ready(backendCompletion0Ready),
        .completion0Tag(backendCompletion0Tag),
        .completion0Value(backendCompletion0Value),
        .completion0Flags(backendCompletion0Flags),
        .completion1Valid(backendCompletion1Valid),
        .completion1Ready(backendCompletion1Ready),
        .completion1Tag(backendCompletion1Tag),
        .completion1Value(backendCompletion1Value),
        .completion1Flags(backendCompletion1Flags),
        .retire0Valid(retire0Valid),
        .retire0Tag(retire0Tag),
        .retire0Value(retire0Value),
        .retire0Flags(retire0Flags),
        .retire1Valid(retire1Valid),
        .retire1Tag(retire1Tag),
        .retire1Value(retire1Value),
        .retire1Flags(retire1Flags),
        .retireReady(retireReady),
        .occupancy(occupancy),
        .idle(idle),
        .allocationsAccepted(allocationsAccepted),
        .issuesAccepted(issuesAccepted),
        .completionsAccepted(completionsAccepted),
        .retirementsAccepted(retirementsAccepted),
        .allocationBankConflictCycles(allocationBankConflictCycles),
        .completionBankConflictCycles(completionBankConflictCycles),
        .completionReadConflictCycles(completionReadConflictCycles),
        .duplicateAllocationCycles(duplicateAllocationCycles),
        .invalidCompletionCycles(invalidCompletionCycles),
        .retirementBackpressureCycles(retirementBackpressureCycles),
        .protocolError(protocolError)
    );
endmodule
