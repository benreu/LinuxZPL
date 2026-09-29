"""MicroPDF417 encoder - returns the rows of dark modules for ^BF.

PDF417's codewords, compaction and Reed-Solomon correction, in a symbol
built for small labels. The seventeen-module start and stop patterns and the
row indicators are gone; in their place each row starts and ends with a ten-
module row address pattern, and three and four column symbols have a third
one in the middle. The patterns run through a sequence of 52, and a reader
tells where it is in the symbol from which of them it sees.

The symbol comes in 34 fixed sizes of one to four columns, and ^BF's m names
one of them outright: nothing here chooses a size for the data. Data that
does not fit the size asked for draws nothing, as the printer prints nothing.

The sizes, the error correction each carries, and the patterns each starts
its rows on are ISO/IEC 24728's Table 1 and ISO/IEC 15438's Tables 2 and 10
to 12, transcribed from zint's copy (backend/pdf417_tabs.h) and checked
against zxing-cpp, which reads back every size from the preview.
"""

import functools

from . import pdf417
from .pdf417_patterns import PATTERNS

# Every size, in the standard's order, as (columns, rows, error-correction
# codewords, left, centre and right row address pattern the first row uses,
# and the cluster it uses). The patterns are numbered from 1, as the
# standard numbers them; a symbol of one or two columns has no centre one.
_VARIANTS = (
    (1, 11,  7,  1,  0,  9, 0),
    (1, 14,  7,  8,  0,  8, 1),
    (1, 17,  7, 36,  0, 36, 2),
    (1, 20,  8, 19,  0, 19, 0),
    (1, 24,  8,  9,  0, 17, 2),
    (1, 28,  8, 25,  0, 33, 0),
    (2,  8,  8,  1,  0,  1, 0),
    (2, 11,  9,  1,  0,  9, 0),
    (2, 14,  9,  8,  0,  8, 1),
    (2, 17, 10, 36,  0, 36, 2),
    (2, 20, 11, 19,  0, 19, 0),
    (2, 23, 13,  9,  0, 17, 2),
    (2, 26, 15, 27,  0, 35, 2),
    (3,  6, 12,  1,  1,  1, 0),
    (3,  8, 14,  7,  7,  7, 0),
    (3, 10, 16, 15, 15, 15, 2),
    (3, 12, 18, 25, 25, 25, 0),
    (3, 15, 21, 37, 37, 37, 0),
    (3, 20, 26,  1, 17, 33, 0),
    (3, 26, 32,  1,  9, 17, 0),
    (3, 32, 38, 21, 29, 37, 2),
    (3, 38, 44, 15, 31, 47, 2),
    (3, 44, 50,  1, 25, 49, 0),
    (4,  4,  8, 47, 19, 43, 1),
    (4,  6, 12,  1,  1,  1, 0),
    (4,  8, 14,  7,  7,  7, 0),
    (4, 10, 16, 15, 15, 15, 2),
    (4, 12, 18, 25, 25, 25, 0),
    (4, 15, 21, 37, 37, 37, 0),
    (4, 20, 26,  1, 17, 33, 0),
    (4, 26, 32,  1,  9, 17, 0),
    (4, 32, 38, 21, 29, 37, 2),
    (4, 38, 44, 15, 31, 47, 2),
    (4, 44, 50,  1, 25, 49, 0),
)

# ^BF's m, 0 to 33, as the standard's size it names. The manual's Table 10
# follows the standard's order except for the smallest four-column symbol,
# four rows tall, which it moves from the front of the four-column sizes to
# the very end - so mode 23 is four columns by six rows and mode 33 four by
# four.
_MODE_VARIANT = tuple(range(23)) + tuple(range(24, 34)) + (23,)
MODES = len(_MODE_VARIANT)

# The row address patterns, ten modules each, most significant bit first.
# The side patterns go at both ends of a row and the centre ones between its
# columns; both run through the same 52 positions.
_RAP_SIDE = (
    0x322, 0x3a2, 0x3b2, 0x332, 0x372, 0x37a, 0x33a, 0x3ba,
    0x39a, 0x3da, 0x3ca, 0x38a, 0x30a, 0x31a, 0x312, 0x392,
    0x3d2, 0x3d6, 0x3d4, 0x394, 0x3b4, 0x3a4, 0x3a6, 0x3ae,
    0x3ac, 0x3a8, 0x328, 0x32c, 0x32e, 0x326, 0x336, 0x3b6,
    0x396, 0x316, 0x314, 0x334, 0x374, 0x364, 0x366, 0x36e,
    0x36c, 0x368, 0x348, 0x358, 0x35c, 0x35e, 0x34e, 0x34c,
    0x344, 0x346, 0x342, 0x362,
)
_RAP_CENTRE = (
    0x2ce, 0x24e, 0x26e, 0x22e, 0x226, 0x236, 0x216, 0x212,
    0x21a, 0x23a, 0x232, 0x222, 0x262, 0x272, 0x27a, 0x2fa,
    0x2f2, 0x2f6, 0x276, 0x274, 0x264, 0x266, 0x246, 0x242,
    0x2c2, 0x2e2, 0x2e6, 0x2e4, 0x2ec, 0x26c, 0x22c, 0x228,
    0x268, 0x2e8, 0x2c8, 0x2cc, 0x2c4, 0x2c6, 0x286, 0x28e,
    0x28c, 0x29c, 0x298, 0x2b8, 0x2b0, 0x290, 0x2d0, 0x250,
    0x258, 0x25c, 0x2dc, 0x2de,
)
_RAP_WIDTH = 10


