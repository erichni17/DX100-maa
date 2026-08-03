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

SUITE = gradzatp gradzatz gradzatp_invert gradzatz_invert

# Targets for baseline binaries
BASELINE_BINARIES = $(addsuffix _base, $(SUITE))
# Targets for MAA binaries with '_maa' suffix
MAA_BINARIES = $(addsuffix _maa, $(SUITE))
MAA_BINARIES += $(addsuffix _maa_1K, $(SUITE))
MAA_BINARIES += $(addsuffix _maa_2K, $(SUITE))
MAA_BINARIES += $(addsuffix _maa_4K, $(SUITE))
MAA_BINARIES += $(addsuffix _maa_8K, $(SUITE))
MAA_BINARIES += $(addsuffix _maa_16K, $(SUITE))
MAA_BINARIES += $(addsuffix _maa_32K, $(SUITE))
MAA_BINARIES += $(addsuffix _maa_64K, $(SUITE))
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

gradzatz_base gradzatz_maa gradzatz_maa_1K gradzatz_maa_2K \
gradzatz_maa_4K gradzatz_maa_8K gradzatz_maa_16K gradzatz_maa_32K \
gradzatz_maa_64K gradzatz_maa_4C gradzatz_maa_8C gradzatz_maa_16C \
gradzatz_maa_1K_verify gradzatz_maa_1K_virtual_verify \
gradzatz_maa_16K_fixed_fp gradzatz_maa_16K_virtual_fixed_fp: \
	gzz_chunk_policy.h

# Rule for building baseline binaries
%_base: %.cpp m5op.o
	$(CXX) $(MAA_INCLUDE) $(GEM5_INCLUDE) $(CXX_FLAGS) $(GEM5_LIB) m5op.o $< -o $@

# Rule for building MAA binaries
%_maa: %.cpp m5op.o
	$(CXX) $(MAA_INCLUDE) $(GEM5_INCLUDE) $(CXX_FLAGS) -DMAA -DNUM_CORES=4 -DTILE_SIZE=16384 $(GEM5_LIB) m5op.o $< -o $@

%_maa_1K: %.cpp m5op.o
	$(CXX) $(MAA_INCLUDE) $(GEM5_INCLUDE) $(CXX_FLAGS) $(EXTRA_CXX_FLAGS) -DMAA -DNUM_CORES=4 -DTILE_SIZE=1024 $(GEM5_LIB) m5op.o $< -o $@.tmp
	mv -f $@.tmp $@

%_maa_2K: %.cpp m5op.o
	$(CXX) $(MAA_INCLUDE) $(GEM5_INCLUDE) $(CXX_FLAGS) $(EXTRA_CXX_FLAGS) -DMAA -DNUM_CORES=4 -DTILE_SIZE=2048 $(GEM5_LIB) m5op.o $< -o $@.tmp
	mv -f $@.tmp $@

%_maa_4K: %.cpp m5op.o
	$(CXX) $(MAA_INCLUDE) $(GEM5_INCLUDE) $(CXX_FLAGS) $(EXTRA_CXX_FLAGS) -DMAA -DNUM_CORES=4 -DTILE_SIZE=4096 $(GEM5_LIB) m5op.o $< -o $@.tmp
	mv -f $@.tmp $@

%_maa_8K: %.cpp m5op.o
	$(CXX) $(MAA_INCLUDE) $(GEM5_INCLUDE) $(CXX_FLAGS) $(EXTRA_CXX_FLAGS) -DMAA -DNUM_CORES=4 -DTILE_SIZE=8192 $(GEM5_LIB) m5op.o $< -o $@.tmp
	mv -f $@.tmp $@

%_maa_16K: %.cpp m5op.o
	$(CXX) $(MAA_INCLUDE) $(GEM5_INCLUDE) $(CXX_FLAGS) $(EXTRA_CXX_FLAGS) -DMAA -DNUM_CORES=4 -DTILE_SIZE=16384 $(GEM5_LIB) m5op.o $< -o $@.tmp
	mv -f $@.tmp $@

%_maa_32K: %.cpp m5op.o
	$(CXX) $(MAA_INCLUDE) $(GEM5_INCLUDE) $(CXX_FLAGS) $(EXTRA_CXX_FLAGS) -DMAA -DNUM_CORES=4 -DTILE_SIZE=32768 $(GEM5_LIB) m5op.o $< -o $@.tmp
	mv -f $@.tmp $@

%_maa_64K: %.cpp m5op.o
	$(CXX) $(MAA_INCLUDE) $(GEM5_INCLUDE) $(CXX_FLAGS) $(EXTRA_CXX_FLAGS) -DMAA -DNUM_CORES=4 -DTILE_SIZE=65536 $(GEM5_LIB) m5op.o $< -o $@.tmp
	mv -f $@.tmp $@

%_maa_4C: %.cpp m5op.o
	$(CXX) $(MAA_INCLUDE) $(GEM5_INCLUDE) $(CXX_FLAGS) -DMAA -DNUM_CORES=4 -DTILE_SIZE=16384 $(GEM5_LIB) m5op.o $< -o $@

