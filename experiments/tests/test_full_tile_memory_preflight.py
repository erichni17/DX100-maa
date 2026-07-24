import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import preflight_full_tile_memory as preflight  # noqa: E402


def healthy_meminfo(available_gib=200, swap_current_kib=0):
    total_kib = 346_566_728
    swap_total_kib = 2_097_148
    return preflight.parse_meminfo(
        "\n".join(
            (
                f"MemTotal: {total_kib} kB",
                f"MemAvailable: {available_gib * 1024**2} kB",
                f"SwapTotal: {swap_total_kib} kB",
                f"SwapFree: {swap_total_kib - swap_current_kib} kB",
            )
        )
    )


def unit(name, maximum_gib, active=True):
    return preflight.unit_from_properties(
        name,
        {
            "LoadState": "loaded",
            "ActiveState": "active" if active else "inactive",
            "ControlGroup": f"/fixture/{name}",
            "MemoryCurrent": "0",
            "MemoryMax": str(maximum_gib * preflight.GIB_BYTES),
        },
    )


def health(label="fixture"):
    return {
        "label": label,
        "path": f"/fixture/{label}",
        "psi_some_avg10": Decimal("0"),
        "psi_full_avg10": Decimal("0"),
        "events": {
            key: 0 for key in preflight.OOM_EVENT_KEYS
        },
        "swap_current_bytes": 0,
        "enforce_swap": True,
        "enforce_events": True,
    }


def report(
    units=(),
    proposed=preflight.FINAL_SLICE_CAP_GIB,
    meminfo=None,
    vmstat=None,
    host_pressure=None,
    cgroup_health=None,
):
    return preflight.evaluate(
        meminfo or healthy_meminfo(),
        vmstat or {"pswpin": 0, "pswpout": 0},
        host_pressure
        or {"some": Decimal("0"), "full": Decimal("0")},
        list(units),
        cgroup_health or [health()],
        proposed,
    )


