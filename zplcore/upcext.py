"""UPC/EAN Extension barcode encoder - returns module widths for drawing.

The two-digit and five-digit add-on symbols publishers use for a magazine
issue or a book's suggested price. Both draw the same per-digit shapes as
EAN-13's own left-hand digits, so this module reuses ean13's tables rather
than repeating them.
"""

from . import ean13

_GUARD = "01011"      # space, bar, space, bar - the last one wide
_SEPARATOR = "01"      # space, bar - between every pair of digits

# Two digits: the value they spell, mod 4, picks the parity pattern.
_TWO_DIGIT_PARITY = ("LL", "LG", "GL", "GG")
# Five digits: a checksum of the digits themselves picks it, out of ten.
_FIVE_DIGIT_PARITY = ("GGLLL", "GLGLL", "GLLGL", "GLLLG", "LGGLL",
                      "LLGGL", "LLLGG", "LGLGL", "LGLLG", "LLGLG")


def _target_length(data: str) -> int:
    """Two digits unless there are more than that to fit, then five."""
    digits = ''.join(ch for ch in data if ch.isdigit())
    return 2 if len(digits) <= 2 else 5


def normalize(data: str) -> str:
    """`data`'s digits, fit to whichever extension length they call for."""
    return ean13.fit(data, _target_length(data))


def _parity(full: str) -> str:
    digits = [int(ch) for ch in full]
    if len(full) == 2:
        return _TWO_DIGIT_PARITY[(digits[0] * 10 + digits[1]) % 4]
    checksum = (3 * (digits[0] + digits[2] + digits[4])
                + 9 * (digits[1] + digits[3])) % 10
    return _FIVE_DIGIT_PARITY[checksum]


def encode(data: str) -> list:
    """Module widths for a UPC/EAN extension, alternating bar/space.

    There is no check digit and no end guard - the symbol simply stops
    after its last digit.
    """
    full = normalize(data)
    parity = _parity(full)
    bits = _GUARD
    for i, (code, digit) in enumerate(zip(parity, full)):
        if i:
            bits += _SEPARATOR
        # L-code and G-code are this family's usual names for what ean13
        # itself calls A and B - odd and even parity for the same digit.
        bits += ean13.CODES['A' if code == 'L' else 'B'][int(digit)]
    return ean13.bits_to_modules(bits)
