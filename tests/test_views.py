import datetime as dt

import pytest

from kitchen_display.providers import forecast as fc
from kitchen_display.providers.base import Context
from kitchen_display.views import registry

NOW = dt.datetime(2026, 9, 21, 20, 0)


def _ctx(**kw):
    f = fc.FixtureForecastProvider().fetch()
    base = dict(now=NOW, version="abc1234", location_name="Lafayette, CO",
                forecast=f, now_wx=fc.now_from(f), events=None, meals=None)
    base.update(kw)
    return Context(**base)


@pytest.mark.parametrize("name", ["glance", "weather", "nextweek"])
def test_every_registered_view_renders_a_panel_sized_image(name):
    img = registry.render(name, _ctx())
    assert img.size == (800, 480)
    assert img.mode == "RGB"


def test_unknown_view_falls_back_to_the_glance():
    img = registry.render("does-not-exist", _ctx())
    assert img.size == (800, 480)


def test_nextweek_starts_on_the_monday_after_this_week():
    from kitchen_display.views import nextweek

    # Mon 2026-09-21 -> next week starts Mon 2026-09-28
    assert nextweek.start_date(dt.date(2026, 9, 21)) == dt.date(2026, 9, 28)
    # Sun 2026-09-27 -> still the same following Monday
    assert nextweek.start_date(dt.date(2026, 9, 27)) == dt.date(2026, 9, 28)
