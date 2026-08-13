# Frozen XRAGE four-context steady-state profile

## Decision

The exact line-handoff arm is **2.4166x native16** because the 4K-backed
virtual producer and coherent intermediate path dominate execution, not because
the four-context consumer lacks correctness or reaches its advertised credit
capacity.  Line handoff is real and useful, but small: it removes 12,106 MAA
cycles (3,789,178 ticks, 3.600%) from the page arm and leaves a 190,035-cycle
gap to native16.

The largest measured delta is before or alongside the consumer.  Virtual
indirect execution takes 276,357 cycles versus native's 50,380 (+225,977), and
its Fill component takes 217,680 versus 13,851 (+203,829).  That Fill delta is
107.3% of the end-to-end gap because native overlaps its tile-level stream,
indirect, and ALU units while the virtual path exposes other costs.  The direct
arm issues only four architectural MAA instructions, compared with native's
sixteen; fewer decoded MAA instructions did not translate into a shorter
critical path.

The evidence does **not** support blaming DRAM bandwidth, 16-credit capacity,
or exact-address exclusion.  The line arm performs 26.5% fewer DRAM read
commands than native, sees 95.3% fewer DRAM queue-full observations, and sees
the queue empty 6.43x as often.  It reaches only 16 of the four contexts' 64
aggregate line credits and 16 of 64 fixed request records, with zero credit
stalls and zero address stalls.

## Frozen evidence and comparability

Campaign:
`/data1/nier/dx100-runs/2026-08-13-xrage-direct-x3-multicontext-64k-40dae46c`

| Item | Frozen value |
| --- | --- |
| Simulator source | `40dae46cf32f164c375751f407f45ea9707af7b7` |
| Simulator SHA-256 | `be050681e8f6b1358ba720e06a7653ad27de0e9ef2965307946e6671fa8b8a2e` |
| Guest source | `3c2bb8210ebf0b2a91cc21c5623fba420c216a16` |
| Guest SHA-256 | `5ce84f74176551306fb3d60820e3f849ff7ecdb0078ca25d53410efd85eb9e02` |
| Input SHA-256 | `70e3d82973d7a93300db950d2c81e9db5b6a37273b0f21da8344302ce53022d9` |
| Output oracle | exact length 65,536, hash `5576400619275092867` |
| Direct-pair checkpoint | normalized `m5.cpt` `6ff2844121c5c3856ab1499d3f5167c82d62dbada1fe802e5712df7100d55d65`; pmem `23bfbe8088d4b435ba617bef769ddff642e85b2826cab3718bc80d2684925bf5` |

All three checkpoint, restore, and driver exits are zero.  Every restore log has
the exact verifier pass and a terminal `m5_exit`.  Each `result.tsv` agrees with
the first of two raw `run/stats.txt` blocks.  Page and line are a strict matched
pair whose only registered treatment is `direct_retirement_line_handoff`; the
strict validator requires four descriptors and a context high-water mark of
four.  Native is the intended 16K physical-tile control, so its geometry and
guest arm differ by design.

## Measured facts

### End-to-end and dominant cycle deltas

One MAA cycle is exactly 313 simTicks in every arm.

| First-ROI metric | native16-64k | page-64k | line-64k | line minus native |
| --- | ---: | ---: | ---: | ---: |
| `simTicks` | 41,989,576 | 105,259,709 | 101,470,531 | +59,480,955 |
| MAA/CPU cycles | 134,152 | 336,293 | 324,187 | **+190,035** |
| Indirect instruction cycles | 50,380 | 276,532 | 276,357 | **+225,977** |
| Indirect Fill cycles | 13,851 | 218,165 | 217,680 | **+203,829** |
| Indirect Request cycles | 36,529 | 58,367 | 58,677 | +22,148 |
| CPU committed instructions | 380,566 | 867,143 | 863,378 | +482,812 (+126.9%) |

Native records 192,739 summed MAA instruction-cycles but finishes in 134,152
cycles: 58,587 unit-cycles overlap, for 1.437 active instruction units per
elapsed cycle on average.  Its four workers issue four stream reads, four
indirect gathers, four tile ALUs, and four stream writes.  The line arm records
only its four indirect instructions architecturally; its direct consumer is
internal work and continues until cycle 324,187.  The first-ROI stats cannot
assign every overlapped direct-consumer cycle to a unique critical-path bucket.

All four OpenMP workers span the same 324,187-cycle line ROI.  Their committed
instructions are 297,289 / 284,667 / 187,331 / 94,091, while the direct-context
high-water mark is four.  Thus four software workers and four live direct
descriptors are present.  The configuration still has one MAA and one indirect
unit; four workers do not instantiate four independent ALU/stream pipelines.

### Producer locality and coherent intermediate traffic

