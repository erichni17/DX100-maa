# Virtual combiner page-ordered drain

`virtual_page_ordered_combiner_drain` is disabled by default.  When enabled,
the virtual gather retirement path chooses among already-full 64-byte
combiner lines in increasing logical output-page order.  Page zero is the
active 4K-word consumer page; later pages follow in address order.  The page
is derived from `line_vaddr - backingAddr` only after checked membership in
the current `[backingAddr, backingAddr + outputWords * wordBytes)` range.
This prevents subtraction/range wrap from changing priority.

The policy leaves the configured 384 line slots, 4096-word capacity, and
4-way set lookup unchanged.  It adds no payload.  On a line's
partial-to-full transition, it derives the page once and inserts that slot
into an intrusive per-page ready queue.  Each slot needs two 9-bit links and
a 4-bit page id (22 bits; about 1056 bytes at 384 slots), while the 16-page
control block has head/tail slot indices.  Retirement uses a 16-input
ready-head priority encoder and one selected slot index—there is no 384-slot
priority scan, CAM, or extra tag comparison.  The existing 4-way lookup path
is neither widened nor replicated.

`IND_VirtPageOrderedDrainSelections` counts full lines retired through this
arbiter; `IND_VirtPageOrderedDrainDeferrals` counts selections for which a
later logical page was ready but deferred.  Partial-line final flushing
retains the established slot order.
