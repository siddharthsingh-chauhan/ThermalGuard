import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from autostart import task_xml  # noqa: E402
from config import DEFAULTS, merge  # noqa: E402
from controller import Controller, Sample  # noqa: E402
from power import BALANCED, SCHEME, choose_original, read_saved_plan, save_plan  # noqa: E402
from sensors import HEADER, READING, parse_hwinfo, parse_lhm, summarize  # noqa: E402

TICK = 2


class Sim:
    def __init__(self, start_mode="balanced", **cfg_overrides):
        self.c = Controller(merge(DEFAULTS, cfg_overrides), start_mode)
        self.t = 0.0
        self.d = None

    def run(self, seconds, pct, cores=0.0, **kw):
        kw.setdefault("temp_c", 60.0)  # pass temp_c=None to test the no-thermometer paths
        for _ in range(int(seconds / TICK)):
            self.d = self.c.update(Sample(self.t, pct, cores, **kw))
            self.t += TICK
        return self.d


class LoadModes(unittest.TestCase):
    def test_idle_settles_to_quiet(self):
        sim = Sim()
        self.assertEqual(sim.run(70, 3).mode, "quiet")

    def test_quiet_mode_disables_boost_and_uses_passive_cooling(self):
        s = Sim().run(70, 3).settings
        self.assertEqual((s["PERFBOOSTMODE"], s["SYSCOOLPOL"], s["PROCFREQMAX"]), (0, 0, 0))

    def test_brief_spikes_stay_quiet(self):
        sim = Sim("quiet")
        for _ in range(6):
            sim.run(2, 100)
            self.assertEqual(sim.run(8, 3).mode, "quiet")

    def test_background_noise_spread_over_many_apps_is_quiet(self):
        # Measured on this laptop while idle: ~15% total, busiest app (indexer) ~0.55 cores
        self.assertEqual(Sim().run(90, 15, cores=0.55).mode, "quiet")

    def test_app_launch_spike_does_not_block_quiet(self):
        sim = Sim()
        sim.run(30, 14, cores=0.5)
        sim.run(2, 90, cores=6.0)
        self.assertEqual(sim.run(30, 14, cores=0.5).mode, "quiet")

    def test_sustained_single_thread_gets_turbo(self):
        sim = Sim("quiet")
        self.assertEqual(sim.run(20, 9, cores=1.0).mode, "balanced")

    def test_heavy_load_goes_to_performance_then_steps_down(self):
        sim = Sim("quiet")
        self.assertEqual(sim.run(20, 90).mode, "performance")
        self.assertEqual(sim.run(10, 30).mode, "performance")  # dwell + long average keep it up
        self.assertEqual(sim.run(60, 30).mode, "balanced")
        self.assertEqual(sim.run(90, 2).mode, "quiet")

    def test_moderate_load_holds_balanced_without_flapping(self):
        sim = Sim("quiet")
        sim.run(20, 30)
        modes = {sim.run(TICK, 18 if i % 2 else 30).mode for i in range(60)}
        self.assertEqual(modes, {"balanced"})

    def test_battery_caps_at_balanced_and_raises_epp(self):
        sim = Sim()
        d = sim.run(30, 95, on_ac=False)
        self.assertEqual(d.mode, "balanced")
        self.assertEqual(d.settings["PERFEPP"], DEFAULTS["modes"]["balanced"]["epp"] + 20)

    def test_app_floor(self):
        self.assertEqual(Sim().run(70, 2, app_min_mode="balanced").mode, "balanced")

    def test_manual_override_wins(self):
        sim = Sim()
        sim.c.override = "performance"
        self.assertEqual(sim.run(30, 1, on_ac=False).mode, "performance")


