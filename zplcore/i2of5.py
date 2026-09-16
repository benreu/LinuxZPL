"""Interleaved 2 of 5 barcode encoder - returns module widths for drawing."""

# Each digit is five elements, all bars or all spaces - the symbol
# interleaves one digit's bars with the next digit's spaces, which is where
# the name comes from. Two of the five are wide (2); the rest are narrow
# (1). The actual wide-to-narrow ratio ^BY carries is applied by the caller.
_TABLE = {
    '0': (1, 1, 2, 2, 1), '1': (2, 1, 1, 1, 2), '2': (1, 2, 1, 1, 2),
    '3': (2, 2, 1, 1, 1), '4': (1, 1, 2, 1, 2), '5': (2, 1, 2, 1, 1),
    '6': (1, 2, 2, 1, 1), '7': (1, 1, 1, 2, 2), '8': (2, 1, 1, 2, 1),
    '9': (1, 2, 1, 2, 1),
}
_START = (1, 1, 1, 1)  # bar, space, bar, space, all narrow
_STOP = (2, 1, 1)      # wide bar, narrow space, narrow bar


def normalize(data: str) -> str:
    """`data`'s digits, with a leading zero if there is an odd number of them.

    Two digits share every symbol - one in its bars, one in its spaces - so
    an odd count cannot be interleaved at all. ZPL adds the zero itself
    rather than rejecting the field.
    """
    digits = ''.join(ch for ch in data if ch.isdigit())
    return digits if len(digits) % 2 == 0 else '0' + digits


def encode(data: str) -> list:
    """Module widths for an Interleaved 2 of 5 barcode, alternating bar/space."""
    digits = normalize(data)
    mods = list(_START)
    for i in range(0, len(digits), 2):
        bars, spaces = _TABLE[digits[i]], _TABLE[digits[i + 1]]
        for bar, space in zip(bars, spaces):
            mods.append(bar)
            mods.append(space)
    mods.extend(_STOP)
    return mods
