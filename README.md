# ThermalGuard

Keeps a Windows laptop cool and quiet without giving up speed when you need it.

ThermalGuard sits in the system tray and switches the CPU between three modes
every couple of seconds, based on what you are actually doing:

| Mode | When | What it does |
|---|---|---|
| **Quiet** (green shield) | browsing, documents, idle | turbo off, efficiency-biased, slows the CPU before spinning fans up |
| **Balanced** (blue shield) | sustained work, or one busy app (single-threaded CAD, compiles) | turbo on |
| **Performance** (orange shield) | heavy all-core load | aggressive turbo |

On top of that it **caps the CPU clock step by step when the CPU gets hot**
(the tray shield gets a red rim and red thermometer) and releases the cap gradually once it has cooled down.

It only changes processor settings in its own power plan (a copy of Balanced
called "ThermalGuard"), so your other plans are never modified. Exiting from
the tray menu switches back to the plan you had before.

### Why not just use the Windows power slider?

The slider is static: "Best performance" runs hot and loud while you read
email; "Best power efficiency" is sluggish when you compile. Background
services (search indexing, antivirus scans, updates, vendor agents) also
trigger turbo and fan noise. ThermalGuard ignores those — only *your* apps
decide the mode (see `background_apps` below).

## Install

1. Download `ThermalGuard-Setup-x.y.z.exe` from
   [Releases](https://github.com/siddharthsingh-chauhan/ThermalGuard/releases).
2. Run it. The installer isn't code-signed, so Windows SmartScreen may say
   "Windows protected your PC" — click **More info → Run anyway**.
3. Optional checkboxes during setup:
   - **Install LibreHardwareMonitor** — recommended. ThermalGuard needs a CPU
     temperature for Performance mode and for heat-based caps; without one it
     stays at Balanced at most and only reacts to Windows' own throttling
     counters. Uses `winget`, so it needs internet.
   - **Hibernate on lid close (battery) and power button** — stops a laptop
     from cooking itself in a bag. See [Lid and power button](#lid-and-power-button).

ThermalGuard starts at every logon through a Task Scheduler task (it needs
admin rights to change power settings, and a task is the only way to start
elevated without a UAC prompt each time).

Requirements: Windows 10 or 11, 64-bit. Built and tested on an Intel laptop;
AMD should work since it only uses standard Windows power settings.

**Uninstall:** Settings → Apps → ThermalGuard. This removes the logon task and
the ThermalGuard power plan and switches back to the plan you used before
ThermalGuard (Balanced if that plan no longer exists). Your settings and logs in
`%LOCALAPPDATA%\ThermalGuard` are kept; LibreHardwareMonitor and lid/power
button settings are left as they are.

## Tray menu

- **Automatic** (default) or a fixed **Quiet / Balanced / Performance**
- **Open log** — every mode change with the reason, plus a status line every 30 s
- **Edit settings** — opens `config.json`; changes apply within a few seconds
- **Exit** — restores your previous power plan

## Temperature sources

Tried in order, first one with a CPU temperature wins:

1. **LibreHardwareMonitor** web server (`Options → Remote Web Server → Run`,
   port 8085). The installer option sets this up.
2. **HWiNFO** shared memory (`Settings → Shared Memory Support`). Note the free
   version switches shared memory off after 12 hours.

## Settings

`%LOCALAPPDATA%\ThermalGuard\config.json` — only include the keys you want to
change; everything else uses the defaults in [config.py](config.py).

| Key | Default | Meaning |
|---|---|---|
| `battery_max_mode` | `"balanced"` | highest mode on battery (`"quiet"` = coolest) |
| `battery_epp_offset` | `20` | extra efficiency bias on battery (0–100 scale) |
| `load.balanced_up_pct` | `25` | total CPU % (your apps) that switches to Balanced |
| `load.performance_up_pct` | `60` | total CPU % that switches to Performance |
| `load.single_thread_up_cores` | `0.8` | one app using this many cores → Balanced |
| `thermal.hot_c` / `cool_c` | `90` / `78` | start capping / start releasing (°C) |
| `thermal.critical_c` | `97` | jump straight to the lowest cap |
| `thermal.freq_caps_mhz` | `[4200, 3800, 3400, 3000, 2600]` | cap steps; set to suit your CPU's max turbo |
| `thermal.no_temp_max_mode` | `"balanced"` | highest mode while no temperature is available |
| `app_min_modes` | `{}` | e.g. `{"SLDWORKS.exe": "balanced"}` keeps turbo while that app is open |
| `background_apps` | indexer, Defender, updates, vendor agents… | names or wildcards whose CPU use doesn't count |
| `lhm_url` | `http://127.0.0.1:8085/data.json` | `""` to skip LibreHardwareMonitor |

Example:

```json
{
  "battery_max_mode": "quiet",
  "app_min_modes": {"blender.exe": "performance"}
}
```

## Lid and power button

`Set-LidPolicy.ps1` (in `C:\Program Files\ThermalGuard\scripts`) sets what the
lid and power button do, in **every** power plan. Run it in an admin PowerShell:

```powershell
& "C:\Program Files\ThermalGuard\scripts\Set-LidPolicy.ps1" -OnBattery Hibernate -PluggedIn Nothing -PowerButton Hibernate
```

| Parameter | Values | Default |
|---|---|---|
| `-OnBattery` | lid close on battery: `Sleep`, `Hibernate`, `Keep` | `Hibernate` |
| `-PluggedIn` | lid close plugged in: `Sleep`, `Hibernate`, `Nothing`, `Keep` | `Keep` |
| `-PowerButton` | `Sleep`, `Hibernate`, `ShutDown`, `Keep` | `Hibernate` |
| `-HibernateAfterMin` | on battery, sleep becomes hibernate after N minutes | unchanged |

`Nothing` when plugged in keeps external monitors running with the lid shut.

### If your laptop runs hot while "asleep" or "off"

Things found while building this, worth checking on any laptop:

- **Modern Standby** (`powercfg /a` says "Standby (S0 Low Power Idle)") can stay
  awake in a bag if a device blocks deep sleep. `powercfg /sleepstudy` shows how
  much each sleep session drained. Hibernate avoids the problem entirely.
- **Fast Startup** makes "Shut down" a partial hibernate; if that hangs, the
  laptop stays on with the screen off. Event Viewer → System → Kernel-Power 41
  after a shutdown is the tell. Turn it off in Control Panel → Power Options →
  Choose what the power buttons do.
- **Wake-armed devices**: `powercfg /devicequery wake_armed`.

## Build from source

Needs Python 3.11 (64-bit), and [Inno Setup 6](https://jrsoftware.org/isinfo.php)
for the installer (`winget install JRSoftware.InnoSetup`).

```powershell
.\build.ps1          # tests, then dist\ThermalGuard\ and dist\ThermalGuard-Setup-<version>.exe
```

Without Inno Setup you still get `dist\ThermalGuard\`; `install.ps1` (admin)
installs that folder directly. Run unpackaged with
`python app.py --console --dry-run` to watch decisions without changing anything.

Releases: bump `version.py`, then push a tag `v<version>` — GitHub Actions
builds the installer and attaches it to a release.

## License

MIT — see [LICENSE](LICENSE). No warranty: ThermalGuard changes Windows power
settings; uninstalling restores Balanced.
