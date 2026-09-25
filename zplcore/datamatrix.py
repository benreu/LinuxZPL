"""Data Matrix (ECC 200) encoder - returns the grid of dark modules.

^BX offers six quality levels, 0 to 200. Only 200 is drawn: ECC 000 to 140
use convolutional coding, were only ever meant for closed systems where one
party controls both the printing and the reading, and no reader made this
century decodes them. 200 is what "for new applications, ECC 200 is
recommended" means in the manual, and what every scanner expects.

Four things have to happen to turn a string into a symbol, and they are the
four sections below: the data is compressed into codewords, the codewords
are padded to one of thirty standard sizes and given Reed-Solomon error
correction, those bytes are laid into the grid on a diagonal path that wraps
at the edges, and the finder pattern is drawn around each data region.
"""

# --- sizes ------------------------------------------------------------------

# Every ECC 200 symbol, as (rows, columns, region rows, region columns, data
# codewords, error codewords, interleaved blocks). A symbol is divided into
# that many regions, each of which carries a two-module finder pattern on two
# of its sides, so the usable grid is smaller than the symbol by two modules
# per region in each direction.
SIZES = (
    (10, 10, 1, 1, 3, 5, 1), (12, 12, 1, 1, 5, 7, 1),
    (14, 14, 1, 1, 8, 10, 1), (16, 16, 1, 1, 12, 12, 1),
    (18, 18, 1, 1, 18, 14, 1), (20, 20, 1, 1, 22, 18, 1),
    (22, 22, 1, 1, 30, 20, 1), (24, 24, 1, 1, 36, 24, 1),
    (26, 26, 1, 1, 44, 28, 1), (32, 32, 2, 2, 62, 36, 1),
    (36, 36, 2, 2, 86, 42, 1), (40, 40, 2, 2, 114, 48, 1),
    (44, 44, 2, 2, 144, 56, 1), (48, 48, 2, 2, 174, 68, 1),
    (52, 52, 2, 2, 204, 84, 2), (64, 64, 4, 4, 280, 112, 2),
    (72, 72, 4, 4, 368, 144, 4), (80, 80, 4, 4, 456, 192, 4),
    (88, 88, 4, 4, 576, 224, 4), (96, 96, 4, 4, 696, 272, 4),
    (104, 104, 4, 4, 816, 336, 6), (120, 120, 6, 6, 1050, 408, 6),
    (132, 132, 6, 6, 1304, 496, 8), (144, 144, 6, 6, 1558, 620, 10),
)
RECTANGULAR = (
    (8, 18, 1, 1, 5, 7, 1), (8, 32, 1, 2, 10, 11, 1),
    (12, 26, 1, 1, 16, 14, 1), (12, 36, 1, 2, 22, 18, 1),
    (16, 36, 1, 2, 32, 24, 1), (16, 48, 1, 2, 49, 28, 1),
)


def choose_size(count: int, rectangular: bool = False,
                rows: int = 0, columns: int = 0):
    """The smallest standard symbol that holds `count` codewords.

    `rows` and `columns` force a size up: ^BX lets a label ask for a bigger
    symbol than the data needs, so a row of them comes out the same size. A
    symbol smaller than the data is not a smaller symbol, it is no symbol -
    which is what the manual means by "if you attempt to force the data into
    too small of a symbol, no symbol is printed".
    """
    table = RECTANGULAR if rectangular else SIZES
    if rows or columns:
        # A forced size is a size, not a floor to search up from: the symbol
        # is the smallest standard one that is at least as big as asked for,
        # and if the data does not fit *that* one, none is printed. Searching
        # on past it would quietly give back a symbol bigger than the row of
        # them it was meant to line up with.
        for size in table:
            if size[0] < rows or size[1] < columns:
                continue
            if size[4] < count:
                raise ValueError(
                    f"{count} codewords do not fit a {size[0]} by {size[1]} "
                    f"Data Matrix, which holds {size[4]}")
            return size
        raise ValueError(
            f"no Data Matrix symbol is {rows} by {columns} or larger")
    for size in table:
        if size[4] >= count:
            return size
    raise ValueError(f"{count} codewords do not fit any Data Matrix symbol")


