# CG direct4/q16 publisher ping-pong (2026-08-26)

## Decision

**REJECT. Do not promote, run full CG, or compare to native.**

The selectable `direct4_product_page_fed_q16_pingpong` treatment is bounded
to the existing eight physical tiles and has a legal source/completion
ownership construction, but the final CG_NA=256 cache-on A/B did not produce
a terminal candidate. It therefore has no exact candidate output, final
publisher ledger, nonzero-overlap proof, or candidate `simTicks`. The required
acceptance conjunction is false.

## Ownership legality established before implementation

The response-bearing publisher retains its IF source reference until the final
WriteResp, so its product source cannot be reused early. Its separate
completion tile becomes ready only in the terminal `finishInstructionCompute`
path, after all credits, retry state, and responses close. The candidate uses
those existing semantics without changing generic MAA code:

- group A is `t4..t7`, with final product in `t7` and publisher completion in
  `t6`;
- group B is `t0..t3`, with final product in `t3` and publisher completion in
  `t2`;
- reuse waits the corresponding `t6` or `t2` completion before preloading the
  next colidx page into that group;
- the groups use disjoint publisher scalar identities (`r4/r5/r2` and
  `r6/r7/r3`), avoiding register-file serialization or early overwrite;
- q-side page-fed Row/Offset generation starts only after both final group
  terminals and uses `t4` as its separate close completion.

The treatment keeps q-side 16K order, `p16_reorder_preserved=0`, eight
tiles/core, four cores, 4,096 words/tile, 524,288 B physical SPD, 262,144 B
product backing, zero virtual-p backing, zero host SPD payload access, and the
existing response/provenance gates.

## Final bounded A/B

Raw root:

`/data1/nier/worktrees/codex-coordination/sessions/hybrid-q16-publisher-pingpong-20260826-20260826-110416-344cfa93/evidence/direct4-q16-publisher-pingpong-na256-r3`

Command:

```text
python3 experiments/scripts/run_cg_direct4_product_page_fed_q16.py RAW_ROOT --cg-na 256 --publisher-pingpong-pair
```

Both arms are cache-on and restore one immutable checkpoint. The only selector
difference is serial `direct4_product_page_fed_q16` versus
`direct4_product_page_fed_q16_pingpong`. There are zero native and zero full-CG
runs.

- Source commit: `27de0429eb7d929851a598c7ac11feb510e92a52`
- Guest SHA-256:
  `1ad3f353e31b4d0d39fea4f755da423311d5f587dd78bc3641577923efa18684`
- Frozen gem5 SHA-256:
  `606eb920d2e33d1ad3948ae026057b2b74a12f2f5a94e202165c57dbf15f0427`
- Frozen Ramulator SHA-256:
  `76ea3a9c7467a5fc0dc04f2b5f083909c03e8b7280c1872046fc78edb2a15753`
- Checkpoint ledger SHA-256:
  `dac6eba8bb16b8b2d84312c1e66eef4936850d61331c8cac330ada9e71e67dd5`
- The checkpoint tree revalidated unchanged after the interrupted pair, and
  normalized resolved configs are identical.

### Serial arm

The serial arm completed with the exact raw and quantized fingerprint, all 11
deterministic reduction records, one ROI terminal, and one m5 exit.

| Metric | Serial cache-on |
|---|---:|
| `simTicks` | 184,712,881 |
| selected aliases / value deliveries | 163,840 / 163,840 |
| A read / write lines | 75 / 75 |
| publisher issues / accepts / WriteResps | 10,240 / 10,240 / 10,240 |
| publisher terminals / retries | 40 / 0 |
| publisher overlap issues | 0 |
| publisher credit-stall observations | 9,920 |
| page-fed command responses | 50 |

Serial restore-log SHA-256 is
`c6d73aaa9847021fa6d1792e4848b6b5d52d862c7868c6d746f42010e9eedb42`;
serial stats SHA-256 is
`8d4abcba6a88cbf8a0ff6db907687454733da356f4a9e15739f3d33e4a3fca0b`.

### Ping-pong arm

The candidate emitted no `ROI End`, fingerprint, treatment terminal, or m5
terminal. A one-time liveness debugger snapshot ultimately delivered gem5's
user-interrupt exit at absolute tick 240,235,140,330 after about ten continuous
CPU-minutes. The candidate's post-restore event loop had already advanced
113,768,704,457 ticks from the common real-simulation entry at
126,466,435,873. Serial completed over the corresponding 297,247,336-tick
span. This incomplete elapsed-tick lower bound is 382.74x the serial span; it
is not reported as candidate `simTicks`.

Candidate restore-log SHA-256 is
`464154383c006664861dafa98194402309bd7429f49c656079505dc0ad82a848`.
Because the arm is nonterminal, no overlap or performance statistic is
accepted from it.

## Rejected precursors

- `.../direct4-q16-publisher-pingpong-na256-r1` stopped after the exact serial
  arm because the parser treated an omitted zero-valued `nozero` retry stat as
  missing schema. The parser was narrowed to decode absence as zero only for
  the known retry/overlap nozero counters.
- `.../direct4-q16-publisher-pingpong-na256-r2` completed serial, then rejected
  ping-pong at tick 126,569,501,140 with the fail-closed page-fed index-owner
  panic. The cause was completion/index aliasing on `t0`; the final r3 source
  separates those roles as described above.

Neither precursor supports a mechanism or performance claim.

## Handoff

Keep commits `2438f9f6`, `0515bdb5`, and `27de0429` as a rejected experimental
milestone. The guest-only schedule proved that tile source/completion ownership
can be expressed without generic MAA changes, but it did not prove a live
serial-vs-ping-pong treatment. Any successor must first explain the r3 polling
liveness failure, then start a fresh bounded shared-checkpoint pair and satisfy
exact output, all response/provenance ledgers, nonzero publisher overlap, and
lower `simTicks` together. Full CG and native remain unauthorized.
