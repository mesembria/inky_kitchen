"""The resting view: layout G — weather rail on the left, week agenda on the right.

Any block whose data is None is dropped and the rest reflows, which is what
lets Core ship before the calendar and Home Assistant slices exist.
"""
import datetime as dt
import os

from PIL import Image, ImageDraw

from inky_weather import icons
from inky_weather import render as wr

from . import agenda

GLANCE_COLORS = [wr.INK, wr.RED, wr.BLUE, wr.ORANGE]

ICON_CACHE = os.path.join(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))), "inky_weather", "assets", "icons")

RAIL_X = 18
RAIL_W = 232
VRULE_X = 268
AGENDA_X = 290
HEADER_H = 42
BODY_Y = 52


def hourly_slice(forecast, count=6):
    """The next `count` forecast hours, or as many as exist."""
    if forecast is None or not forecast.hours:
        return []
    return list(forecast.hours[:count])


def dinner_lines(meals, today):
    """(tonight, next_label, next_meal).

    Tonight is None when nothing is planned — the rail says so in light weight
    and shows the next planned meal beneath, rather than hiding the block.
    """
    if not meals:
        return (None, None, None)
    tonight = meals.get(today)
    future = sorted(d for d in meals if d > today)
    if not future:
        return (tonight, None, None)
    nxt = future[0]
    label = "Tomorrow" if nxt == today + dt.timedelta(days=1) else nxt.strftime("%a")
    return (tonight, label, meals[nxt])


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


def _rail(img, draw, ctx):
    y = BODY_Y
    if ctx.now_wx is not None:
        draw.text((RAIL_X, y - 8), f"{ctx.now_wx.temp_f}°",
                  font=wr.display_font(88, 600), fill=wr.temp_color(ctx.now_wx.temp_f))
        icon = icons.get_icon(ctx.now_wx.icon_uri, 38, ICON_CACHE)
        img.paste(icon, (RAIL_X + 140, y + 4), icon)
        draw.text((RAIL_X + 140, y + 46), ctx.now_wx.condition.replace("_", " ").title(),
                  font=wr.display_font(16, 500), fill=wr.INK)
        draw.text((RAIL_X + 140, y + 64), f"feels {ctx.now_wx.feels_f}°",
                  font=wr.display_font(14, 300), fill=wr.INK)
        y += 96

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
        y += 8
        for h in hours:
            draw.text((RAIL_X, y), h["ampm_label"], font=wr.display_font(14, 300),
                      fill=wr.INK)
            icon = icons.get_icon(h.get("icon_uri", ""), 22, ICON_CACHE)
            img.paste(icon, (RAIL_X + 34, y), icon)
            draw.text((RAIL_X + 62, y), f"{h['temp_f']}°",
                      font=wr.display_font(17, 600), fill=wr.temp_color(h["temp_f"]))
            if h.get("pop"):
                draw.text((RAIL_X + 104, y + 2), f"{h['pop']}%",
                          font=wr.display_font(13, 600), fill=wr.BLUE)
            y += 24

    tonight, label, nxt = dinner_lines(ctx.meals, ctx.now.date())
    if ctx.meals is not None:
        wr._dotted_line(draw, RAIL_X, RAIL_X + RAIL_W, y + 4, wr.INK)
        y += 14
        draw.text((RAIL_X, y), "TONIGHT", font=wr.display_font(13, 600), fill=wr.RED)
        y += 16
        if tonight:
            draw.text((RAIL_X, y), agenda.fit_text(tonight, wr.display_font(19, 600),
                                                   RAIL_W),
                      font=wr.display_font(19, 600), fill=wr.INK)
        else:
            draw.text((RAIL_X, y), "Nothing planned",
                      font=wr.display_font(16, 300), fill=wr.INK)
        y += 24
        if nxt:
            draw.text((RAIL_X, y), label.upper(), font=wr.display_font(13, 600),
                      fill=wr.INK)
            draw.text((RAIL_X, y + 15),
                      agenda.fit_text(nxt, wr.display_font(17, 600), RAIL_W),
                      font=wr.display_font(17, 600), fill=wr.INK)


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
