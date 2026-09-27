"""Forecast data, borrowed wholesale from the weather app."""
import datetime as dt
import os

from inky_weather import weather

from .base import Forecast, safe

FIXTURE_DIR = os.path.join(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))), "inky_weather", "fixtures")


class ForecastProvider:
    """Live Google Weather + Open-Meteo, via the weather app's own fetchers."""

    def __init__(self, cfg):
        self.cfg = cfg

    def fetch(self):
        def _go():
            hours, days = weather.fetch_live(
                self.cfg["lat"], self.cfg["long"], self.cfg["google_weather_key"])
            days = weather.days_from(days, dt.date.today())
            sun = safe(lambda: weather.fetch_sun(
                self.cfg["lat"], self.cfg["long"]), default={}) or {}
            return Forecast(hours=hours, days=days, sun=sun)
        return safe(_go)


class FixtureForecastProvider:
    """Canned data so the glance can be developed with no network."""

    def fetch(self):
        def _go():
            hours, days = weather.load_from_fixtures(FIXTURE_DIR)
            return Forecast(hours=hours, days=days,
                            sun={"sunrise": "6a", "sunset": "7p"})
        return safe(_go)

