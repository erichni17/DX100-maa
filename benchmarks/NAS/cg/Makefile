GEM5_HOME=../../..
MAA_HOME = $(GEM5_HOME)/benchmarks/API/
CXX_FLAGS += -std=c++11 -O3 -Wall -g3 -fopenmp
GEM5_INCLUDE = -I$(GEM5_HOME)/include/ -I$(GEM5_HOME)/util/m5/src/
MAA_INCLUDE = -I$(MAA_HOME)
GEM5_LIB = -L$(GEM5_HOME)/util/m5/build/x86/out

SUITE = cg

BASELINE_BINARIES = $(SUITE)_base $(SUITE)_base_4C $(SUITE)_base_8C
MAA_BINARIES =

ifdef GEM5_BUILD
	CXX_FLAGS += -DGEM5
endif

ifdef MAA_MEM_SIZE
	CXX_FLAGS += -DMAA_MEM_SIZE=$(MAA_MEM_SIZE)
endif

ifdef DUMP
	CXX_FLAGS += -DDUMP_TO_FILE
else ifndef RUNTIME_DATA
	CXX_FLAGS += -DUSE_DATA_FROM_FILE
endif

ifndef DUMP
	MAA_BINARIES += $(SUITE)_maa $(SUITE)_maa_1K $(SUITE)_maa_2K
	MAA_BINARIES += $(SUITE)_maa_4K $(SUITE)_maa_8K $(SUITE)_maa_16K
	MAA_BINARIES += $(SUITE)_maa_32K $(SUITE)_maa_64K
	MAA_BINARIES += $(SUITE)_maa_4C $(SUITE)_maa_8C
	MAA_BINARIES += $(SUITE)_maa_16K_fp $(SUITE)_maa_16K_virtual
	MAA_BINARIES += $(SUITE)_maa_16K_bounded
endif

.PHONY: all FUNC GEM5 clean
all: $(BASELINE_BINARIES) $(MAA_BINARIES)

FUNC: CXX_FLAGS += -DFUNC
FUNC: all

GEM5: CXX_FLAGS += -DGEM5
GEM5: m5op.o all

# Compile to a temporary file so parallel tile builds never expose partial binaries.
define build_binary
	$(CXX) $(MAA_INCLUDE) $(GEM5_INCLUDE) $(CXX_FLAGS) $(1) $(GEM5_LIB) m5op.o cg.cpp -o $@.tmp
	mv $@.tmp $@
endef

%_base: cg.cpp m5op.o
	$(call build_binary,-DNUM_CORES=4)

%_base_4C: cg.cpp m5op.o
	$(call build_binary,-DNUM_CORES=4)

%_base_8C: cg.cpp m5op.o
	$(call build_binary,-DNUM_CORES=8)

%_maa: cg.cpp m5op.o
	$(call build_binary,-DMAA -DNUM_CORES=4 -DTILE_SIZE=16384)

%_maa_1K: cg.cpp m5op.o
	$(call build_binary,-DMAA -DNUM_CORES=4 -DTILE_SIZE=1024)

%_maa_2K: cg.cpp m5op.o
	$(call build_binary,-DMAA -DNUM_CORES=4 -DTILE_SIZE=2048)

%_maa_4K: cg.cpp m5op.o
	$(call build_binary,-DMAA -DNUM_CORES=4 -DTILE_SIZE=4096)

%_maa_8K: cg.cpp m5op.o
	$(call build_binary,-DMAA -DNUM_CORES=4 -DTILE_SIZE=8192)

%_maa_16K: cg.cpp m5op.o
	$(call build_binary,-DMAA -DNUM_CORES=4 -DTILE_SIZE=16384)

%_maa_16K_fp: cg.cpp m5op.o
	$(call build_binary,-DMAA -DCG_FP_ENABLE -DNUM_CORES=4 -DTILE_SIZE=16384)

%_maa_16K_virtual: cg.cpp m5op.o
	$(call build_binary,-DMAA -DMAA_VIRTUAL_GATHER -DCG_FP_ENABLE -DNUM_CORES=4 -DTILE_SIZE=16384)

%_maa_16K_bounded: cg.cpp m5op.o
	$(call build_binary,-DMAA -DMAA_VIRTUAL_GATHER -DMAA_BOUNDED_VIRTUAL_GATHER -DCG_FP_ENABLE -DNUM_CORES=4 -DTILE_SIZE=16384)

%_maa_32K: cg.cpp m5op.o
	$(call build_binary,-DMAA -DNUM_CORES=4 -DTILE_SIZE=32768)

%_maa_64K: cg.cpp m5op.o
	$(call build_binary,-DMAA -DNUM_CORES=4 -DTILE_SIZE=65536)

%_maa_4C: cg.cpp m5op.o
	$(call build_binary,-DMAA -DNUM_CORES=4 -DTILE_SIZE=16384)

%_maa_8C: cg.cpp m5op.o
	$(call build_binary,-DMAA -DNUM_CORES=8 -DTILE_SIZE=16384)

m5op.o: $(GEM5_HOME)/util/m5/build/x86/abi/x86/m5op.S
	$(CXX) $(GEM5_INCLUDE) $(CXX_FLAGS) -c $^ -o $@.tmp
	mv $@.tmp $@

clean:
	rm -f $(BASELINE_BINARIES) $(MAA_BINARIES) *.tmp
