# Bounded backed indirect-vector RMW contract

## Grounded ordinary path

`INDIR_RMW_VECTOR` is an indirect-unit write instruction. The guest ABI names
an index SPD tile, a value SPD tile, an optional predicate tile, an optional
old-value destination tile, the typed `ADD`/`MIN`/`MAX` operation, and A's base
address. `IF.cc` assigns 32-bit index/predicate words and datatype-sized value
and destination words. `IndirectAccess.cc::fillRowTable` waits for equal-length
operands, translates each selected `A[index]`, and inserts `{A line, logical
iteration, word offset}` into finite Row/Offset state. `recvData` obtains the A
line, optionally copies the old word to `dst`, applies the typed operation in
Offset-list order, and writes the dirty line. The instruction retires after
all expected line transactions close. A sequence of four ordinary 4K RMW
instructions is correct, but each instruction has only a 4K reorder scope.

## Guarded backed form

The opcode remains `INDIR_RMW_VECTOR`. The guarded shape has no SPD index,
value, or predicate source; words 3 and 4 instead name an isolated descriptor
backing range and a coherent array of 32-byte records:

`{u32 index, u32 predicate, u32 generation, u32 reserved, u64 value_bits,
u64 reserved}`.

The min/max/stride registers define exactly 16,384 logical records. Every
record has one common non-zero generation. The API publishes all records with
ordinary guest stores and a release fence inside the measured ROI. Hardware
reads them through the existing indirect cache ports.

The primary oracle gives Row/Offset metadata the full 16K capacity while the
physical SPD remains 4K. Row entries retain logical record identities, not a
16K value payload. Before issuing each row-ordered A line, hardware fetches its
operand records through the cache into a fixed 64-record/eight-line response
window. Thus all 16K A references compete in one Row/Offset ordering domain,
while values remain finite and timed. A-side updates use response-bearing
coherent `WriteReq`; a 64-entry tagged scoreboard retains physical line and
generation until exact `WriteResp`.

The secondary 4K-metadata diagnostic uses a bounded summary, four counted grow
ranges, and the existing six-byte `{iteration,index}` descriptor spool. It
writes three nonresident populations to coherent backing, replays all four
populations in ordered grow ranges, and holds one explicit 4,096-entry value
epoch. That array and the 32-byte AoS publication traffic are non-promotable
hardware; they exist only to diagnose the spill mechanism. Neither treatment
allocates a hidden 16K SPD payload, operation-sized host vector, decoded replay
queue, or zero-time host work.
Completion requires `selected == applied`, descriptor writes equal descriptor
ACKs, A writes equal A ACKs, and both finite scoreboards empty.

The `backed_rmw_complete` trace reports record line/byte reads, descriptor
line/byte writes and reads, descriptor ACKs/responses, response-window and
diagnostic-value capacities, generation closure, and A write/ACK counts. All
fixed arrays are explicitly included as hardware control/storage bytes in that
event, together with `promotable=0`.

## Evidence boundary

The API matrix compares exact integer output hashes and `simTicks` for native16
(one 16K ordinary RMW), native4 (four 4K ordinary RMWs), backed16meta (one 16K
RMW with 4K physical SPD and full 16K Row/Offset metadata), and backed4diag (the
secondary 4K spill diagnostic). Record publication is timed inside the ROI, but
this remains a non-promotable correctness oracle and API mechanism evidence. It
is not an application-speedup claim. GZP is intentionally unwired until this
API path is correct; a future first GZP RMW may read sequential
index/value/predicate arrays directly, whereas a second dependent RMW must pay
a modeled computed-value spill/materialization cost.
