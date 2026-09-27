import datetime as dt
import os
from zoneinfo import ZoneInfo

import pytest
import requests

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


GOOD = _cal(_vevent("g", "DTSTART;VALUE=DATE:20261009", "SUMMARY:Café night"))


class _Resp:
    def __init__(self, body, status=200):
        self.content = body.encode("utf-8")
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))


def _get_returning(body, status=200):
    return lambda url, timeout: _Resp(body, status)


def _get_raising(url, timeout):
    raise requests.ConnectionError("offline")


def _feed(tmp_path, get, clock=lambda: 1_000_000.0, sub="cache"):
    return ics.IcsFeed("https://example.invalid/secret.ics",
                       str(tmp_path / sub / "events.ics"),
                       max_age_s=3600, get=get, clock=clock)


def test_a_good_fetch_returns_the_text_and_caches_it(tmp_path):
    # Review Focus 4: the cache dir does not exist yet on a fresh Pi.
    feed = _feed(tmp_path, _get_returning(GOOD))
    assert feed.text() == GOOD
    assert (tmp_path / "cache" / "events.ics").read_text(encoding="utf-8") == GOOD


def test_bytes_are_decoded_as_utf8_whatever_the_server_says(tmp_path):
    # Review Focus 3: text/calendar without a charset would decode as Latin-1.
    assert "Café" in _feed(tmp_path, _get_returning(GOOD)).text()


def test_a_failed_fetch_falls_back_to_a_fresh_cache(tmp_path):
    _feed(tmp_path, _get_returning(GOOD)).text()
    mtime = os.path.getmtime(tmp_path / "cache" / "events.ics")
    feed = _feed(tmp_path, _get_raising, clock=lambda: mtime + 60)
    assert feed.text() == GOOD


def test_a_failed_fetch_with_a_stale_cache_returns_none(tmp_path):
    _feed(tmp_path, _get_returning(GOOD)).text()
    mtime = os.path.getmtime(tmp_path / "cache" / "events.ics")
    feed = _feed(tmp_path, _get_raising, clock=lambda: mtime + 3601)
    assert feed.text() is None


def test_a_failed_fetch_with_no_cache_returns_none(tmp_path):
    assert _feed(tmp_path, _get_raising).text() is None


def test_an_http_error_falls_back_to_the_cache(tmp_path):
    _feed(tmp_path, _get_returning(GOOD)).text()
    mtime = os.path.getmtime(tmp_path / "cache" / "events.ics")
    assert _feed(tmp_path, _get_returning("gone", 404),
                 clock=lambda: mtime + 60).text() == GOOD


def test_a_200_html_page_never_replaces_the_good_cache(tmp_path):
    # Review Focus 2: a revoked secret URL answers 200 with a sign-in page.
    _feed(tmp_path, _get_returning(GOOD)).text()
    mtime = os.path.getmtime(tmp_path / "cache" / "events.ics")
    feed = _feed(tmp_path, _get_returning("<html>Sign in</html>"),
                 clock=lambda: mtime + 60)
    assert feed.text() == GOOD
    assert (tmp_path / "cache" / "events.ics").read_text(encoding="utf-8") == GOOD


def test_an_unwritable_cache_still_returns_fresh_text(tmp_path):
    # Review Focus 4: a cache path that can't be created (a file sits where
    # the directory should be) must not cost the fresh fetch.
    (tmp_path / "cache").write_text("not a directory")
    assert _feed(tmp_path, _get_returning(GOOD)).text() == GOOD


def test_the_url_never_appears_in_the_log(tmp_path, caplog):
    _feed(tmp_path, _get_raising).text()
    assert "secret.ics" not in caplog.text


FIXTURES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "kitchen_display", "fixtures")


