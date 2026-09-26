import datetime as dt

from PIL import Image, ImageDraw

from inky_weather import render as wrender
from kitchen_display.providers.base import Event
from kitchen_display.views import agenda

MON = dt.date(2026, 9, 21)


def _ev(day, hour, title, all_day=False):
    if all_day:
        return Event(start=None, end=None, title=title, all_day=True, date=day)
    start = dt.datetime.combine(day, dt.time(hour))
    return Event(start=start, end=start + dt.timedelta(hours=1), title=title)


def test_day_rows_covers_every_day_even_the_empty_ones():
    rows = agenda.day_rows([], MON, 7, today=MON)
    assert len(rows) == 7
    assert [r.date for r in rows] == [MON + dt.timedelta(days=i) for i in range(7)]
    assert rows[1].items == []


def test_today_and_weekend_are_marked():
    rows = agenda.day_rows([], MON, 7, today=MON)
    assert rows[0].is_today and not rows[1].is_today
    assert not rows[0].is_weekend
    assert rows[5].is_weekend and rows[6].is_weekend


def test_events_land_on_their_day_in_time_order():
    evs = [_ev(MON, 16, "Late"), _ev(MON, 9, "Early")]
    rows = agenda.day_rows(evs, MON, 7, today=MON)
    assert [i.title for i in rows[0].items] == ["Early", "Late"]
    assert rows[0].items[0].time_label == "9:00a"


def test_all_day_events_sort_first_and_carry_no_time():
    evs = [_ev(MON, 9, "Soccer"), _ev(MON, 0, "No school", all_day=True)]
    rows = agenda.day_rows(evs, MON, 7, today=MON)
    assert rows[0].items[0].all_day is True
    assert rows[0].items[0].time_label is None


def test_midnight_rollover_uses_the_passed_today_not_a_cached_one():
    # Review Focus 2: "today" must be recomputed at render time, or the panel
    # shows yesterday's week until the next restart.
    rows = agenda.day_rows([], MON, 7, today=MON + dt.timedelta(days=1))
    assert not rows[0].is_today
    assert rows[1].is_today


def test_fit_text_ellipsizes_rather_than_overflowing():
    font = wrender.display_font(19, 400)
    long = "Dog Adoption Virtual Home Visit (all 4 of us present)"
    out = agenda.fit_text(long, font, 240)
    assert out != long
    assert out.endswith("…")
    assert font.getlength(out) <= 240


def test_fit_text_leaves_short_text_alone():
    font = wrender.display_font(19, 400)
    assert agenda.fit_text("Piano", font, 240) == "Piano"


def test_fit_text_handles_an_empty_title():
    # Review Focus 5: a calendar entry with no summary must not crash the render.
    font = wrender.display_font(19, 400)
    assert agenda.fit_text("", font, 240) == ""


def test_render_draws_within_its_box_and_returns_nothing():
    img = Image.new("RGB", (800, 480), wrender.PAPER)
    d = ImageDraw.Draw(img)
    rows = agenda.day_rows([_ev(MON, 9, "Soccer")], MON, 7, today=MON)
    agenda.render(d, 290, 52, 492, 410, rows)
    # Nothing drawn outside the box: the column left of x=290 stays paper.
    for y in range(52, 462, 10):
        assert img.getpixel((280, y)) == wrender.PAPER


def test_agenda_colors_survive_seven_color_quantization():
    PANEL = [(0, 0, 0), (255, 255, 255), (0, 255, 0), (0, 0, 255),
             (255, 0, 0), (255, 255, 0), (255, 140, 0)]

    def nearest(c):
        return min(PANEL, key=lambda p: sum((a - b) ** 2 for a, b in zip(p, c)))

    for color in agenda.AGENDA_COLORS:
        assert nearest(color) != (255, 255, 255), color


def _busy(rows, counts):
    for row, n in zip(rows, counts):
        row.items = [agenda.Item("9:00a", f"Event {i}") for i in range(n)]
    return rows


def test_a_busy_day_gets_a_taller_row_and_rows_never_overlap():
    # Seen on the real family calendar: a three-event Monday overprinted Tuesday
    # when every row had the same fixed height.
    rows = _busy(agenda.day_rows([], MON, 7, today=MON), [3, 1, 2, 1, 2, 1, 3])
    slots = agenda.layout(rows, 410)
    assert slots[0].height >= 3 * agenda.LINE_H
    for a, b in zip(slots, slots[1:]):
        assert a.top + a.height <= b.top
    assert slots[-1].top + slots[-1].height <= 410
    assert all(s.max_items == 3 for s in slots)


def test_an_overfull_week_caps_items_per_day_so_everything_fits():
    rows = _busy(agenda.day_rows([], MON, 7, today=MON), [5] * 7)
    slots = agenda.layout(rows, 410)
    assert slots[-1].top + slots[-1].height <= 410
    assert all(s.max_items >= 1 for s in slots)


FRI = MON + dt.timedelta(days=4)


def _titles(rows):
    return [[i.title for i in r.items] for r in rows]


def test_multi_day_all_day_event_shows_on_every_day_it_covers():
    ev = Event(start=None, end=None, title="Grandparents", all_day=True,
               date=FRI, end_date=FRI + dt.timedelta(days=2))
    rows = agenda.day_rows([ev], MON, 7, today=MON)
    assert _titles(rows) == [[], [], [], [], ["Grandparents"], ["Grandparents"],
                             ["Grandparents"]]


def test_timed_multi_day_event_shows_its_time_once_then_reads_all_day():
    start = dt.datetime.combine(FRI, dt.time(17))
    ev = Event(start=start, end=start + dt.timedelta(hours=46), title="Camping",
               end_date=FRI + dt.timedelta(days=2))
    rows = agenda.day_rows([ev], MON, 7, today=MON)
    fri, sat, sun = rows[4].items[0], rows[5].items[0], rows[6].items[0]
    assert (fri.time_label, fri.all_day) == ("5:00p", False)
    assert (sat.time_label, sat.all_day) == (None, True)
    assert (sun.time_label, sun.all_day) == (None, True)


def test_event_that_began_before_the_window_shows_on_the_days_it_still_covers():
    ev = Event(start=None, end=None, title="Trip", all_day=True,
               date=MON - dt.timedelta(days=3), end_date=MON + dt.timedelta(days=1))
    rows = agenda.day_rows([ev], MON, 7, today=MON)
    assert _titles(rows)[:3] == [["Trip"], ["Trip"], []]


def test_event_running_past_the_window_is_clipped():
    ev = Event(start=None, end=None, title="Break", all_day=True,
               date=MON + dt.timedelta(days=5), end_date=MON + dt.timedelta(days=12))
    rows = agenda.day_rows([ev], MON, 7, today=MON)
    assert len(rows) == 7
    assert _titles(rows)[5:] == [["Break"], ["Break"]]


def test_continuation_day_sorts_ahead_of_that_days_timed_events():
    start = dt.datetime.combine(FRI, dt.time(17))
    trip = Event(start=start, end=start + dt.timedelta(days=1), title="Trip",
                 end_date=FRI + dt.timedelta(days=1))
    rows = agenda.day_rows([_ev(FRI + dt.timedelta(days=1), 9, "Soccer"), trip],
                           MON, 7, today=MON)
    assert _titles(rows)[5] == ["Trip", "Soccer"]


def test_end_date_none_means_one_day():
    rows = agenda.day_rows([_ev(MON, 9, "Soccer")], MON, 7, today=MON)
    assert _titles(rows)[:2] == [["Soccer"], []]
