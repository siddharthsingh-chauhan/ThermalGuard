import copy
import json
import os
from pathlib import Path

from controller import MODES

APP_DIR = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "ThermalGuard"
CONFIG_PATH = APP_DIR / "config.json"
LOG_PATH = APP_DIR / "thermalguard.log"

DEFAULTS = {
    "tick_seconds": 2,
    # boost_mode: 0=off 1=on 2=aggressive | epp: 0=max performance .. 100=max efficiency
    # cooling: 0=passive (slow the CPU before spinning fans) 1=active (fans first)
    "modes": {
        "quiet": {"boost_mode": 0, "epp": 70, "cooling": 0},
        "balanced": {"boost_mode": 1, "epp": 40, "cooling": 1},
        "performance": {"boost_mode": 2, "epp": 15, "cooling": 1},
    },
    "battery_max_mode": "balanced",
    "battery_epp_offset": 20,
    "load": {
        "short_window_s": 15,
        "long_window_s": 60,
        "balanced_up_pct": 25,
        "performance_up_pct": 60,
        "performance_down_pct": 40,
        "quiet_down_pct": 18,
        "single_thread_up_cores": 0.8,
        "single_thread_down_cores": 0.6,
        "min_dwell_s": 20,
    },
    "thermal": {
        "hot_c": 90,
        "cool_c": 78,
        "critical_c": 97,
        "step_interval_s": 10,
        "release_after_s": 30,
        "freq_caps_mhz": [4200, 3800, 3400, 3000, 2600],
        "fallback_max_level": 2,
        "heat_soak_load_pct": 70,
        "heat_soak_after_s": 300,
        "limit_min_load_pct": 30,
        # Without a thermometer (HWiNFO's free shared memory expires after 12 h)
        # nothing can catch overheating early, so skip aggressive turbo.
        "no_temp_max_mode": "balanced",
    },
    # LibreHardwareMonitor > Options > Remote Web Server > Run (port 8085).
    # Tried before HWiNFO shared memory; "" turns it off.
    "lhm_url": "http://127.0.0.1:8085/data.json",
    "app_min_modes": {},
    # Not "your task": their CPU is left out of the load that picks the mode,
    # so indexing, scans, updates and vendor agents run without turbo.
    # Names or wildcards, ".exe" optional.
    "background_apps": [
        "SearchIndexer", "SearchProtocolHost", "SearchFilterHost",
        "MsMpEng", "MpDefenderCoreService", "MBAM*", "TiWorker", "TrustedInstaller",
        "MoUsoCoreWorker", "CompatTelRunner", "wermgr", "WmiPrvSE", "svchost", "SDXHelper",
        "Dell*", "SupportAssist*", "nvcontainer", "NVDisplay.Container",
        "OneDrive", "GoogleDriveFS",
    ],
}

FIRST_RUN_EXTRAS = {}


def merge(base, override):
    out = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict) and out[key]:
            out[key] = merge(out[key], value)
        else:
            out[key] = value
    return out


def validate(cfg):
    for mode in MODES:
        if mode not in cfg["modes"]:
            raise ValueError(f"modes.{mode} is missing")
    if cfg["battery_max_mode"] not in MODES:
        raise ValueError(f"battery_max_mode must be one of {MODES}")
    for app, mode in cfg["app_min_modes"].items():
        if mode not in MODES:
            raise ValueError(f"app_min_modes[{app}] must be one of {MODES}")
    if cfg["thermal"]["no_temp_max_mode"] not in MODES:
        raise ValueError(f"thermal.no_temp_max_mode must be one of {MODES}")
    if not cfg["thermal"]["freq_caps_mhz"]:
        raise ValueError("thermal.freq_caps_mhz must not be empty")


def load_config(path=CONFIG_PATH):
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(merge(DEFAULTS, FIRST_RUN_EXTRAS), indent=2), encoding="utf-8")
    cfg = merge(DEFAULTS, json.loads(path.read_text(encoding="utf-8-sig")))
    validate(cfg)
    return cfg
