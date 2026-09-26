# Calendar and Meals Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `NullEvents`/`NullMeals` with providers that read the family calendar and the AnyList meal plan from secret ICS URLs, and fix the three deferred Core minors (multi-day events, "+N more", midnight "Today").

**Architecture:** One ICS client (`providers/ics.py`: fetch with a last-good disk cache, expand recurrence into `Occurrence`s) used by two thin adapters (`providers/calendar.py`: `IcsEvents` → `list[Event]`, `IcsMeals` → `dict[date, list[str]]`). Rendering changes stay inside `views/agenda.py`, `views/glance.py`, and one line of `gate.py`. The manager loop is untouched.

**Tech Stack:** Python 3.11+ (Pi, Bookworm) / 3.14 (Mac dev venv), Pillow, requests, `icalendar>=6.1,<8`, `recurring-ical-events>=3.3,<4`, pytest.

**Spec:** `docs/superpowers/specs/2026-09-26-calendar-meals-design.md` (read it before starting; it carries the why).

## Global Constraints

- Run tests with `.venv/bin/python -m pytest` from the repo root. The suite is 246 passing at the start; it must stay green after every task.
- The ICS URLs are secrets. Never log a URL, never commit one, never put one in a test.
- The rest of the app runs on **naive local time** (`Context.now` is `dt.datetime.now()`). Any datetime handed to a view must be naive local; `Occurrence` is tz-aware internally and is converted in the adapter.
- Display time zone comes from config key `timezone`, default `"America/Denver"`.
- No new colours. Everything new draws in `wr.INK`. The quantization guard does not change.
- Colour e-ink review rule: judge visuals from `tools/panel_sim.py` output, never the raw PNG.
- Cache writes use tmp file + `os.replace`, best-effort, the pattern in `inky_weather/cache.py`.
- Commit after each task. End every commit message with:
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`

## Library facts (verified against icalendar 7.3.0 / recurring-ical-events 3.8.2)

- `recurring_ical_events.of(cal).between(date, date)` expands RRULE, honours EXDATE and RECURRENCE-ID overrides, keeps local wall time across DST, and fills in `DTEND` when an event has none (or has `DURATION`).
- It does **not** drop `STATUS:CANCELLED` — we must.
- One malformed VEVENT (no DTSTART, garbage DTSTART, bad RRULE) makes `.between()` raise for the **whole** calendar. So `occurrences` expands each UID's series in its own mini-calendar, inside its own `try`.
- `icalendar.Calendar.from_ical` raises `ValueError` on HTML and on truncated text. Given a bare `BEGIN:VEVENT` it returns a component whose `.name` is `"VEVENT"`, so check `.name == "VCALENDAR"`.
- A floating `DTSTART` (no TZID, no `Z`) comes back naive; treat it as display-zone wall time.

## Review Focus

1. **Recurring event across the DST change** (Nov 1 2026): a weekly 5:00p event must still read 5:00p after fall-back. Pinned in Task 1.
2. **Revoked or wrong URL returning a 200 HTML page** (Google sign-in page): must not replace the good cache; the cached copy is used. Pinned in Task 2.
3. **Non-ASCII titles** ("Café", "Crème brûlée") when the server sends `text/calendar` with no charset: `requests` would decode as Latin-1 and garble them. Decode bytes as UTF-8. Pinned in Task 2.
4. **First run on a fresh Pi** where `kitchen_display/cache/` doesn't exist yet, and a cache that can't be written: fresh text is still returned. Pinned in Task 2.
5. **Floating-time and zero-length events** (`DTSTART` with no zone; `DTEND == DTSTART`): placed on the right day, never on the day before. Pinned in Task 1.

---

## File Structure

| File | Responsibility |
|---|---|
| Create `kitchen_display/providers/ics.py` | `Occurrence`, `parse`, `occurrences`, `IcsFeed`, `FixtureFeed`, `shift_dates` — everything that knows ICS exists |
| Create `kitchen_display/providers/calendar.py` | `IcsEvents`, `IcsMeals` — map occurrences to the shapes views consume |
| Create `kitchen_display/fixtures/events.ics`, `meals.ics` | Hand-written feeds anchored at 2026-01-01, shifted to today at load |
| Modify `kitchen_display/providers/base.py` | `Event.end_date`; `Context.meals` doc |
| Modify `kitchen_display/views/agenda.py` | multi-day placement, `visible()`, "+N more" |
| Modify `kitchen_display/views/glance.py` | `dinner_lines` list shape, two-meal rail |
| Modify `kitchen_display/gate.py` | date change counts as due |
| Modify `kitchen_display/__main__.py` | wire real providers and fixture feeds |
| Modify `kitchen_display/config_example.py`, `.gitignore`, `requirements.txt`, `README.md` | config keys, cache dir, deps, setup docs |
| Create `tests/test_ics.py`, `tests/test_calendar.py` | new unit tests |
| Modify `tests/test_agenda.py`, `tests/test_glance.py`, `tests/test_gate.py`, `tests/test_main.py` | extended tests |

---

### Task 1: Expand ICS text into occurrences

**Files:**
- Modify: `requirements.txt`
- Create: `kitchen_display/providers/ics.py`
- Test: `tests/test_ics.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `ics.Occurrence(title: str, all_day: bool, start: datetime|None, end: datetime|None, start_date: date, end_date: date)` — frozen dataclass; `start`/`end` tz-aware in the display zone, `None` when `all_day`; `end_date` inclusive.
  - `ics.parse(text: str) -> icalendar.Calendar` — raises `ValueError` if the text is not a VCALENDAR.
  - `ics.occurrences(text: str, start: date, end: date, tz: tzinfo) -> list[Occurrence]` — instances overlapping `[start, end)`, cancelled dropped, sorted by `(start_date, timed-after-all-day, start)`, feed order preserved on ties. Raises `ValueError` if `text` is not a calendar.

- [ ] **Step 1: Add the dependencies and install them**

Append to `requirements.txt`:

```
icalendar>=6.1,<8
recurring-ical-events>=3.3,<4
```

Run: `.venv/bin/pip install -q -r requirements-dev.txt`
Expected: exits 0.

- [ ] **Step 2: Write the failing tests**

Create `tests/test_ics.py`:

```python
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
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_ics.py -q`
Expected: collection error, `ImportError: cannot import name 'ics'`.

