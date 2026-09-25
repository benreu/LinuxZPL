"""UPC-A barcode encoder - returns module widths for drawing.

UPC-A is EAN-13 with a leading zero: the same ninety-five modules, the same
guard patterns, and the same digit shapes. What differs is how it is written
and read - eleven data digits rather than twelve, and a check digit the
interpretation line may or may not show - so it is its own command (^BU) and
its own symbology here, encoding through ean13 rather than repeating it.
"""

from . import code128
from . import ean13

# The eleven digits ^BU's field data carries, before its own check digit.
LENGTH = 11


def normalize(data: str) -> str:
    """The twelve digits UPC-A actually carries, check digit included.

    The manual: field data is "limited to exactly 11 characters. ZPL II
    automatically truncates or pads on the left with zeros" - the same
    fitting EAN-13 does, one digit shorter.
    """
    base = ean13.fit(data, LENGTH)
    return base + code128.ucc_check_digit(base)


def encode(data: str) -> list:
    """Module widths for a UPC-A barcode, alternating bar/space.

    A UPC-A symbol is the EAN-13 symbol for the same digits with a zero in
    front: that leading zero is what chooses the all-odd parity pattern for
    the left-hand group, which is why UPC-A has no parity of its own to
    encode and only ever spells twelve digits in ninety-five modules.
    """
    return ean13.encode('0' + ean13.fit(data, LENGTH))
