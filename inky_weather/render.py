"""Pillow rendering for the Inky Impression weather display."""
import os
from PIL import Image, ImageDraw, ImageFont
from . import weather

WIDTH = 800
HEIGHT = 480

# Palette-friendly RGB colors (quantized to the panel by the inky library).
BLACK = (0, 0, 0)
WHITE = (255, 255, 255)
PAPER = (255, 255, 255)      # background
RED = (200, 30, 30)
BLUE = (30, 70, 200)
GREEN = (30, 140, 60)
YELLOW = (230, 190, 0)
ORANGE = (230, 120, 0)

# Semantic mapping. Storm defaults to RED so it reads on 6-color panels.
COLOR_RAIN = BLUE
COLOR_STORM = RED
COLOR_SNOW = GREEN          # distinct from rain on a limited palette
COLOR_MIX = GREEN
COLOR_DRY = (210, 210, 210)

ICON_SZ_HOUR = 28

# --- new redesign palette + font (additive) ---
INK = (20, 22, 28)
PURPLE = (150, 40, 140)
GRAY = (120, 122, 130)
FAINT = (225, 226, 230)
# Gridlines must survive the panel's 7-color quantization: a near-white gray snaps to
# white and vanishes on the hardware. Use ink and get "faint" from dot coverage instead.
GRIDLINE = INK
GRIDLINE_STEP = 3

ACCENTS = {"red": RED, "orange": ORANGE, "blue": BLUE, "green": GREEN,
           "purple": PURPLE, "ink": INK, "gray": GRAY}

_BADGE_GLYPH = {"HIGH CONFIDENCE": "◆", "MIXED CONFIDENCE": "◈", "LOW AGREEMENT": "◇"}

_FONT_PATH = os.path.join(os.path.dirname(__file__), "assets", "fonts", "Oswald.ttf")


def display_font(size, weight=600):
    """Bundled Oswald at the given size and weight (300 or 600)."""
    f = ImageFont.truetype(_FONT_PATH, int(size))
    try:
        f.set_variation_by_axes([weight])
    except Exception:
        pass
    return f


def temp_color(temp_f):
    if temp_f >= 80:
        return RED
    if temp_f >= 72:
        return ORANGE
    if temp_f >= 55:
        return INK
    return BLUE


# Layout constants for banner cards
BANNER_Y = 50
BANNER_H = 104
HEADER_RULE_Y = 40


def _ctext(d, t, cx, cy, font, fill, anchor="mm"):
    """Draw text with anchor point."""
    d.text((cx, cy), t, font=font, fill=fill, anchor=anchor)


def _dotted_line(d, x0, x1, y, fill, step=GRIDLINE_STEP):
    """Horizontal dotted rule: one pixel every `step`, so it reads faint on the panel."""
    y = int(round(y))
    for x in range(int(round(x0)), int(round(x1)) + 1, step):
        d.point((x, y), fill=fill)


# Graph layout constants for ensemble display
GRAPH_Y = BANNER_Y + BANNER_H + 2
GRAPH_H = HEIGHT - GRAPH_Y - 8
BAND_OUT = (213, 220, 240)
BAND_IN = (178, 190, 224)

_KIND_BAR = {"storm": RED, "snow": COLOR_SNOW, "mix": COLOR_MIX, "rain": BLUE, "dry": BLUE}

# Precip probability buckets (lower bound of each non-dry tier), NWS-style:
# dry <5, slight 5-24, chance 25-54, likely 55-79, definite 80+.
_PRECIP_TIERS = (5, 25, 55, 80)


def _precip_tier(pop):
    """Map pop% to a bucket: 0=dry, 1=slight, 2=chance, 3=likely, 4=definite."""
    return sum(pop >= t for t in _PRECIP_TIERS)


def _faint_rect(d, x0, y0, x1, y1, step=GRIDLINE_STEP):
    """Panel-safe faint rectangle outline: stipple the perimeter with INK dots.

    A near-white gray would snap to white and vanish on the 6-color panel, so
    'faint' comes from dot coverage instead — the same trick the gridlines use.
    """
    x0, y0, x1, y1 = (int(round(v)) for v in (x0, y0, x1, y1))
    for x in range(x0, x1 + 1, step):
        d.point((x, y0), fill=INK)
        d.point((x, y1), fill=INK)
    for y in range(y0, y1 + 1, step):
        d.point((x0, y), fill=INK)
        d.point((x1, y), fill=INK)


