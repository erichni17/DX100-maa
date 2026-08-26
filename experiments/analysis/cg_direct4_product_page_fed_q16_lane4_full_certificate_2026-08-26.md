# Full CG direct4/q16 lane-4 successor certificate (2026-08-26)

## Verdict

The read-only successor classifier returns
`PASS_NUMERICAL_MECHANISM_CORRECT` for the completed cache-on lane-4 raw run:

- immutable raw root:
  `/data1/nier/dx100-runs/2026-08-26-cg-direct4-product-page-fed-q16-lane4-full-r1`;
- accepted cache-on lane-1 control:
  `/data1/nier/dx100-runs/2026-08-26-cg-direct4-product-page-fed-q16-value-cache-full-r2`;
- fresh successor certificate:
  `/data1/nier/dx100-runs/2026-08-26-cg-direct4-product-page-fed-q16-lane4-full-certificate-r2`.

The raw root was not modified and no gem5 process was launched. A concurrently
created `certificate-r1` directory was discovered from a different worktree
and classifier hash; it was preserved untouched. This session therefore used
the fresh `r2` root so its input ledger binds the classifier committed with
this report.

## Terminal-state distinction

The original durable service is terminal, but its service result is expectedly
`failed/exit-code` because the historical runner rejected a valid sparse-row
high-water sum after the simulation had completed:

- unit:
  `dx100-cg-direct4-product-page-fed-q16-lane4-full-r1.service`;
- invocation: `81533b2061ac43df8f331a4945f7c23d`;
- registered main PID/start ticks: `3632390/311246485`;
- registered watcher PID/start ticks: `3632878/311249804`;
- current `MainPID=0`; both registered PIDs and any exact raw-root/guest argv
  process are absent;
- `ExecMainCode=1`, `ExecMainStatus=1`, `Result=exit-code`;
- checkpoint wrapper exit `0`, restore wrapper exit `0`;
- exactly one checkpoint boundary, one ROI close, and one
  `m5_exit instruction encountered` at tick `172839942144`; and
- nonempty final stats/config with no fatal restore text or trace artifact.

Thus the service failure is a post-restore classifier failure, not a gem5,
checkpoint, or guest failure. The raw manifest remains the original
pre-execution `terminal=false` document and the raw root still has no
`result.json`, `gate.complete`, or certified-artifact seal. The successor
certificate does not backfill those files.

## Corrected four-lane gate

The future runner now requires, for `instructions=10960`:

```text
IND_SoaJitActiveApplyLanes == 4 * instructions
3 * instructions < IND_SoaJitApplyLaneHighWater <= 4 * instructions
```

The raw candidate reports `ActiveApplyLanes=43840` and
`ApplyLaneHighWater=43242`, so `32880 < 43242 <= 43840`. The exact active-lane
sum proves the four-lane configuration was selected for every completed
instruction. The strict high-water lower bound proves at least one operation
used four lanes concurrently while allowing operations with sparse rows. New
adversarial tests reject high water at or below `3x`, above `4x`, and any
non-exact active-lane sum.

## Evidence closure

The classifier pins and verifies 82 input identities, including the raw
manifest/log/stats/config, equal before/after source commit and clean status,
32 immutable artifact entries, 13 immutable checkpoint entries, the tolerant
numerical authority, the bounded lane-selection authority, and the lane-1
certificate plus its 13 certified artifacts.

The candidate closes exactly:

- 10,960 full/direct4/page-fed operations and terminal completions, split as
  8,768 q windows plus 2,192 residual windows;
- 57,491 A read issues/responses and write issues/responses;
- 179,568,640 selected/applied/delivered/admitted/SPD-read/row-written words;
- value issue/response/fill/cached-response count 11,266,316, retained hits
  168,302,262, and merged waiters 62, satisfying
  `issues + hits + merged = deliveries` exactly;
- 43,840 page admits, 10,960 closes, and 54,800 command responses;
- 11,223,040 publisher issues/accepts/write responses and 43,840 terminals;
- zero predicate rejects, value/lookahead/context stalls, epoch drains,
  bounded-global-merge fallbacks, coherent q-index traffic, virtual-p backing,
  and host payload access; and
- 524,288 B physical SPD storage, 262,144 B external product backing, q16
  reorder preserved, and p16 reorder deliberately not preserved.

All six scalar deltas remain within the predeclared tolerant full-CG authority:
`x_sum=9.65234e-12`, `x_norm_sq=3.67262e-11`,
`z_sum=9.45265e-11`, `z_norm_sq=1.71718e-10`,
`rnorm=2.33105e-4`, and `zeta=5.16761e-16`. This is not raw or quantized
fingerprint equality and is not official NAS verification.

The resolved candidate config selects value retention, 32 active owners, and
four apply lanes exactly once. The fixed simulator accounting remains four
lane owners per indirect unit, 32 B per owner, 144 B per four-lane pool per
unit, and 576 B per MAA. Relative to lane 1, the selected setting adds zero
payload bytes, control bytes, ports, or installed lane-pool bytes. This is
simulator state-layout accounting, not an area or synthesis result.

## Lane-1 comparison and claim boundary

The classifier opens and verifies the accepted lane-1 gate, result, manifest,
certified ledger, raw stats, and resolved one-lane config only after the lane-4
candidate passes its independent terminal, numerical, mechanism, provenance,
storage, and corrected lane gates. It then computes:

```text
lane1 / lane4 = 123968991971 / 111116739967
              = 1.1156644085114171409355310968524861888148630371166
lane4 tick reduction = 12852252004 / 123968991971
                     = 0.10367311857312286961743274656057177306286866814907
```

This is one simulated first-ROI observation per lane setting: a `1.115664409x`
lane-1/lane-4 ratio, or `10.3673%` fewer candidate ticks. It is not a native
speedup, not an iso-area result, and not a full-promotion claim.

## Certificate seals

- `manifest.json`:
  `432de99c8abc57b39d525b562d54d6eedf579bdbad6f61352475b7fc3570ca4d`;
- `certificate.json`:
  `49c827e63ed2d9b543d05354b7d1f18afabbf7b2d4bad7d6a9f0b2841e7e901c`;
- `input_sha256.txt`:
  `0f2b1c9c59221a31d5335dc0f7849fe1a0f8d4be00f918ac31be54313eb707c2`;
- `gate.complete`:
  `159bcaff6df8eb671932509409d820238f69b8fc996be11669b58149fa563bcc`.

The gate is the final artifact and records
`raw_root_modified=false` and `gem5_runs_launched=0`.
