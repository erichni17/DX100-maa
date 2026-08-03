# Iso-area transparent-SPD ping-pong experiment

> **Superseded provenance and interpretation.** This record came from a
> mutable-binary run whose binary manifest no longer verifies.  Its numerical
> values are retained as historical observations only and are superseded by
> the exact-clean-`c26a082` reproduction record.  In particular, the trace
> intersections below are issue-to-completion *interval-envelope* overlaps;
> they do not prove cycle-aligned simultaneous useful STREAM and ALU work.

This is a completed gem5 measurement, not a fixed-duration trace projection.
All three arms ran the same four-core FP64 virtual-tile consumer, binary,
logical geometry, producer, memory system, queues, response credits, and
completion rule. All produced hash `7228541527853630339` with zero errors.

## Result

| Arm | simTicks | Descriptor interval | Fill ticks | ALU ticks | Store ticks | Cross-unit interval-envelope overlap |
|---|---:|---:|---:|---:|---:|---:|
| serial 4K | 46,659,849 | 44,590,918 | 1,292,690 | 641,024 | 7,602,770 | 0 |
| serial 2K | 47,193,514 | 45,124,270 | 1,292,690 | 641,024 | 8,549,282 | 0 |
| two-half 2K ping-pong | 46,647,329 | 44,578,085 | 1,293,003 | 642,589 | 8,445,366 | 642,589 |

Serial 2K regresses 1.143735% versus serial 4K.  The serial-2K versus
ping-pong-2K comparison is the treatment-only ping-pong comparison: both use
2K chunks, with controller mode the intended treatment.  Ping-pong is
1.170882% faster than that control.  The serial-4K comparison is iso-area
overall-design context, not treatment-only evidence, because it also changes
chunk size; its 0.026840% difference must be interpreted accordingly.

The interval checker found eight legal issue-to-completion envelope
intersections: each 2K ALU interval intersects either the next half's fill or
the preceding half's store. Their total envelope duration is 642,589 ticks.
It found no STREAM/STREAM or ALU/ALU interval intersection. Each page obeys
fill-complete <= compute-issue and compute-complete <= store-issue. These
intervals establish legal scheduling envelopes, not proven concurrent useful
work; that would require direct dual-progress instrumentation. Exact per-chunk
intervals are in the adjacent JSON evidence file.

## Fixed-area ledger

The fair fixed payload budget is 589,824 bytes for the measured one-MAA,
four-core configuration:

- visible SPD: 32 lanes x 4096 elements x 4 bytes = 524,288 bytes;
- logical-SPD Runtime payload: 1 MAA x 2 FP64 slots x 4096 x 8 bytes =
  65,536 bytes (Runtime-owned and unused by this path; it is not an SPD tail);
- total MAA-local payload: 32 visible SPD lanes plus two Runtime FP64 slots =
  589,824 bytes.

Within the visible total, the transparent descriptor owns three disjoint FP64
spans: a 32 KiB completion-token span, a 32 KiB physical-input span, and a
32 KiB out-of-place output span. These are not additive to the 524,288-byte
visible total. Coherent logical backing and destination are each 131,072 bytes
in memory, not MAA-local SRAM. Serial 4K and serial 2K reserve the same input
and output spans as ping-pong; serial 2K intentionally leaves their upper
halves unused. Ping-pong merely assigns the two 2K halves finite owner tags.

Every arm has one STREAM unit, one 16-lane ALU, four SPD read ports, four SPD
write ports, four stream words/cycle, one memory channel, 32 IF entries, 128 x
16 STREAM request-table entries, a 16K indirect Offset Table, 16 x 64 x 8
initial Row-Table geometry, 96 response slots, a 480-word response pool, 384
combiner slots/4096 words, four direct-index lines, 64 outstanding write
credits, 512 cache-side credits, 512 CPU-side credits, and four producer-page
ready credits. The controller has no request FIFO: it exposes at most one
STREAM and one ALU value at a time.

