"""Entry point: wire providers, views, panel, and buttons into the manager."""
import argparse
import logging
import os
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

CACHE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "last_display.png")


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
        if view_name == "weather":           # fetches its own data
            return registry.render(view_name, base.Context(
                now=now, version="", location_name=cfg.get("location_name", "")))
        ctx = build_context(cfg, providers, now)
        if ctx.forecast is None and ctx.events is None and ctx.meals is None:
            # Every feed is dead. Rendering now would put up an empty panel
            # headed "updated <now>" and overwrite the last good image; raise so
            # the manager re-shows that image marked STALE instead.
            raise RuntimeError("no data from any provider")
        return registry.render(view_name, ctx)
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

    cache = manager.MemoryCache() if args.simulate else manager.FileCache(CACHE_PATH)
    m = manager.Manager(q, panel, renderer, cfg, dt.datetime.now, cache=cache)
    try:
        m.run()
    except manager.RestartRequested:
        logging.info("restart requested; exiting 0 so run.sh pulls new code")
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
