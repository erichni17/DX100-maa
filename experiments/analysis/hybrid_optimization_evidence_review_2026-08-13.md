# Hybrid optimization evidence review — 2026-08-13

## Verdict

**Do not promote the optimized hybrid configuration to GZP or CG yet.** The
best completed evidence is a deterministic API microbenchmark, not an
application result. The two GZP roots and two general-hybrid CG roots were
still marked `running` and had no completed arm report when reviewed. The
separate fully bounded CG result is accepted but slower than both native
controls, so it is not a substitute for missing general-hybrid evidence.

The defensible statement is narrower: at `0f6e8d26`, the 16K-logical/4K-
physical token materializer is exact-correct on the API microbenchmark and,
at the best completed point, remains 4.63% slower than its matched native16
control while 1.534x faster than native4. This is feasibility evidence, not a
workload or area promotion. Commit `489e4d35` only separates packed response
payload storage and introduces no new gem5 evidence.

## Completed API evidence

Both roots have campaign exit zero, two identical replicas per arm, one
terminal `m5_exit` per replica, nonempty final statistics, and the exact result
key `7228541527853630339`.

| candidate | root | native16 / native4 / token `simTicks` | interpretation |
|---|---|---:|---|
| R1280/P2560/C512/W16 | `/data1/nier/dx100-runs/2026-08-13-general-hybrid-api-confirm-r1280p2560-c512w16-30d6397a-r2x2` | 18,471,069 / 29,351,575 / 19,866,423 | token is 7.56% slower than native16, 1.477x faster than native4 |
| R1280/P2560/C512/WPC8 | `/data1/nier/dx100-runs/2026-08-13-general-hybrid-api-confirm-r1280p2560-c512w16-wpc8-30d6397a-r2x2` | 18,257,290 / 29,351,575 / 19,103,016 | token is 4.63% slower than native16, 1.534x faster than native4 |

R is retained response slots, P is the packed response-word pool, C is
combiner slots, and WPC is the virtual word-retirement limit per cycle. WPC8
is the best completed API point, but not a clean WPC8-versus-W16 attribution:
the knob is applied to every arm and native16 time also changes between roots.

Within each matrix, native16 is a fair timing reference to the hybrid token
arm: same hybrid binary, frozen hybrid checkpoint, input, cache/memory
settings, and configured virtual resource knobs; physical tile capacity and
selector differ. Native4 uses a different 4K checkpoint and binary/geometry,
so it is a capacity endpoint, not a same-checkpoint treatment comparison. Do
not describe either ratio as an isolated benefit of R/P/C/WPC.

## Bounded payload and control ledger

The WPC8 token config has one indirect unit, FP64 words, 128 feeder lines,
R=1280, P=2560, C=512, 16 ways, 64 write credits, and four 4K pages. Running
the checked `report_maa_storage.py` ledger against its frozen `config.ini`
produces the following per-unit terms.

| bounded term | bytes | basis |
|---|---:|---|
| packed response payload | 20,480 | 2,560 FP64 words x 8 B |
| destination-combiner payload | 32,768 | 512 fixed 64-B slots |
| direct-index feeder payload | 8,192 | 128 fixed 64-B lines |
| virtual payload subtotal | 61,440 | sum above |
| semantic virtual-control lower bound | 37,678 | tags, ownership, counters, replacement; 193 response bits/slot |
| direct-retirement handoff lower bound | 10,496 | separate fixed queue/ledger/records |

The named virtual payload/control floor is therefore 109,614 B per active
unit with handoff enabled. It is not an RTL area estimate. The four-core,
eight-lane-tile configuration has 524,288 B physical SPD versus 2,097,152 B
for native16; the fixed-organization comparable lower bound is 896,314 B
versus 2,408,704 B (62.79% lower). This excludes SRAM periphery, ports,
arbitration, wiring, and host-container overhead. In particular,
`virtual_combine_words=4096` limits live words; it does not shrink the already
allocated `512 x 64 B` combiner-slot array.

## Material omissions and timing assumptions

The source still has non-synthesized dynamic structures: address-history maps,
`virtual_source_reservations`, retirement-page maps containing vectors, an
outstanding-write set, direct-index maps/vectors, per-page vectors, and port
outstanding/deferred unordered maps/deques. Cardinality guards do not price
their node/pointer/allocator footprints.

The performance model also assumes four L3 ports, two memory channels, and an
abstract WPC8 word budget. Existing traces establish exact materializer
closure, but not an LLC/link bandwidth ceiling: they lack page credit occupancy
and issue-to-response distributions. The critical-path audit instead exposes
single-active-page serialization and ACK-before-admission cache-read fallback.

## Promotion recommendation

1. Carry **R1280/P2560/C512/WPC8** only as the best API-qualified candidate,
   with its 61,440-B payload and 37,678-B control floor.
2. Do not promote for **GZP** until a completed, repeated, exact-correct GZP
   matrix has terminal exits, frozen binary/checkpoint identities, and the
   same profile across native16/native4/hybrid arms.
3. Do not promote for **CG**. The general-hybrid roots are incomplete; the
   independent bounded-CG root `/data1/nier/dx100-runs/2026-08-12-cg-bounded-i32-full`
   is 58.135% slower than native4 and 111.410% slower than native16.
4. Before claiming an application architecture win, replace or bound the
   listed simulator containers and add page-level credit/latency counters.
   Compare matched repetitions using `simTicks`, never host time.

## Evidence and source anchors

- W16 API: `/data1/nier/dx100-runs/2026-08-13-general-hybrid-api-confirm-r1280p2560-c512w16-30d6397a-r2x2/analysis/report.md`
- WPC8 API: `/data1/nier/dx100-runs/2026-08-13-general-hybrid-api-confirm-r1280p2560-c512w16-wpc8-30d6397a-r2x2/analysis/report.md`
- Incomplete GZP: `/data1/nier/dx100-runs/2026-08-13-general-hybrid-gzp-r1280p2560-c512w16-30d6397a-r1` and `...-wpc8-30d6397a-r1`
- Incomplete general-hybrid CG: `/data1/nier/dx100-runs/2026-08-13-general-hybrid-cg-dedicated-reg-823deb45-r1` and `...-shared-reg-9692859d-r1`
- Source/accounting: `src/mem/MAA/IndirectAccess.hh`, `IndirectAccess.cc`,
  `VirtualResponsePayloadStore.hh`, `experiments/scripts/report_maa_storage.py`,
  and `experiments/analysis/spd_hardware_accounting.py`.
