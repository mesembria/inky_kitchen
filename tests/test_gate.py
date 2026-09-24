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
