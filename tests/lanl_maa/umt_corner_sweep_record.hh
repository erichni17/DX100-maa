#ifndef __TESTS_LANL_MAA_UMT_CORNER_SWEEP_RECORD_HH__
#define __TESTS_LANL_MAA_UMT_CORNER_SWEEP_RECORD_HH__

#include <charconv>
#include <cstdint>
#include <cstring>
#include <istream>
#include <limits>
#include <string>
#include <system_error>
#include <utility>
#include <vector>

#include "mem/LANLMAA/UmtCornerSweepModel.hh"

namespace gem5
{
namespace lanlmaa
{

constexpr char UmtSweepRecordMagic[] = "LANL_MAA_UMT_SWEEP_V1";
constexpr char UmtSweepRecordEnd[] = "END_LANL_MAA_UMT_SWEEP_V1";
constexpr char UmtSweepRecordRevision[] =
    "5fd8c132560b5debbe06bdaf0bbd70ce5fcb4979";
constexpr uint32_t UmtSweepRecordMaximumCorners = 64;
constexpr uint32_t UmtSweepRecordMaximumFluxPoints =
    UmtSweepRecordMaximumCorners *
    (UmtSweepMaximumFacesPerCorner + 1);

struct UmtCornerSweepRecord
{
    uint32_t setId = 0;
    uint32_t angle = 0;
    uint32_t nativeZone = 0;
    uint32_t nativeGroupCount = 0;
    UmtCornerSweepDescriptor descriptor;
    UmtCornerSweepInput input;
    std::vector<double> nativeExpected;
};

struct UmtCornerSweepRecordParseResult
{
    UmtCornerSweepRecord record;
    std::string error;

    explicit operator bool() const
    {
        return error.empty();
    }
};

class UmtCornerSweepRecordParser
{
  private:
    std::istream &input;
    std::string error;

    bool
    word(std::string &value)
    {
        if (!error.empty()) {
            return false;
        }
        if (!(input >> value)) {
            error = "unexpected end of UMT sweep record";
            return false;
        }
        return true;
    }

    bool
    expect(const char *expected)
    {
        std::string value;
        if (!word(value)) {
            return false;
        }
        if (value != expected) {
            error = "expected token " + std::string(expected) +
                ", found " + value;
            return false;
        }
        return true;
    }

    bool
    unsignedValue(uint32_t &value)
    {
        std::string text;
        if (!word(text)) {
            return false;
        }
        uint64_t parsed = 0;
        const auto conversion = std::from_chars(
            text.data(), text.data() + text.size(), parsed, 10);
        if (conversion.ec != std::errc() ||
            conversion.ptr != text.data() + text.size() ||
            parsed > std::numeric_limits<uint32_t>::max()) {
            error = "invalid unsigned integer " + text;
            return false;
        }
        value = static_cast<uint32_t>(parsed);
        return true;
    }

    bool
    namedUnsigned(const char *name, uint32_t &value)
    {
        return expect(name) && unsignedValue(value);
    }

    bool
    fp64Bits(double &value)
    {
        std::string text;
        if (!word(text)) {
            return false;
        }
        uint64_t bits = 0;
        const auto conversion = std::from_chars(
            text.data(), text.data() + text.size(), bits, 16);
        if (text.size() != 16 || conversion.ec != std::errc() ||
            conversion.ptr != text.data() + text.size()) {
            error = "invalid FP64 bit pattern " + text;
            return false;
        }
        std::memcpy(&value, &bits, sizeof(value));
        return true;
    }

    bool
    namedBits(const char *name, double &value)
    {
        return expect(name) && fp64Bits(value);
    }

    UmtCornerSweepRecordParseResult
    failed(UmtCornerSweepRecord record)
    {
        UmtCornerSweepRecordParseResult result;
        result.record = std::move(record);
        result.error = error;
        return result;
    }

  public:
    explicit UmtCornerSweepRecordParser(std::istream &stream)
        : input(stream)
    {}

