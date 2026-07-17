#include <cassert>

#include "mem/MAA/ChainedConsumerProfiler.hh"

using gem5::ChainedConsumerProfiler;

int
main()
{
    using Stage = ChainedConsumerProfiler::Stage;

    ChainedConsumerProfiler profiler(true, 8, 16);
    profiler.declareProducer(2, Stage::IndirectLoad);
    profiler.produce(2, Stage::IndirectLoad, 3, true, 10);
    profiler.produce(2, Stage::IndirectLoad, 0, true, 12);
    profiler.produce(2, Stage::IndirectLoad, 1, false, 13);
    profiler.declareConsumer(2, Stage::Alu);
    profiler.consume(2, Stage::Alu, 0, true, 20);
    profiler.consume(2, Stage::Alu, 1, false, 21);
    profiler.consume(2, Stage::Alu, 3, true, 25);
    auto load_alu = profiler.finishConsumer(2, Stage::Alu);
    assert(load_alu.has_value());
    assert(load_alu->logicalElements == 3);
    assert(load_alu->enabledElements == 2);
    assert(load_alu->skippedElements == 1);
    assert(load_alu->enabledConsumed == 2);
    assert(load_alu->skippedConsumed == 1);
    assert(load_alu->maxLiveValues == 2);
    assert(load_alu->maxLiveSpan == 4);
    assert(load_alu->readyOrderRegressions == 1);
    assert(load_alu->liveValueTicks == 23);
    assert(!load_alu->incomplete);

    profiler.declareProducer(4, Stage::Alu);
    profiler.declareConsumer(4, Stage::IndirectRmw);
    profiler.produce(4, Stage::Alu, 0, true, 100);
    profiler.produce(4, Stage::Alu, 1, true, 101);
    profiler.consume(4, Stage::IndirectRmw, 0, true, 110);
    auto alu_rmw = profiler.finishConsumer(4, Stage::IndirectRmw);
    assert(alu_rmw.has_value());
    assert(alu_rmw->maxLiveValues == 2);
    assert(alu_rmw->incomplete);

    ChainedConsumerProfiler disabled(false, 8, 16);
    disabled.declareProducer(1, Stage::IndirectLoad);
    disabled.produce(1, Stage::IndirectLoad, 0, true, 1);
    disabled.declareConsumer(1, Stage::Alu);
    assert(!disabled.finishConsumer(1, Stage::Alu).has_value());

    ChainedConsumerProfiler fanout(true, 8, 16);
    fanout.declareProducer(1, Stage::IndirectLoad);
    fanout.declareConsumer(1, Stage::Alu);
    fanout.declareConsumer(1, Stage::Alu);
    assert(fanout.unsupportedFanouts() == 1);
    assert(!fanout.finishConsumer(1, Stage::Alu).has_value());

    return 0;
}
