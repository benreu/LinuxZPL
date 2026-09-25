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
    'upca': "UPC-A",
    'upce': "UPC-E",
    'ean8': "EAN-8",
    'code93': "Code 93",
    'codabar': "Codabar",
    'code11': "Code 11",
    'msi': "MSI",
    'plessey': "Plessey",
    'industrial2of5': "Industrial 2 of 5",
    'standard2of5': "Standard 2 of 5",
    'logmars': "LOGMARS",
    'postal': "POSTAL (Postnet / PLANET / Intelligent Mail)",
    'planet': "Planet Code",
    'datamatrix': "Data Matrix",
    'pdf417': "PDF417",
    'aztec': "Aztec Code",
    'qr': "QR Code",
}

# The command each symbology is written as.
COMMAND = {
    'code128': '^BC',
    'code39': '^B3',
    'ean13': '^BE',
    'interleaved2of5': '^B2',
    'upcean_extension': '^BS',
    'upca': '^BU',
    'upce': '^B9',
    'ean8': '^B8',
    'code93': '^BA',
    'codabar': '^BK',
    'code11': '^B1',
    'msi': '^BM',
    'plessey': '^BP',
    'industrial2of5': '^BI',
    'standard2of5': '^BJ',
    'logmars': '^BL',
    'postal': '^BZ',
    'planet': '^B5',
    'datamatrix': '^BX',
    'pdf417': '^B7',
    'aztec': '^B0',
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
    '^BX': ('o', 'w', 'quality_dm', 'columns', 'rows', 'format_id',
            'escape_char', 'aspect'),
    '^B7': ('o', 'h', 'security', 'columns', 'rows', 'truncate'),
    '^B0': ('o', 'w', 'eci', 'aztec_size', 'menu', 'append_count',
            'append_id'),
    '^BO': ('o', 'w', 'eci', 'aztec_size', 'menu', 'append_count',
            'append_id'),
    '^BU': ('o', 'h', 'f', 'g', 'e'),
    '^B9': ('o', 'h', 'f', 'g', 'e'),
    '^B8': ('o', 'h', 'f', 'g'),
    '^BA': ('o', 'h', 'f', 'g', 'e'),
    '^BK': ('o', 'e', 'h', 'f', 'g', 'start_char', 'stop_char'),
    '^B1': ('o', 'code11_check', 'h', 'f', 'g'),
    '^BM': ('o', 'msi_check', 'h', 'f', 'g', 'msi_show_check'),
    '^BP': ('o', 'e', 'h', 'f', 'g'),
    '^BI': ('o', 'h', 'f', 'g'),
    '^BJ': ('o', 'h', 'f', 'g'),
    '^BL': ('o', 'h', 'g'),
    '^BZ': ('o', 'h', 'f', 'g', 'postal_type'),
    '^B5': ('o', 'h', 'f', 'g'),
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
# A second spelling some commands have. ^BO is ^B0 with the letter O, which
# the manual lists twice under the same name; a file that used it reads the
# same, and comes back spelled the way COMMAND above says.
ALIASES = {'^BO': 'aztec'}

SYMBOLOGY_OF = {cmd: sym for sym, cmd in COMMAND.items()}
SYMBOLOGY_OF.update(ALIASES)

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
        elif self.kind is str:
            value = raw.upper()
        else:
            # A parameter that is whatever character the file wrote - ^BX's
            # escape character, which may be any of them - so neither the
            # case nor the choices apply.
            value = raw[:1]
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
    # ^BK k and l - which of the four start and stop characters to use. They
    # are a pair a reader can be told to expect, which is the only reason
    # Codabar has four of them.
    'start_char': Param(str, 'A', choices=('A', 'B', 'C', 'D')),
    'stop_char': Param(str, 'A', choices=('A', 'B', 'C', 'D')),
    # ^B1 e - one check character or two. The manual's default is two, and
    # it spells that N, which is the opposite way round from every other e
    # in ZPL: Code 11 is not self-checking, so it always carries at least one.
    'code11_check': Param(str, 'N', choices=('Y', 'N')),
    # ^BM e - which of MSI's four checksums the symbol carries, and ^BM e2,
    # whether the interpretation line shows it.
    'msi_check': Param(str, 'B', choices=('A', 'B', 'C', 'D')),
    'msi_show_check': Param(str, 'N', choices=('Y', 'N')),
    # ^BZ t - which postal code. 2 is reserved, and draws nothing.
    'postal_type': Param(str, '0', choices=('0', '1', '2', '3')),
    # ^BX s - the quality level. Only 200 is drawn; see the note in
    # datamatrix.py and FUNCTIONAL_SPEC.md section 18.
    'quality_dm': Param(int, 0, choices=(0, 50, 80, 100, 140, 200)),
    # ^BX c and r - force the symbol up to at least this many columns and
    # rows, so a row of them comes out the same size. Zero is "whatever the
    # data needs".
    'columns': Param(int, 0),
    'rows': Param(int, 0),
    # ^BX f - the format ID, for the quality levels that are not drawn, and
    # ^BX g, the character that introduces an escape sequence in the field
    # data. The manual's own default moved from a tilde to an underscore.
    'format_id': Param(int, 6, choices=tuple(range(7))),
    'escape_char': Param(None, '_'),
    # ^BX a - 1 square, 2 rectangular.
    'aspect': Param(int, 1, choices=(1, 2)),
    # ^B7 s - how many error-correction codewords to generate. 0 detects
    # errors without correcting any; each level up roughly doubles them.
    'security': Param(int, 0, choices=tuple(range(9))),
    # ^B7 t - drop the right row indicator and the stop pattern, which is
    # about a fifth narrower and worth having only where the label will not
    # be damaged.
    'truncate': Param(str, 'N', choices=('Y', 'N')),
    # ^B0 c - whether the field data carries extended channel interpretation
    # codes, and ^B0 e, whether this is a reader-initialisation symbol.
    # Carried, neither simulated.
    'eci': Param(str, 'N', choices=('Y', 'N')),
    'menu': Param(str, 'N', choices=('Y', 'N')),
    # ^B0 d - error control and symbol size in one number: 0 the default,
    # 1 to 99 a percentage of correction, 101 to 104 a compact symbol of
    # that many layers, 201 to 232 a full-range one, and 300 a Rune.
    'aztec_size': Param(int, 0),
    # ^B0 f and g - how many symbols a structured append runs to, and its
    # identifier. Carried, not simulated.
    'append_count': Param(int, 1),
    'append_id': Param(None, ''),
}

