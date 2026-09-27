"""The resting view: layout G — forecast rail on the left, week agenda on the right.

Any block whose data is None is dropped and the rest reflows.
"""
import os

from PIL import Image, ImageDraw

from inky_weather import icons
from inky_weather import render as wr
from inky_weather import weather

from . import agenda

GLANCE_COLORS = [wr.INK, wr.RED, wr.BLUE, wr.ORANGE, wr.GREEN]

ICON_CACHE = os.path.join(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))), "inky_weather", "assets", "icons")

RAIL_X = 18
RAIL_W = 232
VRULE_X = 268
AGENDA_X = 290
HEADER_H = 42
BODY_Y = 52
FORECAST_HOURS = 12
ROW_MAX_H = 34       # a short forecast shouldn't stretch the hours apart
MIN_SPAN_F = 10
BAR_STUB = 0.2       # the coldest hour's bar, as a share of the bar range
METER_SEG_W = 7
METER_GAP = 3
METER_X = RAIL_X + RAIL_W - 4 * METER_SEG_W - 3 * METER_GAP
BAR_X0 = RAIL_X + 62
BAR_X1 = METER_X - 44  # leaves room for a "100°" label before the meter


def hourly_slice(forecast, count=FORECAST_HOURS):
    """The next `count` forecast hours, or as many as exist."""
    if forecast is None or not forecast.hours:
        return []
    return list(forecast.hours[:count])


def _header(draw, ctx):
    draw.text((RAIL_X, 6), ctx.now.strftime("%a %b %-d").upper(),
              font=wr.display_font(26, 600), fill=wr.INK)
    meta = " · ".join(x for x in (
        ctx.location_name,
        "updated " + ctx.now.strftime("%-I:%M%p").lower().lstrip("0"),
        ctx.version) if x)
    draw.text((wr.WIDTH - RAIL_X, 14), meta, font=wr.display_font(14, 300),
              fill=wr.INK, anchor="ra")
    draw.line([RAIL_X, HEADER_H, wr.WIDTH - RAIL_X, HEADER_H], fill=wr.INK, width=2)


def temp_scale(temps, x0, x1, min_span=MIN_SPAN_F):
    """A degrees-to-x function: coldest of `temps` at x0, warmest at x1.

    The scale never spans fewer than `min_span` degrees, so on a flat day the
    bars stay nearly level instead of a 2° wobble filling the whole width.
    """
    lo, hi = min(temps), max(temps)
    pad = max(0, min_span - (hi - lo)) / 2
    lo, hi = lo - pad, hi + pad
    return lambda t: x0 + (x1 - x0) * (t - lo) / (hi - lo)


def bar_ends(temps, x0, x1):
    """Where each hour's temperature bar stops. The warmest reaches x1; the
    coldest keeps a stub, so no hour's bar vanishes."""
    x = temp_scale(temps, x0 + BAR_STUB * (x1 - x0), x1)
    return [x(t) for t in temps]


def _meter(draw, x, cy, half, h):
    """The weather view's four-step precip meter, laid on its side."""
    tier = wr._precip_tier(h.get("pop", 0))
    kind = weather.precip_kind(h.get("pop", 0), h.get("precip_type"),
                               h.get("thunder", 0))
    color = wr._KIND_BAR.get(kind, wr.BLUE)
    for s in range(4):
        sx = x + s * (METER_SEG_W + METER_GAP)
        box = [sx, cy - half, sx + METER_SEG_W, cy + half]
        if s < tier:
            draw.rounded_rectangle(box, radius=2, fill=color)
        else:
            wr._faint_rect(draw, *box)


def _forecast(img, draw, hours, y, bottom):
    """One row per hour: time, icon, a bar as long as it is warm, and the
    precip meter at the right edge."""
    n = len(hours)
    row_h = min(ROW_MAX_H, (bottom - y) / n)
    ends = bar_ends([h["temp_f"] for h in hours], BAR_X0, BAR_X1)
    icon_sz = min(26, int(row_h) - 4)
    bar_half = max(4, min(8, row_h / 2 - 7))
    label_font = wr.display_font(14, 600)
    temp_font = wr.display_font(18, 600)
    wet = any(h.get("pop", 0) >= 5 for h in hours)
    for i, h in enumerate(hours):
        cy = y + row_h * (i + 0.5)
        color = wr.temp_color(h["temp_f"])
        draw.text((RAIL_X, cy), h["ampm_label"], font=label_font, fill=wr.INK,
                  anchor="lm")
        icon = icons.get_icon(h.get("icon_uri", ""), icon_sz, ICON_CACHE)
        img.paste(icon, (RAIL_X + 30, int(cy - icon_sz / 2)), icon)
        draw.rectangle([BAR_X0, cy - bar_half, ends[i], cy + bar_half], fill=color)
        draw.text((ends[i] + 4, cy), f"{h['temp_f']}°", font=temp_font,
                  fill=color, anchor="lm")
        if wet:
            _meter(draw, METER_X, cy, bar_half - 1, h)


def _rail(img, draw, ctx):
    y = BODY_Y
    if ctx.forecast is not None and ctx.forecast.days:
        d0 = ctx.forecast.days[0]
        draw.text((RAIL_X, y), f"{d0['hi_f']}°", font=wr.display_font(22, 600),
                  fill=wr.temp_color(d0["hi_f"]))
        draw.text((RAIL_X + 42, y), f"/ {d0['lo_f']}°", font=wr.display_font(22, 600),
                  fill=wr.INK)
        sunset = (ctx.forecast.sun or {}).get("sunset")
        if sunset:
            draw.text((RAIL_X + 110, y + 4), "sunset " + sunset,
                      font=wr.display_font(13, 300), fill=wr.INK)
        y += 32

    hours = hourly_slice(ctx.forecast)
    if hours:
        wr._dotted_line(draw, RAIL_X, RAIL_X + RAIL_W, y, wr.INK)
        y += 4
        _forecast(img, draw, hours, y, wr.HEIGHT - 18)


def render(ctx):
    """Compose the 800x480 glance. Returns an RGB PIL Image."""
    img = Image.new("RGB", (wr.WIDTH, wr.HEIGHT), wr.PAPER)
    draw = ImageDraw.Draw(img)

    _header(draw, ctx)
    _rail(img, draw, ctx)
    draw.line([VRULE_X, BODY_Y, VRULE_X, wr.HEIGHT - 18], fill=wr.INK, width=2)

    agenda_w = wr.WIDTH - RAIL_X - AGENDA_X
    if ctx.events is None:
        draw.text((AGENDA_X, BODY_Y), "No calendar configured",
                  font=wr.display_font(19, 300), fill=wr.INK)
    else:
        today = ctx.now.date()
        rows = agenda.day_rows(ctx.events, today, 7, today=today)
        agenda.render(draw, AGENDA_X, BODY_Y, agenda_w,
                      wr.HEIGHT - BODY_Y - 18, rows)
    return img
