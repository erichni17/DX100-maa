#ifndef SSSP_CHUNK_ADMISSION_HH
#define SSSP_CHUNK_ADMISSION_HH

#include <cstddef>
#include <cstdint>
#include <vector>

namespace sssp_chunk_admission
{

class Tracker
{
  public:
    enum Reason : uint8_t
    {
        None = 0,
        Bounds = 1U << 0,
        ActiveSource = 1U << 1,
        CrossOwner = 1U << 2,
    };

    bool reset(std::size_t chunks)
    {
        if (chunks == 0)
            return false;
        reasons.assign(chunks, None);
        return true;
    }

    bool reject(std::size_t owner, Reason reason)
    {
        if (owner >= reasons.size() || reason == None)
            return false;
        reasons[owner] |= reason;
        return true;
    }

    void rejectAll(Reason reason)
    {
        if (reason == None)
            return;
        for (auto &entry : reasons)
            entry |= reason;
    }

    bool observeDestination(std::size_t owner, bool activeSource,
                            uint32_t epoch, uint32_t &destinationEpoch,
                            uint32_t &destinationOwner)
    {
        if (owner >= reasons.size() || epoch == 0)
            return false;
        if (activeSource)
            reasons[owner] |= ActiveSource;
        if (destinationEpoch != epoch) {
            destinationEpoch = epoch;
            destinationOwner = static_cast<uint32_t>(owner);
            return true;
        }
        if (destinationOwner == owner)
            return true;
        if (destinationOwner >= reasons.size())
            return false;
        reasons[destinationOwner] |= CrossOwner;
        reasons[owner] |= CrossOwner;
        return true;
    }

    bool safe(std::size_t owner) const
    {
        return owner < reasons.size() && reasons[owner] == None;
    }

    bool hasReason(std::size_t owner, Reason reason) const
    {
        return owner < reasons.size() &&
            (reasons[owner] & reason) != 0;
    }

    std::size_t chunks() const { return reasons.size(); }

    std::size_t count(Reason reason) const
    {
        std::size_t result = 0;
        for (const auto entry : reasons)
            result += (entry & reason) != 0;
        return result;
    }

  private:
    std::vector<uint8_t> reasons;
};

} // namespace sssp_chunk_admission

#endif // SSSP_CHUNK_ADMISSION_HH
