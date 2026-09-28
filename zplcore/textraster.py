"""
Rasterising label text for the canvas.

Drawn with the same library that measures it. TextElement.printed_width() asks
PIL for the advance width, so drawing with anything else would let the glyphs
and the box that claims to contain them disagree - and the box is what the
printer honours. Both frontends blit the result; only the wrapping into a
toolkit image differs.
"""

import unicodedata

from PIL import (Image as PILImage, ImageDraw as PILImageDraw,
                 ImageFont as PILImageFont)

from . import fonts as zpl_fonts

# One entry per string at one size. A label has a handful of text elements, so
# this only has to survive editing one of them; it exists because the whole
# string is drawn rather than a truncated preview of it, and a drag can repaint
# many times a second.
_CACHE_LIMIT = 64
_cache = {}

# Padding around the glyphs, so an overhanging edge is not clipped
MARGIN = 2


def open_face(font_path: str, font_height):
    """The face at the size that draws text font_height dots tall - the
    height itself, or for font 0's stand-in the size that gives it font 0's
    cap height (fonts.em_size). Every face opened to measure or draw a field
    comes from here, so the box and the glyphs cannot be sized apart."""
    size = zpl_fonts.em_size(font_path, font_height)
    try:
        return PILImageFont.truetype(font_path, size)
    except TypeError:
        # Pillow before 10.1 opens a face at a whole size only
        return PILImageFont.truetype(font_path, max(1, int(round(size))))


def advance(draw, text, font) -> float:
    """The advance width of `text` in `font`, less its line breaks.

    A line break in ^FD is not a character: the printer discards it - the
    manual says so of a block's, where only \\& breaks a line - and the
    preview never sees one, since it reads a file a line at a time. The
    parser keeps it, so a field written ^FDtext on one line and ^FS on the
    next ends in one, and PIL will not measure a string that has one.
    """
    return draw.textlength((text or "").replace('\r', '').replace('\n', ''),
                           font=font)


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
        font = open_face(font_path, height)
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


# --- field direction (^FP) -------------------------------------------------

def clusters(text):
    """The characters ^FP places one at a time.

    Each is a base character with the combining marks that follow it - what
    the manual calls combining semantic clusters. Placing a code point at a
    time would give an accent a cell of its own, a row below the letter it
    belongs on.
    """
    pieces = []
    for char in text or "":
        if pieces and unicodedata.combining(char):
            pieces[-1] += char
        else:
            pieces.append(char)
    return pieces


def layout(text, measure, direction='H', gap=0, font_height=0):
    """Where ^FP puts each character of a one-line field.

    Returns (places, size, ends): `places` is (character, x, y) in dots in the
    field's own upright frame, `size` that frame's (width, height), and `ends`
    the advances of the first and last characters, which is what a reversed
    field's origin is measured by (geometry.field_anchor).

    H runs the characters left to right with `gap` extra dots between them;
    R runs the same row right to left, the first character at the right end;
    V stacks them top to bottom, one to a row of font_height + gap, each
    centred on a column as wide as the widest. Arithmetic over `measure`
    alone, so the fixed-width estimate lays out the same way a real face does
    and every drawing path reads the one answer - as placements() is for a
    block.
    """
    pieces = clusters(text)
    advances = [measure(piece) for piece in pieces]
    gap = max(0, int(gap or 0))
    height = max(1, int(font_height))
    ends = ((int(round(advances[0])), int(round(advances[-1])))
            if pieces else (0, 0))

    if direction == 'V':
        column = max(advances, default=0.0)
        places = [(piece, (column - advance) / 2, row * (height + gap))
                  for row, (piece, advance) in enumerate(zip(pieces, advances))]
        rows = max(1, len(pieces))
        return (places, (max(1, int(round(column))),
                         rows * height + (rows - 1) * gap), ends)

    places = []
    x = 0.0
    for piece, advance in zip(pieces, advances):
        places.append((piece, x, 0))
        x += advance + gap
    run = x - gap if pieces else 0.0
    if direction == 'R':
        places = [(piece, run - px - advance, 0)
                  for (piece, px, _y), advance in zip(places, advances)]
    return places, (max(1, int(round(run))), height), ends


