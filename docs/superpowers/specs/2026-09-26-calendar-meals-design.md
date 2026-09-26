# Kitchen Display — Calendar and Meals

Design for the second slice: real data behind the glance's seven-day agenda,
the next-week view, and the dinner block. Builds on the interfaces in
`2026-09-23-kitchen-display-core-design.md`; nothing in the manager loop or the
view protocol changes.

Status: approved 2026-09-26.

## Intent

Core ships with `NullEvents` and `NullMeals`, so the agenda reads "No calendar
configured" and the dinner block is absent. This slice replaces both with real
providers, and fixes the three Core review minors that only bite once real
events are on the panel.

Success: the glance shows the family calendar and tonight's dinner, correctly,
including the awkward cases (recurring events with exceptions, multi-day trips,
busy days, two-meal nights), and survives a network outage without blanking.

### Decisions carried in from brainstorming

| Decision | Choice |
|---|---|
| Access method | Secret iCal (ICS) URLs, fetched over HTTPS. **Not** the Google Calendar API. |
| Family calendar source | The "Colorado Davis Moore Family Calendar" secret iCal address |
| Meal plan source | AnyList's own ICS feed, directly — not Google's imported copy |
| Recurrence expansion | `icalendar` + `recurring-ical-events` |
| Deferred Core minors | All three in scope: multi-day events, "+N more", midnight "Today" |
| Two meals on one night | Both shown, one per line |

Why ICS over the Google API: no Google Cloud project, no OAuth consent, no
refresh token that expires every seven days in "testing" mode. And the meal plan
skips Google's import sync, which lands roughly every 12 hours (observed
`updated` stamps cluster at 04:00 and 16:00 UTC) — a meal added in AnyList would
otherwise take up to half a day to reach the panel. The cost is expanding
recurrence ourselves, which `recurring-ical-events` handles (RRULE, EXDATE,
RECURRENCE-ID overrides, time zones).

This supersedes the Core spec's "one Google Calendar client" wording: it is now
one ICS client, used twice.

### What the meal plan actually looks like

Read from the live calendar during design:

- Every meal is an **all-day** event, DTEND exclusive (one day long).
- Titles sometimes carry trailing spaces (`"Maque Choux "`).
- `LOCATION` holds a recipe URL or a cookbook page (`"p. 117"`). Ignored.
- Some days have **two** entries (2026-08-11: gnocchi and barbecue chicken).
- Many days have none. The plan is sparse and written a week at a time.

## Components

### `providers/ics.py` — the feed client

```python
class IcsFeed:
    def __init__(self, url, cache_path, timeout=15, max_age_s=24 * 3600): ...
    def text(self) -> str | None
        """Fresh ICS text, else the cached copy if younger than max_age_s, else None."""

def occurrences(text, start: date, end: date, tz) -> list[Occurrence]
    """Every instance overlapping [start, end), recurrence expanded, cancelled dropped."""
```

- `text()` GETs the URL. On a 2xx response it **parses the body first**, and
  only then writes it to `cache_path` (tmp file + `os.replace`, the pattern in
  `inky_weather/cache.py`). A truncated or HTML-error body therefore never
  replaces a good cache.
- On any fetch or parse failure it logs a warning and returns the cached text if
  the file's mtime is within `max_age_s`; otherwise `None`.
- `occurrences` uses `recurring_ical_events.of(cal).between(start, end)`,
  drops `STATUS:CANCELLED`, and converts timed instances to `tz`
  (`America/Denver`). A single VEVENT that raises during conversion is logged and
  skipped; the rest survive.

```python
@dataclass(frozen=True)
class Occurrence:
    title: str
    all_day: bool
    start: datetime | None    # tz-aware, None when all_day
    end: datetime | None
    start_date: date          # first day covered
    end_date: date            # last day covered (inclusive)
```

`end_date` converts ICS's exclusive end to an inclusive last day: an all-day
event with DTEND 2026-10-05 ends on 10-04; a timed event ending exactly at
00:00 ends on the previous day, so an evening event that runs "until midnight"
stays on one row.

