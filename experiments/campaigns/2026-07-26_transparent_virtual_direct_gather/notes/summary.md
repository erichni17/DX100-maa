# Transparent Virtual Direct Gather

Both arms completed with one exact `errors=0` result, one ROI marker, normal
`m5_exit`, and no panic or fatal diagnostic. The virtual arm issued and
completed 4096 retirement writes, so the candidate mechanism was active.

The native arm took 3,658,970 first-dump `simTicks`; the virtual arm took
6,277,528. Virtual gather was 1.715654405x slower, or 71.565441% overhead.
This is the expected direction for a design that replaces direct scratchpad
completion with backing-memory retirement.

The CG retirement geometry then reduced virtual time to 3,961,328 ticks. This
is a 1.584702908x speedup over the default virtual geometry and leaves 8.263473%
overhead relative to native. It converted 4096 partial writes into 255 full-line
writes and two partial writes. Its conservative structure and in-flight payload
budget is 36,864 bytes. The optimized arm therefore improves the mechanism for
a concrete reason, while preserving the expected native-versus-virtual order.

The 71.565441% to 8.263473% change is not a paging optimization. Both arms are
the same backing-memory retirement mechanism. The improvement comes from
coalescing many per-word C retirement writes into cache-line writes and allowing
more bounded source responses in flight. This microbenchmark result is also
separate from the first direct-index mechanism, which read B through a bounded
line feeder while retaining a full 16K Row/Offset reorder window. The current
fully bounded successor uses 4K descriptor epochs instead.

A five-point cost sweep found a knee near 23,808 modeled bytes: this point had
10.111206% overhead, while 32,768 bytes had 9.067579% overhead. Both issued 255
full-line and two partial writes. Adding the fixed 4 KiB retirement-cache data
array gives lower-bound totals of 27,904 and 36,864 bytes. These counts exclude
cache tags, control logic, and physical-design effects, so they are not area
estimates.

This result isolates one direct gather. It does not explain application-level
CG or XRAGE performance, and it has one observation per arm. Application
speedups require separate attribution to fusion, overlap, scheduling, or a
configuration mismatch rather than a claim that virtualization itself is
intrinsically faster.

Routing the direct-index B feeder through the cache was also tested and
rejected. It reduced total DRAM reads by 6.540%, activates by 10.872%, and
precharges by 14.764%, but made FLAG00 0.110% slower in two exact deterministic
observations. The A-request order and output were unchanged; cache routing made
B fill 167 MAA cycles slower instead of removing the direct-versus-compact
critical-path gap.

As an intermediate step, reducing active Row-Table capacity from 16K to 4K
entries was validated on
all 14 FLAG gathers. It added only 1.127% geometric-mean latency, with a -0.626%
to +3.165% range, while exact output and treatment-only configuration checks
passed. The Offset Table remains 16K, so this is a 4K-Row/16K-Offset result, not
yet a fully bounded 4K descriptor subsystem. The modeled comparable lower bound
falls from 842,482 to 682,322 bytes. See `descriptor_capacity.md`.

The Offset Table was subsequently made reusable and bounded to 4K entries. A
three-arm same-binary experiment separated its storage capacity from its drain
epoch across all 14 FLAG gathers. At a fixed 4K epoch, 16K and 4K Offset
capacity produced identical ROI ticks, writes, DRAM commands, and MAA issue
traces in every case. Changing only the epoch from 16K to 4K changed geometric-
mean latency by -1.051% and writes by -4.867%. The prior surprising gains are
therefore caused by schedule-dependent C write coalescing, not by the storage
shrink or by intrinsically faster virtualization. The fully bounded comparable
storage lower bound is 653,138 bytes. See `offset_capacity_epoch.md`.

Follow-up sweeps rejected the obvious local alternatives. A 2K epoch was
3.505% slower, an 8K descriptor window was 0.353% slower while using roughly
twice the descriptor state, and increasing the B feeder from 128 to 192 lines
improved one case by only 0.105% for 4 KiB more payload. Trace accounting shows
that source-flight latency, not an empty refill bubble, is now the dominant
remaining gap. See `bounded_window_followups.md`.

The complete XRAGE input was then rerun as a same-binary seven-arm attribution.
The original eight-line direct4 mechanism is 5.723% slower than `compact16`,
confirming the expected virtualization overhead. A deeper bounded B feeder,
Row-bound scheduling, and a 4K Offset epoch recover that loss and make the
current bounded arm 7.146% faster than `compact16`. The dominant 4K-epoch gain
comes from reducing dense C retirement writes from 327,924 to 262,903, not from
preserving the 16K A reorder window. Offset storage shrink itself remains a
0.000% effect at a matched schedule. See `xrage_full_attribution.md`.
