"""Aztec Code encoder - returns the grid of dark modules for drawing.

Named for the bullseye at its centre, which is what a reader finds it by: it
needs no quiet zone, which is the whole point of the symbology and why it
turns up on train tickets and boarding passes where there is no room to
spare.

Around that bullseye sits a mode message saying how big the symbol is, and
around that the data spirals outward in layers, two modules to a ring. A
symbol comes in two shapes - compact, of one to four layers, and full-range,
of one to thirty-two - and a full-range symbol large enough grows a reference
grid through it, which the data has to be threaded around.
"""

# --- encodation -------------------------------------------------------------

# The five character modes, as the character each of their values stands for.
# A null is a value that switches mode rather than printing anything.
_UPPER = ("\0 ABCDEFGHIJKLMNOPQRSTUVWXYZ\0\0\0\0")
_LOWER = ("\0 abcdefghijklmnopqrstuvwxyz\0\0\0\0")
_MIXED = ("\0 \x01\x02\x03\x04\x05\x06\x07\x08\x09\x0a\x0b\x0c\x0d"
          "\x1b\x1c\x1d\x1e\x1f@\\^_`|~\x7f\0\0\0\0")
_PUNCT = ("\0\0\0\0\0\0!\"#$%&'()*+,-./:;<=>?[]{}\0")
_DIGIT = ("\0 0123456789,.\0\0")

_WIDTH = {'upper': 5, 'lower': 5, 'mixed': 5, 'punct': 5, 'digit': 4}
_TABLE = {'upper': _UPPER, 'lower': _LOWER, 'mixed': _MIXED,
          'punct': _PUNCT, 'digit': _DIGIT}
_MODES = ('upper', 'lower', 'mixed', 'punct', 'digit')

# The two-character sequences punct mode carries in a single value, which is
# what makes a sentence cheaper than its characters suggest.
_PAIRS = {'\r\n': 2, '. ': 3, ', ': 4, ': ': 5}

# What it costs to get from one mode to another: the values to emit, and
# whether the change lasts (a latch) or covers one character (a shift).
_LATCH = {
    ('upper', 'lower'): (28,), ('upper', 'mixed'): (29,),
    ('upper', 'digit'): (30,), ('upper', 'punct'): (29, 30),
    ('lower', 'mixed'): (29,), ('lower', 'digit'): (30,),
    ('lower', 'upper'): (29, 29), ('lower', 'punct'): (29, 30),
    ('mixed', 'lower'): (28,), ('mixed', 'upper'): (29,),
    ('mixed', 'punct'): (30,), ('mixed', 'digit'): (29, 30),
    ('punct', 'upper'): (31,), ('punct', 'lower'): (31, 28),
    ('punct', 'mixed'): (31, 29), ('punct', 'digit'): (31, 30),
    ('digit', 'upper'): (14,), ('digit', 'lower'): (14, 28),
    ('digit', 'mixed'): (14, 29), ('digit', 'punct'): (14, 29, 30),
}
_SHIFT = {('upper', 'punct'): 0, ('lower', 'punct'): 0, ('mixed', 'punct'): 0,
          ('digit', 'punct'): 0, ('lower', 'upper'): 28, ('digit', 'upper'): 15}
# Binary is a shift from any mode, followed by a length.
_BINARY_SHIFT = {'upper': 31, 'lower': 31, 'mixed': 31, 'punct': 31, 'digit': 14}


class _Bits:
    """The message as a string of bits, built a value at a time."""

    def __init__(self):
        self.bits = []

    def add(self, value: int, width: int) -> None:
        self.bits.extend((value >> shift) & 1
                         for shift in range(width - 1, -1, -1))

    def __len__(self):
        return len(self.bits)


def _find(mode: str, char: str):
    """The value this character has in this mode, or None."""
    if char == '\0':
        return None
    index = _TABLE[mode].find(char)
    return index if index > 0 else None


def _binary_run(text: str, start: int) -> int:
    """How many characters from here no mode can carry."""
    count = 0
    while start + count < len(text):
        char = text[start + count]
        if any(_find(mode, char) is not None for mode in _MODES):
            break
        count += 1
    return count


