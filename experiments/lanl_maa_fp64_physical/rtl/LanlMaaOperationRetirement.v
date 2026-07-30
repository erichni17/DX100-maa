`timescale 1ns/1ps

module LanlMaaOperationRetirement64x4x2(
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

    reg allocated [0:63];
    reg issued [0:63];
    reg completed [0:63];
    reg [63:0] resultValue [0:63];
    reg [4:0] resultFlags [0:63];

    reg [3:0] bankHasCompletion;
    reg [5:0] bankCompletionTag [0:3];
    reg unorderedFirstFound;
    reg unorderedSecondFound;
    reg [1:0] unorderedFirstBank;
    reg [1:0] unorderedSecondBank;

    wire configureAccepted = configureValid && configureReady;
    wire allocate0Accepted = allocate0Valid && allocate0Ready;
    wire allocate1Accepted = allocate1Valid && allocate1Ready;
    wire issue0Accepted = issue0Commit && issue0Eligible;
    wire issue1Accepted = issue1Commit && issue1Eligible;
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
    wire allocationBankConflict =
        allocate0Valid && allocate1Valid && allocate0Ready &&
        !allocated[allocate1Tag] &&
        allocate0Tag[1:0] == allocate1Tag[1:0];
    integer entry;
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

        completion0Ready = !retirementStalled && completion0BaseEligible;
        completion1Ready = !retirementStalled && completion1BaseEligible;
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
        retire0Value = 64'b0;
        retire0Flags = 5'b0;
        retire1Valid = 1'b0;
        retire1Tag = 6'b0;
        retire1Value = 64'b0;
        retire1Flags = 5'b0;
        if (orderedMode) begin
            retire0Valid = completed[orderedHead];
            retire0Tag = orderedHead;
            retire0Value = resultValue[orderedHead];
            retire0Flags = resultFlags[orderedHead];
            retire1Tag = orderedHead + 1'b1;
            retire1Valid = retire0Valid && completed[retire1Tag];
            retire1Value = resultValue[retire1Tag];
            retire1Flags = resultFlags[retire1Tag];
        end else begin
            retire0Valid = unorderedFirstFound;
            retire0Tag = bankCompletionTag[unorderedFirstBank];
            retire0Value = resultValue[retire0Tag];
            retire0Flags = resultFlags[retire0Tag];
            retire1Valid = unorderedSecondFound;
            retire1Tag = bankCompletionTag[unorderedSecondBank];
            retire1Value = resultValue[retire1Tag];
            retire1Flags = resultFlags[retire1Tag];
        end
    end

    always @(posedge clock or negedge nReset) begin
        if (!nReset) begin
            epochActive <= 1'b0;
            orderedMode <= 1'b1;
            orderedHead <= 6'b0;
            unorderedBankHead <= 2'b0;
            occupiedCount <= 7'b0;
            allocationsAccepted <= 32'b0;
            issuesAccepted <= 32'b0;
            completionsAccepted <= 32'b0;
            retirementsAccepted <= 32'b0;
            allocationBankConflictCycles <= 32'b0;
            completionBankConflictCycles <= 32'b0;
            duplicateAllocationCycles <= 32'b0;
            invalidCompletionCycles <= 32'b0;
            retirementBackpressureCycles <= 32'b0;
            protocolError <= 1'b0;
            for (entry = 0; entry < 64; entry = entry + 1) begin
                allocated[entry] <= 1'b0;
                issued[entry] <= 1'b0;
                completed[entry] <= 1'b0;
                resultValue[entry] <= 64'b0;
                resultFlags[entry] <= 5'b0;
            end
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
            if ((issue0Commit && !issue0Eligible) ||
                (issue1Commit && !issue1Eligible)) begin
                protocolError <= 1'b1;
            end

            if (completion0Accepted) begin
                completed[completion0Tag] <= 1'b1;
                resultValue[completion0Tag] <= completion0Value;
                resultFlags[completion0Tag] <= completion0Flags;
            end
            if (completion1Accepted) begin
                completed[completion1Tag] <= 1'b1;
                resultValue[completion1Tag] <= completion1Value;
                resultFlags[completion1Tag] <= completion1Flags;
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
                    orderedHead <= orderedHead + retire0Valid + retire1Valid;
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
                allocationsAccepted <=
                    allocationsAccepted + acceptedAllocations;
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
                retirementsAccepted <=
                    retirementsAccepted + acceptedRetirements;
            end
            if (allocationBankConflict) begin
                allocationBankConflictCycles <=
                    allocationBankConflictCycles + 1'b1;
            end
            if (completionBankConflict && !completionSameTag) begin
                completionBankConflictCycles <=
                    completionBankConflictCycles + 1'b1;
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

module LanlFp64Portfolio2SSharedRecode1A1M8DCompletion2WSplitRetirement64x4x2(
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

    LanlMaaOperationRetirement64x4x2 retirement(
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
        .duplicateAllocationCycles(duplicateAllocationCycles),
        .invalidCompletionCycles(invalidCompletionCycles),
        .retirementBackpressureCycles(retirementBackpressureCycles),
        .protocolError(protocolError)
    );
endmodule
