import datetime as dt

from kitchen_display.providers import base, forecast


def test_safe_swallows_exceptions_and_returns_the_default():
    def boom():
        raise RuntimeError("no network")

    assert base.safe(boom) is None
    assert base.safe(boom, default=[]) == []


def test_safe_passes_through_a_good_value():
    assert base.safe(lambda: 42) == 42


def test_null_providers_return_none():
    assert base.NullEvents().fetch(dt.date(2026, 9, 23), 7) is None
    assert base.NullMeals().fetch(dt.date(2026, 9, 23), 7) is None


def test_fixture_forecast_provider_returns_hours_and_days():
    f = forecast.FixtureForecastProvider().fetch()
    assert f is not None
    assert len(f.hours) >= 6
    assert len(f.days) >= 7
    assert "temp_f" in f.hours[0]


def test_now_from_forecast_uses_the_first_hour():
    f = forecast.FixtureForecastProvider().fetch()
    now = forecast.now_from(f)
    assert now.temp_f == f.hours[0]["temp_f"]
    assert now.feels_f == f.hours[0]["feels_f"]


def test_now_from_none_forecast_is_none():
    assert forecast.now_from(None) is None
