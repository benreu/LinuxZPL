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


def raster(text: str, font_path: str, font_height: int, ink=(0, 0, 0, 255)):
    """The text as a PIL RGBA image with transparent background, or None.

    Cached by (text, font, height, ink) - not by the element - so two elements
    sharing a string, a face and an ink colour share the raster. `ink` is what
    a ^FR field draws with, white instead of black.
    """
    text = text or " "
    height = max(1, int(font_height))
    key = (text, font_path, height, ink)
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
                                  fill=ink, font=font)

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


def to_editor(text: str) -> str:
    """Field data as it appears in a multi-line text box.

    ZPL has no newline: a break inside ^FD is the two characters \\&, and only
    inside a ^FB does the printer act on them. Here and in from_editor() is the
    only place the two spellings meet, so neither dialog has to know the rule.
    """
    return (text or "").replace(FORCED_BREAK, "\n")


def from_editor(text: str) -> str:
    """A multi-line text box's contents as ZPL field data."""
    return (text or "").replace("\r\n", "\n").replace(
        "\r", "\n").replace("\n", FORCED_BREAK)


def join_lines(text: str) -> str:
    """The same text on one line, for when a block is switched off.

    Left in place, a forced break would print as the two characters it is
    written with, since nothing outside a ^FB reads it as a break.
    """
    return " ".join(part.strip() for part in
                    (text or "").split(FORCED_BREAK) if part.strip())


def measurer(font_path, font_height, font_width):
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


# Where a baseline sits inside a character cell when there is no font file to
# measure it from: about four fifths of the way down, which is where the faces
# that can be measured come out.
BASELINE_RATIO = 0.8


def baseline_offset(font_path, font_height) -> int:
    """Dots from the top of a character cell down to the baseline.

    ^FT names the baseline where ^FO names the top, so converting one into the
    other needs this. Asked of the same library that measures the advance, so
    the glyphs and the origin that places them cannot disagree.
    """
    height = max(1, int(font_height))
    if font_path:
        try:
            ascent, descent = PILImageFont.truetype(font_path, height).getmetrics()
            if ascent + descent > 0:
                return int(round(height * ascent / (ascent + descent)))
        except Exception:
            pass
    return int(round(height * BASELINE_RATIO))


def wrap_marked(text, font_path, font_height, font_width, block):
    """(line, ends_a_paragraph) for each line `text` breaks into in `block`.

    Greedy, like the printer: words are added until the next one would not
    fit. A single word too long for the block is left on its own line rather
    than being split, and lines past max_lines are dropped - the printer
    discards them too, instead of overflowing the block.

    The flag is what justification needs: a line that ends a paragraph is
    short because the text ran out, not because the next word would not fit,
    so stretching it to both edges would be wrong.
    """
    measure, _font = measurer(font_path, font_height, font_width)
    marked = []
    for paragraph in (text or "").split(FORCED_BREAK):
        words = paragraph.split()
        if not words:
            marked.append(("", True))
            continue
        current = words[0]
        for word in words[1:]:
            candidate = f"{current} {word}"
            if measure(candidate) <= block.width:
                current = candidate
            else:
                marked.append((current, False))
                current = word
        marked.append((current, True))

    kept = marked[:block.max_lines]
    if kept:
        # Whatever survives the truncation ends the text as printed, so it is
        # not stretched either.
        kept[-1] = (kept[-1][0], True)
    return kept


def wrap(text, font_path, font_height, font_width, block):
    """The lines `text` breaks into inside `block`."""
    return [line for line, _last in
            wrap_marked(text, font_path, font_height, font_width, block)]


def block_size(text, font_path, font_height, font_width, block):
    """The dots a wrapped block occupies: the block's width by its lines."""
    lines = wrap(text, font_path, font_height, font_width, block)
    return block.width, max(1, len(lines) * pitch(font_height, block))


def pitch(font_height, block) -> int:
    """Dots from one baseline to the next inside a block."""
    return max(1, int(font_height) + block.line_spacing)


def raster_block(text, font_path, font_height, font_width, block, ink=(0, 0, 0, 255)):
    """A wrapped block as a PIL RGBA image, already at its printed size.

    Unlike raster(), nothing further is scaled by the caller: the block width
    is in final dots, so the horizontal squeeze from font_width is applied
    here, per line.
    """
    measure, font = measurer(font_path, font_height, font_width)
    if font is None:
        return None

    marked = wrap_marked(text, font_path, font_height, font_width, block)
    step = pitch(font_height, block)
    height = max(1, len(marked) * step)
    image = PILImage.new('RGBA', (max(1, block.width), height), (0, 0, 0, 0))

    for row, (line, last) in enumerate(marked):
        if not line:
            continue
        for piece, x in placements(line, measure, block, last):
            drawn = raster(piece, font_path, max(1, int(font_height)), ink)
            if drawn is None:
                continue
            printed = max(1, int(round(measure(piece))))
            squeezed = drawn.resize(
                (max(1, int(round(printed + 2 * MARGIN * max(1, int(font_width))
                                  / max(1, int(font_height))))), drawn.height),
                PILImage.LANCZOS)
            image.alpha_composite(squeezed, (x, row * step))
    return image


def placements(line, measure, block, last):
    """(piece, x) pairs placing one line inside the block, x in dots.

    One piece for the ordinary justifications. For `J` the line is placed word
    by word, with the slack shared out among the gaps so it meets both edges -
    which is the whole of justified text, and cannot be expressed as a single
    starting x. The line that ends a paragraph is placed like a left-aligned
    one, as the printer leaves it.
    """
    width = int(round(measure(line)))
    if block.justification != 'J' or last:
        return [(line, _justified_x(width, block))]

    words = line.split()
    widths = [measure(word) for word in words]
    slack = block.width - sum(widths)
    if len(words) < 2 or slack < 0:
        return [(line, _justified_x(width, block))]

    gap = slack / (len(words) - 1)
    places = []
    x = 0.0
    for word, advance in zip(words, widths):
        places.append((word, int(round(x))))
        x += advance + gap
    return places


def _justified_x(line_width, block):
    """Where a line of `line_width` dots starts inside the block."""
    if block.justification == 'C':
        return max(0, (block.width - line_width) // 2)
    if block.justification == 'R':
        return max(0, block.width - line_width)
    return max(0, block.indent)
