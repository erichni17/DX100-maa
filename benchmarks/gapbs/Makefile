# See LICENSE.txt for license details.

GEM5_HOME = ../..
MAA_HOME = $(GEM5_HOME)/benchmarks/API/
CXX_FLAGS += -std=c++11 -O3 -Wall -g3
PAR_FLAG = -fopenmp
GEM5_INCLUDE = -I$(GEM5_HOME)/include/ -I$(GEM5_HOME)/util/m5/src/
MAA_INCLUDE = -I$(MAA_HOME)
GEM5_LIB = -L$(GEM5_HOME)/util/m5/build/x86/out

ifneq ($(MAA_MEM_SIZE),)
	CXX_FLAGS += -DMAA_MEM_SIZE=$(MAA_MEM_SIZE)
endif

ifneq (,$(findstring icpc,$(CXX)))
	PAR_FLAG = -openmp
endif

ifneq (,$(findstring sunCC,$(CXX)))
	CXX_FLAGS = -std=c++11 -xO3 -m64 -xtarget=native
	PAR_FLAG = -xopenmp
endif

ifneq ($(SERIAL), 1)
	CXX_FLAGS += $(PAR_FLAG)
endif

ifdef GEM5_BUILD
	CXX_FLAGS += -DGEM5
endif

KERNELS = bc bfs cc cc_sv pr pr_spmv sssp tc
SUITE = $(KERNELS) converter

# Targets for baseline binaries
BASELINE_BINARIES = $(SUITE)
# Targets for MAA binaries with '_maa' suffix
MAA_BINARIES = $(addsuffix _maa, $(SUITE))
MAA_BINARIES += $(addsuffix _maa_1K, $(SUITE))
MAA_BINARIES += $(addsuffix _maa_2K, $(SUITE))
MAA_BINARIES += $(addsuffix _maa_4K, $(SUITE))
MAA_BINARIES += $(addsuffix _maa_8K, $(SUITE))
MAA_BINARIES += $(addsuffix _maa_16K, $(SUITE))
MAA_BINARIES += $(addsuffix _maa_32K, $(SUITE))
MAA_BINARIES += $(addsuffix _maa_4C, $(SUITE))
MAA_BINARIES += $(addsuffix _maa_8C, $(SUITE))
MAA_BINARIES += $(addsuffix _maa_16C, $(SUITE))

.PHONY: all
all: $(BASELINE_BINARIES) $(MAA_BINARIES)

.PHONY: FUNC
FUNC: CXX_FLAGS += -DFUNC
FUNC: all

.PHONY: GEM5
GEM5: CXX_FLAGS += -DGEM5
GEM5: m5op.o all

