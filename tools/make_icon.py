"""Renders assets/icon.svg into assets/ThermalGuard.ico (exe, installer, Start menu)
and assets/tray.svg into the six tray icons in assets/tray/ (3 modes x capped).

Run after editing the SVG:  python tools/make_icon.py
Uses headless Microsoft Edge (or Chrome) as the SVG renderer, so nothing extra
needs installing on Windows. Each size is rendered separately for crisp edges.
"""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SVG = ROOT / "assets" / "icon.svg"
SMALL_SVG = ROOT / "assets" / "icon-small.svg"  # bolder, fewer lines
SMALL_UP_TO = 24
TRAY_SVG = ROOT / "assets" / "tray.svg"
TRAY_DIR = ROOT / "assets" / "tray"
TRAY_SIZE = 64  # Windows scales it to 16-32 px depending on display scaling
TRAY_COLORS = {  # mode: (gradient top, gradient bottom)
    "quiet": ("#4CAF50", "#1B5E20"),
    "balanced": ("#3B8BF5", "#173FA8"),
    "performance": ("#FF9A2E", "#D84300"),
}
CAP_RED = "#E53935"
ICO = ROOT / "assets" / "ThermalGuard.ico"
SIZES = [16, 20, 24, 32, 40, 48, 64, 128, 256]
BROWSERS = [
    r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe",
    r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe",
    r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
    r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe",
]

PAGE = """<!doctype html><html><head><style>
html, body {{ margin: 0; background: transparent; }}
img {{ display: block; width: {size}px; height: {size}px; }}
</style></head><body><img src="{svg}"></body></html>"""


def browser():
    for candidate in BROWSERS:
        path = os.path.expandvars(candidate)
        if os.path.exists(path):
            return path
    sys.exit("Microsoft Edge or Google Chrome is needed to render the SVG")


def render(exe, svg, size, workdir):
    page = workdir / f"{svg.stem}-{size}.html"
    out = workdir / f"{svg.stem}-{size}.png"
    page.write_text(PAGE.format(size=size, svg=svg.as_uri()), encoding="utf-8")
    subprocess.run([exe, "--headless=new", "--disable-gpu", "--hide-scrollbars",
                    f"--user-data-dir={workdir / 'profile'}", "--allow-file-access-from-files",
                    "--default-background-color=00000000", f"--window-size={size},{size}",
                    "--force-device-scale-factor=1", f"--screenshot={out}", page.as_uri()],
                   check=True, capture_output=True, timeout=60)
    img = Image.open(out).convert("RGBA")
    return img.crop((0, 0, size, size))


def tray_icons(exe, work):
    """assets/tray/<mode>[-capped].png from the tray.svg template."""
    template = TRAY_SVG.read_text(encoding="utf-8")
    TRAY_DIR.mkdir(exist_ok=True)
    out = {}
    for mode, (top, bottom) in TRAY_COLORS.items():
        for capped in (False, True):
            name = f"{mode}-capped" if capped else mode
            svg = work / f"tray-{name}.svg"
            svg.write_text(template.format(top=top, bottom=bottom, rim=CAP_RED if capped else "none",
                                           mercury=CAP_RED if capped else "#FFFFFF",
                                           bulb_ring=10 if capped else 0), encoding="utf-8")
            img = render(exe, svg, TRAY_SIZE, work)
            img.save(TRAY_DIR / f"{name}.png")
            out[name] = img
    return out


def save_preview(app_images, tray):
    """build/icon-preview.png: app icon sizes, then the tray icons on light and dark taskbars."""
    preview = ROOT / "build" / "icon-preview.png"
    preview.parent.mkdir(exist_ok=True)
    width = max(sum(SIZES) + 10 * len(SIZES), 6 * 90)
    sheet = Image.new("RGBA", (width, 256 + 2 * 70), (255, 255, 255, 255))
    x = 0
    for img in app_images:
        sheet.alpha_composite(img, (x, 256 - img.height))
        x += img.width + 10
    for row, bg in enumerate([(243, 243, 243, 255), (32, 32, 32, 255)]):
        y = 256 + row * 70
        sheet.paste(bg, (0, y, width, y + 70))
        for i, img in enumerate(tray.values()):
            for j, size in enumerate((16, 24, 32)):
                sheet.alpha_composite(img.resize((size, size), Image.LANCZOS),
                                      (10 + i * 90 + j * 26, y + 35 - size // 2))
    sheet.convert("RGB").save(preview)
    return preview


def main():
    exe = browser()
    work = Path(tempfile.mkdtemp(prefix="tg-icon-"))
    try:
        images = [render(exe, SMALL_SVG if size <= SMALL_UP_TO else SVG, size, work) for size in SIZES]
        images[-1].save(ICO, format="ICO", sizes=[(s, s) for s in SIZES], append_images=images[:-1])
        tray = tray_icons(exe, work)
        preview = save_preview(images, tray)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    print(f"wrote {ICO}, {TRAY_DIR} and {preview}")


if __name__ == "__main__":
    main()
