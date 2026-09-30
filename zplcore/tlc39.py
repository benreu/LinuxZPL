"""TLC39 encoder - returns the dots a printer lays down for ^BT.

The telecommunications industry's can-tag symbol: a Code 39 carrying a part's
six-digit ECI number, and a four-column MicroPDF417 carrying its serial
number and whatever else follows. ^BT's field data is those, comma
separated - `123456,ABCD12345678901234,5551212,888999` - and when the seventh
character is not a comma there is no MicroPDF417 at all, only the Code 39.

The two parts have module widths and heights of their own (^BT's w1, r1 and
h1 for the Code 39, w2 and h2 for the MicroPDF417), so the symbol is drawn
here, in dots, and leaves as runs of dark dots as a MaxiCode does. The
MicroPDF417 goes under the Code 39, left edges together, one of its modules
below, and holds its data in byte compaction alone: that is how the manual
draws its own example (p.135) - its MicroPDF417 is twelve rows, which is what
that data needs in byte compaction and not otherwise. Neither has been
printed yet; FUNCTIONAL_SPEC.md section 18.
"""

import functools

from . import code39, micropdf417

# The ECI number's length, which the Code 39 carries alone.
ECI_LENGTH = 6

# The MicroPDF417's four-column sizes, smallest first: ^BF's mode 33, four
# rows, and then 23 to 32, six rows to 44. The printer "must determine what
# mode to use based on the number of characters to be encoded".
_FOUR_COLUMNS = (33,) + tuple(range(23, 33))


def split(data: str) -> tuple:
    """(ECI number, what the MicroPDF417 carries or None)."""
    eci = data[:ECI_LENGTH]
    if len(eci) < ECI_LENGTH or not eci.isdigit():
        raise ValueError("a TLC39 starts with its six-digit ECI number, "
                         f"and {data[:ECI_LENGTH]!r} is not one")
    rest = data[ECI_LENGTH + 1:] if data[ECI_LENGTH:ECI_LENGTH + 1] == ',' else ''
    return eci, rest or None


def micro_mode(rest: str) -> int:
    """The smallest four-column MicroPDF417 that holds `rest`, in byte
    compaction."""
    needed = len(micropdf417.byte_codewords(rest))
    for mode in _FOUR_COLUMNS:
        if needed <= micropdf417.capacity(mode):
            return mode
    raise ValueError(f"the data after the ECI number needs {needed} "
                     "codewords, more than a four-column MicroPDF417 holds")


def _bars(eci: str, module: int, ratio: float) -> list:
    """The Code 39's bars as (x, width) in dots: a narrow element `module`
    dots wide and a wide one `ratio` times that, rounded to a whole dot."""
    wide = max(module + 1, round(module * ratio))
    bars, x = [], 0
    for index, width in enumerate(code39.encode(eci)):
        dots = wide if width == 2 else module
        if index % 2 == 0:
            bars.append((x, dots))
        x += dots
    return bars


def placeholder(module: int, ratio: float, height: int) -> tuple:
    """(width, height) of the Code 39 alone, for a TLC39 whose data cannot
    be drawn: where it will stand once the data is put right."""
    x, width = _bars('0' * ECI_LENGTH, module, ratio)[-1]
    return x + width, height


@functools.lru_cache(maxsize=32)
def symbol(data: str, module: int, ratio: float, height: int,
           micro_module: int, micro_row: int) -> tuple:
    """(width, height, runs): the whole TLC39 in dots, as runs of dark dots
    (x, y, length) one dot tall. Kept, because both canvases ask on every
    paint."""
    module, height = max(1, module), max(1, height)
    micro_module, micro_row = max(1, micro_module), max(1, micro_row)
    eci, rest = split(data)
    bars = _bars(eci, module, ratio)
    width = bars[-1][0] + bars[-1][1]
    runs = [(x, y, length) for y in range(height) for x, length in bars]
    total = height
    if rest is not None:
        top = height + micro_module
        rows = micropdf417.encode(rest, micro_mode(rest), bytes_only=True)
        for index, row in enumerate(rows):
            spans, start = [], None
            for column, dark in enumerate(tuple(row) + (False,)):
                if dark and start is None:
                    start = column
                elif not dark and start is not None:
                    spans.append((start * micro_module,
                                  (column - start) * micro_module))
                    start = None
            for dy in range(micro_row):
                runs.extend((x, top + index * micro_row + dy, length)
                            for x, length in spans)
        width = max(width, len(rows[0]) * micro_module)
        total = top + len(rows) * micro_row
    return width, total, tuple(runs)
