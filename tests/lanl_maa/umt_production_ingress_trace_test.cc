#include <cassert>
#include <cstdint>
#include <limits>

#include "mem/LANLMAA/UmtOrderedWaveIngressTrace.hh"
#include "mem/LANLMAA/UmtOrderedWaveStreamState.hh"

using namespace gem5::lanlmaa;

namespace
{

UmtOrderedWaveDescriptor
descriptor(uint16_t abi, size_t groups)
{
    UmtOrderedWaveDescriptor value;
    value.abiVersion = abi;
    value.groupCount = groups;
    value.recordBase = 0x4000;
    value.recordStride = abi == UmtOrderedWaveD32DescriptorVersion ?
        UmtOrderedWaveD32PlaneStride : UmtOrderedWavePlaneStride;
    value.sumArea.fill(2.0);
    return value;
}

template <class State>
void
seedSources(State &state, const UmtOrderedWaveDescriptor &desc)
{
    assert(state.configure(desc.groupCount));
    assert(state.bindDescriptor(desc));
    for (size_t group = 0; group < desc.groupCount; ++group) {
        for (size_t corner = 0; corner < UmtOrderedWaveCorners; ++corner) {
            assert(state.writeSource(
                group, corner, umtOrderedWaveStreamEncodeFp64(1.0), 0).
                accepted);
        }
    }
}

template <class State>
void
checkSourceWrites()
{
    const auto desc = descriptor(UmtOrderedWaveD32DescriptorVersion, 1);
    State state;
    assert(state.configure(desc.groupCount));
    assert(state.bindDescriptor(desc));
    UmtOrderedWaveIngressTrace trace;
    trace.beginCallback(10);
    for (size_t source = 0; source < UmtOrderedWaveCorners; ++source) {
        const auto before = state.traceStateSnapshot();
        const auto reservation = state.writeSource(
            0, source, umtOrderedWaveStreamEncodeFp64(1.0), 10);
        assert(reservation.accepted);
        const auto after = state.traceStateSnapshot();
        UmtOrderedWaveIngressRecord record;
        record.cycle = 10;
        record.packetAddress = desc.recordBase + source * sizeof(uint64_t);
        record.lineAddress = desc.recordBase;
        record.abiVersion = desc.abiVersion;
        record.stage = source;
        record.group = 0;
        record.corner = source;
        record.waiterOrder = source;
        record.waiterCount = UmtOrderedWaveCorners;
        record.preStateDigest = before.digest;
        record.postStateDigest = after.digest;
        trace.sourceWrite(record);
    }
    trace.endCallback(11);
    assert(trace.records().size() == UmtOrderedWaveCorners);
    assert(trace.histograms().front().sourceWrites == UmtOrderedWaveCorners);
    assert(trace.histograms().front().maxLanesPerCallback ==
           UmtOrderedWaveCorners);
    for (size_t source = 0; source < UmtOrderedWaveCorners; ++source) {
        const auto &record = trace.records()[source];
        assert(record.kind == UmtOrderedWaveIngressKind::SourceWrite);
        assert(record.callbackLane == source);
        assert(record.corner == source);
        assert(record.nextEngineTick == 11);
        assert(record.preStateDigest != record.postStateDigest);
    }
}

template <class State>
void
recordAdmissions(
    State &state, UmtOrderedWaveIngressTrace &trace,
    const UmtOrderedWaveDescriptor &desc, size_t waiters, uint64_t cycle,
    size_t firstGroup = 0)
{
    trace.beginCallback(cycle);
    for (size_t order = 0; order < waiters; ++order) {
        const auto before = state.traceStateSnapshot();
        const auto reservation = state.enqueueDenominator(
            firstGroup + order, firstGroup + order, 0,
            umtOrderedWaveStreamEncodeFp64(1.0));
        assert(reservation.accepted);
        const auto after = state.traceStateSnapshot();
        UmtOrderedWaveIngressRecord record;
        record.cycle = cycle;
        record.packetAddress = desc.recordBase;
        record.lineAddress = desc.recordBase;
        record.abiVersion = desc.abiVersion;
        record.stage = UmtOrderedWaveCorners;
        record.group = firstGroup + order;
        record.corner = 0;
        record.waiterOrder = order;
        record.waiterCount = waiters;
        record.selectedToken = reservation.selectedToken;
        record.preStateDigest = before.digest;
        record.postStateDigest = after.digest;
        trace.denominatorAdmission(record);
    }
    trace.endCallback(cycle + 1);
}

template <class State>
void
checkWaiterBoundaries()
{
    const auto d32 = descriptor(UmtOrderedWaveD32DescriptorVersion, 8);
    const auto d64 = descriptor(UmtOrderedWaveD64DescriptorVersion, 8);

    for (const size_t waiters : {size_t{1}, size_t{7}, size_t{8}}) {
        State state;
        seedSources(state, d64);
        UmtOrderedWaveIngressTrace trace;
        recordAdmissions(state, trace, d64, waiters, 100 + waiters);
        assert(trace.records().size() == waiters);
        assert(trace.histograms().size() == 1);
        const auto &histogram = trace.histograms().front();
        assert(histogram.denominatorAdmissions == waiters);
        assert(histogram.maxLanesPerCallback == waiters);
        for (size_t index = 0; index < waiters; ++index) {
            const auto &record = trace.records()[index];
            assert(record.callbackSequence == 1);
            assert(record.callbackLane == index);
            assert(record.waiterOrder == index);
            assert(record.waiterCount == waiters);
            assert(record.selectedToken == index);
            assert(record.preStateDigest != record.postStateDigest);
            assert(record.nextEngineTick == 101 + waiters);
        }
    }

    // The D32 path releases a partial line, while D64 holds it until the
    // expected full eight-waiter set exists.  The observer keeps these as
    // explicit, non-interchangeable witnesses.
    UmtOrderedWaveIngressTrace trace;
    UmtOrderedWaveIngressRecord witness;
    witness.cycle = 200;
    witness.packetAddress = d32.recordBase;
    witness.lineAddress = d32.recordBase;
    witness.abiVersion = d32.abiVersion;
    witness.waiterCount = 7;
    trace.d32Release(witness);
    witness.abiVersion = d64.abiVersion;
    trace.d64Hold(witness);
    witness.waiterCount = 8;
    trace.d64Release(witness);
    assert(trace.records().size() == 3);
    assert(trace.records()[0].kind == UmtOrderedWaveIngressKind::D32Release);
    assert(trace.records()[1].kind == UmtOrderedWaveIngressKind::D64Hold);
    assert(trace.records()[2].kind == UmtOrderedWaveIngressKind::D64Release);
    assert(trace.histograms().front().d32Releases == 1);
    assert(trace.histograms().front().d64Holds == 1);
    assert(trace.histograms().front().d64Releases == 1);
}

template <class State>
void
checkSerializationNegative()
{
    const auto desc = descriptor(UmtOrderedWaveD64DescriptorVersion, 8);
    State baselineState;
    seedSources(baselineState, desc);
    UmtOrderedWaveIngressTrace baseline;
    recordAdmissions(baselineState, baseline, desc, 2, 300);

    State serialState;
    seedSources(serialState, desc);
    UmtOrderedWaveIngressTrace serialized;
    recordAdmissions(serialState, serialized, desc, 1, 300);
    recordAdmissions(serialState, serialized, desc, 1, 301, 1);

    // Serializing a two-lane response is deliberately a negative trace
    // transformation: it changes callback identity, per-callback lanes,
    // cycle histogram, and the engine-tick order witness.
    assert(baseline.records().size() == serialized.records().size());
    assert(baseline.records()[1].callbackSequence == 1);
    assert(baseline.records()[1].callbackLane == 1);
    assert(serialized.records()[1].callbackSequence == 2);
    assert(serialized.records()[1].callbackLane == 0);
    assert(baseline.records()[1].cycle != serialized.records()[1].cycle);
    assert(baseline.histograms().size() == 1);
    assert(serialized.histograms().size() == 2);
}

} // anonymous namespace

int
main()
{
    checkSourceWrites<UmtOrderedWaveStreamState>();
    checkWaiterBoundaries<UmtOrderedWaveStreamState>();
    checkSerializationNegative<UmtOrderedWaveStreamState>();
    return 0;
}
