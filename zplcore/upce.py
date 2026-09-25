"""UPC-E barcode encoder - returns module widths for drawing.

UPC-E is UPC-A with its zeros squeezed out: six digits where UPC-A has
eleven, in fifty-one modules where UPC-A has ninety-five. Which zeros may be
squeezed out is a table, and it runs both ways - the field data is the full
ten-digit manufacturer-and-product number, as the manual insists ("you must
enter the full 10-character sequence. ZPL II calculates and prints the
shortened version"), and the symbol is what is left of it.

There is no module width to spare for the check digit, so it is encoded the
way EAN-13 encodes its thirteenth digit: as the pattern of odd and even
parities across the six digits that are drawn.
"""

from . import code128
from . import ean13

# The ten digits ^B9's field data carries: five of manufacturer's code and
# five of product code.
LENGTH = 10

# Which of the two parity sets each of the six digits uses, keyed by the
# check digit - and then by the number system, which inverts it. This is the
# whole of UPC-E's error detection: the digits themselves are the odd (A)
# and even (B) shapes EAN-13 already has.
_PARITY = ("EEEOOO", "EEOEOO", "EEOOEO", "EEOOOE", "EOEEOO",
           "EOOEEO", "EOOOEE", "EOEOEO", "EOEOOE", "EOOEOE")

EDGE = "101"
# UPC-E ends on six modules of guard rather than three - there is no
# right-hand group for a centre guard to separate.
END = "010101"


def compress(data: str) -> str:
    """The six digits UPC-E draws, from the ten UPC-A digits it is given.

    The four rules are the manual's own ("Rules for Proper Product Code
    Numbers"), read the other way round: a product code of 00000-00999
    against a manufacturer ending 000, 100 or 200 is the case that squeezes
    hardest, and a manufacturer not ending in zero at all is the case that
    barely squeezes.

    A number that fits none of them raises, and no symbol is drawn. Drawing
    its last six digits anyway would produce a perfectly scannable barcode
    for a different product code - the expansion is a fixed table, so six
    digits chosen freely expand to something that is not what was typed -
    and a barcode that reads as the wrong thing is worse than one that is
    not there.
    """
    digits = ''.join(ch for ch in data if ch.isdigit())[-LENGTH:].rjust(LENGTH, '0')
    manufacturer, product = digits[:5], digits[5:]

    if manufacturer[2] in '012' and manufacturer[3:] == '00' and product[:2] == '00':
        # a manufacturer ending 000, 100 or 200, with a product code under 1000
        return manufacturer[:2] + product[2:] + manufacturer[2]
    if manufacturer[3:] == '00' and product[:3] == '000':
        # one ending in two zeros, with a product code under 100
        return manufacturer[:3] + product[3:] + '3'
    if manufacturer[4] == '0' and product[:4] == '0000':
        # ....0 with a product code under 10
        return manufacturer[:4] + product[4] + '4'
    if product[:4] == '0000' and product[4] in '56789':
        # a manufacturer not ending in zero, product 00005-00009
        return manufacturer + product[4]
    raise ValueError(
        f"{digits} is not a number UPC-E can shorten. A UPC-E product code "
        "must be 00000-00999 for a manufacturer ending 000, 100 or 200; "
        "00000-00099 for one ending in two zeros; 00000-00009 for one "
        "ending in one; or 00005-00009 otherwise.")


def normalize(data: str) -> str:
    """What the interpretation line shows: the number system, the six drawn
    digits, and the check digit - which is UPC-A's, computed on the long
    number, not on the short one."""
    digits = ''.join(ch for ch in data if ch.isdigit())[-LENGTH:].rjust(LENGTH, '0')
    # The number system is 0 or 1; ^B9's own field data carries neither, so
    # it is 0, which is what the manual means by "used for number system 0".
    return '0' + compress(digits) + code128.ucc_check_digit('0' + digits)


def encode(data: str) -> list:
    """Module widths for a UPC-E barcode, alternating bar/space."""
    value = normalize(data)
    system, body, check = value[0], value[1:7], value[7]
    parity = _PARITY[int(check)]
    if system == '1':
        # Number system 1 is number system 0's parity inverted.
        parity = ''.join('O' if letter == 'E' else 'E' for letter in parity)

    bits = EDGE
    for letter, digit in zip(parity, body):
        # O is EAN's odd set, which it calls A; E is the even set, B.
        bits += ean13.CODES['A' if letter == 'O' else 'B'][int(digit)]
    bits += END
    return ean13.bits_to_modules(bits)