def raster_directed(text, font_path, font_height, font_width, direction,
                    gap, ink=(0, 0, 0, 255), measure=None):
    """A ^FP field as a PIL RGBA image, already at its printed size, or None.

    Like raster_block(), nothing further is scaled by the caller: the image's
    top-left is the field's frame's. Each character is drawn at em size,
    squeezed, and centred in the cell layout() gave it - so the glyphs cannot
    drift from the box the field was sized by.
    Every row shares the one baseline raster() would give the whole line, so
    a lower-case letter does not float up to the top of its cell.

    `measure` is the metrics to lay out by when they are not this face's
    own: the preview draws a field with no face at all in DejaVu Sans, but
    has to place its characters where the fixed-width estimate the canvas
    sized its box by puts them.
    """
    height = max(1, int(font_height))
    try:
        font = open_face(font_path, height)
    except Exception:
        return None
    if measure is None:
        measure, _font = measurer(font_path, font_height, font_width, gap)
    places, (width, tall), _ends = layout(text, measure, direction, gap,
                                          font_height)
    probe = PILImageDraw.Draw(PILImage.new('RGBA', (1, 1)))
    try:
        top = font.getbbox(text or " ", anchor='ls')[1]
    except Exception:
        top = -height
    baseline = MARGIN - top
    # Room below the last row for its descenders, and to the right for an
    # overhanging last glyph, as raster() pads its own
    image = PILImage.new('RGBA', (width + 2 * MARGIN, tall + height // 2),
                         (0, 0, 0, 0))
    # One squeeze for every character, as the whole-string raster has: laid
    # out by the face's own metrics it is font_width / font_height and each
    # glyph fills its cell exactly; laid out by the fixed-width estimate a
    # narrow glyph is centred in its cell rather than stretched to fill it.
    naturals = [advance(probe, piece, font) for piece, _x, _y in places]
    cells = [measure(piece) for piece, _x, _y in places]
    squeeze = (sum(cells) / sum(naturals) if sum(naturals) > 0
               else max(1, int(font_width)) / height)
    for (piece, x, y), natural, cell in zip(places, naturals, cells):
        x += (cell - natural * squeeze) / 2
        box = font.getbbox(piece, anchor='ls')
        glyph = PILImage.new('RGBA', (max(1, max(box[2], int(natural)))
                                      + 2 * MARGIN,
                                      baseline + max(0, box[3]) + MARGIN),
                             (0, 0, 0, 0))
        PILImageDraw.Draw(glyph).text((MARGIN, baseline), piece, fill=ink,
                                      font=font, anchor='ls')
        if abs(squeeze - 1.0) > 1e-6:
            glyph = glyph.resize((max(1, int(round(glyph.width * squeeze))),
                                  glyph.height), PILImage.LANCZOS)
        # The glyph's own margin sits left of where the character starts,
        # and the first character starts at the frame's edge: what would
        # land left of the image is only that blank margin, so it is cut.
        left = int(round(x - MARGIN * squeeze))
        image.alpha_composite(glyph, (max(0, left), int(round(y))),
                              (max(0, -left), 0))
    return image


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


def measurer(font_path, font_height, font_width, gap=0):
    """Advance width of a string in printed dots, however little is known.

    The printer scales the em square to font_width x font_height, so a width
    measured at font_height scales by font_width / font_height - the same rule
    TextElement.printed_width() uses, so a wrap and the box that holds it
    cannot disagree. With no font file there are no metrics, and the built-in
    fixed-width estimate is all there is.

    `gap` is ^FP's extra dots between characters. With one, the string is
    measured a character at a time, as the printer places it - so a single
    character measures the same with a gap as without.
    """
    height = max(1, int(font_height))
    gap = max(0, int(gap or 0))
    font = None
    if font_path:
        try:
            font = open_face(font_path, height)
        except Exception:
            font = None
    if font is None:
        if not gap:
            return lambda text: len(text) * max(1, int(font_width)), None

        def fixed(text):
            gaps = max(0, len(clusters(text)) - 1)
            return len(text or "") * max(1, int(font_width)) + gap * gaps
        return fixed, None

    draw = PILImageDraw.Draw(PILImage.new('RGBA', (1, 1)))
    scale = max(1, int(font_width)) / height

    def measure(text):
        if not gap:
            return advance(draw, text, font) * scale
        pieces = clusters(text)
        return (sum(advance(draw, piece, font) for piece in pieces) * scale
                + gap * max(0, len(pieces) - 1))

    return measure, font


# Where a baseline sits inside a character cell when there is no font file to
# measure it from: about four fifths of the way down, which is where the faces
# that can be measured come out.
BASELINE_RATIO = 0.8


def baseline_offset(font_path, font_height) -> int:
    """Dots from the top of a character cell down to the baseline.

    ^FT names the baseline where ^FO names the top, so converting one into the
    other needs this. Asked of the same library that measures the advance, so
    the glyphs and the origin that places them cannot disagree - except for
    font 0's stand-in, whose own ascent is not where font 0's baseline is:
    that one is Table 33's (fonts.resident_baseline).
    """
    height = max(1, int(font_height))
    resident = zpl_fonts.resident_baseline(font_path, height)
    if resident is not None:
        return resident
    if font_path:
        try:
            ascent, descent = open_face(font_path, height).getmetrics()
            if ascent + descent > 0:
                return int(round(height * ascent / (ascent + descent)))
        except Exception:
            pass
    return int(round(height * BASELINE_RATIO))


def wrap_marked(text, font_path, font_height, font_width, block, gap=0,
                measure=None):
    """(line, ends_a_paragraph) for each line `text` breaks into in `block`.

    Greedy, like the printer: words are added until the next one would not
    fit. A single word too long for the block is left on its own line rather
    than being split, and lines past max_lines are dropped - the printer
    discards them too, instead of overflowing the block.

    The flag is what justification needs: a line that ends a paragraph is
    short because the text ran out, not because the next word would not fit,
    so stretching it to both edges would be wrong.

    `gap` is ^FP's, which widens every line by its characters' gaps and so
    wraps it sooner. `measure` is the metrics to wrap by when they are not
    the face's own - see raster_directed().
    """
    if measure is None:
        measure, _font = measurer(font_path, font_height, font_width, gap)
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


def wrap(text, font_path, font_height, font_width, block, gap=0,
         measure=None):
    """The lines `text` breaks into inside `block`."""
    return [line for line, _last in
            wrap_marked(text, font_path, font_height, font_width, block, gap,
                        measure)]


def block_size(text, font_path, font_height, font_width, block, gap=0):
    """The dots a wrapped block occupies: the block's width by its lines."""
    lines = wrap(text, font_path, font_height, font_width, block, gap)
    return block.width, max(1, len(lines) * pitch(font_height, block))


def pitch(font_height, block) -> int:
    """Dots from one baseline to the next inside a block."""
    return max(1, int(font_height) + block.line_spacing)


def raster_block(text, font_path, font_height, font_width, block,
                 ink=(0, 0, 0, 255), gap=0, measure=None):
    """A wrapped block as a PIL RGBA image, already at its printed size.

    Unlike raster(), nothing further is scaled by the caller: the block width
    is in final dots, so the horizontal squeeze from font_width is applied
    here, per line. With ^FP's gap each piece is laid out a character at a
    time instead, since squeezing a whole piece to its gapped width would
    widen the glyphs rather than the spaces between them. `measure`, as for
    raster_directed(), lays the block out by other metrics than the face's.
    """
    own, font = measurer(font_path, font_height, font_width, gap)
    if font is None:
        return None
    measure = measure or own

    marked = wrap_marked(text, font_path, font_height, font_width, block, gap,
                         measure)
    step = pitch(font_height, block)
    height = max(1, len(marked) * step)
    image = PILImage.new('RGBA', (max(1, block.width), height), (0, 0, 0, 0))

    for row, (line, last) in enumerate(marked):
        if not line:
            continue
        for piece, x in placements(line, measure, block, last):
            if gap:
                spaced = raster_directed(piece, font_path, font_height,
                                         font_width, 'H', gap, ink, measure)
                if spaced is not None:
                    image.alpha_composite(spaced, (x, row * step))
                continue
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
