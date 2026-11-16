"""Draws benchmark/benchmark.png from two runs of tools/benchmark.py.

    python tools/plot_benchmark.py                          # benchmark/without.csv vs benchmark/with.csv
    python tools/plot_benchmark.py A.csv B.csv --names "Without" "With"

Needs matplotlib (pip install -r requirements-bench.txt).
"""
import argparse
import csv
import json
import urllib.request
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "benchmark"
# MATLAB's default line colours: orange for the baseline, blue for ThermalGuard
COLORS = ["#D95319", "#0072BD"]
PHASE_SHADE = {"idle": "#FFFFFF", "one core": "#F2F2F2", "all cores": "#E6E6E6", "cooldown": "#FFFFFF"}

MATLAB_STYLE = {
    "font.family": "sans-serif",
    "font.sans-serif": ["Arial", "DejaVu Sans"],
    "font.size": 9,
    "axes.linewidth": 0.6,
    "axes.edgecolor": "#262626",
    "axes.labelcolor": "#262626",
    "axes.titlesize": 10,
    "axes.titleweight": "bold",
    "axes.grid": True,
    "axes.axisbelow": True,
    "grid.color": "#DCDCDC",
    "grid.linewidth": 0.5,
    "xtick.direction": "in",
    "ytick.direction": "in",
    "xtick.top": True,
    "ytick.right": True,
    "xtick.color": "#262626",
    "ytick.color": "#262626",
    "lines.linewidth": 1.4,
    "legend.frameon": True,
    "legend.edgecolor": "#262626",
    "legend.fancybox": False,
    "legend.framealpha": 1,
    "figure.facecolor": "white",
    "savefig.facecolor": "white",
}


def load(path):
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    def col(name):
        return np.array([float(r[name]) if r[name] not in ("", "None") else np.nan for r in rows])

    return {
        "t": col("t") / 60, "phase": [r["phase"] for r in rows],
        "temp": col("cpu_temp"), "power": col("pkg_w"), "clock": col("clock_mhz") / 1000,
        "gpu": col("gpu_temp"), "work": col("work_rate") / 1e6,
    }


