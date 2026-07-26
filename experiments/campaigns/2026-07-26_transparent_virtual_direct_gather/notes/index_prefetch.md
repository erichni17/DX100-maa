# Direct-index prefetch

The direct-index virtual gather initially allowed only one 64-byte index line
to be pending or buffered. A bounded window now permits multiple index lines
to be in flight while preserving the rule that each line remains resident
until all of its useful words are consumed. Returned lines may arrive out of
order, and an index word is consumed only after row-table insertion succeeds.

## Paired result

The paired 4,096-element random-gather test changed only
`virtual_index_buffer_lines` from 1 to 4. Both arms used gem5 SHA-256
`f73cf818dc65987d3be343f93dfca2aaf76d0ba992a996516c83454414a3e4c5`
and test-binary SHA-256
`6382974de0c4d2e276294863de88082139a5e1709013aae62e06381ad0988cc0`.
Their generated `config.ini` files differ only in the treatment parameter and
output-directory paths.

Depth 1 took 6,968,632 first-ROI `simTicks`; depth 4 took 4,244,906. Depth 4
is 1.641645775x faster, corresponding to a 39.085519% latency reduction. Both
runs produced output hash `5061705292974490889`, zero element or guard errors,
one ROI, normal `m5_exit`, 256 index-line reads, 4,096 delivered index words,
257 issued and completed retirement writes, and zero scratchpad index reads.
The intended mechanism changed: index-line high water increased from 1 to 4.

The earlier 3,961,328-tick result is not the depth-1 baseline for this test. It
used an SPD-resident index tile and therefore did not model direct-index line
ingestion. The new pair isolates the cost of serial versus overlapped direct
index reads.

## Cost and limits

The conservative index payload budget rises from 64 to 256 bytes, an increase
of 192 bytes, plus tags and control state for up to three additional line
transactions. This is a capacity count, not a synthesized area or power
estimate.

This is one microbenchmark observation. It did not encounter a row-table-full
retry. Promotion still requires the 16K attribution result, a retry-path gate,
and representative application validation. No application speedup is claimed
from this pair alone.