- [ ] **Step 4: Implement `occurrences`**

Create `kitchen_display/providers/ics.py`:

```python
"""ICS feeds: expand a calendar into the concrete occurrences in a window.

Both the family calendar and the AnyList meal plan arrive as secret ICS URLs,
so this module is the only place that knows ICS exists. Recurrence (RRULE,
EXDATE, moved instances, DST) is recurring-ical-events' job; ours is keeping
one bad entry from costing the whole feed, and turning ICS's exclusive ends
into the inclusive last day the agenda draws.
"""
import datetime as dt
import logging
from dataclasses import dataclass
from typing import Optional

import icalendar
import recurring_ical_events

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Occurrence:
    title: str
    all_day: bool
    start: Optional[dt.datetime]   # tz-aware in the display zone; None when all_day
    end: Optional[dt.datetime]
    start_date: dt.date            # first day covered
    end_date: dt.date              # last day covered, inclusive


def parse(text):
    """Parse ICS text. Raises ValueError for anything that isn't a VCALENDAR —
    an HTML sign-in page, a truncated download, an empty body."""
    try:
        cal = icalendar.Calendar.from_ical(text)
    except Exception as e:     # icalendar's own error types vary by version
        raise ValueError(f"unparseable ICS: {type(e).__name__}") from e
    if getattr(cal, "name", None) != "VCALENDAR":
        raise ValueError("not a VCALENDAR")
    return cal


def _series(cal):
    """One mini-calendar per UID, carrying the feed's time zones.

    recurring-ical-events raises for the whole calendar when a single VEVENT
    is malformed, so each series is expanded on its own. Overrides share their
    master's UID, which keeps moved instances with the rule they replace.
    """
    zones = [c for c in cal.subcomponents if c.name == "VTIMEZONE"]
    groups = {}
    for comp in cal.subcomponents:
        if comp.name == "VEVENT":
            groups.setdefault(str(comp.get("UID", id(comp))), []).append(comp)
    for uid, comps in groups.items():
        mini = icalendar.Calendar()
        for c in zones + comps:
            mini.add_component(c)
        yield uid, mini


def _local(value, tz):
    if value.tzinfo is None:       # floating time: the calendar's wall clock
        return value.replace(tzinfo=tz)
    return value.astimezone(tz)


def _occurrence(comp, tz):
    start = comp.get("DTSTART").dt
    end_prop = comp.get("DTEND")
    end = end_prop.dt if end_prop is not None else start
    title = str(comp.get("SUMMARY", "")).strip()
    if not isinstance(start, dt.datetime):             # a DATE: all-day
        end_day = end.date() if isinstance(end, dt.datetime) else end
        return Occurrence(title, True, None, None, start,
                          max(start, end_day - dt.timedelta(days=1)))
    start = _local(start, tz)
    end = _local(end, tz) if isinstance(end, dt.datetime) else start
    # Ends are exclusive: an event ending at 00:00 belongs to the day before.
    last = (end - dt.timedelta(microseconds=1)).date() if end > start else start.date()
    return Occurrence(title, False, start, end, start.date(), last)


def occurrences(text, start, end, tz):
    """Every instance overlapping [start, end), cancelled ones dropped.

    Sorted by first day, all-day before timed, then start time; ties keep feed
    order. Raises ValueError if text is not a calendar. A broken series is
    logged and skipped.
    """
    out = []
    for uid, mini in _series(parse(text)):
        try:
            for comp in recurring_ical_events.of(mini).between(start, end):
                if str(comp.get("STATUS", "")).upper() == "CANCELLED":
                    continue
                out.append(_occurrence(comp, tz))
        except Exception:
            log.warning("skipping unreadable event series %s", uid, exc_info=True)
    out.sort(key=lambda o: (o.start_date, not o.all_day,
                            o.start.timestamp() if o.start else 0.0))
    return out
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_ics.py -q`
Expected: all pass. Then `.venv/bin/python -m pytest -q` — full suite green.

- [ ] **Step 6: Commit**

```bash
git add requirements.txt kitchen_display/providers/ics.py tests/test_ics.py
git commit -m "feat: expand ICS feeds into occurrences, one bad series at a time

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Fetch a feed with a last-good cache

**Files:**
- Modify: `kitchen_display/providers/ics.py`
- Test: `tests/test_ics.py`

**Interfaces:**
- Consumes: `ics.parse` (Task 1).
- Produces: `ics.IcsFeed(url: str, cache_path: str, timeout: float = 15, max_age_s: float = 86400, get=requests.get, clock=time.time)` with `.text() -> str | None`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_ics.py`:

```python
import os

import requests

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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_ics.py -q`
Expected: the new tests fail with `AttributeError: module ... has no attribute 'IcsFeed'`.

- [ ] **Step 3: Implement `IcsFeed`**

In `kitchen_display/providers/ics.py`, extend the imports to:

```python
import datetime as dt
import logging
import os
import time
from dataclasses import dataclass
from typing import Optional

import icalendar
import recurring_ical_events
import requests
```

and append:

```python
class IcsFeed:
    """One secret ICS URL, with its last good copy on disk.

    text() returns fresh text when the fetch works, the cached copy when it
    doesn't (if younger than max_age_s), else None. Text is cached only after
    it parses, so a sign-in page or a truncated download never replaces a
    good copy. The URL is a secret: it is never logged.
    """

    def __init__(self, url, cache_path, timeout=15, max_age_s=24 * 3600,
                 get=None, clock=time.time):
        self.url = url
        self.cache_path = cache_path
        self.timeout = timeout
        self.max_age_s = max_age_s
        self._get = get or requests.get
        self._clock = clock

    def text(self):
        try:
            resp = self._get(self.url, timeout=self.timeout)
            resp.raise_for_status()
            # ICS is UTF-8 by spec; servers often omit the charset, and
            # requests would then decode text/* as Latin-1.
            body = resp.content.decode("utf-8", errors="replace")
            parse(body)
        except Exception as e:
            # Only the exception type: requests' messages embed the URL.
            log.warning("ICS fetch for %s failed (%s); trying the cached copy",
                        os.path.basename(self.cache_path), type(e).__name__)
            return self._cached()
        self._save(body)
        return body

    def _save(self, body):
        try:
            os.makedirs(os.path.dirname(self.cache_path), exist_ok=True)
            tmp = self.cache_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(body)
            os.replace(tmp, self.cache_path)
        except OSError:
            log.warning("could not write ICS cache %s", self.cache_path, exc_info=True)

    def _cached(self):
        try:
            age = self._clock() - os.path.getmtime(self.cache_path)
            if age > self.max_age_s:
                log.warning("ICS cache %s is %.0fh old; dropping the block",
                            os.path.basename(self.cache_path), age / 3600)
                return None
            with open(self.cache_path, encoding="utf-8") as f:
                return f.read()
        except OSError:
            return None
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_ics.py -q`
Expected: all pass. Then the full suite.

