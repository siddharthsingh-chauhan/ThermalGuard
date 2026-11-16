"""Before/after benchmark: the same workload, with and without ThermalGuard.

Records CPU temperature, package power, core clock, GPU temperature and work
done every 2 s through four phases (idle, one busy core, all cores busy,
cooldown), reading sensors from LibreHardwareMonitor's web server.

    python tools/benchmark.py --label with        # ThermalGuard on Automatic
    (tray icon > Exit, wait for the fans to settle)
    python tools/benchmark.py --label without
    python tools/plot_benchmark.py                # draws benchmark/benchmark.png

Fair comparison: plugged in, same surface, same apps open, nothing else
heavy running. Each run waits until the CPU has cooled down before starting.
Total ~17 min per run (use --minutes to scale the phases).
"""
import argparse
import csv
import hashlib
import json
import multiprocessing as mp
import os
import re
import sys
import time
import urllib.request
from pathlib import Path

import psutil

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from sensors import parse_lhm  # noqa: E402

OUT_DIR = ROOT / "benchmark"
LHM_URL = "http://127.0.0.1:8085/data.json"
TG_LOG = Path(os.environ.get("LOCALAPPDATA", "")) / "ThermalGuard" / "thermalguard.log"
SAMPLE_S = 2.0
# name, seconds, busy workers (None = one per logical CPU)
PHASES = [("idle", 180, 0), ("one core", 180, 1), ("all cores", 480, None), ("cooldown", 150, 0)]
CHUNK = 64  # hashes per counter update


def burn(counter, stop):
    """One busy core: hash a 4 KB buffer forever, counting hashes.

    Each worker has its own lock-free counter, so stopping a worker can never
    leave a shared lock held.
    """
    data = os.urandom(4096)
    while not stop.is_set():
        for _ in range(CHUNK):
            hashlib.sha256(data).digest()
        counter.value += CHUNK


class Work:
    def __init__(self):
        self.counters = []  # every worker ever started, so finished work still counts

    def new_counter(self):
        c = mp.RawValue("Q", 0)
        self.counters.append(c)
        return c

    @property
    def value(self):
        return sum(c.value for c in self.counters)


_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def read_lhm():
    with _opener.open(LHM_URL, timeout=1.5) as resp:
        return json.loads(resp.read().decode("utf-8-sig"))


def _number(text):
    m = re.search(r"-?\d+(?:[.,]\d+)?", text or "")
    return float(m.group().replace(",", ".")) if m else None


def extra_sensors(tree):
    """Average CPU core clock (MHz) and GPU core temperature from the LHM tree."""
    clocks, gpu = [], []

    def walk(node, hw, group):
        image = node.get("ImageURL", "")
        if image:
            hw = "cpu" if "cpu" in image else "gpu" if any(g in image for g in ("nvidia", "ati", "amd")) else hw
        group = node.get("Text") if node.get("Text") in ("Clocks", "Temperatures") else group
        kids = node.get("Children") or []
        if not kids:
            v = _number(node.get("Value"))
            if v is None:
                return
            if hw == "cpu" and group == "Clocks" and "Core #" in node.get("Text", ""):
                clocks.append(v)
            elif hw == "gpu" and group == "Temperatures" and node.get("Text") == "GPU Core":
                gpu.append(v)
        for k in kids:
            walk(k, hw, group)

    walk(tree, None, None)
    return (sum(clocks) / len(clocks) if clocks else None), (max(gpu) if gpu else None)


def thermalguard_state():
    """(running, last mode/cap from its log)."""
    running = any((p.info["name"] or "").lower() == "thermalguard.exe" for p in psutil.process_iter(["name"]))
    if not running or not TG_LOG.exists():
        return running, "", 0
    with open(TG_LOG, "rb") as f:
        f.seek(max(0, TG_LOG.stat().st_size - 4000))
        lines = f.read().decode("utf-8", "replace").splitlines()
    for line in reversed(lines):
        # "-> mode[, capped at N MHz]" is logged on every mode or cap change ("status" lines omit the cap)
        m = re.search(r"-> (quiet|balanced|performance)(?:, capped at (\d+) MHz)?", line)
        if m:
            return running, m.group(1), int(m.group(2) or 0)
    return running, "", 0


