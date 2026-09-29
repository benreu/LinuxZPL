"""
^GS - the graphic symbol font, drawn.

^GS prints from the printer's resident GS font, and which glyph is chosen by
the field data: A through E (manual, ^GS). There is no file for it on this
machine - fonts.RESIDENT_FONTS can only name it - so the five glyphs are
drawn here.

What they are drawn in is what a 203 dpi printer printed at x1, x3 and x4:
GS is a bitmap font like A-H, a 24 x 24 cell magnified in whole steps, each
axis on its own (fonts.magnification), and each symbol starts 26 dots on from
the last - its cell and a 2 dot gap. The (R) and (C) are 15 across in the
cell's top left, the TM 19 x 10 there too, the UL mark the whole cell and the
CSA mark 22 of its 24 across. The strokes here are this designer's own, laid
out to fill those boxes; FUNCTIONAL_SPEC.md section 18 records that their
shapes are not the printer's bitmaps.

The preview and both canvases blit the one raster made here, the way they
share textraster's and geometry.barcode_rects', so none of them can draw a
different symbol.
"""

from PIL import Image as PILImage, ImageDraw as PILImageDraw

from . import fonts as zpl_fonts

# (letter, what it prints as, what it is called), in the manual's order. Both
# frontends build their menus and editors from this, so neither can offer a
# symbol the other does not.
SYMBOLS = (
    ('A', '®', 'Registered trademark'),
    ('B', '©', 'Copyright'),
    ('C', '™', 'Trademark'),
    ('D', 'UL', 'Underwriters Laboratories approval'),
    ('E', 'CSA', 'Canadian Standards Association approval'),
)

# The GS font's cell, its gap along a row and its baseline, all at x1 and in
# dots. A symbol is drawn on this grid one unit to a dot, then magnified.
GRID = 24
GAP = 2
BASELINE = 18           # three quarters of the cell, Table 33's figure for GS

# Stroke weights, in grid units
_RING = 2.0
_STROKE = 2.0
_LETTER = 1.5           # the letters inside the (R) and (C), which are small
_FINE = 1.0

# Every glyph as a list of strokes on the grid, each one of
#   ('ring', (x0, y0, x1, y1), weight)                    a whole ellipse
#   ('arc', (x0, y0, x1, y1), start, end, weight)         part of one, in
#                                                         degrees clockwise
#                                                         from three o'clock
#   ('line', ((x, y), ...), weight)                       a polyline
# An ellipse's box is the centre line of its stroke, not its outer edge, and a
# line's ends are round, so its ink runs half the weight past them.
_STROKES = {
    # (R) - an R in a ring, the ring filling 15 x 15 at the top left
    'A': (('ring', (1, 1, 14, 14), _RING),
          ('line', ((5.5, 11), (5.5, 4), (7.5, 4)), _LETTER),
          ('arc', (5.5, 4, 9.5, 7.5), 270, 90, _LETTER),
          ('line', ((7.5, 7.5), (5.5, 7.5)), _LETTER),
          ('line', ((7.5, 7.5), (9.5, 11)), _LETTER)),
    # (C) - a C in a ring, the same 15 x 15
    'B': (('ring', (1, 1, 14, 14), _RING),
          ('arc', (4.5, 4.5, 10.5, 10.5), 45, 315, _LETTER)),
    # TM, raised, as the manual's example prints it after "ZPL II": 19 x 10
    # at the top left
    'C': (('line', ((1, 1), (7, 1)), _STROKE),
          ('line', ((4, 1), (4, 9)), _STROKE),
          ('line', ((11, 9), (11, 1), (14.5, 6.5), (18, 1), (18, 9)),
           _STROKE)),
    # UL in a ring filling the cell, the U high and the L low, with a small
    # (R) under them
    'D': (('ring', (1, 1, 23, 23), _RING),
          ('line', ((6, 5.5), (6, 10.5)), _STROKE),
          ('arc', (6, 8, 12, 14), 0, 180, _STROKE),
          ('line', ((12, 10.5), (12, 5.5)), _STROKE),
          ('line', ((13.5, 8), (13.5, 15), (18.5, 15)), _STROKE),
          ('ring', (10.5, 17.5, 13.5, 20.5), _FINE)),
    # SA inside an open C the cell's height, the C's gap to the right, where
    # its ends stop 22 across
    'E': (('arc', (1, 1, 23, 23), 35, 325, _RING),
          ('line', ((10.5, 8), (9.5, 7), (6.75, 7), (5.5, 8.25), (5.75, 10.25),
                    (9.25, 12), (10.5, 13.5), (10, 15.75), (7.5, 17),
                    (5.25, 16)), _STROKE),
          ('line', ((11.5, 17), (14.75, 7), (18, 17)), _STROKE),
          ('line', ((12.75, 13.5), (16.75, 13.5)), _STROKE)),
}

# Drawn this many times larger and then reduced, so a stroke that falls
# between dots comes out grey at its edges rather than jagged.
_SUPERSAMPLE = 4

# One entry per (data, magnification across, magnification down, ink); see
# textraster's cache, which this follows for the same reason - a drag
# repaints many times a second.
_CACHE_LIMIT = 64
_cache = {}


