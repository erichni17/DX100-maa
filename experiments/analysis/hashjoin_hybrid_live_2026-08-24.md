# HashJoin 4K-physical hybrid live evidence (2026-08-24)

## Status

Accepted as candidate-only small-input correctness/mechanism evidence after an
independent final review found no source or correctness defects. This is not a
performance claim and did not rerun a native arm.

- Evidence source: `a77f77f1fd01090b61b1e579553f9fc8ed61fe13`
- Recorded gem5 SHA-256: `688346958561175ae6e5a839c7bbe482e6115bc9af11ee8160c6fe05af8dd071`
- Guest SHA-256: `9137ca242beb2b5a451ca592021047dfdf6da5f35efc53f34844c7d87de9f299`
- Raw root: `/data1/nier/dx100-runs/2026-08-24-hashjoin-hybrid-small-a77f77f1`
- Geometry: 16K logical, 4K physical, two memory channels, four indirect
  units, 32 initial row-table slices
- Input: R/S sizes 65,536, seeds 12,345/54,321
- Wall timeout/native rerun: none/zero

The exact recorded gem5 binary was not archived before its mutable shared path
was rebuilt. The manifest and raw outputs retain its hash; the successor
`binary_provenance.txt` records this limitation without changing the manifest.

## Application mapping

Both first-pass radix histograms route full 16K windows through scalar-broadcast
SoA/JIT ADD. Four per-thread 16K index slices share one contiguous coherent
arena and one registered region, keeping the final region ID at 31/31. All
ordinary histogram tails and padded scatter work are split into ordered 4K SPD
actions. PRO bucket-chain and PRH histogram probe/collision code remains
unchanged.

## Exact result

| Kernel | Result cardinality | Routed windows | SoA instructions/terminals | Runner simTicks |
|---|---:|---:|---:|---:|
| PRO | 65,536 | 8/8 | 8/8 | 6,533,806,766 |
| PRH | 65,536 | 8/8 | 8/8 | 7,058,331,853 |

For each arm:

- 131,072 selected aliases completed with zero fallback.
- A read and write issue/response ledgers were balanced and nonzero.
- First-pass scatter used 32 ordered 4K actions.
- Shifted second-pass/tail scatter used 256 ordered 4K actions.
- The guest emitted one exact result line and one clean `m5_exit`.

Raw manifest, results, logs, stats, and trace hashes are frozen in
`result_sha256.txt` under the durable root.

## Limits

This small input has no full 16K second-pass histogram window, so the shifted
histogram's SoA/JIT route is statically checked but not live-routed. Full inputs,
performance, and comparison to frozen native tile-sweep timing remain future
work.

