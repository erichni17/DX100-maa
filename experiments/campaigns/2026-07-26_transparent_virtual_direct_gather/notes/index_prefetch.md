# Direct-index prefetch

The direct-index virtual gather initially allowed only one 64-byte index line
to be pending or buffered. A bounded window now permits multiple index lines
to be in flight while preserving the rule that each line remains resident
until all of its useful words are consumed. Returned lines may arrive out of
order, and an index word is consumed only after row-table insertion succeeds.

## Depth sweep

The 4,096-element random-gather sweep changed only
`virtual_index_buffer_lines`. All four arms used gem5 SHA-256
`f73cf818dc65987d3be343f93dfca2aaf76d0ba992a996516c83454414a3e4c5`
and test-binary SHA-256
`6382974de0c4d2e276294863de88082139a5e1709013aae62e06381ad0988cc0`.
Their generated `config.ini` files differ only in that treatment parameter and
output-directory paths. Depths 1 and 4 captured the uncommitted source state;
depths 2 and 8 captured commit `d9e7fd6`. The only source-content difference
was a brace-style correction made after freezing gem5, so the executable hash
is the authoritative treatment control.

| Lines | Payload | `simTicks` | Speedup vs. depth 1 | Latency reduction |
| ---: | ---: | ---: | ---: | ---: |
| 1 | 64 B | 6,968,632 | 1.000000000x | 0.000000% |
| 2 | 128 B | 4,970,440 | 1.402015113x | 28.674093% |
| 4 | 256 B | 4,244,906 | 1.641645775x | 39.085519% |
| 8 | 512 B | 4,184,810 | 1.665220643x | 39.947898% |

Depth 8 improves latency by only 1.415720% over depth 4 while doubling the
payload budget. Depth 4 is therefore the measured performance-capacity knee:
it is only 1.436051% slower than depth 8 with half the payload. Depth 2 is the
lower-cost point and captures a 28.674093% latency reduction with only 64
additional payload bytes.

All runs produced output hash `5061705292974490889`, zero element or guard
errors, one ROI, normal `m5_exit`, 233 simulated instructions, 256 index-line
reads, 4,096 delivered index words, 257 issued and completed retirement
writes, and zero scratchpad index reads. The intended mechanism changed:
index-line high water equaled 1, 2, 4, and 8 respectively.

The earlier 3,961,328-tick result is not the depth-1 baseline for this test. It
used an SPD-resident index tile and therefore did not model direct-index line
ingestion. The new pair isolates the cost of serial versus overlapped direct
index reads.

## Cost and limits

The conservative depth-4 index payload budget is 256 bytes, an increase of 192
bytes over depth 1, plus tags and control state for up to three additional
line transactions. This is a capacity count, not a synthesized area or power
estimate. Depth-2 host telemetry recorded 32 KiB of aggregate swap-in across
four one-second samples from other workloads, with no swap-out. The job's
cgroup prohibited swap, and gem5 performance is reported in simulated ticks,
not host wall time.

This is one microbenchmark observation. It did not encounter a row-table-full
retry. Promotion still requires a retry-path gate and representative
application validation. No application speedup is claimed from this pair
alone.

## 16K scaling check

A successor pair increased the random gather from 4,096 to 16,384 elements
while retaining a 16K logical window and 4K physical tile capacity. It compared
depth 1 with the depth-4 knee selected above. Both arms used the same frozen
gem5 and test-binary hashes, and their generated configurations differ only in
`virtual_index_buffer_lines` and output-directory paths.

| Lines | Payload | `simTicks` | Speedup vs. depth 1 | Latency reduction |
| ---: | ---: | ---: | ---: | ---: |
| 1 | 64 B | 31,973,889 | 1.000000000x | 0.000000% |
| 4 | 256 B | 20,307,753 | 1.574467101x | 36.486447% |

Both arms produced exact output hash `1782858901698472045`, zero element or
guard errors, one ROI, normal `m5_exit`, 233 simulated instructions, 1,025
index-line reads, all 16,384 index words, zero row-table-full events, and zero
scratchpad index reads. Index-line high water changed from 1 to 4 as intended.
Every retirement write completed: depth 1 issued/completed 2,322 writes and
depth 4 issued/completed 2,318. The four-write reduction is a schedule-dependent
combining effect of the treatment, not evidence that source work was omitted;
the exact output and index-work counters are unchanged.

The larger pair confirms that the four-line window's benefit survives at the
full 16K logical-window size. It does not establish virtualization overhead
relative to native 16K hardware and must not be presented as a production-
benchmark speedup. The matched native/virtual attribution matrix and an
application-level test remain separate requirements.
