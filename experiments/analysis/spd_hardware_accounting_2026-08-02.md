# DX100 SPD hardware-accounting audit

This is a source trace, not a gem5 result. The checked ledger is
`experiments/analysis/spd_hardware_accounting.py`; it fails if the allocation
markers used below disappear. Values use the default topology: 4 cores x 8 SPD
visible lane tiles/core = 32 visible lane tiles, plus two private FP64 logical
payload slots per each of 4 MAAs. No gem5 run was performed.

## Bottom line

An SPD *tile* is one shared 4-byte-lane payload allocation, not a separate
gather, compute, and store allocation. `SPD::SPD` allocates exactly one
`tiles_data[allocated_payload_bytes]`. Its visible prefix contains the
configured lane tiles. Its private tail contains four fixed 4K-word lanes per
MAA: two FP64 logical slots, each represented by two adjacent 32-bit lanes.
All public SPD reads/writes remain limited to the visible prefix; the private
tail is separately allocated internal payload, not additional visible tiles.

| case | arithmetic | payload |
| --- | ---: | ---: |
| native 16K visible SPD | 32 x 16,384 x 4 B | 2,097,152 B (2 MiB) |
| transparent visible SPD | 32 x 4,096 x 4 B | 524,288 B (512 KiB) |
| private logical-SPD tail | 4 MAAs x 2 slots x 2 lanes x 4,096 x 4 B | 262,144 B (256 KiB) |
| transparent visible + private total | 524,288 B + 262,144 B | 786,432 B (768 KiB) |

The complete source-backed transparent SPD payload therefore saves 1,310,720 B
(62.5%) against native 16K at this topology. The visible payload alone is 75%
smaller, but omitting the private tail would understate implemented storage.
This is payload only: it is not a complete hardware-area claim.

For FP64, the code rejects the final tile ID and accesses adjacent tile IDs.
One 4K FP64 logical tile consequently occupies two 4K lane tiles = 32 KiB.
The two private 4K FP64 slots are **64 KiB per MAA** (four lane allocations).
Across four MAAs they are the 256 KiB private tail charged above. The comparable
two native-16K FP64 slots would be 256 KiB per MAA. The hidden lanes are not
added to the public 32-tile count.

## Address aperture versus backing

`MAAConfig.py` still sizes each of the cacheable and non-cacheable SPD data
address ranges from `num_tile_elements`, not `physical_tile_elements`: each is
2 MiB in the default 16K-logical case. SPD itself backs only 512 KiB after a
4K physical setting. The two 2 MiB apertures are address-map exposure, not two
payload arrays. This mismatch must be treated as an interface/translation
requirement, not claimed as saved or allocated SRAM.

## Bounded virtual mechanisms

Each response and combiner slot visibly contains a 64-byte line array. The
direct-index window is map-backed, but the code bounds it in cache lines; its
data capacity is 16x32-bit words per line. The ledger reports per indirect unit
and totals over configured indirect units (default: 4), separate from SPD
payload and simultaneously resident rather than aliases of tiles.

| named point | response slots | combiner slots | index lines | generic / unit | direct / unit | direct / 4 units |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| source defaults (`MAA.py`) | 8 | 16 | 1 | 1,536 B | 1,600 B | 6,400 B |
| matched `run_virtual_tile_consumer_case.sh` | 96 | 384 | 4 | 30,720 B | 30,976 B | 123,904 B |

The consumer-script point comes from its shell defaults (`96`, `384`, and the
command-line `--maa_virtual_index_buffer_lines=4`), not the MAA parameter
defaults. Use `--response-slots`, `--combine-slots`, `--index-lines`, and
`--indirect-units` to select another ledger point; the two named rows are
always emitted for comparison.

The response slot also contains a 64-byte legacy line even in packed-word
mode. Its `packed_words` vector capacity, along with the direct-index tag and
control fields, cannot be translated into a bit-exact SRAM number from this
C++ model alone.

## Retained descriptor/controller state

| state | allocation/path | accounting status |
| --- | --- | --- |
| Offset Table | `OffsetTableEntry[num_entries]`, valid `bool[]`, free-entry vector | capacity is configurable (0 -> logical 16K); three `int`s/entry, padding and vector cost unresolved |
| Row Table | every organization is allocated: `RowTableSlice[]`, row entries, valid/sent/claimed `bool[]` | rows/entries are bounded by DRAM geometry, but `Addr + int + int` width/padding are unresolved |
| completion PTE-like readiness | `vector<array<bool, MaxVirtualPages>>`, `MaxVirtualPages=16`; address range exports 2 bytes/token/page | fixed 16-page model; bool representation/synthesis mapping unresolved |
| retirement/combiner control | bounded slot counts and write-count limit; C++ uses maps/sets/vectors | line payload counted above; tags, pointers, `Addr`, `Tick`, map nodes unresolved |

The source-specific Row/Offset allocation is real and must remain in any full
controller ledger, but a fabricated “N bits per entry” would be less accurate
than this explicit unresolved designation. The existing
`experiments/scripts/report_maa_storage.py` can produce conditional lower
bounds from a frozen `config.ini`; it is not substituted for this source audit.

## Simulator-only/unbounded structures

Do not report these as bounded hardware storage: per-tile waiting vectors;
address-history maps; virtual source-reservation and retirement-page maps;
outstanding-write set node storage; port outstanding/deferred unordered
maps/deques; and all STL allocator/pointer/capacity overhead. The outstanding
write *count* is bounded by `virtual_max_outstanding_writes`, but the C++ set
is not a synthesized descriptor/PTE implementation.

Run:

```sh
python3 experiments/analysis/spd_hardware_accounting.py
python3 experiments/analysis/spd_hardware_accounting.py \\
  --maas 4 --response-slots 96 --combine-slots 384 --index-lines 4 \\
  --indirect-units 4
python3 -m unittest experiments.tests.test_spd_hardware_accounting
```