class FullTileMemoryPreflightTests(unittest.TestCase):
    def test_slice_template_has_exact_inert_limits(self):
        root = Path(__file__).resolve().parents[2]
        text = (
            root
            / "experiments/systemd/dx100-full-tile-sweep.slice"
        ).read_text()
        self.assertIn("MemoryAccounting=yes", text)
        self.assertIn("MemoryHigh=256G", text)
        self.assertIn("MemoryMax=272G", text)
        self.assertIn("MemorySwapMax=0", text)
        self.assertNotIn("[Install]", text)

    def test_exact_memtotal_and_ninety_percent_ceiling(self):
        memory = healthy_meminfo()
        ceiling = preflight.ninety_percent_ceiling(
            memory["mem_total_bytes"]
        )
        self.assertEqual(memory["mem_total_kib"], 346_566_728)
        self.assertEqual(memory["mem_total_bytes"], 354_884_329_472)
        self.assertEqual(ceiling.numerator, 1_596_979_482_624)
        self.assertEqual(ceiling.denominator, 5)
        self.assertEqual(
            preflight.exact_decimal(ceiling), "319395896524.8"
        )

    def test_current_eight_legacy_units_sum_to_272_binary_gib(self):
        maxima = (60, 16, 28, 16, 64, 64, 16, 8)
        units = [
            unit(name, maximum)
            for name, maximum in zip(
                preflight.DEFAULT_LEGACY_UNITS, maxima
            )
        ]
        current = report(units, proposed=24)
        self.assertEqual(current["legacy"]["active_hard_sum_gib"], 272)
        self.assertEqual(current["slice"]["safe_slice_cap_gib"], 24)
        self.assertTrue(current["ok"])

    def test_inactive_legacy_unit_is_not_reserved(self):
        units = [unit("retired.service", 64, active=False)]
        current = report(units)
        self.assertEqual(current["legacy"]["active_hard_sum_gib"], 0)
        self.assertEqual(current["slice"]["safe_slice_cap_gib"], 272)
        self.assertTrue(current["ok"])

    def test_non_binary_gib_memory_max_is_rejected(self):
        with self.assertRaisesRegex(
            preflight.PreflightError, "exact binary GiB"
        ):
            preflight.unit_from_properties(
                "bad.service",
                {
                    "ActiveState": "active",
                    "ControlGroup": "/bad.service",
                    "MemoryCurrent": "0",
                    "MemoryMax": str(preflight.GIB_BYTES + 1),
                },
            )

    def test_cgroup_memory_max_must_match_systemd(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "memory.pressure").write_text(
                "some avg10=0.00 avg60=0.00 avg300=0.00 total=0\n"
                "full avg10=0.00 avg60=0.00 avg300=0.00 total=0\n"
            )
            (path / "memory.events.local").write_text(
                "low 0\nhigh 0\nmax 0\noom 0\noom_kill 0\n"
                "oom_group_kill 0\n"
            )
            (path / "memory.swap.current").write_text("0\n")
            expected = 60 * preflight.GIB_BYTES
            (path / "memory.max").write_text(f"{expected}\n")
            checked = preflight.read_cgroup_health(
                "legacy.service", path, expected
            )
            self.assertEqual(checked["swap_current_bytes"], 0)
            with self.assertRaisesRegex(
                preflight.PreflightError, "MemoryMax/cgroup mismatch"
            ):
                preflight.read_cgroup_health(
                    "legacy.service", path, expected + preflight.GIB_BYTES
                )

    def test_parent_health_uses_hierarchical_descendant_events(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "memory.pressure").write_text(
                "some avg10=0.00 avg60=0.00 avg300=0.00 total=0\n"
                "full avg10=0.00 avg60=0.00 avg300=0.00 total=0\n"
            )
            (path / "memory.events.local").write_text(
                "low 0\nhigh 0\nmax 0\noom 0\noom_kill 0\n"
                "oom_group_kill 0\n"
            )
            (path / "memory.events").write_text(
                "low 0\nhigh 0\nmax 1\noom 1\noom_kill 1\n"
                "oom_group_kill 0\n"
            )
            (path / "memory.swap.current").write_text("0\n")
            expected = 272 * preflight.GIB_BYTES
            (path / "memory.max").write_text(f"{expected}\n")
            leaf = preflight.read_cgroup_health(
                "leaf", path, expected
            )
            parent = preflight.read_cgroup_health(
                "parent",
                path,
                expected,
                hierarchical_events=True,
            )
            self.assertEqual(leaf["events"]["oom_kill"], 0)
            self.assertEqual(parent["events"]["oom_kill"], 1)
            self.assertTrue(
                parent["events_path"].endswith("/memory.events")
            )

    def test_full_slice_is_refused_while_272_gib_is_legacy(self):
        units = [unit("legacy.service", 272)]
        current = report(units)
        self.assertFalse(current["ok"])
        self.assertIn(
            "transition_hard_sum_exceeds_296_gib",
            current["violations"],
        )
        self.assertEqual(current["slice"]["safe_slice_cap_gib"], 24)

    def test_safe_cap_rises_to_272_as_legacy_units_retire(self):
        expected = {272: 24, 208: 88, 96: 200, 24: 272, 0: 272}
        for legacy_gib, safe_gib in expected.items():
            with self.subTest(legacy_gib=legacy_gib):
                units = (
                    [unit("legacy.service", legacy_gib)]
                    if legacy_gib
                    else []
                )
                current = report(units, proposed=safe_gib)
                self.assertEqual(
                    current["slice"]["safe_slice_cap_gib"], safe_gib
                )
                self.assertTrue(current["ok"])

    def test_transition_hard_sum_above_296_is_refused(self):
        current = report([unit("legacy.service", 297)], proposed=0)
        self.assertFalse(current["ok"])
        self.assertIn(
            "active_legacy_hard_sum_exceeds_296_gib",
            current["violations"],
        )

    def test_memavailable_below_50_gib_is_refused(self):
        current = report(meminfo=healthy_meminfo(available_gib=49))
        self.assertIn(
            "mem_available_below_50_gib", current["violations"]
        )

    def test_stable_host_swap_is_reported_but_not_refused(self):
        current = report(
            meminfo=healthy_meminfo(swap_current_kib=1),
            vmstat={
                "pswpin": 100,
                "pswpout": 200,
                "pswpin_delta": 0,
                "pswpout_delta": 0,
            },
        )
        self.assertTrue(current["ok"])
        self.assertIn("host_swap_occupied", current["warnings"])

    def test_current_swap_activity_is_refused(self):
        current = report(
            vmstat={
                "pswpin": 100,
                "pswpout": 200,
                "pswpin_delta": 1,
                "pswpout_delta": 0,
            }
        )
        self.assertIn("host_swap_activity_nonzero", current["violations"])

    def test_campaign_swap_is_refused_but_user_manager_swap_warns(self):
        swap_health = health()
        swap_health["swap_current_bytes"] = 4096
        current = report(cgroup_health=[swap_health])
        self.assertIn(
            "cgroup_swap_current_nonzero:fixture",
            current["violations"],
        )
        swap_health["enforce_swap"] = False
        current = report(cgroup_health=[swap_health])
        self.assertTrue(current["ok"])
        self.assertIn(
            "cgroup_swap_occupied:fixture", current["warnings"]
        )

    def test_vmstat_is_evaluated_as_a_sampled_delta(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "vmstat"
            path.write_text("pswpin 100\npswpout 200\n")

            def advance(_seconds):
                path.write_text("pswpin 100\npswpout 203\n")

            sampled = preflight.sample_vmstat(
                root, 1, sleep=advance
            )
            self.assertEqual(sampled["pswpin"], 100)
            self.assertEqual(sampled["pswpout"], 203)
            self.assertEqual(sampled["pswpin_delta"], 0)
            self.assertEqual(sampled["pswpout_delta"], 3)

    def test_legacy_discovery_excludes_services_in_target_slice(self):
        records = {
            "dx100-full-tile-old.service": unit(
                "dx100-full-tile-old.service", 64
            ),
            "dx100-full-tile-new.service": unit(
                "dx100-full-tile-new.service", 64
            ),
        }
        records["dx100-full-tile-new.service"][
            "slice"
        ] = preflight.TARGET_SLICE
        original_names = preflight.discover_legacy_unit_names
        original_query = preflight.query_unit
        try:
            preflight.discover_legacy_unit_names = (
                lambda systemctl, patterns: list(records)
            )
            preflight.query_unit = (
                lambda name, systemctl: records[name]
            )
            discovered = preflight.discover_legacy_units()
        finally:
            preflight.discover_legacy_unit_names = original_names
            preflight.query_unit = original_query
        self.assertEqual(
            [record["unit"] for record in discovered],
            ["dx100-full-tile-old.service"],
        )

    def test_explicit_legacy_units_augment_instead_of_bypass_discovery(self):
        discovered = unit("dx100-full-tile-discovered.service", 64)
        explicit = unit("special-campaign.service", 16)
        original_discover = preflight.discover_legacy_units
        original_query = preflight.query_unit
        try:
            preflight.discover_legacy_units = (
                lambda systemctl, patterns: [discovered]
            )
            preflight.query_unit = (
                lambda name, systemctl: explicit
            )
            collected = preflight.collect_legacy_units(
                explicit_units=["special-campaign.service"]
            )
        finally:
            preflight.discover_legacy_units = original_discover
            preflight.query_unit = original_query
        self.assertEqual(
            [record["unit"] for record in collected],
            [
                "dx100-full-tile-discovered.service",
                "special-campaign.service",
            ],
        )

    def test_active_host_or_cgroup_psi_is_refused(self):
        current = report(
            host_pressure={
                "some": Decimal("0.01"),
                "full": Decimal("0"),
            }
        )
        self.assertIn(
            "host_memory_psi_some_active", current["violations"]
        )
        pressure_health = health()
        pressure_health["psi_full_avg10"] = Decimal("0.02")
        current = report(cgroup_health=[pressure_health])
        self.assertIn(
            "cgroup_memory_psi_full_active:fixture",
            current["violations"],
        )

    def test_oom_or_max_event_is_refused(self):
        event_health = health()
        event_health["events"]["oom_kill"] = 1
        current = report(cgroup_health=[event_health])
        self.assertIn(
            "cgroup_memory_event:fixture:oom_kill",
            current["violations"],
        )
        event_health["enforce_events"] = False
        current = report(cgroup_health=[event_health])
        self.assertTrue(current["ok"])
        self.assertIn(
            "cgroup_memory_event:fixture:oom_kill",
            current["warnings"],
        )


if __name__ == "__main__":
    unittest.main()
