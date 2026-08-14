#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <stdexcept>

#include "mem/MAA/VirtualResponsePayloadStore.hh"

using gem5::VirtualResponsePayloadStore;

namespace
{

#define CHECK(condition)                                                     \
    do {                                                                     \
        if (!(condition)) {                                                  \
            std::cerr << __FILE__ << ':' << __LINE__                         \
                      << ": check failed: " #condition << std::endl;         \
            std::exit(1);                                                    \
        }                                                                    \
    } while (false)

void
packedModeHasNoFixedLines()
{
    VirtualResponsePayloadStore store;
    store.configure(128, true);

    CHECK(store.packedResponse());
    CHECK(store.slotCapacity() == 128);
    CHECK(store.lineCount() == 0);
    CHECK(store.payloadBytes() == 0);
    store.reset();
    CHECK(store.payloadBytes() == 0);
}

void
unpackedModeOwnsOneBoundedLinePerSlot()
{
    VirtualResponsePayloadStore store;
    store.configure(3, false);

    CHECK(!store.packedResponse());
    CHECK(store.slotCapacity() == 3);
    CHECK(store.lineCount() == 3);
    CHECK(store.payloadBytes() ==
          3 * VirtualResponsePayloadStore::LineBytes);

    for (std::size_t slot = 0; slot < store.lineCount(); ++slot) {
        for (std::size_t byte = 0;
             byte < VirtualResponsePayloadStore::LineBytes; ++byte) {
            CHECK(store.lineData(slot)[byte] == 0);
        }
    }

    store.lineData(0)[7] = 0x11;
    store.lineData(2)[7] = 0x33;
    CHECK(store.lineData(0)[7] == 0x11);
    CHECK(store.lineData(1)[7] == 0);
    CHECK(store.lineData(2)[7] == 0x33);

    bool rejected = false;
    try {
        static_cast<void>(store.lineData(store.slotCapacity()));
    } catch (const std::out_of_range &) {
        rejected = true;
    }
    CHECK(rejected);

    store.reset();
    CHECK(store.lineData(0)[7] == 0);
    CHECK(store.lineData(2)[7] == 0);
}

} // anonymous namespace

int
main()
{
    packedModeHasNoFixedLines();
    unpackedModeOwnsOneBoundedLinePerSlot();
    std::cout << "PASS virtual response payload store\n";
    return 0;
}
