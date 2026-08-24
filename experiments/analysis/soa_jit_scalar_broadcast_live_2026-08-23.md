# Scalar-broadcast SoA/JIT RMW live evidence (2026-08-23)

The guarded ordinary `INDIR_RMW_SCALAR` path now reuses the bounded logical-16K
SoA/JIT Row/Offset engine without materializing a 16K value array. One captured
four- or eight-byte scalar supplies every selected alias; indices and optional
predicates remain cache-timed direct-memory spans.

The hybrid-only physical-capacity pair passed exactly:

| Arm | `simTicks` | Instructions | Selected / rejected | Value-memory reads | A reads / writes / WriteResps |
|---|---:|---:|---:|---:|---:|
| physical16 | 135,628,534 | 2 | 25,310 / 7,458 | 0 | 8 / 8 / 8 |
| physical4 | 135,628,534 | 2 | 25,310 / 7,458 | 0 | 8 / 8 / 8 |

Both arms record 25,310 scalar-capture events, 2,048 predicate-line reads,
two terminal generations, exact output checks for FP32 ADD and INT32 MAX, and
zero fatal/error markers. The identical timing and traffic establish that this
instruction has no hidden result-SPD capacity dependency; they are not a
native-DX100 speedup comparison.

Raw root:

`/data1/nier/dx100-runs/2026-08-23-hybrid-rmw-scalar-soa-199d1579-r1`

The runner launched no native baseline and used no wall-clock timeout. This
mechanism is directly applicable to scalar histogram updates such as NAS IS
and HashJoin. Full-application integration and performance remain separate
gates.