def _encode_text(text: str) -> list:
    """The message as bits: a value at a time, switching mode as needed.

    Greedy rather than optimal - a shift where one exists and only one
    character needs it, a latch otherwise - which is valid and within a few
    bits of the best for label data. Bytes no mode can carry go into a
    binary run, which carries its own length.
    """
    out = _Bits()
    mode = 'upper'
    index = 0
    while index < len(text):
        pair = text[index:index + 2]
        if mode == 'punct' and pair in _PAIRS:
            out.add(_PAIRS[pair], 5)
            index += 2
            continue

        char = text[index]
        value = _find(mode, char)
        if value is not None:
            out.add(value, _WIDTH[mode])
            index += 1
            continue

        wanted = next((name for name in _MODES
                       if _find(name, char) is not None), None)
        if wanted is None:
            index += _binary(out, text, index, mode)
            continue

        # A two-character punct sequence is worth a latch of its own.
        if wanted == 'punct' and text[index:index + 2] in _PAIRS:
            for step in _LATCH[(mode, 'punct')]:
                out.add(step, _WIDTH[mode] if step != 30 or mode != 'mixed' else 5)
            mode = 'punct'
            continue

        shift = _SHIFT.get((mode, wanted))
        nxt = text[index + 1:index + 2]
        if shift is not None and (not nxt or _find(mode, nxt) is not None):
            out.add(shift, _WIDTH[mode])
            out.add(_find(wanted, char), _WIDTH[wanted])
            index += 1
            continue

        current = mode
        for step in _LATCH[(current, wanted)]:
            out.add(step, _WIDTH[mode])
            # A two-step latch passes through a mode on the way; the second
            # value is spelled in the width of the one it lands in.
            mode = _through(mode, step)
        mode = wanted
    return out.bits


def _through(mode: str, value: int) -> str:
    """Which mode a latch value lands in, so the next one is the right width."""
    if mode == 'digit':
        return 'upper' if value == 14 else mode
    if mode == 'punct':
        return 'upper' if value == 31 else mode
    return {28: 'lower', 29: 'mixed' if mode != 'mixed' else 'upper',
            30: 'punct' if mode == 'mixed' else 'digit'}.get(value, mode)


def _binary(out: _Bits, text: str, index: int, mode: str) -> int:
    """A run of bytes no mode carries, with its own length in front.

    Up to 31 bytes are counted in five bits; a longer run spends eleven more
    and reaches 2047, which is past anything a label holds.
    """
    raw = text[index:index + _binary_run(text, index)].encode('utf-8')
    out.add(_BINARY_SHIFT[mode], _WIDTH[mode])
    if mode == 'digit':
        # Digit mode has no binary shift of its own, so it goes up first.
        out.add(31, 5)
    if len(raw) <= 31:
        out.add(len(raw), 5)
    else:
        out.add(0, 5)
        out.add(min(len(raw), 2047) - 31, 11)
    for byte in raw[:2047]:
        out.add(byte, 8)
    return _binary_run(text, index)


# --- sizes ------------------------------------------------------------------

# The correction ^B0 spends when its own d parameter says nothing: the
# specification's own recommendation, a little under a quarter of the symbol.
DEFAULT_PERCENT = 23


def _codeword_bits(layers: int, compact: bool) -> int:
    """How many bits a codeword holds at this size."""
    if layers <= 2:
        return 6
    if layers <= 8:
        return 8
    if layers <= 22:
        return 10
    return 12


def _capacity(layers: int, compact: bool) -> int:
    """How many bits the spiral holds at this size."""
    return ((88 if compact else 112) + 16 * layers) * layers


def _stuffed(bits: list, width: int) -> list:
    """The message as codewords, with the standard's bit stuffing.

    No codeword may be all ones or all zeros - a reader uses those to find
    its way - so each one is built from the first width-1 bits of what is
    left: if they are all the same, the complement is pushed in behind them
    and the stream has not moved on a whole codeword; otherwise the next bit
    of the stream finishes it off.

    A stuffed message is therefore longer than the data by an amount that
    depends on the data, which is why the size has to be found by trying
    each one rather than worked out.
    """
    out = []
    index = 0
    while index < len(bits):
        chunk = bits[index:index + width - 1]
        if len(chunk) < width - 1:
            # The last codeword is padded with ones - and if that made it
            # all ones, the last of them becomes a zero.
            chunk = chunk + [1] * (width - 1 - len(chunk))
            out.extend(chunk)
            out.append(0 if all(chunk) else 1)
            return out
        if all(chunk):
            out.extend(chunk + [0])
            index += width - 1
        elif not any(chunk):
            out.extend(chunk + [1])
            index += width - 1
        elif index + width - 1 < len(bits):
            out.extend(chunk + [bits[index + width - 1]])
            index += width
        else:
            # Exactly width-1 bits left, and mixed: a one finishes it.
            out.extend(chunk + [1])
            return out
    return out


