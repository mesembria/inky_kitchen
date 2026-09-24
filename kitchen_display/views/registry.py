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
