`timescale 1ns/1ps

module LanlMaaLineTable32x4LinkedWaiters(
    input clock,
    input nReset,

    input issue0Valid,
    input [41:0] issue0Line,
    input [5:0] issue0Slot,
    output reg issue0Ready,
    output reg issue0Merged,

    input issue1Valid,
    input [41:0] issue1Line,
    input [5:0] issue1Slot,
    output reg issue1Ready,
    output reg issue1Merged,

    output reg requestValid,
    input requestReady,
    output reg [41:0] requestLine,
    output reg [20:0] requestToken,

    input responseValid,
    input [20:0] responseToken,
    output responseReady,
    output reg staleResponse,

    output reg completionValid,
    input completionReady,
    output reg [5:0] completionSlot,

    output reg [31:0] acceptedSlots,
    output reg [31:0] mergedSlots,
    output reg [31:0] bankConflictCycles,
    output reg [31:0] tableWouldBlockCycles,
    output reg [31:0] addressBusyCycles,
    output reg [31:0] duplicateIssueCycles,
    output reg [31:0] lineRequests,
    output reg [31:0] staleResponses,
    output reg [31:0] completionAcks
);
    localparam [1:0] STATE_FREE = 2'b00;
    localparam [1:0] STATE_ALLOCATED = 2'b01;
    localparam [1:0] STATE_IN_FLIGHT = 2'b10;
    localparam [1:0] STATE_DRAINING = 2'b11;

    reg [1:0] entryState [0:31];
    reg [41:0] entryLine [0:31];
    reg [15:0] entryGeneration [0:31];
    reg [5:0] entryWaiterHead [0:31];
    reg [5:0] entryWaiterTail [0:31];
    reg [6:0] entryWaiterCount [0:31];
    reg [5:0] slotNext [0:63];
    reg slotWaiting [0:63];

    reg requestHoldValid;
    reg [4:0] requestHoldIndex;
    reg [4:0] drainQueue [0:31];
    reg [4:0] drainHead;
    reg [4:0] drainTail;
    reg [5:0] drainCount;

    reg issue0Hit;
    reg issue0DrainHit;
    reg issue0FreeFound;
    reg [4:0] issue0HitIndex;
    reg [4:0] issue0FreeIndex;
    reg [4:0] issue0SelectedIndex;
    reg issue1Hit;
    reg issue1DrainHit;
    reg issue1FreeFound;
    reg [4:0] issue1HitIndex;
    reg [4:0] issue1FreeIndex;
    reg [4:0] issue1SelectedIndex;
    reg issueBankConflict;
    reg issueTableBlocked;
    reg issueAddressBusy;
    reg issueDuplicate;

    reg requestFound;
    reg [4:0] requestFoundIndex;
    reg [4:0] requestSelectedIndex;
    reg [4:0] completionSelectedIndex;
    reg [5:0] completionSelectedSlot;

    wire issue0Accepted = issue0Valid && issue0Ready;
    wire issue1Accepted = issue1Valid && issue1Ready;
    wire [4:0] responseIndex = responseToken[4:0];
    wire [15:0] responseGeneration = responseToken[20:5];
    wire responseMatches =
        entryState[responseIndex] == STATE_IN_FLIGHT &&
        entryGeneration[responseIndex] == responseGeneration;
    wire responseAccepted = responseValid && responseReady;
    wire responseEnqueues = responseAccepted && responseMatches;
    wire completionAccepted = completionValid && completionReady;
    wire completionFinishesEntry = completionAccepted &&
        entryWaiterCount[completionSelectedIndex] == 7'd1;

    integer issueIndex;
    integer issueWay;
    integer requestIndex;
    integer resetIndex;
    integer resetSlot;

    assign responseReady = !responseMatches || drainCount < 6'd32;

    always @* begin
        issue0Hit = 1'b0;
        issue0DrainHit = 1'b0;
        issue0FreeFound = 1'b0;
        issue0HitIndex = 5'b0;
        issue0FreeIndex = 5'b0;
        for (issueWay = 0; issueWay < 8; issueWay = issueWay + 1) begin
            issueIndex = {issue0Line[1:0], 3'b000} + issueWay;
            if (!issue0Hit &&
                (entryState[issueIndex] == STATE_ALLOCATED ||
                 entryState[issueIndex] == STATE_IN_FLIGHT) &&
                entryLine[issueIndex] == issue0Line) begin
                issue0Hit = 1'b1;
                issue0HitIndex = issueIndex[4:0];
            end
            if (!issue0DrainHit &&
                entryState[issueIndex] == STATE_DRAINING &&
                entryLine[issueIndex] == issue0Line) begin
                issue0DrainHit = 1'b1;
            end
            if (!issue0FreeFound &&
                entryState[issueIndex] == STATE_FREE) begin
                issue0FreeFound = 1'b1;
                issue0FreeIndex = issueIndex[4:0];
            end
        end

        issue0SelectedIndex = issue0Hit ? issue0HitIndex : issue0FreeIndex;
        issue0Ready = !issue0DrainHit &&
            (issue0Hit || issue0FreeFound) &&
            !slotWaiting[issue0Slot];
        issue0Merged = issue0Ready && issue0Hit;

        issue1Hit = 1'b0;
        issue1DrainHit = 1'b0;
        issue1FreeFound = 1'b0;
        issue1HitIndex = 5'b0;
        issue1FreeIndex = 5'b0;
        for (issueWay = 0; issueWay < 8; issueWay = issueWay + 1) begin
            issueIndex = {issue1Line[1:0], 3'b000} + issueWay;
            if (!issue1Hit &&
                (entryState[issueIndex] == STATE_ALLOCATED ||
                 entryState[issueIndex] == STATE_IN_FLIGHT) &&
                entryLine[issueIndex] == issue1Line) begin
                issue1Hit = 1'b1;
                issue1HitIndex = issueIndex[4:0];
            end
            if (!issue1DrainHit &&
                entryState[issueIndex] == STATE_DRAINING &&
                entryLine[issueIndex] == issue1Line) begin
                issue1DrainHit = 1'b1;
            end
            if (!issue1FreeFound &&
                entryState[issueIndex] == STATE_FREE) begin
                issue1FreeFound = 1'b1;
                issue1FreeIndex = issueIndex[4:0];
            end
        end

        issueBankConflict = 1'b0;
        issue1SelectedIndex = issue1Hit ? issue1HitIndex : issue1FreeIndex;
        issue1Ready = !issue1DrainHit &&
            (issue1Hit || issue1FreeFound) &&
            !slotWaiting[issue1Slot];
        issue1Merged = issue1Ready && issue1Hit;
        if (issue0Valid && issue1Valid && issue0Line == issue1Line) begin
            issue1SelectedIndex = issue0SelectedIndex;
            issue1Ready = issue0Ready && issue0Slot != issue1Slot &&
                !slotWaiting[issue1Slot];
            issue1Merged = issue1Ready;
        end else if (issue0Valid && issue1Valid &&
                     issue0Line[1:0] == issue1Line[1:0]) begin
            issueBankConflict = 1'b1;
            issue1Ready = 1'b0;
            issue1Merged = 1'b0;
        end else if (issue0Valid && issue1Valid &&
                     issue0Slot == issue1Slot) begin
            issue1Ready = 1'b0;
            issue1Merged = 1'b0;
        end

        issueTableBlocked =
            (issue0Valid && !issue0Hit && !issue0FreeFound &&
             !issue0DrainHit) ||
            (issue1Valid && !issue1Hit && !issue1FreeFound &&
             !issue1DrainHit && !issueBankConflict &&
             issue0Line != issue1Line);
        issueAddressBusy =
            (issue0Valid && issue0DrainHit) ||
            (issue1Valid && issue1DrainHit && !issueBankConflict);
        issueDuplicate =
            (issue0Valid && slotWaiting[issue0Slot]) ||
            (issue1Valid && slotWaiting[issue1Slot]) ||
            (issue0Valid && issue1Valid &&
             issue0Slot == issue1Slot);
    end

    always @* begin
        requestFound = 1'b0;
        requestFoundIndex = 5'b0;
        for (requestIndex = 0; requestIndex < 32;
             requestIndex = requestIndex + 1) begin
            if (!requestFound &&
                entryState[requestIndex] == STATE_ALLOCATED) begin
                requestFound = 1'b1;
                requestFoundIndex = requestIndex[4:0];
            end
        end

        requestValid = requestHoldValid || requestFound;
        requestSelectedIndex = requestHoldValid ? requestHoldIndex :
                                                        requestFoundIndex;
        requestLine = entryLine[requestSelectedIndex];
        requestToken = {
            entryGeneration[requestSelectedIndex], requestSelectedIndex};
    end

    always @* begin
        completionSelectedIndex = drainQueue[drainHead];
        completionSelectedSlot = entryWaiterHead[completionSelectedIndex];
        completionValid = drainCount != 0 &&
            entryWaiterCount[completionSelectedIndex] != 0;
        completionSlot = completionSelectedSlot;
    end

    always @(posedge clock) begin
        if (!nReset) begin
            requestHoldValid <= 1'b0;
            requestHoldIndex <= 5'b0;
            drainHead <= 5'b0;
            drainTail <= 5'b0;
            drainCount <= 6'b0;
            staleResponse <= 1'b0;
            acceptedSlots <= 32'b0;
            mergedSlots <= 32'b0;
            bankConflictCycles <= 32'b0;
            tableWouldBlockCycles <= 32'b0;
            addressBusyCycles <= 32'b0;
            duplicateIssueCycles <= 32'b0;
            lineRequests <= 32'b0;
            staleResponses <= 32'b0;
            completionAcks <= 32'b0;
            for (resetIndex = 0; resetIndex < 32;
                 resetIndex = resetIndex + 1) begin
                entryState[resetIndex] <= STATE_FREE;
                entryLine[resetIndex] <= 42'b0;
                entryGeneration[resetIndex] <= 16'b0;
                entryWaiterHead[resetIndex] <= 6'b0;
                entryWaiterTail[resetIndex] <= 6'b0;
                entryWaiterCount[resetIndex] <= 7'b0;
                drainQueue[resetIndex] <= 5'b0;
            end
            for (resetSlot = 0; resetSlot < 64;
                 resetSlot = resetSlot + 1) begin
                slotNext[resetSlot] <= 6'b0;
                slotWaiting[resetSlot] <= 1'b0;
            end
        end else begin
            staleResponse <= 1'b0;

            if (!requestHoldValid && requestFound && !requestReady) begin
                requestHoldValid <= 1'b1;
                requestHoldIndex <= requestFoundIndex;
            end else if (requestHoldValid && requestReady) begin
                requestHoldValid <= 1'b0;
            end
            if (requestValid && requestReady) begin
                entryState[requestSelectedIndex] <= STATE_IN_FLIGHT;
                lineRequests <= lineRequests + 1'b1;
            end

            if (completionAccepted) begin
                slotWaiting[completionSelectedSlot] <= 1'b0;
                if (completionFinishesEntry) begin
                    entryState[completionSelectedIndex] <= STATE_FREE;
                    entryWaiterCount[completionSelectedIndex] <= 7'b0;
                    drainHead <= drainHead + 1'b1;
                end else begin
                    entryWaiterHead[completionSelectedIndex] <=
                        slotNext[completionSelectedSlot];
                    entryWaiterCount[completionSelectedIndex] <=
                        entryWaiterCount[completionSelectedIndex] - 1'b1;
                end
                completionAcks <= completionAcks + 1'b1;
            end

            if (responseAccepted) begin
                if (responseMatches) begin
                    entryState[responseIndex] <= STATE_DRAINING;
                    drainQueue[drainTail] <= responseIndex;
                    drainTail <= drainTail + 1'b1;
                end else begin
                    staleResponse <= 1'b1;
                    staleResponses <= staleResponses + 1'b1;
                end
            end

            case ({responseEnqueues, completionFinishesEntry})
              2'b10: drainCount <= drainCount + 1'b1;
              2'b01: drainCount <= drainCount - 1'b1;
              default: drainCount <= drainCount;
            endcase

            if (issue0Accepted && issue1Accepted &&
                issue0SelectedIndex == issue1SelectedIndex) begin
                if (entryState[issue0SelectedIndex] == STATE_FREE) begin
                    entryState[issue0SelectedIndex] <= STATE_ALLOCATED;
                    entryLine[issue0SelectedIndex] <= issue0Line;
                    entryGeneration[issue0SelectedIndex] <=
                        entryGeneration[issue0SelectedIndex] + 1'b1;
                    entryWaiterHead[issue0SelectedIndex] <= issue0Slot;
                    entryWaiterTail[issue0SelectedIndex] <= issue1Slot;
                    entryWaiterCount[issue0SelectedIndex] <= 7'd2;
                end else begin
                    slotNext[entryWaiterTail[issue0SelectedIndex]] <=
                        issue0Slot;
                    entryWaiterTail[issue0SelectedIndex] <= issue1Slot;
                    entryWaiterCount[issue0SelectedIndex] <=
                        entryWaiterCount[issue0SelectedIndex] + 2'd2;
                end
                slotWaiting[issue0Slot] <= 1'b1;
                slotWaiting[issue1Slot] <= 1'b1;
                slotNext[issue0Slot] <= issue1Slot;
            end else begin
                if (issue0Accepted) begin
                    if (entryState[issue0SelectedIndex] == STATE_FREE) begin
                        entryState[issue0SelectedIndex] <= STATE_ALLOCATED;
                        entryLine[issue0SelectedIndex] <= issue0Line;
                        entryGeneration[issue0SelectedIndex] <=
                            entryGeneration[issue0SelectedIndex] + 1'b1;
                        entryWaiterHead[issue0SelectedIndex] <= issue0Slot;
                        entryWaiterTail[issue0SelectedIndex] <= issue0Slot;
                        entryWaiterCount[issue0SelectedIndex] <= 7'd1;
                    end else begin
                        slotNext[entryWaiterTail[issue0SelectedIndex]] <=
                            issue0Slot;
                        entryWaiterTail[issue0SelectedIndex] <= issue0Slot;
                        entryWaiterCount[issue0SelectedIndex] <=
                            entryWaiterCount[issue0SelectedIndex] + 1'b1;
                    end
                    slotWaiting[issue0Slot] <= 1'b1;
                end
                if (issue1Accepted) begin
                    if (entryState[issue1SelectedIndex] == STATE_FREE) begin
                        entryState[issue1SelectedIndex] <= STATE_ALLOCATED;
                        entryLine[issue1SelectedIndex] <= issue1Line;
                        entryGeneration[issue1SelectedIndex] <=
                            entryGeneration[issue1SelectedIndex] + 1'b1;
                        entryWaiterHead[issue1SelectedIndex] <= issue1Slot;
                        entryWaiterTail[issue1SelectedIndex] <= issue1Slot;
                        entryWaiterCount[issue1SelectedIndex] <= 7'd1;
                    end else begin
                        slotNext[entryWaiterTail[issue1SelectedIndex]] <=
                            issue1Slot;
                        entryWaiterTail[issue1SelectedIndex] <= issue1Slot;
                        entryWaiterCount[issue1SelectedIndex] <=
                            entryWaiterCount[issue1SelectedIndex] + 1'b1;
                    end
                    slotWaiting[issue1Slot] <= 1'b1;
                end
            end

            if (issue0Accepted || issue1Accepted) begin
                acceptedSlots <= acceptedSlots + issue0Accepted +
                                                      issue1Accepted;
            end
            if ((issue0Accepted && issue0Merged) ||
                (issue1Accepted && issue1Merged)) begin
                mergedSlots <= mergedSlots +
                    (issue0Accepted && issue0Merged) +
                    (issue1Accepted && issue1Merged);
            end
            if (issueBankConflict) begin
                bankConflictCycles <= bankConflictCycles + 1'b1;
            end
            if (issueTableBlocked) begin
                tableWouldBlockCycles <= tableWouldBlockCycles + 1'b1;
            end
            if (issueAddressBusy) begin
                addressBusyCycles <= addressBusyCycles + 1'b1;
            end
            if (issueDuplicate) begin
                duplicateIssueCycles <= duplicateIssueCycles + 1'b1;
            end
        end
    end
endmodule
