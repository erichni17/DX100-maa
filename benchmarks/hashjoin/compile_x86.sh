#!/usr/bin/env bash
set -euo pipefail

GEM5_HOME=${GEM5_HOME:-../../..}
MAA_HOME=$GEM5_HOME/benchmarks/API
cd src
if [[ -d bin ]]; then
    rm -r bin
fi
mkdir -p bin/x86

if [ $# -lt 1 ]; then
    echo "Usage: $0 (FUNC|GEM5) [LOAD|DUMP]"
    exit 1
fi
# check whether the second argument is FUNC or GEM5
if [ $1 != "FUNC" ] && [ $1 != "GEM5" ]; then
    echo "Usage: $0 (FUNC|GEM5) [LOAD|DUMP]"
    exit 1
fi

EXTRA_FILE=""
if [ $1 == "GEM5" ]; then
    EXTRA_FILE=${M5OP_ASM:-$GEM5_HOME/util/m5/src/abi/x86/m5op.S}
    [[ -f "$EXTRA_FILE" ]] || {
        echo "missing m5op assembly source: $EXTRA_FILE" >&2
        exit 2
    }
fi
MACROS="-D$1 -I$GEM5_HOME/include -I${GEM5_HOME}/util/m5/src/ -I$MAA_HOME/"
if [ $# -ge 2 ]; then
    MACROS="$MACROS -D$2"
fi
if [ -n "${MAA_MEM_SIZE:-}" ]; then
    MACROS="$MACROS -DMAA_MEM_SIZE=${MAA_MEM_SIZE}"
fi
g++ -O0 -g3 npj2epb.c -c  -std=c++11 $MACROS
g++ -O0 -g3 $MACROS $EXTRA_FILE npj2epb.o main.c generator.c genzipf.c perf_counters.c cpu_mapping.c parallel_radix_join.cpp -lpthread -fopenmp -lm  -o bin/x86/hj_maa -DMAA -std=c++11 -DNUM_CORES=4 -DTILE_SIZE=16384
g++ -O0 -g3 $MACROS $EXTRA_FILE npj2epb.o main.c generator.c genzipf.c perf_counters.c cpu_mapping.c parallel_radix_join.cpp -lpthread -fopenmp -lm  -o bin/x86/hj_maa_64K -DMAA -std=c++11 -DNUM_CORES=4 -DTILE_SIZE=65536
g++ -O0 -g3 $MACROS $EXTRA_FILE npj2epb.o main.c generator.c genzipf.c perf_counters.c cpu_mapping.c parallel_radix_join.cpp -lpthread -fopenmp -lm  -o bin/x86/hj_maa_32K -DMAA -std=c++11 -DNUM_CORES=4 -DTILE_SIZE=32768
g++ -O0 -g3 $MACROS $EXTRA_FILE npj2epb.o main.c generator.c genzipf.c perf_counters.c cpu_mapping.c parallel_radix_join.cpp -lpthread -fopenmp -lm  -o bin/x86/hj_maa_16K -DMAA -std=c++11 -DNUM_CORES=4 -DTILE_SIZE=16384
g++ -O0 -g3 $MACROS $EXTRA_FILE npj2epb.o main.c generator.c genzipf.c perf_counters.c cpu_mapping.c parallel_radix_join.cpp -lpthread -fopenmp -lm  -o bin/x86/hj_maa_16K_hybrid -DMAA -DHASHJOIN_HYBRID_SOA_JIT -std=c++11 -DNUM_CORES=4 -DTILE_SIZE=16384
g++ -O0 -g3 $MACROS $EXTRA_FILE npj2epb.o main.c generator.c genzipf.c perf_counters.c cpu_mapping.c parallel_radix_join.cpp -lpthread -fopenmp -lm  -o bin/x86/hj_maa_8K -DMAA -std=c++11 -DNUM_CORES=4 -DTILE_SIZE=8192
g++ -O0 -g3 $MACROS $EXTRA_FILE npj2epb.o main.c generator.c genzipf.c perf_counters.c cpu_mapping.c parallel_radix_join.cpp -lpthread -fopenmp -lm  -o bin/x86/hj_maa_4K -DMAA -std=c++11 -DNUM_CORES=4 -DTILE_SIZE=4096
g++ -O0 -g3 $MACROS $EXTRA_FILE npj2epb.o main.c generator.c genzipf.c perf_counters.c cpu_mapping.c parallel_radix_join.cpp -lpthread -fopenmp -lm  -o bin/x86/hj_maa_2K -DMAA -std=c++11 -DNUM_CORES=4 -DTILE_SIZE=2048
g++ -O0 -g3 $MACROS $EXTRA_FILE npj2epb.o main.c generator.c genzipf.c perf_counters.c cpu_mapping.c parallel_radix_join.cpp -lpthread -fopenmp -lm  -o bin/x86/hj_maa_1K -DMAA -std=c++11 -DNUM_CORES=4 -DTILE_SIZE=1024
g++ -O0 -g3 $MACROS $EXTRA_FILE npj2epb.o main.c generator.c genzipf.c perf_counters.c cpu_mapping.c parallel_radix_join.cpp -lpthread -fopenmp -lm  -o bin/x86/hj_maa_4C -DMAA -std=c++11 -DNUM_CORES=4 -DTILE_SIZE=16384
g++ -O0 -g3 $MACROS $EXTRA_FILE npj2epb.o main.c generator.c genzipf.c perf_counters.c cpu_mapping.c parallel_radix_join.cpp -lpthread -fopenmp -lm  -o bin/x86/hj_maa_8C -DMAA -std=c++11 -DNUM_CORES=8 -DTILE_SIZE=16384
g++ -O0 -g3 $MACROS $EXTRA_FILE npj2epb.o main.c generator.c genzipf.c perf_counters.c cpu_mapping.c parallel_radix_join.cpp -lpthread -fopenmp -lm  -o bin/x86/hj_base -DCPU -std=c++11
