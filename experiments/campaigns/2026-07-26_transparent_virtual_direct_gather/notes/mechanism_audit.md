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

## Current direct-index 4K path

`INDIR_LD_VIRTUAL_INDEX` receives the A base, B base, C backing base, logical
range, and a completion token. It operates as follows:

1. Read sequential B lines into a bounded 128-line (8 KiB) feeder.
2. Consume B words into the baseline 16K Offset and Row tables. A B line can be
   released once all useful words from that line have been inserted.
3. After the full logical window is described, select A cache lines using the
   baseline native issue order across all 16K iterations.
4. Keep only bounded live A responses: 128 response slots sharing a 480-word
   64-bit pool.
5. Place returned A words into a 384-line C write combiner and retire them to
   dense C memory. The nominal destination tile is completion state, not A
   payload storage.

The physical SPD is globally configured at 4K words per tile ID, but this
specialized instruction does not page four independent 4K A/B chunks through
it. It preserves the 16K reorder opportunity by retaining 16K descriptors while
bounding live payload. This is why a single-digit overhead is plausible; it is
not comparable to OS virtual-memory paging with page-table walks and arbitrary
page faults.

The limitation is equally important: this implementation handles a gather whose
result can retire directly to memory. It has not shown transparent paging for an
arbitrary later MAA instruction that expects the entire destination tile in SPD.

## Professor's subset proposal

Keeping a selected subset of B descriptors in DX100 and spilling other subsets
to LLC is a way to recover a larger reorder window when descriptor capacity is
also limited to 4K. The current design does not need that policy for its present
one-unit configuration because it retains the baseline 16K descriptor capacity.
The proposal becomes relevant if the research target requires shrinking both
payload and Row/Offset metadata, or if synthesis shows that retained descriptor
storage dominates the cost.

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