def label(code: str) -> str:
    """What the letter prints as - "®", "UL" - or the letter itself when
    it is not one of the five."""
    for letter, shown, _name in SYMBOLS:
        if letter == code:
            return shown
    return code


def cells(data: str) -> int:
    """How many cells the data occupies: one per character, and one for an
    empty field, so a ^GS with nothing to print still has a box."""
    return max(1, len(data or ''))


def cell(height: int, width: int) -> zpl_fonts.BitmapCell:
    """The cell ^GS's h and w print one symbol in: the 24 dot cell at the
    nearest whole magnification of each, from 1 to 10, as fonts A-H round
    theirs - so ^GSN,60,60 is 72 dots square, not 60 - with the gap after it
    along the row and the baseline's depth in it."""
    across = zpl_fonts.magnification(width, GRID)
    down = zpl_fonts.magnification(height, GRID)
    return zpl_fonts.BitmapCell(GRID * down, GRID * across, GAP * across,
                                BASELINE * down)


def run(data: str, height: int, width: int) -> int:
    """Dots along the symbols: a cell each, and a gap between one and the
    next but not after the last, as a row of bitmap text is measured."""
    size = cell(height, width)
    count = cells(data)
    return count * size.width + (count - 1) * size.gap


def baseline_offset(height: int) -> int:
    """Dots from the top of a cell down to its baseline - three quarters of
    the cell, Table 33's figure for GS - which is where ^FT places it."""
    return BASELINE * zpl_fonts.magnification(height, GRID)


def _draw_glyph(draw, strokes, scale):
    """Draw one glyph's strokes, `scale` pixels to a grid unit."""
    def point(x, y):
        return (x * scale, y * scale)

    for stroke in strokes:
        kind, weight = stroke[0], stroke[-1]
        width = max(_SUPERSAMPLE, int(round(weight * scale)))
        half = width / 2
        if kind in ('ring', 'arc'):
            x0, y0 = point(*stroke[1][:2])
            x1, y1 = point(*stroke[1][2:])
            # PIL strokes inward from the box it is given, so the box goes
            # out by half the weight to centre the stroke on the path.
            box = (x0 - half, y0 - half, x1 + half, y1 + half)
            if kind == 'ring':
                draw.ellipse(box, outline=255, width=width)
            else:
                draw.arc(box, stroke[2], stroke[3], fill=255, width=width)
        else:
            points = [point(x, y) for x, y in stroke[1]]
            draw.line(points, fill=255, width=width, joint='curve')
            # Round the ends, which PIL leaves square
            for x, y in (points[0], points[-1]):
                draw.ellipse((x - half, y - half, x + half, y + half), fill=255)


def _glyph(strokes, across: int, down: int):
    """One symbol's cell as an 'L' mask, `across` and `down` its two
    magnifications. Drawn square at the larger and then stretched, as the
    printer stretches its own dots, so an h and w that magnify differently
    thicken the strokes running one way and not the other."""
    times = max(across, down)
    side = GRID * times * _SUPERSAMPLE
    mask = PILImage.new('L', (side, side), 0)
    _draw_glyph(PILImageDraw.Draw(mask), strokes, times * _SUPERSAMPLE)
    return mask.resize((GRID * across, GRID * down), PILImage.BOX)


def raster(data: str, height: int, width: int, ink=(0, 0, 0, 255)):
    """The field data as symbols: an RGBA PIL image, transparent where there
    is no ink, `run(data, height, width)` dots across and a cell down.

    Drawn upright; a turned field turns the image, the way text is turned.
    A character that is not A-E is a blank cell - "Unidentified characters
    should default to a space", as the manual puts it for every font. `ink`
    is what a ^FR field draws with, white instead of black.
    """
    size = cell(height, width)
    across, down = size.width // GRID, size.height // GRID
    data = data or ''
    key = (data, across, down, ink)
    hit = _cache.get(key)
    if hit is not None:
        return hit

    mask = PILImage.new('L', (run(data, height, width), size.height), 0)
    for index, character in enumerate(data):
        strokes = _STROKES.get(character)
        if strokes:
            mask.paste(_glyph(strokes, across, down),
                       (index * (size.width + size.gap), 0))

    image = PILImage.new('RGBA', mask.size, tuple(ink[:3]) + (0,))
    # The ink's own alpha scales the mask, so a translucent ink stays one
    image.putalpha(mask if ink[3] == 255
                   else mask.point(lambda v: v * ink[3] // 255))

    if len(_cache) >= _CACHE_LIMIT:
        _cache.clear()
    _cache[key] = image
    return image


def icon(code: str):
    """The symbol as both + Symbol menus show it: its x1 ink centred in a
    cell-sized square, since the (R), (C) and TM print in the cell's top left
    and would sit small and high beside their names."""
    drawn = raster(code, GRID, GRID)
    square = PILImage.new('RGBA', drawn.size, (0, 0, 0, 0))
    ink = drawn.getchannel('A').getbbox()
    if ink is not None:
        piece = drawn.crop(ink)
        square.paste(piece, ((GRID - piece.width) // 2,
                             (GRID - piece.height) // 2))
    return square


def clear_cache():
    """Drop every cached raster. Used by the tests."""
    _cache.clear()
