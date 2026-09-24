# Kitchen Display Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the kitchen display daemon — a long-running view manager with a refresh gate, four rear buttons, and a resting "glance" view — on top of the forked weather app.

**Architecture:** One process, one worker thread. GPIO callbacks and the presence sensor push events onto a `queue.Queue`; the main loop folds each event into a small `State`, asks a pure `gate.decide()` whether to draw, then builds a `Context` from providers and hands it to a view that is a pure function of data to image. Providers that fail return `None` and their block is dropped from the layout, which is what lets Core ship before the calendar and Home Assistant slices exist.

**Tech Stack:** Python 3.11+, Pillow, requests, pytest, `inky` (Pi only, lazy import), systemd.

**Spec:** `docs/superpowers/specs/2026-09-23-kitchen-display-core-design.md`

## Global Constraints

- Panel is 800×480, seven inks: black, white, red, green, blue, yellow, orange. **No gray.**
- Never encode meaning in lightness. Faintness comes from coverage (`render._dotted_line`, `render._faint_rect`), never a pale colour.
- Every new colour constant must be added to the quantization guard in `tests/test_smoke.py` style, asserting it does not snap to white.
- Green is the weakest ink on white — low-stakes markers only.
- `inky_weather/` is not modified by this plan. Its 169 existing tests must keep passing after every task.
- Shared drawing helpers are imported from `inky_weather.render`, never copied.
- The `inky` library is imported lazily, inside the function that pushes to the panel, so tests and PNG rendering work on a Mac.
- All fetches are wrapped so one dead feed never blanks the panel.
- Gate timings live in `kitchen_display/config.py` (gitignored), with defaults in `config_example.py`.
- Run tests with `.venv/bin/python -m pytest` from the repo root.

## Review Focus

These are input classes the spec implies but that no single feature task would naturally exercise. Each has a test assigned to the task that owns the code.

1. **Clock moves backward** (NTP correction, DST fall-back): `last_refresh_at` ends up in the future, the elapsed delta goes negative, and a naive `>` comparison means the panel never refreshes again. Expected: treat a future `last_refresh_at` as "due now". — Task 2.
2. **Midnight rollover while a button view is up**: the agenda's "today" must be recomputed from `now` at render time, never cached from process start, or the panel shows yesterday's week until restart. — Task 5.
3. **Fewer than six forecast hours available** (late-night truncation, partial API response): the rail's hourly list must render what exists rather than raising IndexError. — Task 6.
4. **Button bounce and presses during a 25s render**: repeated presses must coalesce into one pending request per view rather than queueing five full refreshes back to back. — Task 3.
5. **Degenerate calendar events** (empty title, title far wider than the column, all-day event spanning several days): must render without overflowing the column or crashing. — Task 5.

---

### Task 1: Package skeleton, config, and the Panel protocol

**Files:**
- Create: `kitchen_display/__init__.py`
- Create: `kitchen_display/config_example.py`
- Create: `kitchen_display/settings.py`
- Create: `kitchen_display/hw/__init__.py`
- Create: `kitchen_display/hw/panel.py`
- Test: `tests/test_panel.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `settings.load()` returning a settings dict with keys `refresh_interval_s`, `staleness_ceiling_s`, `long_press_s`, `debounce_s`, `daily_restart_hour`, `location_name`. `panel.NullPanel` (records images in `.shown`), `panel.PngPanel(path)`, `panel.InkyPanel`, all exposing `show(img) -> None`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_panel.py
from PIL import Image

from kitchen_display.hw import panel


def _img():
    return Image.new("RGB", (800, 480), (255, 255, 255))


def test_null_panel_records_what_it_was_shown():
    p = panel.NullPanel()
    img = _img()
    p.show(img)
    assert p.shown == [img]


def test_png_panel_writes_the_file(tmp_path):
    dest = tmp_path / "out.png"
    panel.PngPanel(str(dest)).show(_img())
    assert dest.exists()
    with Image.open(dest) as im:
        assert im.size == (800, 480)


def test_inky_panel_does_not_import_inky_until_shown():
    # Constructing must be safe on a Mac, where the inky library is not installed.
    panel.InkyPanel()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_panel.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'kitchen_display'`

- [ ] **Step 3: Write minimal implementation**

```python
# kitchen_display/__init__.py
```

```python
# kitchen_display/hw/__init__.py
```

```python
# kitchen_display/hw/panel.py
"""Output targets for a rendered image.

The Inky Impression is the real target; the other two exist so the whole
daemon can run on a development machine with no panel attached.
"""


class NullPanel:
    """Records what it was shown. Used by tests and by --simulate."""

    def __init__(self):
        self.shown = []

    def show(self, img):
        self.shown.append(img)


class PngPanel:
    """Writes each frame to a PNG path. Used by --out."""

    def __init__(self, path):
        self.path = path

    def show(self, img):
        img.save(self.path)


class InkyPanel:
    """The real 7-colour panel.

    `inky` needs Pi-only GPIO libraries, so it is imported inside show() —
    constructing an InkyPanel on a Mac must not raise.
    """

    def __init__(self):
        self._display = None

    def show(self, img):
        if self._display is None:
            from inky.auto import auto
            self._display = auto()
        self._display.set_image(img.convert("RGB"))
        self._display.show()
```

```python
# kitchen_display/config_example.py
"""Defaults. Copy to kitchen_display/config.py to override; config.py is
gitignored.

Timings live here rather than in code so they can be tuned on the wall
without a redeploy.
"""
config = {
    "location_name": "Lafayette, CO",
    "refresh_interval_s": 60 * 60,        # base cadence: hourly
    "staleness_ceiling_s": 3 * 60 * 60,   # refresh even if occupied past this
    "long_press_s": 2.0,
    "debounce_s": 0.2,
    "daily_restart_hour": 4,              # exit 0 at ~04:00 so run.sh pulls updates
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_panel.py -v`
Expected: 3 passed

- [ ] **Step 5: Add the settings loader with its test**

```python
# append to tests/test_panel.py
from kitchen_display import settings


def test_settings_fall_back_to_the_defaults_when_config_py_is_absent():
    cfg = settings.load()
    assert cfg["refresh_interval_s"] == 3600
    assert cfg["staleness_ceiling_s"] == 10800
```

```python
# kitchen_display/settings.py
"""Load kitchen_display/config.py, falling back to the bundled defaults."""


def load():
    try:
        from .config import config
    except ImportError:
        from .config_example import config
    return dict(config)
```

- [ ] **Step 6: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: 173 passed (169 inherited + 4 new)

- [ ] **Step 7: Commit**

```bash
git add kitchen_display tests/test_panel.py
git commit -m "feat: kitchen_display package skeleton, settings loader, panel targets"
```

---

### Task 2: The refresh gate

**Files:**
- Create: `kitchen_display/events.py`
- Create: `kitchen_display/gate.py`
- Test: `tests/test_gate.py`

**Interfaces:**
- Consumes: `settings.load()` from Task 1.
- Produces: `events.ButtonPressed(name: str, long: bool)`, `events.PresenceChanged(occupied: bool)`, `events.Tick`, `events.Request(view: str, reason: str)`, `events.State(current_view, last_refresh_at, occupied, pending)`; `gate.decide(state, now, cfg) -> Decision` where `Decision` is `gate.Render(view, reason)`, `gate.HOLD`, or `gate.NOTHING`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_gate.py
import datetime as dt

import pytest

from kitchen_display import events, gate

CFG = {"refresh_interval_s": 3600, "staleness_ceiling_s": 10800}
NOW = dt.datetime(2026, 9, 23, 14, 0, 0)


def _state(**kw):
    base = dict(current_view="glance",
                last_refresh_at=NOW - dt.timedelta(minutes=5),
                occupied=False,
                pending=None)
    base.update(kw)
    return events.State(**base)


def test_nothing_to_do_when_fresh_and_no_request():
    assert gate.decide(_state(), NOW, CFG) is gate.NOTHING


def test_button_request_renders_immediately_even_when_occupied():
    st = _state(occupied=True, pending=events.Request("weather", "button"))
    d = gate.decide(st, NOW, CFG)
    assert isinstance(d, gate.Render)
    assert (d.view, d.reason) == ("weather", "button")


