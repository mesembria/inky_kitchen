"""The existing weather app, wrapped as a view."""
from inky_weather import main as weather_main


def render(ctx):
    """Delegate to the weather app's own composer.

    It fetches its own data — this view is the one exception to 'views never
    fetch', because the app already owns that pipeline end to end and there is
    nothing to gain by taking it apart.

    Bundled fixtures are used only when there is no config.py (development).
    A live fetch that fails raises, so the manager's failure path shows the
    last good image marked stale — never someone else's canned forecast.
    """
    try:
        from inky_weather.config import config as live_cfg
    except ImportError:
        return weather_main.build_image(True, {"location_name": ctx.location_name})
    return weather_main.build_image(False, live_cfg)
