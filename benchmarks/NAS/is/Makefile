GEM5_HOME=../../..
MAA_HOME = $(GEM5_HOME)/benchmarks/API/
CXX_FLAGS += -std=c++11 -O3 -Wall -g3 -fopenmp
GEM5_INCLUDE = -I$(GEM5_HOME)/include/ -I$(GEM5_HOME)/util/m5/src/
MAA_INCLUDE = -I$(MAA_HOME)
IS_INPUT_INCLUDE ?=
GEM5_LIB = -L$(GEM5_HOME)/util/m5/build/x86/out

SUITE = is
ROI_VERIFY_FLAGS = -DDO_VERIFY -DCLASS=\'B\' -DVERIFY_BEFORE_GEM5_EXIT
VERIFY_BINARIES = $(SUITE)_maa_16K_roi_verify
VERIFY_BINARIES += $(SUITE)_maa_16K_scalar_soa_roi_verify

# Targets for baseline binaries
BASELINE_BINARIES = $(SUITE)_base
BASELINE_BINARIES += $(SUITE)_base_4C
BASELINE_BINARIES += $(SUITE)_base_8C
MAA_BINARIES =

ifdef SMALL
	CXX_FLAGS += -DSMALL_CLASS
endif

ifdef GEM5_BUILD
	CXX_FLAGS += -DGEM5
endif

ifdef DUMP
	CXX_FLAGS += -DDUMP_TO_FILE
else
	CXX_FLAGS += -DUSE_DATA_FROM_FILE
	MAA_BINARIES += $(SUITE)_maa
	MAA_BINARIES += $(SUITE)_maa_1K
	MAA_BINARIES += $(SUITE)_maa_2K
	MAA_BINARIES += $(SUITE)_maa_4K
	MAA_BINARIES += $(SUITE)_maa_8K
	MAA_BINARIES += $(SUITE)_maa_16K
	MAA_BINARIES += $(SUITE)_maa_32K
	MAA_BINARIES += $(SUITE)_maa_64K
	MAA_BINARIES += $(SUITE)_maa_4C
	MAA_BINARIES += $(SUITE)_maa_8C
endif

.PHONY: all
all: $(BASELINE_BINARIES) $(MAA_BINARIES)

.PHONY: FUNC
FUNC: CXX_FLAGS += -DFUNC
FUNC: all

.PHONY: GEM5
GEM5: CXX_FLAGS += -DGEM5
GEM5: m5op.o all

# Rule for building baseline binaries
%_base: $(SUITE).cpp m5op.o
	$(CXX) $(MAA_INCLUDE) $(GEM5_INCLUDE) $(CXX_FLAGS) -DNUM_CORES=4 $(GEM5_LIB) m5op.o $< -o $@

%_base_4C: $(SUITE).cpp m5op.o
	$(CXX) $(MAA_INCLUDE) $(GEM5_INCLUDE) $(CXX_FLAGS) -DNUM_CORES=4 $(GEM5_LIB) m5op.o $< -o $@

%_base_8C: $(SUITE).cpp m5op.o
	$(CXX) $(MAA_INCLUDE) $(GEM5_INCLUDE) $(CXX_FLAGS) -DNUM_CORES=8 $(GEM5_LIB) m5op.o $< -o $@

# Rule for building MAA binaries
%_maa: $(SUITE).cpp m5op.o
	$(CXX) $(MAA_INCLUDE) $(GEM5_INCLUDE) $(CXX_FLAGS) -DMAA -DNUM_CORES=4 -DTILE_SIZE=16384 $(GEM5_LIB) m5op.o $< -o $@

%_maa_1K: $(SUITE).cpp m5op.o
	$(CXX) $(MAA_INCLUDE) $(GEM5_INCLUDE) $(CXX_FLAGS) -DMAA -DNUM_CORES=4 -DTILE_SIZE=1024 $(GEM5_LIB) m5op.o $< -o $@

