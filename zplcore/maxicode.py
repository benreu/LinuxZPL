"""MaxiCode encoder - returns the dots a printer lays down for ^BD.

UPS's parcel symbol: always the same size, about an inch across, and read by
a camera over a conveyor rather than scanned. It is 33 rows of 30 hexagons
round a bullseye of three dark rings, and the bullseye is what a reader finds
it by at any angle.

Every symbol carries 144 six-bit codewords. The first ten are the primary
message, which in modes 2 and 3 is a parcel's sorting code - class of
service, country and postal code - packed as bits; the rest is the secondary
message, text in five code sets. Both halves carry their own Reed-Solomon
correction, and the secondary's is split between its odd and even codewords.

Because the symbol is hexagons and rings rather than squares, it cannot be
handed on as a grid the way the other matrix codes are. It is drawn here, at
the head's own resolution, and leaves as runs of dark dots.
"""

import functools
import math

import numpy as np

from . import aztec
from .maxicode_map import MODULE_BITS, DARK

ROWS, COLUMNS = 33, 30

# The modes ^BD's m can name, and how many codewords of message each leaves.
# Modes 2 and 3 spend the whole primary on the sorting code; 4 and 6 start
# their message there instead; 5 gives up sixteen codewords of it for more
# correction.
CAPACITY = {2: 84, 3: 84, 4: 93, 5: 77, 6: 93}

# --- the five code sets -----------------------------------------------------

# What each of the 64 values means in each code set, from ISO/IEC 16023
# Table 2. A string longer than one character is a function rather than a
# character: a shift covers the next character only, a latch lasts, and LOCK
# makes the set just shifted into a latch.
_ECI, _NS, _PAD, _LOCK = 'ECI', 'NS', 'PAD', 'LOCK'
_SPECIAL = ('\x1c', '\x1d', '\x1e')      # FS, GS and RS, in every set


def _run(first, last):
    return tuple(chr(code) for code in range(first, last + 1))


CODE_SETS = {
    'A': (('\r',) + _run(ord('A'), ord('Z')) + (_ECI,) + _SPECIAL + (_NS,)
          + (' ', _PAD) + tuple('"#$%&\'()*+,-./') + tuple('0123456789:')
          + ('SHIFT B', 'SHIFT C', 'SHIFT D', 'SHIFT E', 'LATCH B')),
    'B': (('`',) + _run(ord('a'), ord('z')) + (_ECI,) + _SPECIAL + (_NS,)
          + tuple('{') + (_PAD,) + tuple('}~\x7f;<=>?[\\]^_ ,./:@!|')
          + (_PAD, '2SHIFT A', '3SHIFT A', _PAD)
          + ('SHIFT A', 'SHIFT C', 'SHIFT D', 'SHIFT E', 'LATCH A')),
    'C': (_run(0xC0, 0xDA) + (_ECI,) + _SPECIAL + (_NS,)
          + _run(0xDB, 0xDF)
          + tuple(chr(c) for c in (0xAA, 0xAC, 0xB1, 0xB2, 0xB3, 0xB5, 0xB9,
                                   0xBA, 0xBC, 0xBD, 0xBE))
          + _run(0x80, 0x89)
          + ('LATCH A', ' ', _LOCK, 'SHIFT D', 'SHIFT E', 'LATCH B')),
    'D': (_run(0xE0, 0xFA) + (_ECI,) + _SPECIAL + (_NS,)
          + _run(0xFB, 0xFF)
          + tuple(chr(c) for c in (0xA1, 0xA8, 0xAB, 0xAF, 0xB0, 0xB4, 0xB7,
                                   0xB8, 0xBB, 0xBF, 0x8A))
          + _run(0x8B, 0x94)
          + ('LATCH A', ' ', 'SHIFT C', _LOCK, 'SHIFT E', 'LATCH B')),
    'E': (_run(0x00, 0x1A) + (_ECI, _PAD, _PAD, '\x1b', _NS)
          + _SPECIAL
          + tuple(chr(c) for c in (0x1F, 0x9F, 0xA0, 0xA2, 0xA3, 0xA4, 0xA5,
                                   0xA6, 0xA7, 0xA9, 0xAD, 0xAE, 0xB6))
          + _run(0x95, 0x9E)
          + ('LATCH A', ' ', 'SHIFT C', 'SHIFT D', _LOCK, 'LATCH B')),
}

