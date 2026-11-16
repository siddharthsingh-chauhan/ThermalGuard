import argparse
import ctypes
import logging
import subprocess
import sys
import threading
import time
from ctypes import wintypes
from logging.handlers import RotatingFileHandler

import psutil

import autostart
from config import APP_DIR, CONFIG_PATH, DEFAULTS, LOG_PATH, load_config
from controller import Controller, Sample
from power import PowerError, PowerManager
from sensors import AppWatcher, HwinfoReader, LhmReader, PerfCounters, TopProcess, is_limited, on_ac_power
from version import __version__

log = logging.getLogger("thermalguard")
STATUS_EVERY_S = 30
PLAN_STATE_PATH = APP_DIR / "previous_plan.txt"  # survives a forced stop (upgrade, crash)
_mutex = None


def setup_logging(console):
    APP_DIR.mkdir(parents=True, exist_ok=True)
    handlers = [RotatingFileHandler(LOG_PATH, maxBytes=1_000_000, backupCount=3, encoding="utf-8")]
    if console and sys.stdout:
        handlers.append(logging.StreamHandler(sys.stdout))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", handlers=handlers)


def acquire_single_instance():
    global _mutex
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateMutexW.restype = wintypes.HANDLE
    _mutex = k32.CreateMutexW(None, False, "Local\\ThermalGuard")
    return ctypes.get_last_error() != 183  # ERROR_ALREADY_EXISTS


def _fmt(value, unit, digits=0):
    return "n/a" if value is None else f"{value:.{digits}f}{unit}"


class Engine:
    def __init__(self, cfg, power):
        self.cfg = cfg
        self.power = power
        self.controller = Controller(cfg)
        self.perf = PerfCounters()
        self.hw_sources = [("LibreHardwareMonitor", LhmReader(cfg["lhm_url"])),
                           ("HWiNFO shared memory", HwinfoReader())]
        self.apps = AppWatcher()
        self.top = TopProcess()
        self.cpus = psutil.cpu_count() or 1
        self.stop = threading.Event()
        self.wake = threading.Event()
        self.on_change = lambda decision: None
        self.status = "ThermalGuard: starting"
        self._cfg_mtime = self._config_mtime()
        self._last_status = float("-inf")
        self._last_error = None
        self._temp_source = False  # unset, so the first tick logs where temperatures come from
        self._prev = None
        psutil.cpu_percent(None)

    def run(self):
        while not self.stop.is_set():
            try:
                self.tick()
                self._last_error = None
            except Exception as exc:  # keep guarding even if one tick fails
                if str(exc) != self._last_error:
                    log.exception("Tick failed")
                    self._last_error = str(exc)
            self.wake.wait(self.cfg["tick_seconds"])
            self.wake.clear()

    def tick(self):
        now = time.monotonic()
        self._maybe_reload_config()
        total = psutil.cpu_percent(None)
        procs = self.top.read(self.cfg["background_apps"])
        background = min(total, procs.background_cores * 100 / self.cpus)
        pct = total - background  # only your own work picks the mode
        counters = self.perf.read()
        hw, source = self._read_hw()
        temp = hw.temp_c if hw else None
        self._note_temp_source(source)
        sample = Sample(
            t=now,
            load_pct=pct,
            top_cores=procs.top_cores,
            on_ac=on_ac_power(),
            temp_c=temp,
            limited=is_limited(counters),
            app_min_mode=self.apps.min_mode(self.cfg["app_min_modes"], now),
        )
        d = self.controller.update(sample)
        self.power.ensure_active()
        self.power.apply(d.settings)

        details = (f"load {pct:.0f}% + background {background:.0f}% "
                   f"(busiest app: {procs.top_name or 'n/a'} {procs.top_cores:.1f} cores), "
                   f"temp {_fmt(temp, 'C')}, "
                   f"pkg {_fmt(hw.package_w if hw else None, 'W', 1)}, "
                   f"fans {', '.join(f'{k} {v:.0f}rpm' for k, v in hw.fans.items()) if hw and hw.fans else 'n/a'}, "
                   f"perf-limit {_fmt(counters['perf_limit'], '%')} flags {_fmt(counters['limit_flags'], '')}, "
                   f"{'AC' if sample.on_ac else 'battery'}")
        state = (d.mode, d.cap_mhz)
        if state != self._prev:
            cap = f", capped at {d.cap_mhz} MHz" if d.cap_mhz else ""
            log.info("-> %s%s (%s) | %s", d.mode, cap, d.reason, details)
            self._prev = state
        if now - self._last_status >= STATUS_EVERY_S:
            log.info("status %s | %s", d.mode, details)
            self._last_status = now

        cap = f" | cap {d.cap_mhz / 1000:.1f} GHz" if d.cap_mhz else ""
        manual = " (manual)" if self.controller.override else ""
        self.status = f"ThermalGuard: {d.mode.title()}{manual}{cap} | {_fmt(temp, ' C')} | load {pct:.0f}%"
        self.on_change(d)

    def _read_hw(self):
        for name, reader in self.hw_sources:
            data = reader.read()
            if data and data.temp_c is not None:
                return data, name
        return None, None

    def _note_temp_source(self, source):
        if source == self._temp_source:
            return
        if source:
            log.info("Reading CPU temperature from %s", source)
        else:
            log.info("No CPU temperature available (LibreHardwareMonitor not running or its web server "
                     "is off; HWiNFO shared memory off or expired); staying at balanced at most")
        self._temp_source = source

    @staticmethod
    def _config_mtime():
        try:
            return CONFIG_PATH.stat().st_mtime
        except OSError:
            return None

    def _maybe_reload_config(self):
        mtime = self._config_mtime()
        if mtime is None or mtime == self._cfg_mtime:
            return
        self._cfg_mtime = mtime
        try:
            cfg = load_config()
        except (ValueError, KeyError, TypeError) as exc:
            log.error("Ignoring invalid config.json, keeping previous settings: %s", exc)
            return
        self.cfg = self.controller.cfg = cfg
        self.hw_sources[0][1].url = cfg["lhm_url"]
        log.info("Reloaded config.json")


