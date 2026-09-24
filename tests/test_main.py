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
