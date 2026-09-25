"""ANSI Codabar barcode encoder - returns module widths for drawing.

Seven elements a character - four bars and three spaces - and two character
sets: the sixteen that carry data, and the four (A, B, C, D) that can only
start or stop a symbol. ^BK names the start and stop separately, which is
the point of having four: a reader can be told which pair to expect.

The wide-to-narrow ratio ^BY carries is applied by the caller, the same way
Code 39's is; this table only says which elements are which category.
"""

# Each character's seven elements, alternating bar and space, starting with
# a bar: 1 narrow, 2 wide. The ten digits, the six symbols, then the four
# start/stop characters. Every data character has exactly two wide elements;
# the four symbols spend all three of theirs on bars, and the start and stop
# characters spend one on a bar and two on spaces, which is what makes them
# impossible to mistake for data.
_TABLE = {
    '0': (1, 1, 1, 1, 1, 2, 2), '1': (1, 1, 1, 1, 2, 2, 1),
    '2': (1, 1, 1, 2, 1, 1, 2), '3': (2, 2, 1, 1, 1, 1, 1),
    '4': (1, 1, 2, 1, 1, 2, 1), '5': (2, 1, 1, 1, 1, 2, 1),
    '6': (1, 2, 1, 1, 1, 1, 2), '7': (1, 2, 1, 1, 2, 1, 1),
    '8': (1, 2, 2, 1, 1, 1, 1), '9': (2, 1, 1, 2, 1, 1, 1),
    '-': (1, 1, 1, 2, 2, 1, 1), '$': (1, 1, 2, 2, 1, 1, 1),
    ':': (2, 1, 1, 1, 2, 1, 2), '/': (2, 1, 2, 1, 1, 1, 2),
    '.': (2, 1, 2, 1, 2, 1, 1), '+': (1, 1, 2, 1, 2, 1, 2),
    'A': (1, 1, 2, 2, 1, 2, 1), 'B': (1, 2, 1, 2, 1, 1, 2),
    'C': (1, 1, 1, 2, 1, 2, 2), 'D': (1, 1, 1, 2, 2, 2, 1),
}

START_STOP = ('A', 'B', 'C', 'D')
DEFAULT_START_STOP = 'A'


def normalize(data: str) -> str:
    """The data characters the symbol carries.

    The four start and stop characters are named by ^BK's own k and l
    parameters, so one typed into the field data is not one of those - it is
    a character Codabar cannot carry, and is dropped rather than drawn as a
    second start character in the middle of the symbol, which no reader
    would get past.
    """
    return ''.join(char for char in data.upper()
                   if char in _TABLE and char not in START_STOP)


def encode(data: str, start: str = DEFAULT_START_STOP,
           stop: str = DEFAULT_START_STOP) -> list:
    """Module widths for a Codabar barcode, alternating bar/space.

    A narrow space separates every pair of characters, as in Code 39: each
    character's own seven elements end on a bar and the next one's begin on
    a bar.
    """
    start = start.upper() if start.upper() in START_STOP else DEFAULT_START_STOP
    stop = stop.upper() if stop.upper() in START_STOP else DEFAULT_START_STOP
    characters = [start] + list(normalize(data)) + [stop]

    mods = list(_TABLE[characters[0]])
    for char in characters[1:]:
        mods.append(1)
        mods.extend(_TABLE[char])
    return mods
