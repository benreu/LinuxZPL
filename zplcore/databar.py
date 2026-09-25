"""^BR's twelve symbologies - the GS1 DataBar family and its relations.

^BR's own b parameter picks one of twelve, and only six of them are DataBar.
Types 7 to 10 are the UPC and EAN symbols and types 11 and 12 are GS1-128,
all of which already have encoders here; this module is what maps ^BR's
numbering onto them, and what splits a composite field from its linear part.

Types 1 to 6 - DataBar Omnidirectional, Truncated, Stacked, Stacked
Omnidirectional, Limited and Expanded - are not drawn. Their data characters
are ranked combinations of element widths whose tables this implementation
could not establish to the standard the rest of these symbologies are held
to; see FUNCTIONAL_SPEC.md section 18.
"""

from . import code128
from . import ean8
from . import ean13
from . import upca
from . import upce

# ^BR's b: which symbology, by the manual's own numbering.
TYPES = {
    '1': 'omnidirectional', '2': 'truncated', '3': 'stacked',
    '4': 'stacked_omnidirectional', '5': 'limited', '6': 'expanded',
    '7': 'upca', '8': 'upce', '9': 'ean13', '10': 'ean8',
    '11': 'gs1_128_cc_ab', '12': 'gs1_128_cc_c',
}
DEFAULT_TYPE = '1'

# The six this module can draw, and what draws each.
LINEAR = {'upca': upca.encode, 'upce': upce.encode, 'ean13': ean13.encode,
          'ean8': ean8.encode}
GS1_128 = ('gs1_128_cc_ab', 'gs1_128_cc_c')

# The six it cannot, with the name the manual gives each.
DATABAR = {
    'omnidirectional': "GS1 DataBar Omnidirectional",
    'truncated': "GS1 DataBar Truncated",
    'stacked': "GS1 DataBar Stacked",
    'stacked_omnidirectional': "GS1 DataBar Stacked Omnidirectional",
    'limited': "GS1 DataBar Limited",
    'expanded': "GS1 DataBar Expanded",
}

# ^BR's field data is the linear value, a vertical bar, and the composite
# component that prints above it.
SEPARATOR = '|'


def split(data: str) -> tuple:
    """(linear, composite) - the field data either side of its bar.

    The manual's own examples are `12345678901|this is composite info`. A
    field with no bar in it is all linear, which is what a symbology with no
    composite component gets.
    """
    linear, _bar, composite = (data or '').partition(SEPARATOR)
    return linear, composite


def normalize(data: str, kind: str) -> str:
    """What the interpretation line shows for this type."""
    linear, _composite = split(data)
    if kind == 'upca':
        return upca.normalize(linear)
    if kind == 'upce':
        try:
            return upce.normalize(linear)
        except ValueError:
            return linear
    if kind == 'ean13':
        return ean13.normalize(linear)
    if kind == 'ean8':
        return ean8.normalize(linear)
    return linear


def encode(data: str, kind: str = 'omnidirectional') -> list:
    """Module widths for ^BR's linear part, alternating bar/space."""
    linear, _composite = split(data)
    if kind in LINEAR:
        return LINEAR[kind](linear)
    if kind in GS1_128:
        # Types 11 and 12 are GS1-128: Code 128 with an FNC1 in front, so a
        # reader takes the digits as application identifiers.
        return code128.encode(linear, mode='A', gs1=True)
    raise ValueError(
        f"{DATABAR.get(kind, kind)} is not drawn yet - its own data "
        "characters are not implemented; the field round-trips unchanged")
