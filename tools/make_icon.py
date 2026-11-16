"""Draws assets/ThermalGuard.ico (exe + installer icon). Run once after changing it."""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

SIZE = 256
OUT = Path(__file__).resolve().parents[1] / "assets" / "ThermalGuard.ico"


def draw():
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((8, 8, SIZE - 8, SIZE - 8), fill=(21, 101, 192, 255))
    try:
        font = ImageFont.truetype("segoeuib.ttf", 150)
    except OSError:
        font = ImageFont.load_default()
    box = d.textbbox((0, 0), "T", font=font)
    d.text(((SIZE - (box[2] - box[0])) / 2 - box[0], (SIZE - (box[3] - box[1])) / 2 - box[1]),
           "T", fill="white", font=font)
    return img


if __name__ == "__main__":
    OUT.parent.mkdir(exist_ok=True)
    draw().save(OUT, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print(f"wrote {OUT}")
