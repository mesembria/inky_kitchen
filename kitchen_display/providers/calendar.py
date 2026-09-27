"""The family calendar, as the views want it.

It is an ICS feed; ics.py does the reading. This adapter only reshapes
occurrences, and hand the views naive local datetimes — the rest of the app
compares against datetime.now(), and one aware datetime would break every
sort and comparison in the agenda.
"""
import datetime as dt

from . import ics
from .base import Event


def _naive(value):
    return value.replace(tzinfo=None) if value is not None else None


class IcsEvents:
    def __init__(self, feed, tz):
        self.feed = feed
        self.tz = tz

    def fetch(self, start_date, days):
        text = self.feed.text()
        if text is None:
            return None
        end = start_date + dt.timedelta(days=days)
        return [Event(start=_naive(o.start), end=_naive(o.end), title=o.title,
                      all_day=o.all_day,
                      date=o.start_date if o.all_day else None,
                      end_date=o.end_date)
                for o in ics.occurrences(text, start_date, end, self.tz)]
