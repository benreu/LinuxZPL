"""Code 39 barcode encoder - returns module widths for drawing."""

# Each character is nine elements - five bars, four spaces, alternating and
# starting with a bar - three of them wide (2) and six narrow (1). The
# specific wide-to-narrow ratio ^BY carries is applied by the caller; this
# table only says which elements are which category.
_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ-. $/+%"

_TABLE = {
    '0': (1, 1, 1, 2, 2, 1, 2, 1, 1), '1': (2, 1, 1, 2, 1, 1, 1, 1, 2),
    '2': (1, 1, 2, 2, 1, 1, 1, 1, 2), '3': (2, 1, 2, 2, 1, 1, 1, 1, 1),
    '4': (1, 1, 1, 2, 2, 1, 1, 1, 2), '5': (2, 1, 1, 2, 2, 1, 1, 1, 1),
    '6': (1, 1, 2, 2, 2, 1, 1, 1, 1), '7': (1, 1, 1, 2, 1, 1, 2, 1, 2),
    '8': (2, 1, 1, 2, 1, 1, 2, 1, 1), '9': (1, 1, 2, 2, 1, 1, 2, 1, 1),
    'A': (2, 1, 1, 1, 1, 2, 1, 1, 2), 'B': (1, 1, 2, 1, 1, 2, 1, 1, 2),
    'C': (2, 1, 2, 1, 1, 2, 1, 1, 1), 'D': (1, 1, 1, 1, 2, 2, 1, 1, 2),
    'E': (2, 1, 1, 1, 2, 2, 1, 1, 1), 'F': (1, 1, 2, 1, 2, 2, 1, 1, 1),
    'G': (1, 1, 1, 1, 1, 2, 2, 1, 2), 'H': (2, 1, 1, 1, 1, 2, 2, 1, 1),
    'I': (1, 1, 2, 1, 1, 2, 2, 1, 1), 'J': (1, 1, 1, 1, 2, 2, 2, 1, 1),
    'K': (2, 1, 1, 1, 1, 1, 1, 2, 2), 'L': (1, 1, 2, 1, 1, 1, 1, 2, 2),
    'M': (2, 1, 2, 1, 1, 1, 1, 2, 1), 'N': (1, 1, 1, 1, 2, 1, 1, 2, 2),
    'O': (2, 1, 1, 1, 2, 1, 1, 2, 1), 'P': (1, 1, 2, 1, 2, 1, 1, 2, 1),
    'Q': (1, 1, 1, 1, 1, 1, 2, 2, 2), 'R': (2, 1, 1, 1, 1, 1, 2, 2, 1),
    'S': (1, 1, 2, 1, 1, 1, 2, 2, 1), 'T': (1, 1, 1, 1, 2, 1, 2, 2, 1),
    'U': (2, 2, 1, 1, 1, 1, 1, 1, 2), 'V': (1, 2, 2, 1, 1, 1, 1, 1, 2),
    'W': (2, 2, 2, 1, 1, 1, 1, 1, 1), 'X': (1, 2, 1, 1, 2, 1, 1, 1, 2),
    'Y': (2, 2, 1, 1, 2, 1, 1, 1, 1), 'Z': (1, 2, 2, 1, 2, 1, 1, 1, 1),
    '-': (1, 2, 1, 1, 1, 1, 2, 1, 2), '.': (2, 2, 1, 1, 1, 1, 2, 1, 1),
    ' ': (1, 2, 2, 1, 1, 1, 2, 1, 1), '$': (1, 2, 1, 2, 1, 2, 1, 1, 1),
    '/': (1, 2, 1, 2, 1, 1, 1, 2, 1), '+': (1, 2, 1, 1, 1, 2, 1, 2, 1),
    '%': (1, 1, 1, 2, 1, 2, 1, 2, 1),
}
_START_STOP = (1, 2, 1, 1, 2, 1, 2, 1, 1)  # the '*' character


def mod43_check_digit(data: str) -> str:
    """The Mod-43 check character for a Code 39 value.

    Each character's value is its position in the 43-character set - 0-9 are
    0-9, A-Z are 10-35, then the seven symbols are 36-42 in the order above.
    The check character is whichever one's value equals the sum of the
    data's own values, mod 43. Characters outside the set score nothing,
    the same way an out-of-range one is drawn as a blank in `encode`.
    """
    total = sum(_ALPHABET.index(ch) for ch in data.upper() if ch in _ALPHABET)
    return _ALPHABET[total % 43]


def encode(data: str) -> list:
    """Module widths for a Code 39 barcode, alternating bar/space.

    The start and stop character (*) is generated automatically, on both
    ends, rather than written into the field data. A character's own nine
    elements end on a bar, and the next one's begin on a bar too, so a
    narrow inter-character gap - one more space - separates every pair of
    them, the start and stop characters included.
    """
    patterns = ([_START_STOP] + [_TABLE.get(ch, _TABLE[' ']) for ch in data.upper()]
                + [_START_STOP])
    mods = list(patterns[0])
    for pattern in patterns[1:]:
        mods.append(1)
        mods.extend(pattern)
    return mods