def test_scheduled_refresh_renders_glance_when_room_is_empty():
    st = _state(last_refresh_at=NOW - dt.timedelta(hours=2), current_view="weather")
    d = gate.decide(st, NOW, CFG)
    assert isinstance(d, gate.Render)
    assert (d.view, d.reason) == ("glance", "scheduled")


def test_scheduled_refresh_holds_while_the_room_is_occupied():
    st = _state(last_refresh_at=NOW - dt.timedelta(hours=2), occupied=True)
    assert gate.decide(st, NOW, CFG) is gate.HOLD


def test_held_refresh_flushes_the_moment_the_room_clears():
    st = _state(last_refresh_at=NOW - dt.timedelta(hours=2),
                occupied=False,
                pending=events.Request("glance", "scheduled"))
    d = gate.decide(st, NOW, CFG)
    assert isinstance(d, gate.Render)
    assert d.reason == "scheduled"


def test_staleness_ceiling_beats_occupancy():
    st = _state(last_refresh_at=NOW - dt.timedelta(hours=4), occupied=True)
    d = gate.decide(st, NOW, CFG)
    assert isinstance(d, gate.Render)
    assert (d.view, d.reason) == ("glance", "stale")


def test_clock_moving_backward_counts_as_due():
    # Review Focus 1: an NTP correction or DST fall-back can leave last_refresh_at
    # in the future. A naive elapsed > interval comparison would go negative and
    # the panel would never refresh again.
    st = _state(last_refresh_at=NOW + dt.timedelta(hours=6))
    d = gate.decide(st, NOW, CFG)
    assert isinstance(d, gate.Render)
    assert d.view == "glance"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_gate.py -v`
Expected: FAIL with `ImportError: cannot import name 'events'`

- [ ] **Step 3: Write minimal implementation**

```python
# kitchen_display/events.py
"""Everything that can happen to the display, plus the state it folds into."""
import datetime as dt
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ButtonPressed:
    name: str          # "A" | "B" | "C" | "D"
    long: bool = False


@dataclass(frozen=True)
class PresenceChanged:
    occupied: bool


@dataclass(frozen=True)
class Tick:
    pass


@dataclass(frozen=True)
class Request:
    view: str          # "glance" | "weather" | "nextweek"
    reason: str        # "button" | "scheduled" | "startup"


@dataclass
class State:
    current_view: str
    last_refresh_at: dt.datetime
    occupied: bool
    pending: Optional[Request] = None