def size(mode: int) -> tuple:
    """(columns, rows, error-correction codewords) for ^BF's m."""
    columns, rows, checks = _VARIANTS[_MODE_VARIANT[mode]][:3]
    return columns, rows, checks


def dimensions(mode: int) -> tuple:
    """(modules across, rows) of a symbol of this mode, whatever it holds:
    an address pattern at each end and, past two columns, one in the middle,
    seventeen modules a codeword, and the stop bar."""
    columns, rows, _checks = size(mode)
    patterns = 3 if columns > 2 else 2
    return patterns * _RAP_WIDTH + columns * 17 + 1, rows


def capacity(mode: int) -> int:
    """How many data codewords a symbol of this mode holds."""
    columns, rows, checks = size(mode)
    return columns * rows - checks


def data_codewords(data: str) -> list:
    """The message as codewords, in whichever modes are shortest.

    PDF417's own compaction, with two differences. A MicroPDF417 begins in
    byte compaction rather than text (ISO/IEC 24728 5.4.3), so a message that
    opens with text latches into it first; one that opens with digits or
    bytes already starts with its own latch. And since a symbol this small
    has codewords to spare for none of it, a run of digits goes into numeric
    mode whenever that comes out shorter, not only once it is thirteen long:
    Table 10 has mode 0 hold eight digits, which only numeric mode fits.
    """
    best = None
    # The longest run first, so a tie keeps PDF417's own choice.
    for numeric_from in range(13, 0, -1):
        codewords = pdf417._compact(data, numeric_from)
        if codewords and codewords[0] < pdf417._LATCH_TEXT:
            codewords.insert(0, pdf417._LATCH_TEXT)
        if best is None or len(codewords) < len(best):
            best = codewords
    return best


def _rows(body: list, mode: int) -> tuple:
    """The symbol for these data codewords, padding included, as rows of
    booleans: its correction is added here and each row laid out with its
    address patterns."""
    variant = _MODE_VARIANT[mode]
    columns, rows, checks, left, centre, right, cluster = _VARIANTS[variant]
    codewords = list(body) + pdf417.error_codewords(body, checks)

    grid = []
    for row in range(rows):
        patterns = PATTERNS[(cluster + row) % 3]
        values = codewords[row * columns:(row + 1) * columns]
        bits = format(_RAP_SIDE[(left - 1 + row) % 52], f'0{_RAP_WIDTH}b')
        for index, value in enumerate(values):
            # Three columns put the centre pattern after the first one, four
            # after the second - so it always has two columns to its right.
            if columns > 2 and index == columns - 2:
                bits += format(_RAP_CENTRE[(centre - 1 + row) % 52],
                               f'0{_RAP_WIDTH}b')
            bits += format(patterns[value], '017b')
        bits += format(_RAP_SIDE[(right - 1 + row) % 52], f'0{_RAP_WIDTH}b')
        bits += '1'                     # the one-module stop bar
        grid.append(tuple(bit == '1' for bit in bits))
    return tuple(grid)


@functools.lru_cache(maxsize=64)
def encode(data: str, mode: int = 0) -> tuple:
    """The symbol as rows of booleans, one per module, dark where True.

    One row per row of codewords; ^BF's h is how many dots tall each is,
    which the caller applies. Kept, because both canvases ask on every
    paint.
    """
    if not 0 <= mode < MODES:
        raise ValueError(f"MicroPDF417 has no mode {mode}; ^BF's m is 0 to "
                         f"{MODES - 1}")
    payload = data_codewords(data)
    room = capacity(mode)
    if len(payload) > room:
        columns, rows, _checks = size(mode)
        raise ValueError(
            f"the data needs {len(payload)} codewords, more than the {room} "
            f"a mode {mode} symbol ({columns} by {rows}) holds")
    # The gap is padded with the text-mode latch, which decodes to nothing.
    body = payload + [pdf417._LATCH_TEXT] * (room - len(payload))
    return _rows(body, mode)
