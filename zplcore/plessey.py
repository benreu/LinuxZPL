"""Plessey barcode encoder - returns module widths for drawing.

The oldest of the pulse-width codes, and the one MSI is a variant of. Each
character is a hexadecimal digit sent as four bits, least significant first,
and each bit is a wide bar and a narrow space (1) or a narrow bar and a wide
space (0). What it has that MSI does not is a cyclic redundancy check: eight
bits generated over the whole message, which is not optional and which ^BP's
own e parameter therefore only chooses whether to *show*.

Unlike every other symbology here, Plessey is drawn at fixed proportions
rather than at ^BY's ratio - see FUNCTIONAL_SPEC.md section 18.
"""

ALPHABET = "0123456789ABCDEF"

# A bit as its modules: bar then space. A wide bar and a narrow space is a
# one; a narrow bar and a wide space is a zero.
_ONE = (3, 2)
_ZERO = (1, 4)

# Plessey's start is the bits 1101, and its terminator is a fixed pattern
# rather than four more bits - the last bar is stretched, which is what
# tells a reader the message has ended rather than merely paused.
_START_BITS = (1, 1, 0, 1)
_TERMINATOR = (5, 4, 1, 4, 1, 2, 3, 2, 3)

# The CRC generator, as the nine bits it exclusive-ors in: x^8 + x^7 + x^6 +
# x^5 + x^3 + 1, applied to each set bit of the message in turn.
_GENERATOR = (1, 1, 1, 1, 0, 1, 0, 0, 1)


def normalize(data: str) -> str:
    """The characters the symbol carries. Plessey is hexadecimal, so
    anything outside 0-9 and A-F is dropped rather than drawn wrong."""
    return ''.join(char for char in data.upper() if char in ALPHABET)


def _bits(data: str) -> list:
    """The message as bits: four per character, least significant first."""
    out = []
    for char in normalize(data):
        value = ALPHABET.index(char)
        out.extend((value >> shift) & 1 for shift in (0, 1, 2, 3))
    return out


def check_bits(data: str) -> list:
    """The eight-bit CRC over the message.

    Long division by the generator, bit by bit from the front: wherever the
    running remainder has a one, the generator is exclusive-ored in at that
    position, and what is left in the eight bits past the message is the
    check.
    """
    message = _bits(data)
    working = message + [0] * 8
    for index in range(len(message)):
        if working[index]:
            for offset, bit in enumerate(_GENERATOR):
                working[index + offset] ^= bit
    return working[len(message):]


def encode(data: str) -> list:
    """Module widths for a Plessey barcode, alternating bar/space."""
    mods = []
    for bit in list(_START_BITS) + _bits(data) + check_bits(data):
        mods.extend(_ONE if bit else _ZERO)
    mods.extend(_TERMINATOR)
    return mods
