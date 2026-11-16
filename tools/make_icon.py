"""Renders assets/icon.svg into assets/ThermalGuard.ico (exe, installer, Start menu).

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


def render(exe, size, workdir):
    page = workdir / f"icon{size}.html"
    out = workdir / f"icon{size}.png"
    svg = SMALL_SVG if size <= SMALL_UP_TO else SVG
    page.write_text(PAGE.format(size=size, svg=svg.as_uri()), encoding="utf-8")
    subprocess.run([exe, "--headless=new", "--disable-gpu", "--hide-scrollbars",
                    f"--user-data-dir={workdir / 'profile'}", "--allow-file-access-from-files",
                    "--default-background-color=00000000", f"--window-size={size},{size}",
                    "--force-device-scale-factor=1", f"--screenshot={out}", page.as_uri()],
                   check=True, capture_output=True, timeout=60)
    img = Image.open(out).convert("RGBA")
    return img.crop((0, 0, size, size))


def main():
    exe = browser()
    work = Path(tempfile.mkdtemp(prefix="tg-icon-"))
    try:
        images = [render(exe, size, work) for size in SIZES]
        images[-1].save(ICO, format="ICO", sizes=[(s, s) for s in SIZES], append_images=images[:-1])
        preview = ROOT / "build" / "icon-preview.png"
        preview.parent.mkdir(exist_ok=True)
        sheet = Image.new("RGBA", (sum(SIZES) + 10 * len(SIZES), 256), (0, 0, 0, 0))
        x = 0
        for img in images:
            sheet.paste(img, (x, 256 - img.height))
            x += img.width + 10
        sheet.save(preview)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    print(f"wrote {ICO} and {preview}")


if __name__ == "__main__":
    main()