def _draw_precip_meter(d, cx, pbase, sc, half, tier, color):
    """Grounded 4-segment precip meter centered at cx.

    The bottom `tier` segments (0-4) are filled solid in `color`; the remaining
    segments are drawn as faint outlines so the meter reads as "N out of 4".
    """
    seg_n = 4
    gap = 3
    seg_h = (sc - gap * (seg_n - 1)) / seg_n
    for s in range(seg_n):
        sb = pbase - s * (seg_h + gap)
        st = sb - seg_h
        box = [cx - half, st, cx + half, sb]
        if s < tier:
            d.rounded_rectangle(box, radius=2, fill=color)
        else:
            _faint_rect(d, cx - half, st, cx + half, sb)


def _updated_label(updated_str, version=None):
    """The header's updated-time line, optionally suffixed with the running version."""
    base = updated_str + " · NEXT 12H"
    return base + " · " + version if version else base


def draw_header(draw, location, date_str, updated_str, badge, version=None):
    """Draw the header band with location, date, confidence badge, and updated time."""
    _ctext(draw, (location or "").upper(), 20, 20, display_font(26, 600), INK, anchor="lm")
    w = draw.textlength((location or "").upper(), font=display_font(26, 600))
    _ctext(draw, date_str, 28 + w, 22, display_font(15, 600), INK, anchor="lm")
    if badge:
        label, accent = badge
        glyph = _BADGE_GLYPH.get(label, "◇")
        _ctext(draw, glyph + " " + label, WIDTH - 20, 15, display_font(12, 600),
               ACCENTS.get(accent, INK), anchor="rm")
    _ctext(draw, _updated_label(updated_str, version), WIDTH - 20, 30, display_font(12, 600), INK, anchor="rm")
    draw.line([20, HEADER_RULE_Y, WIDTH - 20, HEADER_RULE_Y], fill=INK, width=2)


def _draw_card(draw, x, y, w, h, card):
    """Draw a single card with category, verdict, and detail."""
    accent = ACCENTS.get(card["accent"], INK)
    _ctext(draw, card["cat"], x + 14, y + 15, display_font(13, 600), accent, anchor="lm")
    vf = display_font(30 if len(card["verdict"]) <= 15 else 26, 600)
    _ctext(draw, card["verdict"], x + 14, y + 45, vf, INK, anchor="lm")
    _ctext(draw, card["detail"], x + 14, y + h - 13, display_font(14, 600), INK, anchor="lm")


def draw_banner(draw, cards):
    """Draw 2-3 cards across the banner."""
    cw = (WIDTH - 40) / 3
    for i, card in enumerate(cards):
        x = 20 + i * cw
        _draw_card(draw, x, BANNER_Y, cw, BANNER_H, card)
        if i > 0:
            draw.line([x, BANNER_Y + 8, x, BANNER_Y + BANNER_H - 8], fill=INK, width=1)
    draw.line([20, BANNER_Y + BANNER_H, WIDTH - 20, BANNER_Y + BANNER_H], fill=INK, width=1)


