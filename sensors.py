import ctypes
import fnmatch
import functools
import json
import re
import struct
import time
import urllib.request
from ctypes import wintypes
from dataclasses import dataclass
from typing import Optional

import psutil

from controller import RANK

# ---------------------------------------------------------------- PDH counters

_pdh = ctypes.WinDLL("pdh")
PDH_FMT_DOUBLE = 0x00000200


class _FmtValue(ctypes.Structure):
    _fields_ = [("CStatus", wintypes.DWORD), ("doubleValue", ctypes.c_double)]


_pdh.PdhOpenQueryW.argtypes = [wintypes.LPCWSTR, ctypes.c_size_t, ctypes.POINTER(wintypes.HANDLE)]
_pdh.PdhAddEnglishCounterW.argtypes = [wintypes.HANDLE, wintypes.LPCWSTR, ctypes.c_size_t,
                                       ctypes.POINTER(wintypes.HANDLE)]
_pdh.PdhCollectQueryData.argtypes = [wintypes.HANDLE]
_pdh.PdhGetFormattedCounterValue.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                             ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(_FmtValue)]


class PerfCounters:
    # English names on purpose: localized Windows translates counter names.
    PATHS = {
        "perf_limit": r"\Processor Information(_Total)\% Performance Limit",
        "limit_flags": r"\Processor Information(_Total)\Performance Limit Flags",
        "perf_pct": r"\Processor Information(_Total)\% Processor Performance",
    }

    def __init__(self):
        self._query = wintypes.HANDLE()
        self._counters = {}
        if _pdh.PdhOpenQueryW(None, 0, ctypes.byref(self._query)) != 0:
            return
        for key, path in self.PATHS.items():
            handle = wintypes.HANDLE()
            if _pdh.PdhAddEnglishCounterW(self._query, path, 0, ctypes.byref(handle)) == 0:
                self._counters[key] = handle
        _pdh.PdhCollectQueryData(self._query)

    def read(self):
        out = dict.fromkeys(self.PATHS)
        if not self._counters or _pdh.PdhCollectQueryData(self._query) != 0:
            return out
        for key, handle in self._counters.items():
            value = _FmtValue()
            if _pdh.PdhGetFormattedCounterValue(handle, PDH_FMT_DOUBLE, None, ctypes.byref(value)) == 0:
                out[key] = value.doubleValue
        return out


class _FmtItem(ctypes.Structure):
    _fields_ = [("szName", wintypes.LPWSTR), ("FmtValue", _FmtValue)]


_pdh.PdhGetFormattedCounterArrayW.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
                                              ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
PDH_MORE_DATA = 0x800007D2
PDH_FMT_NOCAP100 = 0x00008000


def process_key(name):
    """'SearchIndexer.exe' / PDH's 'chrome#3' -> 'searchindexer' / 'chrome'."""
    name = name.split("#", 1)[0].lower()
    return name[:-4] if name.endswith(".exe") else name


@dataclass
class ProcessLoad:
    top_name: Optional[str] = None
    top_cores: float = 0.0  # busiest non-background process
    background_cores: float = 0.0  # all background processes together


@functools.lru_cache(maxsize=8)
def _background_regex(patterns):
    return re.compile("|".join(fnmatch.translate(process_key(p)) for p in patterns) or "(?!)")


def summarize(items, background=()):
    """Split (name, percent) pairs into the busiest real app and background load.

    background holds names or wildcards ("Dell*"), with or without ".exe".
    """
    regex = _background_regex(tuple(background))
    out = ProcessLoad()
    for name, pct in items:
        key = process_key(name)
        if key in ("_total", "idle"):
            continue
        if key == "system" or regex.match(key):
            out.background_cores += pct / 100
        elif pct / 100 > out.top_cores:
            out.top_name, out.top_cores = name, pct / 100
    return out


