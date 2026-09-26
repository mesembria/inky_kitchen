import datetime as dt
from zoneinfo import ZoneInfo

import pytest

from kitchen_display.providers import ics

TZ = ZoneInfo("America/Denver")


def _cal(*events):
    return ("BEGIN:VCALENDAR\nVERSION:2.0\nPRODID:-//test//test//EN\n"
            + "".join(events) + "END:VCALENDAR\n")


def _vevent(uid, *lines):
    return "BEGIN:VEVENT\nUID:" + uid + "\n" + "\n".join(lines) + "\nEND:VEVENT\n"


WEEKLY = _vevent("piano",
                 "DTSTART;TZID=America/Denver:20261005T170000",
                 "DTEND;TZID=America/Denver:20261005T180000",
                 "RRULE:FREQ=WEEKLY;COUNT=8",
                 "EXDATE;TZID=America/Denver:20261012T170000",
                 "SUMMARY:Piano")
MOVED = _vevent("piano",
                "RECURRENCE-ID;TZID=America/Denver:20261019T170000",
                "DTSTART;TZID=America/Denver:20261020T160000",
                "DTEND;TZID=America/Denver:20261020T170000",
                "SUMMARY:Piano (moved)")


def _occ(text, start=dt.date(2026, 10, 1), end=dt.date(2026, 11, 30)):
    return ics.occurrences(text, start, end, TZ)


def test_weekly_event_expands_skips_the_exdate_and_takes_the_moved_instance():
    occ = _occ(_cal(WEEKLY, MOVED))
    days = [(o.start_date, o.title) for o in occ]
    assert (dt.date(2026, 10, 5), "Piano") in days
    assert all(d != dt.date(2026, 10, 12) for d, _ in days)          # EXDATE
    assert (dt.date(2026, 10, 19), "Piano") not in days              # moved away
    assert (dt.date(2026, 10, 20), "Piano (moved)") in days


def test_recurring_event_keeps_its_wall_time_across_the_dst_change():
    # Review Focus 1: fall-back is 2026-11-01. A 5:00p lesson stays 5:00p.
    occ = [o for o in _occ(_cal(WEEKLY)) if o.start_date >= dt.date(2026, 11, 2)]
    assert occ and all((o.start.hour, o.start.minute) == (17, 0) for o in occ)


def test_cancelled_events_are_dropped():
    text = _cal(_vevent("c", "DTSTART:20261007T160000Z", "DTEND:20261007T170000Z",
                        "STATUS:CANCELLED", "SUMMARY:Gone"))
    assert _occ(text) == []


def test_utc_times_are_converted_to_the_display_zone():
    text = _cal(_vevent("u", "DTSTART:20261007T160000Z", "DTEND:20261007T170000Z",
                        "SUMMARY:Call"))
    (o,) = _occ(text)
    assert o.start.tzinfo is not None
    assert (o.start.hour, o.start_date) == (10, dt.date(2026, 10, 7))   # MDT = UTC-6


def test_floating_time_is_read_as_display_zone_wall_time():
    # Review Focus 5
    text = _cal(_vevent("f", "DTSTART:20261006T090000", "SUMMARY:Floating"))
    (o,) = _occ(text)
    assert (o.start.hour, o.start_date, o.end_date) == (9, dt.date(2026, 10, 6),
                                                         dt.date(2026, 10, 6))


def test_zero_length_event_stays_on_its_own_day():
    # Review Focus 5: end == start must not compute a last day before the first.
    text = _cal(_vevent("z", "DTSTART;TZID=America/Denver:20261006T000000",
                        "DTEND;TZID=America/Denver:20261006T000000", "SUMMARY:Blip"))
    (o,) = _occ(text)
    assert (o.start_date, o.end_date) == (dt.date(2026, 10, 6), dt.date(2026, 10, 6))


def test_all_day_end_is_exclusive():
    text = _cal(_vevent("a", "DTSTART;VALUE=DATE:20261009", "DTEND;VALUE=DATE:20261012",
                        "SUMMARY:Fall break"))
    (o,) = _occ(text)
    assert o.all_day and o.start is None
    assert (o.start_date, o.end_date) == (dt.date(2026, 10, 9), dt.date(2026, 10, 11))


def test_timed_event_ending_at_midnight_stays_on_one_day():
    text = _cal(_vevent("m", "DTSTART;TZID=America/Denver:20261006T190000",
                        "DTEND;TZID=America/Denver:20261007T000000", "SUMMARY:Late show"))
    (o,) = _occ(text)
    assert o.end_date == dt.date(2026, 10, 6)


def test_timed_multi_day_event_covers_every_day():
    text = _cal(_vevent("t", "DTSTART;TZID=America/Denver:20261009T170000",
                        "DTEND;TZID=America/Denver:20261011T150000", "SUMMARY:Camping"))
    (o,) = _occ(text)
    assert (o.start_date, o.end_date) == (dt.date(2026, 10, 9), dt.date(2026, 10, 11))


def test_event_that_started_before_the_window_is_included():
    text = _cal(_vevent("b", "DTSTART;VALUE=DATE:20260928", "DTEND;VALUE=DATE:20261004",
                        "SUMMARY:Trip"))
    (o,) = _occ(text)
    assert o.start_date == dt.date(2026, 9, 28)


def test_events_outside_the_window_are_excluded():
    text = _cal(_vevent("o", "DTSTART;VALUE=DATE:20261201", "DTEND;VALUE=DATE:20261202",
                        "SUMMARY:Later"))
    assert _occ(text) == []


def test_title_is_stripped_and_a_missing_summary_is_empty():
    text = _cal(_vevent("s", "DTSTART;VALUE=DATE:20261009", "SUMMARY:Maque Choux "),
                _vevent("n", "DTSTART;VALUE=DATE:20261010"))
    assert [o.title for o in _occ(text)] == ["Maque Choux", ""]


def test_one_broken_event_does_not_cost_the_rest():
    text = _cal(_vevent("nostart", "SUMMARY:No start"),
                _vevent("badrule", "DTSTART:20261007T100000Z", "RRULE:FREQ=BOGUS",
                        "SUMMARY:Bad rule"),
                _vevent("ok", "DTSTART:20261006T160000Z", "DTEND:20261006T170000Z",
                        "SUMMARY:Good"))
    assert [o.title for o in _occ(text)] == ["Good"]


def test_all_day_sorts_before_timed_and_ties_keep_feed_order():
    text = _cal(_vevent("t", "DTSTART;TZID=America/Denver:20261006T090000",
                        "DTEND;TZID=America/Denver:20261006T100000", "SUMMARY:Timed"),
                _vevent("a1", "DTSTART;VALUE=DATE:20261006", "SUMMARY:Gnocchi"),
                _vevent("a2", "DTSTART;VALUE=DATE:20261006", "SUMMARY:Chicken"))
    assert [o.title for o in _occ(text)] == ["Gnocchi", "Chicken", "Timed"]


@pytest.mark.parametrize("bad", ["<html><body>Sign in</body></html>",
                                 "BEGIN:VCALENDAR\nVERSION:2.0\nBEGIN:VEVENT\nUID:x\n",
                                 "BEGIN:VEVENT\nUID:x\nEND:VEVENT\n",
                                 ""])
def test_text_that_is_not_a_calendar_raises_value_error(bad):
    with pytest.raises(ValueError):
        ics.parse(bad)
