# Fixed direct-index feeder audit (2026-08-28)

## Decision

Integrate the fixed feeder.  The mapping from the former three dynamic maps is
semantically exact for configured capacities 1 through 128, including the
selected 64-line point.  The default request-generation width is one line per
cycle; widths two and four exist only for bounded sensitivity experiments.

This is a functional fixed-storage model suitable for hardware reasoning.  It
is not an RTL area, frequency, or power result.

Worker implementation checkpoint:
`35a54fe85ac0d9c13d26b412a2916cf1d674abbe`.  The integrated lead checkpoint
after the global-victim follow-up is `85b1b2b3`.  Its optimized gem5 SHA-256 is
`182a6696a60983aa690fa6b4131592cff4408b380891fa31098f1f978cdada0d`.

## Source audit and exact mapping

The old representation used:

- `map<Addr, vector<pair<int,uint16_t>>>` for pending line requests;
- `map<Addr,int>` for the number of live words in ready lines; and
- `map<int,DirectIndexWord>` for one host record per ready word.

`DirectIndexFeeder` replaces those containers with 128 fixed line slots.  Each
slot contains exactly one 64-byte payload, a physical response tag, a
free/pending/ready state, a phase, a 16-bit reservation mask, a 16-bit payload
valid mask, and one logical owner for each of the sixteen 32-bit physical
words.  A configured capacity gates the active prefix of those slots.

The live behavior maps as follows:

1. `fillDirectIndexWindow` derives the same translated 64-byte line and the
   same `{logical iteration, physical word}` reservations.  Allocation rejects
   a duplicate tag or owner and stops at configured line capacity.
2. One normal `MemCmd::ReadReq` is still sent through the existing cache/DRAM
   path.  The feeder only owns bounded storage and generation credits.
3. A response matches an exactly pending physical tag, writes one 64-byte
   payload, and atomically changes its reserved words to ready.  It no longer
   creates sixteen independent host map records.
4. Read and consumption match the logical owner, validate the shared phase and
   expected value, and clear only that word.  The slot becomes free only after
   its final owner is consumed.  The same physical address can then be reused.
5. Phase changes and adaptive-pass transitions already require an empty
   feeder.  Reset clears payload, tag, state, masks, owners, high-water marks,
   and issue counters; stale/duplicate operations fail closed.

No unbounded metadata or response-side word fanout remains.  A memory response
writes one line, and normal Row/Offset admission consumes individual words.
The line/owner match is a bounded fixed search; this work does not claim a
synthesized CAM timing or Fmax.

## Finite request-generation width

The former fill loop could allocate all 64 selected entries at one `curTick`.
The feeder now accepts at most the configured one, two, or four allocations in
the caller-supplied cycle.  A blocked allocation schedules the indirect unit
one cycle later.  Cache and DRAM acceptance, coalescing, and response latency
remain unchanged.

The new counters are:

- `IND_VirtIndexIssueCycles`: cycles with at least one generated line;
- `IND_VirtIndexIssueWidthStalls`: allocation attempts stopped at the width;
- `IND_VirtIndexIssuePeak`: the sum of each operation's peak lines/cycle.

The optimized and ASan/UBSan unit test checks exact peaks at widths one, two,
and four and exhaustively configures every line capacity from 1 through 128.

## Selected depth-64 sensitivity

One accepted NA256 matched checkpoint was used for three short arms.  Feeder
depth 64, masked-line retirement, guest, checkpoint, semantic work, and gem5
binary were fixed.  Only request-generation width changed.

| Lines/cycle | `simTicks` | B lines | Issue cycles | Peak sum / 10 operations | Width stalls | P writes |
|---:|---:|---:|---:|---:|---:|---:|
| 1 | 246,463,712 | 10,240 | 10,240 | 10 | 42,513 | 26,672 |
| 2 | 246,463,712 | 10,240 | 8,436 | 20 | 37,362 | 26,672 |
| 4 | 246,463,712 | 10,240 | 7,838 | 40 | 34,241 | 26,672 |

Every arm passes the exact CG fingerprint, all 11 deterministic reductions,
ten whole P/Q windows, and all 26,672 masked P write ACKs.  The one-line/cycle
default reproduces the selected depth-64 NA256 `simTicks`; the observed speed
point therefore does not depend on creating 64 queue entries at zero cycle
cost.  Equal `simTicks` across this short deterministic sensitivity is an
observation, not a general claim that request-generation width never matters.