class TopProcess:
    """Per-process CPU, in cores (1.0 = one core fully busy).

    One PDH call covers every process; per-process psutil scans took ~1 s here.
    """

    def __init__(self):
        self._query = wintypes.HANDLE()
        self._counter = wintypes.HANDLE()
        # No priming collection here: the first read() then has no baseline and
        # returns nothing, instead of a wild rate measured over a few milliseconds.
        self._ok = (_pdh.PdhOpenQueryW(None, 0, ctypes.byref(self._query)) == 0 and
                    _pdh.PdhAddEnglishCounterW(self._query, r"\Process(*)\% Processor Time", 0,
                                               ctypes.byref(self._counter)) == 0)

    def read(self, background=()):
        if not self._ok or _pdh.PdhCollectQueryData(self._query) != 0:
            return ProcessLoad()
        fmt = PDH_FMT_DOUBLE | PDH_FMT_NOCAP100
        for _ in range(3):  # buffer can be outgrown if a process starts between the two calls
            size, count = wintypes.DWORD(0), wintypes.DWORD(0)
            status = _pdh.PdhGetFormattedCounterArrayW(self._counter, fmt, ctypes.byref(size),
                                                       ctypes.byref(count), None)
            if status & 0xFFFFFFFF != PDH_MORE_DATA:
                return ProcessLoad()
            buf = (ctypes.c_byte * size.value)()
            status = _pdh.PdhGetFormattedCounterArrayW(self._counter, fmt, ctypes.byref(size),
                                                       ctypes.byref(count), buf)
            if status == 0:
                break
        else:
            return ProcessLoad()
        items = ctypes.cast(buf, ctypes.POINTER(_FmtItem))
        valid = ((items[i].szName, items[i].FmtValue.doubleValue) for i in range(count.value)
                 if items[i].FmtValue.CStatus in (0, 1))
        return summarize(valid, background)


def is_limited(counters):
    limit, flags = counters["perf_limit"], counters["limit_flags"]
    return (limit is not None and limit < 95) or bool(flags)


# ------------------------------------------------------- HWiNFO shared memory

_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_k32.OpenFileMappingW.restype = wintypes.HANDLE
_k32.OpenFileMappingW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
_k32.MapViewOfFile.restype = ctypes.c_void_p
_k32.MapViewOfFile.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.c_size_t]
_k32.UnmapViewOfFile.argtypes = [ctypes.c_void_p]
_k32.CloseHandle.argtypes = [wintypes.HANDLE]

FILE_MAP_READ = 0x0004
HEADER = struct.Struct("<4sIIqIIIIII")
READING = struct.Struct("<III128s128s16sdddd")
SIGNATURE = b"HWiS"
TEMP, FAN, POWER = 1, 3, 5
STALE_AFTER_S = 120
MAX_SIZE = 16 * 1024 * 1024


@dataclass
class HwData:
    poll_time: int
    temp_c: Optional[float]
    package_w: Optional[float]
    fans: dict


def _cpu_temp(temps):
    for name in ("CPU Package", "Core Max"):
        if name in temps:
            return temps[name]
    # HWiNFO says "Core #0", LibreHardwareMonitor "CPU Core #1"
    cores = [v for k, v in temps.items() if "Core #" in k and "Distance" not in k]
    return max(cores) if cores else None


def parse_hwinfo(buf):
    sig, _ver, _rev, poll_time, _so, _ss, _sn, offset, size, count = HEADER.unpack_from(buf, 0)
    if sig != SIGNATURE:
        return None
    temps, fans, package_w = {}, {}, None
    for i in range(count):
        rtype, _sensor, _rid, label, _user, _unit, value = READING.unpack_from(buf, offset + i * size)[:7]
        label = label.split(b"\0", 1)[0].decode("latin-1")
        if rtype == TEMP:
            temps.setdefault(label, value)
        elif rtype == FAN:
            fans[label] = value
        elif rtype == POWER and label == "CPU Package Power" and package_w is None:
            package_w = value
    return HwData(poll_time, _cpu_temp(temps), package_w, fans)