### `providers/calendar.py` — two thin adapters

```python
class IcsEvents:
    def __init__(self, feed): ...
    def fetch(self, start_date, days) -> list[Event] | None

class IcsMeals:
    def __init__(self, feed): ...
    def fetch(self, start_date, days) -> dict[date, list[str]] | None
```

- Both return `None` when `feed.text()` is `None`, so the block drops exactly as
  a Null provider's does.
- `IcsEvents` maps each `Occurrence` to an `Event`.
- `IcsMeals` keys each meal by its `start_date`, strips the title, skips empty
  titles, and keeps feed order within a day.
- Both fetch a window of `days` from `start_date`; `__main__` keeps asking for 14
  so next-week is covered.

### Data-shape changes (`providers/base.py`)

- `Event` gains `end_date: Optional[date]` — the inclusive last day covered.
  `date` stays as the first day for all-day events. For timed events,
  `end_date` is set too; `None` (older callers, fixtures) means "one day".
- `Context.meals` becomes `dict[date, list[str]]`.

### Config

`kitchen_display/config_example.py` gains:

```python
"calendar_ics_url": None,   # Google Calendar: Settings → calendar → Secret address in iCal format
"meals_ics_url": None,      # AnyList: Meal Plan → Settings → calendar feed URL
"ics_max_age_s": 24 * 60 * 60,
```

Real values go in the gitignored `kitchen_display/config.py` — the URLs are
secrets (anyone holding one can read the calendar). An unset URL leaves that
provider Null, which is Core's behaviour today.

Cache files live in `kitchen_display/cache/events.ics` and `meals.ics`;
`kitchen_display/cache/` is added to `.gitignore`.

### Wiring (`__main__.py`)

`_live_providers(cfg)` builds `IcsEvents(IcsFeed(...))` / `IcsMeals(IcsFeed(...))`
when the corresponding URL is set, else the Null provider. `_fixture_providers()`
builds the same classes over the bundled fixture files (see Testing), so
`--fixture` runs the real parser. Nothing in `manager.py` changes.

### Dependencies

`requirements.txt` adds `icalendar` and `recurring-ical-events`. Both are pure
Python; `run.sh` reinstalls automatically because `requirements.txt` changed.

## Rendering changes

### Multi-day events (`agenda.day_rows`)

- An event is placed on **every** row from its first day through `end_date`
  that falls in the window. An event that began before `start_date` appears on
  the days it still covers.
- A timed event shows its start time on its first day. On every later day it is
  drawn as an all-day item — the boxed ALL DAY tag, sorted to the top with the
  other all-day items. A trip from Fri 5:00p to Sun 3:00p reads: Fri "5:00p
  Trip", Sat "ALL DAY Trip", Sun "ALL DAY Trip".
- Ordering within a day stays as in Core: all-day (including continuations)
  first, then timed by real start time.

### "+N more" (`agenda.render`)

- When a day has more items than `slot.max_items`, the last visible line is
  replaced by **"+N more"** in light weight (19px, weight 300, INK), where N
  counts every item not drawn. No event disappears without a trace.
- When `layout` has squeezed rows to one line (`max_items == 1`) and a day has
  more than one item, that line keeps the first item, ellipsized to leave room
  for a right-aligned **"+N"** in the same light weight.

### Two meals (`glance.dinner_lines` and the rail)

- `dinner_lines` returns `(tonight: list[str], next_label, next_meals: list[str])`.
- TONIGHT draws up to two meals, one per line, 19px semibold, each ellipsized to
  the rail width. With three or more, the second line becomes "+N more".
- "Nothing planned" behaviour is unchanged when tonight's list is empty.
- The next-planned line shows the first meal of the next planned day, with a
  trailing "+1" (or "+N") when that day has more.
