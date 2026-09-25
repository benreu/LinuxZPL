"""The catalogue of barcode symbologies: which ZPL command spells each, in
what order that command's parameters come, what an omitted one means, and
which of them the editors offer.

Everything here is data. BarcodeElement, the parser, the preview renderer
and both editors read it, so none of them can hold a different opinion about
what `^B3N,Y,60` says or what a bare `^BQ,2` leaves to the printer. Before
this table the parser and `to_zpl` each spelled the parameter order out
again, and ^B3 - the one command whose check digit comes before the height -
was a special case in both.
"""

# Every symbology this designer draws, keyed the way BarcodeElement.symbology
# spells it, with the label both editors show for it.
SYMBOLOGIES = {
    'code128': "Code 128",
    'code39': "Code 39",
    'ean13': "EAN-13",
    'interleaved2of5': "Interleaved 2 of 5",
    'upcean_extension': "UPC/EAN Extension",
    'qr': "QR Code",
}

# The command each symbology is written as.
COMMAND = {
    'code128': '^BC',
    'code39': '^B3',
    'ean13': '^BE',
    'interleaved2of5': '^B2',
    'upcean_extension': '^BS',
    'qr': '^BQ',
}

# The command's positional parameters, in the order ZPL spells them. Seven
# names are shared and held on the element under their own attributes:
# o (orientation), h (bar_height), w (module_width, which a matrix symbology
# spells as its magnification), f (print the interpretation line), g (print
# it above), e (check digit) and m (mode). Every other name is one of
# PARAMETERS below, held on the element under that name.
COMMAND_PARAMS = {
    '^BC': ('o', 'h', 'f', 'g', 'e', 'm'),
    '^BQ': ('o', 'qr_model', 'w', 'quality', 'qr_mask'),
    '^B3': ('o', 'e', 'h', 'f', 'g'),
    '^BE': ('o', 'h', 'f', 'g'),
    '^B2': ('o', 'h', 'f', 'g', 'e'),
    '^BS': ('o', 'h', 'f', 'g'),
}

SHARED_PARAMS = ('o', 'h', 'w', 'f', 'g', 'e', 'm')
# The shared parameters that are a Y/N flag, in the canonical order
# BarcodeElement holds them in.
FLAG_PARAMS = ('f', 'g', 'e', 'm')

# The symbology each command reads as. More than one command can spell the
# same symbology (^B0 and ^BO are both Aztec); COMMAND above picks the one a
# save writes.
SYMBOLOGY_OF = {cmd: sym for sym, cmd in COMMAND.items()}

class Param:
    """One parameter beyond the shared six: how its ZPL spelling is read,
    how the value is written back, and what an omitted one means.

    `default` may be a plain value or a dict keyed by symbology, since more
    than one command can share a parameter name and not its default - ^BQ's
    error correction defaults to Q while ^B7's security level defaults to 0.
    `kind` is the Python type the attribute holds: a value that will not
    convert keeps the default rather than raising, because a barcode
    parameter another tool spelled oddly is not a reason to refuse the whole
    label.
    """

    def __init__(self, kind, default, choices=None, invalid=None):
        self.kind = kind
        self.default = default
        # The values ZPL defines, upper-cased for a letter parameter.
        self.choices = choices
        # What a value outside `choices` means, when that is not the same as
        # leaving the parameter out. ^BQ's error correction is the case the
        # manual is explicit about: "Q = if empty, M = invalid values".
        self.invalid = invalid

    def default_for(self, symbology: str):
        if isinstance(self.default, dict):
            return self.default.get(symbology, self.default.get(None))
        return self.default

    def read(self, raw: str, symbology: str):
        raw = (raw or '').strip()
        if not raw:
            return self.default_for(symbology)
        if self.kind is int:
            try:
                value = int(raw)
            except ValueError:
                return self.default_for(symbology)
        else:
            value = raw.upper() if self.kind is str else raw
        if self.choices is not None and value not in self.choices:
            return (self.invalid if self.invalid is not None
                    else self.default_for(symbology))
        return value

    def write(self, value) -> str:
        return '' if value is None else str(value)

    def default_zpl(self, symbology: str) -> str:
        return self.write(self.default_for(symbology))


# The parameters beyond the shared six, by the attribute that holds each one.
# A default of None means "the parser resolves it" - a magnification from the
# print resolution, a row height from the data - and such a parameter is
# named in ALWAYS_WRITTEN, so a file never depends on a printer's own
# default again.
PARAMETERS = {
    # ^BQ b - model 1 is the original specification and model 2 the enhanced
    # one the manual recommends and every reader expects. Carried, but always
    # drawn as model 2; see FUNCTIONAL_SPEC.md section 18.
    'qr_model': Param(int, 2, choices=(1, 2)),
    # ^BQ d - error correction. Omitted and unreadable mean different things
    # here, which is why `invalid` exists at all.
    'quality': Param(str, {'qr': 'Q'}, choices=('L', 'M', 'Q', 'H'),
                     invalid='M'),
    # ^BQ e - which of the eight masks to apply. The manual's default is 7
    # rather than "whichever scores best", so that is what is drawn.
    'qr_mask': Param(int, 7, choices=tuple(range(8))),
}