# What an omitted f, g, e and m mean, per symbology, in that order - the
# canonical (show_text, text_above, check_digit, mode) order BarcodeElement
# holds them in. The UPC/EAN extension prints its line above the bars by
# default; every other symbology below it.
_FLAG_DEFAULTS = {
    'upcean_extension': ('Y', 'Y', 'N', 'N'),
    # UPC-A and UPC-E print their check digit in the interpretation line
    # unless told not to - their e means "show it", not "add it": both carry
    # one whatever the command says.
    'upca': ('Y', 'N', 'Y', 'N'),
    'upce': ('Y', 'N', 'Y', 'N'),
    # The postal codes print no interpretation line unless asked: they go on
    # an envelope under an address, where a line of digits is clutter.
    'postal': ('N', 'N', 'N', 'N'),
    'planet': ('N', 'N', 'N', 'N'),
}


def flag_defaults(symbology: str) -> tuple:
    """(show_text, text_above, check_digit, mode) as ZPL letters, for an
    omitted parameter of this symbology."""
    return _FLAG_DEFAULTS.get(symbology, ('Y', 'N', 'N', 'N'))


# Parameters a command spells but whose value ZPL fixes. ^BK's check digit
# is the only one: the manual gives it as "Fixed Value: N", because Codabar
# has no checksum at all and the parameter exists to keep the ones after it
# in position. It is written, since the start and stop characters follow it,
# but nothing offers to change it.
FIXED = {'codabar': ('e',)}


def varies(symbology: str, name: str) -> bool:
    """Whether this symbology's own command lets that parameter change."""
    return (name in COMMAND_PARAMS[COMMAND[symbology]]
            and name not in FIXED.get(symbology, ()))


# Parameters written even when they hold the default: the ones the printer
# would otherwise resolve for itself, which a file this designer writes must
# not leave to it. `h` is always among them, as it always was.
ALWAYS_WRITTEN = frozenset(('h', 'w'))

# What a symbology's `h` measures: dots for the 1-D family and the postal
# codes, modules for PDF417's row height, and nothing for the matrix codes
# whose size is their grid. Anything not here is dots.
HEIGHT_UNIT = {
    'qr': None,
    'datamatrix': None,
    # ^B7's h is how many modules tall each row of codewords is drawn, not
    # how many dots: the manual says "this number multiplied by the module
    # equals the height of the individual rows in dots". Scaling it for a
    # different head resolution would double-count, since the module width
    # it multiplies is scaled already.
    'pdf417': 'modules',
}

# The symbologies whose symbol is a grid of square modules rather than bars
# and spaces. Their size is the grid, so neither ^BY's height nor their own
# command carries one.
MATRIX = frozenset(('qr', 'datamatrix', 'pdf417', 'aztec'))

# The symbologies drawn as bars of differing height rather than differing
# width. Every bar is narrow and every gap the same; what carries the data is
# how tall each bar is and where it sits, so ^BY's ratio means nothing to
# them and their own encoders return extents rather than widths.
POSTAL = frozenset(('postal', 'planet'))

# Symbologies whose module width is ^BY's w rather than a magnification the
# command carries itself - which is exactly those whose own command has no w
# in it. They write ^BY; the rest write their magnification in the command
# and no ^BY at all, since ^BY's w is not what they are drawn at.
#
# Not every matrix symbology is in the second group: PDF417 is stacked out of
# ordinary bars and spaces and takes both its module width and, when its own
# command leaves the row height out, its height from ^BY.
READS_BY = frozenset(key for key, command in COMMAND.items()
                     if 'w' not in COMMAND_PARAMS[command])

