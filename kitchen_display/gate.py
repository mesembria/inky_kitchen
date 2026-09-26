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

    # 3/4. Due, or held from earlier and the room has now cleared. A new day
    # is due too, or "Today" would sit on yesterday for up to an hour.
    due = (elapsed > cfg["refresh_interval_s"]
           or now.date() != state.last_refresh_at.date()
           or state.pending is not None)
    if due:
        if state.occupied:
            return HOLD
        pending = state.pending
        return Render(pending.view if pending else "glance",
                      pending.reason if pending else "scheduled")

    return NOTHING
