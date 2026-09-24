from PIL import Image

from tools.panel_sim import PANEL, quantize


def test_every_pixel_lands_on_a_panel_ink():
    img = Image.new("RGB", (4, 4))
    img.putdata([(230, 231, 236), (200, 30, 30), (20, 22, 28), (30, 70, 200)] * 4)
    out = quantize(img)
    assert all(rgb in PANEL for _, rgb in out.getcolors())


def test_a_near_white_faint_gray_collapses_to_white():
    # This is the shipped bug the tool exists to catch: it looks fine in a PNG
    # and is invisible on the panel.
    img = Image.new("RGB", (1, 1), (230, 231, 236))
    assert quantize(img).getpixel((0, 0)) == (255, 255, 255)