# Symbologies with no interpretation line at all - the matrix codes, whose
# commands carry no f parameter. Everything else has one, on by default or
# not as flag_defaults says.
NO_TEXT = frozenset(('qr', 'datamatrix', 'pdf417', 'aztec'))


# The matrix symbologies whose own command, with its size left out, means
# "fit the symbol into the height ^BY gives" rather than "use the default for
# this print resolution". Data Matrix is the only one: ^BX's h is the size of
# one module, and the manual divides ^BY's height by the rows the data needs.
SIZED_BY_HEIGHT = frozenset(('datamatrix',))

# How big a placeholder a symbology that could not be built stands in, in
# modules - the smallest QR symbol either way, and the width of a UPC-A for a
# one-dimensional one. Enough to be seen and clicked, which is the point.
PLACEHOLDER_GRID = 21
PLACEHOLDER_MODULES = 95

# ^BY's ratio only changes symbologies whose wide elements are drawn at it.
# The manual is explicit that it "has no effect on fixed-ratio bar codes".
USES_RATIO = frozenset(('code39', 'interleaved2of5', 'logmars', 'codabar',
                        'code11', 'industrial2of5', 'standard2of5'))


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
    # The UPC/EAN family's e says whether the interpretation line shows the
    # check digit the symbol always carries - not whether to add one.
    'upca':             _features(check_digit="Print Check Digit"),
    'upce':             _features(check_digit="Print Check Digit"),
    'ean8':             _features(),
    'code93':           _features(check_digit="Print Check Characters"),
    'codabar':          _features(ratio=True),
    'code11':           _features(ratio=True),
    'msi':              _features(),
    'plessey':          _features(check_digit="Print Check Digits"),
    'industrial2of5':   _features(ratio=True),
    'standard2of5':     _features(ratio=True),
    # LOGMARS has no f parameter at all: the line always prints.
    'logmars':          _features(ratio=True, text='always'),
    'postal':           _features(),
    'planet':           _features(),
    'datamatrix':       _features(height=None, module_width="Module Size",
                                  text=False),
    'pdf417':           _features(height=(1, 30), module_width="Module Width",
                                  text=False),
    'aztec':            _features(height=None, module_width="Magnification",
                                  text=False),
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
    'datamatrix': (('aspect', "Shape",
                    (("Square", 1), ("Rectangular", 2))),
                   ('quality_dm', "Quality",
                    tuple((str(q), q) for q in (200, 0, 50, 80, 100, 140))),
                   ('columns', "Least Columns",
                    tuple((str(n) if n else "Fit the data", n)
                          for n in (0, 10, 16, 20, 26, 32, 36, 44, 52))),
                   ('rows', "Least Rows",
                    tuple((str(n) if n else "Fit the data", n)
                          for n in (0, 10, 16, 20, 26, 32, 36, 44, 52)))),
    'pdf417': (('security', "Security Level",
                tuple((str(n) if n else "0 (detection only)", n)
                      for n in range(9))),
               ('columns', "Columns",
                tuple((str(n) if n else "Fit the aspect", n)
                      for n in (0, 1, 2, 3, 4, 6, 8, 10, 12, 16, 20, 30))),
               ('rows', "Rows",
                tuple((str(n) if n else "Fit the data", n)
                      for n in (0, 3, 5, 10, 15, 20, 30, 45, 60, 90))),
               ('truncate', "Truncated", (("No", 'N'), ("Yes", 'Y')))),
    'aztec': (('aztec_size', "Size and Correction",
               (("Default (23%)", 0), ("Minimum (5%)", 5),
                ("Low (10%)", 10), ("High (50%)", 50), ("Maximum (95%)", 95),
                ("Compact, 1 layer", 101), ("Compact, 2 layers", 102),
                ("Compact, 3 layers", 103), ("Compact, 4 layers", 104),
                ("Full range, 1 layer", 201), ("Full range, 4 layers", 204),
                ("Full range, 8 layers", 208), ("Full range, 16 layers", 216),
                ("Full range, 32 layers", 232))),),
    'codabar': (('start_char', "Start Character",
                 tuple((c, c) for c in 'ABCD')),
                ('stop_char', "Stop Character",
                 tuple((c, c) for c in 'ABCD'))),
    'code11': (('code11_check', "Check Characters",
                (("Two", 'N'), ("One", 'Y'))),),
    'postal': (('postal_type', "Postal Code",
                (("Postnet", '0'), ("PLANET", '1'),
                 ("USPS Intelligent Mail", '3'), ("Reserved", '2'))),),
    'msi': (('msi_check', "Check Digits",
             (("One Mod 10", 'B'), ("None", 'A'), ("Two Mod 10", 'C'),
              ("Mod 11 then Mod 10", 'D'))),
            ('msi_show_check', "Show Check Digits",
             (("No", 'N'), ("Yes", 'Y')))),
}
