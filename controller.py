"""Decision logic: CPU load and temperature in, power mode and frequency cap out.

Pure and clock-free (time comes in on each Sample) so it can be unit tested.
"""
import statistics
from collections import deque
from dataclasses import dataclass
from typing import Optional

MODES = ("quiet", "balanced", "performance")
RANK = {m: i for i, m in enumerate(MODES)}


@dataclass
class Sample:
    t: float
    load_pct: float
    top_cores: float  # busiest single process; 1.0 = one core fully busy
    on_ac: bool = True
    temp_c: Optional[float] = None
    limited: bool = False
    app_min_mode: Optional[str] = None


@dataclass
class Decision:
    mode: str
    cap_mhz: int
    settings: dict
    reason: str


class Controller:
    def __init__(self, cfg, start_mode="balanced"):
        self.cfg = cfg
        self.mode = start_mode
        self.mode_since = None
        self.override = None
        self.cap_level = 0
        self._samples = deque()
        self._last_cap_change = float("-inf")
        self._cool_since = None
        self._heavy_since = None
        self._limited_streak = 0

    @property
    def cap_mhz(self):
        caps = self.cfg["thermal"]["freq_caps_mhz"]
        return caps[min(self.cap_level, len(caps)) - 1] if self.cap_level else 0

    def update(self, s):
        if self.mode_since is None:
            self.mode_since = s.t
        short_pct, short_cores, long_pct, long_cores = self._window(s)
        if s.t - self._samples[0][0] < self.cfg["load"]["short_window_s"] / 2:
            target, reason = self.mode, "collecting load history"
        else:
            target, reason = self._load_mode(s.t, short_pct, short_cores, long_pct, long_cores)
        if s.app_min_mode and RANK[s.app_min_mode] > RANK[target]:
            target, reason = s.app_min_mode, "app floor"
        battery_max = self.cfg["battery_max_mode"]
        if not s.on_ac and RANK[target] > RANK[battery_max]:
            target, reason = battery_max, "on battery"
        no_temp_max = self.cfg["thermal"]["no_temp_max_mode"]
        if s.temp_c is None and RANK[target] > RANK[no_temp_max]:
            target, reason = no_temp_max, "no temperature reading"
        if self.override:
            target, reason = self.override, "manual"
        if target != self.mode:
            self.mode, self.mode_since = target, s.t
        self._update_cap(s, short_pct)
        return Decision(self.mode, self.cap_mhz, self._settings(s.on_ac), reason)

    def _window(self, s):
        # Medians, so a 2-second spike (opening an app) doesn't count as "work";
        # only load sustained for ~half the window does.
        L = self.cfg["load"]
        self._samples.append((s.t, s.load_pct, s.top_cores))
        while s.t - self._samples[0][0] > L["long_window_s"]:
            self._samples.popleft()
        recent = [x for x in self._samples if s.t - x[0] <= L["short_window_s"]]
        return (
            statistics.median(x[1] for x in recent),
            statistics.median(x[2] for x in recent),
            statistics.median(x[1] for x in self._samples),
            statistics.median(x[2] for x in self._samples),
        )

    def _load_mode(self, t, short_pct, short_cores, long_pct, long_cores):
        L = self.cfg["load"]
        settled = t - self.mode_since >= L["min_dwell_s"]
        if short_pct >= L["performance_up_pct"]:
            return "performance", f"heavy load ({short_pct:.0f}%)"
        if self.mode == "performance":
            dropped = max(short_pct, long_pct) < L["performance_down_pct"]
            if not (settled and dropped):
                return "performance", "heavy load, cooling off"
            return "balanced", "heavy load ended"
        # The busiest-process signal catches single-threaded work, which is only
        # ~8% of total CPU on a 12-thread chip but still deserves turbo. It must be
        # per process: background noise spread over many apps is not a task.
        if short_pct >= L["balanced_up_pct"] or short_cores >= L["single_thread_up_cores"]:
            return "balanced", f"active work ({short_pct:.0f}%, busiest app {short_cores:.1f} cores)"
        if self.mode == "balanced":
            idle = (max(short_pct, long_pct) < L["quiet_down_pct"]
                    and max(short_cores, long_cores) < L["single_thread_down_cores"])
            if not (settled and idle):
                return "balanced", "waiting for load to settle"
        return "quiet", "light load"

    def _update_cap(self, s, short_pct):
        T = self.cfg["thermal"]
        caps = T["freq_caps_mhz"]
        if s.temp_c is not None:
            max_level = len(caps)
            self._limited_streak, self._heavy_since = 0, None
            if s.temp_c >= T["critical_c"]:
                if self.cap_level != max_level:
                    self.cap_level, self._last_cap_change = max_level, s.t
                self._cool_since = None
                return
            hot, cool = s.temp_c >= T["hot_c"], s.temp_c <= T["cool_c"]
        else:
            # No temperature source: react to Windows' own throttle counters and
            # pre-emptively trim long all-core runs. Capped lower than with real
            # temperatures because these signals can be false positives.
            max_level = min(len(caps), T["fallback_max_level"])
            if short_pct >= T["heat_soak_load_pct"]:
                if self._heavy_since is None:
                    self._heavy_since = s.t
            else:
                self._heavy_since = None
            soaked = self._heavy_since is not None and s.t - self._heavy_since >= T["heat_soak_after_s"]
            throttling = s.limited and short_pct >= T["limit_min_load_pct"]
            self._limited_streak = self._limited_streak + 1 if throttling else 0
            hot = self._limited_streak >= 3 or (soaked and self.cap_level == 0)
            cool = not throttling and not soaked

        self.cap_level = min(self.cap_level, max_level)
        step_ok = s.t - self._last_cap_change >= T["step_interval_s"]
        if hot:
            self._cool_since = None
            if self.cap_level < max_level and step_ok:
                self.cap_level += 1
                self._last_cap_change = s.t
        elif cool and self.cap_level > 0:
            if self._cool_since is None:
                self._cool_since = s.t
            if s.t - self._cool_since >= T["release_after_s"] and step_ok:
                self.cap_level -= 1
                self._last_cap_change = self._cool_since = s.t
        else:
            self._cool_since = None

    def _settings(self, on_ac):
        m = self.cfg["modes"][self.mode]
        epp = m["epp"] + (0 if on_ac else self.cfg["battery_epp_offset"])
        return {
            "PERFBOOSTMODE": int(m["boost_mode"]),
            "PERFEPP": max(0, min(100, int(epp))),
            "SYSCOOLPOL": 1 if self.cap_level else int(m["cooling"]),
            "PROCFREQMAX": self.cap_mhz,
        }
