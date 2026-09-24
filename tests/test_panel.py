from PIL import Image

from kitchen_display.hw import panel


def _img():
    return Image.new("RGB", (800, 480), (255, 255, 255))


def test_null_panel_records_what_it_was_shown():
    p = panel.NullPanel()
    img = _img()
    p.show(img)
    assert p.shown == [img]


def test_png_panel_writes_the_file(tmp_path):
    dest = tmp_path / "out.png"
    panel.PngPanel(str(dest)).show(_img())
    assert dest.exists()
    with Image.open(dest) as im:
        assert im.size == (800, 480)


def test_inky_panel_does_not_import_inky_until_shown():
    # Constructing must be safe on a Mac, where the inky library is not installed.
    panel.InkyPanel()


def test_settings_fall_back_to_the_defaults_when_config_py_is_absent():
    from kitchen_display import settings

    cfg = settings.load()
    assert cfg["refresh_interval_s"] == 3600
    assert cfg["staleness_ceiling_s"] == 10800