class ThermalCap(unittest.TestCase):
    def test_hot_steps_cap_down_one_level_per_interval(self):
        sim = Sim("quiet")
        d = sim.run(4, 50, temp_c=92)
        self.assertEqual(d.cap_mhz, 4200)
        self.assertEqual(sim.run(10, 50, temp_c=92).cap_mhz, 3800)
        self.assertEqual(d.settings["SYSCOOLPOL"], 1)

    def test_holds_cap_between_thresholds(self):
        sim = Sim()
        sim.run(12, 50, temp_c=92)
        level = sim.c.cap_level
        sim.run(120, 50, temp_c=85)
        self.assertEqual(sim.c.cap_level, level)

    def test_critical_temperature_caps_fully(self):
        d = Sim().run(2, 80, temp_c=99)
        self.assertEqual(d.cap_mhz, DEFAULTS["thermal"]["freq_caps_mhz"][-1])

    def test_cool_releases_cap_gradually(self):
        sim = Sim()
        sim.run(2, 80, temp_c=99)
        sim.run(30, 20, temp_c=70)
        self.assertGreater(sim.c.cap_level, 0)
        sim.run(300, 20, temp_c=70)
        self.assertEqual(sim.c.cap_level, 0)

    def test_heat_soak_without_temperature(self):
        sim = Sim()
        self.assertEqual(sim.run(290, 85, temp_c=None).cap_mhz, 0)
        self.assertEqual(sim.run(20, 85, temp_c=None).cap_mhz, 4200)
        sim.run(60, 10, temp_c=None)
        self.assertEqual(sim.c.cap_level, 0)

    def test_throttle_flags_ignored_when_idle(self):
        sim = Sim()
        self.assertEqual(sim.run(60, 5, limited=True, temp_c=None).cap_mhz, 0)

    def test_throttle_flags_under_load_cap_at_most_fallback_level(self):
        sim = Sim()
        sim.run(120, 60, limited=True, temp_c=None)
        self.assertEqual(sim.c.cap_level, DEFAULTS["thermal"]["fallback_max_level"])


def fake_hwinfo(readings, signature=b"HWiS", poll_time=1_700_000_000):
    offset = HEADER.size
    head = HEADER.pack(signature, 2, 1, poll_time, offset, 0, 0, offset, READING.size, len(readings))
    body = b"".join(
        READING.pack(rtype, 0, i, label.encode(), b"", b"", value, value, value, value)
        for i, (rtype, label, value) in enumerate(readings))
    return head + body


class HwinfoParsing(unittest.TestCase):
    def test_prefers_cpu_package_and_ignores_distance_to_tjmax(self):
        buf = fake_hwinfo([
            (1, "Core #0 Distance to TjMAX", 20.0),
            (1, "Core #0", 81.0),
            (1, "CPU Package", 84.0),
            (5, "CPU Package Power", 37.5),
            (3, "CPU Fan", 3100.0),
            (3, "Video Fan", 2900.0),
        ])
        data = parse_hwinfo(buf)
        self.assertEqual((data.temp_c, data.package_w), (84.0, 37.5))
        self.assertEqual(data.fans, {"CPU Fan": 3100.0, "Video Fan": 2900.0})

    def test_falls_back_to_hottest_core(self):
        data = parse_hwinfo(fake_hwinfo([(1, "Core #0", 70.0), (1, "Core #3", 77.0),
                                         (1, "Core #3 Distance to TjMAX", 23.0)]))
        self.assertEqual(data.temp_c, 77.0)

    def test_inactive_memory_is_ignored(self):
        self.assertIsNone(parse_hwinfo(fake_hwinfo([(1, "CPU Package", 50.0)], signature=b"DEAD")))


def lhm_node(text, children=(), value="", image=""):
    return {"id": 0, "Text": text, "Min": "", "Value": value, "Max": "", "ImageURL": image,
            "Children": list(children)}


def fake_lhm(cpu_temps, cpu_power="28,4 W", fans=()):
    cpu = lhm_node("13th Gen Intel Core i7-13700H", [
        lhm_node("Temperatures", [lhm_node(n, value=v) for n, v in cpu_temps]),
        lhm_node("Powers", [lhm_node("CPU Package", value=cpu_power), lhm_node("CPU Cores", value="20,1 W")]),
    ], image="images_icon/cpu.png")
    gpu = lhm_node("NVIDIA RTX 3050", [lhm_node("Temperatures", [lhm_node("GPU Core", value="99,0 °C")])],
                   image="images_icon/nvidia.png")
    board = lhm_node("Dell Inc.", [lhm_node("Fans", [lhm_node(n, value=v) for n, v in fans])],
                     image="images_icon/mainboard.png")
    return lhm_node("Sensor", [lhm_node("DESKTOP", [cpu, gpu, board], image="images_icon/computer.png")])


class LhmParsing(unittest.TestCase):
    def test_reads_cpu_package_with_danish_decimal_comma(self):
        data = parse_lhm(fake_lhm([("CPU Core #1", "70,0 °C"), ("CPU Package", "84,5 °C"),
                                   ("Core Max", "83,0 °C")], fans=[("Fan #1", "3100 RPM")]), 1_700_000_000)
        self.assertEqual((data.temp_c, data.package_w, data.fans), (84.5, 28.4, {"Fan #1": 3100.0}))

    def test_falls_back_to_hottest_core_and_ignores_gpu(self):
        data = parse_lhm(fake_lhm([("CPU Core #1", "71,0 °C"), ("CPU Core #2", "76,0 °C"),
                                   ("CPU Core #2 Distance to TjMax", "24,0 °C")]), 0)
        self.assertEqual(data.temp_c, 76.0)

    def test_fahrenheit_is_converted(self):
        data = parse_lhm(fake_lhm([("CPU Package", "185,0 °F")]), 0)
        self.assertEqual(data.temp_c, 85.0)

    def test_empty_values_are_skipped(self):
        self.assertIsNone(parse_lhm(fake_lhm([("CPU Package", "")], cpu_power=""), 0))


