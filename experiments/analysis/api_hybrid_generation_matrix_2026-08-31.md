# API hybrid generation matrix (2026-08-31)

## Decision

Accept the sealed four-arm, feeder-64 API micro matrix at:

`/data1/nier/worktrees/codex-coordination/sessions/api-hybrid-generation-matrix-20260831-20260831-104013-8cf9d1cc/evidence/api-hybrid-generation-matrix-r3`

The added `original_hybrid64` arm is a genuinely distinct strict-off
transparent hybrid. The existing `hybrid64` label remains attached to the
accepted strict two-pass arm; no arm was relabeled. Exactly one new restore
was launched for the accepted matrix. Existing native and strict observations
were independently revalidated and not rerun.

## Semantic audit

Independent read-only validation of both frozen authorities passes:

- equal-work r4: `ACCEPT_ALL_FOUR_ARMS`;
- feeder-matched successor: `ACCEPT_ALL_SIX_ARMS`.

Both `hybrid1` and `hybrid64` are strict two-pass, not historical original
hybrids. Their frozen commands contain `--maa_virtual_strict_two_phase`, their
resolved configurations say `virtual_strict_two_phase=true`, and each has one
strict begin, admission-close, and terminal timing signature. The terminal
signatures prove 16,384 B/index words and descriptors, zero A issues at
admission close, A issue after final B/Row admission, exact-once B fetch,
four ready pages, coherent ACK closure, `order_ok=1`, and `terminal=1`.

At simulator source commit `6c180e391e738dfd83376bd88d68a2fcaf48b3cc`,
the strict option is explicitly default-off and applies only to virtual
direct-index loads outside SoA-JIT RMW. The strict path retains the entire
Row/Offset population and fences A issue until admission closes. The ordinary
strict-off path instead permits a bounded pressure drain and a later
generation. Thus the modes are semantically distinct.

The historical `transparent_4k` evidence is terminal, exact, and predates the
strict option. It establishes the original label/treatment (`transparent
4096`) but is not performance-comparable because its binary, checkpoint, and
hardware differ. The new arm supplies the missing same-guest, same-gem5, and
same-checkpoint observation.

## Frozen identity and bounded geometry

- Runner source commit: `c1b41488ed2301cc20798756aa2bd67178843e12`.
- Runner SHA-256:
  `b8bda9cd22ece677745520d32ff7d0e9b9d4a3ad571ac33a64770309e6d79ac7`.
- Simulator source commit: `6c180e391e738dfd83376bd88d68a2fcaf48b3cc`.
- gem5 SHA-256:
  `2a672ecaef6cd6a273004312d80fdad4446ae880f7b46b41458d0f4e59d37009`.
- Guest SHA-256:
  `78099e9440f375c3c6cba04c31d3a376441730c40b88d769b34c775ddc13e12e`.
- Checkpoint identity:
  `e1858287768fd4926f8288d759de41611e2ac090bee4529ad9822eef0da2cbd7`.
- Ramulator SHA-256:
  `76ea3a9c7467a5fc0dc04f2b5f083909c03e8b7280c1872046fc78edb2a15753`.

All four selected arms use the same two memory channels and a 64-line index
feeder. `native16_f64` retains logical16/physical16 geometry; the other arms
use logical16/physical4. `native4_f64` executes four exact 4K operations in
the T16K aperture. `hybrid64` uses the strict 16,384-line RowTable capacity.
`original_hybrid64` uses 8,192 RowTable line slots, capacity-equivalent to the
historical one-channel geometry while retaining the matrix's two channels.
The other combiner, response, write-credit, masked-write, Offset, replay,
partition, spool, merge, issue-order, and ACK knobs remain frozen and bounded.

The strict and original performance comparison is therefore an arm-level
comparison, not an isolated strict-flag A/B: the smaller RowTable capacity is
what positively activates the ordinary generation/drain mechanism.

## Exact result and positive mechanism evidence

Every arm terminates with one exact output hash `7228541527853630339`, clean
guards, one ROI close, and one `m5_exit`. Every arm consumes exactly 16,384
index words. The hybrid arms retain one indirect operation and four scalar and
stream-write operations.