# Where each character can be written, as {code set: value}. Every character
# from 0 to 255 is somewhere; a few - the space, FS, GS and RS - are in all
# five, which is what lets a message move between sets without spending a
# codeword on each.
_WHERE = {}
for _name, _values in CODE_SETS.items():
    for _value, _meaning in enumerate(_values):
        if len(_meaning) == 1:
            _WHERE.setdefault(_meaning, {})[_name] = _value


def _latch(from_set: str, to_set: str) -> tuple:
    """The codewords that move the message into another set for good: a
    latch where the set has one, otherwise a shift and then LOCK."""
    here = CODE_SETS[from_set]
    if 'LATCH ' + to_set in here:
        return (here.index('LATCH ' + to_set),)
    return (here.index('SHIFT ' + to_set), CODE_SETS[to_set].index(_LOCK))


_LATCHES = {(a, b): _latch(a, b) for a in CODE_SETS for b in CODE_SETS
            if a != b}


def _digits(text: str) -> bool:
    return bool(text) and all('0' <= char <= '9' for char in text)


def _compact(text: str) -> tuple:
    """The message as codewords, in as few as the code sets allow, and the
    set it ends in.

    A shortest path over (characters written, set latched into): each step
    writes one character in the current set, shifts for one, shifts for two
    or three into set A from B, packs nine digits into a Numeric Shift, or
    latches first. Every message starts in set A.
    """
    for char in text:
        if char not in _WHERE:
            raise ValueError(f"MaxiCode has no way to carry {char!r}")

    length = len(text)
    # best[i][set] = (codewords, (previous i, previous set, codewords added))
    best = [dict() for _ in range(length + 1)]
    best[0]['A'] = (0, None)

    def relax(i, code_set, cost, step):
        if code_set not in best[i] or cost < best[i][code_set][0]:
            best[i][code_set] = (cost, step)

    for i in range(length + 1):
        for code_set, (cost, _step) in list(best[i].items()):
            for other in CODE_SETS:
                if other != code_set:
                    latch = _LATCHES[(code_set, other)]
                    relax(i, other, cost + len(latch), (i, code_set, latch))
        if i == length:
            break
        char = text[i]
        for code_set, (cost, _step) in best[i].items():
            here = CODE_SETS[code_set]
            places = _WHERE[char]
            if code_set in places:
                relax(i + 1, code_set, cost + 1,
                      (i, code_set, (places[code_set],)))
            for other, value in places.items():
                if other != code_set and 'SHIFT ' + other in here:
                    relax(i + 1, code_set, cost + 2,
                          (i, code_set, (here.index('SHIFT ' + other), value)))
            if code_set == 'B':
                for count in (2, 3):
                    run = text[i:i + count]
                    if len(run) == count and all('A' in _WHERE[c] for c in run):
                        relax(i + count, 'B', cost + 1 + count,
                              (i, 'B', (here.index(f'{count}SHIFT A'),)
                               + tuple(_WHERE[c]['A'] for c in run)))
            run = text[i:i + 9]
            if len(run) == 9 and _digits(run):
                # Nine digits as one 30-bit number in five codewords, from
                # any set - a third the cost of writing them out.
                number = int(run)
                relax(i + 9, code_set, cost + 6,
                      (i, code_set, (here.index(_NS),)
                       + tuple((number >> shift) & 0x3F
                               for shift in (24, 18, 12, 6, 0))))

    # Fewest codewords wins; on a tie, a set that can pad without latching.
    end = min(best[length], key=lambda s: (best[length][s][0], s in 'CD'))
    words = []
    i, code_set = length, end
    while best[i][code_set][1] is not None:
        previous, previous_set, added = best[i][code_set][1]
        words[:0] = added
        i, code_set = previous, previous_set
    return words, end


# --- the primary message ----------------------------------------------------

