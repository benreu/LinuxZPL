"""EAN-8 barcode encoder - returns module widths for drawing.

The shortened EAN: four digits either side of the centre guard rather than
six, and no parity pattern at all - the left-hand group is always the odd
set - so its eight digits cost sixty-seven modules where EAN-13's thirteen
cost ninety-five.
"""

from . import code128
from . import ean13

# The seven digits ^B8's field data carries, before its own check digit.
LENGTH = 7


def normalize(data: str) -> str:
    """The eight digits EAN-8 actually carries, check digit included."""
    base = ean13.fit(data, LENGTH)
    return base + code128.ucc_check_digit(base)


def encode(data: str) -> list:
    """Module widths for an EAN-8 barcode, alternating bar/space."""
    digits = normalize(data)
    bits = ean13.EDGE
    for digit in digits[:4]:
        bits += ean13.CODES['A'][int(digit)]
    bits += ean13.MIDDLE
    for digit in digits[4:]:
        bits += ean13.CODES['C'][int(digit)]
    bits += ean13.EDGE
    return ean13.bits_to_modules(bits)
