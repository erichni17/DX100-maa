# CG_NA=1024 confirmation

The runner is parameterized for exactly one `CG_NA=1024` control/candidate pair,
but the launch was rejected during the mandatory clean-source preflight because
this worktree contained the uncommitted runner change. No guest, checkpoint, or
simulation was started; therefore there is no NA=1024 performance result to
accept or reject. The guard requires `--confirm-from` and revalidates the
accepted NA=256 successor authority, 43-field schema, immutable ledgers, and
source/gem5 fingerprints before compiling.

The accepted NA=256 authority remains the only attributable bounded point:
control `419398090` simTicks, candidate `396154397` simTicks (1.058673x), with
exact fingerprints/reductions, zero drains/fallbacks/publisher/virtual-p bytes,
and corrected 140-B semantic / 392-B conservative control accounting. No
coefficient-locality or product-transport scaling claim is made for NA=1024.