def _primary(data: str, mode: int) -> list:
    """The ten codewords of a structured carrier message, read from the
    high-priority message at the front of the field data - the parcel's
    class of service, country and postal code, in the order ZPL gives them.
    """
    if mode == 2:
        head = data[:15]
        if len(head) < 15 or not _digits(head):
            raise ValueError(
                "mode 2 starts with 15 digits: a 3-digit class of service, "
                "a 3-digit country code and a 9-digit postal code")
        service, country = int(head[0:3]), int(head[3:6])
        postcode, postcode_length = int(head[6:15]), 9
        return [((postcode & 0x03) << 4) | 2,
                (postcode & 0xFC) >> 2,
                (postcode & 0x3F00) >> 8,
                (postcode & 0xFC000) >> 14,
                (postcode & 0x3F00000) >> 20,
                ((postcode & 0x3C000000) >> 26)
                | ((postcode_length & 0x03) << 4),
                ((postcode_length & 0x3C) >> 2) | ((country & 0x03) << 4),
                (country & 0xFC) >> 2,
                ((country & 0x300) >> 8) | ((service & 0x0F) << 2),
                (service & 0x3F0) >> 4]

    head = data[:12]
    postcode = head[6:12].upper()
    if (len(head) < 12 or not _digits(head[:6])
            or not all(char >= ' ' and 'A' in _WHERE.get(char, {})
                       for char in postcode)):
        raise ValueError(
            "mode 3 starts with a 3-digit class of service, a 3-digit "
            "country code and a 6-character postal code of letters and "
            "digits")
    service, country = int(head[0:3]), int(head[3:6])
    code = [_WHERE[char]['A'] for char in postcode]
    return [((code[5] & 0x03) << 4) | 3,
            ((code[4] & 0x03) << 4) | ((code[5] & 0x3C) >> 2),
            ((code[3] & 0x03) << 4) | ((code[4] & 0x3C) >> 2),
            ((code[2] & 0x03) << 4) | ((code[3] & 0x3C) >> 2),
            ((code[1] & 0x03) << 4) | ((code[2] & 0x3C) >> 2),
            ((code[0] & 0x03) << 4) | ((code[1] & 0x3C) >> 2),
            ((code[0] & 0x3C) >> 2) | ((country & 0x03) << 4),
            (country & 0xFC) >> 2,
            ((country & 0x300) >> 8) | ((service & 0x0F) << 2),
            (service & 0x3F0) >> 4]


# --- the whole symbol -------------------------------------------------------

