# Virtual Gather Mechanism Audit

## Native 16K path

For `C[i] = A[B[i]]`, the native path performs four macro operations:

1. Stream 16K sequential 32-bit B indices into one SPD tile ID.
2. Insert the resulting A cache-line addresses into the 16K Row/Offset tables.
3. Issue A reads in the Row-Table order and write 16K 64-bit results into two
   adjacent SPD tile IDs.
4. Stream those results from SPD to dense C memory.

The speedup-producing reorder state is the Row/Offset metadata, not the A-result
payload tile itself.

## Original compact XRAGE path

The old `compact16` path still has a native-size physical SPD and a full B tile.
It fuses the indirect A load with the following dense C stream store: returned A
words bypass the destination SPD payload and retire through bounded response and
C-line combining buffers. Its full-XRAGE gain was therefore fusion, destination
SPD bypass, and retirement overlap. It was not evidence that a smaller physical
tile was faster.

## Current fully bounded direct-index 4K path

`INDIR_LD_VIRTUAL_INDEX` receives the A base, B base, C backing base, logical
range, and a completion token. It operates as follows:

1. Read sequential B lines into a bounded 128-line (8 KiB) feeder.
2. Consume B words into 4K-capacity Offset and Row tables. A B line can be
   released once all useful words from that line have been inserted.
3. Drain and issue A cache lines after each 4K descriptor epoch, then continue
   the 16K logical instruction with the next epoch.
4. Keep only bounded live A responses: 128 response slots sharing a 480-word
   64-bit pool.
5. Place returned A words into a 384-line C write combiner and retire them to
   dense C memory. The nominal destination tile is completion state, not A
   payload storage.

The first direct-index version bounded live B/A/C payload but retained 16K Row
and Offset descriptors, and therefore preserved the full 16K source reorder
opportunity. A later Row-only version used 4K Row capacity but retained a 16K
Offset array. The current version bounds both structures at 4K. It does not
preserve one 16K reorder window and does not page four complete SPD payloads;
instead, it executes four bounded descriptor epochs and retires C directly.

A same-binary, three-arm experiment separates storage from scheduling. At a
fixed 4K epoch, 16K and 4K Offset arrays produced identical timing, writes,
DRAM commands, and MAA issue traces across all 14 FLAG gathers. Changing the
epoch itself produced a -1.051% geometric-mean latency change because it altered
when A responses reached the fixed C combiner. Thus the occasional speedup is a
schedule/coalescing effect, not a claim that virtualization is intrinsically
faster. See `offset_capacity_epoch.md`.

The limitation is equally important: this implementation handles a gather whose
result can retire directly to memory. It has not shown transparent paging for an
arbitrary later MAA instruction that expects the entire destination tile in SPD.

## Professor's subset proposal

Keeping a selected subset of B descriptors in DX100 and rescanning cached B for
other subsets is a way to recover a larger reorder window when descriptor
capacity is limited. An intermediate experiment reduced active Row-Table capacity to 4K
while retaining the 16K Offset Table. One-pass dynamic drain was only 1.127%
slower geometrically across all 14 FLAG gathers, so repeated scans are not the
default. On FLAG00, cached two- and three-partition policies with C-combiner
retention were 4.639% and 4.582% slower than full descriptors; one pass was only
3.013% slower. See `descriptor_capacity.md` for the complete distinction and
evidence.

## Meaning of pure virtualization

The early direct-gather microbenchmark replaced native destination-SPD
completion with backing-memory retirement while keeping source work equivalent.
Its initial 71.565441% overhead came from issuing 4,096 small retirement writes.
The 8.263473% version coalesced those into 255 full-line writes plus two partial
writes. That optimization changed C write geometry and concurrency; it did not
remove paging overhead.

A stricter producer/consumer test stores virtual output to backing memory and
then makes a later consumer reload it. That path measured 10.410% overhead with
warm backing data and 19.77% when the data was displaced. It is the closer model
of the cost that a general virtual tile chain must pay.
