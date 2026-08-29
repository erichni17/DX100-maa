/*
 * Copyright (c) 2026
 * All rights reserved.
 */

#ifndef __MEM_MAA_DENSE_BACKING_LINE_TRACKER_HH__
#define __MEM_MAA_DENSE_BACKING_LINE_TRACKER_HH__

#include <array>
#include <cstddef>
#include <cstdint>

namespace gem5::maa
{

class DenseBackingLineTracker
{
  public:
    static constexpr uint32_t MaxLines = 2048;
    static constexpr uint32_t PackedBytes = MaxLines / 8;

    enum class Result : uint8_t
    {
        Accepted,
        Invalid,
        Duplicate,
    };

    Result reset(uint32_t lines)
    {
        if (lines == 0 || lines > MaxLines)
            return Result::Invalid;
        words_ = {};
        lines_ = lines;
        initialized_ = 0;
        return Result::Accepted;
    }

    bool validLine(uint32_t line) const { return line < lines_; }

    bool initialized(uint32_t line) const
    {
        return validLine(line) &&
            (words_[line / 64] & (uint64_t{1} << (line % 64))) != 0;
    }

    Result acknowledge(uint32_t line)
    {
        if (!validLine(line))
            return Result::Invalid;
        if (initialized(line))
            return Result::Duplicate;
        words_[line / 64] |= uint64_t{1} << (line % 64);
        ++initialized_;
        return Result::Accepted;
    }

    uint32_t lines() const { return lines_; }
    uint32_t initializedLines() const { return initialized_; }
    bool allInitialized() const { return initialized_ == lines_; }

    static const char *resultName(Result result)
    {
        switch (result) {
          case Result::Accepted: return "accepted";
          case Result::Invalid: return "invalid";
          case Result::Duplicate: return "duplicate";
        }
        return "unknown";
    }

  private:
    uint32_t lines_ = 0;
    uint32_t initialized_ = 0;
    std::array<uint64_t, MaxLines / 64> words_{};
};

static_assert(DenseBackingLineTracker::MaxLines % 64 == 0);
static_assert(DenseBackingLineTracker::PackedBytes == 256);

} // namespace gem5::maa

#endif // __MEM_MAA_DENSE_BACKING_LINE_TRACKER_HH__
