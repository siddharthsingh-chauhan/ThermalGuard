"""Logs every lid and display state change Windows reports, with timestamps.

Diagnostic for "closing the lid does nothing": if no "lid closed" lines show
up while the lid is shut, Windows never got the signal from the lid sensor.

    python tools\\lid_monitor.py [seconds] [logfile]
"""
import ctypes
import sys
import time
import uuid
from ctypes import wintypes

PBT_POWERSETTINGCHANGE = 0x8013
DEVICE_NOTIFY_CALLBACK = 2
WATCH = {
    uuid.UUID("ba3e0f4d-b817-4094-a2d1-d56379e6a0f3"): ("lid", {0: "CLOSED", 1: "open"}),
    uuid.UUID("6fe69556-704a-47a0-8f24-c28d936fda47"): ("display", {0: "off", 1: "on", 2: "dimmed"}),
    uuid.UUID("5d3e9a59-e9d5-4b00-a6bd-ff34ff516548"): ("power source", {0: "AC", 1: "battery", 2: "UPS"}),
}


class GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD),
                ("Data4", ctypes.c_ubyte * 8)]

    @classmethod
    def of(cls, u):
        return cls.from_buffer_copy(u.bytes_le)


CALLBACK = ctypes.WINFUNCTYPE(wintypes.ULONG, ctypes.c_void_p, wintypes.ULONG, ctypes.c_void_p)


class SubscribeParams(ctypes.Structure):
    _fields_ = [("Callback", CALLBACK), ("Context", ctypes.c_void_p)]


def main():
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 600
    out = open(sys.argv[2] if len(sys.argv) > 2 else "lid_monitor.log", "a", buffering=1, encoding="utf-8")

    def log(msg):
        out.write(f"{time.strftime('%H:%M:%S')} {msg}\n")

    @CALLBACK
    def on_event(_ctx, kind, setting):
        if kind == PBT_POWERSETTINGCHANGE and setting:
            guid = uuid.UUID(bytes_le=ctypes.string_at(setting, 16))
            value = ctypes.c_ulong.from_address(setting + 20).value
            if guid in WATCH:
                name, labels = WATCH[guid]
                log(f"{name}: {labels.get(value, value)}")
        return 0

    powrprof = ctypes.WinDLL("powrprof")
    params = SubscribeParams(on_event, None)
    handles = []
    for u in WATCH:
        h = ctypes.c_void_p()
        rc = powrprof.PowerSettingRegisterNotification(ctypes.byref(GUID.of(u)), DEVICE_NOTIFY_CALLBACK,
                                                       ctypes.byref(params), ctypes.byref(h))
        if rc:
            log(f"register {WATCH[u][0]} failed: {rc}")
        handles.append(h)
    log(f"monitoring for {seconds:.0f} s (current states follow)")
    time.sleep(seconds)
    for h in handles:
        powrprof.PowerSettingUnregisterNotification(h)
    log("done")


if __name__ == "__main__":
    main()