| First-ROI counter | native16 | line | delta |
| --- | ---: | ---: | ---: |
| A lines missing cache and accessing memory | 8,638 | 12,734 | +4,096 (+47.4%) |
| Virtual build rounds | 0 | 140 | +140 |
| Virtual response-word pool stalls | 0 | 136 | +136 |
| Producer backing write requests | 0 | 10,857 | +10,857 |
| Full-line / partial producer writes | 0 / 0 | 5,613 / 5,244 | — |
| Producer write excess over 8,192 result lines | 0 | 2,665 (+32.5%) | — |
| Consumer backing reads / destination writes | 0 / 0 | 8,192 / 8,192 | — |

The current direct path therefore performs 19,049 intermediate requests before
counting its final destination writes: 10,857 producer backing writes plus
8,192 consumer read-backs.  Source inspection confirms that the producer sends
its writes through the coherent retirement-cache path
(`IndirectAccess.cc:6317-6357` at the frozen commit).  The consumer constructs
one 64-byte `ReadReq`, transforms the credit-owned buffer in place, then retains
it through one acknowledged 64-byte `WriteReq`
(`MAA.cc:1397-1496`).  This traffic is architectural evidence of the current
implementation, not an estimate.

Summed first-ROI crossbar packet statistics provide a second view.  Native has
32,768 observed MAA/retirement-cache packets and 1,310,720 packet-bytes; line has
63,447 and 1,957,760, increases of 93.6% and 49.4%.  The four line-arm MAA port
counts are balanced (7,729 / 7,723 / 7,714 / 7,729), so no one cache-side port
is visibly skewed.

### Shared ALU and 64-byte invocation cost

The frozen source has one queue-global `computeInFlight` owner
(`HybridConsumerContextQueue.hh:159-180`) and the MAA additionally requires the
selected `aluUnitsIdle` bit before launch (`MAA.cc:1789-1820`).  These checks
serialize ALU work across all four contexts.

Every direct line contains eight FP64 values.  `startDirectLine` charges
`ceil(8 / 16) * 1 = 1` ALU cycle and schedules one completion event per line
(`ALU.cc:79-113`).  Measured consequences:

| ALU compute metric | native16 | line |
| --- | ---: | ---: |
| FP64 elements | 65,536 | 65,536 |
| Compute cycles | 4,318 | 8,192 |
| Elements per compute cycle | 15.177 | 8.000 |
| Invocation granularity | 4 tile instructions | 8,192 line launches |

The direct ALU therefore uses half of the 16 FP64 lanes per charged cycle and
needs 1.897x native compute cycles.  The measured +3,874 compute cycles are only
2.04% of the 190,035-cycle total gap.  The extra cost of 8,192 C++ event and
scheduler invocations is not separately counted, so it is an inference—not a
measured cycle attribution—that invocation overhead adds further latency.

Native's tile-level ALU reaches 15.177 elements/compute-cycle (94.9% of 16
lanes).  Its shared stream unit processes 12,288 cache lines in 90,076 request
cycles, or 0.1364 lines/cycle; using the known 256 KiB index input plus 512 KiB
FP64 output, that is 8.731 bytes/request-cycle.  Native can overlap these
tile-granular stream, indirect, and ALU units across the four workers; the
direct consumer's one-line ALU token cannot exploit equivalent ALU width.

### Credits, cache limits, retries, and address exclusion

The source gives each of four contexts sixteen 64-byte buffers and declares 64
fixed request records (`HybridConsumerPipeline.hh:27-36`,
`MAA.hh:584-603`).  Line measures only 16 aggregate credits and 16 request
records in use (25% of capacity), with zero credit stalls.  More credits are
not supported as the next optimization by this run.

Exact-address exclusion scans all fixed direct records and also blocks behind
generic outstanding or deferred ownership (`MAA.cc:1105-1155`,
`MAA.cc:1701-1786`; `Port.cc:48-80`).  The line arm records zero address stalls
and zero generic virtual-retirement deferrals, so address exclusion is legal
but not measured as a bottleneck here.

The cache-side configuration is roomy relative to observed direct occupancy:
256 L3 MSHRs, 128 L3 write buffers, and a configured 32,768 response cap per
MAA cache-side port (the source reserves 32 entries, leaving 32,736).  Each of
the four producer retirement caches has 16 MSHRs and 16 write buffers.  Their
blocked-cycle sums are 12,425 for line and 13,774 for page; these per-cache sums
may overlap and must not be treated as critical-path cycles.

