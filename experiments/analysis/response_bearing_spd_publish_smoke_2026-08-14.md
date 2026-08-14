# Response-bearing SPD publisher smoke (2026-08-14)

This is correctness evidence only. It makes no speedup claim. The first timed
path is deliberately scoped to FP32, GZP logical-16K geometry, logical page 0,
and one physical 4096-element publication.

The clean source revision under test was
`6a764e3719bc06fb6a13e3451fdcbeae733b4740`. The evidence directory is
`/tmp/dx100-response-bearing-spd-publish-20260814-1250`.

The checkpoint/restore smoke exited normally through `m5_exit` at tick
2436475258, with `simTicks=57098086` and no fatal markers. The CPU observed the
exact expected FP32 bit hash `16924436845436167371`. MAA reloaded the coherent
backing through the cache path and compared all 4096 words bit-for-bit as
UINT32; its reduced equality result was one with zero reported errors.

Publisher evidence:

- issues: 256
- cache-path accepts: 256
- retained-packet retries: 4
- unique WriteResps: 256
- credit high-water mark: 8
- credit-stall observations: 248
- terminal completions: 1

The source is captured one 64-byte line at a time into one of eight fixed
credits. The source tile nevertheless remains leased by IF and is not reusable
until the final unique WriteResp, publisher reset, and terminal completion.
This conservative rule makes source reuse independent of which individual
line payloads have already been acknowledged.

The production X86 binary SHA-256 was
`d2d9f99b2ac5c3f89f4f63d0235c0ebd1f23d9b3dd52eb6f21680292489cb89b`.
The smoke source, test binary, `se.py`, and Ramulator configuration hashes were,
respectively:

- `23df1dce61bf72c3acd5d97d29f8695e5a75a90569e0a612a16c6a45bba0549f`
- `59689f392162bbb036c0e46f87bd7a15ff25d1844d01635d82919961a780d501`
- `aacc6e624b7ab0e7b032d5cb913974fa790efdca84598bf468c11f14b9575d0f`
- `aca6e27b58afdfbfd80b7ec41c3f0e7e574a1fc7355a3512981ead823f68731b`

The bounded model still unit-tests FP64 geometry, but this timed evidence does
not promote a live FP64/GZP contract.

## Lead-branch reproduction

After integration, source commit `6fd9c3efd5eb6181335e1c288eb5116cf3d2be51`
was rebuilt and rerun without a checkpoint or restore wall-clock timeout. The
evidence is at
`/data1/nier/dx100-runs/2026-08-14-response-bearing-spd-publish-6fd9c3ef-r1`.
It passed the same exact CPU/MAA bit hash and protocol closure at
`simTicks=57,322,507`: 256 issues, 256 accepts, four retained-packet retries,
256 unique `WriteResp`s, credit high-water eight, 248 credit stalls, and one
terminal completion. The rebuilt production gem5 SHA-256 is
`9bf0d7cfca70768b0e7bf4b7fa7bda7700601f4ce1a2c71d61594b9baa5a06b4`.

This reproduction remains correctness evidence only. Its 0.393% tick
difference from the worker smoke crosses binaries and checkpoints, so it is
not a performance regression or improvement. Any optimization claim must use
matched arms from the same binary and frozen checkpoint.