def sample(t0, phase, counter, last):
    tree = read_lhm()
    hw = parse_lhm(tree, time.time())
    clock, gpu = extra_sensors(tree)
    now = time.monotonic()
    work = counter.value
    elapsed = now - last[0]
    rate = (work - last[1]) / elapsed if elapsed > 0.5 else 0.0
    running, mode, cap = thermalguard_state()
    row = {
        "t": round(now - t0, 1), "phase": phase,
        "cpu_temp": hw.temp_c if hw else None, "pkg_w": hw.package_w if hw else None,
        "clock_mhz": round(clock) if clock else None, "gpu_temp": gpu,
        "load_pct": psutil.cpu_percent(None), "work_rate": round(rate),
        "tg_running": int(running), "tg_mode": mode, "tg_cap_mhz": cap,
    }
    return row, (now, work)


def settle(target_c, max_s):
    print(f"Waiting for the CPU to cool to {target_c} °C (max {max_s // 60} min) so both runs start alike...")
    end = time.monotonic() + max_s
    while time.monotonic() < end:
        hw = parse_lhm(read_lhm(), time.time())
        if hw and hw.temp_c is not None:
            print(f"\r  CPU {hw.temp_c:.0f} °C ", end="", flush=True)
            if hw.temp_c <= target_c:
                break
        time.sleep(5)
    print()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--label", required=True, help='name of this run, e.g. "with" or "without"')
    ap.add_argument("--minutes", type=float, default=None, help="scale the phases to this total length")
    ap.add_argument("--settle-c", type=float, default=60, help="start once the CPU is at or below this (°C)")
    args = ap.parse_args()

    try:
        read_lhm()
    except OSError:
        sys.exit("LibreHardwareMonitor's web server isn't answering on port 8085 "
                 "(LibreHardwareMonitor > Options > Remote Web Server > Run).")

    phases = PHASES
    if args.minutes:
        scale = args.minutes * 60 / sum(p[1] for p in PHASES)
        phases = [(n, max(10, int(s * scale)), w) for n, s, w in PHASES]
    cpus = psutil.cpu_count() or 1
    running, _, _ = thermalguard_state()
    print(f"Run '{args.label}': ThermalGuard is {'RUNNING' if running else 'NOT running'}; "
          f"{cpus} logical CPUs; phases: " + ", ".join(f"{n} {s // 60}:{s % 60:02d}" for n, s, _ in phases))

    settle(args.settle_c, 300)
    OUT_DIR.mkdir(exist_ok=True)
    out = OUT_DIR / f"{args.label}.csv"
    counter, stop = Work(), mp.Event()
    workers, rows = [], []
    psutil.cpu_percent(None)
    t0 = time.monotonic()
    last = (t0, 0)
    try:
        for name, seconds, busy in phases:
            want = cpus if busy is None else busy
            while len(workers) > want:
                workers.pop().terminate()
            while len(workers) < want:
                p = mp.Process(target=burn, args=(counter.new_counter(), stop), daemon=True)
                p.start()
                workers.append(p)
            end = time.monotonic() + seconds
            while time.monotonic() < end:
                next_at = time.monotonic() + SAMPLE_S
                try:
                    row, last = sample(t0, name, counter, last)
                    rows.append(row)
                    print(f"\r{name:>9} {row['t']:6.0f}s  CPU {row['cpu_temp'] or 0:5.1f} °C  "
                          f"{row['pkg_w'] or 0:5.1f} W  {row['clock_mhz'] or 0:5.0f} MHz  "
                          f"work {row['work_rate'] / 1000:7.1f}k/s  {row['tg_mode'] or '-':<11}", end="", flush=True)
                except OSError:
                    pass  # one missed LHM reading; keep going
                time.sleep(max(0.0, next_at - time.monotonic()))
            print()
    finally:
        stop.set()
        for p in workers:
            p.terminate()
        if rows:
            with open(out, "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=list(rows[0]))
                w.writeheader()
                w.writerows(rows)
            print(f"Saved {len(rows)} samples to {out}")


if __name__ == "__main__":
    mp.freeze_support()
    main()
