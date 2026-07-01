"""Color scales for table cells (shared by Streamlit, HTML, and Excel).

Speed scale (anchored on the posted limit):
    speed <= limit-5            -> green
    limit-5 < speed <= limit    -> green fading to white (white at the limit)
    limit  < speed <= limit+5   -> white fading to yellow (yellow at +5)
    speed > limit+5             -> red

Count scale: white -> blue, normalized between a column's min and max.
"""
from __future__ import annotations

import math

GREEN = (99, 190, 123)     # #63BE7B
WHITE = (255, 255, 255)
YELLOW = (255, 214, 102)   # #FFD666
RED = (231, 92, 92)        # #E75C5C
BLUE = (49, 130, 189)      # #3182BD


def _lerp(c1, c2, t):
    t = max(0.0, min(1.0, t))
    return tuple(round(a + (b - a) * t) for a, b in zip(c1, c2))


def _hex(rgb):
    return "#%02X%02X%02X" % rgb


def _text_on(rgb):
    """Black or white text for readable contrast on a background color."""
    r, g, b = rgb
    lum = (0.299 * r + 0.587 * g + 0.114 * b) / 255
    return "#000000" if lum > 0.6 else "#FFFFFF"


def text_on_hex(hexstr):
    """Contrast text color for a #RRGGBB background."""
    rgb = tuple(int(hexstr[i:i + 2], 16) for i in (1, 3, 5))
    return _text_on(rgb)


def speed_rgb(speed, limit):
    """RGB tuple for a speed value relative to the limit (None if no value)."""
    if speed is None or (isinstance(speed, float) and math.isnan(speed)):
        return None
    x = float(speed) - float(limit)
    if x <= -5:
        return GREEN
    if x <= 0:
        return _lerp(GREEN, WHITE, (x + 5) / 5.0)   # white at the limit
    if x <= 5:
        return _lerp(WHITE, YELLOW, x / 5.0)        # yellow at +5
    return RED


def count_rgb(value, vmin, vmax):
    """RGB tuple white->blue for a count between vmin and vmax."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    rng = (vmax - vmin)
    t = (float(value) - vmin) / rng if rng > 0 else 0.0
    return _lerp(WHITE, BLUE, t)


# --- hex helpers (Excel / HTML) ------------------------------------------- #
def speed_hex(speed, limit):
    rgb = speed_rgb(speed, limit)
    return _hex(rgb) if rgb else None


def count_hex(value, vmin, vmax):
    rgb = count_rgb(value, vmin, vmax)
    return _hex(rgb) if rgb else None


# --- pandas Styler helpers (Streamlit / HTML) ----------------------------- #
def _css(rgb):
    if rgb is None:
        return ""
    return f"background-color: {_hex(rgb)}; color: {_text_on(rgb)}"


def style_speed(df, limit, subset=None):
    """Return a pandas Styler coloring numeric cells on the speed scale."""
    def _fn(v):
        try:
            return _css(speed_rgb(float(v), limit))
        except (TypeError, ValueError):
            return ""
    sty = df.style.map(_fn, subset=subset) if subset is not None else df.style.map(_fn)
    return sty


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return float("nan")


def style_hourly_table(table, limit, speed_col="Weekday 85th %ile"):
    """Style the hourly report table: speed scale on the 85th column, white->blue
    (normalized over the count columns) on the per-day counts and averages."""
    import pandas as pd
    count_cols = [c for c in table.columns if c != speed_col]
    nums = table[count_cols].apply(pd.to_numeric, errors="coerce") if count_cols else None
    vmin = float(nums.min().min()) if (nums is not None and nums.size) else 0.0
    vmax = float(nums.max().max()) if (nums is not None and nums.size) else 1.0

    def _spd(v):
        n = _num(v)
        return _css(speed_rgb(n, limit)) if not math.isnan(n) else ""

    def _cnt(v):
        n = _num(v)
        return _css(count_rgb(n, vmin, vmax)) if not math.isnan(n) else ""

    sty = table.style
    if speed_col in table.columns:
        sty = sty.map(_spd, subset=[speed_col])
    if count_cols:
        sty = sty.map(_cnt, subset=count_cols)
    return sty.format(precision=2)


def style_counts(df, subset=None):
    """Return a pandas Styler coloring numeric cells white->blue per the table range."""
    import pandas as pd
    block = df[subset] if subset is not None else df
    nums = block.apply(pd.to_numeric, errors="coerce")
    vmin = float(nums.min().min()) if nums.size else 0.0
    vmax = float(nums.max().max()) if nums.size else 1.0

    def _fn(v):
        try:
            return _css(count_rgb(float(v), vmin, vmax))
        except (TypeError, ValueError):
            return ""
    return df.style.map(_fn, subset=subset) if subset is not None else df.style.map(_fn)
