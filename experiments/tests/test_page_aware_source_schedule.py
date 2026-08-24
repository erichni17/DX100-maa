import random
import unittest

from experiments.analysis.analyze_page_aware_source_schedule import (
    Descriptor,
    IssueEvent,
    analyze,
    build_lines,
    least_complete_score_order,
    metadata_cost,
    page_major_row_order,
    reconstruct_request_instances,
    row_first_page_order,
)


class PageAwareSourceScheduleTest(unittest.TestCase):
    @staticmethod
    def fixture():
        # Four two-element pages.  Lines 0x1000/0x3000/0x4000 cross page
        # boundaries and therefore exercise exact one-request coalescing.
        rows = {
            0x1000: (0, 0, 0, 0, 1),
            0x2000: (0, 0, 0, 0, 0),
            0x3000: (0, 0, 0, 0, 1),
            0x4000: (0, 0, 0, 1, 0),
            0x5000: (0, 0, 0, 0, 0),
        }
        mapping = [
            0x1000,
            0x2000,
            0x1000,
            0x3000,
            0x3000,
            0x4000,
            0x4000,
            0x5000,
        ]
        return [
            Descriptor(iteration, line, rows[line], iteration & 7)
            for iteration, line in enumerate(mapping)
        ]

    def test_duplicate_a_lines_across_pages_are_one_request(self):
        report = analyze(
            self.fixture(),
            [0x2000, 0x1000, 0x3000, 0x4000, 0x5000],
            logical_elements=8,
            page_elements=2,
            capacity_lines=8,
        )
        for policy in report["policies"].values():
            self.assertEqual(policy["semantic_descriptors"], 8)
            self.assertEqual(policy["unique_a_lines"], 5)
            self.assertEqual(policy["requests"], 5)
            self.assertEqual(
                policy["coalesced_descriptor_requests_avoided"], 3
            )

    def test_page_masks_capture_every_contributor_endpoint(self):
        lines, pages = build_lines(self.fixture(), 8, 2)
        masks = {line.line: line.page_mask for line in lines}
        self.assertEqual(pages, 4)
        self.assertEqual(masks[0x1000], 0b0011)
        self.assertEqual(masks[0x2000], 0b0001)
        self.assertEqual(masks[0x3000], 0b0110)
        self.assertEqual(masks[0x4000], 0b1100)
        self.assertEqual(masks[0x5000], 0b1000)

    def test_finite_reissue_keeps_cross_page_line_instances_separate(self):
        row = (0, 0, 0, 0, 1)
        descriptors = [
            Descriptor(0, 0x1000, row, 0, 1),
            Descriptor(1, 0x2000, row, 1, 2),
            Descriptor(2, 0x1000, row, 2, 4),
            Descriptor(3, 0x3000, row, 3, 5),
        ]
        issues = [
            IssueEvent(0, 0x1000, 3, 0),
            IssueEvent(1, 0x2000, 6, 0),
            IssueEvent(2, 0x1000, 7, 1),
            IssueEvent(3, 0x3000, 8, 1),
        ]
        requests, pages = reconstruct_request_instances(
            descriptors, issues, 4, 2
        )
        self.assertEqual(pages, 2)
        self.assertEqual(
            [request.line for request in requests].count(0x1000), 2
        )
        self.assertEqual(
            [
                request.page_mask
                for request in requests
                if request.line == 0x1000
            ],
            [0b01, 0b10],
        )
        self.assertEqual(sum(request.descriptors for request in requests), 4)

    def test_policy_ordering_is_exact_and_distinct(self):
        lines, pages = build_lines(self.fixture(), 8, 2)
        page = [record.line for record in page_major_row_order(lines)]
        row = [record.line for record in row_first_page_order(lines)]
        score = [
            record.line for record in least_complete_score_order(lines, pages)
        ]
        self.assertEqual(page, [0x2000, 0x1000, 0x3000, 0x4000, 0x5000])
        self.assertEqual(row, [0x2000, 0x5000, 0x1000, 0x3000, 0x4000])
        self.assertEqual(set(score), set(page))
        self.assertEqual(len(score), len(set(score)))

    def test_ties_are_deterministic_under_input_shuffle(self):
        baseline, pages = build_lines(self.fixture(), 8, 2)
        expected = {
            "page": [x.line for x in page_major_row_order(baseline)],
            "row": [x.line for x in row_first_page_order(baseline)],
            "score": [
                x.line for x in least_complete_score_order(baseline, pages)
            ],
        }
        for seed in range(10):
            shuffled = list(self.fixture())
            random.Random(seed).shuffle(shuffled)
            lines, pages = build_lines(shuffled, 8, 2)
            self.assertEqual(
                [x.line for x in page_major_row_order(lines)], expected["page"]
            )
            self.assertEqual(
                [x.line for x in row_first_page_order(lines)], expected["row"]
            )
            self.assertEqual(
                [x.line for x in least_complete_score_order(lines, pages)],
                expected["score"],
            )

    def test_metadata_cost_charges_masks_counters_and_rounding(self):
        cost = metadata_cost(observed_lines=5, capacity_lines=8, pages=4)
        self.assertEqual(cost["current"]["provisioned"]["total_bits"], 0)
        self.assertEqual(
            cost["page_major_then_row"]["provisioned"],
            {
                "line_page_mask_bits": 32,
                "page_counter_bits": 16,
                "total_bits": 48,
                "total_bytes_ceil": 6,
            },
        )
        self.assertEqual(
            cost["least_complete_score"]["provisioned"]["total_bits"], 66
        )
        self.assertEqual(
            cost["least_complete_score"]["provisioned"]["total_bytes_ceil"],
            9,
        )


if __name__ == "__main__":
    unittest.main()
