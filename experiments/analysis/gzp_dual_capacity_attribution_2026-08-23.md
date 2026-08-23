# GZP dual-masked physical-capacity attribution (2026-08-23)

## Disposition

**Rejected; no capacity timing claim.** The accepted dual-masked guest and
checkpoint do not yet complete at logical16/physical16. The requested
response-bearing publisher admission repair is valid, but exact reruns expose
a separate page-1 backed-materializer liveness defect. Physical16 therefore
has no first-ROI `simTicks`, and physical4 timing cannot be paired or promoted.

## Exact experiment boundary

Every attempted final row uses the accepted full-GZP guest SHA-256
`79f80081611f986e0ef07f79ba498b948f77189ec1f1edd4c5687f6912c06b76`,
immutable selector SHA-256
`d20df4072e9e62b710c6e228c585d463592b7b4183a6be18717affe8410af4cd`
with payload `token_stream_ld dual_masked_index`, and one freeze-copy of the
accepted hybrid-dual checkpoint identity
`35fd8fb275763e3b14a9ee38265eb3d7ef702de0747a389beaee0f076d6cf862`.
Commands normalize to one difference besides outdir:
`--maa_physical_tile_elements=16384` versus `4096`. Both use
`MAAVirtualTrace,MAATrace`, logical16 Row/Offset metadata, fixed full-GZP
input, and six concurrent no-timeout restores.

## Rejected evidence sequence

1. `/data1/nier/worktrees/codex-coordination/sessions/gzp-dual-capacity-attribution-20260823-143100-970237e2/evidence/gzp-dual-capacity-44b6-r1`
   uses accepted be77 binary SHA-256
   `44b6e86ebc86fd692ce02dcb2e1f627082ded10c741134ae49de5721e2edcb45`.
   All three physical16 replicas abort before ROI at
   `StreamAccess.cc:225`: response-bearing publication strictly requires
   physical 4096. The old-binary physical4 rows are partial and excluded.
2. Commit `accb8c45` replaces that equality with an exact fail-closed
   logical16 allow-list for physical 4096 or 16384. Optimized and ASan/UBSan
   unit runs prove byte-identical page-local captured payload/address order at
   both capacities and reject physical 2K/8K and other logical geometries.
   The corresponding production binary is
   `c88bc6a4eb0421298d81781795486f69f91449674d25e4f138a6d2f75626e4be`.
3. `/data1/nier/worktrees/codex-coordination/sessions/gzp-dual-capacity-attribution-20260823-143100-970237e2/evidence/gzp-dual-capacity-c88bc6a4-r1`
   passes the old panic but physical16 stops after one publisher terminal and
   four SoA terminals. Its three fresh physical4 replicas exit zero at
   `6,322,114,850` first-ROI ticks with exact output hash
   `11225737641199706160`, zero nonfinite values, and zero reference errors
   over 1,180,000 elements. Those rows are rejected because physical16 does
   not complete and the simulator changes again.
4. Commit `9dadd730` changes the publisher's source wait to page-local
   readiness; its production binary is
   `faf4922ea1d6f499d877dc05de86b27ee84cdbf4e541c63c25e17806f0aea4c4`.
   `/data1/nier/worktrees/codex-coordination/sessions/gzp-dual-capacity-attribution-20260823-143100-970237e2/evidence/gzp-dual-capacity-faf4922e-r1`
   reproduces the identical physical16 stop in all three replicas. Each has
   exactly one publisher terminal, four SoA terminals, and byte-identical
   trace SHA-256
   `b8204b608bc8f7b2ec5063fe1842ddd0faa9d27e3f4d96cd97ea437b2e736e84`.
   The service was stopped after exact reproduction; no physical4 result from
   this binary is accepted.

The isolated production build used ignored spdlog gitlink `ad0e89cb...` and
yaml-cpp gitlink `0579ae3d...` trees copied from
`/data1/nier/worktrees/DX100-virtualization-line-handoff-20260812`, plus only a
`libramulator.so` symlink to the accepted prepublisher artifact. `ldd` resolves
that exact library at SHA-256
`76ea3a9c7467a5fc0dc04f2b5f083909c03e8b7280c1872046fc78edb2a15753`.

## Exact liveness finding

The physical16 trace proves page 0 completes: its response-bearing publisher
receives all 256 WriteResps and emits one terminal. Page 1 then proceeds far
enough to submit the existing backed materializer, but that materializer
issues and commits only six lines and emits no later materializer event. Its
dependent FP32 multiply ALU starts but never ends; the next publisher remains
at source stall. A diagnostic-only restore with `MAAALU`, `MAAStream`, and
`MAAController` confirms the block is upstream of publisher capture.

The trace also shows continuing direct-retirement port wakes with three active
shared contexts. This supports, but does not by itself prove, a shared
materializer/SoA scheduling-resource deadlock. Repairing that scheduler is a
separate architecture change and is not folded into this capacity attribution.

## Hardware accounting boundary

The intended matched comparison would remove exactly **1,572,864 B** of
physical SPD payload: 32 tiles x (16,384 - 4,096) elements x 4 B. Physical16
is 2,097,152 B and physical4 is 524,288 B. Logical metadata remains fixed at
16 row-table slices, 64 rows/slice, 8 entries/subslice-row, 16,384 offset
entries, and 16,384 offset-epoch entries.

These unmeasured capacity values exclude and must be reported separately from
the one **920 B** response-bearing publisher and the **262,144 B** coherent
gradient backing in LLC/DRAM address space. Because the physical16 arm never
reaches exact output/reference or the required 244 publisher terminals, 122
SoA terminals, and 62,464 publisher issues/accepts/WriteResps, none of these
hardware deltas has an accepted performance pairing here.