- [ ] **Step 5: Commit**

```bash
git add kitchen_display/providers/ics.py tests/test_ics.py
git commit -m "feat: ICS feed with a parse-checked last-good cache

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Events and meals adapters

**Files:**
- Modify: `kitchen_display/providers/base.py`
- Create: `kitchen_display/providers/calendar.py`
- Test: `tests/test_calendar.py`

**Interfaces:**
- Consumes: `ics.occurrences(text, start, end, tz)`, `ics.Occurrence` (Task 1); anything with `.text() -> str | None` as a feed (Task 2's `IcsFeed`).
- Produces:
  - `base.Event` gains a last field `end_date: Optional[date] = None` — inclusive last day; `None` means one day.
  - `calendar.IcsEvents(feed, tz).fetch(start_date: date, days: int) -> list[Event] | None` — `start`/`end` **naive local**; all-day events carry `date=start_date`; timed events `date=None`.
  - `calendar.IcsMeals(feed, tz).fetch(start_date: date, days: int) -> dict[date, list[str]] | None` — titles stripped, empties skipped, feed order per day, only days `>= start_date`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_calendar.py`:

```python
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
    assert calendar.IcsMeals(_Feed(None), TZ).fetch(START, 14) is None


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


def test_meals_keep_both_entries_for_a_night_in_feed_order():
    text = _cal(_vevent("m1", "DTSTART;VALUE=DATE:20261006",
                        "SUMMARY:Crispy Gnocchi With Tomato and Red Onion"),
                _vevent("m2", "DTSTART;VALUE=DATE:20261006",
                        "SUMMARY:Fast Oven Barbecue Chicken"),
                _vevent("m3", "DTSTART;VALUE=DATE:20261008", "SUMMARY:Maque Choux ",
                        "LOCATION:p. 117"))
    meals = calendar.IcsMeals(_Feed(text), TZ).fetch(START, 14)
    assert meals == {
        dt.date(2026, 10, 6): ["Crispy Gnocchi With Tomato and Red Onion",
                               "Fast Oven Barbecue Chicken"],
        dt.date(2026, 10, 8): ["Maque Choux"],
    }


def test_meals_skip_empty_titles_and_days_before_the_window():
    text = _cal(_vevent("e", "DTSTART;VALUE=DATE:20261006", "SUMMARY:  "),
                _vevent("p", "DTSTART;VALUE=DATE:20261003", "DTEND;VALUE=DATE:20261007",
                        "SUMMARY:Leftovers week"))
    assert calendar.IcsMeals(_Feed(text), TZ).fetch(START, 14) == {}


def test_window_is_days_long():
    text = _cal(_vevent("x", "DTSTART;VALUE=DATE:20261019", "SUMMARY:Too late"))
    assert calendar.IcsEvents(_Feed(text), TZ).fetch(START, 14) == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_calendar.py -q`
Expected: `ImportError: cannot import name 'calendar'`.

- [ ] **Step 3: Add `end_date` to `Event`**

In `kitchen_display/providers/base.py`, replace the `Event` class with:

```python
@dataclass(frozen=True)
class Event:
    start: Optional[dt.datetime]     # None for all-day; naive local time
    end: Optional[dt.datetime]
    title: str
    all_day: bool = False
    date: Optional[dt.date] = None   # the day an all-day event starts
    end_date: Optional[dt.date] = None  # last day covered, inclusive; None = one day
```

and in `Context`, change the `meals` line to:

```python
    meals: Optional[dict] = None     # {date: [title, ...]}, feed order
```

- [ ] **Step 4: Implement the adapters**

Create `kitchen_display/providers/calendar.py`:

```python
"""The family calendar and the AnyList meal plan, as the views want them.

Both are ICS feeds; ics.py does the reading. These adapters only reshape
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


class IcsMeals:
    """AnyList publishes each planned meal as an all-day event on its night."""

    def __init__(self, feed, tz):
        self.feed = feed
        self.tz = tz

    def fetch(self, start_date, days):
        text = self.feed.text()
        if text is None:
            return None
        end = start_date + dt.timedelta(days=days)
        meals = {}
        for o in ics.occurrences(text, start_date, end, self.tz):
            if o.title and o.start_date >= start_date:
                meals.setdefault(o.start_date, []).append(o.title)
        return meals
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_calendar.py -q`, then `.venv/bin/python -m pytest -q`.
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add kitchen_display/providers/base.py kitchen_display/providers/calendar.py tests/test_calendar.py
git commit -m "feat: events and meals adapters over the ICS client

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Multi-day events in the agenda

**Files:**
- Modify: `kitchen_display/views/agenda.py` (`day_rows`)
- Test: `tests/test_agenda.py`

**Interfaces:**
- Consumes: `Event.end_date` (Task 3).
- Produces: `agenda.day_rows(events, start_date, days, today)` — same signature; an event now lands on every row from its first day through `end_date`; later days of a timed event are `Item(time_label=None, all_day=True)`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_agenda.py`:

```python
FRI = MON + dt.timedelta(days=4)


def _titles(rows):
    return [[i.title for i in r.items] for r in rows]


def test_multi_day_all_day_event_shows_on_every_day_it_covers():
    ev = Event(start=None, end=None, title="Grandparents", all_day=True,
               date=FRI, end_date=FRI + dt.timedelta(days=2))
    rows = agenda.day_rows([ev], MON, 7, today=MON)
    assert _titles(rows) == [[], [], [], [], ["Grandparents"], ["Grandparents"],
                             ["Grandparents"]]


