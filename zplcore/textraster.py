"""
Rasterising label text for the canvas.

Drawn with the same library that measures it. TextElement.printed_width() asks
PIL for the advance width, so drawing with anything else would let the glyphs
and the box that claims to contain them disagree - and the box is what the
printer honours. Both frontends blit the result; only the wrapping into a
toolkit image differs.
"""

from PIL import (Image as PILImage, ImageDraw as PILImageDraw,
                 ImageFont as PILImageFont)

# One entry per string at one size. A label has a handful of text elements, so
# this only has to survive editing one of them; it exists because the whole
# string is drawn rather than a truncated preview of it, and a drag can repaint
# many times a second.
_CACHE_LIMIT = 64
_cache = {}

# Padding around the glyphs, so an overhanging edge is not clipped
MARGIN = 2


def raster(text: str, font_path: str, font_height: int):
    """The text as a PIL RGBA image with transparent background, or None.

    Cached by (text, font, height) - not by the element - so two elements
    sharing a string and a face share the raster.
    """
    text = text or " "
    height = max(1, int(font_height))
    key = (text, font_path, height)
    hit = _cache.get(key)
    if hit is not None:
        return hit

    try:
        font = PILImageFont.truetype(font_path, height)
    except Exception:
        return None

    bbox = PILImageDraw.Draw(PILImage.new('RGBA', (1, 1))).textbbox(
        (0, 0), text, font=font)
    width = max(1, bbox[2] - bbox[0])
    tall = max(1, bbox[3] - bbox[1])

    image = PILImage.new('RGBA', (width + 2 * MARGIN, tall + 2 * MARGIN), (0, 0, 0, 0))
    PILImageDraw.Draw(image).text((MARGIN - bbox[0], MARGIN - bbox[1]), text,
                                  fill=(0, 0, 0, 255), font=font)

    if len(_cache) >= _CACHE_LIMIT:
        _cache.clear()
    _cache[key] = image
    return image


def clear_cache():
    """Drop every cached raster. Used by the tests."""
    _cache.clear()


# --- field blocks (^FB) -----------------------------------------------------

# A forced line break inside ^FD, which is how ZPL splits a block by hand
FORCED_BREAK = '\\&'


def _measurer(font_path, font_height, font_width):
    """Advance width of a string in printed dots, however little is known.

    The printer scales the em square to font_width x font_height, so a width
    measured at font_height scales by font_width / font_height - the same rule
    TextElement.printed_width() uses, so a wrap and the box that holds it
    cannot disagree. With no font file there are no metrics, and the built-in
    fixed-width estimate is all there is.
    """
    height = max(1, int(font_height))
    font = None
    if font_path:
        try:
            font = PILImageFont.truetype(font_path, height)
        except Exception:
            font = None
    if font is None:
        return lambda text: len(text) * max(1, int(font_width)), None

    draw = PILImageDraw.Draw(PILImage.new('RGBA', (1, 1)))
    scale = max(1, int(font_width)) / height

    def measure(text):
        return draw.textlength(text or "", font=font) * scale

    return measure, font


def wrap(text, font_path, font_height, font_width, block):
    """The lines `text` breaks into inside `block`.

    Greedy, like the printer: words are added until the next one would not
    fit. A single word too long for the block is left on its own line rather
    than being split, and lines past max_lines are dropped - the printer
    discards them too, instead of overflowing the block.
    """
    measure, _font = _measurer(font_path, font_height, font_width)
    lines = []
    for paragraph in (text or "").split(FORCED_BREAK):
        words = paragraph.split()
        if not words:
            lines.append("")
            continue
        current = words[0]
        for word in words[1:]:
            candidate = f"{current} {word}"
            if measure(candidate) <= block.width:
                current = candidate
            else:
                lines.append(current)
                current = word
        lines.append(current)
    return lines[:block.max_lines]


def block_size(text, font_path, font_height, font_width, block):
    """The dots a wrapped block occupies: the block's width by its lines."""
    lines = wrap(text, font_path, font_height, font_width, block)
    pitch = max(1, int(font_height) + block.line_spacing)
    return block.width, max(1, len(lines) * pitch)


def raster_block(text, font_path, font_height, font_width, block):
    """A wrapped block as a PIL RGBA image, already at its printed size.

    Unlike raster(), nothing further is scaled by the caller: the block width
    is in final dots, so the horizontal squeeze from font_width is applied
    here, per line.
    """
    measure, font = _measurer(font_path, font_height, font_width)
    if font is None:
        return None

    lines = wrap(text, font_path, font_height, font_width, block)
    pitch = max(1, int(font_height) + block.line_spacing)
    height = max(1, len(lines) * pitch)
    image = PILImage.new('RGBA', (max(1, block.width), height), (0, 0, 0, 0))

    for row, line in enumerate(lines):
        if not line:
            continue
        drawn = raster(line, font_path, max(1, int(font_height)))
        if drawn is None:
            continue
        printed = max(1, int(round(measure(line))))
        squeezed = drawn.resize(
            (max(1, int(round(printed + 2 * MARGIN * max(1, int(font_width))
                              / max(1, int(font_height))))), drawn.height),
            PILImage.LANCZOS)
        image.alpha_composite(squeezed, (_justified_x(printed, block), row * pitch))
    return image


def _justified_x(line_width, block):
    """Where a line of `line_width` dots starts inside the block."""
    if block.justification == 'C':
        return max(0, (block.width - line_width) // 2)
    if block.justification == 'R':
        return max(0, block.width - line_width)
    return max(0, block.indent)
