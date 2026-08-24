/*
 * Copyright (c) 2026
 * All rights reserved.
 *
 * A standalone, finite model for indirect logical-tile read-modify-write
 * completion.  It is intentionally not connected to gem5 packet handling.
 */

#ifndef __MEM_MAA_LOGICAL_TILE_RMW_CONTRACT_HH__
#define __MEM_MAA_LOGICAL_TILE_RMW_CONTRACT_HH__

#include <array>
#include <cstddef>
#include <cstdint>
#include <limits>

namespace gem5::maa
{

class LogicalTileRmwContract
{
  public:
    static constexpr uint32_t MaxLogicalInsertions = 16 * 1024;

    enum class ResultMode : uint8_t { NoOldValue, PageBackedOldValue };
    enum class Status : uint8_t
    {
        Accepted, InvalidArgument, CapacityExceeded, UnknownOrdinal,
        PredicateAlreadyDecided, NotSelected, AlreadyIssued, MissingResultPage,
        AmbiguousAlias, StaleGeneration, WrongContext, WrongAlias,
        DuplicateReadEx, ReadExNotIssued, PayloadTooLarge, DuplicateWriteResp,
        WriteRespBeforeReadEx, WriteRespNotIssued, SequenceExhausted,
        CompletionNotClosed
    };

    struct Ticket
    {
        uint64_t generation = 0;
        uint16_t context = 0;
        uint32_t ordinal = 0;
        uint64_t alias = 0;
        uint64_t issueSequence = 0;
    };

    // Caller-owned bounded result storage. The contract owns no payload.
    struct ResultPage
    {
        uint64_t *words = nullptr;
        uint8_t *valid = nullptr;
        size_t size = 0;

        bool contains(size_t word) const
        {
            return words != nullptr && valid != nullptr && word < size;
        }
    };

    struct Limits
    {
        uint16_t contexts = 1;
        uint16_t maxLinePayloadBytes = 64;
        uint32_t maxInsertions = MaxLogicalInsertions;
    };

    explicit LogicalTileRmwContract(Limits limits, uint64_t generation,
                                    ResultMode mode)
        : limits_(limits), generation_(generation), mode_(mode)
    {
        valid_ = generation != 0 && limits.contexts != 0 &&
                 limits.maxLinePayloadBytes != 0 &&
                 limits.maxLinePayloadBytes <= 64 &&
                 limits.maxInsertions != 0 &&
                 limits.maxInsertions <= MaxLogicalInsertions;
    }

    Status insert(uint16_t context, uint64_t alias)
    {
        if (!valid_ || context >= limits_.contexts)
            return Status::InvalidArgument;
        if (selectionClosed_ || entryCount_ == limits_.maxInsertions)
            return Status::CapacityExceeded;
        entries_[entryCount_++] = {context, alias};
        return Status::Accepted;
    }

    Status decidePredicate(uint32_t ordinal, bool selected,
                           ResultPage *page = nullptr, size_t pageWord = 0)
    {
        Entry *entry = find(ordinal);
        if (!entry)
            return Status::UnknownOrdinal;
        if (entry->predicateDecided)
            return Status::PredicateAlreadyDecided;
        if (selected && mode_ == ResultMode::PageBackedOldValue &&
            (page == nullptr || !page->contains(pageWord)))
            return Status::MissingResultPage;
        entry->predicateDecided = true;
        entry->selected = selected;
        entry->page = page;
        entry->pageWord = pageWord;
        return Status::Accepted;
    }

    Status closeSelection()
    {
        for (uint32_t ordinal = 0; ordinal < entryCount_; ++ordinal)
            if (!entries_[ordinal].predicateDecided)
                return Status::CompletionNotClosed;
        selectionClosed_ = true;
        return Status::Accepted;
    }

    // Alias-only issue is provided solely to reject duplicate-index ambiguity.
    Status issueByAlias(uint16_t context, uint64_t alias, Ticket *ticket)
    {
        uint32_t match = MaxLogicalInsertions;
        for (uint32_t ordinal = 0; ordinal < entryCount_; ++ordinal) {
            if (entries_[ordinal].alias != alias)
                continue;
            if (match != MaxLogicalInsertions)
                return Status::AmbiguousAlias;
            match = ordinal;
        }
        return match == MaxLogicalInsertions ? Status::UnknownOrdinal
                                              : issue(context, match, ticket);
    }

