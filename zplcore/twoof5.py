"""Industrial and Standard 2 of 5 - returns module widths for drawing.

The oldest of the 2 of 5 family, and the simplest: all of the information is
in the bars, every space is narrow, and each digit is five bars of which two
are wide - which is what "2 of 5" names. Interleaved 2 of 5 (i2of5.py) packs
a second digit into the spaces; these two do not, so they are half as dense
and twice as forgiving.

The two differ only in how they start and stop. ^BI's Industrial start is
two wide bars and a narrow one; ^BJ's Standard start is the other way about.
Both are drawn here rather than in two files, because a single digit table
and a single loop is the whole of both.

The wide-to-narrow ratio ^BY carries is applied by the caller, as Code 39's
is; this table only says which bars are wide.
"""

# Each digit's five bars: 1 narrow, 2 wide. Every space between them is
# narrow, and is added by `encode`.
_BARS = {
    '0': (1, 1, 2, 2, 1), '1': (2, 1, 1, 1, 2), '2': (1, 2, 1, 1, 2),
    '3': (2, 2, 1, 1, 1), '4': (1, 1, 2, 1, 2), '5': (2, 1, 2, 1, 1),
    '6': (1, 2, 2, 1, 1), '7': (1, 1, 1, 2, 2), '8': (2, 1, 1, 2, 1),
    '9': (1, 2, 1, 2, 1),
}

# (start bars, stop bars) for each, as the manual names them: ^BI's are
# "internal" and ^BJ's "automatic", which is the same thing said twice - a
# symbol carries them and the field data never does.
#
# The manual prints neither pattern, and names both commands in words that
# fit either. ^BJ is drawn with the IATA start and stop - two narrow bars,
# and a wide and a narrow - because the only other reading of "Standard 2 of
# 5" is the Matrix variant, whose start carries a wide *space*, and ^BJ is
# explicit that "all of the information is contained in the bars". See
# FUNCTIONAL_SPEC.md section 18.
GUARDS = {
    'industrial2of5': ((2, 2, 1), (2, 1, 2)),
    'standard2of5': ((1, 1), (2, 1)),
}


def normalize(data: str) -> str:
    """The digits the symbol carries. Both are numeric only."""
    return ''.join(char for char in data if char.isdigit())


def encode(data: str, kind: str = 'industrial2of5') -> list:
    """Module widths for an Industrial or Standard 2 of 5 barcode,
    alternating bar/space."""
    start, stop = GUARDS[kind]
    bars = list(start)
    for digit in normalize(data):
        bars.extend(_BARS[digit])
    bars.extend(stop)

    # Every bar is followed by a narrow space, bar the last one - which ends
    # the symbol, and would otherwise run a space into the quiet zone.
    mods = []
    for index, bar in enumerate(bars):
        mods.append(bar)
        if index < len(bars) - 1:
            mods.append(1)
    return mods
