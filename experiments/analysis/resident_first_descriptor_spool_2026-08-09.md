# Resident-first counted descriptor spool (2026-08-09)

## Result

The resident-first successor to `ab9666f6` is implemented and validated. It
keeps the timed counted-grow summary, scans B exactly once more to classify the
final plan, admits one deterministic population directly, and writes and reads
only the other three populations. The mechanism uses a 48-bit external record
(46 payload bits: a 14-bit logical iteration and a 32-bit index value), fixed
staging and credit scoreboards, and timing-visible coherent backing.

The final four-arm matrix completed and exact-output matched. Resident-first
reduced descriptor write-plus-read traffic from 262,144 to 147,456 bytes
(-43.75%) and total B-scan-plus-spool traffic from 393,216 to 278,528 bytes
(-29.17%). It did not improve total time in this workload: 62,572,456 simTicks
versus 61,940,509 for the accepted ab spool reference (+1.0202%). The split is
useful for the next optimization: fill time fell 8.1479%, but request time rose
8.6582%.

Implementation checkpoints are:

- `69c62ec50646ba8bc83d1ed1f93045812246b74a`: resident-first mechanism and
  focused tests.
- `fcfa193c1226fee6a6ebc5cf30052dded653df6e`: `59ad3fbb` accounting semantics
  and the matched evidence runner.
- `a0677ed7315fb768bc72cc95c0728f7b8b33a7ee`: final trace-side feeder closure
  used by the passing matrix (with `bcdaf655` restoring the runner mode).

## Mechanism

The timed summary scan observes all 16,384 B words and builds the existing
bounded counted grow/quota plan. The plan must produce four populations of at
most 4,096 entries; overflow, an unrepresentable grow, a plan that needs more
passes, or insufficient backing is fatal. There is no iteration-range or other
fallback.

After the plan is accepted, `residentPass()` selects the largest planned
population, breaking ties in favor of the lowest pass number. This is derived
only from the timed plan. In the evidence workload all populations are 4,096,
so pass 0 is resident. During the second and final B scan, pass-0 descriptors
flow directly into Word/Offset/RowTable state. Nonresident descriptors are
packed into their segment and leave the B feeder. After all segment writes are
acknowledged, the fixed decoder replays passes 1 through 3 from coherent
backing. It reconstructs the original index-word address from the stored
logical iteration and a fixed 17-page translation map, preserving the exact
logical destination mapping without serializing a host pointer.

The external record is six bytes. Its low 14 bits hold the iteration and its
high 32 bits hold the index value. Records are densely packed and may cross a
64-byte boundary; each segment therefore has one 64-byte staging line and a
five-byte carry. For the exact 4,096-entry populations each external segment
contains 24,576 bytes (384 lines), so three segments require 73,728 bytes and
1,152 lines. No resident descriptor is written or read.

Retry behavior is idempotent. A full-line write-credit denial returns before
advancing the B feeder or committing the counted ordinal, so the next attempt
re-inspects the same word exactly once. The terminal ledger requires
`attempts == unique_commits + retry_inspections`. Final partial-line flush
stalls have a separate counter and explicitly report zero B re-inspections.
Write ACKs are required before replay starts; each replay descriptor is retired
once, and operation completion waits for source responses, consumer/combiner
work, backing reads and writes, and all write ACKs.

## Bounded-state ledger

| State | Bound | Evidence/role |
|---|---:|---|
| Logical descriptors | 16,384 | Fixed logical tile size; never active as a functional descriptor array |
| Active Word entries | 4,096 | `bounded_word_entries=4096` |
| Active Offset entries | 4,096 | `bounded_offset_entries=4096` |
| Row directory / row lines | 512 / 4,096 | `bounded_row_directory_entries=512`, `bounded_row_line_entries=4096` |
| Resident population | 1 x 4,096 | Direct admission during the final B scan |
| External segments | 3 x 4,096 | Finite, independently bounded backing regions |
| External descriptor | 6 bytes | 14-bit iteration + 32-bit value (46 used bits) |
| External backing | 73,728 bytes | 3 x 24,576; exact payload and reserved bytes |
| Staging | 207 bytes | 3 x (64-byte line + 5-byte carry), reported as 35 descriptor-equivalents |
| Write credits | 16 | Fixed address/data scoreboard; observed high-water 16 |
| Read credits | 4 lines | Four fixed 64-byte response slots; observed high-water 4 |
| Index translation map | 17 pages | Fixed address and valid arrays; no host map |
| Candidate control charge | 1,763 bytes | Includes spool control, staging, both scoreboards, current descriptor, and page map |
| B feeder | 4 cache lines | Spool treatment caps the ordinary feeder to four lines; no operation-sized replay queue |
| Response/combiner | 96 slots, 480 response words; 384 combiner slots, 4,096 words | Existing finite virtual-tile consumer resources |

The functional spool object contains no `std::vector`, operation-sized set,
identity bitmap, host descriptor queue, or decoded replay queue. Exhaustive
identity checking is emitted in the trace and validated by the runner. The
legacy `my_unique_WORD_addrs`, `my_unique_CL_addrs`, and `my_unique_ROW_addrs`
sets remain unchanged and functional for every non-spool path; only the
descriptor-spool treatment suppresses their updates and uses the explicit
bounded-plan cache route. Focused source gates protect that distinction.

## Correctness and accounting closure

The resident-first completion trace reports:

```text
b_scans=2 descriptors=16384 resident_pass=0 resident_descriptors=4096
external_descriptors=12288 external_segments=3 descriptor_bytes=6
payload_bytes=73728 write_lines=1152 write_acks=1152 read_lines=1152
read_responses=1152 control_bytes=1763 backing_bytes=73728
staging_bytes=207 write_hwm=16 read_hwm=4 unique_inspections=16384
retry_inspections=3 final_flush_stalls=0 active_limit=4096 fallback=none
```