%_maa_8C: %.cpp m5op.o
	$(CXX) $(MAA_INCLUDE) $(GEM5_INCLUDE) $(CXX_FLAGS) -DMAA -DNUM_CORES=8 -DTILE_SIZE=16384 $(GEM5_LIB) m5op.o $< -o $@

%_maa_16C: %.cpp m5op.o
	$(CXX) $(MAA_INCLUDE) $(GEM5_INCLUDE) $(CXX_FLAGS) -DMAA -DNUM_CORES=16 -DTILE_SIZE=16384 $(GEM5_LIB) m5op.o $< -o $@

gradzatp_invert_maa_1K_verify: gradzatp_invert.cpp m5op.o
	$(CXX) $(MAA_INCLUDE) $(GEM5_INCLUDE) $(CXX_FLAGS) -DMAA -DUME_GATHER_VERIFY -DNUM_CORES=4 -DTILE_SIZE=1024 -DMAA_MEM_SIZE=0x80000000 $(GEM5_LIB) m5op.o $< -o $@

gradzatp_invert_maa_1K_virtual_verify: gradzatp_invert.cpp m5op.o
	$(CXX) $(MAA_INCLUDE) $(GEM5_INCLUDE) $(CXX_FLAGS) -DMAA -DMAA_VIRTUAL_GATHER -DUME_GATHER_VERIFY -DNUM_CORES=4 -DTILE_SIZE=1024 -DMAA_MEM_SIZE=0x80000000 $(GEM5_LIB) m5op.o $< -o $@

gradzatp_invert_maa_16K_virtual: gradzatp_invert.cpp m5op.o
	$(CXX) $(MAA_INCLUDE) $(GEM5_INCLUDE) $(CXX_FLAGS) -DMAA -DMAA_VIRTUAL_GATHER -DNUM_CORES=4 -DTILE_SIZE=16384 -DMAA_MEM_SIZE=0x80000000 $(GEM5_LIB) m5op.o $< -o $@

gradzatp_invert_maa_16K_fixed_fp: gradzatp_invert.cpp m5op.o
	$(CXX) $(MAA_INCLUDE) $(GEM5_INCLUDE) $(CXX_FLAGS) -DMAA -DUME_FIXED_INPUT -DUME_OUTPUT_FINGERPRINT -DNUM_CORES=4 -DTILE_SIZE=16384 -DMAA_MEM_SIZE=0x80000000 $(GEM5_LIB) m5op.o $< -o $@

gradzatp_invert_maa_16K_virtual_fixed_fp: gradzatp_invert.cpp m5op.o
	$(CXX) $(MAA_INCLUDE) $(GEM5_INCLUDE) $(CXX_FLAGS) -DMAA -DMAA_VIRTUAL_GATHER -DUME_FIXED_INPUT -DUME_OUTPUT_FINGERPRINT -DNUM_CORES=4 -DTILE_SIZE=16384 -DMAA_MEM_SIZE=0x80000000 $(GEM5_LIB) m5op.o $< -o $@

gradzatp_maa_1K_verify: gradzatp.cpp m5op.o
	$(CXX) $(MAA_INCLUDE) $(GEM5_INCLUDE) $(CXX_FLAGS) -DMAA -DUME_GATHER_VERIFY -DNUM_CORES=4 -DTILE_SIZE=1024 -DMAA_MEM_SIZE=0x80000000 $(GEM5_LIB) m5op.o $< -o $@

gradzatp_maa_1K_virtual_verify: gradzatp.cpp m5op.o
	$(CXX) $(MAA_INCLUDE) $(GEM5_INCLUDE) $(CXX_FLAGS) -DMAA -DMAA_VIRTUAL_GATHER -DUME_GATHER_VERIFY -DNUM_CORES=4 -DTILE_SIZE=1024 -DMAA_MEM_SIZE=0x80000000 $(GEM5_LIB) m5op.o $< -o $@

gradzatp_maa_16K_fixed_fp: gradzatp.cpp m5op.o
	$(CXX) $(MAA_INCLUDE) $(GEM5_INCLUDE) $(CXX_FLAGS) -DMAA -DUME_FIXED_INPUT -DUME_OUTPUT_FINGERPRINT -DNUM_CORES=4 -DTILE_SIZE=16384 -DMAA_MEM_SIZE=0x80000000 $(GEM5_LIB) m5op.o $< -o $@

gradzatp_maa_16K_virtual_fixed_fp: gradzatp.cpp m5op.o
	$(CXX) $(MAA_INCLUDE) $(GEM5_INCLUDE) $(CXX_FLAGS) -DMAA -DMAA_VIRTUAL_GATHER -DUME_FIXED_INPUT -DUME_OUTPUT_FINGERPRINT -DNUM_CORES=4 -DTILE_SIZE=16384 -DMAA_MEM_SIZE=0x80000000 $(GEM5_LIB) m5op.o $< -o $@

