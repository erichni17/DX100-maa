# Full CG page-fed reclassification review (2026-08-26)

## Findings and decision

1. **ACCEPT full correctness and the performance comparison versus the full
   physical-page predecessor, within the claim boundary below.** The accepted
   correctness claim is numerical and mechanism-specific: the completed
   `CG_NA=150000` page-fed run is finite, passes the custom CG sanity check,
   passes all six predeclared scalar relative tolerances against the frozen
   native16 oracle, and closes every required page-fed/SoA/JIT work and
   response ledger. It is **not** raw-bit exact or q5/q6-quantized exact to
   native16 or the physical-page predecessor.
2. **The accepted run did not execute the official NAS CG verification.** At
   source commit `31c00be859eed7d6fa161b4868201fde0a8359a7`,
   `benchmarks/NAS/cg/cg.cpp` has `// #define DO_VERIFY`, fixes `NITER=1`, and
   emits no `VERIFICATION SUCCESSFUL`/`UNSUCCESSFUL` line. Its
   `CG_FINGERPRINT ... result=PASS` is a project-local finite/norm sanity
   check, not the official algorithm verifier. This review therefore does not
   relabel it as an official NAS verification pass.
3. **The deterministic intervention is causal at 4096 only.** In the untreated
   matched `CG_NA=4096` pair, normalized MAA source order and all mechanism
   closures match while raw x/z fingerprints differ. Fixing work ownership and
   combining thread partials in order `0,1,2,3` makes all eleven reduction
   records and both complete raw/quantized fingerprints byte-identical. That
   directly establishes timing-dependent OpenMP reduction order as the cause
   of the *medium raw* mismatch. It does not directly establish the cause of
   the ordinary full run's q5/q6 mismatch; extending the cause to
   `CG_NA=150000` remains a well-supported inference.
4. **The reportable full observation is `1.144396618x`, or 12.6177% lower
   simulated latency, versus the predecessor.** Page-fed is
   `715,387,684,015 simTicks`; physical-page predecessor is
   `818,687,246,165`. This is one archived observation per configuration, not
   a repeated-sample estimate and not an attribution of the entire difference
   solely to coherent-index traffic. Page-fed remains
   `12.139998894x` slower than native16 (`58,928,150,676 simTicks`); there is
   no native speedup claim.
5. **The feature is not native or iso-area.** Page-fed removes the 262,144-byte
   coherent index array/backing and its coherent index publication/read
   traffic, reducing reported external coherent backing from 786,432 to
   524,288 bytes. It retains 4,096-element physical pages, a 16,384-element
   logical Row/Offset operation and no-drain Offset epoch, the 524,288-byte
   physical SPD payload in this full configuration, and coherent product
   backing/publication. No freed-capacity reinvestment or iso-area comparison
   was run.
6. **Two archival weaknesses must remain visible.** Because the original full
   wrapper stopped at its exact-hash guard, the page-fed root has no
   `result.txt`, post-run artifact/source ledgers, or `gate.complete`; the raw
   log/stats hashes below are current audit hashes rather than a run-time final
   seal. Also, the predecessor certificate's current `--validate` fails only
   because it names a mutable external `cg.cpp` path that has since changed.
   The frozen Git object at source commit `5d51743bfca566c486c6786cf3b18e6d378d805a`
   reconstructs the expected `d254b68d...` source hash, and every other
   certificate-ledger entry revalidates. These are provenance defects, not
   observed scientific-counter failures.

The original wrapper outcome at `e6373c9f3e7bb20fc2ef912ca78cd6b56db35e78`
must remain historically `REJECTED`: its declared conjunction included exact
q5/q6 hashes and a too-strict one-physical-read-issue-per-logical-value guard,
so it correctly emitted no gate. This review is a successor classification; it
does not rewrite the wrapper result.

## Exact acceptance boundary

This review accepts the full result under all of the following existing,
non-retrofitted conditions:

- terminal gem5 completion, zero stored checkpoint/restore return codes,
  nonempty final stats, a single ROI end and `m5_exit`, and no fatal pattern;
- frozen binary, Ramulator, full matrix header, guest, selector, source/config,
  checkpoint, native16 log/stats, and predecessor-certificate identities;
- finite x/z, project-local fingerprint `result=PASS`, and the manifest's
  predeclared relative bounds of `1e-8` for x/z sum and norm-square, `1e-3`
  for rnorm, and `1e-10` for zeta;
- exact workload/mechanism closure, including logical value delivery (which
  permits legal cache hits and merged waiters rather than requiring one
  physical issue per selected value); and
- independent causal evidence that architecture-dependent timing can legally
  change the benchmark's unordered OpenMP reductions even when MAA source
  semantics match.

Thus the minimal honest correctness wording is:

