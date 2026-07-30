module LanlFp64Portfolio2SSharedRecode1A1M8DCompletion2W(
    input clock,
    input nReset,

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

    output completion0Valid,
    input completion0Ready,
    output [5:0] completion0Tag,
    output [63:0] completion0Value,
    output [4:0] completion0Flags,

    output completion1Valid,
    input completion1Ready,
    output [5:0] completion1Tag,
    output [63:0] completion1Value,
    output [4:0] completion1Flags,

    output reg [31:0] completionsCaptured,
    output reg [31:0] completionsRetired,
    output reg [31:0] completionBackpressureCycles,
    output reg overflow
);
    localparam [1:0] OpAdd = 2'b00;
    localparam [1:0] OpSubtract = 2'b01;
    localparam [1:0] OpMultiply = 2'b10;
    localparam [1:0] OpDivide = 2'b11;

    wire rawReq0Ready;
    wire rawReq1Ready;
    wire rawAddValid;
    wire [5:0] rawAddTag;
    wire [63:0] rawAddValue;
    wire [4:0] rawAddFlags;
    wire rawMulValid;
    wire [5:0] rawMulTag;
    wire [63:0] rawMulValue;
    wire [4:0] rawMulFlags;
    wire [7:0] rawDivValid;
    wire [47:0] rawDivTag;
    wire [511:0] rawDivValue;
    wire [39:0] rawDivFlags;

    reg [5:0] addTags [0:2];
    reg [63:0] addValues [0:2];
    reg [4:0] addFlags [0:2];
    reg [1:0] addHead;
    reg [1:0] addTail;
    reg [1:0] addCount;

    reg [5:0] mulTags [0:2];
    reg [63:0] mulValues [0:2];
    reg [4:0] mulFlags [0:2];
    reg [1:0] mulHead;
    reg [1:0] mulTail;
    reg [1:0] mulCount;

    reg [7:0] divPending;
    reg [5:0] divTags [0:7];
    reg [63:0] divValues [0:7];
    reg [4:0] divFlags [0:7];

    reg hold0Valid;
    reg [5:0] hold0Tag;
    reg [63:0] hold0Value;
    reg [4:0] hold0Flags;
    reg hold1Valid;
    reg [5:0] hold1Tag;
    reg [63:0] hold1Value;
    reg [4:0] hold1Flags;

    reg [3:0] roundRobin;
    reg [9:0] sourceValid;
    reg [5:0] sourceTag [0:9];
    reg [63:0] sourceValue [0:9];
    reg [4:0] sourceFlags [0:9];
    reg [9:0] selectedMask;
    reg fill0;
    reg fill1;
    reg [3:0] selectedSource0;
    reg [3:0] selectedSource1;
    reg [5:0] fill0Tag;
    reg [63:0] fill0Value;
    reg [4:0] fill0Flags;
    reg [5:0] fill1Tag;
    reg [63:0] fill1Value;
    reg [4:0] fill1Flags;

    wire addDequeue =
        (fill0 && selectedSource0 == 0) ||
        (fill1 && selectedSource1 == 0);
    wire mulDequeue =
        (fill0 && selectedSource0 == 1) ||
        (fill1 && selectedSource1 == 1);
    wire [7:0] divDequeue = {
        ((fill0 && selectedSource0 == 9) ||
         (fill1 && selectedSource1 == 9)),
        ((fill0 && selectedSource0 == 8) ||
         (fill1 && selectedSource1 == 8)),
        ((fill0 && selectedSource0 == 7) ||
         (fill1 && selectedSource1 == 7)),
        ((fill0 && selectedSource0 == 6) ||
         (fill1 && selectedSource1 == 6)),
        ((fill0 && selectedSource0 == 5) ||
         (fill1 && selectedSource1 == 5)),
        ((fill0 && selectedSource0 == 4) ||
         (fill1 && selectedSource1 == 4)),
        ((fill0 && selectedSource0 == 3) ||
         (fill1 && selectedSource1 == 3)),
        ((fill0 && selectedSource0 == 2) ||
         (fill1 && selectedSource1 == 2))
    };

    wire completion0Accepted = completion0Valid && completion0Ready;
    wire completion1Accepted = completion1Valid && completion1Ready;
    wire hold0Available = !hold0Valid || completion0Ready;
    wire hold1Available = !hold1Valid || completion1Ready;

    wire addIssueCredit =
        ({1'b0, addCount} + rawAddValid) < 3;
    wire mulIssueCredit =
        ({1'b0, mulCount} + rawMulValid) < 3;
    wire divIssueCredit = !(|divPending) && !(|rawDivValid);

    wire req0AddKind = req0Op == OpAdd || req0Op == OpSubtract;
    wire req1AddKind = req1Op == OpAdd || req1Op == OpSubtract;
    wire req0ResourceReady = req0AddKind ? addIssueCredit :
        (req0Op == OpMultiply ? mulIssueCredit : divIssueCredit);
    wire req1ResourceReady = req1AddKind ? addIssueCredit :
        (req1Op == OpMultiply ? mulIssueCredit : divIssueCredit);
    wire gatedReq0Valid = req0Valid && req0ResourceReady;
    wire gatedReq1Valid = req1Valid && req1ResourceReady;

    assign req0Ready = req0ResourceReady && rawReq0Ready;
    assign req1Ready = req1ResourceReady && rawReq1Ready;

    assign completion0Valid = hold0Valid;
    assign completion0Tag = hold0Tag;
    assign completion0Value = hold0Value;
    assign completion0Flags = hold0Flags;
    assign completion1Valid = hold1Valid;
    assign completion1Tag = hold1Tag;
    assign completion1Value = hold1Value;
    assign completion1Flags = hold1Flags;

    function [1:0] next3;
        input [1:0] current;
        begin
            next3 = current == 2 ? 0 : current + 1'b1;
        end
    endfunction

    LanlFp64Portfolio2SSharedRecode1A1M8D raw(
        clock, nReset,
        gatedReq0Valid, req0Op, req0Tag, req0A, req0B, rawReq0Ready,
        gatedReq1Valid, req1Op, req1Tag, req1A, req1B, rawReq1Ready,
        rawAddValid, rawAddTag, rawAddValue, rawAddFlags,
        rawMulValid, rawMulTag, rawMulValue, rawMulFlags,
        rawDivValid, rawDivTag, rawDivValue, rawDivFlags
    );

    integer source;
    integer offset;
    integer candidate;
    always @* begin
        sourceValid = 10'b0;
        sourceValid[0] = addCount != 0;
        sourceValid[1] = mulCount != 0;
        sourceTag[0] = addTags[addHead];
        sourceValue[0] = addValues[addHead];
        sourceFlags[0] = addFlags[addHead];
        sourceTag[1] = mulTags[mulHead];
        sourceValue[1] = mulValues[mulHead];
        sourceFlags[1] = mulFlags[mulHead];
        for (source = 0; source < 8; source = source + 1) begin
            sourceValid[source + 2] = divPending[source];
            sourceTag[source + 2] = divTags[source];
            sourceValue[source + 2] = divValues[source];
            sourceFlags[source + 2] = divFlags[source];
        end

        selectedMask = 10'b0;
        fill0 = 1'b0;
        fill1 = 1'b0;
        selectedSource0 = 4'b0;
        selectedSource1 = 4'b0;
        fill0Tag = 6'b0;
        fill0Value = 64'b0;
        fill0Flags = 5'b0;
        fill1Tag = 6'b0;
        fill1Value = 64'b0;
        fill1Flags = 5'b0;
        candidate = 0;
        for (offset = 0; offset < 10; offset = offset + 1) begin
            candidate = roundRobin + offset;
            if (candidate >= 10) begin
                candidate = candidate - 10;
            end
            if (sourceValid[candidate] && !selectedMask[candidate]) begin
                if (hold0Available && !fill0) begin
                    fill0 = 1'b1;
                    selectedSource0 = candidate[3:0];
                    fill0Tag = sourceTag[candidate];
                    fill0Value = sourceValue[candidate];
                    fill0Flags = sourceFlags[candidate];
                    selectedMask[candidate] = 1'b1;
                end else if (hold1Available && !fill1) begin
                    fill1 = 1'b1;
                    selectedSource1 = candidate[3:0];
                    fill1Tag = sourceTag[candidate];
                    fill1Value = sourceValue[candidate];
                    fill1Flags = sourceFlags[candidate];
                    selectedMask[candidate] = 1'b1;
                end
            end
        end
    end

    integer resetIndex;
    integer lane;
    integer capturedThisCycle;
    always @(posedge clock or negedge nReset) begin
        if (!nReset) begin
            addHead <= 0;
            addTail <= 0;
            addCount <= 0;
            mulHead <= 0;
            mulTail <= 0;
            mulCount <= 0;
            divPending <= 0;
            hold0Valid <= 0;
            hold0Tag <= 0;
            hold0Value <= 0;
            hold0Flags <= 0;
            hold1Valid <= 0;
            hold1Tag <= 0;
            hold1Value <= 0;
            hold1Flags <= 0;
            roundRobin <= 0;
            completionsCaptured <= 0;
            completionsRetired <= 0;
            completionBackpressureCycles <= 0;
            overflow <= 0;
            for (resetIndex = 0; resetIndex < 3;
                 resetIndex = resetIndex + 1) begin
                addTags[resetIndex] <= 0;
                addValues[resetIndex] <= 0;
                addFlags[resetIndex] <= 0;
                mulTags[resetIndex] <= 0;
                mulValues[resetIndex] <= 0;
                mulFlags[resetIndex] <= 0;
            end
            for (resetIndex = 0; resetIndex < 8;
                 resetIndex = resetIndex + 1) begin
                divTags[resetIndex] <= 0;
                divValues[resetIndex] <= 0;
                divFlags[resetIndex] <= 0;
            end
        end else begin
            if (fill0) begin
                hold0Valid <= 1'b1;
                hold0Tag <= fill0Tag;
                hold0Value <= fill0Value;
                hold0Flags <= fill0Flags;
            end else if (completion0Accepted) begin
                hold0Valid <= 1'b0;
            end
            if (fill1) begin
                hold1Valid <= 1'b1;
                hold1Tag <= fill1Tag;
                hold1Value <= fill1Value;
                hold1Flags <= fill1Flags;
            end else if (completion1Accepted) begin
                hold1Valid <= 1'b0;
            end

            if (fill1) begin
                roundRobin <= selectedSource1 == 9 ? 0 :
                    selectedSource1 + 1'b1;
            end else if (fill0) begin
                roundRobin <= selectedSource0 == 9 ? 0 :
                    selectedSource0 + 1'b1;
            end

            if (rawAddValid) begin
                if (addCount < 3 || addDequeue) begin
                    addTags[addTail] <= rawAddTag;
                    addValues[addTail] <= rawAddValue;
                    addFlags[addTail] <= rawAddFlags;
                    addTail <= next3(addTail);
                end else begin
                    overflow <= 1'b1;
                end
            end
            if (addDequeue) begin
                addHead <= next3(addHead);
            end
            case ({rawAddValid, addDequeue})
              2'b10: if (addCount < 3) addCount <= addCount + 1'b1;
              2'b01: addCount <= addCount - 1'b1;
              default: addCount <= addCount;
            endcase

            if (rawMulValid) begin
                if (mulCount < 3 || mulDequeue) begin
                    mulTags[mulTail] <= rawMulTag;
                    mulValues[mulTail] <= rawMulValue;
                    mulFlags[mulTail] <= rawMulFlags;
                    mulTail <= next3(mulTail);
                end else begin
                    overflow <= 1'b1;
                end
            end
            if (mulDequeue) begin
                mulHead <= next3(mulHead);
            end
            case ({rawMulValid, mulDequeue})
              2'b10: if (mulCount < 3) mulCount <= mulCount + 1'b1;
              2'b01: mulCount <= mulCount - 1'b1;
              default: mulCount <= mulCount;
            endcase

            for (lane = 0; lane < 8; lane = lane + 1) begin
                if (rawDivValid[lane]) begin
                    if (divPending[lane] && !divDequeue[lane]) begin
                        overflow <= 1'b1;
                    end
                    divPending[lane] <= 1'b1;
                    divTags[lane] <= rawDivTag[lane*6 +: 6];
                    divValues[lane] <= rawDivValue[lane*64 +: 64];
                    divFlags[lane] <= rawDivFlags[lane*5 +: 5];
                end else if (divDequeue[lane]) begin
                    divPending[lane] <= 1'b0;
                end
            end

            capturedThisCycle = rawAddValid + rawMulValid;
            for (lane = 0; lane < 8; lane = lane + 1) begin
                capturedThisCycle = capturedThisCycle + rawDivValid[lane];
            end
            if (capturedThisCycle != 0) begin
                completionsCaptured <=
                    completionsCaptured + capturedThisCycle;
            end
            if (completion0Accepted || completion1Accepted) begin
                completionsRetired <= completionsRetired +
                    completion0Accepted + completion1Accepted;
            end
            if ((completion0Valid && !completion0Ready) ||
                (completion1Valid && !completion1Ready)) begin
                completionBackpressureCycles <=
                    completionBackpressureCycles + 1'b1;
            end
        end
    end
endmodule