    UmtCornerSweepRecordParseResult
    parse()
    {
        UmtCornerSweepRecord record;
        if (!expect(UmtSweepRecordMagic) || !expect("upstream_revision")) {
            return failed(std::move(record));
        }
        std::string revision;
        if (!word(revision)) {
            return failed(std::move(record));
        }
        if (revision != UmtSweepRecordRevision) {
            error = "unexpected UMT revision " + revision;
            return failed(std::move(record));
        }
        if (!namedUnsigned("set_id", record.setId) ||
            !namedUnsigned("angle", record.angle) ||
            !namedUnsigned("native_zone", record.nativeZone) ||
            !namedUnsigned("native_group_count",
                           record.nativeGroupCount) ||
            !namedUnsigned("corner_count", record.descriptor.cornerCount) ||
            !namedUnsigned("zone_count", record.descriptor.zoneCount) ||
            !namedUnsigned("flux_point_count",
                           record.descriptor.fluxPointCount) ||
            !namedUnsigned("total_groups", record.descriptor.totalGroups) ||
            !namedUnsigned("selected_corner_count",
                           record.descriptor.selectedCornerCount) ||
            !namedUnsigned("first_group", record.descriptor.firstGroup) ||
            !namedUnsigned("group_count", record.descriptor.groupCount) ||
            !namedBits("tau_bits", record.descriptor.tau)) {
            return failed(std::move(record));
        }
        if (record.descriptor.cornerCount == 0 ||
            record.descriptor.cornerCount > UmtSweepRecordMaximumCorners ||
            record.descriptor.zoneCount != 1 ||
            record.descriptor.fluxPointCount <
                record.descriptor.cornerCount ||
            record.descriptor.fluxPointCount >
                UmtSweepRecordMaximumFluxPoints ||
            record.nativeGroupCount == 0 ||
            record.descriptor.totalGroups == 0 ||
            record.descriptor.totalGroups > UmtSweepMaximumGroups ||
            record.descriptor.totalGroups > record.nativeGroupCount ||
            record.descriptor.selectedCornerCount != 1 ||
            record.descriptor.firstGroup != 0 ||
            record.descriptor.groupCount !=
                record.descriptor.totalGroups) {
            error = "unsupported UMT sweep record extent";
            return failed(std::move(record));
        }

        uint32_t selectedCorner = 0;
        uint32_t faceRecordCount = 0;
        if (!namedUnsigned("corner_order", selectedCorner) ||
            !namedUnsigned("face_record_count", faceRecordCount)) {
            return failed(std::move(record));
        }
        if (selectedCorner >= record.descriptor.cornerCount ||
            faceRecordCount > record.descriptor.cornerCount *
                UmtSweepMaximumFacesPerCorner) {
            error = "invalid UMT corner order or face-record count";
            return failed(std::move(record));
        }
        record.input.cornerOrder = {selectedCorner};
        record.input.corners.resize(record.descriptor.cornerCount);
        record.input.faces.resize(faceRecordCount);

        uint32_t expectedFaceOffset = 0;
        for (uint32_t index = 0; index < record.descriptor.cornerCount;
             ++index) {
            uint32_t recordIndex = 0;
            auto &corner = record.input.corners[index];
            if (!expect("corner") || !unsignedValue(recordIndex) ||
                !unsignedValue(corner.zone) ||
                !unsignedValue(corner.faceOffset) ||
                !unsignedValue(corner.faceCount) ||
                !fp64Bits(corner.volume) || !fp64Bits(corner.normSum)) {
                return failed(std::move(record));
            }
            if (recordIndex != index || corner.zone != 0 ||
                corner.faceOffset != expectedFaceOffset ||
                corner.faceCount < 3 ||
                corner.faceCount > UmtSweepMaximumFacesPerCorner ||
                corner.faceCount > faceRecordCount - expectedFaceOffset) {
                error = "invalid UMT corner record";
                return failed(std::move(record));
            }
            expectedFaceOffset += corner.faceCount;
        }
        if (expectedFaceOffset != faceRecordCount) {
            error = "UMT corner records do not cover the face records";
            return failed(std::move(record));
        }

        for (uint32_t index = 0; index < faceRecordCount; ++index) {
            uint32_t recordIndex = 0;
            auto &face = record.input.faces[index];
            if (!expect("face") || !unsignedValue(recordIndex) ||
                !unsignedValue(face.fluxPoint) ||
                !unsignedValue(face.ezCorner) ||
                !fp64Bits(face.fpNorm) || !fp64Bits(face.ezNorm)) {
                return failed(std::move(record));
            }
            if (recordIndex != index ||
                face.fluxPoint >= record.descriptor.fluxPointCount ||
                face.ezCorner >= record.descriptor.cornerCount) {
                error = "invalid UMT face record";
                return failed(std::move(record));
            }
        }

        const size_t cornerValues =
            static_cast<size_t>(record.descriptor.cornerCount) *
            record.descriptor.totalGroups;
        const size_t zoneValues =
            static_cast<size_t>(record.descriptor.zoneCount) *
            record.descriptor.totalGroups;
        const size_t fluxValues =
            static_cast<size_t>(record.descriptor.fluxPointCount) *
            record.descriptor.totalGroups;
        record.input.totalSource.resize(cornerValues);
        record.input.oldPsi.resize(cornerValues);
        record.input.totalCrossSection.resize(zoneValues);
        record.input.psi1.resize(fluxValues);

        for (uint32_t corner = 0;
             corner < record.descriptor.cornerCount; ++corner) {
            for (uint32_t group = 0;
                 group < record.descriptor.totalGroups; ++group) {
                uint32_t readCorner = 0;
                uint32_t readGroup = 0;
                const size_t index =
                    static_cast<size_t>(corner) *
                    record.descriptor.totalGroups + group;
                if (!expect("total_source") ||
                    !unsignedValue(readCorner) ||
                    !unsignedValue(readGroup) ||
                    !fp64Bits(record.input.totalSource[index]) ||
                    readCorner != corner || readGroup != group ||
                    !expect("old_psi") ||
                    !unsignedValue(readCorner) ||
                    !unsignedValue(readGroup) ||
                    !fp64Bits(record.input.oldPsi[index]) ||
                    readCorner != corner || readGroup != group) {
                    if (error.empty()) {
                        error = "misordered UMT source element";
                    }
                    return failed(std::move(record));
                }
            }
        }
        for (uint32_t group = 0;
             group < record.descriptor.totalGroups; ++group) {
            uint32_t zone = 0;
            uint32_t readGroup = 0;
            if (!expect("cross_section") || !unsignedValue(zone) ||
                !unsignedValue(readGroup) ||
                !fp64Bits(record.input.totalCrossSection[group]) ||
                zone != 0 || readGroup != group) {
                if (error.empty()) {
                    error = "misordered UMT cross-section element";
                }
                return failed(std::move(record));
            }
        }
        for (uint32_t point = 0;
             point < record.descriptor.fluxPointCount; ++point) {
            for (uint32_t group = 0;
                 group < record.descriptor.totalGroups; ++group) {
                uint32_t readPoint = 0;
                uint32_t readGroup = 0;
                const size_t index =
                    static_cast<size_t>(point) *
                    record.descriptor.totalGroups + group;
                if (!expect("psi1_before") ||
                    !unsignedValue(readPoint) ||
                    !unsignedValue(readGroup) ||
                    !fp64Bits(record.input.psi1[index]) ||
                    readPoint != point || readGroup != group) {
                    if (error.empty()) {
                        error = "misordered UMT flux element";
                    }
                    return failed(std::move(record));
                }
            }
        }

        record.nativeExpected.resize(record.descriptor.groupCount);
        for (uint32_t group = 0;
             group < record.descriptor.groupCount; ++group) {
            uint32_t readGroup = 0;
            if (!expect("native_expected") ||
                !unsignedValue(readGroup) ||
                !fp64Bits(record.nativeExpected[group]) ||
                readGroup != group) {
                if (error.empty()) {
                    error = "misordered UMT expected element";
                }
                return failed(std::move(record));
            }
        }
        if (!expect(UmtSweepRecordEnd)) {
            return failed(std::move(record));
        }
        std::string trailing;
        if (input >> trailing) {
            error = "trailing token after UMT sweep record: " + trailing;
            return failed(std::move(record));
        }
        UmtCornerSweepRecordParseResult result;
        result.record = std::move(record);
        return result;
    }
};

inline UmtCornerSweepRecordParseResult
parseUmtCornerSweepRecord(std::istream &input)
{
    UmtCornerSweepRecordParser parser(input);
    return parser.parse();
}

} // namespace lanlmaa
} // namespace gem5

#endif // __TESTS_LANL_MAA_UMT_CORNER_SWEEP_RECORD_HH__
