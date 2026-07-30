#ifndef __MEM_LANLMAA_LINE_TABLE_GEOMETRY_HH__
#define __MEM_LANLMAA_LINE_TABLE_GEOMETRY_HH__

#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <vector>

namespace gem5
{
namespace lanlmaa
{

enum class LineBankAccess
{
    DistinctLine,
    SameLine,
    Conflict,
    Invalid
};

/**
 * Physical organization and per-cycle port arbitration for the line-merge
 * table. Entries are laid out as contiguous ways within each bank. A bank can
 * resolve one distinct coherent-line address per cycle; a second request for
 * that same line reuses the first lookup and may join its waiter list.
 */
class LineTableGeometry
{
  private:
    size_t entryCount;
    size_t bankCount;
    size_t bytesPerLine;
    std::vector<uint64_t> accessedLines;
    std::vector<bool> bankAccessed;

  public:
    LineTableGeometry(
        size_t entries = 32, size_t banks = 4, size_t lineBytes = 64)
        : entryCount(entries), bankCount(banks), bytesPerLine(lineBytes),
          accessedLines(banks), bankAccessed(banks)
    {
    }

    bool valid() const
    {
        return entryCount > 0 && bankCount > 0 && bankCount <= entryCount &&
               (bankCount & (bankCount - 1)) == 0 &&
               entryCount % bankCount == 0 && bytesPerLine > 0 &&
               (bytesPerLine & (bytesPerLine - 1)) == 0;
    }

    size_t banks() const { return bankCount; }

    size_t ways() const
    {
        return valid() ? entryCount / bankCount : 0;
    }

    size_t bank(uint64_t lineAddress) const
    {
        return valid() ? (lineAddress / bytesPerLine) & (bankCount - 1) : 0;
    }

    size_t begin(size_t bank) const
    {
        return valid() && bank < bankCount ? bank * ways() : entryCount;
    }

    size_t end(size_t bank) const
    {
        return valid() && bank < bankCount ? begin(bank) + ways() : entryCount;
    }

    void beginCycle()
    {
        std::fill(bankAccessed.begin(), bankAccessed.end(), false);
    }

    LineBankAccess access(uint64_t lineAddress)
    {
        if (!valid() || lineAddress % bytesPerLine != 0) {
            return LineBankAccess::Invalid;
        }

        const size_t targetBank = bank(lineAddress);
        if (!bankAccessed[targetBank]) {
            bankAccessed[targetBank] = true;
            accessedLines[targetBank] = lineAddress;
            return LineBankAccess::DistinctLine;
        }
        if (accessedLines[targetBank] == lineAddress) {
            return LineBankAccess::SameLine;
        }
        return LineBankAccess::Conflict;
    }
};

} // namespace lanlmaa
} // namespace gem5

#endif // __MEM_LANLMAA_LINE_TABLE_GEOMETRY_HH__
