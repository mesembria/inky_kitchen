import datetime as dt

from kitchen_display.providers import forecast as fc
from kitchen_display.providers.base import Context, Forecast
from kitchen_display.views import glance

NOW = dt.datetime(2026, 9, 21, 20, 0)


def _ctx(**kw):
    f = fc.FixtureForecastProvider().fetch()
    base = dict(now=NOW, version="abc1234", location_name="Lafayette, CO",
                forecast=f, events=None)
    base.update(kw)
    return Context(**base)


def test_hourly_slice_defaults_to_twelve_hours():
    f = fc.FixtureForecastProvider().fetch()
    assert len(glance.hourly_slice(f)) == 12


def test_hourly_slice_survives_a_short_forecast():
    # Review Focus 3: a late-night or truncated response can carry fewer hours
    # than the rail wants. Render what exists instead of raising IndexError.
    f = fc.FixtureForecastProvider().fetch()
    short = Forecast(hours=f.hours[:2], days=f.days, sun=f.sun)
    assert len(glance.hourly_slice(short, count=6)) == 2


def test_hourly_slice_of_none_is_empty():
    assert glance.hourly_slice(None) == []


def test_bar_ends_give_the_warmest_hour_the_full_bar():
    ends = glance.bar_ends([60, 70, 80], 100, 200)
    assert ends == [120, 160, 200]


def test_bar_ends_keep_a_stub_for_the_coldest_hour():
    assert min(glance.bar_ends([20, 90], 100, 200)) > 100


def test_bar_ends_keep_a_flat_day_level():
    # A 2° wobble must not swing the bars across the whole range.
    ends = glance.bar_ends([70, 72], 100, 200)
    assert ends[1] - ends[0] <= 20


def test_bar_ends_of_a_constant_day_do_not_divide_by_zero():
    assert glance.bar_ends([70, 70], 100, 200) == [160, 160]


def test_the_warmest_label_clears_the_precip_meter():
    from inky_weather import render as wr
    width = wr.display_font(18, 600).getlength("100°")
    assert glance.BAR_X1 + 4 + width < glance.METER_X


def test_the_forecast_stays_above_the_bottom_margin():
    from inky_weather import render as wr
    img = glance.render(_ctx())
    for y in range(wr.HEIGHT - 17, wr.HEIGHT):
        for x in range(glance.RAIL_X, glance.RAIL_X + glance.RAIL_W):
            assert img.getpixel((x, y)) == wr.PAPER, (x, y)


def test_render_produces_a_panel_sized_image():
    img = glance.render(_ctx())
    assert img.size == (800, 480)
    assert img.mode == "RGB"


def test_render_without_calendar_says_so_and_still_draws():
    img = glance.render(_ctx(events=None))
    assert img.size == (800, 480)


def test_render_without_any_forecast_still_draws():
    img = glance.render(_ctx(forecast=None))
    assert img.size == (800, 480)


def test_glance_colors_survive_seven_color_quantization():
    PANEL = [(0, 0, 0), (255, 255, 255), (0, 255, 0), (0, 0, 255),
             (255, 0, 0), (255, 255, 0), (255, 140, 0)]

    def nearest(c):
        return min(PANEL, key=lambda p: sum((a - b) ** 2 for a, b in zip(p, c)))

    for color in glance.GLANCE_COLORS:
        assert nearest(color) != (255, 255, 255), color
