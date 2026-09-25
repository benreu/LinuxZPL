"""POSTAL barcode encoders - returns each bar's extent for drawing.

The postal codes are the one family here that does not vary bar *width*. Every
bar is narrow and every gap between them is the same; what carries the data is
how tall each bar is and where it sits. POSTNET and PLANET have two heights -
a full bar and a short one standing on the baseline - and the USPS Intelligent
Mail barcode has four, adding a tracker that touches neither edge and an
ascender that touches only the top.

So these encoders return, per bar, the fraction of the symbol's height the bar
starts and ends at: (0.0, 1.0) is a full bar and (0.6, 1.0) a short one.
`geometry.barcode_rects` turns those into rectangles like any other symbol.
"""

from . import code128

# POSTNET's five bars a digit: 1 is a full-height bar, 0 a short one. Two of
# the five are always full, which is what makes a misread detectable.
_POSTNET = ("11000", "00011", "00101", "00110", "01001",
            "01010", "01100", "10001", "10010", "10100")

# PLANET is POSTNET inverted - three full bars a digit rather than two - which
# is how a sorting machine tells one from the other at a glance.
_PLANET = tuple(''.join('1' if bit == '0' else '0' for bit in pattern)
                for pattern in _POSTNET)

# A short bar stands on the baseline and is two fifths of the full height:
# the USPS figures are 0.050 inches against 0.125.
SHORT = 0.6

# The Intelligent Mail barcode's four states, as the extent of each: a full
# bar, an ascender that stops short of the baseline, a descender that starts
# below the top, and a tracker that is only the middle third.
_STATES = {'F': (0.0, 1.0), 'A': (0.0, 2 / 3), 'D': (1 / 3, 1.0),
           'T': (1 / 3, 2 / 3)}

# ^BZ's t: which postal code. 2 is reserved and draws nothing, which is what
# the manual leaves it to mean.
TYPES = {'0': 'postnet', '1': 'planet', '3': 'intelligent_mail'}
DEFAULT_TYPE = '0'


def check_digit(digits: str) -> str:
    """The postal check digit: whatever brings the digit sum to a multiple
    of ten. Shared by POSTNET and PLANET."""
    total = sum(int(char) for char in digits)
    return str((10 - total % 10) % 10)


def normalize(data: str, kind: str = 'postnet') -> str:
    """The digits the symbol carries, check digit included.

    The Intelligent Mail barcode has no check digit of its own - its
    eleven-bit cyclic redundancy check is folded into the bars themselves -
    and takes twenty digits of tracking plus a routing code of nothing, five,
    nine or eleven.
    """
    digits = ''.join(char for char in data if char.isdigit())
    if kind == 'intelligent_mail':
        return digits
    return digits + check_digit(digits)


def encode(data: str, kind: str = 'postnet') -> list:
    """Each bar's (top, bottom) as a fraction of the symbol's height."""
    if kind not in ('postnet', 'planet', 'intelligent_mail'):
        # ^BZ's type 2, which the manual gives as "Reserved" and leaves
        # undefined. Nothing is drawn, rather than a Postnet symbol the file
        # did not ask for.
        raise ValueError(f"^BZ type {kind!r} is reserved, and draws nothing")
    if kind == 'intelligent_mail':
        return _intelligent_mail(normalize(data, kind))

    table = _PLANET if kind == 'planet' else _POSTNET
    digits = normalize(data, kind)
    # A frame bar either end, full height, which is what a reader finds the
    # symbol and its pitch by.
    pattern = '1' + ''.join(table[int(digit)] for digit in digits) + '1'
    return [(0.0, 1.0) if bit == '1' else (SHORT, 1.0) for bit in pattern]


def _intelligent_mail(digits: str) -> list:
    """The sixty-five bars of a USPS Intelligent Mail barcode.

    The conversion from twenty digits of tracking and up to eleven of routing
    to sixty-five four-state bars is a long one - a 102-bit integer, an
    eleven-bit cyclic redundancy check, ten codewords, and a table of
    thirteen-bit characters - and it is somebody else's already: reportlab
    carries it, and returns the bars as a string of T, D, A and F.
    """
    from reportlab.graphics.barcode.usps4s import USPS_4State

    tracking = digits[:20].rjust(20, '0')
    routing = digits[20:]
    if len(routing) not in (0, 5, 9, 11):
        routing = routing[:11] if len(routing) > 11 else ''
    return [_STATES[state]
            for state in USPS_4State(tracking, routing).barcodes]