COLORS = {"quiet": (46, 125, 50), "balanced": (21, 101, 192), "performance": (239, 108, 0)}


def make_icon(mode, capped):
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse((2, 2, 62, 62), fill=COLORS[mode] + (255,))
    if capped:
        draw.ellipse((2, 2, 62, 62), outline=(229, 57, 53, 255), width=7)
    try:
        font = ImageFont.truetype("segoeuib.ttf", 34)
    except OSError:
        font = ImageFont.load_default()
    letter = mode[0].upper()
    box = draw.textbbox((0, 0), letter, font=font)
    draw.text(((64 - (box[2] - box[0])) / 2 - box[0], (64 - (box[3] - box[1])) / 2 - box[1]),
              letter, fill="white", font=font)
    return img


def run_tray(engine):
    import pystray

    def choose(mode):
        def action(icon, item):
            engine.controller.override = mode
            log.info("Manual mode: %s", mode or "automatic")
            engine.wake.set()
        return action

    def is_chosen(mode):
        return lambda item: engine.controller.override == mode

    def open_in_notepad(path):
        return lambda icon, item: subprocess.Popen(["notepad.exe", str(path)])

    def quit_app(icon, item):
        icon.stop()

    menu = pystray.Menu(
        pystray.MenuItem(lambda item: engine.status, None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Automatic", choose(None), checked=is_chosen(None), radio=True),
        pystray.MenuItem("Quiet", choose("quiet"), checked=is_chosen("quiet"), radio=True),
        pystray.MenuItem("Balanced", choose("balanced"), checked=is_chosen("balanced"), radio=True),
        pystray.MenuItem("Performance", choose("performance"), checked=is_chosen("performance"), radio=True),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Open log", open_in_notepad(LOG_PATH)),
        pystray.MenuItem("Edit settings", open_in_notepad(CONFIG_PATH)),
        pystray.MenuItem("Exit (restore power plan)", quit_app),
    )
    icon = pystray.Icon("ThermalGuard", make_icon("balanced", False), "ThermalGuard", menu)
    shown = {"state": None}

    def on_change(decision):
        icon.title = engine.status[:127]
        state = (decision.mode, bool(decision.cap_mhz))
        if state != shown["state"]:
            icon.icon = make_icon(*state)
            shown["state"] = state

    engine.on_change = on_change
    worker = threading.Thread(target=engine.run, name="engine", daemon=True)
    worker.start()
    icon.run()
    engine.stop.set()
    engine.wake.set()
    worker.join(timeout=10)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="ThermalGuard")
    parser.add_argument("--console", action="store_true", help="no tray icon; log to the terminal")
    parser.add_argument("--dry-run", action="store_true", help="read sensors and decide, never change power settings")
    setup = parser.add_mutually_exclusive_group()
    setup.add_argument("--register-task", action="store_true", help="(installer, elevated) start at every logon")
    setup.add_argument("--unregister", action="store_true",
                       help="(uninstaller, elevated) remove the logon task and the ThermalGuard power plan")
    args = parser.parse_args(argv)

    setup_logging(args.console)
    if args.register_task:
        return autostart.register(log)
    if args.unregister:
        return autostart.unregister(log, PLAN_STATE_PATH)
    if not acquire_single_instance():
        log.info("ThermalGuard is already running")
        return 0

    try:
        cfg = load_config()
    except (ValueError, KeyError, TypeError) as exc:
        log.error("config.json is invalid (%s); using built-in defaults", exc)
        cfg = DEFAULTS

    power = PowerManager(dry_run=args.dry_run, state_path=PLAN_STATE_PATH)
    try:
        power.setup()
    except PowerError as exc:
        log.error("Cannot manage power settings: %s", exc)
        return 1

    engine = Engine(cfg, power)
    log.info("ThermalGuard %s started%s (previous power plan %s)", __version__,
             " in dry-run mode" if args.dry_run else "", power.original)
    try:
        if args.console:
            engine.run()
        else:
            try:
                run_tray(engine)
            except ImportError:
                log.warning("pystray/Pillow not installed; running without a tray icon")
                engine.run()
    except KeyboardInterrupt:
        pass
    finally:
        engine.stop.set()
        try:
            power.restore()
            log.info("Stopped; restored power plan %s", power.original)
        except PowerError as exc:
            log.error("Could not restore power plan: %s", exc)
    return 0


if __name__ == "__main__":
    sys.exit(main())