class HwinfoReader:
    """Reads HWiNFO's sensor shared memory (Settings > Shared Memory Support)."""

    NAME = "Global\\HWiNFO_SENS_SM2"

    def __init__(self):
        self._handle = None
        self._view = None

    def _open(self):
        handle = _k32.OpenFileMappingW(FILE_MAP_READ, False, self.NAME)
        if not handle:
            return False
        view = _k32.MapViewOfFile(handle, FILE_MAP_READ, 0, 0, 0)
        if not view:
            _k32.CloseHandle(handle)
            return False
        self._handle, self._view = handle, view
        return True

    def close(self):
        if self._view:
            _k32.UnmapViewOfFile(self._view)
        if self._handle:
            _k32.CloseHandle(self._handle)
        self._handle = self._view = None

    def read(self):
        if self._view is None and not self._open():
            return None
        fields = HEADER.unpack(ctypes.string_at(self._view, HEADER.size))
        total = fields[7] + fields[8] * fields[9]
        data = None
        if fields[0] == SIGNATURE and total <= MAX_SIZE:
            data = parse_hwinfo(ctypes.string_at(self._view, total))
        if data is None or abs(time.time() - data.poll_time) > STALE_AFTER_S:
            self.close()  # HWiNFO exited or stopped sharing; reopen on a later tick
            return None
        return data


# ------------------------------------------- LibreHardwareMonitor web server

_NUMBER = re.compile(r"-?\d+(?:[.,]\d+)?")
_LHM_GROUPS = {"Temperatures": TEMP, "Fans": FAN, "Powers": POWER}


def _lhm_number(text):
    match = _NUMBER.search(text or "")
    if not match:
        return None
    value = float(match.group().replace(",", "."))  # some locales write "65,0 °C"
    if "°F" in text:  # LHM can be set to Fahrenheit; everything here is Celsius
        value = round((value - 32) * 5 / 9, 1)
    return value


def parse_lhm(tree, now):
    """Pulls CPU temperature, package power and fans out of LHM's data.json.

    Sensors sit under hardware nodes (the CPU has ImageURL ".../cpu.png") in
    groups named "Temperatures", "Powers", "Fans"; values are display strings.
    """
    temps, fans, package_w = {}, {}, None

    def walk(node, on_cpu, group):
        nonlocal package_w
        on_cpu = on_cpu or "cpu" in node.get("ImageURL", "")
        group = _LHM_GROUPS.get(node.get("Text"), group)
        children = node.get("Children") or []
        if not children:
            value = _lhm_number(node.get("Value"))
            if value is None:
                return
            if group == TEMP and on_cpu:
                temps.setdefault(node["Text"], value)
            elif group == FAN:
                fans[node["Text"]] = value
            elif group == POWER and on_cpu and node.get("Text") == "CPU Package" and package_w is None:
                package_w = value
        for child in children:
            walk(child, on_cpu, group)

    walk(tree, False, None)
    if not (temps or fans or package_w is not None):
        return None
    return HwData(int(now), _cpu_temp(temps), package_w, fans)


class LhmReader:
    """Reads LibreHardwareMonitor (Options > Remote Web Server > Run)."""

    RETRY_AFTER_S = 30  # don't slow every tick down while LHM isn't running

    def __init__(self, url):
        self.url = url
        self._retry_at = 0.0
        # Straight to localhost, never through a corporate proxy
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def read(self):
        now = time.monotonic()
        if not self.url or now < self._retry_at:
            return None
        try:
            with self._opener.open(self.url, timeout=0.5) as resp:
                data = parse_lhm(json.loads(resp.read().decode("utf-8-sig")), time.time())
        except (OSError, ValueError):
            data = None
        if data is None or data.temp_c is None:
            self._retry_at = now + self.RETRY_AFTER_S
            return None
        return data


# ------------------------------------------------------------- misc sources

def on_ac_power():
    battery = psutil.sensors_battery()
    return battery is None or battery.power_plugged is not False


class AppWatcher:
    def __init__(self, refresh_s=10):
        self.refresh_s = refresh_s
        self._names = set()
        self._at = float("-inf")

    def min_mode(self, rules, now):
        if not rules:
            return None
        if now - self._at >= self.refresh_s:
            self._names = {(p.info["name"] or "").lower() for p in psutil.process_iter(["name"])}
            self._at = now
        found = [mode for app, mode in rules.items() if app.lower() in self._names]
        return max(found, key=RANK.get) if found else None