def test_timed_multi_day_event_shows_its_time_once_then_reads_all_day():
    start = dt.datetime.combine(FRI, dt.time(17))
    ev = Event(start=start, end=start + dt.timedelta(hours=46), title="Camping",
               end_date=FRI + dt.timedelta(days=2))
    rows = agenda.day_rows([ev], MON, 7, today=MON)
    fri, sat, sun = rows[4].items[0], rows[5].items[0], rows[6].items[0]
    assert (fri.time_label, fri.all_day) == ("5:00p", False)
    assert (sat.time_label, sat.all_day) == (None, True)
    assert (sun.time_label, sun.all_day) == (None, True)


def test_event_that_began_before_the_window_shows_on_the_days_it_still_covers():
    ev = Event(start=None, end=None, title="Trip", all_day=True,
               date=MON - dt.timedelta(days=3), end_date=MON + dt.timedelta(days=1))
    rows = agenda.day_rows([ev], MON, 7, today=MON)
    assert _titles(rows)[:3] == [["Trip"], ["Trip"], []]


def test_event_running_past_the_window_is_clipped():
    ev = Event(start=None, end=None, title="Break", all_day=True,
               date=MON + dt.timedelta(days=5), end_date=MON + dt.timedelta(days=12))
    rows = agenda.day_rows([ev], MON, 7, today=MON)
    assert len(rows) == 7
    assert _titles(rows)[5:] == [["Break"], ["Break"]]


def test_continuation_day_sorts_ahead_of_that_days_timed_events():
    start = dt.datetime.combine(FRI, dt.time(17))
    trip = Event(start=start, end=start + dt.timedelta(days=1), title="Trip",
                 end_date=FRI + dt.timedelta(days=1))
    rows = agenda.day_rows([_ev(FRI + dt.timedelta(days=1), 9, "Soccer"), trip],
                           MON, 7, today=MON)
    assert _titles(rows)[5] == ["Trip", "Soccer"]


def test_end_date_none_means_one_day():
    rows = agenda.day_rows([_ev(MON, 9, "Soccer")], MON, 7, today=MON)
    assert _titles(rows)[:2] == [["Soccer"], []]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_agenda.py -q`
Expected: the multi-day tests fail (events appear on their first day only).

- [ ] **Step 3: Implement multi-day placement**

In `kitchen_display/views/agenda.py`, replace `day_rows` with:

```python
def day_rows(events, start_date, days, today):
    """Group events into one row per day, starting at start_date.

    An event lands on every day from its first through its end_date. A timed
    event shows its start time on its first day only; on the days it runs on
    into, it reads as all-day, since "5:00p" on Saturday would be a lie.
    """
    rows = []
    for i in range(days):
        date = start_date + dt.timedelta(days=i)
        rows.append(DayRow(
            date=date,
            label=date.strftime("%a").upper(),
            sublabel="Today" if date == today else date.strftime("%b %-d"),
            is_today=date == today,
            is_weekend=date.weekday() >= 5,
        ))
    by_date = {r.date: r for r in rows}
    last_row = start_date + dt.timedelta(days=days - 1)

    placed = []
    for ev in events or []:
        first = ev.date if ev.all_day else (ev.start.date() if ev.start else None)
        if first is None:
            continue
        last = max(first, ev.end_date or first)
        day = max(first, start_date)
        while day <= min(last, last_row):
            all_day = ev.all_day or day != first
            # Sort on the real start time, never the display label: as
            # strings, '4:00p' sorts before '9:00a'. All-day items lead.
            key = (day, not all_day, dt.datetime.min if all_day else ev.start)
            placed.append((key, Item(
                time_label=None if all_day else _time_label(ev.start),
                title=ev.title or "",
                all_day=all_day)))
            day += dt.timedelta(days=1)

    for key, item in sorted(placed, key=lambda p: p[0]):
        by_date[key[0]].items.append(item)
    return rows
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_agenda.py -q`, then the full suite.
Expected: all pass, including the existing ordering tests.

- [ ] **Step 5: Commit**

```bash
git add kitchen_display/views/agenda.py tests/test_agenda.py
git commit -m "feat: multi-day events appear on every day they cover

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: "+N more" when a day overflows

**Files:**
- Modify: `kitchen_display/views/agenda.py` (`visible`, `render`)
- Test: `tests/test_agenda.py`

**Interfaces:**
- Consumes: `agenda.layout`, `agenda.Slot.max_items` (existing).
- Produces: `agenda.visible(items: list, cap: int) -> tuple[list, int]` — `(shown, hidden)`. Task 6 reuses it for meals.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_agenda.py`:

```python
class _Recorder:
    """Pass-through ImageDraw that remembers every string drawn."""

    def __init__(self, draw):
        self._d = draw
        self.texts = []

    def text(self, xy, text, **kw):
        self.texts.append(text)
        return self._d.text(xy, text, **kw)

    def __getattr__(self, name):
        return getattr(self._d, name)


def _render_texts(counts, h):
    img = Image.new("RGB", (800, 480), wrender.PAPER)
    rec = _Recorder(ImageDraw.Draw(img))
    rows = _busy(agenda.day_rows([], MON, 7, today=MON), counts)
    agenda.render(rec, 290, 52, 492, h, rows)
    return rec.texts, agenda.layout(rows, h)


def test_visible_keeps_everything_that_fits():
    assert agenda.visible(["a", "b"], 3) == (["a", "b"], 0)


def test_visible_gives_up_one_line_to_the_marker():
    assert agenda.visible(["a", "b", "c", "d", "e"], 3) == (["a", "b"], 3)


def test_visible_at_one_line_keeps_the_first_item():
    assert agenda.visible(["a", "b", "c"], 1) == (["a"], 2)


def test_an_overflowing_day_says_how_many_it_hid():
    texts, slots = _render_texts([5] * 7, 410)
    assert slots[0].max_items == 2
    assert texts.count("+4 more") == 7
    assert "Event 1" not in texts


def test_at_one_line_per_day_the_count_rides_on_the_same_line():
    texts, slots = _render_texts([5] * 7, 7 * (agenda.DAY_LABEL_H + agenda.ROW_PAD))
    assert slots[0].max_items == 1
    assert texts.count("+4") == 7
    assert "+4 more" not in texts


