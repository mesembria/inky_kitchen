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
    meals = {dt.date(2026, 9, 21): ["Chicken tikka masala"],
             dt.date(2026, 9, 22): ["Pasta with Garlicky Broccoli"]}
    tonight, label, nxt = glance.dinner_lines(meals, dt.date(2026, 9, 21))
    assert tonight == ["Chicken tikka masala"]
    assert (label, nxt) == ("Tomorrow", ["Pasta with Garlicky Broccoli"])


def test_dinner_lines_when_tonight_is_empty_shows_the_next_planned_meal():
    meals = {dt.date(2026, 9, 24): ["Chicken And Couscous With Chickpeas"]}
    tonight, label, nxt = glance.dinner_lines(meals, dt.date(2026, 9, 21))
    assert tonight == []
    assert label == "Thu"
    assert nxt == ["Chicken And Couscous With Chickpeas"]


def test_dinner_lines_keeps_both_meals_of_a_two_meal_night():
    meals = {dt.date(2026, 9, 21): ["Crispy Gnocchi", "Fast Oven Barbecue Chicken"]}
    tonight, _, _ = glance.dinner_lines(meals, dt.date(2026, 9, 21))
    assert tonight == ["Crispy Gnocchi", "Fast Oven Barbecue Chicken"]


def test_dinner_lines_skips_a_future_day_with_an_empty_list():
    meals = {dt.date(2026, 9, 22): [], dt.date(2026, 9, 23): ["Tacos"]}
    _, label, nxt = glance.dinner_lines(meals, dt.date(2026, 9, 21))
    assert (label, nxt) == ("Wed", ["Tacos"])


def test_dinner_lines_with_no_meal_provider_is_empty():
    assert glance.dinner_lines(None, dt.date(2026, 9, 21)) == ([], None, [])


class _Recorder:
    def __init__(self, draw):
        self._d = draw
        self.texts = []

    def text(self, xy, text, **kw):
        self.texts.append(text)
        return self._d.text(xy, text, **kw)

    def __getattr__(self, name):
        return getattr(self._d, name)


def _rail_texts(meals, monkeypatch):
    """Render the glance, recording every string its own Draw object drew."""
    from PIL import ImageDraw
    recs = []
    real = ImageDraw.Draw

    def recording(img):
        rec = _Recorder(real(img))
        recs.append(rec)
        return rec

    monkeypatch.setattr(glance.ImageDraw, "Draw", recording)
    img = glance.render(_ctx(meals=meals))
    return img, recs[0].texts


def test_two_meals_tonight_are_both_drawn(monkeypatch):
    today = NOW.date()
    _, texts = _rail_texts({today: ["Crispy Gnocchi", "Barbecue Chicken"],
                            today + dt.timedelta(days=1): ["Tacos"]}, monkeypatch)
    assert "Crispy Gnocchi" in texts and "Barbecue Chicken" in texts
    assert "Tacos" in texts


def test_three_meals_tonight_show_one_and_a_count(monkeypatch):
    today = NOW.date()
    _, texts = _rail_texts({today: ["A", "B", "C"]}, monkeypatch)
    assert "A" in texts and "+2 more" in texts and "B" not in texts


def test_next_planned_day_with_two_meals_shows_the_first_and_a_count(monkeypatch):
    today = NOW.date()
    _, texts = _rail_texts({today + dt.timedelta(days=1): ["Tacos", "Salad"]},
                           monkeypatch)
    assert "Tacos +1" in texts


def test_a_two_meal_night_stays_above_the_bottom_margin(monkeypatch):
    from inky_weather import render as wr
    today = NOW.date()
    img, _ = _rail_texts({today: ["Crispy Gnocchi With Tomato and Red Onion",
                                  "Fast Oven Barbecue Chicken"],
                          today + dt.timedelta(days=1): ["Skillet Chili Mac", "Salad"]},
                         monkeypatch)
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
    img = glance.render(_ctx(forecast=None, now_wx=None))
    assert img.size == (800, 480)


def test_glance_colors_survive_seven_color_quantization():
    PANEL = [(0, 0, 0), (255, 255, 255), (0, 255, 0), (0, 0, 255),
             (255, 0, 0), (255, 255, 0), (255, 140, 0)]

    def nearest(c):
        return min(PANEL, key=lambda p: sum((a - b) ** 2 for a, b in zip(p, c)))

    for color in glance.GLANCE_COLORS:
        assert nearest(color) != (255, 255, 255), color