The STREAM Request Table's bounded arrays are 14,336 semantic bytes (18,432
bytes with the x86-64 padded 8-byte entry). The 16K Offset Table plus validity
and free-list indices is 278,528 semantic bytes. Ramulator's 1-channel,
1-rank, 4-bank-group x 4-bank geometry causes all four Row-Table organizations
to be allocated: 2/4/8/16 slices with 64/32/16/8 entries per row. Across those
organizations are 32,768 entries and 1,920 rows; entry data, entry valid and
claimed bits represented as bytes, row grow/cursor state, slice row valid/sent
state, and per-slice request state total 616,734 core array bytes. None changes
between arms.

Exact allocated SPD model metadata is 131,392 bytes: 32 one-byte tile states,
32 dirty bits represented as bytes, 64 ready bytes, 128 size bytes, 131,072
per-element completion bytes, and 64 port-busy timestamp bytes. The logical
Runtime owns its private 65,536-byte payload without appending SPD lanes. The
finite controller's semantic state is 183 bytes; the measured x86-64 C++ object is
208 bytes including ABI padding. Existing MAA virtual-page state is 1,408
semantic bytes. Its MMIO metadata apertures total 1,344 bytes (64 size, 64
ready, 1,024 page-ready, 128 scalar registers, 64 IF). The virtual response,
combiner, and direct-index line arrays are respectively 6,144, 24,576, and 256
bytes. Response-slot tags are 4,704 semantic bytes and combiner-slot tags are
4,224 semantic bytes; the dynamic packed-response occupancy limit is
separately 3,840 bytes.
Allocator/pointer overhead of simulator `std::vector`, `map`, and `set`
objects is not presented as synthesized SRAM. The executable ledger records
all byte arithmetic and every bounded queue/credit dimension.

Completion semantics remain the pre-existing rule: return descriptor-lifetime
tile credits and retire only after the final native `STREAM_ST` completion
(accepted by the memory hierarchy, not a new DRAM-persistence fence).

## Traffic and rows

| Arm | source reads | DRAM reads | writes issue/complete | rows inserted/unique | ACT/PRE |
|---|---:|---:|---:|---:|---:|
| serial 4K | 9,634 | 26,997 | 5,298 / 5,298 | 1,403 / 129 | 5,599 / 4,515 |
| serial 2K | 9,634 | 27,006 | 5,298 / 5,298 | 1,403 / 129 | 5,600 / 4,517 |
| ping-pong 2K | 9,634 | 26,996 | 5,299 / 5,299 | 1,403 / 129 | 5,604 / 4,515 |

The one-write and small DRAM-command differences are measured scheduling
effects, not extra configured credits, ports, or queues.

## Evidence and gates

- controller C++ test plus 11 source-contract tests: PASS;
- ledger/analyzer tests: 4 PASS;
- full `build/X86/gem5.opt`: PASS;
- gem5 SHA-256: `4c87311804fcbbef4b861945b403392e3861d87b010f9de88fae6d97c966ecb8`;
- benchmark SHA-256: `c25d9a55520a6d9bacedae67089d9b6611603381cf0af2db3e0de2566d1977db`;
- normalized common area/config SHA-256 (excluding only controller mode):
  `6faa119dbf304c669a50409c289372d2d87b1a158d30e5de7b5cdae21375fea0`;
- trace SHA-256 serial4/serial2/ping-pong:
  `21c942d31bdba97407b6fc6a4563e065192e3d702751ce9dbd2b2d1b21f5bbfa`,
  `65aa778eb35415a40ff28f666e4a997e9e1ab1b39c7c870af49184a96af06eb8`,
  `6a7f3f6464b191a8c528e92ff97b8ec200b2149f56bb3bbaade8042bd0b919ac`.

Raw frozen snapshots, logs, stats, configs, and hash manifests are under
`/tmp/dx100-isoarea-pingpong-20260803`. The committed JSON is the compact
measurement record; the analyzer can independently regenerate it from those
raw directories.