def smooth(y, n=3):
    if len(y) < n:
        return y
    pad = np.pad(y, (n // 2, n - 1 - n // 2), mode="edge")
    return np.convolve(pad, np.ones(n) / n, mode="valid")


def phase_spans(run):
    """(phase, start, end) in minutes; each span runs up to the next phase's first sample."""
    spans, start, t = [], 0, run["t"]
    for i in range(1, len(run["phase"]) + 1):
        if i == len(run["phase"]) or run["phase"][i] != run["phase"][start]:
            spans.append((run["phase"][start], t[start] if start else 0.0, t[i] if i < len(t) else t[-1]))
            start = i
    return spans


def summary(run):
    out = {}
    phases = np.array(run["phase"])
    for name in dict.fromkeys(run["phase"]):
        m = phases == name
        dt = np.gradient(run["t"])[m] * 60  # seconds per sample
        out[name] = {
            "temp": np.nanmean(run["temp"][m]), "temp_max": np.nanmax(run["temp"][m]),
            "power": np.nanmean(run["power"][m]), "clock": np.nanmean(run["clock"][m]),
            "work": np.nansum(run["work"][m] * dt),  # million hashes
            "joules": np.nansum(run["power"][m] * dt),
        }
    return out


def cpu_name():
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        tree = json.loads(opener.open("http://127.0.0.1:8085/data.json", timeout=1).read().decode("utf-8-sig"))
        stack = [tree]
        while stack:
            node = stack.pop()
            if "cpu" in node.get("ImageURL", ""):
                return node["Text"]
            stack.extend(node.get("Children") or [])
    except (OSError, ValueError, KeyError):
        pass
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="*", default=[OUT_DIR / "without.csv", OUT_DIR / "with.csv"])
    ap.add_argument("--names", nargs=2, default=["Without ThermalGuard", "With ThermalGuard"])
    ap.add_argument("--out", default=OUT_DIR / "benchmark.png")
    ap.add_argument("--cpu", default=None, help="CPU name for the title (default: ask LibreHardwareMonitor)")
    args = ap.parse_args()

    runs = [load(f) for f in args.files]
    stats = [summary(r) for r in runs]
    plt.rcParams.update(MATLAB_STYLE)
    fig = plt.figure(figsize=(13, 8.6), dpi=150)
    grid = fig.add_gridspec(4, 2, width_ratios=[2.6, 1], hspace=0.12, wspace=0.22,
                            left=0.06, right=0.98, top=0.90, bottom=0.07)

    # ---- left: time series -------------------------------------------------
    series = [("temp", "CPU temperature (°C)"), ("power", "CPU package power (W)"),
              ("clock", "Average core clock (GHz)"), ("work", "Work done (M hashes/s)")]
    axes = [fig.add_subplot(grid[i, 0]) for i in range(4)]
    for ax in axes[1:]:
        ax.sharex(axes[0])
    for ax, (key, label) in zip(axes, series):
        for name, a, b in phase_spans(runs[0]):
            ax.axvspan(a, b, color=PHASE_SHADE.get(name, "#FFFFFF"), zorder=0, lw=0)
        for run, color, name in zip(runs, COLORS, args.names):
            ax.plot(run["t"], smooth(run[key]), color=color, label=name)
        ax.set_ylabel(label)
        ax.margins(x=0)
        if ax is not axes[-1]:
            ax.tick_params(labelbottom=False)
    axes[0].axhline(90, color="#A2142F", ls="--", lw=0.9)
    lo, hi = axes[0].get_ylim()
    axes[0].set_ylim(lo, max(hi, 93))
    axes[0].text(0.995, 90.4, "90 °C: ThermalGuard starts capping the clock", color="#A2142F", fontsize=7.5,
                 va="bottom", ha="right", transform=axes[0].get_yaxis_transform())
    axes[-1].set_xlabel("Time (minutes)")
    for name, a, b in phase_spans(runs[0]):
        axes[0].text((a + b) / 2, 1.02, name, transform=axes[0].get_xaxis_transform(),
                     ha="center", va="bottom", fontsize=8.5, color="#404040")
    axes[0].legend(loc="upper left", bbox_to_anchor=(0, 1.32), ncol=2)

    # ---- right: per-phase summary -----------------------------------------
    phases = [p for p in stats[0] if all(p in s for s in stats)]
    work_phases = [p for p in phases if p not in ("idle", "cooldown")]
    bars = [("temp", "Average CPU temperature (°C)", phases, "{:.0f}"),
            ("power", "Average package power (W)", phases, "{:.0f}"),
            ("work_pct", f"Work done (% of {args.names[0]})", work_phases, "{:.0f}%"),
            ("eff", "Efficiency (thousand hashes per joule)", work_phases, "{:.1f}")]
    for s in stats:
        for p in phases:
            first = stats[0][p]
            s[p]["work_pct"] = 100 * s[p]["work"] / first["work"] if first["work"] else 0
            s[p]["eff"] = 1000 * s[p]["work"] / s[p]["joules"] if s[p]["joules"] else 0
    right = grid[:, 1].subgridspec(4, 1, hspace=0.55)
    for i, (key, title, ph, fmt) in enumerate(bars):
        ax = fig.add_subplot(right[i])
        x = np.arange(len(ph))
        width = 0.38
        for j, (s, color, name) in enumerate(zip(stats, COLORS, args.names)):
            vals = [s[p][key] for p in ph]
            rect = ax.bar(x + (j - 0.5) * width, vals, width, color=color, label=name, edgecolor="#262626", lw=0.4)
            ax.bar_label(rect, labels=[fmt.format(v) for v in vals], fontsize=7, padding=1.5)
        ax.set_xticks(x, ph)
        ax.set_title(title, fontsize=9, fontweight="normal")
        ax.margins(y=0.18)
        ax.grid(axis="x", visible=False)

    cpu = args.cpu or cpu_name()
    fig.suptitle("ThermalGuard: the same workload with and without it" + (f"  ·  {cpu}" if cpu else ""),
                 fontsize=12, fontweight="bold", x=0.06, ha="left", y=0.985)
    Path(args.out).parent.mkdir(exist_ok=True)
    fig.savefig(args.out)
    print(f"Saved {args.out}\n")

    print(f"{'phase':<10}" + "".join(f"{n[:22]:>24}" for n in args.names))
    for p in phases:
        cells = [f"{s[p]['temp']:.0f} C (max {s[p]['temp_max']:.0f})  {s[p]['power']:.0f} W" for s in stats]
        print(f"{p:<10}" + "".join(f"{c:>24}" for c in cells))
    for p in work_phases:
        print(f"{p:<10}" + "".join(f"{'work ' + format(s[p]['work_pct'], '.0f') + '%':>24}" for s in stats))


if __name__ == "__main__":
    main()
