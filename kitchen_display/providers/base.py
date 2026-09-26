"""Data shapes the views consume, and the Null providers Core ships with.

Views never fetch. The manager builds a Context and hands it over, so every
view is a pure function of data to image. A provider that fails returns None
and its block is dropped from the layout.
"""
import datetime as dt
import logging
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Event:
    start: Optional[dt.datetime]     # None for all-day; naive local time
    end: Optional[dt.datetime]
    title: str
    all_day: bool = False
    date: Optional[dt.date] = None   # the day an all-day event starts
    end_date: Optional[dt.date] = None  # last day covered, inclusive; None = one day


@dataclass(frozen=True)
class NowWeather:
    temp_f: int
    feels_f: int
    condition: str
    icon_uri: str = ""


@dataclass(frozen=True)
class Forecast:
    hours: list
    days: list
    sun: dict


@dataclass(frozen=True)
class Context:
    now: dt.datetime
    version: str
    location_name: str
    forecast: Optional[Forecast] = None
    now_wx: Optional[NowWeather] = None
    events: Optional[list] = None
    meals: Optional[dict] = None     # {date: [title, ...]}, feed order


def safe(fn, default=None):
    """Run fn, returning default on any exception. One dead feed never blanks
    the panel — the block it feeds is simply dropped."""
    try:
        return fn()
    except Exception:
        log.exception("provider failed")
        return default


class NullEvents:
    """Stands in until the calendar slice lands."""

    def fetch(self, start_date, days):
        return None


class NullMeals:
    """Stands in until the meal-plan slice lands."""

    def fetch(self, start_date, days):
        return None
