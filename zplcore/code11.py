"""Code 11 barcode encoder - returns module widths for drawing.

Also USD-8. Five elements a character - three bars and two spaces - over
eleven characters: the ten digits and the hyphen. It is not self-checking,
which is why its check digits are not optional the way Code 39's is: ^B1's
own e parameter chooses *how many* it carries, one or two, and the manual's
default is two.
"""

ALPHABET = "0123456789-"

# Each character's five elements, alternating bar and space, starting with a
# bar: 1 narrow, 2 wide.
_TABLE = {
    '0': (1, 1, 1, 1, 2), '1': (2, 1, 1, 1, 2), '2': (1, 2, 1, 1, 2),
    '3': (2, 2, 1, 1, 1), '4': (1, 1, 2, 1, 2), '5': (2, 1, 2, 1, 1),
    '6': (1, 2, 2, 1, 1), '7': (1, 1, 1, 2, 2), '8': (2, 1, 1, 2, 1),
    '9': (2, 1, 1, 1, 1), '-': (1, 1, 2, 1, 1),
}
_START_STOP = (1, 1, 2, 2, 1)


def _check(values: list, span: int) -> int:
    """One check character: the data weighted from the right by 1 up to
    `span`, wrapping, mod 11."""
    total = sum(value * (((len(values) - 1 - index) % span) + 1)
                for index, value in enumerate(values))
    return total % 11


def check_characters(data: str, count: int = 2) -> str:
    """The C check character, and the K one after it when `count` is two.

    C weights by 1 to 10 and K by 1 to 9, both mod 11 over the eleven-
    character set - so a check character can itself be the hyphen.
    """
    values = [ALPHABET.index(char) for char in data if char in ALPHABET]
    out = ''
    for span in (10, 9)[:max(0, count)]:
        check = _check(values, span)
        values.append(check)
        out += ALPHABET[check]
    return out


def normalize(data: str, count: int = 2) -> str:
    """The characters the symbol carries, check characters included."""
    kept = ''.join(char for char in data if char in ALPHABET)
    return kept + check_characters(kept, count)


def encode(data: str, count: int = 2) -> list:
    """Module widths for a Code 11 barcode, alternating bar/space."""
    characters = normalize(data, count)
    mods = list(_START_STOP)
    for char in characters:
        mods.append(1)
        mods.extend(_TABLE[char])
    mods.append(1)
    mods.extend(_START_STOP)
    return mods