```

```python
# kitchen_display/gate.py
"""The anti-flash refresh gate.

Colour e-ink has no partial refresh: every update flashes through the palette
for 20-35 seconds. The only lever is *when* that happens, which is what this
module decides. It is pure — no clock, no I/O — so every rule is a table test.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Render:
    view: str
    reason: str        # "button" | "scheduled" | "stale" | "startup"


class _Hold:
    def __repr__(self):
        return "HOLD"


class _Nothing:
    def __repr__(self):
        return "NOTHING"


HOLD = _Hold()
NOTHING = _Nothing()


def _elapsed_s(state, now):
    """Seconds since the last refresh.

    A clock that jumped backward leaves last_refresh_at in the future. Treat
    that as 'infinitely stale' rather than letting a negative number park the
    panel forever.
    """
    delta = (now - state.last_refresh_at).total_seconds()
    return float("inf") if delta < 0 else delta


def decide(state, now, cfg):
    """Return Render(view, reason), HOLD, or NOTHING."""
    elapsed = _elapsed_s(state, now)

    # 1. Someone is standing there and pressed a button.
    if state.pending is not None and state.pending.reason == "button":
        return Render(state.pending.view, "button")

    # 2. Stale beats a lie, even in someone's face.
    if elapsed > cfg["staleness_ceiling_s"]:
        return Render("glance", "stale")

    # 3/4. Due, or held from earlier and the room has now cleared.
    due = elapsed > cfg["refresh_interval_s"] or state.pending is not None
    if due:
        if state.occupied:
            return HOLD
        pending = state.pending
        return Render(pending.view if pending else "glance",
                      pending.reason if pending else "scheduled")

    return NOTHING
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_gate.py -v`
Expected: 7 passed

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: 180 passed

- [ ] **Step 6: Commit**

```bash
git add kitchen_display/events.py kitchen_display/gate.py tests/test_gate.py
git commit -m "feat: pure refresh gate with occupancy hold and staleness ceiling"
```

---

### Task 3: The manager loop

**Files:**
- Create: `kitchen_display/manager.py`
- Test: `tests/test_manager.py`

**Interfaces:**
- Consumes: `events.*`, `gate.decide` (Task 2), `panel.NullPanel` (Task 1).
- Produces: `manager.Manager(queue, panel, renderer, cfg, clock)` with `.state`, `.step(timeout)` (process at most one event and act on the gate), and `.run()`. `renderer` is any callable `(view_name, now) -> PIL.Image`. `clock` is a callable returning a `datetime`, injected so tests can move time.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_manager.py
import datetime as dt
import queue

from PIL import Image

from kitchen_display import events, manager
from kitchen_display.hw import panel

CFG = {"refresh_interval_s": 3600, "staleness_ceiling_s": 10800,
       "daily_restart_hour": 4}


class FakeClock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, **kw):
        self.now += dt.timedelta(**kw)


def _renderer(calls):
    def render(view, now):
        calls.append((view, now))
        return Image.new("RGB", (800, 480), (255, 255, 255))
    return render


def _manager(clock, calls, q=None):
    return manager.Manager(q or queue.Queue(), panel.NullPanel(),
                           _renderer(calls), CFG, clock)


def test_startup_renders_the_glance_immediately():
    clock = FakeClock(dt.datetime(2026, 9, 23, 14, 0))
    calls = []
    m = _manager(clock, calls)
    m.start()
    assert calls == [("glance", clock.now)]
    assert len(m.panel.shown) == 1


def test_button_press_renders_that_view_at_once():
    clock = FakeClock(dt.datetime(2026, 9, 23, 14, 0))
    calls, q = [], queue.Queue()
    m = _manager(clock, calls, q)
    m.start()
    calls.clear()
    q.put(events.ButtonPressed("B"))
    m.step(timeout=0)
    assert calls == [("weather", clock.now)]
    assert m.state.current_view == "weather"


def test_scheduled_refresh_returns_to_glance_from_a_button_view():
    clock = FakeClock(dt.datetime(2026, 9, 23, 14, 0))
    calls, q = [], queue.Queue()
    m = _manager(clock, calls, q)
    m.start()
    q.put(events.ButtonPressed("B"))
    m.step(timeout=0)
    calls.clear()
    clock.advance(hours=2)
    q.put(events.Tick())
    m.step(timeout=0)
    assert calls == [("glance", clock.now)]


def test_repeated_presses_of_the_same_button_coalesce():
    # Review Focus 4: a bouncing button, or an impatient person pressing during
    # a 25s render, must not queue five full refreshes back to back.
    clock = FakeClock(dt.datetime(2026, 9, 23, 14, 0))
    calls, q = [], queue.Queue()
    m = _manager(clock, calls, q)
    m.start()
    calls.clear()
    for _ in range(5):
        q.put(events.ButtonPressed("B"))
    while not q.empty():
        m.step(timeout=0)
    assert len(calls) == 1


def test_a_failing_renderer_does_not_kill_the_loop():
    clock = FakeClock(dt.datetime(2026, 9, 23, 14, 0))
    q = queue.Queue()

    def boom(view, now):
        raise RuntimeError("fetch exploded")

    m = manager.Manager(q, panel.NullPanel(), boom, CFG, clock)
    m.start()                      # must not raise
    q.put(events.ButtonPressed("B"))
    m.step(timeout=0)              # must not raise
    assert m.state.current_view == "weather"


def test_presence_clearing_flushes_a_held_refresh():
    clock = FakeClock(dt.datetime(2026, 9, 23, 14, 0))
    calls, q = [], queue.Queue()
    m = _manager(clock, calls, q)
    m.start()
    q.put(events.PresenceChanged(True))
    m.step(timeout=0)
    calls.clear()
    clock.advance(hours=2)
    q.put(events.Tick())
    m.step(timeout=0)
    assert calls == []             # held while occupied
    q.put(events.PresenceChanged(False))
    m.step(timeout=0)
    assert calls == [("glance", clock.now)]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_manager.py -v`
Expected: FAIL with `ImportError: cannot import name 'manager'`

- [ ] **Step 3: Write minimal implementation**

```python
# kitchen_display/manager.py
"""The main loop: fold events into state, ask the gate, draw if told to."""
import datetime as dt
import logging
import queue as _queue

from . import events, gate

log = logging.getLogger(__name__)

# Short press picks a view; long press is handled by the caller (A-long swaps to
# next week, D-long restarts the process).
BUTTON_VIEWS = {"A": "glance", "B": "weather", "C": None, "D": "glance"}
BUTTON_VIEWS_LONG = {"A": "nextweek", "B": "weather", "C": None, "D": None}


class RestartRequested(Exception):
    """Raised out of run() when the daemon should exit 0 for a code update."""


class Manager:
    def __init__(self, queue, panel, renderer, cfg, clock):
        self.queue = queue
        self.panel = panel
        self.renderer = renderer
        self.cfg = cfg
        self.clock = clock
        self.state = events.State(current_view="glance",
                                  last_refresh_at=dt.datetime.min,
                                  occupied=False,
                                  pending=None)
        self._last_restart_day = None

    # -- event folding -------------------------------------------------
    def _fold(self, event):
        st = self.state
        if isinstance(event, events.PresenceChanged):
            st.occupied = event.occupied
        elif isinstance(event, events.ButtonPressed):
            if event.long and event.name == "D":
                raise RestartRequested()
            table = BUTTON_VIEWS_LONG if event.long else BUTTON_VIEWS
            view = table.get(event.name)
            if view is None:
                return
            # Coalesce: one pending request, not one per bounce.
            st.pending = events.Request(view, "button")

    # -- rendering -----------------------------------------------------
    def _render(self, decision):
        now = self.clock()
        try:
            img = self.renderer(decision.view, now)
            self.panel.show(img)
        except Exception:
            log.exception("render failed for view %s", decision.view)
        self.state.current_view = decision.view
        self.state.last_refresh_at = now
        self.state.pending = None

    def _act(self):
        decision = gate.decide(self.state, self.clock(), self.cfg)
        if isinstance(decision, gate.Render):
            self._render(decision)
        elif decision is gate.HOLD and self.state.pending is None:
            self.state.pending = events.Request("glance", "scheduled")

    def start(self):
        """Draw once at startup so the panel is never blank after a restart."""
        self.state.pending = events.Request("glance", "startup")
        self._render(gate.Render("glance", "startup"))

    def step(self, timeout=60):
        """Process one wake-up.

        Everything already queued is folded in before acting, so a bouncing
        button or five impatient presses during a 25s render coalesce into a
        single refresh rather than five back to back.
        """
        try:
            event = self.queue.get(timeout=timeout) if timeout else self.queue.get_nowait()
        except _queue.Empty:
            event = events.Tick()
        self._fold(event)
        while True:
            try:
                self._fold(self.queue.get_nowait())
            except _queue.Empty:
                break
        self._act()

    def run(self):
        self.start()
        while True:
            self.step(timeout=60)
            self._maybe_restart()

    def _maybe_restart(self):
        """Exit 0 once a day so run.sh pulls new code on the systemd restart."""
        now = self.clock()
        if (now.hour == self.cfg["daily_restart_hour"]
                and self._last_restart_day != now.date()
                and self.state.pending is None):
            self._last_restart_day = now.date()
            raise RestartRequested()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_manager.py -v`
Expected: 6 passed

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: 186 passed

- [ ] **Step 6: Commit**

```bash
git add kitchen_display/manager.py tests/test_manager.py
git commit -m "feat: manager loop with event folding, coalescing, and safe render"
```

---

### Task 4: Providers and the render context

**Files:**
- Create: `kitchen_display/providers/__init__.py`
- Create: `kitchen_display/providers/base.py`
- Create: `kitchen_display/providers/forecast.py`
- Test: `tests/test_providers.py`

**Interfaces:**
- Consumes: `inky_weather.weather` (`fetch_live`, `days_from`, `fetch_sun`, `load_from_fixtures`).
- Produces: `base.Event(start, end, title, all_day)`, `base.NowWeather(temp_f, feels_f, condition, icon_uri)`, `base.Forecast(hours, days, sun)`, `base.Context(now, version, location_name, forecast, now_wx, events, meals)`, `base.safe(fn, default=None)`, `base.NullEvents`, `base.NullMeals`, `forecast.ForecastProvider(cfg)` and `forecast.FixtureForecastProvider()` both exposing `.fetch() -> Forecast | None`, and `forecast.now_from(forecast) -> NowWeather | None`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_providers.py
import datetime as dt

from kitchen_display.providers import base, forecast


def test_safe_swallows_exceptions_and_returns_the_default():
    def boom():
        raise RuntimeError("no network")

    assert base.safe(boom) is None
    assert base.safe(boom, default=[]) == []


def test_safe_passes_through_a_good_value():
    assert base.safe(lambda: 42) == 42


def test_null_providers_return_none():
    assert base.NullEvents().fetch(dt.date(2026, 9, 23), 7) is None
    assert base.NullMeals().fetch(dt.date(2026, 9, 23), 7) is None


def test_fixture_forecast_provider_returns_hours_and_days():
    f = forecast.FixtureForecastProvider().fetch()
    assert f is not None
    assert len(f.hours) >= 6
    assert len(f.days) >= 7
    assert "temp_f" in f.hours[0]


def test_now_from_forecast_uses_the_first_hour():
    f = forecast.FixtureForecastProvider().fetch()
    now = forecast.now_from(f)
    assert now.temp_f == f.hours[0]["temp_f"]
    assert now.feels_f == f.hours[0]["feels_f"]


def test_now_from_none_forecast_is_none():
    assert forecast.now_from(None) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_providers.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'kitchen_display.providers'`

- [ ] **Step 3: Write minimal implementation**

```python
# kitchen_display/providers/__init__.py
```

```python
# kitchen_display/providers/base.py
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
    start: Optional[dt.datetime]     # None for all-day
    end: Optional[dt.datetime]
    title: str
    all_day: bool = False
    date: Optional[dt.date] = None   # the day an all-day event belongs to


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
    meals: Optional[dict] = None


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
```

```python
# kitchen_display/providers/forecast.py
"""Forecast data, borrowed wholesale from the weather app."""
import datetime as dt
import os

from inky_weather import weather

from .base import Forecast, NowWeather, safe

FIXTURE_DIR = os.path.join(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))), "inky_weather", "fixtures")


class ForecastProvider:
    """Live Google Weather + Open-Meteo, via the weather app's own fetchers."""

    def __init__(self, cfg):
        self.cfg = cfg

    def fetch(self):
        def _go():
            hours, days = weather.fetch_live(
                self.cfg["lat"], self.cfg["long"], self.cfg["google_weather_key"])
            days = weather.days_from(days, dt.date.today())
            sun = safe(lambda: weather.fetch_sun(
                self.cfg["lat"], self.cfg["long"]), default={}) or {}
            return Forecast(hours=hours, days=days, sun=sun)
        return safe(_go)


class FixtureForecastProvider:
    """Canned data so the glance can be developed with no network."""

    def fetch(self):
        def _go():
            hours, days = weather.load_from_fixtures(FIXTURE_DIR)
            return Forecast(hours=hours, days=days,
                            sun={"sunrise": "6a", "sunset": "7p"})
        return safe(_go)


