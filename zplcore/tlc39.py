"""TLC39 encoder - returns the dots a printer lays down for ^BT.

The telecommunications industry's can-tag symbol: a Code 39 carrying a part's
six-digit ECI number, and a four-column MicroPDF417 carrying its serial
number and whatever else follows. ^BT's field data is those, comma
separated - `123456,ABCd12345678901234,5551212,88899` - and when the seventh
character is not a comma there is no MicroPDF417 at all, only the Code 39.

The two parts have module widths and heights of their own (^BT's w1, r1 and
h1 for the Code 39, w2 and h2 for the MicroPDF417), so the symbol is drawn
here, in dots, and leaves as runs of dark dots as a MaxiCode does.

As a printer drew the manual's own example (FUNCTIONAL_SPEC.md section 18):
the MicroPDF417 on top, one of its modules in from the left and half of one
down from the top; the Code 39 one MicroPDF417 module below it, at the left; and beside the Code 39, a quiet
zone away, a lone Code 39 T with no start or stop, standing past it by four
of its modules at each end - the link to the MicroPDF417, whose first
codeword is the linkage flag. The MicroPDF417 writes the commas after the
ECI number as asterisks, in whichever compaction is shortest, and is sized as
if it were written in byte compaction. Only the sizes the manual gives as
defaults at 203 dpi have been printed; how each distance goes with the sizes
is taken from which symbol's module it matched.
"""

import functools

from . import code39, micropdf417

# The ECI number's length, which the Code 39 carries alone.
ECI_LENGTH = 6

# The MicroPDF417's four-column sizes, smallest first: ^BF's mode 33, four
# rows, and then 23 to 32, six rows to 44. The printer "must determine what
# mode to use based on the number of characters to be encoded".
_FOUR_COLUMNS = (33,) + tuple(range(23, 33))

# The character that links the Code 39 to the MicroPDF417, and how far it
# stands from the Code 39 and past its ends, in the Code 39's modules
_LINK = 'T'
_LINK_GAP = 10
_LINK_OVERHANG = 4


def split(data: str) -> tuple:
    """(ECI number, what the MicroPDF417 carries or None) - the latter as it
    is written, its commas asterisks."""
    eci = data[:ECI_LENGTH]
    if len(eci) < ECI_LENGTH or not eci.isdigit():
        raise ValueError("a TLC39 starts with its six-digit ECI number, "
                         f"and {data[:ECI_LENGTH]!r} is not one")
    rest = data[ECI_LENGTH + 1:] if data[ECI_LENGTH:ECI_LENGTH + 1] == ',' else ''
    return eci, rest.replace(',', '*') or None


def micro_mode(rest: str) -> int:
    """The smallest four-column MicroPDF417 that holds `rest` and its
    linkage flag in byte compaction - which is what a printer sized one by,
    though it wrote the data shorter."""
    needed = 1 + len(micropdf417.byte_codewords(rest))
    for mode in _FOUR_COLUMNS:
        if needed <= micropdf417.capacity(mode):
            return mode
    raise ValueError(f"the data after the ECI number needs {needed} "
                     "codewords, more than a four-column MicroPDF417 holds")


def _bars(widths, module: int, ratio: float) -> list:
    """Code 39 elements, 1 narrow and 2 wide, as its bars' (x, width) in
    dots: a narrow element `module` dots wide and a wide one `ratio` times
    that, rounded to a whole dot."""
    wide = max(module + 1, round(module * ratio))
    bars, x = [], 0
    for index, width in enumerate(widths):
        dots = wide if width == 2 else module
        if index % 2 == 0:
            bars.append((x, dots))
        x += dots
    return bars


def placeholder(module: int, ratio: float, height: int) -> tuple:
    """(width, height) of the Code 39 alone, for a TLC39 whose data cannot
    be drawn: where it will stand once the data is put right."""
    x, width = _bars(code39.encode('0' * ECI_LENGTH), module, ratio)[-1]
    return x + width, height


def _spans(row, module: int) -> list:
    """A row of MicroPDF417 modules as its dark runs' (x, width) in dots."""
    spans, start = [], None
    for column, dark in enumerate(tuple(row) + (False,)):
        if dark and start is None:
            start = column
        elif not dark and start is not None:
            spans.append((start * module, (column - start) * module))
            start = None
    return spans


@functools.lru_cache(maxsize=32)
def symbol(data: str, module: int, ratio: float, height: int,
           micro_module: int, micro_row: int) -> tuple:
    """(width, height, runs): the whole TLC39 in dots, as runs of dark dots
    (x, y, length) one dot tall. Kept, because both canvases ask on every
    paint."""
    module, height = max(1, module), max(1, height)
    micro_module, micro_row = max(1, micro_module), max(1, micro_row)
    eci, rest = split(data)
    bars = _bars(code39.encode(eci), module, ratio)
    width = bars[-1][0] + bars[-1][1]
    if rest is None:
        runs = [(x, y, length) for y in range(height) for x, length in bars]
        return width, height, tuple(runs)

    runs = []
    rows = micropdf417.encode(rest, micro_mode(rest), linked=True)
    micro_top = micro_module // 2
    for index, row in enumerate(rows):
        spans = _spans(row, micro_module)
        for dy in range(micro_row):
            runs.extend((micro_module + x, micro_top + index * micro_row + dy,
                         length) for x, length in spans)
    micro_width = micro_module + len(rows[0]) * micro_module

    top = micro_top + len(rows) * micro_row + micro_module
    runs.extend((x, top + y, length) for y in range(height) for x, length in bars)

    link_x = width + _LINK_GAP * module
    link_top = top - _LINK_OVERHANG * module
    link_height = height + 2 * _LINK_OVERHANG * module
    link = _bars(code39._TABLE[_LINK], module, ratio)
    runs.extend((link_x + x, link_top + y, length)
                for y in range(link_height) for x, length in link)
    link_width = link[-1][0] + link[-1][1]

    total_width = max(micro_width, link_x + link_width)
    total_height = link_top + link_height
    return total_width, total_height, tuple(runs)
