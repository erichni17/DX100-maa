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

## Primary correctness checkpoint

Published commit `142de1eac038822fcaa98820d06d1f671de66076` passed the
primary API oracle in
`evidence/backed_rmw_full16k_142de1ea`. Native16, native4, and backed16meta
all reported generation 7, zero errors, and output hash
`0xd1f648d95a481cb`. Their exact first-ROI `simTicks` values were 16,657,547,
25,572,413, and 866,646,294 respectively. These are mechanism measurements,
not application-speedup evidence.

The backed16meta terminal event reported `logical=16384`,
`metadata_scope=full16k`, `selected=applied=13107`,
`record_publication_bytes=524288`, `record_line_reads=21299`,
`record_read_bytes=1363136`, descriptor write/read bytes of zero,
`a_write_issues=a_write_acks=1024`, and generation-exact closure. The bounded
A-read and A-write scoreboards each reached a high-water mark of two. The
production gem5 binary SHA-256 was
`bc29db00c3f3c766b9900fe4be7ab676ca0bced91787ebc76de0f19611eee816`;
the backed/native4 guest SHA-256 was
`ea54c61a4cd3ca8b182f33935682db8d83a998f9532779e7569e546b0e90881c`;
and the Ramulator library SHA-256 was
`76ea3a9c7467a5fc0dc04f2b5f083909c03e8b7280c1872046fc78edb2a15753`.

The four-arm matrix is not complete evidence: the secondary backed4diag arm
remains non-promotable follow-up. Its last run exposed a load-only partition
transition after all 1,152 descriptor writes were ACKed but before replay
reads. The guarded transition and the exact rejected-predicate summary-key
round trip are repaired and compile/unit checked, but the diagnostic was not
rerun to a passing terminal result. It must not gate use of the primary
correctness oracle, and no diagnostic performance claim is supported.