def now_from(forecast):
    """Derive a 'now' reading from the first forecast hour.

    The Home Assistant slice replaces this with the station's own sun-corrected
    temperature; until then the forecast's current hour is the honest answer.
    """
    if forecast is None or not forecast.hours:
        return None
    h = forecast.hours[0]
    return NowWeather(temp_f=h["temp_f"], feels_f=h.get("feels_f", h["temp_f"]),
                      condition=h.get("condition", ""), icon_uri=h.get("icon_uri", ""))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_providers.py -v`
Expected: 6 passed

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: 192 passed

- [ ] **Step 6: Commit**

```bash
git add kitchen_display/providers tests/test_providers.py
git commit -m "feat: provider protocols, null providers, and forecast provider"
```

---

### Task 5: The agenda renderer

**Files:**
- Create: `kitchen_display/views/__init__.py`
- Create: `kitchen_display/views/agenda.py`
- Test: `tests/test_agenda.py`

**Interfaces:**
- Consumes: `providers.base.Event`, `inky_weather.render` (`display_font`, `_dotted_line`, `INK`, `RED`, `PAPER`).
- Produces: `agenda.fit_text(text, font, max_px) -> str`, `agenda.day_rows(events, start_date, days, today) -> list[DayRow]` (all-day events are placed by `Event.date`), `agenda.DayRow(date, label, sublabel, is_today, is_weekend, items)`, `agenda.Item(time_label, title, all_day)`, `agenda.render(draw, x, y, w, h, rows)`, and `agenda.AGENDA_COLORS` (the list the quantization guard checks).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_agenda.py
import datetime as dt

from PIL import Image, ImageDraw

from inky_weather import render as wrender
from kitchen_display.providers.base import Event
from kitchen_display.views import agenda

MON = dt.date(2026, 9, 21)


def _ev(day, hour, title, all_day=False):
    if all_day:
        return Event(start=None, end=None, title=title, all_day=True, date=day)
    start = dt.datetime.combine(day, dt.time(hour))
    return Event(start=start, end=start + dt.timedelta(hours=1), title=title)


def test_day_rows_covers_every_day_even_the_empty_ones():
    rows = agenda.day_rows([], MON, 7, today=MON)
    assert len(rows) == 7
    assert [r.date for r in rows] == [MON + dt.timedelta(days=i) for i in range(7)]
    assert rows[1].items == []


def test_today_and_weekend_are_marked():
    rows = agenda.day_rows([], MON, 7, today=MON)
    assert rows[0].is_today and not rows[1].is_today
    assert not rows[0].is_weekend
    assert rows[5].is_weekend and rows[6].is_weekend


def test_events_land_on_their_day_in_time_order():
    evs = [_ev(MON, 16, "Late"), _ev(MON, 9, "Early")]
    rows = agenda.day_rows(evs, MON, 7, today=MON)
    assert [i.title for i in rows[0].items] == ["Early", "Late"]
    assert rows[0].items[0].time_label == "9:00a"


def test_all_day_events_sort_first_and_carry_no_time():
    evs = [_ev(MON, 9, "Soccer"), _ev(MON, 0, "No school", all_day=True)]
    rows = agenda.day_rows(evs, MON, 7, today=MON)
    assert rows[0].items[0].all_day is True
    assert rows[0].items[0].time_label is None


def test_midnight_rollover_uses_the_passed_today_not_a_cached_one():
    # Review Focus 2: "today" must be recomputed at render time, or the panel
    # shows yesterday's week until the next restart.
    rows = agenda.day_rows([], MON, 7, today=MON + dt.timedelta(days=1))
    assert not rows[0].is_today
    assert rows[1].is_today


def test_fit_text_ellipsizes_rather_than_overflowing():
    font = wrender.display_font(19, 400)
    long = "Dog Adoption Virtual Home Visit (all 4 of us present)"
    out = agenda.fit_text(long, font, 240)
    assert out != long
    assert out.endswith("…")
    assert font.getlength(out) <= 240


def test_fit_text_leaves_short_text_alone():
    font = wrender.display_font(19, 400)
    assert agenda.fit_text("Piano", font, 240) == "Piano"


def test_fit_text_handles_an_empty_title():
    # Review Focus 5: a calendar entry with no summary must not crash the render.
    font = wrender.display_font(19, 400)
    assert agenda.fit_text("", font, 240) == ""


def test_render_draws_within_its_box_and_returns_nothing():
    img = Image.new("RGB", (800, 480), wrender.PAPER)
    d = ImageDraw.Draw(img)
    rows = agenda.day_rows([_ev(MON, 9, "Soccer")], MON, 7, today=MON)
    agenda.render(d, 290, 52, 492, 410, rows)
    # Nothing drawn outside the box: the column left of x=290 stays paper.
    for y in range(52, 462, 10):
        assert img.getpixel((280, y)) == wrender.PAPER


def test_agenda_colors_survive_seven_color_quantization():
    PANEL = [(0, 0, 0), (255, 255, 255), (0, 255, 0), (0, 0, 255),
             (255, 0, 0), (255, 255, 0), (255, 140, 0)]

    def nearest(c):
        return min(PANEL, key=lambda p: sum((a - b) ** 2 for a, b in zip(p, c)))

    for color in agenda.AGENDA_COLORS:
        assert nearest(color) != (255, 255, 255), color
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_agenda.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'kitchen_display.views'`

- [ ] **Step 3: Write minimal implementation**

```python
# kitchen_display/views/__init__.py
```

```python
# kitchen_display/views/agenda.py
"""Day-per-row agenda, shared by the glance (this week) and A-long (next week).

One renderer, two call sites, so the two views cannot drift apart.
"""
import datetime as dt
from dataclasses import dataclass, field
from typing import Optional

from inky_weather import render as wr

# Colours this view introduces. Guarded by a quantization test: anything that
# snaps to white would be invisible on the panel.
AGENDA_COLORS = [wr.INK, wr.RED]

ROW_GAP = 4
DAY_COL_W = 78
TIME_GAP = 8


@dataclass(frozen=True)
class Item:
    time_label: Optional[str]
    title: str
    all_day: bool = False


@dataclass
class DayRow:
    date: dt.date
    label: str
    sublabel: str
    is_today: bool
    is_weekend: bool
    items: list = field(default_factory=list)


def _time_label(when):
    """'9:00a' / '4:15p' — the panel is read at a glance, not parsed."""
    return when.strftime("%-I:%M%p").lower().replace("am", "a").replace("pm", "p")


def day_rows(events, start_date, days, today):
    """Group events into one row per day, starting at start_date.

    An all-day event carries no start time, so it is placed by its `date`
    field instead.
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
    for ev in events or []:
        date = ev.date if ev.all_day else (ev.start.date() if ev.start else None)
        row = by_date.get(date)
        if row is None:
            continue
        row.items.append(Item(
            time_label=None if ev.all_day else _time_label(ev.start),
            title=ev.title or "",
            all_day=ev.all_day))
    for row in rows:
        row.items.sort(key=lambda it: (not it.all_day, it.time_label or ""))
    return rows


def fit_text(text, font, max_px):
    """Truncate with an ellipsis so a long title never overflows its column."""
    if not text or font.getlength(text) <= max_px:
        return text
    out = text
    while out and font.getlength(out + "…") > max_px:
        out = out[:-1]
    return out + "…" if out else ""


def render(draw, x, y, w, h, rows):
    """Draw the agenda inside the box at (x, y, w, h)."""
    day_font = wr.display_font(19, 600)
    sub_font = wr.display_font(12, 300)
    time_font = wr.display_font(19, 600)
    item_font = wr.display_font(19, 400)
    quiet_font = wr.display_font(19, 300)

    row_h = h / len(rows)
    text_x = x + DAY_COL_W + TIME_GAP
    text_w = w - DAY_COL_W - TIME_GAP

    for i, row in enumerate(rows):
        top = y + i * row_h
        if i:
            wr._dotted_line(draw, x, x + w, top - ROW_GAP / 2, wr.INK)
        day_color = wr.RED if row.is_weekend else wr.INK
        if row.is_today:
            draw.rectangle([x - 8, top, x - 5, top + row_h - ROW_GAP], fill=wr.INK)
        draw.text((x, top), row.label, font=day_font, fill=day_color)
        draw.text((x, top + 20), row.sublabel, font=sub_font, fill=wr.INK)

        if not row.items:
            draw.text((text_x, top), "—", font=quiet_font, fill=wr.INK)
            continue

        iy = top
        for item in row.items[:3]:
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
            avail = text_w - (tx - text_x)
            draw.text((tx, iy), fit_text(item.title, item_font, avail),
                      font=item_font, fill=wr.INK)
            iy += 23
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_agenda.py -v`
Expected: 10 passed

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: 202 passed

- [ ] **Step 6: Commit**

```bash
git add kitchen_display/views tests/test_agenda.py
git commit -m "feat: agenda renderer with ellipsizing, weekend marking, all-day tags"
```

---

### Task 6: The glance view

**Files:**
- Create: `kitchen_display/views/glance.py`
- Test: `tests/test_glance.py`