> The archived full page-fed CG configuration is correctness-valid under the
> predeclared numerical-tolerance and exact mechanism-closure criterion. It is
> neither FP-bit/quantized exact to native16 nor officially NAS-verified.

The minimal honest performance wording is:

> In the archived full configurations, page-fed takes 715,387,684,015
> simTicks versus 818,687,246,165 for its physical-page predecessor: a
> 1.144396618x predecessor/candidate ratio, or 12.6177% lower simulated
> latency. It remains 12.139998894x slower than native16. This is not an
> iso-area result or a native speedup.

The deterministic4096 result is not required to prove that the full mismatch
has the same cause. It is used to reject raw/q-hash identity as a universal
cross-timing correctness oracle. Full acceptance instead rests on the
predeclared numerical criterion plus full mechanism closure. A future full
deterministic matched pair would be required before saying that reduction
order *caused* the full q5/q6 mismatch; absent that experiment, that causal
sentence is out of scope.

## Raw full-run revalidation

Candidate root:
`/data1/nier/dx100-runs/2026-08-25-cg-page-fed-application-full-31c00be8-r2`.
Contemporaneous report:
`experiments/analysis/cg_page_fed_application_full_2026-08-25.md` at
`e6373c9f3e7bb20fc2ef912ca78cd6b56db35e78`.

### Terminal, process, and artifact evidence

- `manifest.txt` identifies source
  `31c00be859eed7d6fa161b4868201fde0a8359a7`, gem5
  `606eb920d2e33d1ad3948ae026057b2b74a12f2f5a94e202165c57dbf15f0427`,
  Ramulator
  `76ea3a9c7467a5fc0dc04f2b5f083909c03e8b7280c1872046fc78edb2a15753`,
  guest
  `a815fcf93bd6747535e8cf3418867e20cf2c48728622ba6406f58f2169ca1750`,
  and frozen input header
  `f2b18716e4a2356c597c95ee3583549def72700f2cb3294b0fcaacca46dbe131`
  (992,830,458 bytes).
- The complete pre-run 14-entry artifact ledger revalidated, as did all 13
  checkpoint files. The checkpoint-ledger file SHA-256 is
  `92714890250f89b4b52b1e4a24752b0370643699a86f476640ed456d79c49226`.
  `checkpoint.exit` and `run/restore.exit` both contain `0`.
- The logs identify checkpoint PID `3045718` (started 2026-08-25 14:37:11)
  and restore PID `3047115` (started 14:44:22) on
  `mbit1-SYS-F628G2-FTPT`. No gem5 process referencing any audited root was
  live during this review. No durable PID/start-time registry survives, so
  historical process identity is supported by the recorded command/log
  evidence, not independently reattached to `/proc`.
- The restore log has exactly one `ROI End!!!`, one passing fingerprint, one
  passing treatment terminal, and one
  `Exiting @ tick 777094962630 because m5_exit instruction encountered`.
  `run/stats.txt` is 3,216,023 bytes and its first statistics window contains
  `simTicks=715387684015`; the later post-ROI window is not used for the
  comparison.
- Current audit hashes are: manifest
  `211b6f9b1d4f13eb3343d9823599a68a16d256339349f0c86ced789d7e573e07`,
  restore log
  `b532bad66d25906105935f2bae3fc6048d3c86279a33b3a92e08d14c775b6a72`,
  and stats
  `3b0654de30ea2a1024373d2cf23f98f84b01d96abcf7d6906ea82a4762351c23`.

### Exactness and numerical evidence

The raw candidate fingerprint is
`x_raw=3b79f7d75af051aa`, `z_raw=e3ac5fd15c60a4e0`. Its quantized hashes are
`88c0975669c7062d`, `a1c461b83b95f98f`, `1458f2551dfa99c6`, and
`9fd922f4ccdc69c9` for x_q5/x_q6/z_q5/z_q6. Native16 reports
`bd71373530efa77d`, `9a25df4701c4afa9`, `973558f7c958b798`, and
`5c3a7792ee8d00f3`; therefore raw and all four quantized exactness tests fail.

The independently recomputed candidate/native16 relative deltas all meet the
manifest's predeclared bounds:

| Field | Relative delta | Bound |
| --- | ---: | ---: |
| `x_sum` | `2.2924e-11` | `1e-8` |
| `x_norm_sq` | `6.4181e-11` | `1e-8` |
| `z_sum` | `1.1530e-10` | `1e-8` |
| `z_norm_sq` | `2.1246e-10` | `1e-8` |
| `rnorm` | `2.2664e-4` | `1e-3` |
| `zeta` | `5.1676e-16` | `1e-10` |

Both vectors contain zero nonfinite elements; x norm-square is within the
custom sanity bound. These aggregates do not prove elementwise equality and
must not be presented as such.

