#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <iostream>

#include "mem/MAA/DirectProducerResultHandoff.hh"

using gem5::DirectProducerResultHandoff;

namespace {

#define CHECK(condition)                                                     \
    do {                                                                     \
        if (!(condition)) {                                                  \
            std::cerr << __FILE__ << ':' << __LINE__                         \
                      << ": check failed: " #condition << std::endl;         \
            std::exit(1);                                                    \
        }                                                                    \
    } while (false)

DirectProducerResultHandoff::ProducerDescriptor
producer()
{
    DirectProducerResultHandoff::ProducerDescriptor result;
    result.generation = 9;
    result.tokenTile = 3;
    result.logicalElements = DirectProducerResultHandoff::LogicalElements;
    result.rows = DirectProducerResultHandoff::ProducerRows;
    result.rowOffsets = DirectProducerResultHandoff::ProducerRowOffsets;
    result.wordBytes = DirectProducerResultHandoff::WordBytes;
    result.isVirtualGather = true;
    result.completionOnlyToken = true;
    return result;
}

DirectProducerResultHandoff::ConsumerDescriptor
consumer()
{
    DirectProducerResultHandoff::ConsumerDescriptor result;
    result.generation = 9;
    result.tokenTile = 3;
    result.scalarBits = DirectProducerResultHandoff::ScalarThreeBits;
    result.destinationAddress = 0x400000;
    result.destinationRangeMin = 0x400000;
    result.destinationRangeMax = 0x420000;
    result.destinationRangeID = 7;
    result.isFP64MultiplyStore = true;
    return result;
}

DirectProducerResultHandoff::ProducerWord
word(uint16_t logical, double value)
{
    DirectProducerResultHandoff::ProducerWord result;
    result.generation = 9;
    result.row = logical / DirectProducerResultHandoff::ProducerRowOffsets;
    result.offset = logical % DirectProducerResultHandoff::ProducerRowOffsets;
    std::memcpy(result.payload.data(), &value, sizeof(value));
    return result;
}

void
fillLine(DirectProducerResultHandoff &handoff, uint16_t line,
         bool reverse = false)
{
    for (uint8_t count = 0; count < DirectProducerResultHandoff::WordsPerLine;
         ++count) {
        const uint8_t within = reverse
            ? DirectProducerResultHandoff::WordsPerLine - 1 - count : count;
        const uint16_t logical =
            line * DirectProducerResultHandoff::WordsPerLine + within;
        CHECK(handoff.acceptProducerWord(word(logical, logical + 0.25)));
    }
}

double
payloadWord(const DirectProducerResultHandoff &handoff, uint8_t buffer,
            uint8_t wordInLine)
{
    double value = 0.0;
    std::memcpy(&value, handoff.payload(buffer) +
                            wordInLine * sizeof(value), sizeof(value));
    return value;
}

void
completeALU(DirectProducerResultHandoff &handoff, uint16_t expectedLine)
{
    const auto request = handoff.pendingALU();
    CHECK(request.line == expectedLine);
    CHECK(handoff.acceptALU(request));
    auto bad = request;
    bad.transactionID++;
    CHECK(!handoff.completeALU(bad));
    CHECK(handoff.completeALU(request));
}

void
testRendezvousFallbacks()
{
    DirectProducerResultHandoff handoff;
    auto bad = consumer();
    bad.scalarBits++;
    CHECK(handoff.rendezvous(producer(), bad) ==
          DirectProducerResultHandoff::SubmitResult::Fallback);
    bad = consumer();
    bad.tokenTile++;
    CHECK(handoff.rendezvous(producer(), bad) ==
          DirectProducerResultHandoff::SubmitResult::Fallback);
    bad = consumer();
    bad.destinationAddress += 8;
    CHECK(handoff.rendezvous(producer(), bad) ==
          DirectProducerResultHandoff::SubmitResult::Fallback);
    CHECK(handoff.rendezvous(producer(), consumer()) ==
          DirectProducerResultHandoff::SubmitResult::Accepted);
    CHECK(handoff.rendezvous(producer(), consumer()) ==
          DirectProducerResultHandoff::SubmitResult::Busy);
}

void
testActualWordsGateALUAndOrderedStoreAcks()
{
    DirectProducerResultHandoff handoff;
    CHECK(handoff.rendezvous(producer(), consumer()) ==
          DirectProducerResultHandoff::SubmitResult::Accepted);
    // A later producer line may arrive first, but its C store cannot pass
    // missing earlier destination lines.
    CHECK(handoff.reserveProducerLine(1));
    CHECK(handoff.acceptProducerWord(word(8, 8.25)));
    CHECK(handoff.pendingALU().line == DirectProducerResultHandoff::Lines);
    CHECK(!handoff.acceptProducerWord(word(8, 8.25))); // duplicate response
    for (uint8_t index = 1; index < DirectProducerResultHandoff::WordsPerLine;
         ++index)
        CHECK(handoff.acceptProducerWord(word(8 + index, 8 + index + 0.25)));
    completeALU(handoff, 1);
    CHECK(handoff.pendingStore().line == DirectProducerResultHandoff::Lines);

    CHECK(handoff.reserveProducerLine(0));
    for (uint8_t index = 0;
         index + 1 < DirectProducerResultHandoff::WordsPerLine; ++index)
        CHECK(handoff.acceptProducerWord(word(index, index + 0.25)));
    CHECK(handoff.pendingALU().line == DirectProducerResultHandoff::Lines);
    CHECK(handoff.acceptProducerWord(word(7, 7.25)));
    completeALU(handoff, 0);
    const auto store0 = handoff.pendingStore();
    CHECK(store0.line == 0);
    CHECK(store0.address == consumer().destinationAddress);
    CHECK(handoff.acceptStore(store0));
    auto badAck = store0;
    badAck.transactionID++;
    CHECK(!handoff.completeStoreAck(badAck));
    CHECK(handoff.completeStoreAck(store0));
    CHECK(handoff.nextDestinationLine() == 1);

    const auto store1 = handoff.pendingStore();
    CHECK(store1.line == 1);
    for (uint8_t index = 0; index < DirectProducerResultHandoff::WordsPerLine;
         ++index)
        CHECK(std::fabs(payloadWord(handoff, store1.buffer, index) -
                        3.0 * (8 + index + 0.25)) < 1e-12);
    CHECK(handoff.acceptStore(store1));
    CHECK(handoff.completeStoreAck(store1));
    CHECK(!handoff.complete());
    CHECK(handoff.assertInvariants());
}

void
testCreditBoundAndFullTerminalClosure()
{
    DirectProducerResultHandoff handoff;
    CHECK(handoff.rendezvous(producer(), consumer()) ==
          DirectProducerResultHandoff::SubmitResult::Accepted);
    for (uint16_t line = 0; line < DirectProducerResultHandoff::PayloadCredits;
         ++line)
        CHECK(handoff.reserveProducerLine(line));
    CHECK(handoff.creditsInUse() ==
          DirectProducerResultHandoff::PayloadCredits);
    CHECK(!handoff.reserveProducerLine(
        DirectProducerResultHandoff::PayloadCredits));

    // Every line needs all eight actual producer words, an ALU completion,
    // and an exact destination WriteResp before a credit returns.
    for (uint16_t line = 0; line < DirectProducerResultHandoff::Lines;
         ++line) {
        if (line >= DirectProducerResultHandoff::PayloadCredits)
            CHECK(handoff.reserveProducerLine(line));
        fillLine(handoff, line, (line & 1) != 0);
        const auto alu = handoff.pendingALU();
        CHECK(alu.line == line);
        CHECK(handoff.acceptALU(alu));
        CHECK(handoff.completeALU(alu));
        const auto store = handoff.pendingStore();
        CHECK(store.line == line);
        CHECK(handoff.acceptStore(store));
        CHECK(handoff.completeStoreAck(store));
    }
    CHECK(handoff.complete());
    CHECK(handoff.storesAcked() == DirectProducerResultHandoff::Lines);
    CHECK(handoff.creditsInUse() == 0);
    CHECK(handoff.assertInvariants());
}

void
testCostAccounting()
{
    CHECK(DirectProducerResultHandoff::chargedPayloadBytes() == 1024);
    CHECK(DirectProducerResultHandoff::chargedControlBytes() > 0);
    CHECK(DirectProducerResultHandoff::chargedTotalBytes() ==
          DirectProducerResultHandoff::chargedPayloadBytes() +
              DirectProducerResultHandoff::chargedControlBytes());
}

} // anonymous namespace

int
main()
{
    testRendezvousFallbacks();
    testActualWordsGateALUAndOrderedStoreAcks();
    testCreditBoundAndFullTerminalClosure();
    testCostAccounting();
    std::cout << "direct producer result handoff tests passed\n";
    return 0;
}