**Interfaces:**
- Consumes: `providers.base.Context`, `views.agenda` (Task 5), `inky_weather.render`, `inky_weather.icons.get_icon`.
- Produces: `glance.hourly_slice(forecast, count=6) -> list`, `glance.dinner_lines(meals, today) -> (str|None, str|None, str|None)`, `glance.render(ctx) -> PIL.Image`, `glance.GLANCE_COLORS`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_glance.py
import datetime as dt

from kitchen_display.providers import forecast as fc
from kitchen_display.providers.base import Context, Forecast
from kitchen_display.views import glance

NOW = dt.datetime(2026, 9, 21, 20, 0)


def _ctx(**kw):
    f = fc.FixtureForecastProvider().fetch()
    base = dict(now=NOW, version="abc1234", location_name="Lafayette, CO",
                forecast=f, now_wx=fc.now_from(f), events=None, meals=None)
    base.update(kw)
    return Context(**base)


def test_hourly_slice_returns_six_hours():
    f = fc.FixtureForecastProvider().fetch()
    assert len(glance.hourly_slice(f, count=6)) == 6


def test_hourly_slice_survives_a_short_forecast():
    # Review Focus 3: a late-night or truncated response can carry fewer hours
    # than the rail wants. Render what exists instead of raising IndexError.
    f = fc.FixtureForecastProvider().fetch()
    short = Forecast(hours=f.hours[:2], days=f.days, sun=f.sun)
    assert len(glance.hourly_slice(short, count=6)) == 2


def test_hourly_slice_of_none_is_empty():
    assert glance.hourly_slice(None) == []


def test_dinner_lines_when_tonight_is_planned():
    meals = {dt.date(2026, 9, 21): "Chicken tikka masala",
             dt.date(2026, 9, 22): "Pasta with Garlicky Broccoli"}
    tonight, label, nxt = glance.dinner_lines(meals, dt.date(2026, 9, 21))
    assert tonight == "Chicken tikka masala"
    assert (label, nxt) == ("Tomorrow", "Pasta with Garlicky Broccoli")


def test_dinner_lines_when_tonight_is_empty_shows_the_next_planned_meal():
    meals = {dt.date(2026, 9, 24): "Chicken And Couscous With Chickpeas"}
    tonight, label, nxt = glance.dinner_lines(meals, dt.date(2026, 9, 21))
    assert tonight is None
    assert label == "Thu"
    assert nxt == "Chicken And Couscous With Chickpeas"


def test_dinner_lines_with_no_meal_provider_is_all_none():
    assert glance.dinner_lines(None, dt.date(2026, 9, 21)) == (None, None, None)


def test_render_produces_a_panel_sized_image():
    img = glance.render(_ctx())
    assert img.size == (800, 480)
    assert img.mode == "RGB"


def test_render_without_calendar_says_so_and_still_draws():
    img = glance.render(_ctx(events=None))
    assert img.size == (800, 480)


def test_render_without_any_forecast_still_draws():
    img = glance.render(_ctx(forecast=None, now_wx=None))
    assert img.size == (800, 480)


def test_glance_colors_survive_seven_color_quantization():
    PANEL = [(0, 0, 0), (255, 255, 255), (0, 255, 0), (0, 0, 255),
             (255, 0, 0), (255, 255, 0), (255, 140, 0)]

    def nearest(c):
        return min(PANEL, key=lambda p: sum((a - b) ** 2 for a, b in zip(p, c)))

    for color in glance.GLANCE_COLORS:
        assert nearest(color) != (255, 255, 255), color
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_glance.py -v`
Expected: FAIL with `ImportError: cannot import name 'glance'`

- [ ] **Step 3: Write minimal implementation**

```python
# kitchen_display/views/glance.py
"""The resting view: layout G — weather rail on the left, week agenda on the right.

Any block whose data is None is dropped and the rest reflows, which is what
lets Core ship before the calendar and Home Assistant slices exist.
"""
import datetime as dt
import os

from PIL import Image, ImageDraw

from inky_weather import icons
from inky_weather import render as wr

from . import agenda

GLANCE_COLORS = [wr.INK, wr.RED, wr.BLUE, wr.ORANGE]

ICON_CACHE = os.path.join(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))), "inky_weather", "assets", "icons")

RAIL_X = 18
RAIL_W = 232
VRULE_X = 268
AGENDA_X = 290
HEADER_H = 42
BODY_Y = 52


def hourly_slice(forecast, count=6):
    """The next `count` forecast hours, or as many as exist."""
    if forecast is None or not forecast.hours:
        return []
    return list(forecast.hours[:count])


def dinner_lines(meals, today):
    """(tonight, next_label, next_meal).

    Tonight is None when nothing is planned — the rail says so in light weight
    and shows the next planned meal beneath, rather than hiding the block.
    """
    if not meals:
        return (None, None, None)
    tonight = meals.get(today)
    future = sorted(d for d in meals if d > today)
    if not future:
        return (tonight, None, None)
    nxt = future[0]
    label = "Tomorrow" if nxt == today + dt.timedelta(days=1) else nxt.strftime("%a")
    return (tonight, label, meals[nxt])


def _header(draw, ctx):
    draw.text((RAIL_X, 6), ctx.now.strftime("%a %b %-d").upper(),
              font=wr.display_font(26, 600), fill=wr.INK)
    meta = " · ".join(x for x in (
        ctx.location_name,
        "updated " + ctx.now.strftime("%-I:%M%p").lower().lstrip("0"),
        ctx.version) if x)
    draw.text((wr.WIDTH - RAIL_X, 14), meta, font=wr.display_font(14, 300),
              fill=wr.INK, anchor="ra")
    draw.line([RAIL_X, HEADER_H, wr.WIDTH - RAIL_X, HEADER_H], fill=wr.INK, width=2)


def _rail(img, draw, ctx):
    y = BODY_Y
    if ctx.now_wx is not None:
        draw.text((RAIL_X, y - 8), f"{ctx.now_wx.temp_f}°",
                  font=wr.display_font(88, 600), fill=wr.temp_color(ctx.now_wx.temp_f))
        icon = icons.get_icon(ctx.now_wx.icon_uri, 38, ICON_CACHE)
        img.paste(icon, (RAIL_X + 140, y + 4), icon)
        draw.text((RAIL_X + 140, y + 46), ctx.now_wx.condition.replace("_", " ").title(),
                  font=wr.display_font(16, 500), fill=wr.INK)
        draw.text((RAIL_X + 140, y + 64), f"feels {ctx.now_wx.feels_f}°",
                  font=wr.display_font(14, 300), fill=wr.INK)
        y += 96

    if ctx.forecast is not None and ctx.forecast.days:
        d0 = ctx.forecast.days[0]
        draw.text((RAIL_X, y), f"{d0['hi_f']}°", font=wr.display_font(22, 600),
                  fill=wr.temp_color(d0["hi_f"]))
        draw.text((RAIL_X + 42, y), f"/ {d0['lo_f']}°", font=wr.display_font(22, 600),
                  fill=wr.INK)
        sunset = (ctx.forecast.sun or {}).get("sunset")
        if sunset:
            draw.text((RAIL_X + 110, y + 4), "sunset " + sunset,
                      font=wr.display_font(13, 300), fill=wr.INK)
        y += 32

    hours = hourly_slice(ctx.forecast)
    if hours:
        wr._dotted_line(draw, RAIL_X, RAIL_X + RAIL_W, y, wr.INK)
        y += 8
        for h in hours:
            draw.text((RAIL_X, y), h["ampm_label"], font=wr.display_font(14, 300),
                      fill=wr.INK)
            icon = icons.get_icon(h.get("icon_uri", ""), 22, ICON_CACHE)
            img.paste(icon, (RAIL_X + 34, y), icon)
            draw.text((RAIL_X + 62, y), f"{h['temp_f']}°",
                      font=wr.display_font(17, 600), fill=wr.temp_color(h["temp_f"]))
            if h.get("pop"):
                draw.text((RAIL_X + 104, y + 2), f"{h['pop']}%",
                          font=wr.display_font(13, 600), fill=wr.BLUE)
            y += 24

    tonight, label, nxt = dinner_lines(ctx.meals, ctx.now.date())
    if ctx.meals is not None:
        wr._dotted_line(draw, RAIL_X, RAIL_X + RAIL_W, y + 4, wr.INK)
        y += 14
        draw.text((RAIL_X, y), "TONIGHT", font=wr.display_font(13, 600), fill=wr.RED)
        y += 16
        if tonight:
            draw.text((RAIL_X, y), agenda.fit_text(tonight, wr.display_font(19, 600),
                                                   RAIL_W),
                      font=wr.display_font(19, 600), fill=wr.INK)
        else:
            draw.text((RAIL_X, y), "Nothing planned",
                      font=wr.display_font(16, 300), fill=wr.INK)
        y += 24
        if nxt:
            draw.text((RAIL_X, y), label.upper(), font=wr.display_font(13, 600),
                      fill=wr.INK)
            draw.text((RAIL_X, y + 15),
                      agenda.fit_text(nxt, wr.display_font(17, 600), RAIL_W),
                      font=wr.display_font(17, 600), fill=wr.INK)