### Full mechanism closure

The first stats window and terminal line independently close as follows:

- 10,960 instructions, page-fed operations, and terminal completions;
  43,840 admits, 10,960 closes, and 54,800 admit/close command responses;
- 179,568,640 selected aliases, logical deliveries, admitted index words,
  physical-SPD index reads, and Row/Offset writes; zero predicate rejections;
- 179,568,384 value issues/responses/fills, plus 215 ready-cache hits and 41
  merged waiters, exactly totaling 179,568,640 logical deliveries;
- 57,491 A reads/responses and 57,491 A writes/responses;
- zero coherent index read/write lines, zero index publication pages, zero
  epoch drains, zero bounded-global-merge fallbacks, and zero derived open
  contexts;
- 175,360 state-byte observations, exactly 16 bytes for each of 10,960
  completed operations; and
- 43,840 product pages with 11,223,040 publisher
  issues/accepts/write-responses and 43,840 publisher terminals.

The 256 fewer physical value issues are completely accounted for by the 215
cache hits and 41 merged waiters. The original one-issue-per-selected-value
guard was therefore not a valid delivery invariant; the exact logical
delivery/alias equation is the appropriate guard.

## Predecessor and native16 evidence

Physical-page predecessor root:
`/data1/nier/dx100-runs/2026-08-24-cg-page-product-full-precomputed-5d51743b-r2`.
Frozen native16 root:
`/data1/nier/dx100-runs/2026-08-11-cg-bounded-789cc703-full-v8/native16`.
The correctness reports are
`experiments/analysis/cg_full_native16_correctness_2026-08-25.md` and
`experiments/analysis/cg_full_page_product_rejection_2026-08-25.md`, with the
corrected native16 classification committed at
`9d92efec52789be774804af5f751da90f622f379`.

- The predecessor certificate
  `NATIVE16_ORACLE_RESULT.json` has SHA-256
  `74ab79575c6c8b76c711a34b936400aaea0bab1927b07b68cf4f8cb2fb5dac54`.
  It records `PASS_NATIVE16_ORACLE`, exact q5/q6 equality to native16,
  `818687246165` candidate ticks, and `58928150676` native16 ticks.
- The certificate ledger revalidated every frozen binary, log, stats, input,
  checkpoint, and result entry except the now-mutated external source path.
  `git show 5d51743bfca566c486c6786cf3b18e6d378d805a:benchmarks/NAS/cg/cg.cpp`
  hashes to the ledger's expected
  `d254b68d34ff306a566f6b54256720314f3d1745b13284593b040e87ed544e60`;
  the similarly external runner reconstructs as the expected
  `0276956040d539feb6b25a6272b7a89afd5b5e4b21b46a9d92250fac89c7cee8`.
- Current predecessor hashes are manifest
  `59bd17ab91537ad2b15ea8a8c45b8f5793eac9ff6fc955d5ff78d636f1ffedb2`,
  restore log
  `e12cad79f4a70bda04790aba3cd5c0fbdb3e86fa785591d281a12474df7e6796`,
  and stats
  `cce10d70ec3ff077fca5a856a70f4c5757ce6d4dc03608bc954d16fdd653c4df`.
- The frozen native16 log and stats hashes are
  `99c08fcbe3b121a61db866af4a4aa926b0eaddf87ad516a944784b496404ca73`
  and `4122577993c17760b86462bb2bfcb1d87b7d33cf2e3f30a003139f586c0cc070`.

The full configurations consume the identical
`f2b18716...` precomputed header, four O3 CPUs, 3.2 GHz CPU/system clocks, the
same cache hierarchy, two memory channels, frozen Ramulator, one MAA/four
indirect units, 8 tiles/core, 16K logical and 4K physical tile geometry, 16K
Offset capacity/epoch, and 32 initial RowTable slices. A config diff contains
the expected workload/path changes and `page_fed_soa_jit=true`; the newer
binary also exposes old-result pressure defaults, but all old-result counters
are zero in this workload. The gem5 hashes differ because the predecessor
predates the page-fed implementation. Consequently the full ratio is a
configuration/predecessor observation, while the same-binary bounded pairs
corroborate direction; it is not a pure single-code-line ablation.

## Causal evidence and residual inference

Static audit commit
`e03ecc1d2c03fb6ac6a05b4a370617bb3dc76035`
(`experiments/reviews/2026-08-25_page_fed_order_static_audit.md`)
finds no source-visible change in logical ordinal or same-destination FP32 ADD
order. It also identifies the direct timing-to-semantics channel: ordinary CG
accumulates per-thread FP32 `d`, `rho`, and residual `sum` partials in OpenMP
critical-entry order, and the outer FP64 pair is likewise schedule-sensitive.

