`timescale 1ns/1ps

module lanl_fp64_completion_2w_tb;
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

    wire completion0Valid;
    reg completion0Ready = 1'b0;
    wire [5:0] completion0Tag;
    wire [63:0] completion0Value;
    wire [4:0] completion0Flags;
    wire completion1Valid;
    reg completion1Ready = 1'b0;
    wire [5:0] completion1Tag;
    wire [63:0] completion1Value;
    wire [4:0] completion1Flags;
    wire [31:0] completionsCaptured;
    wire [31:0] completionsRetired;
    wire [31:0] completionBackpressureCycles;
    wire overflow;

    reg [63:0] seenTags = 64'b0;
    reg [5:0] heldTag0;
    reg [5:0] heldTag1;
    integer pair;
    integer cycles;
`ifdef LANL_COMPLETION_SPLIT
    localparam integer AddBacklog = 4;
`else
    localparam integer AddBacklog = 5;
`endif
    localparam integer PostAddCompletions = AddBacklog + 2;
    localparam integer TotalCompletions = PostAddCompletions + 8;

`ifdef LANL_COMPLETION_SPLIT
    LanlFp64Portfolio2SSharedRecode1A1M8DCompletion2WSplit dut(
`else
    LanlFp64Portfolio2SSharedRecode1A1M8DCompletion2W dut(
`endif
        clock, nReset,
        req0Valid, req0Op, req0Tag, req0A, req0B, req0Ready,
        req1Valid, req1Op, req1Tag, req1A, req1B, req1Ready,
        completion0Valid, completion0Ready,
        completion0Tag, completion0Value, completion0Flags,
        completion1Valid, completion1Ready,
        completion1Tag, completion1Value, completion1Flags,
        completionsCaptured, completionsRetired,
        completionBackpressureCycles, overflow
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

    task checkCompletion;
        input channel;
        input [5:0] tag;
        input [63:0] value;
        input [4:0] flags;
        begin
            require(!seenTags[tag], "completion tag must be unique");
            require(flags == 0, "exact operation must raise no flags");
`ifdef LANL_COMPLETION_SPLIT
            if (tag == 1 || (tag >= 20 && tag < 25) ||
                (tag >= 8 && tag < 16 && (tag & 1) == 0)) begin
                require(channel == 0,
                        "add and even-divider tags must use channel zero");
            end else begin
                require(channel == 1,
                        "multiply and odd-divider tags must use channel one");
            end
`endif
            if (tag == 1) begin
                require(value == 64'h400e000000000000,
                        "tag 1 add must produce 3.75");
            end else if (tag == 2) begin
                require(value == 64'h400b000000000000,
                        "tag 2 multiply must produce 3.375");
            end else if (tag >= 20 && tag < 25) begin
                require(value == 64'h4000000000000000,
                        "queued add must produce 2.0");
            end else if (tag >= 8 && tag < 16) begin
                if ((tag & 1) == 0) begin
                    require(value == 64'h400c000000000000,
                            "even divide must produce 3.5");
                end else begin
                    require(value == 64'h4010000000000000,
                            "odd divide must produce 4.0");
                end
            end else begin
                require(1'b0, "unexpected completion tag");
            end
            seenTags[tag] = 1'b1;
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
            req0Valid = 1'b1;
            req0Op = operation0;
            req0Tag = tag0;
            req0A = a0;
            req0B = b0;
            req1Valid = 1'b1;
            req1Op = operation1;
            req1Tag = tag1;
            req1A = a1;
            req1B = b1;
            #1;
            require(req0Ready && req1Ready,
                    "nonconflicting pair must be accepted");
            @(posedge clock);
            #1;
            req0Valid = 1'b0;
            req1Valid = 1'b0;
        end
    endtask

    task driveAdd;
        input [5:0] tag;
        begin
            @(negedge clock);
            req0Valid = 1'b1;
            req0Op = 2'b00;
            req0Tag = tag;
            req0A = 64'h3ff0000000000000;
            req0B = 64'h3ff0000000000000;
            #1;
            require(req0Ready, "bounded add buffer must accept request");
            @(posedge clock);
            #1;
            req0Valid = 1'b0;
        end
    endtask

    task drainCycle;
        begin
            @(negedge clock);
            completion0Ready = 1'b1;
            completion1Ready = 1'b1;
            @(posedge clock);
            #1;
        end
    endtask

    always @(posedge clock) begin
        if (nReset) begin
            if (completion0Valid && completion0Ready) begin
                checkCompletion(
                    0, completion0Tag, completion0Value, completion0Flags);
            end
            if (completion1Valid && completion1Ready) begin
                checkCompletion(
                    1, completion1Tag, completion1Value, completion1Flags);
            end
        end
    end

    initial begin
        repeat (2) @(posedge clock);
        #1 nReset = 1'b1;

        drivePair(
            2'b00, 6'd1, 64'h3ff8000000000000,
            64'h4002000000000000,
            2'b10, 6'd2, 64'h3ff8000000000000,
            64'h4002000000000000
        );
        cycles = 0;
        while (!(completion0Valid && completion1Valid) && cycles < 10) begin
            @(posedge clock);
            #1;
            cycles = cycles + 1;
        end
        require(completion0Valid && completion1Valid,
                "add and multiply completions must both be retained");
        heldTag0 = completion0Tag;
        heldTag1 = completion1Tag;
        repeat (2) begin
            @(posedge clock);
            #1;
            require(completion0Valid && completion1Valid &&
                    completion0Tag == heldTag0 && completion1Tag == heldTag1,
                    "backpressured completion identities must remain stable");
        end
        drainCycle();
        completion0Ready = 1'b0;
        completion1Ready = 1'b0;

        for (pair = 0; pair < AddBacklog; pair = pair + 1) begin
            driveAdd(pair + 20);
        end
        @(negedge clock);
        req0Valid = 1'b1;
        req0Op = 2'b00;
        req0Tag = AddBacklog + 20;
        req0A = 64'h3ff0000000000000;
        req0B = 64'h3ff0000000000000;
        #1;
        require(!req0Ready,
                "add issue must stop before retained buffers overflow");
        req0Valid = 1'b0;

        cycles = 0;
        completion0Ready = 1'b1;
        completion1Ready = 1'b1;
        while (completionsRetired < PostAddCompletions && cycles < 20) begin
            drainCycle();
            cycles = cycles + 1;
        end
        require(completionsRetired == PostAddCompletions,
                "all add and multiply completions must retire");
        completion0Ready = 1'b0;
        completion1Ready = 1'b0;

        for (pair = 0; pair < 4; pair = pair + 1) begin
            drivePair(
                2'b11, pair*2 + 8, 64'h401c000000000000,
                64'h4000000000000000,
                2'b11, pair*2 + 9, 64'h4020000000000000,
                64'h4000000000000000
            );
        end
        cycles = 0;
        while (completionsCaptured < TotalCompletions && cycles < 100) begin
            @(posedge clock);
            #1;
            cycles = cycles + 1;
        end
        require(completionsCaptured == TotalCompletions,
                "all eight divide results must be captured");
        @(negedge clock);
        req0Valid = 1'b1;
        req0Op = 2'b11;
        #1;
        require(!req0Ready,
                "pending divide results must backpressure new divides");
        req0Valid = 1'b0;

        completion0Ready = 1'b1;
        completion1Ready = 1'b1;
        cycles = 0;
        while (completionsRetired < TotalCompletions && cycles < 30) begin
            drainCycle();
            cycles = cycles + 1;
        end
        require(completionsRetired == TotalCompletions,
                "all captured completions must retire exactly once");
        require(!overflow, "completion fabric must never overflow");
        require(completionBackpressureCycles > 0,
                "directed smoke must exercise completion backpressure");
        require(seenTags[1] && seenTags[2],
                "add and multiply tags must retire");
        for (pair = 0; pair < AddBacklog; pair = pair + 1) begin
            require(seenTags[pair + 20],
                    "all queued add tags must retire");
        end
        require(seenTags[15:8] == 8'hff,
                "all divider tags must retire");

        $display("LANL_FP64_COMPLETION_2W_SMOKE_PASS");
        $finish(0);
    end
endmodule
