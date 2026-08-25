#include <cassert>
#include <cstdint>
#include <iostream>

#include "gem5/maa_page_fed_soa_abi.hh"

using gem5::maa::PageFedSoaJitABI;
using gem5::maa::PageFedSoaJitState;

namespace
{

void
admitAll(PageFedSoaJitState &state, uint64_t generation)
{
    for (uint8_t page = 0; page < PageFedSoaJitABI::Pages; ++page) {
        assert(state.beginPage(generation, page) ==
               PageFedSoaJitState::Result::Accepted);
        for (uint32_t lane = 0; lane < PageFedSoaJitABI::PageElements;
             ++lane) {
            const uint32_t ordinal =
                page * PageFedSoaJitABI::PageElements + lane;
            assert(state.admitOrdinal(generation, page, ordinal) ==
                   PageFedSoaJitState::Result::Accepted);
        }
        assert(state.finishPage(generation, page) ==
               PageFedSoaJitState::Result::Accepted);
    }
}

} // anonymous namespace

int
main()
{
    static_assert(sizeof(PageFedSoaJitState) == 16);
    PageFedSoaJitABI::Command command;
    const uint64_t admit = PageFedSoaJitABI::encodeAdmit(17, 3, 29);
    assert(PageFedSoaJitABI::decode(admit, command));
    assert(command.action == PageFedSoaJitABI::Action::Admit);
    assert(command.generation == 17 && command.page == 3 &&
           command.tile == 29);
    assert(PageFedSoaJitABI::decode(
        PageFedSoaJitABI::encodeClose(17), command));
    assert(command.action == PageFedSoaJitABI::Action::Close);

    PageFedSoaJitState disabled;
    assert(disabled.open(false, 1, PageFedSoaJitABI::LogicalElements) ==
           PageFedSoaJitState::Result::Disabled);

    PageFedSoaJitState capacity;
    assert(capacity.open(true, 1,
                         PageFedSoaJitABI::LogicalElements - 1) ==
           PageFedSoaJitState::Result::Capacity);

    PageFedSoaJitState ordered;
    assert(ordered.open(true, 1, PageFedSoaJitABI::LogicalElements) ==
           PageFedSoaJitState::Result::Accepted);
    assert(ordered.beginExecution(1) ==
           PageFedSoaJitState::Result::EarlyExecution);

    PageFedSoaJitState wrong_page;
    assert(wrong_page.open(true, 2, PageFedSoaJitABI::LogicalElements) ==
           PageFedSoaJitState::Result::Accepted);
    assert(wrong_page.beginPage(2, 1) ==
           PageFedSoaJitState::Result::PageOrder);

    PageFedSoaJitState duplicate;
    assert(duplicate.open(true, 3, PageFedSoaJitABI::LogicalElements) ==
           PageFedSoaJitState::Result::Accepted);
    assert(duplicate.beginPage(3, 0) ==
           PageFedSoaJitState::Result::Accepted);
    assert(duplicate.admitOrdinal(3, 0, 0) ==
           PageFedSoaJitState::Result::Accepted);
    assert(duplicate.admitOrdinal(3, 0, 0) ==
           PageFedSoaJitState::Result::OrdinalOrder);

    PageFedSoaJitState missing;
    assert(missing.open(true, 4, PageFedSoaJitABI::LogicalElements) ==
           PageFedSoaJitState::Result::Accepted);
    assert(missing.close(4) ==
           PageFedSoaJitState::Result::MissingPages);

    PageFedSoaJitState stale;
    assert(stale.open(true, 5, PageFedSoaJitABI::LogicalElements) ==
           PageFedSoaJitState::Result::Accepted);
    assert(stale.beginPage(4, 0) ==
           PageFedSoaJitState::Result::StaleGeneration);

    PageFedSoaJitState complete;
    assert(complete.open(true, 6, PageFedSoaJitABI::LogicalElements) ==
           PageFedSoaJitState::Result::Accepted);
    admitAll(complete, 6);
    assert(complete.admitted() == PageFedSoaJitABI::LogicalElements);
    assert(complete.close(6) == PageFedSoaJitState::Result::Accepted);
    assert(complete.beginExecution(6) ==
           PageFedSoaJitState::Result::Accepted);
    assert(complete.finishExecution(6) ==
           PageFedSoaJitState::Result::Accepted);
    assert(!complete.active());
    assert(complete.open(true, 6, PageFedSoaJitABI::LogicalElements) ==
           PageFedSoaJitState::Result::StaleGeneration);

    std::cout << "PAGE_FED_SOA_JIT_STATE_TEST_PASS\n";
    return 0;
}