Cache request acceptance is nevertheless imperfect.  Direct retries are
10,559 for line versus 1,562 for page (6.76x).  The local response cap cannot be
the direct cause at a 16-request high-water mark; `CacheSidePort::sendPacket`
can otherwise reject a send only when the selected port is blocked or
downstream returns false (`CacheSidePort.cc:102-152`).  The direct scheduler has
one global retry packet and stops all direct scheduling until that packet's
selected port accepts it (`MAA.cc:1701-1730`).  The counter records attempts,
not wait cycles, so it cannot quantify the retry contribution to latency.

Notably, line remains faster despite the retry increase.  Relative to page, it
reduces L3 `ReadReq_10::maa` accesses from 8,128 to 6,319 (-1,809, -22.3%),
observed crossbar packets from 65,076 to 63,447, and crossbar packet-bytes from
2,062,208 to 1,957,760.  Those reductions are measured.  It is a plausible but
unproven interpretation that earlier producer/consumer proximity satisfies
more reads before a later L3 access; response-source counters are insufficient
to prove that mechanism.

### DRAM is underfed, not saturated

| Two-channel first-ROI DRAM metric | native16 | line | change |
| --- | ---: | ---: | ---: |
| Read commands | 21,301 | 15,651 | -5,650 (-26.5%) |
| Queue-full observations | 53,948 | 2,541 | -95.3% |
| Queue-empty observations | 39,960 | 257,011 | 6.43x |
| Average occupancy, channels 0 / 1 | 11.08 / 11.37 | 1.91 / 1.97 | much lower |
| DRAM ROI cycles, each channel | 67,183 | 162,352 | 2.417x |

The slower arm leaves the memory queues empty far more often while performing
less DRAM work.  This rules out a simple DRAM bandwidth explanation and is
consistent with serialized request generation, producer retirement, read-back,
and one-line consumer scheduling starving the memory system.

## Inference and causal boundary

The measured data make the following ordering credible:

1. The 4K virtual producer's 140-round Fill/retirement path is the dominant
   loss.  Its +203,829 Fill cycles alone exceed the endpoint gap.
2. Coherent intermediate materialization adds 10,857 producer writes and 8,192
   read-backs that native's SPD-resident ALU path does not require.
3. Four contexts improve overlap and exact line handoff helps, but one shared
   ALU token, one global retry packet, and 64-byte operations constrain how that
   concurrency reaches the existing ports and lanes.
4. The half-width line ALU is real but small in charged compute cycles; credits
   and address exclusion are explicitly non-binding in this observation.

This is one deterministic frozen observation per arm.  It supports mechanism
diagnosis for this input, not a general workload average.  Counters whose
intervals overlap are not summed into a critical-path decomposition, and retry
attempts are not converted into cycles.

## Narrow next experiments, ordered by expected ROI

1. **Exact producer-to-consumer payload handoff A/B.** Live-wire the already
   bounded 16-credit producer-result handoff for only the verified FP64 `MUL
   3.0` pair.  Keep four contexts, one ALU, and final acknowledged writes.
   Require exact hash and 8,192 final write responses; the treatment should
   remove the 10,857 producer backing requests and 8,192 consumer read-backs
   (or fail closed).  This directly attacks the largest traffic/ACK chain and
   has the highest expected ROI.
2. **Bounded producer-order upper bound.** From one matched checkpoint, compare
   the current 4K virtual producer with the existing native-issue-order
   diagnostic while holding consumer retirement fixed.  Treat it as an upper
   bound unless its storage/order contract is proven bounded.  Gate on reducing
   A-line memory loads toward 8,638 from 12,734 and on reducing Fill cycles;
   exact output and all write/line closures remain mandatory.  This isolates
   how much of the dominant +203,829 Fill-cycle delta comes from lost 16K
   request order/coalescing.
3. **Per-port retry A/B with refusal-cycle counters.** Replace the single global
   retry token with four fixed per-port tokens, without adding requests,
   credits, ports, or payload.  First add reason-specific wait cycles for local
   response capacity versus downstream retry.  Run page/line from one matched
   checkpoint and require the same 16-credit/request high-water marks and exact
   closure.  This is lower-confidence than the producer experiments: line is
   already faster despite 6.76x retries, so retry count alone is not evidence
   of a large speedup.

Two-line ALU batching is not prioritized.  It could use all 16 FP64 lanes with
existing buffers and halve 8,192 launches, but the measured compute-cycle
ceiling is only 3,874 cycles (1.20% of line runtime, 2.04% of the native gap)
before unmeasured scheduler overhead.

## Reproduction

The read-only parser performs fail-closed completion, correctness, frozen tick,
strict-four-context, and first-ROI checks and emits the committed JSON:

```text
python3 experiments/analysis/analyze_xrage_steady_profile.py \
  --verify-binary \
  --output experiments/analysis/xrage_steady_profile_2026-08-13.json
```

It never writes into the frozen campaign and does not launch gem5.
