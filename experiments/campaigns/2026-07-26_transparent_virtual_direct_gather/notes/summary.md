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