def test_a_day_that_fits_shows_no_marker():
    texts, _ = _render_texts([2] * 7, 410)
    assert not any(t.startswith("+") for t in texts)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_agenda.py -q`
Expected: `AttributeError: ... has no attribute 'visible'`, and the render tests fail.

- [ ] **Step 3: Implement `visible` and the markers**

In `kitchen_display/views/agenda.py`, add above `render`:

```python
def visible(items, cap):
    """(shown, hidden) for a space `cap` lines tall.

    When something has to be cut, one line is given up to a "+N more" marker
    so nothing vanishes silently. At one line there is no line to give, so
    the first item stays and the count rides beside it.
    """
    if len(items) <= cap:
        return list(items), 0
    keep = max(cap - 1, 1)
    return list(items[:keep]), len(items) - keep
```

Then in `render`, replace the block from `iy = top` to the end of the function with:

```python
        shown, hidden = visible(row.items, slot.max_items)
        inline = f"+{hidden}" if hidden and slot.max_items == 1 else ""
        inline_w = quiet_font.getlength(inline) + 6 if inline else 0

        iy = top
        for item in shown:
            tx = text_x
            if item.all_day:
                tag = "ALL DAY"
                tag_font = wr.display_font(11, 600)
                tw = tag_font.getlength(tag) + 8
                draw.rectangle([tx, iy + 3, tx + tw, iy + 17], outline=wr.INK, width=2)
                draw.text((tx + 4, iy + 3), tag, font=tag_font, fill=wr.INK)
                tx += tw + 6
            elif item.time_label:
                draw.text((tx, iy), item.time_label, font=time_font, fill=wr.INK)
                tx += time_font.getlength(item.time_label) + 6
            avail = text_w - (tx - text_x) - inline_w
            draw.text((tx, iy), fit_text(item.title, item_font, avail),
                      font=item_font, fill=wr.INK)
            iy += LINE_H
        if inline:
            draw.text((x + w, top), inline, font=quiet_font, fill=wr.INK, anchor="ra")
        elif hidden:
            draw.text((text_x, iy), f"+{hidden} more", font=quiet_font, fill=wr.INK)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_agenda.py -q`, then the full suite.
Expected: all pass. `test_render_draws_within_its_box_and_returns_nothing` must still pass: the inline count is right-anchored at `x + w`, inside the box.

- [ ] **Step 5: Commit**

```bash
git add kitchen_display/views/agenda.py tests/test_agenda.py
git commit -m "feat: an overflowing day says +N more instead of dropping events

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Two meals on the glance rail

**Files:**
- Modify: `kitchen_display/views/glance.py` (`dinner_lines`, the dinner block of `_rail`)
- Test: `tests/test_glance.py`

**Interfaces:**
- Consumes: `agenda.visible` (Task 5), `agenda.fit_text` (existing), `Context.meals: dict[date, list[str]]` (Task 3).
- Produces: `glance.dinner_lines(meals, today) -> (tonight: list[str], next_label: str | None, next_meals: list[str])`.

Space check (current rail, all blocks present): the six-hour list ends at y≈332; TONIGHT label 346, first meal 362, second meal 386, next-planned label 410, next-planned title ending ≈447. Bottom margin is `wr.HEIGHT - 18 = 462`, so two meals plus the next line fit. The `NEXT_H` guard below drops the next-planned line if a future rail change ever makes it not fit.

- [ ] **Step 1: Write the failing tests**

In `tests/test_glance.py`, replace the three `test_dinner_lines_*` tests with:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_glance.py -q`
Expected: the dinner and rail tests fail (string vs list shape).

- [ ] **Step 3: Implement**

In `kitchen_display/views/glance.py`, add a constant beside the layout constants:

```python
NEXT_H = 37          # next-planned label + title
```

Replace `dinner_lines` with:

```python
def dinner_lines(meals, today):
    """(tonight, next_label, next_meals); tonight and next_meals are lists.

    Tonight is empty when nothing is planned — the rail says so in light
    weight and shows the next planned meal beneath, rather than hiding the
    block. Some nights carry two meals; both are kept, in feed order.
    """
    if not meals:
        return ([], None, [])
    tonight = list(meals.get(today) or [])
    future = sorted(d for d in meals if d > today and meals[d])
    if not future:
        return (tonight, None, [])
    nxt = future[0]
    label = "Tomorrow" if nxt == today + dt.timedelta(days=1) else nxt.strftime("%a")
    return (tonight, label, list(meals[nxt]))
```

Replace the dinner block at the end of `_rail` (from `tonight, label, nxt = dinner_lines(...)` to the end of the function) with:

```python
    tonight, label, nxt = dinner_lines(ctx.meals, ctx.now.date())
    if ctx.meals is not None:
        wr._dotted_line(draw, RAIL_X, RAIL_X + RAIL_W, y + 4, wr.INK)
        y += 14
        draw.text((RAIL_X, y), "TONIGHT", font=wr.display_font(13, 600), fill=wr.RED)
        y += 16
        meal_font = wr.display_font(19, 600)
        quiet_font = wr.display_font(16, 300)
        if tonight:
            shown, hidden = agenda.visible(tonight, 2)
            for title in shown:
                draw.text((RAIL_X, y), agenda.fit_text(title, meal_font, RAIL_W),
                          font=meal_font, fill=wr.INK)
                y += 24
            if hidden:
                draw.text((RAIL_X, y), f"+{hidden} more", font=quiet_font, fill=wr.INK)
                y += 24
        else:
            draw.text((RAIL_X, y), "Nothing planned", font=quiet_font, fill=wr.INK)
            y += 24
        # A two-meal night costs a line; if the next-planned line would then
        # run into the bottom margin, drop it rather than shrink the type.
        if nxt and y + NEXT_H <= wr.HEIGHT - 18:
            next_font = wr.display_font(17, 600)
            suffix = f" +{len(nxt) - 1}" if len(nxt) > 1 else ""
            first = agenda.fit_text(nxt[0], next_font,
                                    RAIL_W - next_font.getlength(suffix))
            draw.text((RAIL_X, y), label.upper(), font=wr.display_font(13, 600),
                      fill=wr.INK)
            draw.text((RAIL_X, y + 15), first + suffix, font=next_font, fill=wr.INK)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_glance.py -q`, then the full suite.
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add kitchen_display/views/glance.py tests/test_glance.py
git commit -m "feat: the dinner block shows both meals of a two-meal night

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Redraw after midnight

**Files:**
- Modify: `kitchen_display/gate.py`
- Test: `tests/test_gate.py`

**Interfaces:**
- Consumes: `events.State`, `gate.decide(state, now, cfg)` (existing).
- Produces: `decide` treats `now.date() != state.last_refresh_at.date()` as due.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_gate.py`:

```python
MIDNIGHT = dt.datetime(2026, 9, 24, 0, 1, 0)


def test_a_new_day_is_due_so_today_is_never_an_hour_late():
    st = _state(last_refresh_at=dt.datetime(2026, 9, 23, 23, 50))
    d = gate.decide(st, MIDNIGHT, CFG)
    assert isinstance(d, gate.Render)
    assert (d.view, d.reason) == ("glance", "scheduled")


def test_a_new_day_waits_for_an_occupied_room_like_any_scheduled_refresh():
    st = _state(last_refresh_at=dt.datetime(2026, 9, 23, 23, 50), occupied=True)
    assert gate.decide(st, MIDNIGHT, CFG) is gate.HOLD


def test_after_the_midnight_redraw_the_day_is_quiet_again():
    st = _state(last_refresh_at=MIDNIGHT)
    assert gate.decide(st, MIDNIGHT + dt.timedelta(minutes=5), CFG) is gate.NOTHING
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_gate.py -q`
Expected: the first two new tests fail (`NOTHING` returned).

- [ ] **Step 3: Implement**

In `kitchen_display/gate.py`, replace the rule 3/4 comment and `due` line with:

```python
    # 3/4. Due, or held from earlier and the room has now cleared. A new day
    # is due too, or "Today" would sit on yesterday for up to an hour.
    due = (elapsed > cfg["refresh_interval_s"]
           or now.date() != state.last_refresh_at.date()
           or state.pending is not None)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_gate.py tests/test_manager.py -q`, then the full suite.
Expected: all pass. (The manager starts with `last_refresh_at=datetime.min` and a pending startup request, so the new condition changes nothing at startup.)

- [ ] **Step 5: Commit**

```bash
git add kitchen_display/gate.py tests/test_gate.py
git commit -m "feat: a new day counts as a due refresh

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Fixtures, wiring, config, docs

**Files:**
- Modify: `kitchen_display/providers/ics.py` (`shift_dates`, `FixtureFeed`)
- Create: `kitchen_display/fixtures/events.ics`, `kitchen_display/fixtures/meals.ics`
- Modify: `kitchen_display/__main__.py`, `kitchen_display/config_example.py`, `.gitignore`, `README.md`
- Test: `tests/test_ics.py`, `tests/test_main.py`

**Interfaces:**
- Consumes: `ics.IcsFeed`, `ics.occurrences` (Tasks 1–2), `calendar.IcsEvents`, `calendar.IcsMeals` (Task 3).
- Produces:
  - `ics.ANCHOR = date(2026, 1, 1)`; `ics.shift_dates(text: str, days: int) -> str`; `ics.FixtureFeed(path: str, today=dt.date.today)` with `.text() -> str`.
  - `__main__._calendar_providers(cfg) -> {"events": ..., "meals": ...}`; `__main__._fixture_providers(cfg=None)`.
  - Config keys `timezone`, `calendar_ics_url`, `meals_ics_url`, `ics_max_age_s`.

- [ ] **Step 1: Write the fixture files**

Day 0 of each file is `20260101` (= `ics.ANCHOR`), and it lands on "today" at load. Only `DTSTART`, `DTEND`, `EXDATE` and `RECURRENCE-ID` values are shifted, so recurrence uses `COUNT`, never `UNTIL`.

Create `kitchen_display/fixtures/events.ics`:

```
BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//kitchen_display//fixture//EN
BEGIN:VEVENT
UID:fx-dentist
DTSTART;TZID=America/Denver:20260101T093000
DTEND;TZID=America/Denver:20260101T101500
SUMMARY:Dentist
END:VEVENT
BEGIN:VEVENT
UID:fx-soccer
DTSTART;TZID=America/Denver:20260101T160000
DTEND;TZID=America/Denver:20260101T173000
SUMMARY:Soccer practice
END:VEVENT
BEGIN:VEVENT
UID:fx-dropoff
DTSTART;TZID=America/Denver:20260102T080000
DTEND;TZID=America/Denver:20260102T083000
SUMMARY:School drop-off
END:VEVENT
BEGIN:VEVENT
UID:fx-library
DTSTART;TZID=America/Denver:20260102T100000
DTEND;TZID=America/Denver:20260102T110000
SUMMARY:Library story time
END:VEVENT
BEGIN:VEVENT
UID:fx-lunch
DTSTART;TZID=America/Denver:20260102T120000
DTEND;TZID=America/Denver:20260102T130000
SUMMARY:Lunch with Grandma at the new place on Main Street
END:VEVENT
BEGIN:VEVENT
UID:fx-piano
DTSTART;TZID=America/Denver:20260102T153000
DTEND;TZID=America/Denver:20260102T163000
RRULE:FREQ=WEEKLY;COUNT=6
EXDATE;TZID=America/Denver:20260109T153000
SUMMARY:Piano lesson
END:VEVENT
BEGIN:VEVENT
UID:fx-piano
RECURRENCE-ID;TZID=America/Denver:20260116T153000
DTSTART;TZID=America/Denver:20260117T100000
DTEND;TZID=America/Denver:20260117T110000
SUMMARY:Piano lesson (moved)
END:VEVENT
BEGIN:VEVENT
UID:fx-dinner-party
DTSTART;TZID=America/Denver:20260102T183000
DTEND;TZID=America/Denver:20260102T210000
SUMMARY:Dinner party
END:VEVENT
BEGIN:VEVENT
UID:fx-grandparents
DTSTART;VALUE=DATE:20260103
DTEND;VALUE=DATE:20260106
SUMMARY:Grandparents visiting
END:VEVENT
BEGIN:VEVENT
UID:fx-cancelled
DTSTART;TZID=America/Denver:20260104T110000
DTEND;TZID=America/Denver:20260104T120000
STATUS:CANCELLED
SUMMARY:Cancelled meeting
END:VEVENT
BEGIN:VEVENT
UID:fx-utc
DTSTART:20260104T160000Z
DTEND:20260104T170000Z
SUMMARY:Work call
END:VEVENT
BEGIN:VEVENT
UID:fx-camping
DTSTART;TZID=America/Denver:20260105T170000
DTEND;TZID=America/Denver:20260107T150000
SUMMARY:Camping trip
END:VEVENT
BEGIN:VEVENT
UID:fx-late
DTSTART;TZID=America/Denver:20260106T190000
DTEND;TZID=America/Denver:20260107T000000
SUMMARY:Late show
END:VEVENT
BEGIN:VEVENT
UID:fx-conference
DTSTART;TZID=America/Denver:20260110T140000
DTEND;TZID=America/Denver:20260110T143000
SUMMARY:Parent-teacher conference
END:VEVENT
BEGIN:VEVENT
UID:fx-noschool
DTSTART;VALUE=DATE:20260112
DTEND;VALUE=DATE:20260113
SUMMARY:No school
END:VEVENT
END:VCALENDAR
```

Create `kitchen_display/fixtures/meals.ics` (AnyList's shape: all-day, trailing spaces, a location, gaps):

```
BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//kitchen_display//fixture//EN
BEGIN:VEVENT
UID:fx-meal-1
DTSTART;VALUE=DATE:20260101
DTEND;VALUE=DATE:20260102
SUMMARY:Crispy Gnocchi With Tomato and Red Onion
END:VEVENT
BEGIN:VEVENT
UID:fx-meal-2
DTSTART;VALUE=DATE:20260101
DTEND;VALUE=DATE:20260102
SUMMARY:Fast Oven Barbecue Chicken
LOCATION:p.231
END:VEVENT
BEGIN:VEVENT
UID:fx-meal-3
DTSTART;VALUE=DATE:20260102
DTEND;VALUE=DATE:20260103
SUMMARY:Maque Choux 
LOCATION:p. 117
END:VEVENT
BEGIN:VEVENT
UID:fx-meal-4
DTSTART;VALUE=DATE:20260104
DTEND;VALUE=DATE:20260105
SUMMARY:Kung Pao Chicken
END:VEVENT
BEGIN:VEVENT
UID:fx-meal-5
DTSTART;VALUE=DATE:20260106
DTEND;VALUE=DATE:20260107
SUMMARY:Skillet Chili Mac
END:VEVENT
END:VCALENDAR
```

(Keep the trailing space after `Maque Choux` — that is the case under test.)

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_ics.py`:

```python
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


def test_fixture_meals_have_a_two_meal_night_today():
    today = dt.date(2026, 9, 26)
    feed = ics.FixtureFeed(os.path.join(FIXTURES, "meals.ics"), today=lambda: today)
    titles = [o.title for o in ics.occurrences(feed.text(), today,
                                               today + dt.timedelta(days=1), TZ)]
    assert titles == ["Crispy Gnocchi With Tomato and Red Onion",
                      "Fast Oven Barbecue Chicken"]
```

Append to `tests/test_main.py`:

```python
def test_unset_ics_urls_leave_the_null_providers():
    from kitchen_display import settings
    from kitchen_display.__main__ import _calendar_providers
    from kitchen_display.providers import base
    p = _calendar_providers(dict(settings.DEFAULTS))
    assert isinstance(p["events"], base.NullEvents)
    assert isinstance(p["meals"], base.NullMeals)


def test_set_ics_urls_build_cached_ics_providers():
    from kitchen_display import settings
    from kitchen_display.__main__ import _calendar_providers, ICS_CACHE_DIR
    from kitchen_display.providers import calendar
    cfg = dict(settings.DEFAULTS, calendar_ics_url="https://example.invalid/a.ics",
               meals_ics_url="https://example.invalid/b.ics")
    p = _calendar_providers(cfg)
    assert isinstance(p["events"], calendar.IcsEvents)
    assert isinstance(p["meals"], calendar.IcsMeals)
    assert p["events"].feed.cache_path == os.path.join(ICS_CACHE_DIR, "events.ics")
    assert p["meals"].feed.cache_path == os.path.join(ICS_CACHE_DIR, "meals.ics")


def test_fixture_context_has_events_and_tonights_two_meals():
    import datetime as dt
    from kitchen_display import settings
    from kitchen_display.__main__ import _fixture_providers, build_context
    cfg = settings.load()
    now = dt.datetime.now()
    ctx = build_context(cfg, _fixture_providers(cfg), now)
    assert ctx.events
    assert ctx.meals[now.date()] == ["Crispy Gnocchi With Tomato and Red Onion",
                                     "Fast Oven Barbecue Chicken"]
```

(If `tests/test_main.py` does not already `import os` at the top, add it.)

- [ ] **Step 3: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_ics.py tests/test_main.py -q`
Expected: failures on `shift_dates`, `FixtureFeed`, `_calendar_providers`, `ICS_CACHE_DIR`.

- [ ] **Step 4: Implement `shift_dates` and `FixtureFeed`**

In `kitchen_display/providers/ics.py`, add `import re` to the imports and append:

```python
ANCHOR = dt.date(2026, 1, 1)
_DATED = ("DTSTART", "DTEND", "EXDATE", "RECURRENCE-ID")
_DATE_TOKEN = re.compile(r"\d{8}(?=T|,|$)")


def shift_dates(text, days):
    """Move every date in DTSTART/DTEND/EXDATE/RECURRENCE-ID by `days`.

    Fixtures are written against ANCHOR so they read as a fixed week; shifting
    them to today at load means --fixture never shows an empty, aged-out
    agenda. Nothing else is touched (use COUNT, not UNTIL, in fixture rules).
    """
    delta = dt.timedelta(days=days)

    def move(m):
        day = dt.datetime.strptime(m.group(0), "%Y%m%d").date() + delta
        return day.strftime("%Y%m%d")

    out = []
    for line in text.splitlines():
        head, sep, value = line.partition(":")
        if sep and head.split(";", 1)[0] in _DATED:
            line = head + ":" + _DATE_TOKEN.sub(move, value)
        out.append(line)
    return "\n".join(out) + "\n"


class FixtureFeed:
    """A bundled ICS file with ANCHOR moved to today. Same text() as IcsFeed,
    so --fixture exercises the real parser."""

    def __init__(self, path, today=dt.date.today):
        self.path = path
        self._today = today

    def text(self):
        with open(self.path, encoding="utf-8") as f:
            raw = f.read()
        return shift_dates(raw, (self._today() - ANCHOR).days)