The integrated lead binary was independently replayed at depth 64 and width
one.  It exits zero in 246,463,712 `simTicks`, reproduces the exact output and
all deterministic reductions, closes all 26,672 masked writes, and reports
10,240 issued B lines across 10,240 issue cycles.  Its result root is
`/data1/nier/dx100-runs/2026-08-28-lead-fixed-feeder-na256-width1-r1`;
`result.json` SHA-256 is
`12409396f0038c7f1892c82210efd39bfc0cb7d6f1876a152b5bf8e8b1021cfc`.
The 13-file ledger is
`experiments/analysis/fixed_direct_index_integrated_na256_artifacts_2026-08-28.sha256`.

The preserved result root is
`/data1/nier/dx100-runs/2026-08-28-fixed-direct-index-width-sensitivity-r1`.
The 54-file artifact ledger is
`experiments/analysis/fixed_direct_index_width_sensitivity_artifacts_2026-08-28.sha256`.
Result SHA-256 values for widths one/two/four are respectively
`6763cb0a837e2da9dfdc5d1a007fa4e852e8958091f726abeb4d279ec5368306`,
`54473ba5b38d19429c4d8373043ab3671026e1d8aaff4739107ed1882f39550d`,
and `171f7548bab4adcd40b2b6447366ee630c9e1c7fae3a211aebed5bfffc6f61a4`.

## Packed storage accounting

The report counts semantic packed bits and explicitly excludes host `sizeof`,
STL nodes, allocator data, and padding.

For a 16K logical domain and 64-bit physical address tag, each line has 322
control bits: 64 tag, two state, sixteen reservation, sixteen payload-valid,
and sixteen 14-bit logical owners.  The selected 64-line configuration has:

- 4,096 payload bytes per indirect unit;
- 20,650 packed control bits per indirect unit, including 42 global bits; and
- four configured indirect units, hence 16 KiB of feeder payload total.

The source class supports a fixed maximum of 128 lines.  The report therefore
also exposes the maximum supported provision: 65,536 payload bits and 41,259
control bits per indirect unit.  Configured-active and maximum-supported counts
are separate; neither is inferred from the 19,576-byte C++ host object.

The selected storage report is
`/data1/nier/dx100-runs/2026-08-28-fixed-direct-index-width-sensitivity-r1/storage64/maa_storage.json`,
SHA-256
`4363553d06e03c2358af8bb31ec518d8a4d3d16c6952adc8cce28d56c8db2f97`.

## Shared packed-payload victim correction

The lead audit found a separate legal panic in the destination combiner.  The
word pool is global, while the old victim loop was always limited to the
incoming address's set.  A globally full word pool with a free incoming set
therefore had no local valid victim.  The first worker integration selected a
global victim but then incorrectly required that victim to free the incoming
set's slot.  Checkpoint `85b1b2b3` separates those two resource effects.

The corrected bounded policy is:

- if the incoming set is full, select a local victim so eviction creates a
  legal line slot;
- if only the shared word pool is full and a local target/free slot exists,
  scan the finite global line table for a payload victim;
- retain the incoming target when the global victim is in another set; and
- advance both the global payload pointer and the victim's actual set pointer,
  never the incoming set pointer by assumption.

No line or word capacity changed.  Packed configurations add only one global
victim pointer: nine bits for 384 line slots.  The optimized and ASan/UBSan
adversarial test fills the global pool from set zero while set one is empty,
evicts from set zero, inserts into set one, rejects a wrong transaction ACK,
and closes only on the exact masked-write `{address,generation,transaction}`.

## Validation and limits

Passed:

- fixed-feeder optimized and ASan/UBSan adversarial tests;
- packed-payload/global-victim optimized and ASan/UBSan adversarial tests;
- ten storage-ledger unit tests and Python compilation;
- gem5 style and all source pre-commit hooks;
- incremental `IndirectAccess.o` and `MAA.o` builds; and
- complete optimized `build/X86/gem5.opt` link.

The local Ramulator nested submodules were empty.  For the complete link only,
the build temporarily referenced headers and `libramulator.so` from a sibling
worktree at the exact expected nested gitlink commits; all temporary symlinks
were removed and no external content entered the commit.

No full workload was launched.  The only simulator execution was the short
three-arm NA256 sensitivity above.  A future promotion still needs synthesis
or a separately justified tag/CAM/mux timing and energy model; host object size
and this packed semantic ledger are not area evidence.
