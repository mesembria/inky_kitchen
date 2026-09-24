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