```

- [ ] **Step 5: Config, gitignore**

In `kitchen_display/config_example.py`, add to the `config` dict:

```python
    "timezone": "America/Denver",
    # Secrets — set these in config.py, never here. Unset leaves the block empty.
    "calendar_ics_url": None,   # Google Calendar: Settings → the calendar → Secret address in iCal format
    "meals_ics_url": None,      # AnyList: Meal Plan → Settings → calendar feed URL
    "ics_max_age_s": 24 * 60 * 60,   # use the last good copy this long when a fetch fails
```

Append to `.gitignore`:

```
kitchen_display/cache/
```

- [ ] **Step 6: Wire the providers**

In `kitchen_display/__main__.py`:

Add imports:

```python
from zoneinfo import ZoneInfo
```

and change `from .providers import base, forecast` to:

```python
from .providers import base, calendar, forecast, ics
```

Add constants after `RUN_SH`:

```python
ICS_CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache")
FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
```

Replace `_live_providers` and `_fixture_providers` with:

```python
def _calendar_providers(cfg):
    """Real ICS providers for each URL that is set; Null for the rest."""
    tz = ZoneInfo(cfg.get("timezone", "America/Denver"))

    def feed(key, name):
        url = cfg.get(key)
        if not url:
            return None
        return ics.IcsFeed(url, os.path.join(ICS_CACHE_DIR, name),
                           max_age_s=cfg.get("ics_max_age_s", 24 * 3600))

    events, meals = feed("calendar_ics_url", "events.ics"), feed("meals_ics_url", "meals.ics")
    return {"events": calendar.IcsEvents(events, tz) if events else base.NullEvents(),
            "meals": calendar.IcsMeals(meals, tz) if meals else base.NullMeals()}


def _live_providers(cfg):
    """Live feeds. A missing weather config.py falls back to the fixture
    forecast — a missing key should degrade the panel, not stop the daemon
    booting — but the calendar stays live: fake events on the wall would lie."""
    try:
        from inky_weather.config import config as wx_cfg
        fc = forecast.ForecastProvider(wx_cfg)
    except ImportError:
        logging.warning("inky_weather/config.py missing; using the fixture forecast")
        fc = forecast.FixtureForecastProvider()
    return {"forecast": fc, **_calendar_providers(cfg)}


def _fixture_providers(cfg=None):
    tz = ZoneInfo((cfg or {}).get("timezone", "America/Denver"))
    return {"forecast": forecast.FixtureForecastProvider(),
            "events": calendar.IcsEvents(
                ics.FixtureFeed(os.path.join(FIXTURES, "events.ics")), tz),
            "meals": calendar.IcsMeals(
                ics.FixtureFeed(os.path.join(FIXTURES, "meals.ics")), tz)}
```

and in `main`, change the providers line to:

```python
    providers = _fixture_providers(cfg) if args.fixture else _live_providers(cfg)
```

- [ ] **Step 7: README**

In `README.md`, replace the `kitchen_display/config.py` bullet under `## Configuration` with:

```markdown
- `kitchen_display/config.py` — optional; overrides anything in
  `kitchen_display/config_example.py`: refresh timings, and the two calendar
  feeds. The feed URLs are secrets (anyone holding one can read the calendar):
  - `calendar_ics_url` — Google Calendar → Settings → "Colorado Davis Moore
    Family Calendar" → Integrate calendar → **Secret address in iCal format**.
  - `meals_ics_url` — AnyList → Meal Plan → Settings → calendar feed URL.

  Leave either unset and that block stays empty. After editing on the Pi,
  long-press D; the version string in the header confirms the restart. The
  last good copy of each feed is kept in `kitchen_display/cache/` and used for
  up to `ics_max_age_s` (24h) when a fetch fails.
```

- [ ] **Step 8: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest -q`
Expected: full suite green.

- [ ] **Step 9: Commit**

```bash
git add kitchen_display/providers/ics.py kitchen_display/fixtures kitchen_display/__main__.py \
        kitchen_display/config_example.py .gitignore README.md tests/test_ics.py tests/test_main.py
git commit -m "feat: wire the calendar and meal-plan feeds, with shifting fixtures

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Visual review on the quantized panel

**Files:** none committed (renders go to `*.out.png`, already gitignored).

- [ ] **Step 1: Render and quantize the fixture views**

```bash
.venv/bin/python -m kitchen_display --fixture --view glance --out glance.out.png
.venv/bin/python tools/panel_sim.py glance.out.png glance.sim.out.png
.venv/bin/python -m kitchen_display --fixture --view nextweek --out nextweek.out.png
.venv/bin/python tools/panel_sim.py nextweek.out.png nextweek.sim.out.png
```

- [ ] **Step 2: Inspect the `.sim.out.png` images** (Read them). Check:
  - Today's rail shows TONIGHT with both meals, and TOMORROW "Maque Choux" (no trailing-space gap).
  - Tomorrow's agenda row (five events) ends in "+N more" (or "+N" at the right edge if squeezed to one line).
  - "Grandparents visiting" appears with ALL DAY on three consecutive days; "Camping trip" shows "5:00p" on its first day and ALL DAY after.
  - "Cancelled meeting" is absent; "Work call" (16:00Z) reads 10:00a while Denver is on MDT, 9:00a on MST.
  - Nothing overprints; the rail's bottom 18px is clear.

- [ ] **Step 3: Real data (when the URLs are in `kitchen_display/config.py`)**

On the Mac or the Pi, render a two-meal night from the live meal plan:

```bash
.venv/bin/python - <<'EOF'
import datetime as dt
from kitchen_display import __main__ as m, settings
from kitchen_display.views import registry
cfg = settings.load()
now = dt.datetime(2026, 8, 11, 17, 0)
ctx = m.build_context(cfg, m._live_providers(cfg), now)
print("events:", None if ctx.events is None else len(ctx.events),
      "meals:", ctx.meals)
registry.render("glance", ctx).save("aug11.out.png")
EOF
.venv/bin/python tools/panel_sim.py aug11.out.png aug11.sim.out.png
```

Inspect `aug11.sim.out.png`. If the AnyList feed does not carry August any more, note it and check today's live render instead (`--view glance --out live.out.png`). If the URLs are not configured on this machine, say so in the handoff; this step then happens on the Pi after deploy.

- [ ] **Step 4: Fix anything the review finds**, with a test first where the problem is behavioural, and commit each fix separately.