def render(ctx):
    """Compose the 800x480 glance. Returns an RGB PIL Image."""
    img = Image.new("RGB", (wr.WIDTH, wr.HEIGHT), wr.PAPER)
    draw = ImageDraw.Draw(img)

    _header(draw, ctx)
    _rail(img, draw, ctx)
    draw.line([VRULE_X, BODY_Y, VRULE_X, wr.HEIGHT - 18], fill=wr.INK, width=2)

    agenda_w = wr.WIDTH - RAIL_X - AGENDA_X
    if ctx.events is None:
        draw.text((AGENDA_X, BODY_Y), "No calendar configured",
                  font=wr.display_font(19, 300), fill=wr.INK)
    else:
        today = ctx.now.date()
        rows = agenda.day_rows(ctx.events, today, 7, today=today)
        agenda.render(draw, AGENDA_X, BODY_Y, agenda_w,
                      wr.HEIGHT - BODY_Y - 18, rows)
    return img
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_glance.py -v`
Expected: 10 passed

- [ ] **Step 5: Eyeball the render against the panel palette**

```bash
.venv/bin/python -c "
from kitchen_display.providers import forecast as fc
from kitchen_display.providers.base import Context, Forecast
from kitchen_display.views import glance
import datetime as dt
f = fc.FixtureForecastProvider().fetch()
ctx = Context(now=dt.datetime.now(), version='dev', location_name='Lafayette, CO',
              forecast=f, now_wx=fc.now_from(f), events=None, meals=None)
glance.render(ctx).save('glance.out.png')
print('wrote glance.out.png')"
```

Expected: a file that looks like layout G with the rail populated and "No calendar configured" on the right. Note: judge the panel-quantized version in Task 10, not this PNG.

- [ ] **Step 6: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: 212 passed

- [ ] **Step 7: Commit**

```bash
git add kitchen_display/views/glance.py tests/test_glance.py
git commit -m "feat: glance view — weather rail plus week agenda, blocks drop when data is absent"
```

---

### Task 7: Weather and next-week views, and the view registry

**Files:**
- Create: `kitchen_display/views/weather.py`
- Create: `kitchen_display/views/nextweek.py`
- Create: `kitchen_display/views/registry.py`
- Test: `tests/test_views.py`

**Interfaces:**
- Consumes: `inky_weather.main.build_image`, `views.agenda`, `views.glance`.
- Produces: `registry.render(view_name, ctx) -> PIL.Image` and `registry.VIEWS` (a dict of name to callable). `weather.render(ctx)`, `nextweek.render(ctx)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_views.py
import datetime as dt

import pytest

from kitchen_display.providers import forecast as fc
from kitchen_display.providers.base import Context
from kitchen_display.views import registry

NOW = dt.datetime(2026, 9, 21, 20, 0)


def _ctx(**kw):
    f = fc.FixtureForecastProvider().fetch()
    base = dict(now=NOW, version="abc1234", location_name="Lafayette, CO",
                forecast=f, now_wx=fc.now_from(f), events=None, meals=None)
    base.update(kw)
    return Context(**base)


@pytest.mark.parametrize("name", ["glance", "weather", "nextweek"])
def test_every_registered_view_renders_a_panel_sized_image(name):
    img = registry.render(name, _ctx())
    assert img.size == (800, 480)
    assert img.mode == "RGB"


def test_unknown_view_falls_back_to_the_glance():
    img = registry.render("does-not-exist", _ctx())
    assert img.size == (800, 480)


def test_nextweek_starts_on_the_monday_after_this_week():
    from kitchen_display.views import nextweek

    # Mon 2026-09-21 -> next week starts Mon 2026-09-28
    assert nextweek.start_date(dt.date(2026, 9, 21)) == dt.date(2026, 9, 28)
    # Sun 2026-09-27 -> still the same following Monday
    assert nextweek.start_date(dt.date(2026, 9, 27)) == dt.date(2026, 9, 28)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_views.py -v`
Expected: FAIL with `ImportError: cannot import name 'registry'`

- [ ] **Step 3: Write minimal implementation**

```python
# kitchen_display/views/weather.py
"""The existing weather app, wrapped as a view."""
from inky_weather import main as weather_main


def render(ctx):
    """Delegate to the weather app's own composer.

    It fetches its own data — this view is the one exception to 'views never
    fetch', because the app already owns that pipeline end to end and there is
    nothing to gain by taking it apart.
    """
    cfg = {"location_name": ctx.location_name}
    try:
        from inky_weather.config import config as live_cfg
        cfg = live_cfg
        return weather_main.build_image(False, cfg)
    except Exception:
        return weather_main.build_image(True, cfg)
```

```python
# kitchen_display/views/nextweek.py
"""Next week's agenda, full width — the A-long-press view."""
import datetime as dt

from PIL import Image, ImageDraw

from inky_weather import render as wr

from . import agenda

MARGIN = 18
BODY_Y = 52


def start_date(today):
    """The Monday of the week after the one containing `today`."""
    return today + dt.timedelta(days=7 - today.weekday())


def render(ctx):
    img = Image.new("RGB", (wr.WIDTH, wr.HEIGHT), wr.PAPER)
    draw = ImageDraw.Draw(img)

    start = start_date(ctx.now.date())
    end = start + dt.timedelta(days=6)
    title = f"NEXT WEEK · {start.strftime('%b %-d')}–{end.strftime('%b %-d')}"
    draw.text((MARGIN, 6), title, font=wr.display_font(26, 600), fill=wr.INK)
    draw.text((wr.WIDTH - MARGIN, 14), ctx.version, font=wr.display_font(14, 300),
              fill=wr.INK, anchor="ra")
    draw.line([MARGIN, 42, wr.WIDTH - MARGIN, 42], fill=wr.INK, width=2)

    if ctx.events is None:
        draw.text((MARGIN, BODY_Y), "No calendar configured",
                  font=wr.display_font(19, 300), fill=wr.INK)
        return img

    rows = agenda.day_rows(ctx.events, start, 7, today=ctx.now.date())
    agenda.render(draw, MARGIN, BODY_Y, wr.WIDTH - 2 * MARGIN,
                  wr.HEIGHT - BODY_Y - 18, rows)
    return img
```

```python
# kitchen_display/views/registry.py
"""Name to renderer. The manager knows view names; it does not import views."""
from . import glance, nextweek, weather

VIEWS = {
    "glance": glance.render,
    "weather": weather.render,
    "nextweek": nextweek.render,
}


def render(name, ctx):
    """Render a view by name, falling back to the glance for anything unknown."""
    return VIEWS.get(name, glance.render)(ctx)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_views.py -v`
Expected: 5 passed

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: 217 passed

- [ ] **Step 6: Commit**

```bash
git add kitchen_display/views tests/test_views.py
git commit -m "feat: weather and next-week views behind a name registry"
```

---

### Task 8: Buttons and presence

**Files:**
- Create: `kitchen_display/hw/buttons.py`
- Create: `kitchen_display/hw/presence.py`
- Test: `tests/test_buttons.py`

**Interfaces:**
- Consumes: `events.ButtonPressed`, `events.PresenceChanged`.
- Produces: `buttons.classify(press_s, cfg) -> bool` (True for long), `buttons.Debouncer(cfg)` with `.accept(name, at_s) -> bool`, `buttons.GpioButtons(queue, cfg)` with `.start()`, `buttons.KeyboardButtons(queue, cfg)` with `.start()`, `buttons.PINS`; `presence.AlwaysEmpty()` with `.occupied() -> bool` and `.start(queue)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_buttons.py
import queue

