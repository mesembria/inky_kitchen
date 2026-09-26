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
