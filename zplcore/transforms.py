"""
The commands that move or flip a whole label, rather than one field on it.

^LH and ^LS displace every field: a label carrying one was drawn where its ^FO
said and printed somewhere else, and a save dropped the command, so the label
then printed where the canvas had been showing it all along. ^PO, ^PM and ^LR
change how the finished label is laid down.

^LT is here too but is deliberately never applied. It is media registration -
a fine-tune for print creeping up or down the roll, which modern printers set at
the printer - and says nothing about where a field sits within the label.
Applying it would move the design on screen to describe a printer adjustment.
It is carried so that a save does not silently delete it.

Toolkit-free, and read by the parser, the preview renderer and both frontends,
so none of them can hold a different opinion about where a field lands - the
arrangement ^FB, ^GB, ^GF and ^FN already have.
"""


import re

# A position or a number, and nothing else. ^FX comments run only to the next
# caret, so prose mentioning ^LH becomes a real ^LH command carrying words -
# treating that as (0, 0) let it take the place of the format's actual home.
_POSITION = re.compile(r'\s*[-+]?\d*\s*(?:,\s*[-+]?\d*\s*)?$')
_NUMBER = re.compile(r'\s*[-+]?\d*\s*$')


def _number(text, fallback=0):
    try:
        return int(str(text).strip())
    except (TypeError, ValueError):
        return fallback


def read_home(params):
    """^LHx,y - the origin every later ^FO is measured from, or None.

    None when the parameters are not a position at all, so junk cannot pass
    itself off as an origin of (0, 0).
    """
    if not _POSITION.match(params or ''):
        return None
    parts = (params or '').split(',')
    x = _number(parts[0]) if parts and parts[0].strip() else 0
    y = _number(parts[1]) if len(parts) > 1 and parts[1].strip() else 0
    return (x, y)


def read_shift(params):
    """^LSa - dots every field moves to the left, or None if not a number."""
    return _number(params) if _NUMBER.match(params or '') else None


def read_top(params):
    """^LTx - dot rows the label moves down the media, or None if not a number."""
    return _number(params) if _NUMBER.match(params or '') else None


def read_flag(params, on='Y'):
    """^LRa / ^PMa / ^POa - a one-letter switch.

    `on` is the letter that means yes, which is Y for ^LR and ^PM and I - for
    invert - for ^PO.
    """
    letter = (params or '').strip()[:1].upper()
    return letter == on.upper()


class LabelTransform:
    """What a format says about the label as a whole.

    Defaults are ZPL's own, and to_zpl writes nothing for a value still at its
    default, so a label this designer created serialises exactly as it always
    did.
    """

    def __init__(self):
        self.home = (0, 0)      # ^LH
        self.shift = 0          # ^LS, leftward
        self.top = 0            # ^LT, carried only
        self.invert = False     # ^PO I
        self.mirror = False     # ^PM Y
        self.reverse = False    # ^LR Y

    def __eq__(self, other):
        return isinstance(other, LabelTransform) and vars(self) == vars(other)

    def __repr__(self):
        return f"LabelTransform({self.to_zpl()!r})"

    def field_offset(self):
        """What ^LH and ^LS add to every ^FO, as (dx, dy).

        ^LT is not in it. See the module docstring: it registers the label
        against the media, it does not lay fields out on the label.
        """
        return (self.home[0] - self.shift, self.home[1])

    def moves_fields(self) -> bool:
        """Whether anything here displaces a field at all."""
        return self.field_offset() != (0, 0)

    def to_zpl(self) -> str:
        """The commands, omitting any still at ZPL's default.

        ^LH goes first: it is the reference point for everything after it, and
        the manual recommends it as one of the first commands in a format.
        """
        out = ''
        if self.home != (0, 0):
            out += f"^LH{self.home[0]},{self.home[1]}\n"
        if self.shift:
            out += f"^LS{self.shift}\n"
        if self.top:
            out += f"^LT{self.top}\n"
        if self.invert:
            out += "^POI\n"
        if self.mirror:
            out += "^PMY\n"
        if self.reverse:
            out += "^LRY\n"
        return out

    def fitted(self, lowest) -> 'LabelTransform':
        """A copy whose offset no element can be written above.

        ^FO's range starts at 0, so an offset larger than the smallest element
        position would be written back as a negative coordinate - which happens
        when a format moves its home part-way through, leaving the fields before
        it behind the new origin. Reducing the home instead is free: printed
        position is home + ^FO, so re-splitting the same absolute coordinate a
        different way lands in exactly the same place.

        `lowest` is the smallest (x, y) any element occupies, or None when there
        are none.
        """
        if lowest is None:
            return self.copy()
        dx, dy = self.field_offset()
        fit = self.copy()
        # ^LS feeds the x offset alongside ^LH, so the home absorbs the change
        # and the shift is left as the file gave it.
        fit.home = (min(dx, lowest[0]) + self.shift, min(dy, lowest[1]))
        return fit

    def copy(self) -> 'LabelTransform':
        """A copy no later edit can reach back through.

        The undo stack holds whole documents, and one shared between snapshots
        would rewrite every entry on it - the trap the field table and a text
        element's block already avoid.
        """
        clone = LabelTransform()
        clone.__dict__.update(self.__dict__)
        return clone