- A two-meal night pushes the rail down one line. Verify it still fits under the
  six-hour list at 480px against `panel_sim.png`; if it does not, drop the
  next-planned line on two-meal nights rather than shrinking type.

### Midnight "Today" (`gate.decide`)

The due check gains one condition:

```python
due = (elapsed > cfg["refresh_interval_s"]
       or now.date() != state.last_refresh_at.date()
       or state.pending is not None)
```

A date change is handled like any scheduled refresh: render if the room is
empty, HOLD if occupied. With the `AlwaysEmpty` stub it redraws at the first
tick after midnight. The gate stays pure — the date comes from the `now`
passed in. A clock that jumps backward across midnight is already caught by
`_elapsed_s` returning infinity (rule 2).

No new colours: everything added draws in INK. The quantization guard is
unchanged.

## Errors and degradation

| Failure | Behaviour |
|---|---|
| Fetch fails, cache younger than `ics_max_age_s` | Cached copy used; warning logged |
| Fetch fails, no cache or cache too old | Provider returns `None`; block dropped (Core behaviour) |
| Body fails to parse | Treated as a failed fetch; the cache is not overwritten |
| One VEVENT fails to convert | That event skipped and logged; the rest render |
| URL not configured | Null provider |
| Provider raises anyway | `base.safe` yields `None` (unchanged) |

The 24-hour cache window is the trade between "never lies" and "never blanks":
calendars change slowly, so yesterday's copy is far more truthful than an
empty agenda; beyond a day, dropping the block is the honest choice.

## Testing

All on the Mac, no network.

**Fixtures** — two hand-written files in `kitchen_display/fixtures/`, with dates
written relative to a fixed anchor and shifted to "today" at load, so they never
age out:

- `events.ics`: a weekly RRULE with one EXDATE and one RECURRENCE-ID moved
  instance; a multi-day all-day event; a Fri 5p–Sun 3p timed event; a
  `STATUS:CANCELLED` event; one `TZID=America/Denver` start and one UTC `Z`
  start; an event ending exactly at midnight; a day with five events.
- `meals.ics`: AnyList's shape — all-day, trailing spaces, a two-meal day, gaps.

**Unit tests**

- `occurrences`: recurrence expansion, EXDATE, moved instance, cancelled
  dropped, both time-zone forms, window edges, event starting before the window,
  midnight-end stays one day, one broken VEVENT skipped.
- `IcsFeed` (HTTP faked): success writes the cache; failure with fresh cache
  returns it; failure with stale cache returns `None`; unparseable body is not
  cached and falls back.
- Adapters: `end_date` inclusivity, meal titles stripped, two meals kept in feed
  order, `None` propagation.
- `day_rows`: multi-day spread, continuation days rendered as all-day, window
  clipping at both ends.
- `agenda.render`: "+N more" at full cap and "+N" at one-line cap.
- `dinner_lines`: two meals tonight, three meals, next-planned "+1", nothing
  planned.
- `gate.decide`: date change → due; date change while occupied → HOLD; same day
  within the interval → NOTHING.
- The existing suite keeps passing, including updated glance tests for the new
  `meals` shape.

**Visual review** — `panel_sim.png` of `--fixture --view glance`, of
`--view nextweek`, and of a real-data glance on a two-meal night: fetch the
live meals feed with `now` set to 2026-08-11 and render that context through
`--out`. Review the quantized
image, never the raw PNG.

## Setup on the Pi (README)

1. Family calendar: Google Calendar → Settings → "Colorado Davis Moore Family
   Calendar" → Integrate calendar → **Secret address in iCal format**.
2. Meal plan: AnyList → Meal Plan → Settings → calendar feed URL.
3. Put both in `~/kitchen_display/kitchen_display/config.py` as
   `calendar_ics_url` / `meals_ics_url`.
4. Long-press D. The header's version string confirms the restart.

## Out of scope

- Event colours per person or per source calendar.
- Showing meal recipe links or page numbers.
- More than one family calendar.
- Push/webhook freshness — hourly polling matches the refresh cadence.
