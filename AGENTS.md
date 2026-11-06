# Branch and worktree lifecycle

- The integration branch is `dx100-improvements`. Start new work from its current
  GitHub tip unless a task explicitly requires another base.
- Keep temporary agent branches, worktrees, checkpoints, and coordination state
  local by default. Do not publish a new GitHub branch for every worker or
  checkpoint. Use local-only coordination when remote synchronization is not
  required by the task or existing runtime authority.
- When publishing is authorized, reuse one remote branch per coherent deliverable
  with a short name such as `fix/topic` or `work/topic`.
- Review and validate the actual change before integrating it. Selective
  cherry-picks are appropriate for independent fixes; do not merge divergent
  experimental histories wholesale to reduce branch count.
- After integration, remove an authorized remote task branch only after checking
  its current tip and active ownership. Preserve work touched within the last
  week. Removing a remote branch does not authorize removing local branches,
  worktrees, uncommitted edits, or running jobs.
- Broad removal of unmerged branches requires explicit cleanup authorization,
  a self-contained recovery bundle, a branch-to-commit manifest, and a verified
  restore. Age alone does not authorize future deletion.
- Do not change benchmark correctness, scientific evidence, resource, or runtime
  authorities as part of repository housekeeping.

The November 2026 cleanup records and recovery bundles are stored on mbit1 at
`/data1/nier/repo-cleanup-20261105/`. See `RECOVERY.txt` before restoring old refs.
