# SoA/JIT ordered epoch pressure live evidence (2026-08-24)

## Status

Accepted as live multi-epoch mechanism/correctness evidence. This successor
gate specifically tests the Row/Offset pressure path that the earlier
single-epoch old-result smoke did not exercise. It is not a speedup claim.

- Source: `a44aaa607a45239860d5203b1fe1f456d1e1eec4`
- gem5 SHA-256: `2d02fa40568d3ed374258d717f15cad3afeca62343fc1ccaa1640215a8586152`
- Guest SHA-256: `f52ebf093126bfafb515a04eb21e42dfac82ac3c4c02e6aa1ade792f2c8a2d3e`
- Raw root: `/data1/nier/dx100-runs/2026-08-24-soa-epoch-pressure-a44aaa60-r1`
- Geometry: 16K logical, 4K physical, two memory channels, four indirect
  units, 32 initial row-table slices
- Native arms/wall timeout: zero/none

## Pressure mechanism

The guest cycles over 128 targets separated by 256 KiB, preserving the mapped
bank/slice while advancing DRAM rows. One row-table slice therefore sees more
than its 64 row entries. On pressure, the MAA latches the exact source ordinal
and predicate decision without committing either count, drains every admitted
Offset alias and A/result `WriteResp`, verifies empty Row/Offset state, then
resumes the same source cursor under the same instruction generation. Only the
final epoch closes old-result selection.

## Exact result

- Two 16K instructions completed at `7,013,570,975 simTicks`.
- 510 non-final epoch drains completed, 255 per instruction.
- 32,768 selected words; zero rejected words.
- Predicate hits and predicate uses were both exactly 32,768.
- 32,768 exact old values matched the sequential two-generation oracle.
- 2,048 result writes matched 2,048 exact `WriteResp`s.
- Fixed result hash: `16970917775049394563`; guest errors: zero.

Trace boundaries confirm transactional retry behavior. The first pressure
occurs at cursor 64 with only ordinals 0-63 counted; refill resumes ordinal 64.
The last non-final drain occurs at cursor 16,320, followed by the final 64
ordinals and terminal selection closure.

Raw manifest, result, restore, stats, and trace hashes are frozen in
`result_sha256.txt` under the raw root.

