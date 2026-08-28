/*
 * Copyright (c) 2026
 * All rights reserved.
 */

#ifndef __MEM_MAA_VIRTUAL_RETIREMENT_SCOREBOARD_HH__
#define __MEM_MAA_VIRTUAL_RETIREMENT_SCOREBOARD_HH__

#include <array>
#include <cstddef>
#include <cstdint>
#include <limits>

namespace gem5::maa
{

class VirtualRetirementScoreboard
{
  public:
    // Existing virtual-tile experiments use up to 64 write credits.  The
    // configured capacity, rather than this compatibility ceiling, determines
    // the modeled hardware state.
    static constexpr uint32_t MaxEntries = 64;
    static constexpr uint32_t MaxPagesPerEntry = 2;
    // This is a packed semantic charge, not sizeof(Entry).  The exact
    // transaction consumes one non-recycled 64-bit tag per live entry, plus
    // one fixed 64-bit allocator per scoreboard.  Allocation fails closed at
    // exhaustion instead of wrapping into an identity that a delayed ACK may
    // still carry.
    static constexpr uint32_t ConservativeBytesPerEntry =
        1 + sizeof(uint64_t) + sizeof(uint64_t) + sizeof(uint64_t) +
        sizeof(int32_t) + sizeof(uint16_t) + sizeof(uint8_t) +
        MaxPagesPerEntry * (sizeof(int32_t) + sizeof(uint16_t));
    static constexpr uint32_t ConservativeFixedBytes = sizeof(uint64_t);
    static constexpr uint32_t ConservativeTotalBytes =
        MaxEntries * ConservativeBytesPerEntry + ConservativeFixedBytes;

    enum class Result : uint8_t
    {
        Accepted,
        Busy,
        Invalid,
        Full,
        Duplicate,
        NotFound,
        WrongAddress,
        WrongGeneration,
        WrongTransaction,
        Exhausted,
    };

    struct Identity
    {
        uint64_t address = 0;
        uint64_t generation = 0;
        uint64_t transaction = 0;
    };

    struct PageWords
    {
        int32_t page = -1;
        uint16_t words = 0;
    };

    struct Metadata
    {
        uint64_t generation = 0;
        int32_t backingLine = -1;
        uint16_t backingWordMask = 0;
        uint8_t pageCount = 0;
        std::array<PageWords, MaxPagesPerEntry> pageWords{};
    };

    Result reset(uint32_t capacity)
    {
        if (!empty())
            return Result::Busy;
        if (capacity == 0 || capacity > MaxEntries)
            return Result::Invalid;
        entries_ = {};
        capacity_ = capacity;
        size_ = 0;
        return Result::Accepted;
    }

    Result insert(uint64_t key, const Metadata &metadata, Identity &identity)
    {
        identity = {};
        if (!valid(metadata))
            return Result::Invalid;
        if (contains(key))
            return Result::Duplicate;
        if (full())
            return Result::Full;
        if (nextTransaction_ == 0)
            return Result::Exhausted;
        for (uint32_t index = 0; index < capacity_; ++index) {
            if (entries_[index].valid)
                continue;
            entries_[index].valid = true;
            entries_[index].key = key;
            entries_[index].transaction = nextTransaction_;
            entries_[index].metadata = metadata;
            identity = {key, metadata.generation, nextTransaction_};
            if (nextTransaction_ ==
                std::numeric_limits<uint64_t>::max()) {
                nextTransaction_ = 0;
            } else {
                ++nextTransaction_;
            }
            ++size_;
            return Result::Accepted;
        }
        return Result::Full;
    }

    const Metadata *find(uint64_t key) const
    {
        for (uint32_t index = 0; index < capacity_; ++index) {
            const auto &entry = entries_[index];
            if (entry.valid && entry.key == key)
                return &entry.metadata;
        }
        return nullptr;
    }

    Result take(const Identity &identity, Metadata &metadata)
    {
        if (identity.generation == 0 || identity.transaction == 0)
            return Result::Invalid;
        for (uint32_t index = 0; index < capacity_; ++index) {
            auto &entry = entries_[index];
            if (!entry.valid || entry.transaction != identity.transaction)
                continue;
            if (entry.key != identity.address)
                return Result::WrongAddress;
            if (entry.metadata.generation != identity.generation)
                return Result::WrongGeneration;
            metadata = entry.metadata;
            entry = {};
            --size_;
            return Result::Accepted;
        }
        if (contains(identity.address))
            return Result::WrongTransaction;
        return Result::NotFound;
    }

    bool contains(uint64_t key) const { return find(key) != nullptr; }
    bool empty() const { return size_ == 0; }
    bool full() const { return size_ == capacity_; }
    uint32_t size() const { return size_; }
    uint32_t capacity() const { return capacity_; }

    static const char *resultName(Result result)
    {
        switch (result) {
          case Result::Accepted: return "accepted";
          case Result::Busy: return "busy";
          case Result::Invalid: return "invalid";
          case Result::Full: return "full";
          case Result::Duplicate: return "duplicate";
          case Result::NotFound: return "not_found";
          case Result::WrongAddress: return "wrong_address";
          case Result::WrongGeneration: return "wrong_generation";
          case Result::WrongTransaction: return "wrong_transaction";
          case Result::Exhausted: return "exhausted";
        }
        return "unknown";
    }

  private:
    struct Entry
    {
        bool valid = false;
        uint64_t key = 0;
        uint64_t transaction = 0;
        Metadata metadata{};
    };

    static bool valid(const Metadata &metadata)
    {
        if (metadata.generation == 0 || metadata.backingLine < 0 ||
            metadata.backingWordMask == 0 || metadata.pageCount == 0 ||
            metadata.pageCount > MaxPagesPerEntry)
            return false;
        for (uint32_t page = 0; page < metadata.pageCount; ++page) {
            if (metadata.pageWords[page].page < 0 ||
                metadata.pageWords[page].words == 0)
                return false;
        }
        return true;
    }

    uint32_t capacity_ = MaxEntries;
    uint32_t size_ = 0;
    uint64_t nextTransaction_ = 1;
    std::array<Entry, MaxEntries> entries_{};
};

} // namespace gem5::maa

#endif // __MEM_MAA_VIRTUAL_RETIREMENT_SCOREBOARD_HH__
