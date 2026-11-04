"""What the Print window edits on the document, whatever the toolkit.

The window holds two kinds of setting. The printer it sends to - address,
port, resolution, font memory - belongs to the session, and the frontends keep
it. Everything else is part of the label, saved with it and undone with it,
and is read and written here:

- ^PQ's five parameters: copies, the pause count, RFID replicates, and the
  override-pause and cut-on-error flags.
- ^PO, ^PM and ^LR: how the finished label is laid down.
- ^CV: whether the printer checks each barcode's data as it prints.
- ^MD, ^PR, ^MM, ^MN and ^MT: the printer's darkness, speeds, print mode,
  media tracking and media type. The document carries these verbatim
  (Document.media_settings), and they are parsed here into values a row can
  show.

A setup command the label does not carry is None, "the printer's setting",
and writes nothing. That is the rule FUNCTIONAL_SPEC section 9 sets for those
five: none has an "off", and what a label does not set belongs to whoever set
the printer up. A carried value no row offers - a reserved print mode, a speed
past the list, a darkness with two decimals - is an AsWritten, and goes back
exactly as it came.

A command whose value is left alone keeps its spelling: ^PRC stays ^PRC,
although a changed one is written ^PR4, and ^MD8.30 stays ^MD8.30. Accepting
the window unchanged never rewrites the label, just as accepting an element
editor unchanged never rewrites the element.

Both frontends draw their rows from these tables, so neither can offer a
choice or a range the other does not.
"""

import math
from dataclasses import dataclass, fields as dataclass_fields
from typing import Any, Dict, Optional, Sequence, Tuple

from .parser import MEDIA_SETTINGS


class AsWritten:
    """A carried value no row offers, kept exactly as the label spelled it."""

    def __init__(self, text: str):
        self.text = text

    def __eq__(self, other):
        return isinstance(other, AsWritten) and other.text == self.text

    def __hash__(self):
        return hash((AsWritten, self.text))

    def __repr__(self):
        return f'AsWritten({self.text!r})'

    def label(self) -> str:
        """How a row offers it: first, and selected, until it is changed."""
        return f"As written: {self.text or '(blank)'}"


# The first choice of every setup row: send nothing, and leave the printer as
# it was set up.
PRINTER_SETTING = "Printer's setting"

# --- ranges, from the manual's own (zpl_manual.pdf pp. 293-324) -------------

QUANTITY_RANGE = (1, 99_999_999)
# 0 is "no pause", ZPL's default, though the manual lists the range from 1.
PAUSE_RANGE = (0, 99_999_999)
REPLICATE_RANGE = (0, 99_999_999)
# Relative to the darkness the printer is configured with, and clamped by it
# at 0 and 30. One decimal, which the XiIIIPlus/Xi4 class accepts.
DARKNESS_RANGE = (-30.0, 30.0)
DARKNESS_DECIMALS = 1
# The widest of the manual's per-printer ranges, so an offset written for a
# 600 dpi printer is shown rather than cut down.
MARK_OFFSET_RANGE = (-240, 566)

# --- choices ----------------------------------------------------------------

# ^PR's speeds in inches per second. A-E are the older names for five of them,
# read on the way in. A changed speed is written as its number.
SPEED_LETTERS = {'A': 2, 'B': 3, 'C': 4, 'D': 6, 'E': 8}
PRINT_SPEEDS = tuple(range(1, 15))
# The manual lists no 1 in/s for slew or backfeed.
FEED_SPEEDS = tuple(range(2, 15))


def speed_label(speed: int) -> str:
    """'4 in/s (C)': the letter too, for whoever knows the speed by it."""
    letter = next((l for l, n in SPEED_LETTERS.items() if n == speed), None)
    return f"{speed} in/s" + (f" ({letter})" if letter else "")


PRINT_SPEED_CHOICES = tuple((speed_label(n), n) for n in PRINT_SPEEDS)
FEED_SPEED_CHOICES = tuple((speed_label(n), n) for n in FEED_SPEEDS)

# ^MM. L and U are reserved and are not offered; a label that carries one has
# it as written.
PRINT_MODES = (('Tear-off (T)', 'T'), ('Peel-off (P)', 'P'),
               ('Rewind (R)', 'R'), ('Applicator (A)', 'A'),
               ('Cutter (C)', 'C'), ('Delayed cutter (D)', 'D'),
               ('RFID (F)', 'F'), ('Kiosk (K)', 'K'))
