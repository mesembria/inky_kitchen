"""ICS feeds: expand a calendar into the concrete occurrences in a window.

Both the family calendar and the AnyList meal plan arrive as secret ICS URLs,
so this module is the only place that knows ICS exists. Recurrence (RRULE,
EXDATE, moved instances, DST) is recurring-ical-events' job; ours is keeping
one bad entry from costing the whole feed, and turning ICS's exclusive ends
into the inclusive last day the agenda draws.
"""
import datetime as dt
import logging
import os
import re
import time
from dataclasses import dataclass
from typing import Optional

import icalendar
import recurring_ical_events
import requests

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
    Calendar-level properties come along too: Google's X-WR-TIMEZONE is what
    keeps a UTC-anchored series at its wall time across DST.
    """
    zones = [c for c in cal.subcomponents if c.name == "VTIMEZONE"]
    groups = {}
    for comp in cal.subcomponents:
        if comp.name == "VEVENT":
            groups.setdefault(str(comp.get("UID", id(comp))), []).append(comp)
    for uid, comps in groups.items():
        mini = icalendar.Calendar()
        for key, value in cal.items():
            mini[key] = value
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
    # Bound the window in the display zone. Bare dates would be read against
    # each event's own zone, so a UTC event at 7:00p on the last local day
    # would fall outside a window that ended at 00:00Z.
    lo = dt.datetime.combine(start, dt.time(), tz)
    hi = dt.datetime.combine(end, dt.time(), tz)
    out = []
    for uid, mini in _series(parse(text)):
        try:
            for comp in recurring_ical_events.of(mini).between(lo, hi):
                if str(comp.get("STATUS", "")).upper() == "CANCELLED":
                    continue
                out.append(_occurrence(comp, tz))
        except Exception:
            log.warning("skipping unreadable event series %s", uid, exc_info=True)
    out.sort(key=lambda o: (o.start_date, not o.all_day,
                            o.start.timestamp() if o.start else 0.0))
    return out


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
            # requests would then decode text/* as Latin-1. -sig drops a BOM,
            # which would otherwise make every fetch fail to parse.
            body = resp.content.decode("utf-8-sig", errors="replace")
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