%_maa_2K: $(SUITE).cpp m5op.o
	$(CXX) $(MAA_INCLUDE) $(GEM5_INCLUDE) $(CXX_FLAGS) -DMAA -DNUM_CORES=4 -DTILE_SIZE=2048 $(GEM5_LIB) m5op.o $< -o $@

%_maa_4K: $(SUITE).cpp m5op.o
	$(CXX) $(MAA_INCLUDE) $(GEM5_INCLUDE) $(CXX_FLAGS) -DMAA -DNUM_CORES=4 -DTILE_SIZE=4096 $(GEM5_LIB) m5op.o $< -o $@

%_maa_8K: $(SUITE).cpp m5op.o
	$(CXX) $(MAA_INCLUDE) $(GEM5_INCLUDE) $(CXX_FLAGS) -DMAA -DNUM_CORES=4 -DTILE_SIZE=8192 $(GEM5_LIB) m5op.o $< -o $@

%_maa_16K: $(SUITE).cpp m5op.o
	$(CXX) $(MAA_INCLUDE) $(GEM5_INCLUDE) $(CXX_FLAGS) -DMAA -DNUM_CORES=4 -DTILE_SIZE=16384 $(GEM5_LIB) m5op.o $< -o $@

# Keep verification outside the measured ROI but before the terminating m5_exit.
%_maa_16K_roi_verify: $(SUITE).cpp m5op.o
	$(CXX) $(MAA_INCLUDE) $(GEM5_INCLUDE) $(CXX_FLAGS) -DMAA -DNUM_CORES=4 -DTILE_SIZE=16384 $(ROI_VERIFY_FLAGS) $(GEM5_LIB) m5op.o $< -o $@

# Opt-in application binary.  Runtime remains legacy unless the guest receives
# the explicit `MAA scalar_soa_jit` selector.
%_maa_16K_scalar_soa_roi_verify: $(SUITE).cpp m5op.o
	$(CXX) $(MAA_INCLUDE) $(GEM5_INCLUDE) $(IS_INPUT_INCLUDE) $(CXX_FLAGS) -DMAA -DIS_SCALAR_SOA_JIT -DNUM_CORES=4 -DTILE_SIZE=16384 $(ROI_VERIFY_FLAGS) $(GEM5_LIB) m5op.o $< -o $@

%_maa_32K: $(SUITE).cpp m5op.o
	$(CXX) $(MAA_INCLUDE) $(GEM5_INCLUDE) $(CXX_FLAGS) -DMAA -DNUM_CORES=4 -DTILE_SIZE=32768 $(GEM5_LIB) m5op.o $< -o $@

%_maa_64K: $(SUITE).cpp m5op.o
	$(CXX) $(MAA_INCLUDE) $(GEM5_INCLUDE) $(CXX_FLAGS) -DMAA -DNUM_CORES=4 -DTILE_SIZE=65536 $(GEM5_LIB) m5op.o $< -o $@

%_maa_4C: $(SUITE).cpp m5op.o
	$(CXX) $(MAA_INCLUDE) $(GEM5_INCLUDE) $(CXX_FLAGS) -DMAA -DNUM_CORES=4 -DTILE_SIZE=16384 $(GEM5_LIB) m5op.o $< -o $@

%_maa_8C: $(SUITE).cpp m5op.o
	$(CXX) $(MAA_INCLUDE) $(GEM5_INCLUDE) $(CXX_FLAGS) -DMAA -DNUM_CORES=8 -DTILE_SIZE=16384 $(GEM5_LIB) m5op.o $< -o $@

# m5op.o is not directly used here, but including it if needed
m5op.o: $(GEM5_HOME)/util/m5/build/x86/abi/x86/m5op.S
	$(CXX) $(GEM5_INCLUDE) $(CXX_FLAGS) -c $^ -o $@

.PHONY: clean
clean:
	rm -f $(BASELINE_BINARIES) $(MAA_BINARIES) $(VERIFY_BINARIES)
