"""Next week's agenda, full width — the A-long-press view."""
import datetime as dt

from PIL import Image, ImageDraw

from inky_weather import render as wr

from . import agenda

MARGIN = 18
BODY_Y = 52


def start_date(today):
    """The Monday of the week after the one containing `today`."""
    return today + dt.timedelta(days=7 - today.weekday())


def render(ctx):
    img = Image.new("RGB", (wr.WIDTH, wr.HEIGHT), wr.PAPER)
    draw = ImageDraw.Draw(img)

    start = start_date(ctx.now.date())
    end = start + dt.timedelta(days=6)
    title = f"NEXT WEEK · {start.strftime('%b %-d')}–{end.strftime('%b %-d')}"
    draw.text((MARGIN, 6), title, font=wr.display_font(26, 600), fill=wr.INK)
    draw.text((wr.WIDTH - MARGIN, 14), ctx.version, font=wr.display_font(14, 300),
              fill=wr.INK, anchor="ra")
    draw.line([MARGIN, 42, wr.WIDTH - MARGIN, 42], fill=wr.INK, width=2)

    if ctx.events is None:
        draw.text((MARGIN, BODY_Y), "No calendar configured",
                  font=wr.display_font(19, 300), fill=wr.INK)
        return img

    rows = agenda.day_rows(ctx.events, start, 7, today=ctx.now.date())
    agenda.render(draw, MARGIN, BODY_Y, wr.WIDTH - 2 * MARGIN,
                  wr.HEIGHT - BODY_Y - 18, rows)
    return img
