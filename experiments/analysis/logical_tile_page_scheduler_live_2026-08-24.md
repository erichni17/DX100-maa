# Logical-tile page scheduler live evidence (2026-08-24)

The generic logical-tile scheduler now runs in production gem5 using existing
4K-element SPD frames and native functional units. It adds no logical-window
payload: eight of the existing sixteen 32-bit SPD lane IDs are reserved as
four FP64-safe frame spans; FP32 uses one lane of each span.

## Exact result

Raw root:

`/data1/nier/dx100-runs/2026-08-24-logical-page-live-fbbf8b30-r4`

- checkpoint/restore exits: `0/0`;
- first-ROI `simTicks`: `1,415,099,918`;
- nine logical architectural operations across two descriptor generations;
- 36 logical pages begun;
- 80 native page actions dispatched and completed;
- 6,144 response-bearing writes issued, accepted, and acknowledged;
- 24 page-write terminals and nine architectural retirements;
- exact hashes for stream load, scalar multiply, distinct-source vector add,
  self-source vector add, dense store, descriptor reuse, and generation-two
  output; `errors=0`.

The instruction stream uses ordinary logical forms of `STREAM_LD`,
`ALU_SCALAR`, `ALU_VECTOR`, and `STREAM_ST`. Every source page is filled into
a reserved frame, compute uses the native ALU, and each dirty destination
frame remains leased until all of its 64-byte `WriteReq` packets receive exact
`WriteResp`s. Architectural completion occurs only after all four pages close.

## Storage boundary

For the same sixteen SPD lane IDs:

- native16 payload: `16 * 16384 * 4 = 1,048,576` bytes;
- hybrid physical4 payload: `16 * 4096 * 4 = 262,144` bytes;
- payload reduction: 75%;
- scheduler-added payload: zero bytes.

The scheduler adds fixed descriptor, frame-lease, transaction, and execution
control. This is not synthesized area or total PPA, and the control overhead
must be accounted separately before an iso-area claim.

## Claim boundary

This is one exact functional/timing smoke, not a speedup comparison. No native
arm was launched. It validates generic page scheduling and native execution,
but does not yet establish full-application performance, transparent compiler
lowering, indirect-gather page overlap, old-value RMW output, or mixed legacy
tile pressure under realistic applications.
