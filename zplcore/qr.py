"""QR Code encoder - returns the grid of dark modules for drawing.

^BQ is not like the one-dimensional commands. Most of what it encodes is not
in the command at all but in a run of switches at the front of the ^FD data:
`^BQN,2,10^FDMM,AAC-42` is a standard-reliability, manually-input,
alphanumeric symbol carrying "AC-42", and the `MM,A` is not part of the
value. Reading those switches here rather than in the parser is what lets
the field data round-trip to the file byte for byte - the parser never has
to take it apart and put it back together.

The symbol itself is built by the `qrcode` package, which knows the version
table, the four error-correction levels, the interleaving and the eight
masks. What this module does is the ZPL half: which of those the switches and
the command's own parameters are asking for.
"""

import re

# The four error correction levels ZPL spells, and what `qrcode` calls each.
# ZPL's own order is by reliability; the package numbers them differently, so
# they are mapped by name rather than by value.
ERROR_CORRECTION = ('L', 'M', 'Q', 'H')

# What ^BQ's own d parameter means when it is left out, and when it is a
# letter QR has no level for. The manual gives two different answers -
# "Q = if empty, M = invalid values" - and means them: an omitted level is
# not the same as an unreadable one.
DEFAULT_ERROR_CORRECTION = 'Q'
INVALID_ERROR_CORRECTION = 'M'

# The mask ^BQ's own e parameter asks for when it leaves it out. The manual
# says 7; a printer picks it rather than evaluating all eight.
DEFAULT_MASK = 7

# The switches, in the fixed order the manual gives them:
#   [D<ii><jj><xx>,]  mixed mode - code number, divisions, parity
#   <H|Q|M|L>         error correction, mandatory
#   <A|M>,            data input, mandatory
#   [<N|A|B####|K>]   character mode, present only when data input is M
_SWITCHES = re.compile(
    r'^(?:D(?P<code>\d{2})(?P<divisions>\d{2})(?P<parity>[0-9A-Fa-f]{2}),)?'
    r'(?P<ecc>[HQMLhqml])'
    r'(?P<input>[AMam]),'
    r'(?P<rest>.*)$', re.DOTALL)

_CHARACTER_MODE = re.compile(r'^(?:(?P<mode>[NAK])|B(?P<count>\d{4}))',
                             re.DOTALL)


def read_switches(data: str) -> dict:
    """The switches at the front of a ^BQ field, and the data behind them.

    Returns `{'ecc', 'input', 'segments', 'mixed'}`, where `segments` is a
    list of (character mode, text) pairs - one for a normal symbol, more for
    a mixed-mode one, which splits its data at commas.

    A field with no switches at all is not an error: the manual's own
    examples always carry them, but a ^BQ whose ^FD is a bare value still
    prints, so the whole value is taken as automatic-input data. Getting
    that wrong would silently eat the first three characters of somebody's
    URL.
    """
    match = _SWITCHES.match(data or '')
    if match is None:
        return {'ecc': None, 'input': 'A', 'mixed': None,
                'segments': [(None, data or '')]}

    ecc = match.group('ecc').upper()
    manual = match.group('input').upper() == 'M'
    mixed = None
    if match.group('code'):
        mixed = (match.group('code'), match.group('divisions'),
                 match.group('parity').upper())

    rest = match.group('rest')
    # Mixed mode splits its data at commas, each piece with its own
    # character mode. A normal symbol is the same thing with one piece -
    # and its data may itself hold commas, so it is not split at all.
    pieces = rest.split(',') if mixed else [rest]
    segments = []
    for piece in pieces:
        if not manual:
            segments.append((None, piece))
            continue
        mode = _CHARACTER_MODE.match(piece)
        if mode is None:
            segments.append((None, piece))
        elif mode.group('mode'):
            segments.append((mode.group('mode'), piece[1:]))
        else:
            # B####, where #### is how many characters follow. The count is
            # what the printer reads; a piece that disagrees with its own
            # count is taken at the count, as the printer would.
            count = int(mode.group('count'))
            segments.append(('B', piece[5:5 + count]))
    return {'ecc': ecc, 'input': 'M' if manual else 'A', 'mixed': mixed,
            'segments': segments}


def error_correction(data: str, parameter: str) -> str:
    """The level this symbol is built at.

    The switch in the field data wins over the command's own parameter: it is
    the mandatory one, and a file that spells both means the one it had to
    write.
    """
    switched = read_switches(data)['ecc']
    if switched in ERROR_CORRECTION:
        return switched
    letter = (parameter or '').strip().upper()
    if not letter:
        return DEFAULT_ERROR_CORRECTION
    return letter if letter in ERROR_CORRECTION else INVALID_ERROR_CORRECTION


def encode(data: str, ecc: str = DEFAULT_ERROR_CORRECTION,
           mask: int = DEFAULT_MASK) -> list:
    """The symbol as rows of booleans, one per module, dark where True.

    Raises ValueError when the data will not fit any version at this error
    correction level - 2953 bytes at L, far less at H - which is what a
    printer does with it too: no symbol prints.
    """
    import qrcode                     # noqa: PLC0415 - see module docstring
    from qrcode import util as qr_util

    read = read_switches(data)
    code = qrcode.QRCode(
        error_correction=getattr(qrcode.constants,
                                 f"ERROR_CORRECT_{ecc}"),
        # ZPL's magnification is applied when the symbol is drawn, and its
        # quiet zone is the label around it, so the grid comes out bare.
        box_size=1, border=0,
        mask_pattern=mask if mask in range(8) else None)

    for mode, text in read['segments']:
        if not text:
            continue
        code.add_data(_segment(qr_util, mode, text))
    if not any(text for _mode, text in read['segments']):
        # A QR code of nothing is still a QR code - a printer draws the
        # smallest symbol rather than refusing - and `qrcode` will not make
        # one from no data at all.
        code.add_data('')

    code.make(fit=True)
    return [[bool(cell) for cell in row] for row in code.get_matrix()]


def _segment(qr_util, mode: str, text: str):
    """One piece of the data, in the character mode the switches asked for.

    A character mode the data cannot actually be written in - `N` in front of
    letters, say - falls back to the most compact mode that can hold it
    rather than refusing the symbol, the same way an unreadable error
    correction letter falls back to M. The symbol comes out bigger than the
    file asked for, which is visible, where a missing barcode is not.

    Kanji is drawn in byte mode: the encoder here has no Shift-JIS table, and
    a UTF-8 label is not in Shift-JIS to begin with. Recorded in
    FUNCTIONAL_SPEC.md section 18.
    """
    raw = qr_util.to_bytestring(text)
    wanted = {'N': qr_util.MODE_NUMBER, 'A': qr_util.MODE_ALPHA_NUM,
              'B': qr_util.MODE_8BIT_BYTE,
              'K': qr_util.MODE_8BIT_BYTE}.get(mode)
    optimal = qr_util.optimal_mode(raw)
    if wanted is None or wanted < optimal:
        wanted = optimal
    return qr_util.QRData(raw, mode=wanted, check_data=False)
