# GZZ corrected direct-index storage ledger

The GZZ storage comparison was regenerated from the three frozen matched-
consumer `r6` configurations using the current reporter and the applicable
mechanisms: `native`, `native`, and `direct-index`.

| Arm | Comparable configured lower bound |
|---|---:|
| native16 | 3,185,920 B |
| strict logical16/physical4 hybrid | 2,014,760 B |
| native4 | 1,401,088 B |

The hybrid lower bound is 36.760496% below native16 and 43.799676% above
native4. These replace the withdrawn generic-virtual values. They are packed
configuration lower bounds, not synthesized area, power, or Fmax results.

The hybrid ledger charges one 4,096-word shared allocator per indirect unit,
2,048 destination line tags, 128 response slots, 16 x 12-bit payload
references and 16 x 15-bit fanout counters per response slot, the fixed spill
bitmap, direct-index feeder state, and retirement metadata. Current source
commit `6602846c` disables the unpacked C++ response-line store in shared mode;
the reported excluded shadow is therefore zero bytes.

Evidence root:
`/data1/nier/dx100-runs/2026-09-01-gzz-storage-ledger-6602846c-r2`

- `summary.json` SHA-256:
  `199e0d54742d302b7b28ef04ef3a817833149a8449f673af51830052c0271615`
- complete artifact ledger SHA-256:
  `f9fa9b37e41cd3f575f246ec47f6200869f32e33b8625669c3fc542e6d77019c`

The frozen `r6` timing result predates the reference-backed response-payload
implementation. Its timing remains historical authority for that binary; a
current-source candidate replay is required before combining the corrected
storage ledger with a performance claim.
