"""Writes processor settings into a dedicated "ThermalGuard" power plan.

Uses a copy of Balanced so the stock plans are never modified, and restores
whichever plan was active before on exit.
"""
import ctypes
import logging
import subprocess
import time
import uuid
from ctypes import wintypes

log = logging.getLogger("thermalguard")

BALANCED = "381b4222-f694-41f0-9685-ff5bb260df2e"
SCHEME = "6f1c2a9e-3d4b-4c8e-9a71-2b5e0d7f4c31"
SUB_PROCESSOR = "54533251-82be-4824-96c1-47b60b740d00"
SETTINGS = {
    "PERFBOOSTMODE": "be337238-0d82-4146-a960-4f3749d470c7",
    "PERFEPP": "36687f9e-e3a5-4dbf-b1dc-15eb381c6863",
    "PROCFREQMAX": "75b0ae3f-bce0-45a7-8c89-c9611c25e100",
    "SYSCOOLPOL": "94d3a615-a899-4ac5-ae2b-e4d8f634367f",
}


class GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]

    @classmethod
    def of(cls, text):
        return cls.from_buffer_copy(uuid.UUID(text).bytes_le)

    def __str__(self):
        return str(uuid.UUID(bytes_le=bytes(self)))


_pp = ctypes.WinDLL("powrprof")
for _fn in (_pp.PowerWriteACValueIndex, _pp.PowerWriteDCValueIndex):
    _fn.argtypes = [wintypes.HKEY, ctypes.POINTER(GUID), ctypes.POINTER(GUID),
                    ctypes.POINTER(GUID), wintypes.DWORD]
_pp.PowerSetActiveScheme.argtypes = [wintypes.HKEY, ctypes.POINTER(GUID)]
_pp.PowerGetActiveScheme.argtypes = [wintypes.HKEY, ctypes.POINTER(ctypes.POINTER(GUID))]
_k32 = ctypes.WinDLL("kernel32")
_k32.LocalFree.argtypes = [ctypes.c_void_p]


class PowerError(RuntimeError):
    pass


def _check(code, what):
    if code != 0:
        raise PowerError(f"{what} failed: {ctypes.FormatError(code).strip()} (error {code})")


def active_scheme():
    ptr = ctypes.POINTER(GUID)()
    _check(_pp.PowerGetActiveScheme(None, ctypes.byref(ptr)), "PowerGetActiveScheme")
    try:
        return str(ptr.contents)
    finally:
        _k32.LocalFree(ctypes.cast(ptr, ctypes.c_void_p))


def _set_active(scheme):
    _check(_pp.PowerSetActiveScheme(None, ctypes.byref(GUID.of(scheme))), "PowerSetActiveScheme")


def _powercfg(*args):
    result = subprocess.run(["powercfg", *args], capture_output=True, text=True, errors="replace",
                            creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode != 0:
        raise PowerError(f"powercfg {' '.join(args)}: {(result.stdout + result.stderr).strip()}")
    return result.stdout


class PowerManager:
    def __init__(self, dry_run=False):
        self.dry_run = dry_run
        self.original = None
        self._applied = {}
        self._last_reassert_log = float("-inf")

    def setup(self):
        current = active_scheme()
        self.original = BALANCED if current == SCHEME else current
        if self.dry_run:
            return
        if SCHEME not in _powercfg("/list").lower():
            _powercfg("/duplicatescheme", BALANCED, SCHEME)
            _powercfg("/changename", SCHEME, "ThermalGuard", "Switched automatically by ThermalGuard")

    def ensure_active(self):
        if self.dry_run:
            return
        current = active_scheme()
        if current != SCHEME:
            if self._applied and time.monotonic() - self._last_reassert_log > 300:
                log.warning("Power plan was switched to %s by something else; switching back. "
                            "Use the tray menu > Exit to stop ThermalGuard.", current)
                self._last_reassert_log = time.monotonic()
            self._applied.clear()

    def apply(self, settings):
        changes = {k: v for k, v in settings.items() if self._applied.get(k) != v}
        if not changes:
            return {}
        if self.dry_run:
            log.info("[dry run] would set %s", changes)
        else:
            scheme, sub = GUID.of(SCHEME), GUID.of(SUB_PROCESSOR)
            for name, value in changes.items():
                setting = GUID.of(SETTINGS[name])
                for write in (_pp.PowerWriteACValueIndex, _pp.PowerWriteDCValueIndex):
                    _check(write(None, ctypes.byref(scheme), ctypes.byref(sub), ctypes.byref(setting), value),
                           f"writing {name}={value}")
            _set_active(SCHEME)
        self._applied.update(changes)
        return changes

    def restore(self):
        if self.dry_run or not self.original:
            return
        _set_active(self.original)
