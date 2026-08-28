# Fixed-storage strict CG line-combiner sweep (2026-08-27)

## Decision

All ten independent `CG_NA=1024` restores validate. The fastest measured arm
is **2-way, 2-bank, fewest-filled victim, 32 credits** at
`2,213,832,098 simTicks` (23,475 fewer than the 4-way RR baseline), with
`355,706` P cache-line writes (2,408 fewer). The timing change is only
0.00106% while changing arbitration and replacement behavior. Retain the
4-way/4-bank round-robin baseline; record 2-way/fewest only as a measured
Pareto point, not a selected optimization.

The requested zero-new-state priority, **4-way/4-bank most-filled
(`victim_policy=2`)**, does reduce P writes to `349,595` (8,519 fewer than
RR), but takes `2,226,080,727 simTicks`—`12,225,154` more (+0.5522%).  The
partial five-window write-count trend therefore survives, while an
end-to-end gain does not.  It is accepted for correctness and rejected as a
performance selection.

No lookahead or Belady metadata was added or used.

## Fixed provenance and hardware boundary

Every arm restored the accepted strict non-fused NA1024 r8 root:

- Root: `/data1/nier/worktrees/codex-coordination/sessions/strict-two-phase-cg-reference-20260827-20260827-182028-096a7ac2/evidence/cg-strict-nonfused-na1024-r8-matched`
- Binary SHA-256: `a78ad432b958b39fe008e496c709a7df4b2cbc4633fda2fad731260b6560148e`
- Guest SHA-256: `20335fcdb7cd89ef7d1ec3a2bc7da88327233bd66cc091b9d23b67af19904349`
- Raw sweep manifest: `/data1/nier/worktrees/codex-coordination/sessions/fixed-storage-combiner-sweep-20260827-20260827-221523-3f1012c6/evidence/na1024-fixed-storage-combiner-r1/report.json`

The configuration gate held 16 cache-line slots, zero extra combiner/resident
word capacity, eight response slots, zero response-word pool, four SPD read
ports, four SPD write ports, and four existing retirement ports.  It also held
the existing logical 16K Row/Offset configuration and did not run native.
Only `ways`, bank mapping, victim policy, and a write-credit bound no larger
than the existing 32-credit reservation varied.  Thus every hardware delta in
the table is relative to `w4/b4/RR/c32`; all protected capacities and external
ports have delta zero.

## Complete-run results

`B`, `Row`, `A`, `Backing`, `Page`, and `Consumer` are the individual strict
phase counters, not additive components of `simTicks`.  `P writes` are trace
records and every one is a 64-byte cache-line write.

| Arm | w/b/v/c delta | simTicks | P writes | B | Row | A | Backing | Page | Consumer |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| baseline RR | 0/0/0/0 | 2,213,855,573 | 358,114 | 4,191,669 | 4,298,831 | 1,041,427 | 1,868,960 | 673,403 | 2,333,426 |
| 1-way/1-bank RR | -3/-3/0/0 | 2,213,833,976 | 357,105 | 4,196,226 | 4,304,678 | 1,043,351 | 1,872,445 | 672,331 | 2,333,426 |
| 2-way/2-bank fewest | -2/-2/+1/0 | 2,213,832,098 | 355,706 | 4,196,835 | 4,304,747 | 1,042,359 | 1,870,414 | 669,643 | 2,333,426 |
| 4-way/4-bank fewest | 0/0/+1/0 | 2,213,846,496 | 354,575 | 4,195,002 | 4,302,343 | 1,043,645 | 1,871,109 | 669,080 | 2,333,426 |
| 4-way/4-bank most-filled | 0/0/+2/0 | 2,226,080,727 | 349,595 | 4,194,391 | 4,302,774 | 1,081,340 | 1,910,001 | 670,652 | 2,333,426 |
| 8-way/2-bank fewest | +4/-2/+1/0 | 2,215,870,667 | 354,067 | 4,196,932 | 4,304,656 | 1,044,158 | 1,871,188 | 669,135 | 2,333,426 |
| 16-way/1-bank most-filled | +12/-3/+2/0 | 2,244,815,655 | 346,076 | 4,200,601 | 4,308,424 | 1,139,476 | 1,969,791 | 672,228 | 2,333,426 |
| fully associative/0-bank fewest | -4/-4/+1/0 | 2,216,508,248 | 353,929 | 4,197,697 | 4,305,657 | 1,044,990 | 1,871,663 | 669,078 | 2,333,426 |
| 4-way/4-bank RR, 16 credits | 0/0/0/-16 | 2,245,807,865 | 358,114 | 4,200,617 | 4,307,481 | 1,126,699 | 1,959,883 | 678,850 | 2,333,426 |
| 4-way/4-bank RR, 8 credits | 0/0/0/-24 | 2,533,031,689 | 358,114 | 4,190,114 | 4,300,421 | 2,027,593 | 2,897,579 | 682,356 | 2,333,426 |

The result is a throughput/write-count tradeoff, so the non-dominated set is
the faster 2-way/fewest arm plus arms with successively fewer writes. None is
selected: the only faster arm improves 0.00106%, and every meaningfully
lower-write arm is slower. No claim is made that a lower-write but slower arm
is superior.

## Acceptance gates

All ten arms have return code zero, one `m5_exit`, final stats, the exact CG
fingerprint and deterministic reductions from r8, and source/guest/binary
provenance above.  Every arm has exactly 65 P timing records, 65 Q timing
records, 65 whole-window ledgers, and 260 product responses.  The strict trace
and stats report zero drains, zero bounded-global fallbacks, and all P writes
at 64 bytes.  The common q16 work remains exact: 1,064,960 admitted/selected
words, 260 page admits, 65 closes, and 325 page-fed command responses.

There were no rejected correctness/provenance arms.  The slower arms are
reported rather than silently discarded; they are simply not selected for the
fastest bounded point.
