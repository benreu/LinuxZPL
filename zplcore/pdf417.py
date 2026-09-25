"""PDF417 encoder - returns the grid of dark modules for drawing.

A stacked symbology: three to ninety rows, each one a start pattern, a row
indicator, some codewords, another row indicator and a stop pattern. Every
codeword is four bars and four spaces in seventeen modules, which is what the
name counts, and a reader tells one row from the next by which of three
clusters its patterns come from.

The three sections below are the three things that have to happen: the text
is compacted into codewords - in whichever of three modes is shortest for the
run at hand - the codewords are given Reed-Solomon correction over GF(929),
and the result is cut into rows, each with the indicators that say where in
the symbol it sits.
"""

from .pdf417_patterns import CODEWORD, PATTERNS, START, STOP, STOP_WIDTH

# ZPL's own limits for ^B7's c and r, and the number of codewords a symbol
# may not reach.
MAX_COLUMNS = 30
MIN_ROWS, MAX_ROWS = 3, 90
MAX_CODEWORDS = 929

# The shape a symbol takes when the command says nothing about it: the
# manual's "1:2 row-to-column aspect ratio".
ASPECT = 2

# The codewords that switch compaction mode.
_LATCH_TEXT = 900
_LATCH_BYTE = 901
_LATCH_BYTE_SIX = 924
_LATCH_NUMERIC = 902


# --- text compaction --------------------------------------------------------

# The four submodes, as the character each of their thirty values stands for.
# A dot is a value that switches submode rather than printing anything, and
# is never matched against a character.
_UPPER = "ABCDEFGHIJKLMNOPQRSTUVWXYZ \0\0\0"
_LOWER = "abcdefghijklmnopqrstuvwxyz \0\0\0"
_MIXED = "0123456789&\r\t,:#-.$/+%*=^\0 \0\0\0"
_PUNCT = ";<>@[\\]_`~!\r\t,:\n-.$/\"|*()?{}'\0"
_SUBMODES = ('upper', 'lower', 'mixed', 'punct')
_TABLES = {'upper': _UPPER, 'lower': _LOWER, 'mixed': _MIXED, 'punct': _PUNCT}

# What each submode spends to reach each other one, as the values to emit.
# Latches stay; the two shifts - upper from lower, punct from anywhere - last
# a single character, which is what makes a lone capital or a lone semicolon
# cost one value rather than two.
_LATCH = {
    ('upper', 'lower'): (27,), ('upper', 'mixed'): (28,),
    ('upper', 'punct'): (28, 25),
    ('lower', 'upper'): (28, 28), ('lower', 'mixed'): (28,),
    ('lower', 'punct'): (28, 25),
    ('mixed', 'upper'): (28,), ('mixed', 'lower'): (27,),
    ('mixed', 'punct'): (25,),
    ('punct', 'upper'): (29,), ('punct', 'lower'): (29, 27),
    ('punct', 'mixed'): (29, 28),
}
_SHIFT = {('lower', 'upper'): 27, ('upper', 'punct'): 29,
          ('lower', 'punct'): 29, ('mixed', 'punct'): 29}


def _text_values(text: str) -> list:
    """The text as submode values, with the switches between them.

    At each character the cheapest way to reach a submode that holds it
    wins: a shift where one exists and only one character needs it, a latch
    otherwise. Going by runs rather than by character is what keeps a word
    of capitals from paying a latch each way for every letter of it.
    """
    values = []
    current = 'upper'
    index = 0
    while index < len(text):
        char = text[index]
        if _TABLES[current].find(char) >= 0 and char != '\0':
            values.append(_TABLES[current].index(char))
            index += 1
            continue

        wanted = next((name for name in _SUBMODES
                       if char != '\0' and _TABLES[name].find(char) >= 0), None)
        if wanted is None:
            return None                      # not text; another mode must take it

        # A shift costs one value and covers one character. It is worth it
        # only when the next character is back in this submode.
        shift = _SHIFT.get((current, wanted))
        nxt = text[index + 1:index + 2]
        if shift is not None and (not nxt or (nxt != '\0'
                                              and _TABLES[current].find(nxt) >= 0)):
            values.append(shift)
            values.append(_TABLES[wanted].index(char))
            index += 1
            continue

        values.extend(_LATCH[(current, wanted)])
        current = wanted
    return values


def _text(text: str) -> list:
    """Codewords for a run of text: two submode values in each."""
    values = _text_values(text)
    if values is None:
        return None
    if len(values) % 2:
        values.append(29)                    # pad with a shift, which is inert
    return [values[i] * 30 + values[i + 1] for i in range(0, len(values), 2)]


# --- byte and numeric compaction --------------------------------------------

def _bytes(raw: bytes) -> list:
    """Codewords for a run of bytes: six of them in every five codewords."""
    out = []
    index = 0
    while index + 6 <= len(raw):
        total = int.from_bytes(raw[index:index + 6], 'big')
        chunk = []
        for _ in range(5):
            total, remainder = divmod(total, 900)
            chunk.append(remainder)
        out.extend(reversed(chunk))
        index += 6
    # Whatever is left over goes one byte to a codeword, as itself.
    out.extend(raw[index:])
    return out


def _numeric(digits: str) -> list:
    """Codewords for a run of digits: forty-four at a time, base 900.

    A one is put in front of the run before the conversion, so a run that
    starts with a zero does not lose it.
    """
    out = []
    for start in range(0, len(digits), 44):
        total = int('1' + digits[start:start + 44])
        chunk = []
        while total:
            total, remainder = divmod(total, 900)
            chunk.append(remainder)
        out.extend(reversed(chunk))
    return out