# ^MN. Y and W "provide the same result"; both are listed because labels are
# written with either.
MEDIA_TRACKING = (('Continuous (N)', 'N'),
                  ('Non-continuous, web sensing (Y)', 'Y'),
                  ('Non-continuous, web sensing (W)', 'W'),
                  ('Non-continuous, mark sensing (M)', 'M'),
                  ('Auto-detect at calibration (A)', 'A'),
                  ('Continuous, variable length (V)', 'V'))
# The one tracking the black mark offset applies to.
MARK_SENSING = 'M'
# ^MT.
MEDIA_TYPES = (('Thermal transfer (T)', 'T'), ('Direct thermal (D)', 'D'))


def choices(options: Sequence[Tuple[str, Any]], current) -> list:
    """A setup row's (label, value) list: the printer's setting first, then
    the carried value when no option is it, then the options."""
    out = [(PRINTER_SETTING, None)]
    if isinstance(current, AsWritten):
        out.append((current.label(), current))
    return out + list(options)


# --- the settings -----------------------------------------------------------

@dataclass
class PrintSettings:
    """Every setting of the label the Print window shows, as one value.

    The setup fields hold None for the printer's setting, the value a row
    offers, or an AsWritten. Prepeel rides on the print mode and the mark
    offset on the tracking: neither is written without the one it belongs to.
    """
    quantity: int = 1
    pause_count: int = 0
    replicates: int = 0
    override_pause: bool = False
    cut_on_error: bool = True
    invert: bool = False
    mirror: bool = False
    reverse: bool = False
    code_validation: bool = False
    darkness: Any = None
    print_speed: Any = None
    slew_speed: Any = None
    backfeed_speed: Any = None
    print_mode: Any = None
    prepeel: bool = False
    media_tracking: Any = None
    mark_offset: Any = 0
    media_type: Any = None

    @classmethod
    def read(cls, document) -> 'PrintSettings':
        """The settings the document holds now."""
        settings = cls(
            quantity=document.print_quantity,
            pause_count=document.print_pause_count,
            replicates=document.print_replicates,
            override_pause=document.print_override_pause,
            cut_on_error=document.print_cut_on_error,
            invert=document.transform.invert,
            mirror=document.transform.mirror,
            reverse=document.transform.reverse,
            code_validation=document.code_validation)
        for command, codec in _CODECS.items():
            params = document.media_settings.get(command)
            if params is not None:
                for name, value in zip(codec.fields, codec.decode(params)):
                    setattr(settings, name, value)
        return settings

    def copy(self) -> 'PrintSettings':
        return PrintSettings(**{f.name: getattr(self, f.name)
                                for f in dataclass_fields(self)})

    def apply(self, document) -> bool:
        """Write these settings onto the document. Whether anything changed,
        so that a window accepted unchanged records nothing."""
        changed = False
        quantity = (self.quantity, self.pause_count, self.replicates,
                    self.override_pause, self.cut_on_error)
        if quantity != (document.print_quantity, document.print_pause_count,
                        document.print_replicates,
                        document.print_override_pause,
                        document.print_cut_on_error):
            (document.print_quantity, document.print_pause_count,
             document.print_replicates, document.print_override_pause,
             document.print_cut_on_error) = quantity
            changed = True
        if self.code_validation != document.code_validation:
            document.code_validation = self.code_validation
            changed = True
        flips = (self.invert, self.mirror, self.reverse)
        transform = document.transform
        if flips != (transform.invert, transform.mirror, transform.reverse):
            # A copy, not an edit: an undo snapshot may still hold the
            # document's own transform. The home, Label Settings', is kept.
            transform = transform.copy()
            transform.invert, transform.mirror, transform.reverse = flips
            document.transform = transform
            changed = True
        media = self._media_settings(document.media_settings)
        if list(media.items()) != list(document.media_settings.items()):
            document.media_settings = media
            changed = True
        return changed

    def _media_settings(self, old: Dict[str, str]) -> Dict[str, str]:
        """The verbatim media settings these settings write, given the ones
        the document carries now."""
        written = {}
        for command, codec in _CODECS.items():
            mine = tuple(getattr(self, name) for name in codec.fields)
            if command in old and codec.decode(old[command]) == mine:
                written[command] = old[command]
                continue
            params = codec.encode(mine)
            if params is not None:
                written[command] = params
        # The commands already carried keep their places - anything carried
        # that is not one of these, untouched - and a new one joins them in
        # the parser's order.
        media = {}
        for command, params in old.items():
            if command in written:
                media[command] = written[command]
            elif command not in _CODECS:
                media[command] = params
        for command in MEDIA_SETTINGS:
            if command in written and command not in media:
                media[command] = written[command]
        return media


