# CG page-fed p16/q16 value retention (2026-08-26)

## Decision

**ACCEPT for bounded CG_NA=256 and CG_NA=1024 evidence. Do not infer a
native, full-CG, or isolated p16-reorder speedup.**

Both same-treatment pairs execute `page_fed_product_soa_jit` from one
checkpoint. The only simulator-knob difference is
`soa_jit_value_cache_enable=false` versus `true`. Both pairs have exact raw
and quantized fingerprints, byte-identical deterministic reductions, terminal
mechanism closure, immutable source/checkpoint/artifact ledgers, reduced value
traffic, and lower `simTicks`. The NA=1024 pair was launched only after the
NA=256 conjunction passed.

There were zero native runs and zero full-CG runs.

## Runner milestone

Commit `fed3640e943d74d7abc6c74ea697d304b6202fa9` adds
`--page-fed-value-cache-pair` to
`experiments/scripts/run_cg_direct4_product_page_fed_q16.py`.

The mode fails closed unless:

- both selector snapshots are exactly
  `token_stream_ld page_fed_product_soa_jit`;
- both terminal mechanism dictionaries are identical;
- normalized resolved configs differ only in
  `soa_jit_value_cache_enable` and run-local redirect paths;
- normalized restore commands differ only by exactly one
  `--maa_soa_jit_value_cache_enable` in cache-on;
- both arms restore the same immutable checkpoint and retain the same source,
  guest, simulator, Ramulator, and compile inputs;
- raw/quantized fingerprints and all 11 deterministic reduction records are
  byte-identical before `simTicks` is read;
- the complete instruction, alias/delivery, value coalescer, A read/write,
  publisher, page-fed response/provenance, no-fallback, and no-epoch-drain
  ledgers close.

The focused runner, full-run import, inherited reduction-order, and inherited
small-application contracts pass 52/52. Source/style hooks passed. The commit
message hook alone was skipped because this checkout lacks the
`MAINTAINERS.yaml` that the hook unconditionally opens.

## Accepted raw roots

### CG_NA=256

Raw root:

`/data1/nier/worktrees/codex-coordination/sessions/hybrid-p16q16-value-retention-20260826-20260826-115636-d87bb966/evidence/page-fed-p16q16-value-cache-na256-r1`

- service: `dx100-cg-page-fed-p16q16-cache-na256-r1.service`;
- wrapper/checkpoint/cache-off/cache-on exits: `0/0/0/0`;
- source: `fed3640e943d74d7abc6c74ea697d304b6202fa9`;
- guest SHA-256:
  `fb5af8e6c5ac38fa810bcf0b599a4e15d86e4cc5f792587cf292899263286b29`;
- checkpoint ledger SHA-256:
  `cac00b102b4d2802a51f72f784633c86a68337d5a4b11b193a89da12a87cfd56`;
- raw-root ledger SHA-256:
  `93a38d5f81353881c5b326a61b5098fb9952d428126cbc189c0a419acabd5727`;
- all 56 raw ledger entries independently revalidate.

Exact fingerprint identity includes
`x_raw=d942be57c8fbc635`, `z_raw=f0b4138d16c12153`,
`x_q5=13d88d9190ab637a`, `x_q6=c9a998d28bb9d093`,
`z_q5=1490d5344f558db4`, and `z_q6=cc5468b8319a4711`.

### CG_NA=1024

Raw root:

`/data1/nier/worktrees/codex-coordination/sessions/hybrid-p16q16-value-retention-20260826-20260826-115636-d87bb966/evidence/page-fed-p16q16-value-cache-na1024-r1`

- service: `dx100-cg-page-fed-p16q16-cache-na1024-r1.service`;
- wrapper/checkpoint/cache-off/cache-on exits: `0/0/0/0`;
- source: `fed3640e943d74d7abc6c74ea697d304b6202fa9`;
- guest SHA-256:
  `8d1ad7a719adb1ffbe08cd399985a4367db7f537a3fdaaaee4810b56e4921ff5`;
- checkpoint ledger SHA-256:
  `851736855f87b91bf10292af433bbc885b57b90e1d8699cfce41df9d8b917297`;
- raw-root ledger SHA-256:
  `ee335fafdceb3ad691724c101966466497edfd8d48872de66566be75dfaa7dde`;
- all 56 raw ledger entries independently revalidate.

Exact fingerprint identity includes
`x_raw=8513a33e8cad9f9e`, `z_raw=59417f9f91294e19`,
`x_q5=6438e193ca03f10a`, `x_q6=9a5b269688cb4313`,
`z_q5=38c02e8ec15b7aa8`, and `z_q6=1caf0b6809305531`.

Both pairs use frozen gem5
`606eb920d2e33d1ad3948ae026057b2b74a12f2f5a94e202165c57dbf15f0427`
and frozen Ramulator
`76ea3a9c7467a5fc0dc04f2b5f083909c03e8b7280c1872046fc78edb2a15753`.

## Same-treatment result

| CG_NA | Cache off `simTicks` | Cache on `simTicks` | Off/on | Latency lower | Value reads off/on | MAA cache-read packets off/on |
|---:|---:|---:|---:|---:|---:|---:|
| 256 | 736,932,521 | 420,140,526 | 1.754014372x | 42.9879% | 163,840 / 10,305 | 189,490 / 35,955 |
| 1024 | 5,298,006,081 | 2,363,254,855 | 2.241825959x | 55.3935% | 1,064,960 / 66,862 | 1,208,099 / 210,001 |

Value reads fall by 93.7103% at NA=256 and 93.7216% at NA=1024.
Total MAA cache-read packets fall by 81.0254% and 82.6172%, respectively.