# --- encodation -------------------------------------------------------------

# The escape sequences ^BX allows in quality 200 field data, introduced by
# the character its own g parameter names - an underscore on current
# firmware, a tilde before it.
_FNC1 = 232
_PAD = 129
_ASCII_UPPER_SHIFT = 235


def _escaped(data: str, escape: str) -> list:
    """The field data with ^BX's escape sequences resolved, as a list of
    characters and codeword numbers.

    `_1` to `_3` are the FNC characters, `__` is a literal escape character
    and `_dNNN` is the character with that decimal value. `_2` structured
    append and `_5NNN` code page are read and dropped: both change what a
    reader reports rather than what is drawn, and neither is simulated.
    """
    if not escape:
        return list(data)
    out = []
    index = 0
    while index < len(data):
        char = data[index]
        if char != escape:
            out.append(char)
            index += 1
            continue
        nxt = data[index + 1:index + 2]
        if nxt == escape:
            out.append(escape)
            index += 2
        elif nxt == '1':
            out.append(_FNC1)
            index += 2
        elif nxt in ('2', '3'):
            # FNC2 is structured append, which takes nine digits after it;
            # FNC3 is a reader programming flag. Neither is drawn.
            index += 11 if nxt == '2' else 2
        elif nxt == '5':
            index += 5                      # _5NNN, a code page
        elif nxt == 'd' and data[index + 2:index + 5].isdigit():
            out.append(chr(int(data[index + 2:index + 5])))
            index += 5
        elif nxt and (nxt.isalpha() or nxt in '@[\\]^_'):
            # _X, the shift for a control character: _@ is NUL, _G is BEL.
            out.append(chr(ord(nxt.upper()) - 64))
            index += 2
        else:
            out.append(char)
            index += 1
    return out


def _ascii(values: list) -> list:
    """Codewords for the ASCII scheme, which every symbol starts in.

    A digit pair costs one codeword and anything else costs one or two, so
    ASCII alone encodes everything - the other schemes exist only to make
    runs of one kind of character cheaper.
    """
    out = []
    index = 0
    while index < len(values):
        value = values[index]
        if isinstance(value, int):
            out.append(value)
            index += 1
            continue
        pair = values[index + 1:index + 2]
        if (value.isdigit() and pair and not isinstance(pair[0], int)
                and pair[0].isdigit()):
            out.append(int(value + pair[0]) + 130)
            index += 2
            continue
        code = ord(value)
        if code > 127:
            out.append(_ASCII_UPPER_SHIFT)
            out.append(code - 128 + 1)
        else:
            out.append(code + 1)
        index += 1
    return out


# The C40 and Text schemes pack three characters into two codewords, which
# is what makes a symbol of alphanumeric text a size smaller than ASCII
# encodation would. The two differ only in which case is in the basic set -
# C40's is upper, Text's is lower - and both reach the other through a shift.
_C40_BASIC = " 0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_TEXT_BASIC = " 0123456789abcdefghijklmnopqrstuvwxyz"
# Shift 2's own set, which both schemes share: the punctuation that is not in
# the basic set, in the order the specification lists it.
_SHIFT2 = "!\"#$%&'()*+,-./:;<=>?@[\\]^_"

_LATCH = {'c40': 230, 'text': 239}
_UNLATCH = 254
_BASE256 = 231


def _scheme_values(char: str, basic: str) -> list:
    """One character's values in C40 or Text: a basic one costs a single
    value, anything else a shift and a second."""
    index = basic.find(char)
    if index >= 0:
        # The basic set starts at 3: 0, 1 and 2 are the three shifts.
        return [index + 3] if index else [3]
    code = ord(char)
    if code < 32:
        return [0, code]                       # shift 1: the control codes
    if char in _SHIFT2:
        return [1, _SHIFT2.index(char)]
    if code < 128:
        # Shift 3 holds whichever case the basic set does not, plus the five
        # characters above the letters. It is ASCII 96 to 127 for C40, whose
        # basic set is upper case; for Text, whose basic set is lower, the
        # letters in it are the upper-case ones, which sit thirty-two lower.
        # Reading them at 96 gave a negative value, and a symbol that
        # decoded to something else entirely.
        if basic is _TEXT_BASIC and 'A' <= char <= 'Z':
            return [2, code - 64]
        return [2, code - 96]
    # Above ASCII: an upper shift, then the character less 128, encoded the
    # same way again.
    return [1, 30] + _scheme_values(chr(code - 128), basic)