from kitchen_display import events
from kitchen_display.hw import buttons, presence

CFG = {"long_press_s": 2.0, "debounce_s": 0.2}


def test_short_and_long_presses_are_classified_by_duration():
    assert buttons.classify(0.1, CFG) is False
    assert buttons.classify(1.9, CFG) is False
    assert buttons.classify(2.5, CFG) is True


def test_debouncer_drops_a_second_edge_inside_the_window():
    d = buttons.Debouncer(CFG)
    assert d.accept("A", 10.00) is True
    assert d.accept("A", 10.05) is False      # bounce
    assert d.accept("A", 10.30) is True       # a real second press


def test_debouncer_tracks_each_button_separately():
    d = buttons.Debouncer(CFG)
    assert d.accept("A", 10.00) is True
    assert d.accept("B", 10.01) is True


def test_every_button_has_a_pin():
    assert set(buttons.PINS) == {"A", "B", "C", "D"}
    assert sorted(buttons.PINS.values()) == [5, 6, 16, 24]


def test_keyboard_buttons_translate_keys_into_events():
    q = queue.Queue()
    kb = buttons.KeyboardButtons(q, CFG)
    kb.handle_key("b")
    kb.handle_key("A")
    first, second = q.get_nowait(), q.get_nowait()
    assert first == events.ButtonPressed("B", long=False)
    assert second == events.ButtonPressed("A", long=True)


def test_keyboard_buttons_ignore_unmapped_keys():
    q = queue.Queue()
    buttons.KeyboardButtons(q, CFG).handle_key("z")
    assert q.empty()


def test_always_empty_presence_reports_an_empty_room():
    assert presence.AlwaysEmpty().occupied() is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_buttons.py -v`
Expected: FAIL with `ImportError: cannot import name 'buttons'`

- [ ] **Step 3: Write minimal implementation**

```python
# kitchen_display/hw/buttons.py
"""Rear buttons: GPIO on the Pi, keyboard keys under --simulate.

The logic worth testing — debounce and short/long classification — is pure and
lives here; the GPIO wiring is a thin shell around it.
"""
import logging
import time

from .. import events

log = logging.getLogger(__name__)

# Inky Impression 7.3" rear buttons, active-low.
PINS = {"A": 5, "B": 6, "C": 16, "D": 24}


def classify(press_s, cfg):
    """True if the press was long enough to count as a long press."""
    return press_s >= cfg["long_press_s"]


class Debouncer:
    """Drops repeat edges inside the debounce window, per button."""

    def __init__(self, cfg):
        self.window = cfg["debounce_s"]
        self._last = {}

    def accept(self, name, at_s):
        last = self._last.get(name)
        if last is not None and at_s - last < self.window:
            return False
        self._last[name] = at_s
        return True


class GpioButtons:
    """Real buttons. gpiod is imported lazily so this module loads on a Mac."""

    def __init__(self, queue, cfg):
        self.queue = queue
        self.cfg = cfg
        self.debouncer = Debouncer(cfg)
        self._down_at = {}

    def start(self):
        import gpiod
        from gpiod.line import Bias, Edge

        chip = gpiod.Chip("/dev/gpiochip0")
        settings = gpiod.LineSettings(edge_detection=Edge.BOTH,
                                      bias=Bias.PULL_UP,
                                      debounce_period=0)
        request = chip.request_lines(
            consumer="kitchen-display",
            config={pin: settings for pin in PINS.values()})
        by_pin = {pin: name for name, pin in PINS.items()}
        while True:
            for event in request.read_edge_events():
                name = by_pin.get(event.line_offset)
                if name is None:
                    continue
                self._edge(name, event.event_type == event.Type.FALLING_EDGE,
                           time.monotonic())

    def _edge(self, name, pressed, at_s):
        """Falling edge = button down (active-low); rising edge = released."""
        if pressed:
            self._down_at[name] = at_s
            return
        down = self._down_at.pop(name, None)
        if down is None or not self.debouncer.accept(name, at_s):
            return
        self.queue.put(events.ButtonPressed(name, long=classify(at_s - down, self.cfg)))


class KeyboardButtons:
    """--simulate: a/b/c/d are short presses, A/B/C/D are long ones."""

    def __init__(self, queue, cfg):
        self.queue = queue
        self.cfg = cfg

    def handle_key(self, key):
        name = key.upper()
        if name not in PINS:
            return
        self.queue.put(events.ButtonPressed(name, long=key.isupper()))

    def start(self):
        import sys
        for line in sys.stdin:
            for ch in line.strip():
                self.handle_key(ch)
```

```python
# kitchen_display/hw/presence.py
"""Room occupancy. Core ships the stub; the mmWave slice replaces it.

Not PIR: a PIR reads a still person as 'empty' and flashes in their face.
"""


class AlwaysEmpty:
    """The Core stub. With it, the gate's occupancy rules collapse to
    'refresh hourly' — exactly the weather app's old behaviour."""

    def occupied(self):
        return False

    def start(self, queue):
        return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_buttons.py -v`
Expected: 7 passed

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: 224 passed

- [ ] **Step 6: Commit**

```bash
git add kitchen_display/hw tests/test_buttons.py
git commit -m "feat: button debounce, long-press classification, GPIO and keyboard sources"
```

---

### Task 9: The entry point

**Files:**
- Create: `kitchen_display/__main__.py`
- Test: `tests/test_main.py`

**Interfaces:**
- Consumes: everything above.
- Produces: `__main__.build_context(cfg, providers, now)`, `__main__.make_renderer(cfg, providers)`, `__main__.main(argv) -> int`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_main.py
import os

from kitchen_display.__main__ import main


def test_out_writes_a_png(tmp_path):
    dest = tmp_path / "glance.png"
    rc = main(["--fixture", "--view", "glance", "--out", str(dest)])
    assert rc == 0
    assert dest.exists() and dest.stat().st_size > 0


def test_out_works_for_every_view(tmp_path):
    for view in ("glance", "nextweek"):
        dest = tmp_path / f"{view}.png"
        assert main(["--fixture", "--view", view, "--out", str(dest)]) == 0
        assert dest.exists()


def test_renderer_builds_a_fresh_context_each_call():
    # Review Focus 2 again, at the seam: the renderer must not close over a
    # Context built at startup, or "today" freezes at process start.
    import datetime as dt

    from kitchen_display.__main__ import make_renderer, _fixture_providers
    from kitchen_display import settings

    renderer = make_renderer(settings.load(), _fixture_providers())
    a = renderer("glance", dt.datetime(2026, 9, 21, 12, 0))
    b = renderer("glance", dt.datetime(2026, 9, 22, 12, 0))
    assert a.tobytes() != b.tobytes()      # the header date differs
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_main.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'kitchen_display.__main__'`

- [ ] **Step 3: Write minimal implementation**

