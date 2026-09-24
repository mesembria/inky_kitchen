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