def _scheme(values: list, name: str):
    """Codewords for the whole message in C40 or Text, or None if it holds
    anything those schemes cannot carry."""
    basic = _C40_BASIC if name == 'c40' else _TEXT_BASIC
    widths = []
    for value in values:
        if isinstance(value, int):
            return None                        # an FNC character; ASCII only
        widths.append(_scheme_values(value, basic))

    # Only whole characters go into the triples. A character whose values
    # straddle the end of a triple would be half in the scheme and half in
    # the ASCII tail, which is two different characters to a reader - so the
    # cut is made at the last character boundary that fills a triple exactly.
    running = 0
    cut = 0
    for count, width in enumerate(widths, start=1):
        running += len(width)
        if running % 3 == 0:
            cut = count
    if cut == 0:
        return None                            # nothing whole to pack

    packed = [value for width in widths[:cut] for value in width]
    out = [_LATCH[name]]
    for index in range(0, len(packed), 3):
        total = (1600 * packed[index] + 40 * packed[index + 1]
                 + packed[index + 2] + 1)
        out.append(total // 256)
        out.append(total % 256)
    # Back to ASCII before anything else, always - not only when there is a
    # tail to encode. A symbol left latched reads its own padding as three
    # more characters per pair of codewords, which is how a correct message
    # came back with a run of nonsense on the end of it.
    out.append(_UNLATCH)
    return out + _ascii(values[cut:])


def _base256(values: list):
    """Codewords for the whole message as bytes, or None if it holds a
    character no single byte can carry.

    Every byte is randomised by its own position, for the same reason the
    padding is: a run of identical bytes would otherwise draw as a block a
    reader could mistake for part of the finder pattern.
    """
    raw = []
    for value in values:
        if isinstance(value, int) or ord(value) > 255:
            return None
        raw.append(ord(value))

    out = [_BASE256]
    if len(raw) < 250:
        out.append(len(raw))
    else:
        out.append(249 + len(raw) // 250)
        out.append(len(raw) % 250)
    out.extend(raw)
    # Randomised from the position each byte ends up at, the first data byte
    # being position 2 of the symbol.
    for position in range(1, len(out)):
        pseudo = ((149 * (position + 1)) % 255) + 1
        out[position] = (out[position] + pseudo) % 256
    out[0] = _BASE256
    return out


def _pad(codewords: list, capacity: int) -> list:
    """Pad to the symbol's capacity: one 129, then a randomised filler.

    The filler is randomised - by position, through the 253 state - so a
    symbol that is mostly padding does not come out as a solid block of
    identical modules that a reader could mistake for a finder pattern.
    """
    out = list(codewords)
    if len(out) < capacity:
        out.append(_PAD)
    while len(out) < capacity:
        pad = ((149 * (len(out) + 1)) % 253) + 1 + 129
        out.append(pad - 254 if pad > 254 else pad)
    return out


# --- Reed-Solomon over GF(256) ----------------------------------------------

# Data Matrix's field is generated by x^8 + x^5 + x^3 + x^2 + 1 (0x12D),
# which is its own, and not the 0x11D that QR and most everything else uses.
_EXP = [0] * 512
_LOG = [0] * 256
_value = 1
for _power in range(255):
    _EXP[_power] = _value
    _LOG[_value] = _power
    _value <<= 1
    if _value & 0x100:
        _value ^= 0x12D
for _power in range(255, 512):
    _EXP[_power] = _EXP[_power - 255]


def _multiply(a: int, b: int) -> int:
    return 0 if a == 0 or b == 0 else _EXP[_LOG[a] + _LOG[b]]


def _generator(count: int) -> list:
    """The generator polynomial for `count` error codewords."""
    poly = [1]
    for power in range(count):
        poly = ([0] + poly)
        for index in range(len(poly) - 1):
            poly[index] ^= _multiply(poly[index + 1], _EXP[power + 1])
    return poly


def error_codewords(data: list, count: int) -> list:
    """The Reed-Solomon check codewords for one block."""
    poly = _generator(count)
    remainder = [0] * count
    for value in data:
        factor = value ^ remainder[0]
        remainder = remainder[1:] + [0]
        if factor:
            for index in range(count):
                remainder[index] ^= _multiply(poly[count - 1 - index], factor)
    return remainder


def _interleave(data: list, size) -> list:
    """The data and its error codewords, in the order the symbol lays them.

    A large symbol splits its data into blocks and corrects each separately,
    so a blot that destroys one block does not exhaust the whole symbol's
    correction. The blocks are then interleaved, which is what spreads such
    a blot across all of them.
    """
    _r, _c, _rr, _rc, data_count, ecc_count, blocks = size
    per_block = ecc_count // blocks
    groups = [data[index::blocks] for index in range(blocks)]
    checks = [error_codewords(group, per_block) for group in groups]

    out = []
    for index in range(max(len(group) for group in groups)):
        for group in groups:
            if index < len(group):
                out.append(group[index])
    for index in range(per_block):
        for check in checks:
            out.append(check[index])
    return out


# --- placement --------------------------------------------------------------

def _place(rows: int, columns: int, codewords: list) -> list:
    """Lay the codewords into the mapping matrix.

    The ECC 200 placement is a diagonal walk that wraps around the edges:
    each codeword's eight bits go into an L-shaped cluster, and the walk
    moves up-right then down-left across the grid, so consecutive bits end up
    far apart. That is what makes a blot recoverable - it damages a little of
    many codewords rather than all of a few.
    """
    grid = [[None] * columns for _ in range(rows)]

    def module(row, column, index, bit):
        # The wrap: a cluster that runs off an edge comes back on the
        # opposite one, shifted by the offsets the specification gives.
        if row < 0:
            row += rows
            column += 4 - ((rows + 4) % 8)
        if column < 0:
            column += columns
            row += 4 - ((columns + 4) % 8)
        grid[row][column] = (index, bit)

    def utah(row, column, index):
        """One codeword's eight bits, in their L-shaped cluster."""
        module(row - 2, column - 2, index, 1)
        module(row - 2, column - 1, index, 2)
        module(row - 1, column - 2, index, 3)
        module(row - 1, column - 1, index, 4)
        module(row - 1, column, index, 5)
        module(row, column - 2, index, 6)
        module(row, column - 1, index, 7)
        module(row, column, index, 8)

    def corner(which, index):
        """The four special clusters, for symbols whose shape leaves a
        corner the diagonal walk cannot reach."""
        if which == 1:
            module(rows - 1, 0, index, 1); module(rows - 1, 1, index, 2)
            module(rows - 1, 2, index, 3); module(0, columns - 2, index, 4)
            module(0, columns - 1, index, 5); module(1, columns - 1, index, 6)
            module(2, columns - 1, index, 7); module(3, columns - 1, index, 8)
        elif which == 2:
            module(rows - 3, 0, index, 1); module(rows - 2, 0, index, 2)
            module(rows - 1, 0, index, 3); module(0, columns - 4, index, 4)
            module(0, columns - 3, index, 5); module(0, columns - 2, index, 6)
            module(0, columns - 1, index, 7); module(1, columns - 1, index, 8)
        elif which == 3:
            module(rows - 3, 0, index, 1); module(rows - 2, 0, index, 2)
            module(rows - 1, 0, index, 3); module(0, columns - 2, index, 4)
            module(0, columns - 1, index, 5); module(1, columns - 1, index, 6)
            module(2, columns - 1, index, 7); module(3, columns - 1, index, 8)
        else:
            module(rows - 1, 0, index, 1); module(rows - 1, columns - 1, index, 2)
            module(0, columns - 3, index, 3); module(0, columns - 2, index, 4)
            module(0, columns - 1, index, 5); module(1, columns - 3, index, 6)
            module(1, columns - 2, index, 7); module(1, columns - 1, index, 8)

    index = 0
    row, column = 4, 0
    while True:
        if row == rows and column == 0:
            corner(1, index); index += 1
        elif row == rows - 2 and column == 0 and columns % 4:
            corner(2, index); index += 1
        elif row == rows - 2 and column == 0 and columns % 8 == 4:
            corner(3, index); index += 1
        elif row == rows + 4 and column == 2 and columns % 8 == 0:
            corner(4, index); index += 1

        while True:                                   # up and to the right
            if row < rows and column >= 0 and grid[row][column] is None:
                utah(row, column, index); index += 1
            row -= 2
            column += 2
            if not (row >= 0 and column < columns):
                break
        row += 1
        column += 3

        while True:                                   # down and to the left
            if row >= 0 and column < columns and grid[row][column] is None:
                utah(row, column, index); index += 1
            row += 2
            column -= 2
            if not (row < rows and column >= 0):
                break
        row += 3
        column += 1
        if not (row < rows or column < columns):
            break

    # The two modules a symbol of this shape never reaches are set to a fixed
    # pattern, which readers know to expect.
    if grid[rows - 1][columns - 1] is None:
        grid[rows - 1][columns - 1] = 'one'
        grid[rows - 2][columns - 2] = 'one'

    out = [[False] * columns for _ in range(rows)]
    for r in range(rows):
        for c in range(columns):
            cell = grid[r][c]
            if cell == 'one':
                out[r][c] = True
            elif cell is not None:
                index_, bit = cell
                if index_ < len(codewords):
                    out[r][c] = bool(codewords[index_] & (1 << (8 - bit)))
    return out


def _framed(mapping: list, size) -> list:
    """The finished symbol: the mapping matrix cut into regions, each one
    wrapped in its own finder pattern.

    Two sides of every region are a solid line and the other two alternate,
    which is what a reader finds the symbol's corners, its size and its
    inclination by.
    """
    rows, columns, region_rows, region_columns = size[:4]
    map_rows = rows - 2 * region_rows
    map_columns = columns - 2 * region_columns
    tall = map_rows // region_rows
    wide = map_columns // region_columns

    out = [[False] * columns for _ in range(rows)]
    for band in range(region_rows):
        for stack in range(region_columns):
            top = band * (tall + 2)
            left = stack * (wide + 2)
            for offset in range(tall + 2):
                out[top + offset][left] = True                  # solid left
                out[top + offset][left + wide + 1] = (offset % 2 == 1)
            for offset in range(wide + 2):
                out[top + tall + 1][left + offset] = True       # solid bottom
                out[top][left + offset] = (offset % 2 == 0)
            for r in range(tall):
                for c in range(wide):
                    out[top + 1 + r][left + 1 + c] = \
                        mapping[band * tall + r][stack * wide + c]
    return out


def encode(data: str, rectangular: bool = False, rows: int = 0,
           columns: int = 0, escape: str = '_') -> list:
    """The symbol as rows of booleans, one per module, dark where True."""
    values = _escaped(data, escape)
    # ISO 16022 allows any valid encodation and defines a look-ahead for
    # choosing one. Trying each scheme over the whole message and keeping the
    # shortest is simpler, always valid, and for label data - which is
    # usually all of one kind - reaches the same symbol size. See
    # FUNCTIONAL_SPEC.md section 18.
    candidates = [_ascii(values), _scheme(values, 'c40'),
                  _scheme(values, 'text'), _base256(values)]
    codewords = min((c for c in candidates if c is not None), key=len)
    size = choose_size(len(codewords), rectangular, rows, columns)
    codewords = _pad(codewords, size[4])
    laid = _interleave(codewords, size)
    mapping = _place(size[0] - 2 * size[2], size[1] - 2 * size[3], laid)
    return _framed(mapping, size)
