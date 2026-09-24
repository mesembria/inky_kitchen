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
        from gpiod.line import Bias, Direction, Edge

        # gpiochip0 on the Pi Zero 2 W and Pi 4. Debounce is done in software
        # (Debouncer), so the kernel's debounce_period is left at its default.
        chip = gpiod.Chip("/dev/gpiochip0")
        settings = gpiod.LineSettings(direction=Direction.INPUT,
                                      edge_detection=Edge.BOTH,
                                      bias=Bias.PULL_UP)
        request = chip.request_lines(
            consumer="kitchen-display",
            config={pin: settings for pin in PINS.values()})
        by_pin = {pin: name for name, pin in PINS.items()}
        while True:
            for event in request.read_edge_events():
                name = by_pin.get(event.line_offset)
                if name is None:
                    continue
                self._edge(name, event.event_type == gpiod.EdgeEvent.Type.FALLING_EDGE,
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