The retained-line identities close exactly:

- NA=256 cache-on has 10,305 issues/responses/fills/cached responses and
  153,535 internal hits, totaling 163,840 deliveries;
- NA=1024 cache-on has 66,862 issues/responses/fills/cached responses and
  998,098 internal hits, totaling 1,064,960 deliveries.

The cache policy adds no payload, control byte, port, backing array, SPD tile,
or owner entry. It activates retention in the already provisioned fixed
128-line-per-indirect-unit owner pool; the selected 32-line prefix is unchanged
between arms.

## Mechanism and allocation closure

Both arms of each pair independently preserve:

- `p16_reorder_preserved=1` and `q16_reorder_preserved=1`;
- eight tiles/core, four cores, 4,096 words/tile, and exactly 524,288 B of
  physical SPD payload;
- 524,288 B external coherent backing: 262,144 B virtual-p plus 262,144 B
  products;
- zero coherent q-index backing and zero host SPD payload access;
- 10/65 full windows and 40/260 page-fed index admissions;
- 163,840/1,064,960 selected aliases and value deliveries;
- 75/375 A read lines and the same number of A write lines;
- 10,240/66,560 publisher issues, accepts, and WriteResps;
- 50/325 page-fed command responses;
- zero epoch drains and zero bounded-global-merge fallbacks.

The changed value-cache counters are expected mechanism evidence rather than
work changes. All conserved counters above are exact across each pair.

## Existing direct4 cache-on comparison

This comparison was performed only after each corresponding page-fed pair
passed. The existing direct4 raw ledgers were also revalidated before use:

- NA=256:
  `/data1/nier/worktrees/codex-coordination/sessions/hybrid-q16-traffic-optimizer-20260826-20260826-102247-d485e726/evidence/direct4-q16-value-cache-na256-r1`,
  raw ledger
  `5b68910c61d106e715f8796ee127e6edacb38bd7f56c3fdd06dbb7da6843bcf7`;
- NA=1024:
  `/data1/nier/dx100-runs/2026-08-26-cg-direct4-q16-value-cache-na1024-r2`,
  raw ledger
  `5990e8283c90a043f77b99c12ee63f56154da182b61ac6db1f10b5b6a9545ec6`.

| CG_NA | Page-fed p16/q16 cache-on | Direct4/q16 cache-on | Page-fed/direct4 latency | Net direct4 saving |
|---:|---:|---:|---:|---:|
| 256 | 420,140,526 | 184,629,936 | 2.275581821x | 235,510,590 ticks (56.0552%) |
| 1024 | 2,363,254,855 | 837,625,247 | 2.821374909x | 1,525,629,608 ticks (64.5563%) |

### Full-reorder value versus backing cost

The page-fed arm retains the architectural capability of p16 and q16
Row/Offset ordering. Direct4 retains q16 only and changes p to four physical
4K gathers. Therefore the table does **not** show that full reorder has a
negative benefit, nor does it measure a p16 benefit in isolation.

The allocation difference is explicit: page-fed retains 524,288 B external
backing, including 262,144 B virtual-p backing and its materialization traffic;
direct4 retains only the 262,144 B product backing. Both retain the same
524,288 B physical SPD and zero q-index backing.

The cross-treatment latency difference is consequently the **net** of two
inseparable changes in these archived roots: removing virtual-p
backing/materialization and giving up p16 reordering. It must not be reported
as backing cost alone or as reorder benefit alone. The roots also have
different source, guest, and checkpoint identities, so this is descriptive
cross-evidence, not a shared-checkpoint A/B. A separate same-backing p16 toggle
would be required to price reorder value independently.

## Full CG confirmation

Accepted raw root:

`/data1/nier/dx100-runs/2026-08-26-cg-page-fed-p16q16-value-cache-full-r1`

The sole candidate restore exits zero and its
`PASS_NUMERICAL_MECHANISM_CORRECT` gate binds result SHA-256
`55d6d3e5...2ee3` and certified-artifact ledger SHA-256
`016dcf09...a31f`; every ledger entry revalidates. The predeclared numerical
tolerances, all 10,960 p16/q16 windows, 179,568,640 selected aliases/value
deliveries, 57,491 A reads/writes, 11,223,040 publisher WriteResps, and all
page-fed terminals close with zero drains/fallbacks.

The full candidate takes `162,849,334,269 simTicks`. The accepted cache-off
page-fed full control takes `715,387,684,015`, yielding a 4.392942024x
control/candidate ratio and 77.2362% lower latency. Value-line reads fall to
11,266,329; 168,302,256 retained-line hits plus 55 merged waiters close the
179,568,640-delivery identity.

The full allocation remains 524,288 B physical SPD and 524,288 B external
backing, split equally between virtual-p and products. Both p16 and q16 reorder
are preserved. Retention still adds zero owner entries, payload bytes, control
bytes, or ports beyond the already provisioned 128-line owner pool.

For the separate full direct4 cache-on point, `123,968,991,971` ticks is
23.8750% lower than this p16/q16 result. That comparison combines elimination
of 262,144 B virtual-p backing/materialization with loss of p16 reorder; it does
not isolate either effect. Historical native4/native16 endpoints are
`77,075,327,902` / `58,928,150,676` ticks, so p16/q16 cache-on remains
2.1129x / 2.7635x slower. These are orientation observations, not native
reruns, native-speedup claims, or iso-area results.

## Handoff

Retain commit `fed3640e`, both bounded roots, full runner commit `43de2d95`,
and the full root as accepted evidence. This establishes the cache-on p16/q16
design point but does not make it competitive with native4 or authorize a
native/iso-area claim.
