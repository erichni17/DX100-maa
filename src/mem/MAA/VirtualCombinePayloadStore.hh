#ifndef __MEM_MAA_VIRTUAL_COMBINE_PAYLOAD_STORE_HH__
#define __MEM_MAA_VIRTUAL_COMBINE_PAYLOAD_STORE_HH__

#include <array>
#include <cstddef>
#include <cstdint>
#include <limits>
#include <vector>

namespace gem5
{

/**
 * Fixed-capacity useful-word storage for virtual destination combining.
 *
 * Line tags retain only WordRef values and a valid-word mask.  Payload words
 * live here in a single configured pool, so adding line tags does not add
 * payload capacity.  A generation is encoded in each simulator reference to
 * make stale, duplicate, and double-free bugs fail closed during development;
 * it is not a claim about a synthesized reference encoding.
 */
class VirtualCombinePayloadStore
{
  public:
    static constexpr size_t MaxWordBytes = 8;
    static constexpr size_t MaxLineWords = 16;
    using WordRef = uint32_t;
    static constexpr WordRef InvalidWord =
        std::numeric_limits<WordRef>::max();
    using LineRefs = std::array<WordRef, MaxLineWords>;
    using LineData = std::array<uint8_t, 64>;

    enum class Result : uint8_t
    {
        Ok,
        Exhausted,
        Busy,
        InvalidCapacity,
        InvalidWordBytes,
        InvalidData,
        InvalidReference,
        DuplicateReference,
        DoubleFree,
    };

    static const char *
    resultName(Result result)
    {
        switch (result) {
          case Result::Ok:
            return "ok";
          case Result::Exhausted:
            return "exhausted";
          case Result::Busy:
            return "busy";
          case Result::InvalidCapacity:
            return "invalid-capacity";
          case Result::InvalidWordBytes:
            return "invalid-word-bytes";
          case Result::InvalidData:
            return "invalid-data";
          case Result::InvalidReference:
            return "invalid-reference";
          case Result::DuplicateReference:
            return "duplicate-reference";
          case Result::DoubleFree:
            return "double-free";
        }
        return "unknown";
    }

    static LineRefs
    emptyLineRefs()
    {
        LineRefs refs;
        refs.fill(InvalidWord);
        return refs;
    }

    Result
    reset(size_t capacity)
    {
        if (usedWords != 0)
            return Result::Busy;
        if (capacity == 0 || capacity >= (WordRef(1) << 31))
            return Result::InvalidCapacity;

        indexBits = 0;
        size_t encoded_values = 1;
        while (encoded_values < capacity) {
            encoded_values <<= 1;
            ++indexBits;
        }
        if (indexBits == 0)
            indexBits = 1;
        indexMask = (WordRef(1) << indexBits) - 1;
        generationMask =
            (WordRef(1) << (32 - indexBits)) - 1;

        payload.assign(capacity, {});
        allocated.assign(capacity, 0);
        generation.assign(capacity, 0);
        freeList.resize(capacity);
        for (size_t i = 0; i < capacity; ++i)
            freeList[i] = static_cast<uint32_t>(capacity - i - 1);
        usedWords = 0;
        return Result::Ok;
    }

    Result
    allocate(const uint8_t *data, size_t word_bytes, WordRef &ref)
    {
        if (!validWordBytes(word_bytes))
            return Result::InvalidWordBytes;
        if (data == nullptr)
            return Result::InvalidData;
        if (ref != InvalidWord)
            return Result::DuplicateReference;
        if (freeList.empty())
            return Result::Exhausted;

        const uint32_t index = freeList.back();
        freeList.pop_back();
        uint32_t next_generation = (generation[index] + 1) & generationMask;
        if (next_generation == 0)
            next_generation = 1;
        generation[index] = next_generation;
        allocated[index] = 1;
        payload[index].fill(0);
        for (size_t byte = 0; byte < word_bytes; ++byte)
            payload[index][byte] = data[byte];
        ref = encode(index, next_generation);
        ++usedWords;
        return Result::Ok;
    }

    Result
    update(WordRef ref, const uint8_t *data, size_t word_bytes)
    {
        if (!validWordBytes(word_bytes))
            return Result::InvalidWordBytes;
        if (data == nullptr)
            return Result::InvalidData;
        uint32_t index = 0;
        const Result checked = validate(ref, index);
        if (checked != Result::Ok)
            return checked;
        payload[index].fill(0);
        for (size_t byte = 0; byte < word_bytes; ++byte)
            payload[index][byte] = data[byte];
        return Result::Ok;
    }