# What an omitted f, g, e and m mean, per symbology, in that order - the
# canonical (show_text, text_above, check_digit, mode) order BarcodeElement
# holds them in. The UPC/EAN extension prints its line above the bars by
# default; every other symbology below it.
_FLAG_DEFAULTS = {
    'upcean_extension': ('Y', 'Y', 'N', 'N'),
}


def flag_defaults(symbology: str) -> tuple:
    """(show_text, text_above, check_digit, mode) as ZPL letters, for an
    omitted parameter of this symbology."""
    return _FLAG_DEFAULTS.get(symbology, ('Y', 'N', 'N', 'N'))


# Parameters written even when they hold the default: the ones the printer
# would otherwise resolve for itself, which a file this designer writes must
# not leave to it. `h` is always among them, as it always was.
ALWAYS_WRITTEN = frozenset(('h', 'w'))

# What a symbology's `h` measures: dots for the 1-D family and the postal
# codes, modules for PDF417's row height, and nothing for the matrix codes
# whose size is their grid. Anything not here is dots.
HEIGHT_UNIT = {
    'qr': None,
}

# Symbologies whose module width is ^BY's w rather than a magnification the
# command carries itself. Everything not here writes ^BY; the rest write
# their magnification in the command and no ^BY at all.
READS_BY = frozenset(('code128', 'code39', 'ean13', 'interleaved2of5',
                      'upcean_extension'))

# Symbologies with no interpretation line at all - the matrix codes, whose
# commands carry no f parameter. Everything else has one, on by default or
# not as flag_defaults says.
NO_TEXT = frozenset(('qr',))

# The symbologies whose symbol is a grid of square modules rather than bars
# and spaces. Their size is the grid, so neither ^BY's height nor their own
# command carries one.
MATRIX = frozenset(('qr',))

# How wide a placeholder a matrix symbology that could not be built stands
# in, in modules - the smallest QR symbol, which is the smallest of them all.
PLACEHOLDER_GRID = 21

# ^BY's ratio only changes symbologies whose wide elements are drawn at it.
# The manual is explicit that it "has no effect on fixed-ratio bar codes".
USES_RATIO = frozenset(('code39', 'interleaved2of5'))


def default_magnification(dpi: int) -> int:
    """The magnification the manual gives a matrix code whose command left
    it out: 1 on a 150 dpi printer, 2 at 200, 3 at 300 and 6 at 600."""
    if dpi < 200:
        return 1
    if dpi < 300:
        return 2
    if dpi < 600:
        return 3
    return 6


# --- what the editors offer -------------------------------------------------

# The choices both frontends offer for a barcode, as (label, value). Here
# rather than in either toolkit's dialog code, because a frontend offering a
# different set would produce a different label from the same design.
BARCODE_SYMBOLOGIES = tuple((label, key) for key, label in SYMBOLOGIES.items())


def _features(mode=False, ratio=False, check_digit=None, height=(20, 300),
              module_width="Module Width", text=True):
    return {'mode': mode, 'ratio': ratio, 'check_digit': check_digit,
            'height': height, 'module_width': module_width, 'text': text}


# Which of the dialog's own rows apply to a given symbology, and what to call
# them there - Code 128's UCC digit, Code 39's Mod-43 and Interleaved 2 of
# 5's Mod-10 are three different checksums under one name, and EAN-13 and the
# UPC/EAN extension have none to offer at all. `height` is the row's range or
# None for a symbology whose height is its grid; `module_width` is the row's
# label (a matrix code calls it magnification) or None; `text` is False for a
# symbology with no interpretation line and 'always' for one whose command
# cannot switch it off.
BARCODE_FEATURES = {
    'code128':          _features(mode=True, check_digit="UCC Check Digit"),
    'code39':           _features(ratio=True, check_digit="Mod-43 Check Digit"),
    'ean13':            _features(),
    'interleaved2of5':  _features(ratio=True, check_digit="Mod-10 Check Digit"),
    'upcean_extension': _features(),
    'qr':               _features(height=None, module_width="Magnification",
                                  text=False),
}

# The rows a symbology adds to the dialog for its own parameters, as
# (attribute, label, choices), where choices is a tuple of (label, value).
# Both editors build these rows from here and show them only while that
# symbology is chosen, so a parameter cannot arrive with no way to set it.
BARCODE_PARAMETERS = {
    'qr': (('quality', "Error Correction",
            (("High density (L)", 'L'), ("Standard (M)", 'M'),
             ("High reliability (Q)", 'Q'), ("Ultra-high (H)", 'H'))),
           ('qr_model', "Model",
            (("2 (recommended)", 2), ("1 (original)", 1))),
           ('qr_mask', "Mask", tuple((str(n), n) for n in range(8)))),
}
