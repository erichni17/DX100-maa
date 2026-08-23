# CG context-capacity result - 2026-08-23

## Decision

Reject context64 as a CG optimization. It is functionally exact but has no
performance or mechanism effect relative to context8 on the current shortened
NAS CG logical16 RMW gate.

## Exact result

Evidence root:
`/data1/nier/dx100-runs/2026-08-23-cg-context32-64-453bcfcb-r3`

The historical experiment name says context32/context64, but the resolved
control configuration is the simulator default of eight active contexts; the
treatment resolves to 64. The matrix itself is authoritative.

| arm | replica 1 `simTicks` | replica 2 `simTicks` | speedup |
|---|---:|---:|---:|
| context8 control | 2,978,885,165 | 2,978,885,165 | baseline |
| context64 treatment | 2,978,885,165 | 2,978,885,165 | 1.000000000x |

Every row has the same exact fingerprint and terminal hash. Each executes
212,992 selected values, 13 terminal instructions, 212,992 balanced value
issues/responses/fills, and 75 balanced A reads/writes/responses. Pre-A is off
in both arms. The normalized resolved configuration hash is identical after
canonicalizing only the declared context delta and run-local redirected
filesystem paths.

The gate has one clean checkpoint, four zero-status restores, two deterministic
replicas per arm, and `gate.complete`. Simulator SHA-256 is
`e84292a2ee03c08af4094b48b4dbe31a72ae4b7e68781f41a3cca5e6f043b5a0`.

## Harness closure

Commits `9e15186c` and `453bcfcb` repair treatment-name and config-identity
normalization. Commit `404f73b9` fixes the decision writer's literal `\\n`
formatting. The original matrix and gate remain valid; only its human-readable
decision line formatting was affected.

## Interpretation

CG never records a context-capacity stall at the tested workload size, so
activating more contexts cannot help. Do not carry context64 into a general
hybrid configuration based only on GZP. CG optimization should instead target
its page-local producer/RMW handoff or a workload size that exercises more
concurrent A lines, with a separately matched gate.