def draw_graph(img, draw, hours, bands, icons, gx, gy, gw, gh):
    n = len(hours)
    temps = [h["temp_f"] for h in hours]
    has_band = len(bands) == n and n > 0
    lows = [b[0] for b in bands] if has_band else temps
    highs = [b[3] for b in bands] if has_band else temps
    mn, mx = min(lows), max(highs)
    rng = (mx - mn) or 1
    bandh = 82
    axis_y = gy + gh - 16
    top = gy + 40
    plot_h = (axis_y - bandh) - top
    pad_top = 24        # headroom so the highest point's label clears the icon row
    lx = gx + 30
    xs = [lx + (gw - 32) * (i + 0.5) / n for i in range(n)]

    def Y(t):
        return (top + pad_top) + (plot_h - pad_top) * (1 - (t - mn) / rng)

    # temp gridlines + labels
    lo10 = int((mn // 10) * 10)
    hi10 = int((mx // 10 + 1) * 10)
    step = 10 if (hi10 - lo10) >= 20 else 5
    for g in range(lo10, hi10 + 1, step):
        if g < mn - 2 or g > mx + 2:
            continue
        gyv = Y(g)
        _dotted_line(draw, lx, gx + gw, gyv, GRIDLINE)
        _ctext(draw, "{}°".format(g), gx + 2, gyv, display_font(12, 600), INK, anchor="lm")

    # nested ensemble band
    if has_band:
        def poly(los, his):
            return list(zip(xs, [Y(v) for v in his])) + list(zip(reversed(xs), [Y(v) for v in reversed(los)]))
        draw.polygon(poly([b[0] for b in bands], [b[3] for b in bands]), fill=BAND_OUT)
        draw.polygon(poly([b[1] for b in bands], [b[2] for b in bands]), fill=BAND_IN)

    # precip strip (grounded 4-bucket meter, colored by kind)
    pbase = axis_y
    sc = bandh - 14
    wet = any(h["pop"] >= 5 for h in hours)
    if wet:
        half = min((gw - 32) / n * 0.32, 16)
        for i, h in enumerate(hours):
            t = _precip_tier(h["pop"])
            if t == 0:
                continue
            kind = weather.precip_kind(h["pop"], h["precip_type"], h["thunder"])
            col = _KIND_BAR.get(kind, BLUE)
            _draw_precip_meter(draw, xs[i], pbase, sc, half, t, col)
        _ctext(draw, "RAIN", gx + 2, pbase - bandh + 2, display_font(10, 600),
               INK, anchor="lm")

    # temp line + points + labels + icons
    ys = [Y(t) for t in temps]
    draw.line(list(zip(xs, ys)), fill=INK, width=3, joint="curve")
    for i, h in enumerate(hours):
        x, y = xs[i], ys[i]
        draw.ellipse([x - 3, y - 3, x + 3, y + 3], fill=INK)
        _ctext(draw, "{}°".format(h["temp_f"]), x, y - 14, display_font(19, 600), temp_color(h["temp_f"]))
        if icons[i] is not None:
            img.paste(icons[i], (int(x - 14), int(top - 32)), icons[i])

    # x axis
    draw.line([lx, axis_y, gx + gw, axis_y], fill=INK, width=1)
    for i, h in enumerate(hours):
        _ctext(draw, h["ampm_label"], xs[i], axis_y + 8, display_font(12, 600), INK)


def render_display(hours, bands, hour_icons, cards, badge,
                   location_name, date_str, updated_str, version=None):
    """Compose the full 800x480 image. Returns an RGB PIL Image."""
    img = Image.new("RGB", (WIDTH, HEIGHT), PAPER)
    draw = ImageDraw.Draw(img)
    draw_graph(img, draw, hours, bands, hour_icons, 14, GRAPH_Y, WIDTH - 28, GRAPH_H)
    draw_banner(draw, cards)
    draw_header(draw, location_name, date_str, updated_str, badge, version)
    return img


def render_error(message):
    img = Image.new("RGB", (WIDTH, HEIGHT), PAPER)
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 0, WIDTH, 30], fill=RED)
    draw.text((10, 7), "Weather update failed", font=display_font(15, 600), fill=WHITE)
    _ctext(draw, message, WIDTH / 2, HEIGHT / 2, display_font(18, 600), INK)
    _ctext(draw, "Will retry next hour", WIDTH / 2, HEIGHT / 2 + 34, display_font(14, 600), INK)
    return img


def stamp_stale(img):
    """Draw a small 'STALE' pill in the top-right corner; return the image.

    The cached render already shows its original update time in the header, so
    the pill only has to flag that the data is old, not when it was fetched.
    """
    draw = ImageDraw.Draw(img)
    font = display_font(14, 600)
    label = "STALE"
    pad_x, pad_y, margin = 8, 4, 6
    left, top, right, bottom = draw.textbbox((0, 0), label, font=font)
    pw = (right - left) + 2 * pad_x
    ph = (bottom - top) + 2 * pad_y
    x0 = WIDTH - margin - pw
    y0 = HEADER_RULE_Y + 4
    draw.rounded_rectangle([x0, y0, x0 + pw, y0 + ph], radius=ph // 2, fill=RED)
    _ctext(draw, label, x0 + pw / 2, y0 + ph / 2, font, WHITE)
    return img
