#!/usr/bin/env python3
"""Contract for the physical-MUL/coherent-publication CG microprobe."""

import struct
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GUEST = ROOT / "benchmarks/API/test_cg_product_handoff.cpp"
RUNNER = ROOT / "experiments/scripts/run_cg_product_handoff_probe.sh"


def fnv1a_words(words: list[int]) -> int:
    value = 1469598103934665603
    for word in words:
        for byte in struct.pack("<I", word):
            value ^= byte
            value = (value * 1099511628211) & ((1 << 64) - 1)
    return value


class CgProductHandoffProbeContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.guest = GUEST.read_text()
        cls.runner = RUNNER.read_text()

    def test_frozen_exact_hashes_match_the_four_page_vectors(self):
        pages = 4
        page_elements = 4096
        index_hash = fnv1a_words(
            [
                offset
                for _page in range(pages)
                for offset in range(page_elements)
            ]
        )
        product_hash = fnv1a_words(
            [
                word
                for word in (0x4B800000, 0x3F800000, 0xCB800000, 0x3F800000)
                for _offset in range(page_elements)
            ]
        )
        destination_hash = fnv1a_words([0x3F800000] * page_elements)
        self.assertEqual(index_hash, 14754458253095254915)
        self.assertEqual(product_hash, 2849837644626199427)
        self.assertEqual(destination_hash, 17263589712773219203)

    def test_guest_is_exactly_four_physical_pages_and_one_16k_set(self):
        for token in (
            "constexpr std::size_t kPages = 4;",
            "constexpr std::size_t kPageElements = 4096;",
            "constexpr std::size_t kLogicalElements = kPages * kPageElements;",
            "static_assert(TILE_SIZE == kLogicalElements",
            "selected_sets=1 masked_passes=0 host_spd_reads=0",
            "ordinary_page_rmws=4 soa_jit_descriptors=1",
        ):
            self.assertIn(token, self.guest)
        self.assertNotIn("soa_jit_masked", self.guest)
        self.assertNotIn(
            "maa_indirect_rmw_vector_soa_jit_masked_indices", self.guest
        )

    def test_each_destination_has_the_order_sensitive_cross_page_product(self):
        self.assertIn("4096.0F, 1.0F, -4096.0F, 1.0F", self.guest)
        self.assertIn("4096.0F, 1.0F, 4096.0F, 1.0F", self.guest)
        self.assertIn(
            "physical_indices[logical] = static_cast<uint32_t>(offset)",
            self.guest,
        )
        self.assertIn("+2^24, +1, -2^24, +1", self.guest)
        self.assertIn("0x3f800000U", self.guest)

    def test_snapshot_is_a_response_closed_coherent_copy_before_publish(self):
        snapshot = self.guest.index(
            "maa_stream_store<float>(prepublication_products"
        )
        snapshot_wait = self.guest.index("wait_ready(product_tile);", snapshot)
        publish = self.guest.index("publishPhysicalPage(page", snapshot)
        self.assertLess(snapshot, snapshot_wait)
        self.assertLess(snapshot_wait, publish)
        self.assertIn("CPU never reads an SPD tile", self.guest)
        self.assertIn("host_spd_reads=0", self.guest)

    def test_index_and_product_publications_are_response_bearing_and_closed(
        self,
    ):
        self.assertEqual(
            self.guest.count(
                "maa_publish_spd_page_logical16_response_bearing"
            ),
            2,
        )
        self.assertIn("wait_ready(index_completion_tile);", self.guest)
        self.assertIn("wait_ready(product_completion_tile);", self.guest)
        self.assertIn("const uint32_t logical_offset", self.guest)
        self.assertIn("uint32_t generation = 0;", self.guest)

    def test_four_ordinary_rmws_precede_one_soa_jit_descriptor(self):
        ordinary = self.guest.index("void\nrunOrdinaryFourPageRmws()")
        soa = self.guest.index("void\nrunOneUsefulLogicalSoaJitAdd()")
        self.assertEqual(
            self.guest.count("maa_indirect_rmw_vector<float>("), 1
        )
        self.assertEqual(
            self.guest.count("maa_indirect_rmw_vector_soa_jit<float>("), 1
        )
        self.assertLess(ordinary, soa)
        self.assertIn(
            "for (std::size_t page = 0; page < kPages; ++page)",
            self.guest[ordinary:soa],
        )
        self.assertIn("runOrdinaryFourPageRmws();", self.guest)
        self.assertIn("runOneUsefulLogicalSoaJitAdd();", self.guest)

    def test_result_checks_exact_wordwise_bridge_and_destination_bits(self):
        for token in (
            "bits(prepublication_products[logical]) !=",
            "bits(published_products[logical])",
            "bits(ordinary_destinations[destination]) != 0x3f800000U",
            "bits(soa_destinations[destination]) != 0x3f800000U",
            "kExpectedIndexHash",
            "kExpectedProductHash",
            "kExpectedDestinationHash",
            "exact_product_words=16384 exact_destination_words=4096",
        ):
            self.assertIn(token, self.guest)

    def test_runner_binds_frozen_provenance_and_exact_closure(self):
        for token in (
            'gem5="$root/build/X86/gem5.opt"',
            'ramulator="$root/ext/ramulator2/ramulator2/example_gem5_config.yaml"',
            'sha256sum "$source_file" "$binary" "$gem5" "$config" "$ramulator"',
            "source_commit=",
            "host_spd_reads=0",
            "performance_claim=0",
            "--maa_num_tile_elements=16384",
            "--maa_physical_tile_elements=4096",
            "'STR_PublishIssues') -eq 2048",
            "'STR_PublishAccepts') -eq 2048",
            "'STR_PublishWriteResponses') -eq 2048",
            "'STR_PublishTerminals') -eq 8",
            "soa_jit_descriptors=1",
        ):
            self.assertIn(token, self.runner)
        self.assertNotIn("masked", self.runner.replace("masked_passes=0", ""))

    def test_runner_shell_is_valid(self):
        subprocess.run(["bash", "-n", str(RUNNER)], check=True)


if __name__ == "__main__":
    unittest.main()