```python
# kitchen_display/__main__.py
"""Entry point: wire providers, views, panel, and buttons into the manager."""
import argparse
import logging
import queue
import sys
import threading

from inky_weather import version

from . import manager, settings
from .hw import buttons as buttons_mod
from .hw import panel as panel_mod
from .hw import presence as presence_mod
from .providers import base, forecast
from .views import registry


def _live_providers(cfg):
    """Live feeds, or fixtures if the weather app has no config.py yet —
    a missing key should degrade the panel, not stop the daemon booting."""
    try:
        from inky_weather.config import config as wx_cfg
    except ImportError:
        logging.warning("inky_weather/config.py missing; falling back to fixtures")
        return _fixture_providers()
    return {"forecast": forecast.ForecastProvider(wx_cfg),
            "events": base.NullEvents(),
            "meals": base.NullMeals()}


def _fixture_providers():
    return {"forecast": forecast.FixtureForecastProvider(),
            "events": base.NullEvents(),
            "meals": base.NullMeals()}


def build_context(cfg, providers, now):
    """Fetch everything a view might want. A failed feed becomes None, and the
    block it feeds is dropped from the layout."""
    fc = base.safe(providers["forecast"].fetch)
    return base.Context(
        now=now,
        version=version.get_version(),
        location_name=cfg.get("location_name", ""),
        forecast=fc,
        now_wx=base.safe(lambda: forecast.now_from(fc)),
        events=base.safe(lambda: providers["events"].fetch(now.date(), 14)),
        meals=base.safe(lambda: providers["meals"].fetch(now.date(), 14)),
    )


def make_renderer(cfg, providers):
    """A fresh Context per render, so the date is never frozen at startup."""
    def render(view_name, now):
        return registry.render(view_name, build_context(cfg, providers, now))
    return render


def main(argv=None):
    parser = argparse.ArgumentParser(prog="kitchen_display")
    parser.add_argument("--fixture", action="store_true",
                        help="Use canned data instead of live fetches")
    parser.add_argument("--out", metavar="PATH",
                        help="Render one view to PNG and exit")
    parser.add_argument("--view", default="glance",
                        choices=["glance", "weather", "nextweek"])
    parser.add_argument("--simulate", action="store_true",
                        help="Read a/b/c/d from stdin instead of GPIO")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    cfg = settings.load()
    providers = _fixture_providers() if args.fixture else _live_providers(cfg)
    renderer = make_renderer(cfg, providers)

    if args.out:
        import datetime as dt
        renderer(args.view, dt.datetime.now()).save(args.out)
        print("Wrote", args.out)
        return 0

    import datetime as dt
    q = queue.Queue()
    panel = panel_mod.NullPanel() if args.simulate else panel_mod.InkyPanel()
    src = (buttons_mod.KeyboardButtons if args.simulate else buttons_mod.GpioButtons)(q, cfg)
    threading.Thread(target=src.start, daemon=True).start()
    presence_mod.AlwaysEmpty().start(q)

    m = manager.Manager(q, panel, renderer, cfg, dt.datetime.now)
    try:
        m.run()
    except manager.RestartRequested:
        logging.info("restart requested; exiting 0 so run.sh pulls new code")
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_main.py -v`
Expected: 3 passed

- [ ] **Step 5: Drive the loop by hand**

```bash
printf 'b\na\nA\n' | .venv/bin/python -m kitchen_display --fixture --simulate -v
```

Expected: log lines showing renders for `weather`, then `glance`, then `nextweek`. Ctrl-C to stop.

- [ ] **Step 6: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: 227 passed

- [ ] **Step 7: Commit**

```bash
git add kitchen_display/__main__.py tests/test_main.py
git commit -m "feat: daemon entry point with --out, --view, --fixture, --simulate"
```

---

### Task 10: Deployment, tooling, and docs

**Files:**
- Create: `kitchen-display.service`
- Create: `tools/panel_sim.py`
- Modify: `run.sh` (the `RUN_CMD` default)
- Modify: `README.md`
- Test: `tests/test_panel_sim.py`

**Interfaces:**
- Consumes: `kitchen_display.__main__`.
- Produces: `tools/panel_sim.py` as a CLI (`python tools/panel_sim.py in.png out.png`) and `panel_sim.quantize(img) -> Image`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_panel_sim.py
from PIL import Image

from tools.panel_sim import PANEL, quantize


def test_every_pixel_lands_on_a_panel_ink():
    img = Image.new("RGB", (4, 4))
    img.putdata([(230, 231, 236), (200, 30, 30), (20, 22, 28), (30, 70, 200)] * 4)
    out = quantize(img)
    assert all(px in PANEL for px in out.getdata())


def test_a_near_white_faint_gray_collapses_to_white():
    # This is the shipped bug the tool exists to catch: it looks fine in a PNG
    # and is invisible on the panel.
    img = Image.new("RGB", (1, 1), (230, 231, 236))
    assert quantize(img).getpixel((0, 0)) == (255, 255, 255)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_panel_sim.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tools'`

- [ ] **Step 3: Write minimal implementation**

```python
# tools/__init__.py
```

```python
# tools/panel_sim.py
"""Quantize a render to the seven panel inks.

A --out PNG is not what the panel shows. Review the quantized version, never
the raw PNG, when judging whether a visual change works.
"""
import sys

from PIL import Image

PANEL = [(0, 0, 0), (255, 255, 255), (0, 255, 0), (0, 0, 255),
         (255, 0, 0), (255, 255, 0), (255, 140, 0)]


def _nearest(c):
    return min(PANEL, key=lambda p: sum((a - b) ** 2 for a, b in zip(p, c)))


def quantize(img):
    """Return a copy with every pixel snapped to its nearest panel ink."""
    img = img.convert("RGB")
    out = Image.new("RGB", img.size)
    out.putdata([_nearest(c) for c in img.getdata()])
    return out


def main(argv=None):
    argv = argv or sys.argv[1:]
    if len(argv) != 2:
        print("usage: python tools/panel_sim.py IN.png OUT.png", file=sys.stderr)
        return 2
    with Image.open(argv[0]) as im:
        quantize(im).save(argv[1])
    print("Wrote", argv[1])
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_panel_sim.py -v`
Expected: 2 passed

- [ ] **Step 5: Point run.sh at the daemon**

In `run.sh`, change the `RUN_CMD` default line:

```bash
RUN_CMD="${INKY_RUN_CMD:-$PY -m kitchen_display}"
```

Run: `.venv/bin/python -m pytest tests/test_run_sh.py -v`
Expected: PASS (the inherited run.sh tests exercise the update logic, not the command)

- [ ] **Step 6: Add the systemd unit**

```ini
# kitchen-display.service — install to ~/.config/systemd/user/ and enable with:
#   systemctl --user enable --now kitchen-display
#   sudo loginctl enable-linger $USER      # so it survives logout / starts at boot
[Unit]
Description=Kitchen Display
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=%h/kitchen_display
ExecStart=%h/kitchen_display/run.sh
Restart=always
RestartSec=10
StartLimitBurst=5
StandardOutput=append:%h/kitchen_display.log
StandardError=append:%h/kitchen_display.log

[Install]
WantedBy=default.target
```

- [ ] **Step 7: Rewrite the README for the new project**

Replace the title and intro, keep every hardware and palette section verbatim
(SPI setup, the `spi0-0cs` overlay, the seven-colour rules, the shipped faint-gray
bug), and replace the "Schedule (hourly refresh)" cron section with:

````markdown
## Run it

Development, on a Mac:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install Pillow requests pytest        # NOT inky — it needs Pi-only GPIO libs
python3 -m pytest -q
python3 -m kitchen_display --fixture --view glance --out glance.png
python3 tools/panel_sim.py glance.png panel_sim.png   # review THIS one
```

Drive the whole loop without hardware — `a/b/c/d` are short presses,
`A/B/C/D` long ones:

```bash
python3 -m kitchen_display --fixture --simulate -v
```

On the Pi, systemd runs the daemon and `run.sh` self-updates on every start:

```bash
cp kitchen-display.service ~/.config/systemd/user/
systemctl --user enable --now kitchen-display
sudo loginctl enable-linger $USER
```

Code updates land on the next restart — the daily 04:00 exit, a long press on
button D, or `systemctl --user restart kitchen-display`. The version string in
the panel header is how you confirm an update landed.

## Buttons

| Button | Short press | Long press (2s) |
|---|---|---|
| A | Glance — back home | Next week's agenda |
| B | Weather view | *(historical weather, later)* |
| C | — | — |
| D | Redraw now | Pull and restart |

The glance is the resting view; a scheduled refresh always returns to it.
````

- [ ] **Step 8: Run the full suite one last time**

Run: `.venv/bin/python -m pytest -q`
Expected: 229 passed

- [ ] **Step 9: Commit**

```bash
git add kitchen-display.service tools tests/test_panel_sim.py run.sh README.md
git commit -m "feat: systemd unit, panel simulator tool, and README for the kitchen display"
```

---

## Done when

- `.venv/bin/python -m pytest -q` passes, including all 169 inherited weather tests.
- `python -m kitchen_display --fixture --view glance --out glance.png` produces layout G, and its `panel_sim.png` is legible with no vanished elements.
- `printf 'b\na\nA\n' | python -m kitchen_display --fixture --simulate -v` shows the three views rendering in order.
- On the Pi: `systemctl --user status kitchen-display` is active, the panel shows the glance, each button does what the README table says, and a long press on D brings the panel back with a new version string in the header.
