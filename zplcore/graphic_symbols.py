"""
^GS - the graphic symbol font, drawn.

^GS prints from the printer's resident GS font, and which glyph is chosen by
the field data: A through E (manual, ^GS). The font is a 24 x 24 matrix
(Table 33, which lists it beside the scalable font 0: proportional, baseline
three quarters of the way down). There is no file for it on this machine -
fonts.RESIDENT_FONTS can only name it - so the five glyphs are drawn here, as
strokes on that 24 x 24 grid, scaled to whatever height and width the field
asks for.

The preview and both canvases blit the one raster made here, the way they
share textraster's and geometry.barcode_rects', so none of them can draw a
different symbol. The drawings are this designer's own, made to read as the
manual's; FUNCTIONAL_SPEC.md section 18 records that they are not the
printer's bitmaps.
"""

from PIL import Image as PILImage, ImageDraw as PILImageDraw

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

# The side of the design grid the strokes are drawn on - the GS font's own
# matrix, so a symbol asked for at 24 x 24 is drawn one grid unit to a dot.
GRID = 24

# Stroke weights, in grid units
_RING = 1.6
_STROKE = 2.0
_FINE = 1.0

# Every glyph as a list of strokes on the grid, each one of
#   ('ring', (x0, y0, x1, y1), weight)                    a whole ellipse
#   ('arc', (x0, y0, x1, y1), start, end, weight)         part of one, in
#                                                         degrees clockwise
#                                                         from three o'clock
#   ('line', ((x, y), ...), weight)                       a polyline
# An ellipse's box is the centre line of its stroke, not its outer edge.
_STROKES = {
    # (R) - an R in a ring
    'A': (('ring', (1.5, 1.5, 22.5, 22.5), _RING),
          ('line', ((8.5, 17.5), (8.5, 6.5), (12.5, 6.5)), _STROKE),
          ('arc', (10, 6.5, 15.5, 12), 270, 90, _STROKE),
          ('line', ((12.5, 12), (8.5, 12)), _STROKE),
          ('line', ((12, 12), (15.5, 17.5)), _STROKE)),
    # (C) - a C in a ring
    'B': (('ring', (1.5, 1.5, 22.5, 22.5), _RING),
          ('arc', (7, 7, 17, 17), 45, 315, _STROKE)),
    # TM, raised, as the manual's example prints it after "ZPL II"
    'C': (('line', ((1.5, 3), (10, 3)), _STROKE),
          ('line', ((5.75, 3), (5.75, 13)), _STROKE),
          ('line', ((12.5, 13), (12.5, 3), (17.25, 10), (22, 3), (22, 13)),
           _STROKE)),
    # UL in a ring, the U high and the L low, with a small (R) under them
    'D': (('ring', (1.5, 1.5, 22.5, 22.5), _RING),
          ('line', ((6, 5.5), (6, 10.5)), _STROKE),
          ('arc', (6, 8, 12, 14), 0, 180, _STROKE),
          ('line', ((12, 10.5), (12, 5.5)), _STROKE),
          ('line', ((13.5, 8), (13.5, 15), (18.5, 15)), _STROKE),
          ('ring', (10.5, 17.5, 13.5, 20.5), _FINE)),
    # SA inside an open C, the C's gap to the right
    'E': (('arc', (1.5, 1.5, 22.5, 22.5), 35, 325, _RING),
          ('line', ((10.5, 8), (9.5, 7), (6.75, 7), (5.5, 8.25), (5.75, 10.25),
                    (9.25, 12), (10.5, 13.5), (10, 15.75), (7.5, 17),
                    (5.25, 16)), _STROKE),
          ('line', ((11.5, 17), (14.75, 7), (18, 17)), _STROKE),
          ('line', ((12.75, 13.5), (16.75, 13.5)), _STROKE)),
}

# Drawn this many times larger and then reduced, so a stroke that falls
# between dots comes out grey at its edges rather than jagged.
_SUPERSAMPLE = 4

# One entry per (data, height, width, ink); see textraster's cache, which this
# follows for the same reason - a drag repaints many times a second.
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


def baseline_offset(height: int) -> int:
    """Dots from the top of a cell down to its baseline - three quarters of
    the height, Table 33's figure for GS - which is where ^FT places it."""
    return (3 * max(1, int(height))) // 4


def _draw_glyph(draw, strokes, left, sx, sy):
    """Draw one glyph's strokes, scaled by (sx, sy) and moved `left` across."""
    def point(x, y):
        return (left + x * sx, y * sy)

    for stroke in strokes:
        kind, weight = stroke[0], stroke[-1]
        width = max(_SUPERSAMPLE, int(round(weight * min(sx, sy))))
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


def raster(data: str, height: int, width: int, ink=(0, 0, 0, 255)):
    """The field data as symbols: an RGBA PIL image, transparent where there
    is no ink, `cells(data) * width` dots across and `height` dots down.

    Drawn upright; a turned field turns the image, the way text is turned.
    A character that is not A-E is a blank cell - "Unidentified characters
    should default to a space", as the manual puts it for every font. `ink`
    is what a ^FR field draws with, white instead of black.
    """
    height = max(1, int(height))
    width = max(1, int(width))
    data = data or ''
    key = (data, height, width, ink)
    hit = _cache.get(key)
    if hit is not None:
        return hit

    count = cells(data)
    big = (count * width * _SUPERSAMPLE, height * _SUPERSAMPLE)
    mask = PILImage.new('L', big, 0)
    draw = PILImageDraw.Draw(mask)
    sx = width * _SUPERSAMPLE / GRID
    sy = height * _SUPERSAMPLE / GRID
    for index, character in enumerate(data):
        strokes = _STROKES.get(character)
        if strokes:
            _draw_glyph(draw, strokes, index * width * _SUPERSAMPLE, sx, sy)
    mask = mask.resize((count * width, height), PILImage.BOX)

    image = PILImage.new('RGBA', mask.size, tuple(ink[:3]) + (0,))
    # The ink's own alpha scales the mask, so a translucent ink stays one
    image.putalpha(mask if ink[3] == 255
                   else mask.point(lambda v: v * ink[3] // 255))

    if len(_cache) >= _CACHE_LIMIT:
        _cache.clear()
    _cache[key] = image
    return image


def clear_cache():
    """Drop every cached raster. Used by the tests."""
    _cache.clear()
