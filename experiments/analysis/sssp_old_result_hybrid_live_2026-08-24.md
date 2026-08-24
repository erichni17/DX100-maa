# SSSP old-result hybrid live evidence (2026-08-24)

## Status

Accepted as candidate-only small-application correctness evidence. The graph is
a deterministic two-level stress input designed to exercise four full logical
windows; this is not a full GAPBS performance result.

- Source: `23e924da13937523af9dce3dba99c2818f924e7e`
- gem5 SHA-256: `688346958561175ae6e5a839c7bbe482e6115bc9af11ee8160c6fe05af8dd071`
- Graph SHA-256: `3fc71246c10bb765d1f67ac15e9fb30561ca70a89a95f8104f85c91fd2954d23`
- Raw root: `/data1/nier/dx100-runs/2026-08-24-sssp-old-result-small-23e924da-r3`
- Geometry: 16K logical, 4K physical, two memory channels, four indirect
  units, 32 initial row-table slices
- Wall timeout/native arms: none/zero

## Application mapping

Four response-bearing 4K index/value publications form each 16K logical MIN
RMW. The old-result mechanism returns each pre-update distance by original
logical ordinal. SSSP reconstructs the same per-physical-page frontier winners
as the legacy path, including duplicate destinations, then retains the legacy
tail path. Host code never reads an SPD payload.

## Exact result

- `10,002,435,519 simTicks` for the candidate arm.
- 4/4 eligible windows routed.
- 16/16 index pages and 16/16 value pages published.
- 65,536 exact old values captured.
- 37,098 result writes matched 37,098 exact `WriteResp`s.
- Shortest-path certificate: 69,633/69,633 vertices reached, distance sum
  135,168, maximum distance 2, zero triangle violations, zero missing
  predecessors, and exact expected hashes.

The raw artifact hashes are frozen in `result_sha256.txt` under the raw root.

## Limits

This input proves the end-to-end SSSP mechanism but not broad graph coverage or
speedup. The result publisher averaged about 1.77 useful words per line write,
so partial-line output traffic remains a likely performance bottleneck before
full-graph promotion.