Untreated matched root:
`/data1/nier/dx100-runs/2026-08-25-cg-page-fed-schedule-diagnosis-4096-r1`,
report/runner commit `053853c824e0658e4a1a92af3360750f93eab493`,
raw-ledger-file SHA-256
`fc068de27495e7fe830c1033e06d542b1a08995c130929a83608dbd49c5585c0`.
All raw-ledger entries revalidated. Physical and page-fed share checkpoint
`b8028a25159cb4c20984c2ea7bd1a23c53a521a1cf893f77954f390b48d0a0f5`
and guest
`c3b8a4a02bfe887f24112daec865a565ad17b209489186e8b0887d981ee3b568`;
normalized source order, RowTable admission, aliases, A/value closure,
quantized fingerprints, and zero drains match, while issue timing and raw
hashes differ:

- physical: `x_raw=1d9819aeded94804`, `z_raw=1bc2927ed159875d`;
- page-fed: `x_raw=225873f272124c14`, `z_raw=36e3b0c8d5f3c391`.

Deterministic roots:

- `/data1/nier/dx100-runs/2026-08-26-cg-reduction-order-na1024-r2`,
  raw-ledger-file SHA-256
  `7ae1cafea19c8e9d17e2a17dd2896e6141bdf89e992e65d3c359eaba59ab1e9e`;
- `/data1/nier/dx100-runs/2026-08-26-cg-reduction-order-na4096-r1`,
  raw-ledger-file SHA-256
  `0d86f3773205f42e9fb01157cef36daf26ce8f234d9d16f2de0ab3de3e262c98`.

All 56 entries in each root revalidated. The accepted 4096 result was produced
from source `51ec728d56932646f6be897753b4480f768bdb6d`, checkpoint-ledger hash
`56112cc8baf146909cd5311b7349ae905c38d051284e83044996bc3a5a95a940`,
guest `114bf93bba9677a1b9b9b4ff3f9ff135ecf3e71720a967f036eb98077f0d4ffc`,
frozen gem5
`606eb920d2e33d1ad3948ae026057b2b74a12f2f5a94e202165c57dbf15f0427`,
and frozen Ramulator
`76ea3a9c7467a5fc0dc04f2b5f083909c03e8b7280c1872046fc78edb2a15753`;
the terminal result and report are through
`286125a5613906c3c9368a18fb94f95845770f71`. Both arms have identical eleven
reduction records and exact full fingerprints
`x_raw=225873f272124c14`, `z_raw=36e3b0c8d5f3c391`, which match the untreated
page-fed arm; the untreated physical arm is the one changed by deterministic
combination.

This intervention rules in reduction-order causality at 4096 and rules out a
requirement for cross-timing bitwise equality. Extrapolating it to full is
plausible because the same source reductions and timing channel operate there,
the full numerical deltas are small and within predeclared bounds, and the
full mechanism ledger is exact. It is still an inference: there is no full
reduction record, full vector dump, or full deterministic paired execution.

## Performance arithmetic and claim scope

Using first-ROI `simTicks` only:

| Configuration | `simTicks` | Relation |
| --- | ---: | --- |
| native16 | 58,928,150,676 | frozen reference; different 16K physical geometry |
| full physical-page predecessor | 818,687,246,165 | accepted native16-correct predecessor |
| full page-fed candidate | 715,387,684,015 | accepted numerical/mechanism-correct candidate |

The exact arithmetic is:

- `818687246165 / 715387684015 = 1.144396618027...`;
- `1 - 715387684015 / 818687246165 = 0.126177075109...`, hence
  12.6177% lower latency; and
- `715387684015 / 58928150676 = 12.139998893710...`, hence page-fed is
  12.139998894x slower than native16.

The bounded same-binary evidence is directionally consistent: untreated 4096
is `29,867,173,640` physical versus `25,058,955,593` page-fed; deterministic
4096 is `29,895,584,337` versus `25,103,505,822`. Those are diagnostic medium
results, not substitutes for the full numbers.

## Reproduction commands used by this review

No gem5 or native process was launched and no architecture source was edited.
The review used read-only checks equivalent to:

```text
sha256sum -c input/artifact_sha256.before
(cd checkpoint && sha256sum -c ../input/checkpoint.files.sha256.before)
(cd SCHEDULE_ROOT && sha256sum -c raw_root.sha256)
(cd DETERMINISTIC_ROOT && sha256sum -c raw_root.sha256)
python3 experiments/scripts/classify_cg_page_product_native16.py PREDECESSOR --validate
```

The last command currently reports the mutable-external-source mismatch
described above; direct ledger checking plus reconstruction from Git verifies
all frozen evidence and both expected external source hashes. No new verifier
was needed because the existing ledgers and simple exact arithmetic reproduce
the classification.