    const uint8_t *
    data(WordRef ref) const
    {
        uint32_t index = 0;
        if (validate(ref, index) != Result::Ok)
            return nullptr;
        return payload[index].data();
    }

    Result
    copyLine(const LineRefs &refs, uint16_t mask, size_t word_bytes,
             LineData &line) const
    {
        if (!validWordBytes(word_bytes))
            return Result::InvalidWordBytes;
        const size_t words_per_line = line.size() / word_bytes;
        if (words_per_line > refs.size() ||
            (words_per_line < refs.size() && (mask >> words_per_line) != 0))
            return Result::InvalidReference;

        line.fill(0);
        for (size_t word = 0; word < words_per_line; ++word) {
            if ((mask & (uint16_t(1) << word)) == 0)
                continue;
            uint32_t index = 0;
            const Result checked = validate(refs[word], index);
            if (checked != Result::Ok)
                return checked;
            for (size_t prior = 0; prior < word; ++prior) {
                if ((mask & (uint16_t(1) << prior)) != 0 &&
                    refs[prior] == refs[word])
                    return Result::DuplicateReference;
            }
            for (size_t byte = 0; byte < word_bytes; ++byte)
                line[word * word_bytes + byte] = payload[index][byte];
        }
        return Result::Ok;
    }

    Result
    release(WordRef &ref)
    {
        if (ref == InvalidWord)
            return Result::DoubleFree;
        uint32_t index = 0;
        const Result checked = validate(ref, index);
        if (checked != Result::Ok)
            return checked;
        return releaseValidated(ref, index);
    }

    Result
    releaseMasked(LineRefs &refs, uint16_t mask)
    {
        std::array<uint32_t, MaxLineWords> indices{};
        for (size_t word = 0; word < refs.size(); ++word) {
            if ((mask & (uint16_t(1) << word)) == 0)
                continue;
            const Result checked = validate(refs[word], indices[word]);
            if (checked != Result::Ok)
                return checked;
            for (size_t prior = 0; prior < word; ++prior) {
                if ((mask & (uint16_t(1) << prior)) != 0 &&
                    refs[prior] == refs[word])
                    return Result::DuplicateReference;
            }
        }
        for (size_t word = 0; word < refs.size(); ++word) {
            if ((mask & (uint16_t(1) << word)) == 0)
                continue;
            const Result released =
                releaseValidated(refs[word], indices[word]);
            if (released != Result::Ok)
                return released;
        }
        return Result::Ok;
    }

    size_t capacity() const { return payload.size(); }
    size_t used() const { return usedWords; }
    bool empty() const { return usedWords == 0; }
    bool full() const { return usedWords == payload.size(); }

  private:
    static bool
    validWordBytes(size_t word_bytes)
    {
        return word_bytes == 4 || word_bytes == MaxWordBytes;
    }

    WordRef
    encode(uint32_t index, uint32_t word_generation) const
    {
        return (word_generation << indexBits) | index;
    }

    Result
    validate(WordRef ref, uint32_t &index) const
    {
        if (ref == InvalidWord || payload.empty())
            return Result::InvalidReference;
        index = ref & indexMask;
        const uint32_t word_generation = ref >> indexBits;
        if (index >= payload.size() || generation[index] != word_generation)
            return Result::InvalidReference;
        if (!allocated[index])
            return Result::DoubleFree;
        return Result::Ok;
    }

    Result
    releaseValidated(WordRef &ref, uint32_t index)
    {
        if (usedWords == 0 || freeList.size() >= payload.size())
            return Result::DoubleFree;
        allocated[index] = 0;
        payload[index].fill(0);
        freeList.push_back(index);
        --usedWords;
        ref = InvalidWord;
        return Result::Ok;
    }

    std::vector<std::array<uint8_t, MaxWordBytes>> payload;
    std::vector<uint8_t> allocated;
    std::vector<uint32_t> generation;
    std::vector<uint32_t> freeList;
    size_t usedWords = 0;
    uint32_t indexBits = 1;
    WordRef indexMask = 1;
    WordRef generationMask = std::numeric_limits<WordRef>::max() >> 1;
};

} // namespace gem5

#endif // __MEM_MAA_VIRTUAL_COMBINE_PAYLOAD_STORE_HH__