gradzatz_maa_1K_verify: gradzatz.cpp m5op.o
	$(CXX) $(MAA_INCLUDE) $(GEM5_INCLUDE) $(CXX_FLAGS) -DMAA -DUME_GRADZATZ_VERIFY -DNUM_CORES=4 -DTILE_SIZE=1024 -DMAA_MEM_SIZE=0x80000000 $(GEM5_LIB) m5op.o $< -o $@

gradzatz_maa_1K_virtual_verify: gradzatz.cpp m5op.o
	$(CXX) $(MAA_INCLUDE) $(GEM5_INCLUDE) $(CXX_FLAGS) -DMAA -DMAA_VIRTUAL_GATHER -DUME_GRADZATZ_VERIFY -DNUM_CORES=4 -DTILE_SIZE=1024 -DMAA_MEM_SIZE=0x80000000 $(GEM5_LIB) m5op.o $< -o $@

gradzatz_maa_16K_fixed_fp: gradzatz.cpp m5op.o
	$(CXX) $(MAA_INCLUDE) $(GEM5_INCLUDE) $(CXX_FLAGS) -DMAA -DUME_GRADZATZ_FIXED_INPUT -DUME_GRADZATZ_OUTPUT_FINGERPRINT -DUME_GRADZATZ_EXPECTED_N=1000000 -DUME_GRADZATZ_EXPECTED_HASH=9234467062988358067ULL -DNUM_CORES=4 -DTILE_SIZE=16384 -DMAA_MEM_SIZE=0x80000000 $(GEM5_LIB) m5op.o $< -o $@

gradzatz_maa_16K_virtual_fixed_fp: gradzatz.cpp m5op.o
	$(CXX) $(MAA_INCLUDE) $(GEM5_INCLUDE) $(CXX_FLAGS) -DMAA -DMAA_VIRTUAL_GATHER -DUME_GRADZATZ_FIXED_INPUT -DUME_GRADZATZ_OUTPUT_FINGERPRINT -DUME_GRADZATZ_EXPECTED_N=1000000 -DUME_GRADZATZ_EXPECTED_HASH=9234467062988358067ULL -DNUM_CORES=4 -DTILE_SIZE=16384 -DMAA_MEM_SIZE=0x80000000 $(GEM5_LIB) m5op.o $< -o $@

gradzatz_invert_maa_1K_verify: gradzatz_invert.cpp m5op.o
	$(CXX) $(MAA_INCLUDE) $(GEM5_INCLUDE) $(CXX_FLAGS) -DMAA -DUME_GRADZATZ_INVERT_VERIFY -DNUM_CORES=4 -DTILE_SIZE=1024 -DMAA_MEM_SIZE=0x80000000 $(GEM5_LIB) m5op.o $< -o $@

gradzatz_invert_maa_1K_virtual_verify: gradzatz_invert.cpp m5op.o
	$(CXX) $(MAA_INCLUDE) $(GEM5_INCLUDE) $(CXX_FLAGS) -DMAA -DMAA_VIRTUAL_GATHER -DUME_GRADZATZ_INVERT_VERIFY -DNUM_CORES=4 -DTILE_SIZE=1024 -DMAA_MEM_SIZE=0x80000000 $(GEM5_LIB) m5op.o $< -o $@

gradzatz_invert_maa_16K_fixed_fp: gradzatz_invert.cpp m5op.o
	$(CXX) $(MAA_INCLUDE) $(GEM5_INCLUDE) $(CXX_FLAGS) -DMAA -DUME_GRADZATZ_INVERT_FIXED_INPUT -DUME_GRADZATZ_INVERT_OUTPUT_FINGERPRINT -DUME_GRADZATZ_INVERT_EXPECTED_N=1000000 -DUME_GRADZATZ_INVERT_EXPECTED_HASH=2570238273900534505ULL -DNUM_CORES=4 -DTILE_SIZE=16384 -DMAA_MEM_SIZE=0x80000000 $(GEM5_LIB) m5op.o $< -o $@

gradzatz_invert_maa_16K_virtual_fixed_fp: gradzatz_invert.cpp m5op.o
	$(CXX) $(MAA_INCLUDE) $(GEM5_INCLUDE) $(CXX_FLAGS) -DMAA -DMAA_VIRTUAL_GATHER -DUME_GRADZATZ_INVERT_FIXED_INPUT -DUME_GRADZATZ_INVERT_OUTPUT_FINGERPRINT -DUME_GRADZATZ_INVERT_EXPECTED_N=1000000 -DUME_GRADZATZ_INVERT_EXPECTED_HASH=2570238273900534505ULL -DNUM_CORES=4 -DTILE_SIZE=16384 -DMAA_MEM_SIZE=0x80000000 $(GEM5_LIB) m5op.o $< -o $@

# m5op.o is not directly used here, but including it if needed
m5op.o: $(GEM5_HOME)/util/m5/build/x86/abi/x86/m5op.S
	$(CXX) $(GEM5_INCLUDE) $(CXX_FLAGS) -c $^ -o $@

.PHONY: clean
clean:
	rm -f $(BASELINE_BINARIES) $(MAA_BINARIES)
