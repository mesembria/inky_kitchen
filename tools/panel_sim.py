"""Quantize a render to the seven panel inks.

A --out PNG is not what the panel shows. Review the quantized version, never
the raw PNG, when judging whether a visual change works.
"""
import sys

from PIL import Image

PANEL = [(0, 0, 0), (255, 255, 255), (0, 255, 0), (0, 0, 255),
         (255, 0, 0), (255, 255, 0), (255, 140, 0)]


def _palette_image():
    pal = Image.new("P", (1, 1))
    flat = [v for rgb in PANEL for v in rgb]
    pal.putpalette(flat + flat[:3] * (256 - len(PANEL)))
    return pal


def quantize(img):
    """Return a copy with every pixel snapped to its nearest panel ink (no dither)."""
    return (img.convert("RGB")
            .quantize(palette=_palette_image(), dither=Image.Dither.NONE)
            .convert("RGB"))


def main(argv=None):
    argv = argv or sys.argv[1:]
    if len(argv) != 2:
        print("usage: python tools/panel_sim.py IN.png OUT.png", file=sys.stderr)
        return 2
    with Image.open(argv[0]) as im:
        quantize(im).save(argv[1])
    print("Wrote", argv[1])
    return 0


if __name__ == "__main__":
    sys.exit(main())
