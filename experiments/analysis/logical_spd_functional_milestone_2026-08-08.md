# Logical SPD functional milestone (2026-08-08)

This milestone establishes only bounded functional behavior.  Each MAA owns
exactly 32,768 bytes of private FP64 payload: Serial4K exposes one
4096-element in-place slot, while PingPong2K exposes two 2048-element slots.
The packed private semantic metadata lower bound is 1,309 bytes and is
reported separately.  Ordinary visible SPD remains an additive allocation.

The logical mode is explicitly two-valued (`0=Serial4K`,
`1=PingPong2K`) and is independent of the transparent controller's
three-valued mode.  Live arms retain a visible physical tile of 4096 elements;
their logical runtime geometry is respectively 4x4K and 8x2K pages.

`isoarea_timing_claim=0`.  The scalar transform is an untimed host loop, so no
`simTicks`, throughput, overlap, area, or performance claim is valid here.

Remaining blockers, intentionally out of scope: drain/checkpoint integration;
authentication of the actual responding and retrying cache port; indirect
producer generation handoff and reorder survival; and timing-legal ALU/cache
contention.  The live smokes are pre-materialized backing transforms only and
are not evidence for those mechanisms.
