from PIL import Image, ImageDraw
from inky_weather import render
import pytest


def test_dimensions():
    assert render.WIDTH == 800
    assert render.HEIGHT == 480


def test_temp_color_hot_is_red_ish():
    r, g, b = render.temp_color(95)
    assert r > b


def _blank():
    img = Image.new("RGB", (render.WIDTH, render.HEIGHT), render.PAPER)
    return img, ImageDraw.Draw(img)


def test_draw_header_marks_top_band():
    img, d = _blank()
    render.draw_header(d, "Blacksburg, VA", "Tue Jun 30", "10:02 AM", ("HIGH", "green"))
    assert any(img.getpixel((x, 20)) != render.PAPER for x in range(0, render.WIDTH, 20))


def test_render_error_card_returns_image():
    img = render.render_error("No network: fetch failed")
    assert img.size == (render.WIDTH, render.HEIGHT)
    assert any(img.getpixel((x, render.HEIGHT // 2)) != render.PAPER
               for x in range(0, render.WIDTH, 10))


def test_display_font_loads_and_varies_weight():
    f = render.display_font(30, 600)
    assert hasattr(f, "getbbox")
    assert render.ACCENTS["red"] == render.RED


def test_draw_banner_marks_pixels():
    img = Image.new("RGB", (render.WIDTH, render.HEIGHT), render.WHITE)
    d = ImageDraw.Draw(img)
    cards = [{"cat": "DRESS", "verdict": "Warm", "detail": "63-81°", "accent": "orange"},
             {"cat": "STORMS", "verdict": "T-storms 2p", "detail": "65%", "accent": "red"}]
    render.draw_banner(d, cards)
    assert img.tobytes() != Image.new("RGB", (render.WIDTH, render.HEIGHT), render.WHITE).tobytes()


def test_draw_graph_runs_with_and_without_band():
    hours = [{"hour": (10 + i), "ampm_label": "{}p".format(i or 12), "is_daytime": True,
              "condition": "CLEAR", "icon_uri": "", "temp_f": 70 + i, "feels_f": 70 + i,
              "pop": 20 * (i % 3), "precip_type": "RAIN", "thunder": 0, "uv": 3}
             for i in range(12)]
    bands = [(t - 3, t - 1, t + 1, t + 3) for t in (h["temp_f"] for h in hours)]
    for b in (bands, []):
        img = Image.new("RGB", (render.WIDTH, render.HEIGHT), render.WHITE)
        d = ImageDraw.Draw(img)
        render.draw_graph(img, d, hours, b, [None] * 12, 14, 160, render.WIDTH - 28, 300)
        assert img.tobytes() != Image.new("RGB", (render.WIDTH, render.HEIGHT), render.WHITE).tobytes()


def test_render_display_dimensions_with_and_without_band():
    hours = [{"hour": (10 + i), "ampm_label": "{}p".format(i or 12), "is_daytime": True,
              "condition": "CLEAR", "icon_uri": "", "temp_f": 70 + i, "feels_f": 70 + i,
              "pop": 10, "precip_type": "RAIN", "thunder": 0, "uv": 3} for i in range(12)]
    bands = [(t - 3, t - 1, t + 1, t + 3) for t in (h["temp_f"] for h in hours)]
    cards = [{"cat": "DRESS", "verdict": "Warm", "detail": "70-81°", "accent": "orange"}]
    for b in (bands, []):
        img = render.render_display(hours, b, [None] * 12, cards,
                                    ("HIGH CONFIDENCE", "green"), "Town", "Thu Jul 3", "2p")
        assert img.size == (800, 480)


def test_stamp_stale_marks_top_right_corner():
    img = Image.new("RGB", (render.WIDTH, render.HEIGHT), render.PAPER)
    out = render.stamp_stale(img)
    assert out.size == (render.WIDTH, render.HEIGHT)
    # The pill sits below the header rule in the top-right corner and is solid
    # RED (no anti-aliasing).
    assert any(out.getpixel((x, y)) == render.RED
               for x in range(render.WIDTH - 60, render.WIDTH)
               for y in range(render.HEADER_RULE_Y, render.HEADER_RULE_Y + 30))


def test_stamp_stale_leaves_center_untouched():
    img = Image.new("RGB", (render.WIDTH, render.HEIGHT), render.PAPER)
    render.stamp_stale(img)
    assert img.getpixel((render.WIDTH // 2, render.HEIGHT // 2)) == render.PAPER


def test_stamp_stale_does_not_cover_confidence_badge():
    # A real render carries a confidence badge in the header top-right (y~15).
    # Use a non-red accent so any exact-RED pixel in that band can only be the pill.
    hours = [{"hour": (10 + i), "ampm_label": "{}p".format(i or 12), "is_daytime": True,
              "condition": "CLEAR", "icon_uri": "", "temp_f": 70 + i, "feels_f": 70 + i,
              "pop": 10, "precip_type": "RAIN", "thunder": 0, "uv": 3} for i in range(12)]
    cards = [{"cat": "DRESS", "verdict": "Warm", "detail": "70-81°", "accent": "orange"}]
    img = render.render_display(hours, [], [None] * 12, cards,
                               ("HIGH CONFIDENCE", "green"), "Town", "Thu Jul 3", "2p")
    render.stamp_stale(img)
    badge_band = [img.getpixel((x, y))
                  for x in range(render.WIDTH - 60, render.WIDTH) for y in range(8, 22)]
    assert render.RED not in badge_band            # pill no longer overprints the badge row
    below_rule = [img.getpixel((x, y))
                  for x in range(render.WIDTH - 60, render.WIDTH)
                  for y in range(render.HEADER_RULE_Y, render.HEADER_RULE_Y + 30)]
    assert render.RED in below_rule                # pill is present just below the header rule


def test_updated_label_without_version():
    assert render._updated_label("9:08pm") == "9:08pm · NEXT 12H"


def test_updated_label_with_version():
    assert render._updated_label("9:08pm", "v1.0.0") == "9:08pm · NEXT 12H · v1.0.0"


def test_draw_header_renders_version_when_given():
    img1, d1 = _blank()
    render.draw_header(d1, "Town", "Tue Jun 30", "10:02 AM", None)
    img2, d2 = _blank()
    render.draw_header(d2, "Town", "Tue Jun 30", "10:02 AM", None, "v9.9.9")
    assert img1.tobytes() != img2.tobytes()   # version text is actually drawn


@pytest.mark.parametrize("pop,tier", [
    (0, 0), (4, 0),        # dry
    (5, 1), (24, 1),       # slight
    (25, 2), (54, 2),      # chance
    (55, 3), (79, 3),      # likely
    (80, 4), (100, 4),     # definite
])
def test_precip_tier_boundaries(pop, tier):
    assert render._precip_tier(pop) == tier


def _meter_img():
    img = Image.new("RGB", (100, 120), render.WHITE)
    return img, ImageDraw.Draw(img)


def _count(img, color):
    return sum(1 for x in range(100) for y in range(120)
               if img.getpixel((x, y)) == color)


def test_precip_meter_fill_increases_with_tier():
    counts = []
    for t in (1, 2, 3, 4):
        img, d = _meter_img()
        render._draw_precip_meter(d, 50, 110, 80, 12, t, render.BLUE)
        counts.append(_count(img, render.BLUE))
    assert counts[0] < counts[1] < counts[2] < counts[3]


def test_precip_meter_uses_kind_color():
    img, d = _meter_img()
    render._draw_precip_meter(d, 50, 110, 80, 12, 1, render.RED)
    assert _count(img, render.RED) > 0
    assert _count(img, render.BLUE) == 0


def test_precip_meter_is_grounded_at_baseline():
    # Baseline is pbase=110; nothing should be filled below it.
    img, d = _meter_img()
    render._draw_precip_meter(d, 50, 110, 80, 12, 4, render.BLUE)
    assert all(img.getpixel((x, y)) == render.WHITE
               for x in range(100) for y in range(112, 120))


def _graph_hours(pop, thunder=0, ptype="RAIN"):
    return [{"hour": (10 + i), "ampm_label": "{}p".format(i or 12),
             "is_daytime": True, "condition": "CLEAR", "icon_uri": "",
             "temp_f": 70 + i, "feels_f": 70 + i, "pop": pop,
             "precip_type": ptype, "thunder": thunder, "uv": 3}
            for i in range(12)]


def _band_color_count(hours, colors):
    """Count precip-colored pixels in the grounded meter band of a full graph."""
    img = Image.new("RGB", (render.WIDTH, render.HEIGHT), render.WHITE)
    d = ImageDraw.Draw(img)
    render.draw_graph(img, d, hours, [], [None] * 12, 14, 160,
                      render.WIDTH - 28, 300)
    axis_y = 160 + 300 - 16          # matches draw_graph's axis_y for these args
    return sum(1 for x in range(14, render.WIDTH - 14)
               for y in range(axis_y - 70, axis_y - 2)
               if img.getpixel((x, y)) in colors)


def test_precip_meter_absent_when_dry():
    # All hours below the dry threshold -> no meter pixels, whatever the color.
    count = _band_color_count(_graph_hours(0),
                              (render.BLUE, render.RED, render.GREEN))
    assert count == 0


def test_precip_meter_present_when_wet():
    count = _band_color_count(_graph_hours(90), (render.BLUE,))
    assert count > 0


def test_precip_meter_uses_storm_color():
    # thunder >= 30 classifies as storm -> RED fill.
    hours = _graph_hours(90, thunder=50)
    assert _band_color_count(hours, (render.RED,)) > 0
    assert _band_color_count(hours, (render.BLUE,)) == 0


def test_precip_meter_draws_faint_headroom():
    # tier 1 fills 1 segment; the 3 empty segments above are faint INK-dot outlines
    img = Image.new("RGB", (100, 120), render.WHITE)
    d = ImageDraw.Draw(img)
    render._draw_precip_meter(d, 50, 110, 80, 12, 1, render.BLUE)
    # INK pixels exist above the single filled bottom segment (the headroom outlines)
    assert any(img.getpixel((x, y)) == render.INK
               for x in range(100) for y in range(30, 80))


def test_precip_buckets_are_quantized():
    # 30% and 50% are both 'chance' -> identical meter; 90% ('definite') differs.
    blue = (render.BLUE,)
    assert _band_color_count(_graph_hours(30), blue) == \
           _band_color_count(_graph_hours(50), blue)
    assert _band_color_count(_graph_hours(30), blue) != \
           _band_color_count(_graph_hours(90), blue)