def _choose(bits: list, layers: int = 0, compact: bool = None,
            percent: int = 23):
    """The smallest symbol that holds this message with the correction asked
    for, as (layers, compact, codewords)."""
    shapes = []
    if compact is None:
        shapes = [(n, True) for n in range(1, 5)] + [(n, False) for n in range(1, 33)]
    else:
        shapes = [(n, compact) for n in range(1, 5 if compact else 33)]
    if layers:
        shapes = [(n, c) for n, c in shapes if n == layers]

    for count, is_compact in sorted(shapes, key=lambda s: _capacity(*s)):
        width = _codeword_bits(count, is_compact)
        stuffed = _stuffed(bits, width)
        total = _capacity(count, is_compact) // width
        # The message plus its correction, rounded up to whole codewords.
        needed = -(-len(stuffed) // width)
        checks = max(3, -(-(total * percent) // 100) + 3)
        if needed + checks <= total:
            return count, is_compact, total, width
    raise ValueError(
        f"{len(bits)} bits do not fit any Aztec symbol at {percent}% correction")


# --- Reed-Solomon -----------------------------------------------------------

# The primitive polynomial for each codeword width, which is a different
# Galois field for each: 6, 8, 10 and 12 bits.
_POLY = {4: 0x13, 6: 0x43, 8: 0x12D, 10: 0x409, 12: 0x1069}


def _field(width: int):
    """Exponent and logarithm tables for the field of this width."""
    size = 1 << width
    exp = [0] * (size * 2)
    log = [0] * size
    value = 1
    for power in range(size - 1):
        exp[power] = value
        log[value] = power
        value <<= 1
        if value & size:
            value ^= _POLY[width]
    for power in range(size - 1, size * 2):
        exp[power] = exp[power - (size - 1)]
    return exp, log


def error_codewords(data: list, count: int, width: int) -> list:
    """The Reed-Solomon check codewords over the field of this width."""
    exp, log = _field(width)

    def multiply(a, b):
        # The exponent table is written out twice over, so the sum of two
        # logarithms indexes it directly.
        return 0 if a == 0 or b == 0 else exp[log[a] + log[b]]

    poly = [1]
    for power in range(1, count + 1):
        poly = [0] + poly
        for index in range(len(poly) - 1):
            poly[index] ^= multiply(poly[index + 1], exp[power])

    remainder = [0] * count
    for value in data:
        factor = value ^ remainder[0]
        remainder = remainder[1:] + [0]
        if factor:
            for index in range(count):
                remainder[index] ^= multiply(poly[count - 1 - index], factor)
    return remainder


# --- the symbol -------------------------------------------------------------

def _mode_message(layers: int, codewords: int, compact: bool) -> list:
    """The ring around the bullseye: how many layers, and how many codewords.

    Its own Reed-Solomon is over GF(16), four bits at a time, whatever size
    the symbol itself uses.
    """
    if compact:
        value = ((layers - 1) << 6) | (codewords - 1)
        words = [(value >> shift) & 0xF for shift in (4, 0)]
        checks = 5
    else:
        value = ((layers - 1) << 11) | (codewords - 1)
        words = [(value >> shift) & 0xF for shift in (12, 8, 4, 0)]
        checks = 6
    words = words + error_codewords(words, checks, 4)
    bits = []
    for word in words:
        bits.extend((word >> shift) & 1 for shift in (3, 2, 1, 0))
    return bits


def encode(data: str, layers: int = 0, compact: bool = None,
           percent: int = 23) -> list:
    """The symbol as rows of booleans, one per module, dark where True."""
    bits = _encode_text(data)
    layers, compact, total, width = _choose(bits, layers, compact, percent)

    stuffed = _stuffed(bits, width)
    words = []
    for index in range(0, len(stuffed), width):
        chunk = stuffed[index:index + width]
        while len(chunk) < width:
            chunk.append(1)
        words.append(int(''.join(str(bit) for bit in chunk), 2))
    # Every codeword the symbol holds is used: whatever is not data is
    # correction, and the mode message is what says where the line between
    # them falls.
    carried = len(words)
    words += error_codewords(words, total - carried, width)

    # A layer's capacity is rarely a whole number of codewords. The few bits
    # left over are zeros at the *start* of the spiral, not the end - so a
    # symbol whose capacity happened to divide evenly worked while every
    # other size drew its data one or two modules around the ring from where
    # a reader looks for it.
    payload = [0] * (_capacity(layers, compact) % width)
    for word in words:
        payload.extend((word >> shift) & 1 for shift in range(width - 1, -1, -1))

    # The grid the data spirals through, and the grid that is actually drawn.
    # For a full-range symbol they are not the same: a reference grid runs
    # through it every sixteen modules, and `spots` is where each coordinate
    # of the first lands in the second.
    base = (11 if compact else 14) + layers * 4
    size, spots = _alignment(base, compact)
    grid = [[False] * size for _ in range(size)]
    centre = size // 2

    # Order matters: each of these is drawn over the last. The data goes
    # down first and the finder patterns on top of it, because a reader
    # finds the symbol by those - a data bit that landed on the bullseye
    # would be a data bit lost, which the error correction covers, where a
    # broken bullseye is a symbol nothing can find at all.
    _place_data(grid, payload, layers, compact, base, spots)
    _place_mode(grid, _mode_message(layers, carried, compact), centre,
                compact)
    _place_bullseye(grid, centre, 5 if compact else 7)
    if not compact:
        _place_reference(grid, size, base)
    return grid


def _alignment(base: int, compact: bool):
    """The symbol's real size, and where each coordinate of the spiral's own
    grid sits inside it.

    A compact symbol is the one grid. A full-range one is wider by a
    reference grid line every sixteen modules, which the data has to be
    threaded around - so the two are not the same grid, and this is the map
    between them.
    """
    if compact:
        return base, list(range(base))
    size = base + 1 + 2 * ((base // 2 - 1) // 15)
    spots = [0] * base
    middle, centre = base // 2, size // 2
    for index in range(middle):
        offset = index + index // 15
        spots[middle - index - 1] = centre - offset - 1
        spots[middle + index] = centre + offset + 1
    return size, spots


def _place_bullseye(grid, centre: int, edge: int) -> None:
    """The rings a reader finds the symbol by, and the orientation marks at
    their corners.

    The rings alternate dark and light outward from a dark centre. The marks
    are three modules at the top left, two at the top right and one at the
    bottom right, and none at the bottom left - four different corners, which
    is what says which way up the symbol is.
    """
    for radius in range(0, edge, 2):
        for offset in range(centre - radius, centre + radius + 1):
            grid[centre - radius][offset] = True
            grid[centre + radius][offset] = True
            grid[offset][centre - radius] = True
            grid[offset][centre + radius] = True

    grid[centre - edge][centre - edge] = True
    grid[centre - edge][centre - edge + 1] = True
    grid[centre - edge + 1][centre - edge] = True
    grid[centre - edge][centre + edge] = True
    grid[centre - edge + 1][centre + edge] = True
    grid[centre + edge - 1][centre + edge] = True


def _place_reference(grid, size: int, base: int) -> None:
    """The reference grid a full-range symbol carries, so a reader can find
    its way across a symbol too big to trust the bullseye alone for."""
    centre = size // 2
    step = 0
    for _line in range(0, base // 2 - 1, 15):
        for offset in range(centre & 1, size, 2):
            grid[centre - step][offset] = True
            grid[centre + step][offset] = True
            grid[offset][centre - step] = True
            grid[offset][centre + step] = True
        step += 16


def _place_mode(grid, bits, centre, compact) -> None:
    """The mode message, on the ring just outside the bullseye."""
    if compact:
        for index in range(7):
            offset = centre - 3 + index
            if bits[index]:
                grid[centre - 5][offset] = True
            if bits[index + 7]:
                grid[offset][centre + 5] = True
            if bits[20 - index]:
                grid[centre + 5][offset] = True
            if bits[27 - index]:
                grid[offset][centre - 5] = True
        return
    for index in range(10):
        offset = centre - 5 + index + index // 5
        if bits[index]:
            grid[centre - 7][offset] = True
        if bits[index + 10]:
            grid[offset][centre + 7] = True
        if bits[29 - index]:
            grid[centre + 7][offset] = True
        if bits[39 - index]:
            grid[offset][centre - 7] = True


def _place_data(grid, bits, layers, compact, base, spots) -> None:
    """The data, spiralling outward two modules to a layer.

    Each layer is a ring drawn in four runs - top, right, bottom, left - of
    equal length, and each run is two modules deep. The bits go round in that
    order, so consecutive ones end up on opposite sides of the symbol.
    """
    start = 0
    for layer in range(layers):
        length = (layers - layer) * 4 + (9 if compact else 12)
        for step in range(length):
            column = step * 2
            for side in range(2):
                near_side = layer * 2 + side
                near_step = layer * 2 + step
                far_side = base - 1 - layer * 2 - side
                far_step = base - 1 - layer * 2 - step
                for run, (row, col) in enumerate((
                        (near_step, near_side),      # down the top edge
                        (far_side, near_step),       # across the right
                        (far_step, far_side),        # back along the bottom
                        (near_side, far_step))):     # up the left
                    index = start + length * 2 * run + column + side
                    if index < len(bits) and bits[index]:
                        grid[spots[row]][spots[col]] = True
        start += length * 8
