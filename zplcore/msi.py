"""MSI barcode encoder - returns module widths for drawing.

A variant of Plessey, and the one that outlived it: four bars and four
spaces a character, every bit of every digit drawn as a wide bar and a
narrow space (1) or a narrow bar and a wide space (0). It is not
self-checking, which is why ^BM's e parameter offers four different
checksums rather than a yes or a no.
"""

# Each bit as its modules, and the two guards. Everything MSI draws is one
# of these four, which is what makes it a pulse-width code rather than a
# table of characters.
_ONE = "110"
_ZERO = "100"
_START = "110"
_STOP = "1001"

# ^BM's e: which checksum, if any, the symbol carries.
SCHEMES = ('A', 'B', 'C', 'D')
DEFAULT_SCHEME = 'B'


def _mod10(digits: str) -> str:
    """The Luhn-style mod 10 check digit: every second digit from the right
    doubled, the doubled ones' own digits summed."""
    total = 0
    for index, char in enumerate(reversed(digits)):
        value = int(char)
        if index % 2 == 0:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return str((10 - total % 10) % 10)


def _mod11(digits: str) -> str:
    """The mod 11 check digit, weighted 2 to 7 from the right and wrapping.

    Eleven has no single digit, so a remainder that asks for one is taken as
    zero - the IBM convention, and the one MSI's own scheme D relies on to
    stay numeric.
    """
    total = sum(int(char) * (index % 6 + 2)
                for index, char in enumerate(reversed(digits)))
    return str((11 - total % 11) % 11 % 10)


def check_digits(data: str, scheme: str = DEFAULT_SCHEME) -> str:
    """The check digits this scheme adds: none, one mod 10, two mod 10, or a
    mod 11 followed by a mod 10."""
    scheme = scheme.upper() if scheme.upper() in SCHEMES else DEFAULT_SCHEME
    if scheme == 'A':
        return ''
    if scheme == 'B':
        return _mod10(data)
    if scheme == 'C':
        first = _mod10(data)
        return first + _mod10(data + first)
    first = _mod11(data)                                  # scheme D
    return first + _mod10(data + first)


def normalize(data: str, scheme: str = DEFAULT_SCHEME) -> str:
    """The digits the symbol carries, check digits included."""
    digits = ''.join(char for char in data if char.isdigit())
    return digits + check_digits(digits, scheme)


def encode(data: str, scheme: str = DEFAULT_SCHEME) -> list:
    """Module widths for an MSI barcode, alternating bar/space."""
    bits = _START
    for digit in normalize(data, scheme):
        # Four bits a digit, most significant first.
        bits += ''.join(_ONE if (int(digit) >> shift) & 1 else _ZERO
                        for shift in (3, 2, 1, 0))
    bits += _STOP
    return _runs(bits)


def _runs(bits: str) -> list:
    """A string of 1s and 0s as run lengths, starting with a bar."""
    mods, current, count = [], bits[0], 0
    for bit in bits:
        if bit == current:
            count += 1
        else:
            mods.append(count)
            current = bit
            count = 1
    mods.append(count)
    return mods