def codewords(data: str, mode: int = 2, number: int = 1,
              count: int = 1) -> list:
    """All 144 codewords, correction included, in the order the module map
    numbers them.

    `number` of `count` is the structured append: a symbol that is one of
    several carrying a single message says which it is, as a PAD and one
    codeword at the very start of its own message.
    """
    if mode not in CAPACITY:
        raise ValueError(f"MaxiCode has no mode {mode}")
    if mode in (2, 3):
        primary = _primary(data, mode)
        message = data[15 if mode == 2 else 12:]
    else:
        primary, message = None, data

    body = []
    if count > 1:
        body += [CODE_SETS['A'].index(_PAD),
                 ((number - 1) & 0x07) << 3 | ((count - 1) & 0x07)]
    words, end = _compact(message)
    body += words
    capacity = CAPACITY[mode]
    if len(body) > capacity:
        raise ValueError(
            f"too much data for one symbol: {len(body)} codewords where "
            f"mode {mode} holds {capacity}")
    if len(body) < capacity:
        # C and D have no PAD of their own, so the message goes back to A
        # before it is filled out.
        if end in 'CD':
            body.append(CODE_SETS[end].index('LATCH A'))
            end = 'A'
        body += [CODE_SETS[end].index(_PAD)] * (capacity - len(body))

    if primary is None:
        primary, body = [mode] + body[:9], body[9:]
    words = primary + aztec.error_codewords(primary, 10, 6)

    correction = 40 if mode != 5 else 56
    secondary = [0] * (len(body) + correction)
    secondary[:len(body)] = body
    for parity in (0, 1):
        checks = aztec.error_codewords(body[parity::2], correction // 2, 6)
        secondary[len(body) + parity::2] = checks
    return words + secondary


def encode(data: str, mode: int = 2, number: int = 1,
           count: int = 1) -> list:
    """The 33 rows of 30 modules, dark where True."""
    words = codewords(data, mode, number, count)
    grid = []
    for row in MODULE_BITS:
        cells = []
        for bit in row:
            if bit >= 0:
                cells.append(bool((words[bit // 6] >> (5 - bit % 6)) & 1))
            else:
                cells.append(bit == DARK)
        grid.append(cells)
    return grid


# --- drawing it -------------------------------------------------------------

# The standard's nominal symbol is 28.14 mm across - Zebra gives it as 1.11
# inches - and thirty modules with the odd rows half a module over make that
# 30.5 module pitches. Everything else is measured in those pitches.
SYMBOL_WIDTH_MM = 28.14
_PITCH_MM = SYMBOL_WIDTH_MM / 30.5
_SQRT3 = math.sqrt(3)
# Rows of hexagons nest, so they are closer together than a hexagon is tall.
_ROW = _SQRT3 / 2
# A hexagon's cell, point to point, and the height it is drawn at - slightly
# smaller, so neighbouring modules stay apart the way they print.
_CELL = 2 / _SQRT3
_HEX = 1.0
# The bullseye (ISO/IEC 16023 section 4.11.4) is centred on the fifteenth
# module of row 16. Its light centre is a cell across and its outermost ring
# nine pitches, and between them five bands of equal width alternate dark and
# light, the first dark.
_CENTRE = (14.5, _CELL / 2 + 16 * _ROW)
_INNER = _CELL / 2
_OUTER = 4.5
_WIDTH = 30.5
_HEIGHT = 32 * _ROW + _CELL


def _pitch(dpi: int) -> float:
    """How many dots one module pitch is at this resolution."""
    return dpi / 25.4 * _PITCH_MM


def size(dpi: int) -> tuple:
    """(width, height) in dots: the one size a MaxiCode prints at."""
    pitch = _pitch(dpi)
    return (math.ceil(_WIDTH * pitch - 1e-9), math.ceil(_HEIGHT * pitch - 1e-9))


def raster(grid: list, dpi: int) -> np.ndarray:
    """The symbol as dots at this resolution, dark where True.

    A dot is dark when its centre falls in a dark hexagon or a dark ring of
    the bullseye. Each dot is tested against the three rows of hexagons it
    could be under, all at once.
    """
    pitch = _pitch(dpi)
    width, height = size(dpi)
    ys, xs = np.mgrid[0:height, 0:width]
    u = (xs + 0.5) / pitch
    v = (ys + 0.5) / pitch
    modules = np.array(grid, dtype=bool)
    dark = np.zeros((height, width), dtype=bool)

    nearest = np.rint((v - _CELL / 2) / _ROW).astype(int)
    for step in (-1, 0, 1):
        row = nearest + step
        within = (row >= 0) & (row < ROWS)
        row = np.clip(row, 0, ROWS - 1)
        offset = 0.5 * (row & 1)
        column = np.rint(u - 0.5 - offset).astype(int)
        within &= (column >= 0) & (column < COLUMNS)
        column = np.clip(column, 0, COLUMNS - 1)
        across = np.abs(u - (column + 0.5 + offset))
        down = np.abs(v - (_CELL / 2 + row * _ROW))
        inside = ((across <= _HEX * _SQRT3 / 4)
                  & (down <= _HEX / 2 - across / _SQRT3))
        dark |= within & inside & modules[row, column]

    distance = np.hypot(u - _CENTRE[0], v - _CENTRE[1])
    band = np.floor((distance - _INNER) / ((_OUTER - _INNER) / 5)).astype(int)
    dark |= (distance >= _INNER) & (distance < _OUTER) & (band % 2 == 0)
    return dark


def _runs(dark: np.ndarray) -> tuple:
    """Every horizontal run of dark dots, as (x, y, length)."""
    height, width = dark.shape
    edged = np.zeros((height, width + 2), dtype=np.int8)
    edged[:, 1:-1] = dark
    change = np.diff(edged, axis=1)
    runs = []
    for y in range(height):
        starts = np.flatnonzero(change[y] == 1)
        ends = np.flatnonzero(change[y] == -1)
        runs.extend((int(start), y, int(end - start))
                    for start, end in zip(starts, ends))
    return tuple(runs)


@functools.lru_cache(maxsize=64)
def symbol(data: str, mode: int, number: int, count: int, dpi: int) -> tuple:
    """(width, height, runs) for this field data at this resolution.

    Kept, because both canvases ask for it on every paint and a symbol that
    has not changed should not be encoded and drawn again each time.
    """
    width, height = size(dpi)
    return (width, height,
            _runs(raster(encode(data, mode, number, count), dpi)))
