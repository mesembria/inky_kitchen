import datetime as dt
from zoneinfo import ZoneInfo

from kitchen_display.providers import calendar

TZ = ZoneInfo("America/Denver")
START = dt.date(2026, 10, 5)


class _Feed:
    def __init__(self, text):
        self._text = text

    def text(self):
        return self._text


def _cal(*events):
    return ("BEGIN:VCALENDAR\nVERSION:2.0\nPRODID:-//test//test//EN\n"
            + "".join(events) + "END:VCALENDAR\n")


def _vevent(uid, *lines):
    return "BEGIN:VEVENT\nUID:" + uid + "\n" + "\n".join(lines) + "\nEND:VEVENT\n"


def test_events_none_when_the_feed_has_nothing():
    assert calendar.IcsEvents(_Feed(None), TZ).fetch(START, 14) is None


def test_timed_event_becomes_naive_local_with_an_inclusive_end_date():
    text = _cal(_vevent("t", "DTSTART:20261009T230000Z", "DTEND:20261011T210000Z",
                        "SUMMARY:Camping"))
    (ev,) = calendar.IcsEvents(_Feed(text), TZ).fetch(START, 14)
    assert ev.start == dt.datetime(2026, 10, 9, 17, 0)     # naive, Denver wall time
    assert ev.start.tzinfo is None and ev.end.tzinfo is None
    assert (ev.all_day, ev.date, ev.end_date) == (False, None, dt.date(2026, 10, 11))


def test_all_day_event_carries_its_first_and_last_day():
    text = _cal(_vevent("a", "DTSTART;VALUE=DATE:20261009", "DTEND;VALUE=DATE:20261012",
                        "SUMMARY:Fall break"))
    (ev,) = calendar.IcsEvents(_Feed(text), TZ).fetch(START, 14)
    assert (ev.all_day, ev.start, ev.date, ev.end_date) == (
        True, None, dt.date(2026, 10, 9), dt.date(2026, 10, 11))
