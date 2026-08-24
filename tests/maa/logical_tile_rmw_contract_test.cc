#include <array>
#include <cstdint>
#include <cstdlib>
#include <iostream>

#include "mem/MAA/LogicalTileRmwContract.hh"

namespace {
#define CHECK(x) do { if (!(x)) { std::cerr << __FILE__ << ':' << __LINE__ \
    << ": " #x << std::endl; std::exit(1); } } while (false)
using Contract = gem5::maa::LogicalTileRmwContract;
using Status = Contract::Status;

void noResultClosesEveryLedger()
{
    Contract c({2, 64, 4}, 19, Contract::ResultMode::NoOldValue);
    CHECK(c.insert(0, 8) == Status::Accepted);
    // Duplicate aliases retain ordinal order.
    CHECK(c.insert(1, 8) == Status::Accepted);
    CHECK(c.insert(0, 9) == Status::Accepted);
    CHECK(c.issueByAlias(0, 8, nullptr) == Status::AmbiguousAlias);
    CHECK(c.decidePredicate(0, true) == Status::Accepted);
    CHECK(c.decidePredicate(1, false) == Status::Accepted);
    CHECK(c.decidePredicate(2, true) == Status::Accepted);
    CHECK(!c.complete());
    CHECK(c.closeSelection() == Status::Accepted);
    Contract::Ticket a, b;
    CHECK(c.issue(0, 0, &a) == Status::Accepted);
    CHECK(a.ordinal == 0 && a.alias == 8 && a.issueSequence == 1);
    CHECK(c.issue(0, 2, &b) == Status::Accepted);
    CHECK(b.ordinal == 2 && b.issueSequence == 2);
    CHECK(c.acceptWriteResp(a) == Status::WriteRespBeforeReadEx);
    CHECK(c.acceptReadEx(a, 65, 1) == Status::PayloadTooLarge);
    CHECK(c.acceptReadEx(a, 64, 1) == Status::Accepted);
    CHECK(c.acceptReadEx(a, 64, 1) == Status::DuplicateReadEx);
    CHECK(c.acceptWriteResp(a) == Status::Accepted);
    CHECK(!c.complete());
    CHECK(c.acceptReadEx(b, 1, 2) == Status::Accepted);
    CHECK(c.acceptWriteResp(b) == Status::Accepted);
    CHECK(c.complete());
}

void oldValueRequiresAndPublishesPage()
{
    Contract c({1, 32, 2}, 42, Contract::ResultMode::PageBackedOldValue);
    std::array<uint64_t, 2> words{};
    std::array<uint8_t, 2> valid{};
    Contract::ResultPage page{words.data(), valid.data(), words.size()};
    CHECK(c.insert(0, 22) == Status::Accepted);
    CHECK(c.decidePredicate(0, true) == Status::MissingResultPage);
    CHECK(c.decidePredicate(0, true, &page, 1) == Status::Accepted);
    CHECK(c.closeSelection() == Status::Accepted);
    Contract::Ticket t;
    CHECK(c.issue(0, 0, &t) == Status::Accepted);
    CHECK(c.acceptReadEx(t, 8, 0xabcdef) == Status::Accepted);
    CHECK(page.valid[1] && page.words[1] == 0xabcdef);
    CHECK(c.acceptWriteResp(t) == Status::Accepted);
    CHECK(c.complete());
}

void rejectsStaleAndDuplicateResponses()
{
    Contract c({1, 64, 1}, 7, Contract::ResultMode::NoOldValue);
    CHECK(c.insert(0, 3) == Status::Accepted);
    CHECK(c.decidePredicate(0, true) == Status::Accepted);
    CHECK(c.closeSelection() == Status::Accepted);
    Contract::Ticket t;
    CHECK(c.issue(0, 0, &t) == Status::Accepted);
    auto stale = t; stale.generation++;
    CHECK(c.acceptReadEx(stale, 8, 1) == Status::StaleGeneration);
    auto wrong_alias = t; wrong_alias.alias++;
    CHECK(c.acceptReadEx(wrong_alias, 8, 1) == Status::WrongAlias);
    auto old_issue = t; old_issue.issueSequence++;
    CHECK(c.acceptReadEx(old_issue, 8, 1) == Status::ReadExNotIssued);
    CHECK(c.acceptReadEx(t, 8, 1) == Status::Accepted);
    CHECK(c.acceptWriteResp(t) == Status::Accepted);
    CHECK(c.acceptWriteResp(t) == Status::DuplicateWriteResp);
}

void boundsAndSelectionAreFailClosed()
{
    Contract invalid({0, 64, 1}, 1, Contract::ResultMode::NoOldValue);
    CHECK(invalid.insert(0, 1) == Status::InvalidArgument);
    Contract zeroGeneration({1, 64, 1}, 0,
                            Contract::ResultMode::NoOldValue);
    CHECK(zeroGeneration.insert(0, 1) == Status::InvalidArgument);
    Contract oversizedLine({1, 65, 1}, 1,
                           Contract::ResultMode::NoOldValue);
    CHECK(oversizedLine.insert(0, 1) == Status::InvalidArgument);
    Contract c({1, 64, Contract::MaxLogicalInsertions}, 1,
               Contract::ResultMode::NoOldValue);
    for (uint32_t i = 0; i < Contract::MaxLogicalInsertions; ++i)
        CHECK(c.insert(0, i) == Status::Accepted);
    CHECK(c.insert(0, 99) == Status::CapacityExceeded);
    CHECK(c.closeSelection() == Status::CompletionNotClosed);
}
}

int main()
{
    noResultClosesEveryLedger();
    oldValueRequiresAndPublishesPage();
    rejectsStaleAndDuplicateResponses();
    boundsAndSelectionAreFailClosed();
    return 0;
}
