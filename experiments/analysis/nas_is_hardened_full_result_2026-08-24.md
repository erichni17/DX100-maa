# NAS IS full hybrid result (2026-08-24)

## Decision

The full NAS IS scalar-SoA candidate is **terminal-valid correctness
evidence**. It is candidate-only and does not by itself promote performance.

- Root: `/data1/nier/dx100-runs/2026-08-24-is-scalar-soa-full-a44aaa60-r5`.
- Service exit: zero; `terminal.status=PASS`.
- NAS verification: `successfull: passed verification 6`.
- Mechanism terminal: 2,048 generations / 2,048 full 16K windows, no tail
  words, 33,554,432 index words, null predicate, scalar source, zero host-SPD
  reads, and zero staging bytes.
- Stats: 2,048 instructions / terminals, 33,554,432 selected, zero rejected.
- A traffic: 31,020,345 reads/responses and 31,020,345 writes/responses.
- First ROI: `379,831,843,258 simTicks`.
- Geometry: 16K logical, 4K physical SPD, 32 RowTable slices, two memory
  channels.
- No native run and no wall timeout.

The one-shot classifier returns `terminal-valid` with no rejection reasons.
The recovered executable manifest binds the launched binary after the lead
build path was replaced; it explicitly records that simulation state did not
change.
