# Hybrid tail issue-ready experiment: negative result

Status: **no promotion**. All four fresh roots used exact output hash
`7228541527853630339`, explicit 16K offset/epoch tables, 16K reorder
metadata, 4K physical payload, one MAA, one indirect unit, serialized unlike
treatments, and matched physical-admission digests within each pair.

The accepted attribution remains 5,256,522 ticks after all pages were ready:
4,936,010 STREAM ticks plus 320,512 ALU ticks, with zero producer write
completions in that interval.

| Variant | Activation | control / candidate simTicks | Candidate writes | Result |
|---|---|---:|---:|---|
| reserve32 (`84b989e6`) | none: 0 pending pages, 0 pending words, 0 forwards | 45,367,472 / 44,998,445 | 5,363 | Negative. The apparent 0.813% movement is not causal activation. Ready occurred only at completed=issued=4096; only 77 full-line writes were forwardable and masked/partial writes remained acknowledgement-gated. |
| victim-first reserve32 (`a38b25ac`) | 2 pages, 256 pending words, 0 forwards | 45,336,798 / 46,679,881 | 5,511 | Negative, 2.962% slower. Fragment-first protection increased writes by 166. |
| reserve4 (`325ee47a`) | 1 page, 32 pending words, 0 forwards | 45,367,472 / 46,316,175 | 5,326 | Negative, 2.091% slower. Holding page 0 delayed the sequential consumer. |
| tail4 (`e6b04755`) | none: 0 pending pages, 0 pending words, 0 forwards | 45,294,543 / 45,294,543 | 5,315 | Negative and tick-identical. Tail-only retention did not activate. |

All forwarding scheduled/delivered/copy/HWM counters were zero; overflow and
fallback deferrals were also zero. Therefore none of these roots validates the
one-cycle forwarding datapath dynamically. The two roots with pending pages
prove early release only, and both lose on simTicks.

Hardware accounting remains separate from timing evidence. The treatment-only
packed design lower bound is 4,096 payload bytes plus 1,216 total metadata
bytes: 18.875 raw and 19 rounded bytes per entry, 5,312 bytes total. This does
not claim the current simulator's dynamic map allocation. The mechanism is
one-MAA evidence (one global line-forward event per MAA-object clock), and
mid-treatment checkpoint serialization is not implemented or tested.

Recommendation: do not integrate `b9f29747`, `84b989e6`, or descendants as an
optimization. The next candidate should remove a measured redundant backing
transfer using a bounded page buffer or producer-to-consumer handoff that
preserves page-0 order, with explicit storage, ownership, ordering, and exact
matched evidence before promotion.
