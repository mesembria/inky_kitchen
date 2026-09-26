"""Day-per-row agenda, shared by the glance (this week) and A-long (next week).

One renderer, two call sites, so the two views cannot drift apart.
"""
import datetime as dt
from dataclasses import dataclass, field
from typing import Optional

from inky_weather import render as wr

# Colours this view introduces. Guarded by a quantization test: anything that
# snaps to white would be invisible on the panel.
AGENDA_COLORS = [wr.INK, wr.RED]

ROW_GAP = 4
DAY_COL_W = 78
TIME_GAP = 8
LINE_H = 23          # one event line
DAY_LABEL_H = 36     # day name + date sublabel
ROW_PAD = 8          # breathing room under each row
MAX_ITEMS = 3


@dataclass(frozen=True)
class Item:
    time_label: Optional[str]
    title: str
    all_day: bool = False


@dataclass
class DayRow:
    date: dt.date
    label: str
    sublabel: str
    is_today: bool
    is_weekend: bool
    items: list = field(default_factory=list)


def _time_label(when):
    """'9:00a' / '4:15p' — the panel is read at a glance, not parsed."""
    return when.strftime("%-I:%M%p").lower().replace("am", "a").replace("pm", "p")


def day_rows(events, start_date, days, today):
    """Group events into one row per day, starting at start_date.

    An event lands on every day from its first through its end_date. A timed
    event shows its start time on its first day only; on the days it runs on
    into, it reads as all-day, since "5:00p" on Saturday would be a lie.
    """
    rows = []
    for i in range(days):
        date = start_date + dt.timedelta(days=i)
        rows.append(DayRow(
            date=date,
            label=date.strftime("%a").upper(),
            sublabel="Today" if date == today else date.strftime("%b %-d"),
            is_today=date == today,
            is_weekend=date.weekday() >= 5,
        ))
    by_date = {r.date: r for r in rows}
    last_row = start_date + dt.timedelta(days=days - 1)

    placed = []
    for ev in events or []:
        first = ev.date if ev.all_day else (ev.start.date() if ev.start else None)
        if first is None:
            continue
        last = max(first, ev.end_date or first)
        day = max(first, start_date)
        while day <= min(last, last_row):
            all_day = ev.all_day or day != first
            # Sort on the real start time, never the display label: as
            # strings, '4:00p' sorts before '9:00a'. All-day items lead.
            key = (day, not all_day, dt.datetime.min if all_day else ev.start)
            placed.append((key, Item(
                time_label=None if all_day else _time_label(ev.start),
                title=ev.title or "",
                all_day=all_day)))
            day += dt.timedelta(days=1)

    for key, item in sorted(placed, key=lambda p: p[0]):
        by_date[key[0]].items.append(item)
    return rows


@dataclass(frozen=True)
class Slot:
    top: float
    height: float
    max_items: int


def layout(rows, h):
    """Give each day the height its events need, then share out the slack.

    A fixed height per row lets a busy day overprint the next one. If even the
    minimum doesn't fit, cap the events shown per day until it does.
    """
    for cap in range(MAX_ITEMS, 0, -1):
        need = [max(DAY_LABEL_H, min(len(r.items), cap) * LINE_H) + ROW_PAD
                for r in rows]
        if sum(need) <= h or cap == 1:
            break
    slack = max(0.0, h - sum(need)) / len(rows)
    slots, top = [], 0.0
    for n in need:
        height = min(n + slack, h - top)
        slots.append(Slot(top=top, height=height, max_items=cap))
        top += height
    return slots


def fit_text(text, font, max_px):
    """Truncate with an ellipsis so a long title never overflows its column."""
    if not text or font.getlength(text) <= max_px:
        return text
    out = text
    while out and font.getlength(out + "…") > max_px:
        out = out[:-1]
    return out + "…" if out else ""


def visible(items, cap):
    """(shown, hidden) for a space `cap` lines tall.

    When something has to be cut, one line is given up to a "+N more" marker
    so nothing vanishes silently. At one line there is no line to give, so
    the first item stays and the count rides beside it.
    """
    if len(items) <= cap:
        return list(items), 0
    keep = max(cap - 1, 1)
    return list(items[:keep]), len(items) - keep


def render(draw, x, y, w, h, rows):
    """Draw the agenda inside the box at (x, y, w, h)."""
    day_font = wr.display_font(19, 600)
    sub_font = wr.display_font(12, 300)
    time_font = wr.display_font(19, 600)
    item_font = wr.display_font(19, 400)
    quiet_font = wr.display_font(19, 300)

    text_x = x + DAY_COL_W + TIME_GAP
    text_w = w - DAY_COL_W - TIME_GAP

    for i, (row, slot) in enumerate(zip(rows, layout(rows, h))):
        top = y + slot.top
        if i:
            wr._dotted_line(draw, x, x + w, top - ROW_GAP / 2, wr.INK)
        day_color = wr.RED if row.is_weekend else wr.INK
        if row.is_today:
            draw.rectangle([x - 8, top, x - 5, top + slot.height - ROW_GAP], fill=wr.INK)
        draw.text((x, top), row.label, font=day_font, fill=day_color)
        draw.text((x, top + 20), row.sublabel, font=sub_font, fill=wr.INK)

        if not row.items:
            draw.text((text_x, top), "—", font=quiet_font, fill=wr.INK)
            continue

        shown, hidden = visible(row.items, slot.max_items)
        inline = f"+{hidden}" if hidden and slot.max_items == 1 else ""
        inline_w = quiet_font.getlength(inline) + 6 if inline else 0

        iy = top
        for item in shown:
            tx = text_x
            if item.all_day:
                tag = "ALL DAY"
                tag_font = wr.display_font(11, 600)
                tw = tag_font.getlength(tag) + 8
                draw.rectangle([tx, iy + 3, tx + tw, iy + 17], outline=wr.INK, width=2)
                draw.text((tx + 4, iy + 3), tag, font=tag_font, fill=wr.INK)
                tx += tw + 6
            elif item.time_label:
                draw.text((tx, iy), item.time_label, font=time_font, fill=wr.INK)
                tx += time_font.getlength(item.time_label) + 6
            avail = text_w - (tx - text_x) - inline_w
            draw.text((tx, iy), fit_text(item.title, item_font, avail),
                      font=item_font, fill=wr.INK)
            iy += LINE_H
        if inline:
            draw.text((x + w, top), inline, font=quiet_font, fill=wr.INK, anchor="ra")
        elif hidden:
            draw.text((text_x, iy), f"+{hidden} more", font=quiet_font, fill=wr.INK)
