import os

from kitchen_display.__main__ import main


def test_out_writes_a_png(tmp_path):
    dest = tmp_path / "glance.png"
    rc = main(["--fixture", "--view", "glance", "--out", str(dest)])
    assert rc == 0
    assert dest.exists() and dest.stat().st_size > 0


def test_out_works_for_every_view(tmp_path):
    for view in ("glance", "nextweek"):
        dest = tmp_path / f"{view}.png"
        assert main(["--fixture", "--view", view, "--out", str(dest)]) == 0
        assert dest.exists()


def test_renderer_builds_a_fresh_context_each_call():
    # Review Focus 2 again, at the seam: the renderer must not close over a
    # Context built at startup, or "today" freezes at process start.
    import datetime as dt

    from kitchen_display.__main__ import make_renderer, _fixture_providers
    from kitchen_display import settings

    renderer = make_renderer(settings.load(), _fixture_providers())
    a = renderer("glance", dt.datetime(2026, 9, 21, 12, 0))
    b = renderer("glance", dt.datetime(2026, 9, 22, 12, 0))
    assert a.tobytes() != b.tobytes()      # the header date differs


def test_a_render_with_every_feed_dead_raises_so_the_stale_path_runs():
    # Review finding: a dead forecast rendered an empty rail headed "updated
    # <now>", counted as success, and overwrote the last good cached image.
    import datetime as dt

    import pytest

    from kitchen_display.__main__ import make_renderer
    from kitchen_display.providers import base

    class Dead:
        def fetch(self):
            raise RuntimeError("network down")

    providers = {"forecast": Dead(), "events": base.NullEvents(), "meals": base.NullMeals()}
    renderer = make_renderer({"location_name": "x"}, providers)
    with pytest.raises(RuntimeError):
        renderer("glance", dt.datetime(2026, 9, 23, 15, 0))
