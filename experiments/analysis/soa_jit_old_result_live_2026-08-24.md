# Bounded SoA/JIT old-result live evidence (2026-08-24)

## Status

Accepted as mechanism/correctness evidence. This is not a performance or
speedup claim and did not rerun a native baseline.

- Source: `37e492c41b9a50e8a26a900eebc4dcbc6a5d91b8`
- gem5 SHA-256: `688346958561175ae6e5a839c7bbe482e6115bc9af11ee8160c6fe05af8dd071`
- Raw root: `/data1/nier/dx100-runs/2026-08-24-old-result-smoke-37e492c4-r4`
- Geometry: 16K logical, 4K physical, two memory channels, four indirect
  units, 32 initial row-table slices
- Wall timeout: none

## Mechanism

For each reordered alias, the indirect unit has the authenticated A cache line
and the alias's original logical ordinal. It copies the A word into a fixed
result-line credit immediately before applying the ordered RMW. Eight retained
64-byte credits publish selected words to coherent result backing with byte
enables. Credits are not reused until their exact `WriteResp`; instruction
completion waits for selection closure, all A writes, all result writes, and
empty bounded state. Rejected ordinals retain their exact sentinel bits.

This preserves duplicate-index order while avoiding a 16K old-result payload
inside the MAA.

## Exact result

- Two generations completed at `687,827,203 simTicks`.
- 25,368 selected logical words matched exact sequential old values.
- 7,400 rejected words retained their exact sentinel bits.
- 11,399 result `WriteReq`s matched 11,399 exact `WriteResp`s.
- Both generations reached the eight-credit high-water mark.
- Guest terminal: `generations=2 logical=16384 errors=0`.

The raw manifest, result, restore log, stats, and trace hashes are frozen in
`result_sha256.txt` under the raw root.

## Hardware and traffic

The simulator reports 1,128 bytes of fixed old-result state per indirect unit:
512 payload bytes plus 616 bytes of fixed metadata. Four units therefore model
4,512 bytes total. For scale only, one 16K FP32 result tile is 65,536 bytes;
this comparison is not a synthesized area estimate.

The current eight-credit design emits many partial lines: 25,368 captures
required 11,399 line writes, about 2.23 useful words per write. This traffic and
the associated 32,872 observed credit stalls are the principal old-result
optimization target.