The trace-side exact-once validator independently closed at 16,384 admitted
and 16,384 retired, with zero duplicate admissions, zero duplicate
retirements, and zero missing records. The accounting distinguishes 16,384
summary inspections, 16,384 unique bucket inspections, three credit-denial
retry re-inspections, and zero final-flush stalls. Thus
`index_filter_words=32771 = 16384 + 16384 + 3`; retries are not hidden in the
unique bucket count.

All descriptor traffic closed exactly: 1,152 write issues, 1,152 write ACKs,
1,152 read issues, and 1,152 read responses. The overall virtual consumer also
closed at 2,635 write issues/completions, four ready pages, a single ROI result,
a single m5 exit, balanced stats sections, and no fatal/error signature.

## Focused validation

The following gates passed before gem5 evidence:

```text
experiments/scripts/run_bounded_range_pass_unit.sh
  bounded_range_pass_test: PASS
  bounded_quantile_ranges_test: PASS
  bounded_metadata_ledger_test: PASS

experiments/scripts/run_bounded_descriptor_spool_unit.sh
  bounded_descriptor_spool_test: PASS

python3 -m unittest \
  experiments.tests.test_descriptor_filter_accounting \
  experiments.tests.test_descriptor_spool_live_contract
  15 tests: OK

scons -j16 build/X86/gem5.opt
  done building targets
```

The C++ tests cover dense 4 x 4,096 populations, six-byte cross-line pack and
unpack, exact segment bounds, write ACK gating, read-credit/replay ordering,
duplicate/unknown response failures, deterministic resident selection, reset,
and fail-closed overflow. The Python gates cover absence of hidden spool state,
preservation of non-spool uniqueness behavior, the retry/final-flush ledger,
portable exact trace closure, shared checkpoints, and four concurrent isolated
arms.

## Exact matched evidence

The accepted evidence is at:

`/data1/nier/worktrees/codex-coordination/sessions/resident-first-spool-20260809-20260809-025450-7603f4d7/evidence/resident_first_descriptor_spool_a0677ed7/matched_matrix`

`matrix.exit` is 0, `matrix.complete` exists, every arm has restore exit 0 and
`virtual_tile_consumer_case.pass`, and every arm reports output hash
`7228541527853630339`.

| Arm | Source/binary treatment | simTicks | Exact output |
|---|---|---:|---|
| native16 | resident binary, physical 16K | 41,243,697 | matched |
| native4 | resident binary, physical 4K | 59,537,295 | matched |
| ab spool reference | accepted `59ad3fbb` binary, physical 4K | 61,940,509 | matched |
| resident-first | `a0677ed7` source / resident binary, physical 4K | 62,572,456 | matched |

The two spool arms use the identical checkpoint path and identity
`38a1290811f22f712d6854fc939e57950a7099a49a84d379dfa460281950998f`,
and identical `paged 4096` treatment hash
`8b007d788e5e0a0c400bafb88417e0f3dcfc543a28f801ee9f7a7141a1930195`.
They match both the 16,384-record physical admission hash
`2ffa5770f8b124c5c53120e7f9735714ba5d4be518b26c28b91d9a16de41f2c8`
and the counted-summary histogram hash
`ac9bb2cc5d0ce59570c660c34d2e9a296ada3cdf027c7e954b868cfca41b6d98`.

Frozen input provenance is explicit:

- Workload SHA-256:
  `96d274918b1164ed692f452d78761ea96f79c117d35176fb2df0e62453c3e066`.
- Canonical Ramulator SHA-256:
  `76ea3a9c7467a5fc0dc04f2b5f083909c03e8b7280c1872046fc78edb2a15753`.
- Accepted ab-reference gem5 SHA-256:
  `328a38f70b759ccf9585a60bae3aa6e5a5c77c1f0f1ebfb013cbd068a43d1056`.
- Resident gem5 SHA-256:
  `aaff496e9cc822367036ab80ef632c5d35d6d7374706d33416698c2e0e958716`.
- Shared config SHA-256:
  `aacc6e624b7ab0e7b032d5cb913974fa790efdca84598bf468c11f14b9575d0f`.

## Bottleneck deltas

Relative to the accepted ab spool reference, resident-first changes:

| Counter | ab reference | resident-first | Delta/interpretation |
|---|---:|---:|---|
| Descriptor write/read bytes | 131,072 / 131,072 | 73,728 / 73,728 | -43.75% traffic |
| Write-credit stalls | 1,116 | 3 | Almost eliminated; resident has 3 true retry inspections |
| Read-credit stalls | 18,278 | 13,299 | -27.24% |
| Control/backing bytes | 2,449 / 131,328 | 1,763 / 73,728 | -28.01% / -43.86% |
| DRAM reads | 24,432 | 14,860 | -39.18% |
| DRAM activates/precharges | 3,502 / 2,433 | 2,474 / 1,909 | -29.35% / -21.54% |
| Fill simTicks | 23,974,548 | 22,021,115 | -8.15% |
| Request simTicks | 30,059,268 | 32,661,863 | +8.66% |
| First/all page-ready cycles | 172,624 / 172,636 | 174,651 / 174,709 | Later and wider (12 to 58 cycles) |
| Total simTicks | 61,940,509 | 62,572,456 | +1.02% |

The external spool and its credit pressure are no longer the total-time
bottleneck. The remaining regression is after fill: resident-first changes
admission/source-request ordering, increases request-stage time, and expands
the page-ready span despite lower DRAM command traffic. The next optimization
should target request ordering and cache/MSHR behavior while preserving the
resident/external ledger and exact physical-admission hash; reverting to hidden
sorting or a materialized descriptor set would invalidate the result.