    Status issue(uint16_t context, uint32_t ordinal, Ticket *ticket)
    {
        Entry *entry = find(ordinal);
        if (!ticket || !entry || context >= limits_.contexts)
            return !entry ? Status::UnknownOrdinal : Status::InvalidArgument;
        if (entry->context != context)
            return Status::WrongContext;
        if (!entry->predicateDecided || !entry->selected)
            return Status::NotSelected;
        if (entry->issued)
            return Status::AlreadyIssued;
        if (nextIssueSequence_ == std::numeric_limits<uint64_t>::max())
            return Status::SequenceExhausted;
        entry->issued = true;
        entry->issueSequence = ++nextIssueSequence_;
        *ticket = {generation_, context, ordinal, entry->alias,
                   entry->issueSequence};
        return Status::Accepted;
    }

    Status acceptReadEx(const Ticket &ticket, size_t payloadBytes,
                        uint64_t oldValue)
    {
        Entry *entry = validateIssued(ticket);
        if (!entry)
            return ticketStatus(ticket);
        if (payloadBytes == 0 || payloadBytes > limits_.maxLinePayloadBytes)
            return Status::PayloadTooLarge;
        if (entry->readEx)
            return Status::DuplicateReadEx;
        entry->readEx = true;
        if (mode_ == ResultMode::PageBackedOldValue) {
            entry->page->words[entry->pageWord] = oldValue;
            entry->page->valid[entry->pageWord] = 1;
        }
        return Status::Accepted;
    }

    Status acceptWriteResp(const Ticket &ticket)
    {
        Entry *entry = validateIssued(ticket);
        if (!entry)
            return ticketStatus(ticket);
        if (entry->writeResp)
            return Status::DuplicateWriteResp;
        if (!entry->readEx)
            return Status::WriteRespBeforeReadEx;
        entry->writeResp = true;
        return Status::Accepted;
    }

    bool complete() const
    {
        if (!selectionClosed_)
            return false;
        for (uint32_t ordinal = 0; ordinal < entryCount_; ++ordinal) {
            const Entry &entry = entries_[ordinal];
            if (!entry.predicateDecided)
                return false;
            if (entry.selected && (!entry.issued || !entry.readEx ||
                                   !entry.writeResp))
                return false;
        }
        return true;
    }

    size_t insertionCount() const { return entryCount_; }
    uint64_t generation() const { return generation_; }

  private:
    struct Entry
    {
        uint16_t context;
        uint64_t alias;
        bool predicateDecided = false;
        bool selected = false;
        bool issued = false;
        bool readEx = false;
        bool writeResp = false;
        ResultPage *page = nullptr;
        size_t pageWord = 0;
        uint64_t issueSequence = 0;
    };

    Entry *find(uint32_t ordinal)
    {
        return ordinal < entryCount_ ? &entries_[ordinal] : nullptr;
    }
    Entry *validateIssued(const Ticket &ticket)
    {
        Entry *entry = find(ticket.ordinal);
        if (!entry || ticket.generation != generation_ ||
            ticket.context != entry->context || ticket.alias != entry->alias ||
            !entry->issued || ticket.issueSequence != entry->issueSequence)
            return nullptr;
        return entry;
    }
    Status ticketStatus(const Ticket &ticket) const
    {
        if (ticket.generation != generation_)
            return Status::StaleGeneration;
        if (ticket.ordinal >= entryCount_)
            return Status::UnknownOrdinal;
        const Entry &entry = entries_[ticket.ordinal];
        if (ticket.context != entry.context)
            return Status::WrongContext;
        if (ticket.alias != entry.alias)
            return Status::WrongAlias;
        return entry.readEx ? Status::WriteRespNotIssued
                            : Status::ReadExNotIssued;
    }

    Limits limits_;
    uint64_t generation_;
    ResultMode mode_;
    bool valid_ = false;
    bool selectionClosed_ = false;
    uint64_t nextIssueSequence_ = 0;
    std::array<Entry, MaxLogicalInsertions> entries_{};
    uint32_t entryCount_ = 0;
};

} // namespace gem5::maa

#endif // __MEM_MAA_LOGICAL_TILE_RMW_CONTRACT_HH__
