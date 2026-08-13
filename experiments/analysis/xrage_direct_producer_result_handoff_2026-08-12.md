# XRAGE direct producer-result handoff: bounded executable contract

## Conclusion

A direct line path is legal only as a narrow fused pair: a completion-only
16K FP64 virtual-gather producer and its later terminal `FP64 MUL` by exact
scalar `3.0`, dense C-store must rendezvous on one token and generation. Every
other pair falls back to the current transparent/direct-retirement behavior.
The prototype deliberately leaves the live gem5 bridge unchanged: that bridge
still performs a producer backing write then backing read, so it is not direct
payload performance evidence.

## Contract

- Pair admission validates matching nonzero generation and token, exact 16K
  (`64 x 256`) FP64 producer Row/Offset geometry, completion-only token,
  scalar `3.0` bit pattern, and an aligned registered 128 KiB C range.
- The producer preserves its full 16K reorder window. The handoff has a 16K
  exact Row/Offset arrival bitmap and sixteen 64-byte payload credits. It can
  reserve any logical line, so it does not impose an in-order producer window.
- A word arrives only as an actual tagged producer response. A line becomes
  ALU-eligible only after all eight unique FP64 words arrive. There is no
  synthetic backing visibility, producer write acknowledgement, or cache read.
- ALU completion is explicit. Stores issue and acknowledge strictly in
  destination-line order. A payload credit returns only after its exact store
  acknowledgement.

## Bounded cost and prediction

Payload storage is 16 x 64 B = 1024 B. Control includes the 2 KiB 16K arrival
bitmap, 2048 line states, sixteen tags, pair identity, and ALU/store state;
the model exposes its full C++ object footprint so it cannot be counted as
payload-only. This is functional control accounting, not an RTL area claim.

If live-wired, the opportunity is removal of producer backing `WriteReq`/
`WriteResp` and consumer backing `ReadReq`/`ReadResp` for 2048 lines. Actual
speedup is unknown: producer availability, one ALU, ordered stores, store ACK
latency, and finite credits may dominate. A matched A/B must count forwarded
words, rejected pairs, stage waits, and final store acknowledgements before
any performance conclusion.
