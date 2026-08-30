`timescale 1ns/1ps

// One fixed-index 471-bit token. Dynamic tags are decoded to writeEnable
// outside this module so synthesis never builds a multi-write token memory.
module LanlUmtTokenEntry(
    input clock,
    input nReset,
    input [5:0] tokenIndex,
    input [63:0] currentCycle,
    input [27:0] coefficientActive,
    input admit0Valid,
    input [5:0] admit0Token,
    input [470:0] admit0State,
    input admit1Valid,
    input [5:0] admit1Token,
    input [470:0] admit1State,
    input issue0Valid,
    input [5:0] issue0Token,
    input issue1Valid,
    input [5:0] issue1Token,
    input [9:0] completionReady,
    input [59:0] completionToken,
    input [639:0] completionResult,
    input [3:0] writebackValid,
    input [23:0] writebackToken,
    output [470:0] state
);
    localparam [3:0] PHASE_DENOMINATOR_ADD_PENDING = 4'd1;
    localparam [3:0] PHASE_DENOMINATOR_ADD_WAIT = 4'd2;
    localparam [3:0] PHASE_DIVIDE_PENDING = 4'd3;
    localparam [3:0] PHASE_DIVIDE_WAIT = 4'd4;
    localparam [3:0] PHASE_MULTIPLY_PENDING = 4'd5;
    localparam [3:0] PHASE_MULTIPLY_WAIT = 4'd6;
    localparam [3:0] PHASE_EDGE_ADD_PENDING = 4'd7;
    localparam [3:0] PHASE_EDGE_ADD_WAIT = 4'd8;
    localparam [3:0] PHASE_RESULT_WRITE_PENDING = 4'd9;

    (* keep = "true", umt_state_class = "token" *)
        reg [470:0] stateReg;
    reg [470:0] nextState;
    reg writeEnable;
    reg [3:0] nextDestination;
    integer source;
    integer destination;
    integer coefficient;
    integer completionSource;
    integer writebackBank;
    reg foundDestination;

    assign state = stateReg;

    always @* begin
        nextState = stateReg;
        writeEnable = 1'b0;
        nextDestination = 4'd8;
        foundDestination = 1'b0;
        coefficient = 0;
        source = stateReg[18:16];
        for (destination = 0; destination < 8;
             destination = destination + 1) begin
            if (destination > source) begin
                coefficient = source * (15 - source) / 2 +
                    destination - source - 1;
                if (!foundDestination &&
                    destination >= stateReg[22:19] + 1'b1 &&
                    coefficientActive[coefficient]) begin
                    nextDestination = destination[3:0];
                    foundDestination = 1'b1;
                end
            end
        end

        if (admit0Valid && admit0Token == tokenIndex) begin
            nextState = admit0State;
            writeEnable = 1'b1;
        end
        if (admit1Valid && admit1Token == tokenIndex) begin
            nextState = admit1State;
            writeEnable = 1'b1;
        end
        if (issue0Valid && issue0Token == tokenIndex) begin
            nextState[3:0] = stateReg[3:0] + 1'b1;
            if (stateReg[3:0] == PHASE_EDGE_ADD_PENDING)
                nextState[86:23] = 64'hffffffffffffffff;
            writeEnable = 1'b1;
        end
        if (issue1Valid && issue1Token == tokenIndex) begin
            nextState[3:0] = stateReg[3:0] + 1'b1;
            if (stateReg[3:0] == PHASE_EDGE_ADD_PENDING)
                nextState[86:23] = 64'hffffffffffffffff;
            writeEnable = 1'b1;
        end
        for (completionSource = 0; completionSource < 10;
             completionSource = completionSource + 1) begin
            if (completionReady[completionSource] &&
                completionToken[completionSource * 6 +: 6] == tokenIndex) begin
                case (stateReg[3:0])
                  PHASE_DENOMINATOR_ADD_WAIT: begin
                      nextState[214:151] = completionResult[
                          completionSource * 64 +: 64];
                      nextState[86:23] = currentCycle + 1'b1;
                      nextState[3:0] = PHASE_DIVIDE_PENDING;
                  end
                  PHASE_DIVIDE_WAIT: begin
                      nextState[342:279] = completionResult[
                          completionSource * 64 +: 64];
                      nextDestination = 4'd8;
                      foundDestination = 1'b0;
                      for (destination = 0; destination < 8;
                           destination = destination + 1) begin
                          if (destination > stateReg[18:16]) begin
                              coefficient = stateReg[18:16] *
                                  (15 - stateReg[18:16]) / 2 +
                                  destination - stateReg[18:16] - 1;
                              if (!foundDestination &&
                                  destination >= stateReg[18:16] + 1'b1 &&
                                  coefficientActive[coefficient]) begin
                                  nextDestination = destination[3:0];
                                  foundDestination = 1'b1;
                              end
                          end
                      end
                      nextState[22:19] = nextDestination;
                      nextState[86:23] = currentCycle + 1'b1;
                      nextState[3:0] = nextDestination == 4'd8 ?
                          PHASE_RESULT_WRITE_PENDING :
                          PHASE_MULTIPLY_PENDING;
                  end
                  PHASE_MULTIPLY_WAIT: begin
                      nextState[406:343] = completionResult[
                          completionSource * 64 +: 64];
                      nextState[86:23] = currentCycle + 1'b1;
                      nextState[3:0] = PHASE_EDGE_ADD_PENDING;
                  end
                  PHASE_EDGE_ADD_WAIT: begin
                      nextState[470:407] = completionResult[
                          completionSource * 64 +: 64];
                      nextState[86:23] = currentCycle;
                  end
                endcase
                writeEnable = 1'b1;
            end
        end
        for (writebackBank = 0; writebackBank < 4;
             writebackBank = writebackBank + 1) begin
            if (writebackValid[writebackBank] &&
                writebackToken[writebackBank * 6 +: 6] == tokenIndex) begin
                if (stateReg[3:0] == PHASE_EDGE_ADD_WAIT) begin
                    nextState[22:19] = nextDestination;
                    nextState[3:0] = nextDestination == 4'd8 ?
                        PHASE_RESULT_WRITE_PENDING :
                        PHASE_MULTIPLY_PENDING;
                end else begin
                    nextState = 471'b0;
                end
                writeEnable = 1'b1;
            end
        end
    end

    always @(posedge clock) begin
        if (!nReset)
            stateReg <= 471'b0;
        else if (writeEnable)
            stateReg <= nextState;
    end
endmodule
