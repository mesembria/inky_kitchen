# Kitchen Display — Core

Design for the first slice of the kitchen wall dashboard: the view manager, the
refresh gate, the buttons, and the resting glance view. Later slices (calendar,
meals, Home Assistant "now", mmWave presence, 13.3" scaling) build on the
interfaces defined here.

Status: approved 2026-09-23.

## Intent

A wall panel in the kitchen that answers "what's today, what's this week, what's
for dinner, what's it like outside" from five feet away, without anyone touching
it. Two properties matter more than features:

- **It never lies.** A failed fetch shows the last good image marked stale, never
  a blank panel and never silently wrong data.
- **It never flashes at a person.** Colour e-ink has no partial refresh; the
  20–35s rainbow flash is inherent. The only lever is *when* it happens, which is
  what the refresh gate and the presence sensor exist for.

Success: it hangs on the wall and the family stops asking "what's for dinner" and
"what's on today".

### Scope of this slice

In: repo fork, view manager daemon, refresh gate, button handling, the glance
view rendered from forecast data, systemd deployment, dev affordances.

Out (later slices, in this order): Google Calendar events + AnyList meal plan,
Home Assistant "now" weather, mmWave presence sensor, 13.3" scaling. Core ships
useful without any of them — the glance renders with the calendar and meal blocks
absent.

### Decisions carried in from brainstorming

| Decision | Choice |
|---|---|
| Relationship to the weather app | Fork `inky_weather_odin` with history; the standalone weather panel is being repurposed for this build |
| Glance layout | "G" — left rail (now, hourly list, dinner) + seven-day agenda |
| Return from a button view | At the next gated refresh, which always draws glance |
| Auto-cycle | Deferred; not in Core |
| Process model | One process, main loop over an event queue |
| Build order | Core → calendar+meals → HA now → presence → 13.3" |

## Hardware and constraints

- Pimoroni Inky Impression 7.3", 800×480, seven inks: black, white, red, green,
  blue, yellow, orange. **No gray.** Full-refresh only.
- Rear buttons A/B/C/D on GPIO 5, 6, 16, 24, active-low. (The 13.3" reassigns
  one; verify when that slice starts.)
- Raspberry Pi Zero 2 W on the wall, Pi 4 for dev.
- mmWave presence sensor, local to the display Pi. Not PIR — a PIR reads a still
  person as "empty" and flashes in their face.

Palette rules inherited from the weather app README, and they are load-bearing:

- Never encode meaning in lightness. A "faint" element gets its faintness from
  **coverage** (dotted lines, see `_dotted_line()`), never from a pale colour.
- Green is the weakest ink on white. Low-stakes markers only.
- Hierarchy comes from size and position, not tone.
- Every new colour constant goes into the quantization guard test, so a colour
  that would vanish on the hardware fails the suite instead of shipping. This
  bug has already shipped once (faint-gray precip reference lines, invisible on
  the panel, perfect in every PNG preview).

## Repository

`kitchen_display` is a fork of `inky_weather_odin` with full history (15 commits
of specs, plans, tests, `run.sh`, versioning, the quantization guard). The old
`origin` remote is removed; a new one is added when the GitHub repo exists.

```
kitchen_display/
  inky_weather/            # unchanged: weather view + shared drawing primitives
  kitchen_display/         # new package
    __main__.py            # daemon entry; --out/--view/--fixture/--simulate for dev
    manager.py             # main loop + event queue
    gate.py                # refresh gate: pure decision function
    config.example.py      # gate timings now; calendar IDs and HA token later
    views/
      base.py              # View protocol
      glance.py            # layout G
      agenda.py            # day-row renderer, shared by glance and next-week
      weather.py           # thin wrapper over inky_weather.main.build_image
    providers/
      base.py              # Provider protocols + Null implementations
      forecast.py          # wraps inky_weather.weather
    hw/
      panel.py             # Panel protocol; InkyPanel, PngPanel, NullPanel
      buttons.py           # GPIO edge callbacks -> events; KeyboardButtons for --simulate
      presence.py          # Presence protocol; AlwaysEmpty stub
  tests/                   # inherited weather tests (169, passing) + new ones
  tools/panel_sim.py       # quantize a PNG to the seven inks for review
  run.sh                   # kept; RUN_CMD default becomes `python -m kitchen_display`
  kitchen-display.service  # systemd unit
```

`inky_weather/` is deliberately left alone. Its 169 tests keep passing untouched,
and shared drawing helpers are imported from `inky_weather.render` rather than
copied. Extract a `palette.py` only if the import graph gets awkward — not
preemptively.

Two gitignored config files: `inky_weather/config.py` (weather key, lat/long,
location name — already exists) and `kitchen_display/config.py` (gate timings,
later the calendar IDs and HA token).

## Deployment

The hourly cron line goes away. systemd runs `run.sh`, which fetches, resets to
`origin/main`, reinstalls dependencies if `requirements.txt` changed, then execs
the daemon — the same self-update logic as today, just once per process start
instead of once per render.

```ini
[Service]
ExecStart=%h/kitchen_display/run.sh
Restart=always
RestartSec=10
StartLimitBurst=5
```

Code updates therefore land on the **next daemon restart**. Three things restart
it: the daily scheduled exit (~04:00, room empty, exit 0), a long press on D, and
a crash. `version.py` keeps putting the git-describe string in the panel header,
which is how you confirm an update landed.

A commit that imports cleanly and then dies will crash-loop; `StartLimitBurst`
stops it after five tries and the panel holds its last image (e-ink retains
without power to the logic). Fix over SSH. `run.sh` already survives a failed
fetch by running the code on disk, so a network outage never stops the panel.

## Manager loop

One process, one thread doing work. Everything that can happen is an event on a
`queue.Queue`:

- `ButtonPressed(name, long: bool)` — pushed from the GPIO callback thread
- `PresenceChanged(occupied: bool)` — from the sensor; the Core stub never fires
- `Tick` — the loop's own 60s timeout wake

Loop body:

1. `event = q.get(timeout=60)`
2. fold the event into `State`
3. `decision = gate.decide(state, now)`
4. on RENDER: build the `Context` (providers, each wrapped in `_safe`), render the
   view, push to the panel, cache the image, record `last_refresh_at`

```python
@dataclass
class State:
    current_view: str            # "glance" | "weather" | "nextweek"
    last_refresh_at: datetime
    occupied: bool
    pending: Request | None      # (view, reason); reason in {button, scheduled, startup}
```

Rendering happens inline on the main thread. A 20–35s panel update blocks button
handling, but presses queue up and are served immediately after — which is the
correct behaviour, since the panel cannot do two things at once.

Any exception during fetch or render falls back to the cached last image stamped
stale (`render.stamp_stale`), or `render.render_error` if there is no cache. The
daemon does not exit on a render failure.

The daily restart is checked on the same `Tick`, after the gate: if the clock has
passed `DAILY_RESTART_AT` since the last restart and nothing is pending, the
daemon exits 0 and systemd brings it back through `run.sh`. It is a manager
concern, not a gate rule — the gate decides what to draw, never whether to live.

## Refresh gate

`gate.py` is pure — no I/O, no clock of its own, `now` passed in:

```python
def decide(state: State, now: datetime) -> Decision   # RENDER(view, reason) | HOLD | NOTHING
```

Rules, in priority order:

1. **Button request pending** → RENDER immediately. Flash accepted: someone is
   standing there and asked for it.
2. **Staleness ceiling exceeded** (`now - last_refresh_at > 3h`) → RENDER glance
   even if the room is occupied. Stale beats a lie.
3. **Scheduled refresh due** (`now - last_refresh_at > 1h`):
   - room empty → RENDER glance
   - room occupied → HOLD, and set `pending = scheduled`
4. **Room just cleared** with something pending → RENDER it now.
5. Otherwise → NOTHING.

Rule 4 is the entire reason for the sensor: the moment the kitchen empties, the
held refresh flushes, so the panel is current before anyone walks back in.

With the `AlwaysEmpty` presence stub that ships in Core, rules 3 and 4 collapse to
"refresh hourly" — today's behaviour exactly, which is how Core ships useful
before the sensor exists.

Timings live in `kitchen_display/config.py` so they are tunable on the wall
without a code change: `REFRESH_INTERVAL = 1h`, `STALENESS_CEILING = 3h`,
`LONG_PRESS = 2s`, `DEBOUNCE = 200ms`, `DAILY_RESTART_AT = 04:00`.

## Views and buttons

The glance is the **resting view**. It has no button; the scheduled refresh always
draws it, which is also how any button view reverts — quietly, at the next gated
refresh, rather than on a timer that might flash in someone's face.

| Button | Short press | Long press (≥2s) |
|---|---|---|
| A | Glance — back home | Next week's agenda |
| B | Weather view (the existing app) | Historical weather view *(designed in a later slice)* |
| C | unbound | unbound |
| D | Redraw now | Pull and restart |

Button handling: edge callbacks with a 200ms software debounce; the callback
times the release edge to classify short vs long, then pushes one event and
returns. No work happens on the GPIO thread.

A long press on D gives no immediate feedback — the panel holds its last image
for the ~10s restart plus the 20–35s render, then returns showing the new version
string in the header. That header is the confirmation. Spending a full 25s
refresh on an "updating…" flash would cost more than it's worth.

### Glance view (layout G)

Left rail, ~250px:

- "Now" temperature, large; condition icon and word; feels-like
- Today's hi/lo, precipitation chance, sunset
- Next six hours as rows: hour · icon · temp · rain%
- Dinner block: tonight, then tomorrow

Right, ~510px: the seven-day agenda, today first.

- One row per day, `MON / Today` style day label, events to the right
- Weekend day labels in red; today's row marked with a left rule
- All-day events get a small boxed ALL DAY tag
- Days with no events show a light em dash
- Long titles ellipsize; the panel does not rewrite event titles
- When tonight has no meal planned, the block says so in light weight and shows
  the next planned meal beneath

Rendered against real data (Lafayette CO forecast, the family calendar, the
AnyList meal plan) during design, including the awkward cases: a day with three
events, titles over 40 characters, an evening where the hourly forecast is all
overnight, and a night with no meal planned.

### Agenda renderer

`views/agenda.py` takes `(events, start_date, days, width)` and renders the day
rows. The glance calls it at ~510px for this week; A-long calls it at full width
for next week. One renderer, two call sites, so the two cannot drift.

## Providers and context

Views never fetch. The manager builds a `Context` and hands it over, so every view
is a pure function of data to image.

```python
@dataclass
class Context:
    now: datetime
    version: str
    location_name: str
    forecast: Forecast | None       # hours, days, sun
    now_wx: NowWeather | None       # temp, feels, condition
    events: list[Event] | None      # calendar slice
    meals: dict[date, str] | None   # meal-plan slice
```

Each provider has a protocol and a Null implementation returning `None`. The
manager calls each inside `_safe()` — the weather app's existing pattern — so one
dead feed never blanks the panel.

**Any block whose data is `None` is dropped and the rest reflows.** That is what
makes Core shippable with only the forecast wired: the rail draws in full, the
agenda area shows a single "No calendar configured" line, and the dinner block is
absent entirely.

In Core: `ForecastProvider` is real (wraps `inky_weather.weather`), `now_wx` is
derived from `hours[0]` until the HA slice replaces it, `events` and `meals` are
Null.

Later slices swap a Null for a real provider and touch nothing else. The calendar
and meal plan arrive through **one** Google Calendar client: the family calendar
is `Colorado Davis Moore Family Calendar`, and AnyList publishes the meal plan to
a subscribed `AnyList Meal Plan` calendar, so no AnyList API is needed on the Pi.

## Errors and degradation

| Failure | Behaviour |
|---|---|
| One provider raises or times out | That block is dropped; the rest of the view renders |
| All providers fail | Cached last image, stamped stale |
| No cache and everything fails | `render_error` with the message |
| Panel push raises | Logged; loop continues; next refresh retries |
| Network down at restart | `run.sh` runs the code already on disk |
| Bad commit deployed | Crash-loop stopped after 5 tries; panel holds last image |

## Testing

- `gate.decide` — table-driven tests, one per rule, including occupied + due →
  HOLD, then cleared → RENDER, and occupied + 3h → RENDER anyway.
- Manager loop — fake queue, fake clock, `NullPanel` recording what it was shown.
  No GPIO, no network, no display; runs on the Mac.
- Views — render from fixture contexts; assert on structure, plus the
  quantization guard extended to every new colour constant the glance introduces.
- Providers — the Null implementations and the `_safe` wrapping, so a raising
  provider provably yields `None` rather than propagating.
- The inherited 169 weather tests keep passing throughout.

## Dev affordances

`python -m kitchen_display` on the Mac:

- `--out PATH` — write a PNG instead of touching a panel
- `--view glance|weather|nextweek` — render one view and exit
- `--fixture` — canned data for all four providers, no network
- `--simulate` — map keys `a/b/c/d` (uppercase for long press) to button events,
  driving the whole loop from a terminal

`tools/panel_sim.py` quantizes a render to the seven inks — the README's existing
inline snippet, promoted to a script. **Review `panel_sim.png`, never the raw
PNG**, when judging whether a visual change works.

## Open questions deferred to later slices

- Historical weather view (B long press) — not designed.
- Auto-cycle — deferred; the manager carries a `mode` concept so it slots in
  without restructuring.
- mmWave sensor model, and confirming it wires local to the display Pi.
- 13.3" scaling: 800×480 → 1600×1200, including the button GPIO reassignment.
- Whether the glance's next-week button earns its place once the calendar is real.