def _compact(data: str) -> list:
    """The whole message as codewords, in whichever modes are shortest.

    Runs of ten digits or more are worth the switch into numeric mode, which
    packs about three of them into a codeword; anything the text mode cannot
    hold goes into byte mode. The symbol starts in text mode, so the first
    run of text needs no latch.
    """
    runs = []
    index = 0
    while index < len(data):
        digits = 0
        while index + digits < len(data) and data[index + digits].isdigit():
            digits += 1
        if digits >= 13:
            runs.append(('numeric', data[index:index + digits]))
            index += digits
            continue
        # Gather up to the next long run of digits, as one piece.
        start = index
        while index < len(data):
            run = 0
            while index + run < len(data) and data[index + run].isdigit():
                run += 1
            if run >= 13:
                break
            index += max(1, run)
        runs.append(('text', data[start:index]))

    out = []
    mode = 'text'
    for kind, piece in runs:
        if kind == 'numeric':
            out.append(_LATCH_NUMERIC)
            out.extend(_numeric(piece))
            mode = 'numeric'
            continue
        codewords = _text(piece)
        if codewords is None:
            raw = piece.encode('utf-8')
            out.append(_LATCH_BYTE_SIX if len(raw) % 6 == 0 else _LATCH_BYTE)
            out.extend(_bytes(raw))
            mode = 'byte'
            continue
        if mode != 'text':
            out.append(_LATCH_TEXT)
        out.extend(codewords)
        mode = 'text'
    return out


# --- Reed-Solomon over GF(929) ----------------------------------------------

_MODULUS = 929


def error_codewords(data: list, count: int) -> list:
    """The Reed-Solomon check codewords, over the prime field GF(929).

    Not a power of two, so there are no logarithm tables here and no carry-
    less multiplication: the arithmetic is ordinary integers modulo 929.
    """
    poly = [1]
    for power in range(1, count + 1):
        root = pow(3, power, _MODULUS)
        poly = [0] + poly
        for index in range(len(poly) - 1):
            poly[index] = (poly[index] - poly[index + 1] * root) % _MODULUS

    remainder = [0] * count
    for value in data:
        factor = (value + remainder[-1]) % _MODULUS
        for index in range(count - 1, 0, -1):
            remainder[index] = (remainder[index - 1]
                                - factor * poly[index]) % _MODULUS
        remainder[0] = (-factor * poly[0]) % _MODULUS
    return [(-value) % _MODULUS for value in reversed(remainder)]


# --- the symbol -------------------------------------------------------------

def _shape(count: int, columns: int, rows: int):
    """How many columns and rows to use for `count` codewords."""
    if columns and rows:
        return columns, rows
    if columns:
        return columns, max(MIN_ROWS, -(-count // columns))
    if rows:
        return max(1, min(MAX_COLUMNS, -(-count // rows))), rows
    # The manual's own default: about twice as many rows as columns.
    best = None
    for candidate in range(1, MAX_COLUMNS + 1):
        needed = max(MIN_ROWS, -(-count // candidate))
        if needed > MAX_ROWS:
            continue
        score = abs(needed - candidate * ASPECT)
        if best is None or score < best[0]:
            best = (score, candidate, needed)
    if best is None:
        raise ValueError(f"{count} codewords do not fit any PDF417 symbol")
    return best[1], best[2]


def encode(data: str, columns: int = 0, rows: int = 0, security: int = 0,
           truncate: bool = False) -> list:
    """The symbol as rows of booleans, one per module, dark where True.

    One row per row of codewords. ^B7 draws each of those several modules
    tall - its own h - which the caller applies, because it is the caller
    that has to work out an omitted h from how many rows there turned out to
    be.
    """
    security = max(0, min(8, security))
    checks = 2 ** (security + 1)

    payload = _compact(data)
    columns, rows = _shape(len(payload) + 1 + checks, columns, rows)
    columns = max(1, min(MAX_COLUMNS, columns))
    rows = max(MIN_ROWS, min(MAX_ROWS, rows))

    capacity = columns * rows
    if capacity >= MAX_CODEWORDS:
        raise ValueError(
            f"{columns} columns by {rows} rows is {capacity} codewords, and "
            f"PDF417 holds fewer than {MAX_CODEWORDS}")
    if len(payload) + 1 + checks > capacity:
        raise ValueError(
            f"the data needs {len(payload) + 1 + checks} codewords, more than "
            f"the {capacity} a {columns} by {rows} symbol holds")

    # The first codeword is how many there are, itself included but not the
    # check codewords; the gap between that and the symbol's capacity is
    # padded with the text-mode latch, which decodes to nothing.
    body = [0] + payload
    body += [_LATCH_TEXT] * (capacity - checks - len(body))
    body[0] = len(body)
    body += error_codewords(body, checks)

    grid = []
    for row in range(rows):
        cluster = row % 3
        third = row // 3
        left = (third * 30 + {0: (rows - 1) // 3,
                              1: security * 3 + (rows - 1) % 3,
                              2: columns - 1}[cluster])
        right = (third * 30 + {0: columns - 1,
                               1: (rows - 1) // 3,
                               2: security * 3 + (rows - 1) % 3}[cluster])
        codewords = body[row * columns:(row + 1) * columns]

        bits = format(START, '017b')
        bits += format(PATTERNS[cluster][left], '017b')
        for value in codewords:
            bits += format(PATTERNS[cluster][value], '017b')
        if truncate:
            # The right row indicator and the stop pattern come off, leaving
            # a single narrow bar to close the symbol. It is about a fifth
            # narrower, and worth having only where the label will not be
            # damaged: there is less redundancy left to recover from.
            bits += '1'
        else:
            bits += format(PATTERNS[cluster][right], '017b')
            bits += format(STOP, f'0{STOP_WIDTH}b')
        grid.append([bit == '1' for bit in bits])
    return grid
