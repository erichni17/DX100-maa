# Current pager debug handoff for Josh

[Download the offline source/evidence bundle](josh-pager-debug-20261112.tar.gz) (about1.1MB).

[Read the model delta](source.patch). The source patch is relative to the recorded historical parent, not to this publishing branch. This branch publishes a snapshot; it does not merge the research model into the integration branch.

Archive SHA256: `43ef879db211ad7a767f84d22aaa2b583de77f6d7452ac017bd33af771bf1903`.

Offline source/evidence bundle; no server access needed. No model binaries, graph data or huge traces included.

## Identity and cautions
- Source snapshot HEAD: 1c6f9a13a7d4f4c5b6fe07c3633ddb11f89bba13; functional region31 isolation change c4eb9457. Base for source.patch:1a231aa4c34119b9b370e40b1d236f15ebd5438c.
- Current GZZ2/GZZ4 model SHA:5a634a43213ecc345502135e4b4961044eb7bc4fdc203b0df0d5446781a288ce.
- GZZ guest SHA:ea88382fc64f13df9604cfa6b3526a41930863646be17853fca7a5a90d3c7ee6. Original kernel revision e4fc4afdf894f295442cef3604667a469fab8e62; timed body preserved; driver supplies thread/input parameters and independent oracle. Exact compile command in guest build.py.
- IMPORTANT: archived one-worker run uses OLDER model36833, while two/four use repaired current5a. Do not attribute their difference purely to thread count. Same-current-model one-worker comparison remains missing.
- Commands/config/whole stats/correctness excerpts included for1/2/4; first stats block is kernel ROI; subsequent output verification is outside ROI. No statistical confidence claim.
- Current source tree clean; unrelated guest-worktree dirt is NOT exported.

## Selectors and code
- GCC9: -std=c++17 -O3 -g -fopenmp -DGEM5 -DMAA -DNUM_CORES=4 -DTILE_SIZE=16384 -DMAA_MEM_SIZE=0x80000000.
- Runtime original GZZ options:65536 1 /65536 2 /65536 4. Four configured CPU cores; active OpenMP workers separately selected.
- Logical16384/physical4096,32frame pool; --maa-device-owned-spd-paging --maa-device-owned-spd-frames=32; dynamic mode33. NO --maa-device-owned-spd-overlap for multiworker.
- Shared spill/fill line credits default32; DX100_PAGER_LINE_CREDITS overrides1..256. They are request metadata, not copied payload.
- SPD.cc/hh: page/frame directory, victim/pin ownership, servicePager and coherent fills/spills. MAA.cc/hh: instruction admission/engine leases; StreamAccess.cc,IndirectAccess.cc,RangeFuser.cc contain paged-engine paths. CacheSidePort.cc routes region31 to existing retirement bank. MAAConfig.py defines SPD-demand topology and coherent backing fronts.
- Rolling refill IS enabled within one active page transfer: free line credits are replenished without waiting for a whole batch. However there is ONE active page transfer globally; dynamic pressure mode admits ONE accelerator command at a time. There is NO general multiple-concurrent-page-miss queue. Do not conflate32line slots with32page misses or32parallel commands.
- Resident overlap mode97 uses fixed frame=tile*4+page and worker0 only, distinct from these dynamic contention runs.

## Current finding
- Current GZZ2:283914162ticks; GZZ4:283724171. Full numerical output and worker tile views pass; same input/output. Two uses roughly half traffic/CPUinstructions. Compare earliest residency/consumption stalls and serialization before proposing a new algorithm.
- Coherent fronts add4x512B data plus32MSHR/256targets,32normal+32reserve write entries, tags/control/ports and1cycle tag/data/response. These are real added costs. Backing through coherent LLC, SPD demand topology separated.
- Preserve assertions and actual dirty ownership; earlier direct rawWriteReq fanout and blocked-LLC retry cycle both failed. Do not bypass coherence or use global drains.

## Useful smallest follow-up
Measure dynamic current1/2/4 on SAME model, then classify wait time by page transfer, CPU tile demand, engine admission and ready work that cannot run. Compare multi-page concurrency/selection only after identifying blocked owners. CPU computation in original GZZ is intentional source behavior.
