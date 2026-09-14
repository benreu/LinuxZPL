"""
Numbered fields: the ^FN placeholders a stored format is built out of.

A ^DF format is a normal design in which some fields carry ^FN instead of ^FD -
the printer supplies their data at print time. Those fields used to vanish
entirely, and a ^FN barcode field was handed the string "123456789" by a
fallback meant for newly created barcodes, so the designer invented label
content and wrote it to disk.

Field numbers are document-scoped and shared. The manual is explicit: "if a
label format contains a field with ^FN and ^FD, the data in that field prints
for any other field containing the same ^FN value." So the values live in one
table on the document rather than on the elements, and an element only
remembers which number it is.

One reading of all that, used by the parser, the preview renderer and both
frontends, so none of them can hold a different opinion about what a field
shows - the arrangement ^FB, ^GB and ^GF already have.
"""

import re

# ^FN#"a" - the number, then an optional prompt in double quotes. The quotes are
# what make it a prompt rather than data, which is why they are required here.
# The (?!\d) matters: without it ^FN99999 matched its first four digits and
# came back as field 9999, inventing a number the file never wrote.
_FN = re.compile(r'\s*(\d{1,4})(?!\d)\s*(?:"([^"]*)")?')

# The most ZPL will number
MAX_NUMBER = 9999

# A placeholder is shown wrapped, so a variable field reads as one even in a
# frontend that draws no other marker.
OPEN, CLOSE = '«', '»'


def read(params: str):
    """^FN#"a" as (number, prompt), or None when it is not one.

    The prompt is None when the field gave none, which is different from an
    empty one: ^FN1"" names a field whose prompt is deliberately blank.
    """
    match = _FN.match(params or '')
    if not match:
        return None
    number = int(match.group(1))
    if number > MAX_NUMBER:
        return None
    return number, match.group(2)


def placeholder(number, prompt=None) -> str:
    """What to show for a field whose data the printer will supply.

    The prompt when there is one, since that is what it is for - ZPL shows it
    on a keyboard display unit for exactly this purpose - and the number
    otherwise, because a field has to be identifiable even unnamed.
    """
    if prompt:
        return f"{OPEN}{prompt}{CLOSE}"
    return f"{OPEN}FN{int(number)}{CLOSE}"


# ^FC<a>,<b>,<c> - the characters that trigger a substitution in a later ^FD.
# Only the primary has a default: the manual gives b and c "Default: none -
# this value cannot be the same as a or c". Defaulting them to characters
# instead registered two indicators the file never asked for, so data
# containing them was clock-substituted rather than printed.
_CLOCK_DEFAULTS = ('%', None, None)


def read_serial(params: str):
    """^SN<start>,<increment>,<leading zeros> as (start, increment, leading
    zero), or None when there is no start value to serialize.

    `increment` defaults to 1 and the leading-zero flag to False when the
    file leaves them out, matching how a printer treats an omitted ^SN
    parameter.
    """
    parts = (params or '').split(',')
    start = parts[0].strip()
    if not start:
        return None
    try:
        increment = int(parts[1]) if len(parts) > 1 and parts[1].strip() else 1
    except ValueError:
        increment = 1
    leading_zero = len(parts) > 2 and parts[2].strip().upper() == 'Y'
    return start, increment, leading_zero


def read_clock_chars(params: str):
    """^FC<a>,<b>,<c> as a 3-tuple, None for a part the file left out."""
    parts = [p.strip() for p in (params or '').split(',')]
    parts += [''] * (3 - len(parts))
    return tuple(p or default for p, default in zip(parts, _CLOCK_DEFAULTS))


def clock_chars_zpl(chars) -> str:
    """^FC's parameters, trimmed after the last one that is actually set.

    Positional, so a gap has to stay a gap: ('%', None, '#') is "%,,#", not
    "%,#". Only trailing absences come off.
    """
    given = list(chars)
    keep = 0
    for index, value in enumerate(given):
        if value is not None:
            keep = index + 1
    return ','.join('' if v is None else v for v in given[:keep])


def serial_display(base: str, increment) -> str:
    """What a canvas draws for a field the printer increments each label.

    The starting value is real content - unlike a bare ^FN, there is always
    something to show - so it is kept, with a marker appended rather than
    replacing it, the way ^FN's brackets mark a field the canvas would
    otherwise draw as if it were fixed text.
    """
    sign = '+' if increment >= 0 else ''
    return f"{base}{OPEN}{sign}{increment}{CLOSE}"


def clock_display(base: str) -> str:
    """What a canvas draws for a field the printer's clock fills in.

    The literal format codes (e.g. %m/%d/%y) are real content too, so they
    are wrapped rather than hidden - the same reasoning as `serial_display`.
    """
    return f"{OPEN}{base}{CLOSE}"


class FieldTable:
    """The data and prompts belonging to a format's numbered fields.

    Keyed by number, because that is what ZPL shares them by. A value set once
    reaches every field carrying the same number, which is the whole mechanism
    behind recalling a stored format and filling it in.
    """

    def __init__(self):
        self._values = {}
        self._prompts = {}

    def __bool__(self):
        return bool(self._values or self._prompts)

    def __eq__(self, other):
        return (isinstance(other, FieldTable)
                and self._values == other._values
                and self._prompts == other._prompts)

    def set_value(self, number, value) -> None:
        """Record the data a ^FN#^FD pair gives field `number`."""
        if value is None:
            return
        self._values[int(number)] = value

    def set_prompt(self, number, prompt) -> None:
        """Record the quoted name a ^FN#"a" gives field `number`."""
        if prompt is None:
            return
        self._prompts[int(number)] = prompt

    def value(self, number):
        """The literal field `number` prints, or None if nothing gave one."""
        return self._values.get(int(number))

    def prompt(self, number):
        """Field `number`'s prompt, or None."""
        return self._prompts.get(int(number))

    def display(self, number, prompt=None) -> str:
        """What a canvas draws for field `number`.

        The value when the file gave one, so the canvas resembles the label
        that will print; the prompt or the number when it did not, because the
        alternative is drawing nothing and leaving an invisible field on the
        design. `prompt` is the one the element itself carried, which wins over
        the table's - a field names itself where it is written.
        """
        number = int(number)
        found = self._values.get(number)
        if found:
            return found
        return placeholder(number, prompt or self._prompts.get(number))

    def numbers(self) -> list:
        """Every number this table knows about, in order."""
        return sorted(set(self._values) | set(self._prompts))

    def pairs(self) -> list:
        """The (number, value) pairs that carry data, in number order.

        Only the ones with data: a prompt alone says what a field is called,
        not what it holds, and writing ^FN2^FD for it would claim it holds an
        empty string.
        """
        return [(n, self._values[n]) for n in sorted(self._values)]

    def to_zpl(self) -> str:
        """The ^FN#^FD pairs that fill in a recalled format.

        This is the whole body of a recall call - the geometry it fills lives on
        the printer - so it has to survive a load and a save intact or the file
        is destroyed by opening it.
        """
        return ''.join(f"^FN{n}^FD{value}^FS\n" for n, value in self.pairs())

    def copy(self) -> 'FieldTable':
        """A copy no later edit can reach back through.

        The undo stack holds whole documents, and a table shared between
        snapshots would rewrite every entry on it - the trap a text element's
        block already has to avoid.
        """
        clone = FieldTable()
        clone._values = dict(self._values)
        clone._prompts = dict(self._prompts)
        return clone
