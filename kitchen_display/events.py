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
