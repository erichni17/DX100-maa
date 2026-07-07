#!/bin/bash
# bisect.sh -- systematic isolation of the ~10GB instantiation blowup.
# Pure platform instantiation (no-restore BASE + --initialize-only via measure_nr.sh).
# Each row toggles ONE axis vs the real run_test.sh step-2 config.
GH=/home/nier/DX100
RAMCFG="$GH/ext/ramulator2/ramulator2/example_gem5_config.yaml"
LOG="$GH/measure_out/BISECT_RESULTS.txt"
mkdir -p "$GH/measure_out"; : > "$LOG"

RAM="--mem-type Ramulator2 --ramulator-config $RAMCFG --mem-channels 2"
SIMPLE="--mem-type SimpleMemory --mem-channels 2"
L1="--caches --l1d_size=32kB --l1d_assoc=8 --l1d_mshrs=16 --l1d_write_buffers=8 \
--l1i_size=32kB --l1i_assoc=8 --l1i_mshrs=16 --l1i_write_buffers=8 --cacheline_size=64"
L2="--l2cache --l2_size=256kB --l2_assoc=4 --l2_mshrs=32 --l2_write_buffers=16"
L3="--l3cache --l3_size=2MB --l3_assoc=4 --l3_mshrs=64 --l3_write_buffers=32 --l3_ports 1"
CACHE="$L1 $L2 $L3"

run(){ local tag="$1"; shift
  echo ">>> $tag : $*" | tee -a "$LOG"
  bash "$GH/measure_nr.sh" "$tag" "$@" 2>&1 | grep -E "RESULT|fatal|panic" | tee -a "$LOG"
  echo "" | tee -a "$LOG"
}

# fast / known-good ones first; full 4-core repro last (may burn the 180s cap)
run g1_1core_full   -n 1 --mem-size 1GB $CACHE $RAM
run g2_2core_full   -n 2 --mem-size 1GB $CACHE $RAM
run g4_nocache      -n 4 --mem-size 1GB $RAM
run g4_simplemem    -n 4 --mem-size 1GB $CACHE $SIMPLE
run g4_L1L2_only    -n 4 --mem-size 1GB $L1 $L2 $RAM
run g4_full         -n 4 --mem-size 1GB $CACHE $RAM
echo "=== DONE; summary in $LOG ===" | tee -a "$LOG"
