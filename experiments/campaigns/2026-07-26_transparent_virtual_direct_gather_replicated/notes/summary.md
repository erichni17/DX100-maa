# Replicated Direct-Gather Cost Knee

The independent `b23k` and `b32k` reruns exactly reproduced their first-run
`simTicks`, `simInsts`, MAA cycles, write counts, coalescing counts, and all four
reported high-water marks. Both reruns produced `errors=0`, one ROI, normal
`m5_exit`, and zero checkpoint and restore exit codes. None of 96 numeric
`vmstat` samples recorded swap-in or swap-out.

The replicated points remain 4,028,936 ticks for `b23k` and 3,990,750 ticks for
`b32k`. Relative to the 3,658,970-tick native arm in the predecessor campaign,
their overheads remain 10.111206% and 9.067579%. Both coalesce the 4096 gathered
words into 255 full-line writes and two partial writes.

This establishes deterministic simulator evidence for the observed cost knee.
It does not establish application performance, physical area, or a true
4K-physical/16K-logical tile implementation.