# --- reading and writing each setup command ---------------------------------

def _text(value) -> str:
    return value.text if isinstance(value, AsWritten) else str(value)


def _choice(text: str, codes) -> Any:
    """A one-letter parameter: its code when it is one of `codes`, else as
    written."""
    if text.upper() in codes:
        return text.upper()
    return AsWritten(text)


def _darkness(text: str):
    try:
        value = float(text)
    except ValueError:
        return AsWritten(text)
    low, high = DARKNESS_RANGE
    if (not math.isfinite(value) or not low <= value <= high
            or abs(value - round(value, DARKNESS_DECIMALS)) > 1e-9):
        return AsWritten(text)
    return round(value, DARKNESS_DECIMALS)


def _darkness_text(value) -> str:
    if isinstance(value, AsWritten):
        return value.text
    if value == int(value):
        return str(int(value))
    return f"{value:.{DARKNESS_DECIMALS}f}"


def _speed(text: str, offered):
    """A ^PR speed: None when blank, else its number when the row offers it,
    else as written."""
    if not text:
        return None
    number = SPEED_LETTERS.get(text.upper())
    if number is None and text.isdigit():
        number = int(text)
    if number in offered:
        return number
    return AsWritten(text)


def _mark_offset(text: str):
    if not text:
        return 0
    try:
        value = int(text)
    except ValueError:
        return AsWritten(text)
    low, high = MARK_OFFSET_RANGE
    return value if low <= value <= high else AsWritten(text)


class _Codec:
    def __init__(self, fields, decode, encode):
        self.fields = fields
        self.decode = decode
        self.encode = encode


def _parts(params: str, count: int) -> list:
    parts = [p.strip() for p in params.split(',')]
    return (parts + [''] * count)[:count]


def _decode_pr(params):
    p, s, b = _parts(params, 3)
    return (_speed(p, PRINT_SPEEDS), _speed(s, FEED_SPEEDS),
            _speed(b, FEED_SPEEDS))


def _encode_pr(values):
    # A chain: a speed with none before it is not written, so no ^PR goes out
    # with a blank parameter in the middle.
    out = []
    for value in values:
        if value is None:
            break
        out.append(_text(value))
    return ','.join(out) or None


def _decode_mm(params):
    mode, prepeel = _parts(params, 2)
    return (_choice(mode, {c for _, c in PRINT_MODES}),
            prepeel.upper() == 'Y')


def _encode_mm(values):
    mode, prepeel = values
    if mode is None:
        return None
    return _text(mode) + (',Y' if prepeel else '')


def _decode_mn(params):
    tracking, offset = _parts(params, 2)
    return (_choice(tracking, {c for _, c in MEDIA_TRACKING}),
            _mark_offset(offset))


def _encode_mn(values):
    tracking, offset = values
    if tracking is None:
        return None
    # The offset means something only to mark sensing, and 0 is its default.
    if tracking == MARK_SENSING and offset != 0:
        return f"{_text(tracking)},{_text(offset)}"
    return _text(tracking)


_CODECS = {
    '^PR': _Codec(('print_speed', 'slew_speed', 'backfeed_speed'),
                  _decode_pr, _encode_pr),
    '^MD': _Codec(('darkness',),
                  lambda params: (_darkness(params.strip()),),
                  lambda values: (None if values[0] is None
                                  else _darkness_text(values[0]))),
    '^MM': _Codec(('print_mode', 'prepeel'), _decode_mm, _encode_mm),
    '^MN': _Codec(('media_tracking', 'mark_offset'), _decode_mn, _encode_mn),
    '^MT': _Codec(('media_type',),
                  lambda params: (_choice(params.strip(),
                                          {c for _, c in MEDIA_TYPES}),),
                  lambda values: (None if values[0] is None
                                  else _text(values[0]))),
}