class ProcessSplit(unittest.TestCase):
    BG = DEFAULTS["background_apps"]

    def test_background_services_do_not_count_as_your_task(self):
        # Measured here: the indexer at 1.5 cores kept it in balanced with turbo
        items = [("_Total", 900.0), ("Idle", 300.0), ("SearchIndexer", 150.0), ("MBAMService", 110.0),
                 ("svchost#12", 60.0), ("chrome#4", 35.0)]
        p = summarize(items, self.BG)
        self.assertEqual((p.top_name, p.top_cores), ("chrome#4", 0.35))
        self.assertAlmostEqual(p.background_cores, 3.2)

    def test_wildcards_cover_vendor_agents(self):
        # Seen at boot: Dell agents and SupportAssist each took 1+ cores
        items = [("Dell.TechHub.DataManager.SubAgent", 100.0), ("DellSupportAssistRemedationService", 130.0),
                 ("nvcontainer#1", 200.0)]
        p = summarize(items, self.BG)
        self.assertEqual((p.top_name, p.background_cores), (None, 4.3))

    def test_real_work_still_counts(self):
        p = summarize([("SearchIndexer", 40.0), ("SLDWORKS", 100.0)], self.BG)
        self.assertEqual((p.top_name, p.top_cores), ("SLDWORKS", 1.0))


class Autostart(unittest.TestCase):
    def test_task_xml_escapes_and_binds_to_user(self):
        xml = task_xml(r"C:\Program Files\Thermal & Co\ThermalGuard.exe", r"CORP\anne")
        self.assertIn(r"<Command>C:\Program Files\Thermal &amp; Co\ThermalGuard.exe</Command>", xml)
        self.assertIn(r"<WorkingDirectory>C:\Program Files\Thermal &amp; Co</WorkingDirectory>", xml)
        self.assertEqual(xml.count(r"<UserId>CORP\anne</UserId>"), 2)


class PreviousPlan(unittest.TestCase):
    DELL = "49ef8fc0-bb7f-488e-b6a0-f1fc77ec649b"
    PLANS = f"{BALANCED} (balanced)\n{DELL} (dell)\n{SCHEME} (thermalguard) *"

    def test_remembers_the_plan_that_was_active(self):
        self.assertEqual(choose_original(self.DELL, None, self.PLANS), self.DELL)

    def test_after_a_forced_stop_uses_the_saved_plan_not_balanced(self):
        # Upgrades kill ThermalGuard, so its own plan is still active on the next start
        self.assertEqual(choose_original(SCHEME, self.DELL, self.PLANS), self.DELL)

    def test_falls_back_to_balanced_if_saved_plan_is_gone_or_missing(self):
        self.assertEqual(choose_original(SCHEME, self.DELL, f"{BALANCED}\n{SCHEME}"), BALANCED)
        self.assertEqual(choose_original(SCHEME, None, self.PLANS), BALANCED)
        self.assertEqual(choose_original(SCHEME, SCHEME, self.PLANS), BALANCED)

    def test_saved_plan_round_trip_and_bad_file(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "sub" / "previous_plan.txt"
            self.assertIsNone(read_saved_plan(path))
            save_plan(path, self.DELL)
            self.assertEqual(read_saved_plan(path), self.DELL)
            path.write_text("not a guid")
            self.assertIsNone(read_saved_plan(path))
        self.assertIsNone(read_saved_plan(None))


class TrayIcons(unittest.TestCase):
    def test_every_mode_and_cap_state_has_a_rendered_icon(self):
        from app import TRAY_DIR, make_icon
        from controller import MODES

        for mode in MODES:
            for capped in (False, True):
                name = f"{mode}{'-capped' if capped else ''}.png"
                self.assertTrue((TRAY_DIR / name).exists(), name)
                self.assertEqual(make_icon(mode, capped).size, (64, 64))


class NoTemperature(unittest.TestCase):
    def test_no_aggressive_turbo_without_a_thermometer(self):
        d = Sim().run(30, 95, temp_c=None)
        self.assertEqual((d.mode, d.reason), ("balanced", "no temperature reading"))

    def test_performance_returns_with_temperature(self):
        self.assertEqual(Sim().run(30, 95, temp_c=70).mode, "performance")


if __name__ == "__main__":
    unittest.main()
