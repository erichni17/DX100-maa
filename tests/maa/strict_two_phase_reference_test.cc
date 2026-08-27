#include <cstdint>
#include <cstdlib>
#include <iostream>

#include "gem5/maa_page_fed_soa_abi.hh"
#include "mem/MAA/StrictTwoPhaseReference.hh"

namespace
{

#define CHECK(expression)                                                    \
    do {                                                                     \
        if (!(expression)) {                                                 \
            std::cerr << __FILE__ << ':' << __LINE__ << ": "               \
                      << #expression << std::endl;                           \
            std::exit(1);                                                    \
        }                                                                    \
    } while (false)

using Reference = gem5::maa::StrictTwoPhaseReference;
using Result = Reference::Result;
using PageState = gem5::maa::PageFedSoaJitState;
using PageResult = PageState::Result;
using ABI = gem5::maa::PageFedSoaJitABI;

void
rejectsOversizedPhysicalState()
{
    Reference feeder;
    CHECK(feeder.begin(true, 0, 1, 0, 7, 16384, 4096, 4097, 128, 4,
                       0x1000, 10) ==
          Result::FeederTooLarge);
    Reference result;
    CHECK(result.begin(true, 0, 1, 0, 7, 16384, 4096, 64, 4097, 4,
                       0x1000, 10) ==
          Result::ResultCapacityTooLarge);
}

void
directReferenceActivelyRejectsEarlyA()
{
    Reference reference;
    CHECK(reference.begin(true, 2, 9, 1, 17, 16384, 4096, 64, 384, 4,
                          0x2000, 100) == Result::Accepted);
    CHECK(reference.aIssue(101) == Result::EarlyAIssue);
    for (uint32_t line = 0; line < Reference::ExpectedBFetchLines; ++line) {
        CHECK(reference.bFetchIssue(110 + line, 64) == Result::Accepted);
        CHECK(reference.bFetchResponse(1200 + line) == Result::Accepted);
    }
    for (uint32_t word = 0; word < Reference::LogicalElements; ++word)
        CHECK(reference.descriptorInsert(2300 + word) == Result::Accepted);
    CHECK(reference.closeAdmission(20000) == Result::Accepted);
    CHECK(reference.consumerBegin(20001) == Result::Accepted);
    for (uint32_t line = 0; line < Reference::ExpectedBFetchLines; ++line) {
        CHECK(reference.aIssue(20100 + line) == Result::Accepted);
        CHECK(reference.aResponse(21200 + line) == Result::Accepted);
        CHECK(reference.backingIssue(22300 + line, 64, 64) ==
              Result::Accepted);
        CHECK(reference.backingAck(23400 + line) == Result::Accepted);
    }
    for (uint32_t page = 0; page < 4; ++page)
        CHECK(reference.pageReady(24500 + page) == Result::Accepted);
    CHECK(reference.producerComplete(25000) == Result::Accepted);
    CHECK(reference.consumerEnd(25100) == Result::Accepted);
    CHECK(reference.record().aFirstIssueTick >=
          reference.record().rowOffsetLastInsertTick);
}

void
pageFedPacketFenceRejectsEarlyA()
{
    PageState early;
    CHECK(early.open(true, 3, ABI::LogicalElements) == PageResult::Accepted);
    // This is the negative packet-boundary gate used by serviceSoaJitBuild.
    CHECK(early.authorizeAIssue(3) == PageResult::EarlyExecution);
    CHECK(early.failed());

    PageState complete;
    CHECK(complete.open(true, 4, ABI::LogicalElements) ==
          PageResult::Accepted);
    for (uint8_t page = 0; page < ABI::Pages; ++page) {
        CHECK(complete.beginPage(4, page) == PageResult::Accepted);
        for (uint32_t lane = 0; lane < ABI::PageElements; ++lane) {
            const uint32_t ordinal = page * ABI::PageElements + lane;
            CHECK(complete.admitOrdinal(4, page, ordinal) ==
                  PageResult::Accepted);
        }
        CHECK(complete.finishPage(4, page) == PageResult::Accepted);
    }
    CHECK(complete.authorizeAIssue(4) == PageResult::EarlyExecution);
    CHECK(complete.failed());

    PageState issued;
    CHECK(issued.open(true, 5, ABI::LogicalElements) == PageResult::Accepted);
    for (uint8_t page = 0; page < ABI::Pages; ++page) {
        CHECK(issued.beginPage(5, page) == PageResult::Accepted);
        for (uint32_t lane = 0; lane < ABI::PageElements; ++lane)
            CHECK(issued.admitOrdinal(
                      5, page, page * ABI::PageElements + lane) ==
                  PageResult::Accepted);
        CHECK(issued.finishPage(5, page) == PageResult::Accepted);
    }
    CHECK(issued.close(5) == PageResult::Accepted);
    CHECK(issued.beginExecution(5) == PageResult::Accepted);
    CHECK(issued.authorizeAIssue(5) == PageResult::Accepted);
}

} // anonymous namespace

int
main()
{
    rejectsOversizedPhysicalState();
    directReferenceActivelyRejectsEarlyA();
    pageFedPacketFenceRejectsEarlyA();
    return 0;
}
