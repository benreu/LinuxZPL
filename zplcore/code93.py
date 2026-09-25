"""Code 93 barcode encoder - returns module widths for drawing.

Six elements a character - three bars and three spaces - in nine modules,
against Code 39's nine elements in twelve to fifteen. That density is the
point of it: the same applications, a third less label.

Unlike Code 39 its two check characters are not optional. ^BA's own e
parameter says whether the interpretation line *shows* them, not whether the
symbol carries them; a Code 93 symbol always does.
"""

# Every character, as its nine modules: bar, space, bar, space, bar, space.
# The four control characters at the end are ZPL's own substitutes - it has
# no way to send a Ctrl-$ in a ^FD, so it spells the shift characters &, ', (
# and ) instead, and the manual's table 5 and 6 say which pair means what.
ALPHABET = ("0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ-. $/+%"
            "\x00\x01\x02\x03")   # the four shifts, in ZPL's &'() order

_PATTERNS = (
    "131112", "111213", "111312", "111411", "121113", "121212", "121311",
    "111114", "131211", "141111", "211113", "211212", "211311", "221112",
    "221211", "231111", "112113", "112212", "112311", "122112", "132111",
    "111123", "111222", "111321", "121122", "131121", "212112", "212211",
    "211122", "211221", "221121", "222111", "112122", "112221", "122121",
    "123111", "121131", "311112", "311211", "321111", "112131", "113121",
    "211131",
    # the four shift characters: ($), (%), (/) and (+) in the manual's tables
    "121221", "312111", "311121", "122211",
)
_START_STOP = "111141"
# Code 93 ends on a single narrow bar after the stop character, which is what
# stops the stop pattern's last space running into the quiet zone.
_TERMINATOR = "1"

# ZPL's substitutes for the four control shifts, in the order _PATTERNS has
# them: the manual's "Ctrl $ = &", "Ctrl % = '", "Ctrl / = (" and
# "Ctrl + = )".
SUBSTITUTES = "&'()"


def _values(data: str) -> list:
    """Each character's place in the alphabet, substitutes folded in.

    A character outside the set scores as a space, the way Code 39 draws one
    as a blank rather than refusing the barcode.
    """
    folded = []
    for char in data.upper():
        index = SUBSTITUTES.find(char)
        if index >= 0:
            folded.append(len(ALPHABET) - 4 + index)
        else:
            folded.append(ALPHABET.find(char) if char in ALPHABET
                          else ALPHABET.index(' '))
    return folded


def check_characters(data: str) -> str:
    """The two check characters, C and K, that every Code 93 symbol carries.

    C weights the data from the right by 1 to 20 and wraps; K does the same
    over the data plus C, by 1 to 15. Both are mod 47 - the full character
    set including the four shifts.
    """
    values = _values(data)
    out = ''
    for span in (20, 15):
        total = sum(value * (((len(values) - 1 - index) % span) + 1)
                    for index, value in enumerate(values))
        check = total % 47
        values.append(check)
        out += ALPHABET[check] if check < 43 else SUBSTITUTES[check - 43]
    return out


def encode(data: str) -> list:
    """Module widths for a Code 93 barcode, alternating bar/space."""
    values = _values(data)
    values += _values(check_characters(data))
    bits = _START_STOP + ''.join(_PATTERNS[value] for value in values)
    bits += _START_STOP + _TERMINATOR
    return [int(width) for width in bits]
