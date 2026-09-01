# UMT PKI4 Gate-B live implementation v23 / audited review bundle v24

Status: implementation and offline adversarial testing only. This commit did
not run a build, systemd unit, gem5, opcode 11, or RTL.

The v23 freezer consumes the untouched, independently reviewed v22 dry plan.
It cannot substitute commands or arms: the only accepted commands are the
plan's D32/G32 and D64/G31 systemd argv arrays, with distinct unit names and
arm roots and a maximum concurrency of two. Freeze requires the exact future
v21 terminal build proof, successful output from its pinned validator, and a
separate independent v23 review that binds both exact command hashes and
explicitly authorizes live launch. The proof, validator stdout, gem5 binary,
review, implementation commit/tree, and reviewed files are frozen by SHA-256.
The freezer checks a separate exact-terminal-proof audit before it reads the
proof, hashes the binary, or executes the validator. That audit can resolve a
validator delta only by binding both the original dry-plan hash and the exact
successor hash; it authorizes proof consumption but never live or RTL launch.
The independent proof audit binds and approves the exact successor validator
delta while retaining the v22 dry review's original SHA-256. Freeze requires
the currently observed validator to equal that audited successor SHA-256; any
later drift fails before any dispatch reservation or live action.

Dispatch reserves its campaign receipt before executing either command. The
existing reviewed service wrapper owns each arm root and reserves all raw
outputs and service launch/ownership/terminal receipt names before child
admission. The v23 manager adds no-clobber live systemd-show and `/proc` start
identity, then terminal systemd-show and binary-safe journal export evidence.
Return codes, invocation ID, PID/start ticks, resources, exact argv hashes,
device/inode continuity, and raw hashes are fail-closed gates.

Post-processing first proves one terminal marker, one result-check pass, exact
opcode-11 selected-ABI submission with no fallback/copy/readback, and exact
descriptor/group/input/state work equations. It then makes one O_NOFOLLOW,
terminal-receipt-bound snapshot of `gem5.stderr`. A single descriptor remains
open while the snapshot is split into exact `UMT_PKI4_CONFORMANCE` and
`UMT_PKI4_LIFECYCLE` streams. Unknown, embedded, cross-schema, malformed, and
truncated prefixes fail. The snapshot is rehashed before, between, and after
both pinned normalizers.

Canonical-v3 must retain C+1 issue decisions, group-modulo-four banking,
per-bank depth at most two, and at least one same-bank C+2 source commit.
Canonical-v4 is routed through the full-successor router and must contain all
five phases, positive reuse, a generation above one, lowest-free and issue
width legality, completion/release ordering, terminal drain, and all 32 bits
free. Every accepted canonical-v3 denominator lane must exactly match the
canonical-v4 admission's epoch, request, callback, operation/group/corner,
token, digest, and masks. All successor events remain bound to that admission.

Canonical-v4 RTL replay remains blocked. No canonical-v4 RTL transactor exists
in this implementation, no RTL launch is authorized, and C+1/C+2 are C++
queue-reference evidence—not observed RTL ready, accept, or queue-commit
timing. No performance, mapped-cost, generality, universal-default,
C++/RTL-equivalence, or Gate-B promotion claim is made.