# Rule for building baseline binaries
%: src/%.cc src/*.h m5op.o
	$(CXX) $(MAA_INCLUDE) $(GEM5_INCLUDE) $(CXX_FLAGS) $(GEM5_LIB) m5op.o $< -o $@.tmp
	mv -f $@.tmp $@

# Rule for building MAA binaries
%_maa: src/%.cc src/*.h m5op.o
	$(CXX) $(MAA_INCLUDE) $(GEM5_INCLUDE) $(CXX_FLAGS) -DMAA -DNUM_CORES=4 -DTILE_SIZE=16384 $(GEM5_LIB) m5op.o $< -o $@.tmp
	mv -f $@.tmp $@

%_maa_1K: src/%.cc src/*.h m5op.o
	$(CXX) $(MAA_INCLUDE) $(GEM5_INCLUDE) $(CXX_FLAGS) $(EXTRA_CXX_FLAGS) -DMAA -DNUM_CORES=4 -DTILE_SIZE=1024 $(GEM5_LIB) m5op.o $< -o $@.tmp
	mv -f $@.tmp $@

%_maa_2K: src/%.cc src/*.h m5op.o
	$(CXX) $(MAA_INCLUDE) $(GEM5_INCLUDE) $(CXX_FLAGS) $(EXTRA_CXX_FLAGS) -DMAA -DNUM_CORES=4 -DTILE_SIZE=2048 $(GEM5_LIB) m5op.o $< -o $@.tmp
	mv -f $@.tmp $@

%_maa_4K: src/%.cc src/*.h m5op.o
	$(CXX) $(MAA_INCLUDE) $(GEM5_INCLUDE) $(CXX_FLAGS) $(EXTRA_CXX_FLAGS) -DMAA -DNUM_CORES=4 -DTILE_SIZE=4096 $(GEM5_LIB) m5op.o $< -o $@.tmp
	mv -f $@.tmp $@

%_maa_8K: src/%.cc src/*.h m5op.o
	$(CXX) $(MAA_INCLUDE) $(GEM5_INCLUDE) $(CXX_FLAGS) $(EXTRA_CXX_FLAGS) -DMAA -DNUM_CORES=4 -DTILE_SIZE=8192 $(GEM5_LIB) m5op.o $< -o $@.tmp
	mv -f $@.tmp $@

%_maa_16K: src/%.cc src/*.h m5op.o
	$(CXX) $(MAA_INCLUDE) $(GEM5_INCLUDE) $(CXX_FLAGS) $(EXTRA_CXX_FLAGS) -DMAA -DNUM_CORES=4 -DTILE_SIZE=16384 $(GEM5_LIB) m5op.o $< -o $@.tmp
	mv -f $@.tmp $@

%_maa_32K: src/%.cc src/*.h m5op.o
	$(CXX) $(MAA_INCLUDE) $(GEM5_INCLUDE) $(CXX_FLAGS) $(EXTRA_CXX_FLAGS) -DMAA -DNUM_CORES=4 -DTILE_SIZE=32768 $(GEM5_LIB) m5op.o $< -o $@.tmp
	mv -f $@.tmp $@

%_maa_64K: src/%.cc src/*.h m5op.o
	$(CXX) $(MAA_INCLUDE) $(GEM5_INCLUDE) $(CXX_FLAGS) $(EXTRA_CXX_FLAGS) -DMAA -DNUM_CORES=4 -DTILE_SIZE=65536 $(GEM5_LIB) m5op.o $< -o $@.tmp
	mv -f $@.tmp $@

%_maa_4C: src/%.cc src/*.h m5op.o
	$(CXX) $(MAA_INCLUDE) $(GEM5_INCLUDE) $(CXX_FLAGS) -DMAA -DNUM_CORES=4 -DTILE_SIZE=16384 $(GEM5_LIB) m5op.o $< -o $@.tmp
	mv -f $@.tmp $@

%_maa_8C: src/%.cc src/*.h m5op.o
	$(CXX) $(MAA_INCLUDE) $(GEM5_INCLUDE) $(CXX_FLAGS) -DMAA -DNUM_CORES=8 -DTILE_SIZE=16384 $(GEM5_LIB) m5op.o $< -o $@.tmp
	mv -f $@.tmp $@

%_maa_16C: src/%.cc src/*.h m5op.o
	$(CXX) $(MAA_INCLUDE) $(GEM5_INCLUDE) $(CXX_FLAGS) -DMAA -DNUM_CORES=16 -DTILE_SIZE=16384 $(GEM5_LIB) m5op.o $< -o $@.tmp
	mv -f $@.tmp $@

# Fingerprinted PageRank binary for airtight float-output validation at 2GB.
pr_maa_2G_fp: src/pr.cc src/*.h m5op.o
	$(CXX) $(MAA_INCLUDE) $(GEM5_INCLUDE) $(CXX_FLAGS) -DMAA -DNUM_CORES=4 -DTILE_SIZE=16384 -DMAA_MEM_SIZE=0x80000000 -DPR_FP_ENABLE=1 $(GEM5_LIB) m5op.o $< -o $@.tmp
	mv -f $@.tmp $@

pr_maa_2G_virtual_fp: src/pr.cc src/*.h m5op.o
	$(CXX) $(MAA_INCLUDE) $(GEM5_INCLUDE) $(CXX_FLAGS) -DMAA -DMAA_VIRTUAL_GATHER -DNUM_CORES=4 -DTILE_SIZE=16384 -DMAA_MEM_SIZE=0x80000000 -DPR_FP_ENABLE=1 $(GEM5_LIB) m5op.o $< -o $@.tmp
	mv -f $@.tmp $@

bfs_maa_2G_fp: src/bfs.cc src/*.h m5op.o
	$(CXX) $(MAA_INCLUDE) $(GEM5_INCLUDE) $(CXX_FLAGS) -DMAA -DNUM_CORES=4 -DTILE_SIZE=16384 -DMAA_MEM_SIZE=0x80000000 -DBFS_FP_ENABLE=1 $(GEM5_LIB) m5op.o $< -o $@.tmp
	mv -f $@.tmp $@

bfs_maa_2G_virtual_fp: src/bfs.cc src/*.h m5op.o
	$(CXX) $(MAA_INCLUDE) $(GEM5_INCLUDE) $(CXX_FLAGS) -DMAA -DMAA_VIRTUAL_GATHER -DNUM_CORES=4 -DTILE_SIZE=16384 -DMAA_MEM_SIZE=0x80000000 -DBFS_FP_ENABLE=1 $(GEM5_LIB) m5op.o $< -o $@.tmp
	mv -f $@.tmp $@

# Fingerprinted SSSP binaries with a post-ROI shortest-path certificate.
sssp_maa_1K_fp: src/sssp.cc src/*.h m5op.o
	$(CXX) $(MAA_INCLUDE) $(GEM5_INCLUDE) $(CXX_FLAGS) -DGEM5 -DMAA -DNUM_CORES=4 -DTILE_SIZE=1024 -DMAA_MEM_SIZE=0x80000000 -DSSSP_FP_ENABLE=1 $(GEM5_LIB) m5op.o $< -o $@.tmp
	mv -f $@.tmp $@

sssp_maa_2K_fp: src/sssp.cc src/*.h m5op.o
	$(CXX) $(MAA_INCLUDE) $(GEM5_INCLUDE) $(CXX_FLAGS) -DGEM5 -DMAA -DNUM_CORES=4 -DTILE_SIZE=2048 -DMAA_MEM_SIZE=0x80000000 -DSSSP_FP_ENABLE=1 $(GEM5_LIB) m5op.o $< -o $@.tmp
	mv -f $@.tmp $@

# Scale-22 BC correctness binary: end the ROI before the serial verifier, then
# exit gem5 only after GAPBS reports the verification result.
bc_maa_1K_verify: src/bc.cc src/*.h m5op.o
	$(CXX) $(MAA_INCLUDE) $(GEM5_INCLUDE) $(CXX_FLAGS) -DGEM5 -DMAA -DNUM_CORES=4 -DTILE_SIZE=1024 -DMAA_MEM_SIZE=0x80000000 -DBC_VERIFY_AFTER_ROI=1 $(GEM5_LIB) m5op.o $< -o $@.tmp
	mv -f $@.tmp $@

# m5op.o is not directly used here, but including it if needed
m5op.o: $(GEM5_HOME)/util/m5/build/x86/abi/x86/m5op.S
	$(CXX) $(GEM5_INCLUDE) $(CXX_FLAGS) -c $^ -o $@

# Testing
include test/test.mk

# Benchmark Automation
include benchmark/bench.mk

.PHONY: clean
clean:
	rm -f $(BASELINE_BINARIES) $(MAA_BINARIES) test/out/*
