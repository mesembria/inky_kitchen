import datetime as dt

from kitchen_display.providers import forecast as fc
from kitchen_display.providers.base import Context, Forecast
from kitchen_display.views import glance

NOW = dt.datetime(2026, 9, 21, 20, 0)


def _ctx(**kw):
    f = fc.FixtureForecastProvider().fetch()
    base = dict(now=NOW, version="abc1234", location_name="Lafayette, CO",
                forecast=f, now_wx=fc.now_from(f), events=None, meals=None)
    base.update(kw)
    return Context(**base)


def test_hourly_slice_returns_six_hours():
    f = fc.FixtureForecastProvider().fetch()
    assert len(glance.hourly_slice(f, count=6)) == 6


def test_hourly_slice_survives_a_short_forecast():
    # Review Focus 3: a late-night or truncated response can carry fewer hours
    # than the rail wants. Render what exists instead of raising IndexError.
    f = fc.FixtureForecastProvider().fetch()
    short = Forecast(hours=f.hours[:2], days=f.days, sun=f.sun)
    assert len(glance.hourly_slice(short, count=6)) == 2


def test_hourly_slice_of_none_is_empty():
    assert glance.hourly_slice(None) == []


def test_dinner_lines_when_tonight_is_planned():
    meals = {dt.date(2026, 9, 21): "Chicken tikka masala",
             dt.date(2026, 9, 22): "Pasta with Garlicky Broccoli"}
    tonight, label, nxt = glance.dinner_lines(meals, dt.date(2026, 9, 21))
    assert tonight == "Chicken tikka masala"
    assert (label, nxt) == ("Tomorrow", "Pasta with Garlicky Broccoli")


def test_dinner_lines_when_tonight_is_empty_shows_the_next_planned_meal():
    meals = {dt.date(2026, 9, 24): "Chicken And Couscous With Chickpeas"}
    tonight, label, nxt = glance.dinner_lines(meals, dt.date(2026, 9, 21))
    assert tonight is None
    assert label == "Thu"
    assert nxt == "Chicken And Couscous With Chickpeas"


def test_dinner_lines_with_no_meal_provider_is_all_none():
    assert glance.dinner_lines(None, dt.date(2026, 9, 21)) == (None, None, None)


def test_render_produces_a_panel_sized_image():
    img = glance.render(_ctx())
    assert img.size == (800, 480)
    assert img.mode == "RGB"


def test_render_without_calendar_says_so_and_still_draws():
    img = glance.render(_ctx(events=None))
    assert img.size == (800, 480)


def test_render_without_any_forecast_still_draws():
    img = glance.render(_ctx(forecast=None, now_wx=None))
    assert img.size == (800, 480)


def test_glance_colors_survive_seven_color_quantization():
    PANEL = [(0, 0, 0), (255, 255, 255), (0, 255, 0), (0, 0, 255),
             (255, 0, 0), (255, 255, 0), (255, 140, 0)]

    def nearest(c):
        return min(PANEL, key=lambda p: sum((a - b) ** 2 for a, b in zip(p, c)))

    for color in glance.GLANCE_COLORS:
        assert nearest(color) != (255, 255, 255), color