def test_shift_dates_moves_only_date_properties():
    text = ("DTSTART;TZID=America/Denver:20260102T153000\n"
            "DTEND;VALUE=DATE:20260106\n"
            "EXDATE;TZID=America/Denver:20260109T153000\n"
            "RECURRENCE-ID:20260116T153000Z\n"
            "SUMMARY:Meet 20260101\n")
    out = ics.shift_dates(text, 10).splitlines()
    assert out == ["DTSTART;TZID=America/Denver:20260112T153000",
                   "DTEND;VALUE=DATE:20260116",
                   "EXDATE;TZID=America/Denver:20260119T153000",
                   "RECURRENCE-ID:20260126T153000Z",
                   "SUMMARY:Meet 20260101"]


def test_fixture_events_land_relative_to_today():
    today = dt.date(2026, 9, 26)
    feed = ics.FixtureFeed(os.path.join(FIXTURES, "events.ics"), today=lambda: today)
    occ = ics.occurrences(feed.text(), today, today + dt.timedelta(days=21), TZ)
    by_day = {}
    for o in occ:
        by_day.setdefault((o.start_date - today).days, []).append(o.title)
    assert len(by_day[1]) == 5                                   # overflow day
    assert "Cancelled meeting" not in sum(by_day.values(), [])
    assert "Piano lesson" not in by_day.get(8, [])               # EXDATE
    assert "Piano lesson (moved)" in by_day[16]                  # moved instance
    late = next(o for o in occ if o.title == "Late show")
    assert late.end_date == late.start_date                      # midnight end


def test_utc_recurring_event_keeps_wall_time_via_x_wr_timezone():
    # Google feeds anchor some series in UTC and name the zone in the
    # calendar-level X-WR-TIMEZONE. Splitting the feed per series must keep
    # that header, or a 5:00p lesson reads 4:00p after the DST change.
    text = ("BEGIN:VCALENDAR\nVERSION:2.0\nPRODID:-//test//test//EN\n"
            "X-WR-TIMEZONE:America/Denver\n"
            + _vevent("w", "DTSTART:20261001T230000Z", "DTEND:20261002T000000Z",
                      "RRULE:FREQ=WEEKLY;COUNT=6", "SUMMARY:Lesson")
            + "END:VCALENDAR\n")
    occ = _occ(text)
    assert [o.start.hour for o in occ] == [17] * 6


def test_utc_evening_event_on_the_last_day_of_the_window_is_kept():
    # The window's end is a local date. Nov 8 7:00p MST is 02:00Z on Nov 9;
    # read as a UTC boundary, the window would stop at 5:00p Denver time.
    text = _cal(_vevent("e", "DTSTART:20261109T020000Z", "DTEND:20261109T030000Z",
                        "SUMMARY:Sunday dinner"))
    occ = ics.occurrences(text, dt.date(2026, 10, 26), dt.date(2026, 11, 9), TZ)
    assert [(o.title, o.start_date) for o in occ] == [("Sunday dinner",
                                                       dt.date(2026, 11, 8))]


def test_utc_morning_event_just_after_the_window_is_excluded():
    # Mirror of the above: 01:00Z Oct 26 is still Oct 25 in Denver.
    text = _cal(_vevent("e", "DTSTART:20261026T010000Z", "DTEND:20261026T020000Z",
                        "SUMMARY:Saturday late"))
    assert ics.occurrences(text, dt.date(2026, 10, 26), dt.date(2026, 11, 9), TZ) == []


def test_a_byte_order_mark_does_not_make_the_feed_unreadable(tmp_path):
    feed = ics.IcsFeed("https://example.invalid/secret.ics",
                       str(tmp_path / "events.ics"), get=_get_returning("﻿" + GOOD))
    assert feed.text() == GOOD


def test_all_day_events_at_the_window_edges():
    text = _cal(_vevent("b", "DTSTART;VALUE=DATE:20261025", "SUMMARY:Day before"),
                _vevent("f", "DTSTART;VALUE=DATE:20261026", "SUMMARY:First day"),
                _vevent("l", "DTSTART;VALUE=DATE:20261108", "SUMMARY:Last day"),
                _vevent("a", "DTSTART;VALUE=DATE:20261109", "SUMMARY:Day after"))
    occ = ics.occurrences(text, dt.date(2026, 10, 26), dt.date(2026, 11, 9), TZ)
    assert [o.title for o in occ] == ["First day", "Last day"]
