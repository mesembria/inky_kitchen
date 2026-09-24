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


def _flaky(calls, fail_after):
    """Renders fine `fail_after` times, then raises every call."""
    def render(view, now):
        calls.append(view)
        if len(calls) > fail_after:
            raise RuntimeError("network down")
        return Image.new("RGB", (800, 480), (255, 255, 255))
    return render


def test_a_failed_render_shows_the_last_good_image_marked_stale():
    # Spec: never a blank panel, never silently wrong data. The last good image
    # goes back up with a STALE marker so nobody trusts it as current.
    clock = FakeClock(dt.datetime(2026, 9, 23, 14, 0))
    q, p = queue.Queue(), panel.NullPanel()
    m = manager.Manager(q, p, _flaky([], fail_after=1), CFG, clock)
    m.start()
    q.put(events.ButtonPressed("B"))
    m.step(timeout=0)
    assert len(p.shown) == 2
    assert p.shown[1].tobytes() != p.shown[0].tobytes()     # stamped, not identical


def test_a_failed_render_with_nothing_cached_shows_an_error_screen():
    clock = FakeClock(dt.datetime(2026, 9, 23, 14, 0))
    p = panel.NullPanel()
    m = manager.Manager(queue.Queue(), p, _flaky([], fail_after=0), CFG, clock)
    m.start()
    assert len(p.shown) == 1
    assert p.shown[0].size == (800, 480)


def test_file_cache_survives_a_restart(tmp_path):
    # After a restart with the network down, the panel must re-show the last good
    # image (stale), not replace a perfectly good picture with an error screen.
    path = str(tmp_path / "last.png")
    clock = FakeClock(dt.datetime(2026, 9, 23, 14, 0))
    manager.Manager(queue.Queue(), panel.NullPanel(), _flaky([], fail_after=1),
                    CFG, clock, cache=manager.FileCache(path)).start()
    p2 = panel.NullPanel()
    manager.Manager(queue.Queue(), p2, _flaky([], fail_after=0),
                    CFG, clock, cache=manager.FileCache(path)).start()
    assert len(p2.shown) == 1
    white = Image.new("RGB", (800, 480), (255, 255, 255))
    assert p2.shown[0].tobytes() != white.tobytes()          # the stale-stamped cache


def test_a_process_started_during_the_restart_hour_does_not_restart_again():
    # Review finding: _last_restart_day was per-process, so every restart during
    # 04:00-04:59 saw "not yet today" and exited again — a startup flash each
    # time until systemd's start limit killed the unit for good.
    clock = FakeClock(dt.datetime(2026, 9, 23, 4, 0, 30))
    m = _manager(clock, [])
    m.start()
    clock.advance(minutes=5)
    m._maybe_restart()             # must not raise


def test_a_process_running_across_the_restart_time_restarts_once():
    import pytest

    clock = FakeClock(dt.datetime(2026, 9, 23, 3, 59))
    m = _manager(clock, [])
    m.start()
    clock.advance(minutes=2)
    with pytest.raises(manager.RestartRequested):
        m._maybe_restart()