| Arm | Strict | `simTicks` | Index HWM | Backing writes | Strict ops | Pressure / drains |
|---|---:|---:|---:|---:|---:|---:|
| `native16_f64` | no | 48,487,143 | 864 | 0 / 0 | 0 | 0 / 0 |
| `native4_f64` | no | 77,011,459 | 3,056 | 0 / 0 | 0 | 0 / 0 |
| `original_hybrid64` | no | 61,502,309 | 1,024 | 6,720 / 6,720 | 0 | 122,435 / 1 |
| `hybrid64` | yes | 57,330,645 | 896 | 8,698 / 8,698 | 1 | 0 / 0 |

The new arm's positive signature is not inferred from an absent flag:

- one `fill_drain` occurs at logical cursor 10,904;
- all 16,384 descriptors are eventually inserted despite 122,435 rejected
  RowTable insertion attempts under pressure;
- A issue begins at tick 3,447,099,987, before final B response at
  3,482,298,402 and final Row insertion at 3,486,050,646;
- B work remains exact at 1,025 lines / 16,384 words;
- 11,257 A lines and 720,448 A bytes close exactly, a 1.182085x source-line
  amplification versus strict's 9,523 lines caused by the bounded generations;
- semantic backing work remains exactly 131,072 bytes, with 6,720 issues and
  6,720 completions;
- strict counters and strict trace events remain exactly zero/absent.

This is the required ordinary strict-off activation. It is not the strict arm
with a different label.

## Simulated comparisons

Only first-ROI `simTicks` are used; host time is excluded.

| Candidate vs reference | Candidate latency change | Reference / candidate |
|---|---:|---:|
| `original_hybrid64` vs `native4_f64` | -20.139% | 1.252172x |
| `original_hybrid64` vs `native16_f64` | +26.843% | 0.788379x |
| `hybrid64` vs `original_hybrid64` | -6.783% | 1.072765x |
| `hybrid64` vs `native4_f64` | -25.556% | 1.343286x |
| `hybrid64` vs `native16_f64` | +18.239% | 0.845746x |

Native16 remains fastest. Strict two-pass is faster than both original hybrid
and native4x4 in this one-observation API matrix.

## Fail-closed history and seal

- r1 is rejected. Its 16-slice/64-row organization still had the full 16,384
  line capacity, produced no pressure/drain, and naturally retained strict
  ordering. It cannot establish positive original-hybrid activation.
- r2 is classifier-rejected only. It positively activated the 8,192-line
  mechanism, but the initial classifier incorrectly required strict's exact
  A-line count. The preserved r2 and r3 mechanism traces are byte-identical;
  r3 was freshly rerun from committed classifier `c1b41488` and sealed.

The accepted tree is fully read-only. Its independent validator rehashes the
complete artifact set, revalidates both predecessor trees, reconstructs the
command and read-only selector overlay, checks terminal/process identity,
exact output and semantic work, strict absence, positive ordinary activation,
ordering, geometry, and comparison arithmetic, then requires exact equality
with the sealed result.

- Accepted result SHA-256:
  `d30bacae9b064f0de74cc82cca5d49ed705f83b56a15639dbd2e24c5d9fcc949`.
- Accepted artifact ledger SHA-256:
  `704848a60d2f3f8e8d8a85ba08b4e3350f7a9b834ccfa584b6959418ecda59d2`.
- Accepted matrix SHA-256:
  `758d40b34b798696a45589906faba8f8c6590b01b5cb7bf2a825bc009a140b1e`.
- Accepted gate SHA-256:
  `816f54fd1b620a585126e7051a6f3dd6c280e0b42cd9e222ca630a3b9db44d55`.

## Limitations

This is one deterministic observation per accepted arm in an API
microbenchmark, not variability or full-application evidence. Native4x4 is
not a true T4096 binary/API-aperture result. The strict/original comparison
includes the declared 16,384-line versus 8,192-line RowTable treatment.
Storage bounds are not synthesized area, power, or Fmax evidence.
