"""
Reading a .zpl file back into a Document.

Parsing is deliberately tolerant: an unrecognised command is skipped rather
than treated as an error, and a missing parameter falls back to the element
default, so ZPL written by another tool still opens.
"""

import base64
import io
import os
import re
from typing import Optional, Tuple

from . import fields as zpl_fields
from . import fonts as zpl_fonts
from . import graphic_store
from . import graphics
from . import symbology as symbologies
from . import transforms as zpl_transforms
from .model import (ORIENTATIONS, BarcodeElement, Document, FieldBlock,
                    FrameElement, ImageElement, StoredGraphicElement,
                    TextElement)

NOPRINT_KEY = '^FXDESIGNER_NOPRINT:'
NOPRINT_MARKER = '^FXDESIGNER_NOPRINT'
DPI_KEY = '^FXDESIGNER_DPI:'
PREVIEW_KEY = '^FXDESIGNER_PREVIEW:'
PATH_KEY = '^FXDESIGNER_PATH:'
GROUP_KEY = '^FXDESIGNER_GROUP:'

# The same keys as the tokeniser sees them: ^FX is the command, the rest is
# its parameters.
NOPRINT_PARAM = NOPRINT_MARKER[len('^FX'):]
DPI_PARAM = DPI_KEY[len('^FX'):]
PREVIEW_PARAM = PREVIEW_KEY[len('^FX'):]
PATH_PARAM = PATH_KEY[len('^FX'):]
GROUP_PARAM = GROUP_KEY[len('^FX'):]

# (no-print flag, group id) - what the designer markers ahead of a field ask
# of it, and the value of having asked nothing.
_NO_PENDING = (False, None)


def parse_label_size(zpl_content: str) -> Tuple[Optional[int], Optional[int]]:
    """The ^PW / ^LL label size recorded in the file, if it has one."""
    pw = re.search(r'\^PW(\d+)', zpl_content)
    ll = re.search(r'\^LL(\d+)', zpl_content)
    return (int(pw.group(1)) if pw else None,
            int(ll.group(1)) if ll else None)


COMMAND = re.compile(
    r'([\^~])([A-Za-z0-9@]{2})((?:(?!\^|~[A-Za-z0-9@]{2})[\s\S])*)', re.S)

# The codec a file's ^CI names, for one that is not UTF-8. The single-byte
# sets: 0-12 are Code Page 850 with a few national replacements (which are
# ASCII positions, so cp850 reads their bytes as the printer stores them), 13
# is CP850 itself, 27 and 31-36 the Windows code pages the manual lists, and
# 15 Shift-JIS, which Python knows. Not here, and so refused rather than
# guessed: 14, 16, 24 and 26, whose meaning is a *.DAT table on the printer,
# and 28-30, which are Unicode and so already tried. No ^CI at all reads as 0,
# the power-up value - a printer given such a file would read it that way.
_CODE_PAGES = {**{n: 'cp850' for n in range(14)},
               15: 'shift_jis', 27: 'cp1252', 31: 'cp1250', 33: 'cp1251',
               34: 'cp1253', 35: 'cp1254', 36: 'cp1255'}
_FIRST_ENCODING = re.compile(rb'\^CI(\d+)')


def decode_file(raw: bytes) -> Tuple[str, Optional[str]]:
    """The text of a .zpl file's bytes, and the code page it had to be read
    with - None for a UTF-8 file, which is every file this designer writes.

    Every read used to be open(..., encoding='utf-8'), so a file whose
    accents were single bytes - a ^CI0 file with é as 0x82, the way a printer
    at power-up reads it, or a ^CI27 one with é as 0xE9 - did not open at all.
    Now the ^CI the file declares picks the codec, and the caller says which
    one was used: the saved file will be UTF-8 with ^CI28 (see
    Document._encoding_zpl), which prints the same glyphs, but a file that
    relied on a printer's saved setting rather than its own ^CI will have
    been read as CP850 and may show the wrong accents - worth a look before
    a save converts them.

    A BOM is honoured first, as the manual's own alternative to ^CI: UTF-16
    by its BOM, and utf-8-sig so a UTF-8 BOM is dropped rather than left as
    a stray character ahead of ^XA. A ^CI whose bytes cannot be read - one of
    the table-driven Asian sets, or a ^CI28 that is not UTF-8 - raises rather
    than guesses, since a wrong guess would be written back as real text on
    the next save.
    """
    if raw[:2] in (b'\xff\xfe', b'\xfe\xff'):
        return raw.decode('utf-16'), None
    try:
        return raw.decode('utf-8-sig'), None
    except UnicodeDecodeError:
        pass
    match = _FIRST_ENCODING.search(raw)
    number = int(match.group(1)) if match else 0
    codec = _CODE_PAGES.get(number)
    if codec is None:
        raise ValueError(f"not UTF-8, and its ^CI{number} encoding is not one "
                         f"this designer can read")
    try:
        return raw.decode(codec), codec
    except UnicodeDecodeError as e:
        raise ValueError(f"not UTF-8, and does not read as ^CI{number} "
                         f"({codec}) either: {e}") from e


def read_file(path: str) -> Tuple[str, Optional[str]]:
    """A .zpl file as text, and the code page it had to be read with, if any.
    See decode_file."""
    with open(path, 'rb') as f:
        return decode_file(f.read())

# ZPL's own factory default font, used by any field that carries neither an ^A
# of its own nor a ^CF before it. No orientation: ^CF has no such parameter,
# and a field relying on it turns with ^FW alone.
DEFAULT_FONT = {'code': 'A', 'height': 9, 'width': 5, 'name': None}

# ^FW's power-up value: the orientation of every field that has an orientation
# parameter and leaves it out - an ^A with no letter, a field with no ^A at
# all, a barcode command with no letter. A running default like ^CF and ^BY.
DEFAULT_ORIENTATION = 'N'
_ORIENTATION_LETTERS = frozenset(code for _label, code in ORIENTATIONS)

# The fonts whose glyphs are scaled rather than chosen from a bitmap, and so
# the ones an omitted ^A width leaves proportional.
SCALABLE_FONTS = ('0', '@')

# ^BY's running defaults, which every later barcode inherits unless it is
# followed by another ^BY. `height` is None rather than a number to record that
# no ^BY has given one, which is a different thing from one having given 100.
DEFAULT_BARCODE = {'module_width': 2, 'ratio': 3.0, 'height': None}

# What a ^BC with no height of its own draws when no ^BY supplied one either.
# ZPL's power-up default is 10 dots, which a printer honours and which would
# make such a barcode a hairline on the canvas, so this is deliberately not it.
# Recorded as a deviation in FUNCTIONAL_SPEC.md section 18.
DESIGNER_BAR_HEIGHT = 100

# Commands the parser can skip without choking. Whether skipping one is worth
# telling the user about is a separate question, answered by workflow.MODELLED.
STRUCTURAL = {'^XA', '^XZ', '^FS', '^FX', '^CI', '^CF', '^LH', '^PR', '^MD',
              '^LT', '^LS', '^PO', '^MN', '^MM', '^MT', '^JM', '^FW'}


def tokenise(zpl_content: str):
    """Every command in the source, as (name, parameters) pairs.

    A ZPL command is a caret (or tilde) plus exactly two characters, and its
    parameters run to the next caret - wherever the newlines happen to fall.
    Reading line by line missed any command that did not start one, which is
    how `^FO45,50^BY3` lost its module width and `^A0N,70,70^BCN,...` lost
    both of its commands.

    Two characters is also what makes the font family fall out for free: ^A0,
    ^AF and ^A@ are one command whose second character is the font.
    """
    return [(m.group(1) + m.group(2).upper(), m.group(3))
            for m in COMMAND.finditer(zpl_content)]


# The three characters ZPL parses by rather than reads as data, and the
# command that moves each. ^CC, ^CT and ^CD (and their ~ twins) take one
# character - the new value - and it is in force from the very next byte:
# `^CC/` is followed by `/FO`, not `^FO`, and put back with `/CC^`.
REDEFINES = {'CC': 'format', 'CT': 'control', 'CD': 'delimiter'}
_DEFAULT_CHARACTERS = {'format': '^', 'control': '~', 'delimiter': ','}

# What COMMAND accepts as the two characters of a name. A prefix that is not
# followed by two of these starts nothing: the regex skips it, and so a bare
# `~` in ^FC's parameters is data rather than the start of a tilde command.
_NAME_CHARS = frozenset('ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789@')

# Commands whose parameters are free text rather than a delimited list. The
# delimiter is never rewritten inside them: a `;` in ^FD data under ^CD; is
# a semicolon, and ^FXDESIGNER_GROUP:1,2 keeps its own comma.
_FREE_TEXT = {'FD', 'FV', 'FX', 'FN'}

# Commands that begin with a delimited list and end with a data payload - hex
# or :Z64: - in which nothing is a delimiter, however it is currently spelled.
# Only this many leading parameters are rewritten.
_LEADING_PARAMETERS = {'GF': 4, 'DG': 3, 'DY': 5}

# The ^FH escape for each default character a field's data may hold literally
# once that character no longer starts a command.
_ESCAPES = {'^': '5E', '~': '7E', '_': '5F'}


def canonicalise(zpl_content: str) -> tuple:
    """The text with ZPL's default control characters restored, and every
    ^CC/^CT/^CD (or ~) that moved one, as written, in the order found.

    The tokeniser above knows only the defaults. Rather than teach it, and
    every parameter split after it, that `,` might be `;` today, a label that
    moves a character is rewritten once, here, into the label it would have
    been with the defaults - and the redefinitions themselves are left out,
    since that label no longer needs them. Everything downstream, the writer
    included, then sees ordinary ZPL: a save writes the standard characters
    and no redefinition, so the saved file prints the same label but no longer
    changes the printer's settings, which the load notice says.

    This is a scan and not a regex because the second redefinition is spelled
    with the first one's character: after `^CC/` the restoring command is
    `/CC^`, which no fixed pattern sees. The characters in force are tracked
    from the start of the text, where they are always the defaults - nothing
    but these commands can change them, so the first one is always spelled
    with `^` or `~`, and a text holding none of the six spellings holds no
    redefinition at all. That check is the fast path: it returns the text it
    was given, the same object, and every file that never used the feature
    takes it.

    The scan mirrors COMMAND, so a label it does rewrite tokenises as the
    printer would have read it: a prefix starts a command only when two name
    characters follow, and parameters run to the next format prefix or to a
    control prefix that starts a command. A prefix that starts nothing is
    written as its default and skipped by the regex exactly as a stray `^` is
    today. The text is scanned as a printer scans it, so a `^CC` inside ^FD
    data counts, because on the printer it would; a redefinition with no
    character after it, or a whitespace one, is recorded by its bare spelling
    and changes nothing - Zebra disallows it, and there is no printer
    behaviour to mirror.

    Field data is the one place the rewrite is not a substitution. The point
    of ^CC is to put a literal `^` in a field, and once `^` is the prefix
    again that byte would end the field, so any default character the data
    holds *while it is not in force* becomes the ^FH escape for it - under
    the field's own indicator if it has a ^FH, else under `_` with a ^FH
    supplied ahead of the ^FD, and any `_` already in the data escaped too so
    the new indicator cannot invent an escape. A ^FH written after its ^FD,
    against the manual, is not seen; a ^FX comment gets a space for each such
    character instead, since its content is not modelled and a `^` in it
    would end the comment early.
    """
    if not any(prefix + name in zpl_content
               for prefix in (_DEFAULT_CHARACTERS['format'],
                              _DEFAULT_CHARACTERS['control'])
               for name in REDEFINES):
        return zpl_content, []

    chars = dict(_DEFAULT_CHARACTERS)
    found = []
    out = []
    indicator = None    # the ^FH of the field being read, if it has one
    i, n = 0, len(zpl_content)

    def starts_command(at):
        """Whether the prefix at `at` begins a command, by COMMAND's rule."""
        return (at + 2 < n and zpl_content[at + 1] in _NAME_CHARS
                and zpl_content[at + 2] in _NAME_CHARS)

    def parameters_end(at):
        """Where the parameters that begin at `at` stop."""
        while at < n:
            ch = zpl_content[at]
            if ch == chars['format']:
                return at
            if ch == chars['control'] and starts_command(at):
                return at
            at += 1
        return n

    while i < n:
        ch = zpl_content[i]
        if ch != chars['format'] and ch != chars['control']:
            out.append(ch)
            i += 1
            continue
        default = (_DEFAULT_CHARACTERS['format'] if ch == chars['format']
                   else _DEFAULT_CHARACTERS['control'])
        if not starts_command(i):
            out.append(default)
            i += 1
            continue
        name = zpl_content[i + 1:i + 3]
        upper = name.upper()
        role = REDEFINES.get(upper)
        if role is not None:
            new = zpl_content[i + 3:i + 4]
            if new and not new.isspace():
                found.append(zpl_content[i:i + 4])
                chars[role] = new
                i += 4
            else:
                found.append(zpl_content[i:i + 3])
                i += 3
            continue
        end = parameters_end(i + 3)
        params = zpl_content[i + 3:end]
        i = end
        if upper in ('FO', 'FT', 'FS'):
            indicator = None
        elif upper == 'FH':
            indicator = zpl_fields.read_hex_indicator(params)
        # The default characters that may sit in data as literals: each one
        # that is not, at this moment, what starts a command.
        literal = [c for c, role in (('^', 'format'), ('~', 'control'))
                   if chars[role] != c]
        if upper in ('FD', 'FV'):
            if any(c in params for c in literal):
                if indicator is None:
                    indicator = '_'
                    out.append(default + 'FH' + indicator)
                    literal.append('_')
                params = ''.join(indicator + _ESCAPES[c] if c in literal else c
                                 for c in params)
        elif upper == 'FX':
            params = ''.join(' ' if c in literal else c for c in params)
        elif upper not in _FREE_TEXT and chars['delimiter'] != ',':
            keep = _LEADING_PARAMETERS.get(upper)
            if keep is None:
                params = params.replace(chars['delimiter'], ',')
            else:
                # A bounded split touches only the first `keep` delimiters;
                # the payload, whatever it holds, stays in the last piece.
                params = ','.join(params.split(chars['delimiter'], keep))
        out.append(default + name + params)
    return ''.join(out), found


def control_redefinitions(zpl_content: str) -> list:
    """Every ^CC/^CT/^CD (or ~) in the text, as written, in the order found."""
    return canonicalise(zpl_content)[1]


def _expand_hidden(zpl_content: str) -> str:
    """Decode ^FXDESIGNER_NOPRINT payloads back into the line stream, in place.

    In place, because list position is z-order: a hidden element expanded at
    the end would come back on top of everything it was drawn behind. The
    marker line tells the parser to flag whatever the next block builds.
    """
    lines = []
    for raw in zpl_content.split('\n'):
        stripped = raw.strip()
        if stripped.startswith(NOPRINT_KEY):
            try:
                body = base64.b64decode(stripped[len(NOPRINT_KEY):]).decode('utf-8')
            except Exception:
                continue
            lines.append(NOPRINT_MARKER)
            lines.extend(body.split('\n'))
        else:
            lines.append(raw)
    return '\n'.join(lines)


def _decode_gfa_image(x: int, y: int, params: str, preview_b64, path_hint):
    """An image element from a ^GF field, best source first.

    The 1-bit data is the only thing a printer needs, but it is also the worst
    thing to edit from - it has already been dithered. So the original file
    wins, then the embedded JPEG, and the ^GF data is the last resort (and the
    only option for ZPL that came from another tool).
    """
    decoded = graphics.decode(params)
    header = graphics.header(params)
    bpr = decoded[1] if decoded else (header[3] if header else 0)
    if bpr <= 0:
        return None
    gf_w = bpr * 8
    # The row count comes from the data when there is data: ^GF's two byte
    # counts mean different things once it is compressed, and generators
    # disagree about which is which.
    gf_h = (len(decoded[0]) // bpr) if decoded else (header[2] // bpr)

    # 1. Original file still present - highest quality
    if path_hint and os.path.exists(path_hint):
        try:
            return ImageElement(x, y, gf_w, gf_h, image_path=path_hint)
        except Exception:
            pass

    # 2. Embedded JPEG preview - same quality as first import
    if preview_b64:
        try:
            from PIL import Image
            jpeg_data = base64.b64decode(preview_b64)
            pil_img = Image.open(io.BytesIO(jpeg_data)).convert('RGB')
            return ImageElement(x, y, gf_w, gf_h, _pil_image=pil_img)
        except Exception:
            pass

    # 3. Decode the 1-bit ^GF data - last resort
    if decoded is not None:
        raw, bpr = decoded
        try:
            import numpy as np
            from PIL import Image
            rows = len(raw) // bpr
            arr = np.frombuffer(raw, dtype=np.uint8).reshape(rows, bpr)
            unpacked = np.unpackbits(arr, axis=1)[:, :gf_w]
            # A set bit is black, so the bits invert to greyscale levels.
            pixel_data = ((1 - unpacked) * 255).astype(np.uint8)
            pil_img = Image.fromarray(pixel_data, mode='L').convert('RGB')
            return ImageElement(x, y, gf_w, rows, _pil_image=pil_img)
        except Exception:
            pass
    return None


def _capture_image_save(expanded: str, offset: int, spec: str, doc: Document) -> None:
    """^IS: flatten everything drawn before this point and store it.

    Reusing the standalone renderer rather than a third drawing
    implementation: "everything drawn before this point" is exactly what
    running it over the text up to here, and no further, produces. A
    snapshot that fails to render is skipped rather than raised - a bad ^IS
    must not be the reason the rest of the file fails to open.
    """
    from . import renderer as zpl_renderer

    prefix = expanded[:offset]
    if not prefix.rstrip().endswith('^XZ'):
        prefix += '^XZ'
    try:
        image = zpl_renderer.ZPLRenderer(
            width=doc.label_width, height=doc.label_height).render(prefix)
    except Exception:
        return
    graphic_store.store(spec, image)


def _read_stored_graphic(cmd: str, params: str):
    """The device spec and magnification an ^XG/^IM field names.

    ^IM has no magnification of its own - it is, per the ZPL manual,
    "identical to ^XG... except there are no sizing parameters" - so both are
    read the same way and ^IM's is simply always 1,1. That is what lets one
    element class serve both commands.
    """
    parts = [p.strip() for p in params.split(',')]
    spec = parts[0] if parts and parts[0] else 'R:UNKNOWN.GRF'

    def number(index):
        if len(parts) > index and parts[index]:
            try:
                return max(1, min(10, int(parts[index])))
            except ValueError:
                pass
        return 1

    mag_x = number(1) if cmd == '^XG' else 1
    mag_y = number(2) if cmd == '^XG' else 1
    return spec, mag_x, mag_y


def read_field_table(tokens):
    """The values and prompts a format's ^FN#^FD pairs give its fields.

    Read in a pass of its own because a pair can appear *after* the field that
    needs its value - that is exactly what a recall call is, a list of pairs for
    geometry declared earlier - and because one value fills every field sharing
    the number. Gathering them field by field in the main loop could only ever
    see the ones already passed.
    """
    table = zpl_fields.FieldTable()
    pending = None
    for cmd, params in tokens:
        if cmd == '^FN':
            read = zpl_fields.read(params)
            pending = None
            if read is not None:
                number, prompt = read
                table.set_prompt(number, prompt)
                pending = number
        elif cmd in ('^FD', '^FV') and pending is not None:
            table.set_value(pending, params)
            pending = None
        elif cmd in ('^FS', '^FO', '^FT', '^XZ'):
            pending = None
    return table


def parse_zpl(zpl_content: str, renderer=None) -> Tuple[Document, Optional[int]]:
    """Build a Document from ZPL text.

    Returns the document and the resolution the file records, or None when it
    records none. The caller decides what to do about a mismatch - the "assume
    203 dpi, and say it was assumed" rule lives with the prompt, not here.
    """
    # First, before the label-size regex or anything else reads the text:
    # from here on the file is spelled with ZPL's default characters.
    zpl_content, _ = canonicalise(zpl_content)
    doc = Document()
    width, height = parse_label_size(zpl_content)
    if width:
        doc.label_width = width
    if height:
        doc.label_height = height

    expanded = _expand_hidden(zpl_content)
    tokens = tokenise(expanded)
    # Same matches tokenise() itself found, kept alongside the tokens rather
    # than folded into its return value - `tokenise` is used elsewhere for
    # just the (cmd, params) pairs, and only ^IS needs to know where in the
    # text a token started (see _capture_image_save).
    token_offsets = [m.start() for m in COMMAND.finditer(expanded)]
    doc.fields = read_field_table(tokens)
    loaded_dpi = None
    # Designer markers written in front of a field, held until it is built.
    pending = _NO_PENDING
    field = None            # commands gathered since the last ^FO
    # ^CF sets the font for every field that does not name one of its own, so
    # it has to be carried between fields rather than gathered into one. ^BY is
    # the same kind of command for barcodes.
    default_font = dict(DEFAULT_FONT)
    default_barcode = dict(DEFAULT_BARCODE)
    # ^FW is the same kind of command for orientation: the turn every field
    # that leaves its own out takes, whether text or barcode.
    default_orientation = DEFAULT_ORIENTATION
    # The ^LH/^LS offset in force. Elements hold the absolute dot position, so
    # the canvas, dragging and clamping never have to know these exist.
    origin = (0, 0)
    home = (0, 0)           # the ^LH in force, which is not always the first
    seen_home = False

    for index, (cmd, params) in enumerate(tokens):
        if cmd == '^FX':
            key = params.strip()
            if key.startswith(DPI_PARAM):
                try:
                    loaded_dpi = int(key[len(DPI_PARAM):])
                except ValueError:
                    pass
            elif key == NOPRINT_PARAM:
                pending = (True, pending[1])
            elif key.startswith(GROUP_PARAM):
                # The path of groups the next field is in, outermost first;
                # a marker that is not all integers is ignored as a whole.
                try:
                    path = tuple(int(p) for p in key[len(GROUP_PARAM):].split(','))
                except ValueError:
                    pass
                else:
                    pending = (pending[0], path)
            elif field is not None and key.startswith(PREVIEW_PARAM):
                field['preview'] = key[len(PREVIEW_PARAM):]
            elif field is not None and key.startswith(PATH_PARAM):
                field['path'] = key[len(PATH_PARAM):]
            continue

        if cmd in ('^LH', '^LS', '^LT', '^PO', '^PM', '^LR'):
            # What the format says about the label as a whole. ^LH is a running
            # origin - "this command affects only fields that come after it" -
            # so it is read wherever it appears, like ^CF and ^BY. The document
            # keeps the first one to write back; later ones still land every
            # field in the right absolute place.
            if cmd == '^LH':
                home = zpl_transforms.read_home(params)
                if home is None:        # not a position; not an origin
                    continue
                if not seen_home:
                    doc.transform.home, seen_home = home, True
            elif cmd == '^LS':
                shift = zpl_transforms.read_shift(params)
                if shift is None:
                    continue
                doc.transform.shift = shift
            elif cmd == '^LT':
                top = zpl_transforms.read_top(params)
                if top is None:
                    continue
                doc.transform.top = top
            elif cmd == '^PO':
                doc.transform.invert = zpl_transforms.read_flag(params, 'I')
            elif cmd == '^PM':
                doc.transform.mirror = zpl_transforms.read_flag(params)
            else:
                doc.transform.reverse = zpl_transforms.read_flag(params)
            if cmd in ('^LH', '^LS'):
                # ^LT is not in the offset: it registers the label against the
                # media rather than laying fields out on it, so applying it
                # would move the design on screen to describe a printer
                # adjustment. See zplcore/transforms.py.
                # Through the shared function, not spelled again here: the
                # running home and the document's differ, but a second copy of
                # the arithmetic could have a sign wrong and the round-trip
                # would still look right, since folding in and taking back out
                # would make the same mistake.
                origin = zpl_transforms.field_offset(home, doc.transform.shift)
            continue

        if cmd == '^DF':
            # Stored format: everything after it is saved on the printer rather
            # than printed, so the design this file describes *is* the template.
            doc.stored_format = params.strip() or None
            continue

        if cmd == '^XF':
            # A recall merges data into geometry held on the printer. Recorded
            # so a save writes it back: opening one used to empty the file.
            recalled = params.strip()
            if recalled:
                doc.recalls.append(recalled)
            continue

        if cmd == '^IL':
            # The graphic counterpart of ^XF: a stored image, always placed at
            # ^FO0,0, for the fields after it to overlay. Recorded rather than
            # turned into an element - like ^XF, this is data the printer
            # holds, not geometry this file drew - so a save writes it back
            # unchanged and the canvas/renderer resolve it (if this session's
            # ^IS has it) at draw time instead.
            doc.image_load = params.strip() or None
            continue

        if cmd == '^IS':
            # Saves everything drawn so far as a named image. Recorded so a
            # save writes it back, and captured into this session's graphic
            # store now, while `expanded` still has the text to render - see
            # _capture_image_save.
            saved = params.strip()
            if saved:
                doc.image_saves.append(saved)
                _capture_image_save(expanded, token_offsets[index],
                                    saved.split(',')[0].strip(), doc)
            continue

        if cmd == '^PQ':
            (doc.print_quantity, doc.print_pause_count,
             doc.print_replicates, doc.print_override_pause) = \
                _read_print_quantity(params)
            continue

        if cmd == '^CV':
            # A switch the printer keeps until told otherwise, so the last one
            # in the format is the state it leaves behind. Read whether or not
            # a field is open, like ^BY. Nothing to draw: it checks barcode
            # data at print time.
            doc.code_validation = zpl_transforms.read_flag(params)
            continue

        if cmd == '^CI':
            # The encoding the field data is in. The first one is kept - the
            # one the fields at the top were written under, and the ^LH rule
            # - because a trailing ^CI0 that puts the printer back before ^XZ
            # is something generators do write, and must not be the one a
            # save puts at the top. Carried verbatim, remap pairs and all;
            # nothing to draw, though the model's own rule for what is
            # written back is in Document._encoding_zpl.
            encoding = read_encoding(params)
            if encoding is not None and doc.encoding is None:
                doc.encoding = encoding
            continue

        if cmd == '^CW':
            # A letter assigned to a downloaded font, verbatim: resolving it
            # into the fields as ^CF is would have to spell the font as an
            # ^A@, which only ever writes E:NAME.TTF. A letter assigned twice
            # keeps its place and takes the last assignment - the printer's
            # own end state.
            letter = params.strip()[:1]
            if letter.isalnum():
                doc.font_identifiers[letter.upper()] = params.strip()
            continue

        if cmd == '^FL':
            # A font linked to (or unlinked from) another, for the glyphs it
            # lacks. Every one in order - an unlink after a link is not the
            # same as neither.
            if params.strip():
                doc.font_links.append(params.strip())
            continue

        if cmd == '^CF':
            default_font = _read_default_font(params, default_font)
            continue

        if cmd == '^FW':
            default_orientation = read_field_orientation(params,
                                                         default_orientation)
            continue

        if cmd == '^BY':
            # Read whether or not a field is open, because it is a running
            # default: one written before the first ^FO belongs to every
            # barcode after it. An open field also takes it immediately, so a
            # ^BY between the ^FO and the ^BC still applies to that barcode.
            default_barcode = _read_barcode_default(params, default_barcode)
            if field is not None:
                field['module_width'] = default_barcode['module_width']
                field['ratio'] = default_barcode['ratio']
                field['bar_height'] = default_barcode['height']
            continue

        if cmd in ('^FO', '^FT'):
            # A field that never saw ^FS still ends here, at the next one
            pending = _flush(field, doc, renderer, pending)
            match = re.match(r'\s*(-?\d+),(-?\d+)', params)
            field = _new_field(int(match.group(1)) + origin[0],
                               int(match.group(2)) + origin[1],
                               default_font, default_barcode,
                               default_orientation) if match else None
            # ^FT places a field exactly as ^FO does, but names its baseline
            # rather than its top. Opening no field on it did not degrade such
            # a label - it dropped every field in it, so a file from another
            # tool opened completely empty.
            if field is not None:
                field['typeset'] = (cmd == '^FT')
            continue

        if cmd == '^FS':
            pending = _flush(field, doc, renderer, pending)
            field = None
            continue

        if field is None:
            continue

        if cmd.startswith('^A'):
            field['font'] = read_font(cmd[2], params, field['default_font'],
                                      default_orientation)
        elif cmd == '^FB':
            field['block'] = FieldBlock.from_zpl(params)
        elif cmd == '^FR':
            field['reverse'] = True
        elif cmd in BARCODE_COMMANDS:
            field['barcode'] = _read_barcode(cmd, params, field['bar_height'],
                                             default_orientation,
                                             loaded_dpi or doc.dpi)
        elif cmd.startswith('^B') or cmd == '^GS':
            # QR, Data Matrix, PDF417 and the rest - a symbology this designer
            # cannot draw. Recorded so the field is dropped, because falling
            # through to the text branch did not merely lose the barcode: it
            # put a text element holding the barcode's data on the label in
            # its place.
            #
            # ^GS draws a glyph from the symbol font and is the same trap for
            # the same reason. It is not a ^B command, so it went on falling
            # through: ^GSN,50,50^FDA arrived as a nine-dot text element
            # reading "A".
            field['symbology'] = cmd
        elif cmd == '^GB':
            field['frame'] = params
        elif cmd == '^GF':
            field['graphic'] = params
        elif cmd in ('^IM', '^XG'):
            field['stored_graphic'] = (cmd, params)
        elif cmd == '^FN':
            read = zpl_fields.read(params)
            if read is not None:
                field['field_number'], field['field_prompt'] = read
        elif cmd == '^SN':
            read = zpl_fields.read_serial(params)
            if read is not None:
                (field['serial_start'], field['serial_increment'],
                 field['serial_leading_zero']) = read
        elif cmd == '^SF':
            field['serial_field_raw'] = params
        elif cmd == '^FC':
            field['clock_format'] = True
            field['clock_chars'] = zpl_fields.read_clock_chars(params)
        elif cmd == '^FH':
            field['hex_indicator'] = zpl_fields.read_hex_indicator(params)
        elif cmd == '^FD':
            field['data'] = params
        elif cmd == '^FV':
            # ^FV is ^FD for a field the printer clears after printing. Reading
            # it as data is what stops such a field vanishing outright.
            field['data'] = params

    _flush(field, doc, renderer, pending)

    doc.selected_element = None
    return doc, loaded_dpi


def _new_field(x: int, y: int, default_font=None, default_barcode=None,
               default_orientation=DEFAULT_ORIENTATION) -> dict:
    """The state gathered between a ^FO and the ^FS that ends it.

    `font` stays None until an ^A names one, because "this field named a font"
    and "this field inherits the default" are different things: a barcode with
    no ^A of its own must go on writing none, while text with no ^A prints in
    whatever ^CF last set - and turns the way ^FW last said, since ^CF has no
    orientation of its own to give it.

    The three ^BY values are not like that: ZPL has a default for each, so a
    field always has one, whether from the last ^BY or from the power-up value.
    """
    inherited = dict(default_barcode or DEFAULT_BARCODE)
    return {'x': x, 'y': y, 'block': None, 'font': None,
            'field_number': None, 'field_prompt': None,
            'serial_start': None, 'serial_increment': None,
            'serial_leading_zero': False,
            'clock_format': False, 'clock_chars': None,
            'serial_field_raw': None, 'hex_indicator': None,
            'module_width': inherited['module_width'],
            'ratio': inherited['ratio'],
            'bar_height': inherited['height'],
            'default_font': dict(default_font or DEFAULT_FONT),
            'default_orientation': default_orientation,
            'barcode': None, 'frame': None, 'graphic': None,
            'stored_graphic': None, 'data': None,
            'preview': None, 'path': None, 'typeset': False, 'symbology': None,
            'reverse': False}


def _read_default_font(params: str, current: dict) -> dict:
    """^CFf,h,w - the font every later field uses unless it names its own.

    Each parameter is optional and keeps its previous value when omitted, which
    is what makes a bare ^CF0 mean "font 0, sizes unchanged".
    """
    parts = [p.strip() for p in params.split(',')]
    font = dict(current)
    if parts and parts[0]:
        font['code'] = parts[0][0].upper()
        font['name'] = None
    for index, key in ((1, 'height'), (2, 'width')):
        if len(parts) > index and parts[index]:
            try:
                font[key] = int(parts[index])
            except ValueError:
                pass
    return font


def read_encoding(params: str):
    """^CIa,s1,d1,... - the parameters, verbatim, or None for a ^CI that names
    no character set.

    Verbatim because everything after the number is a remap table the printer
    applies and nothing here simulates: re-spelling it could only lose a pair.
    The number itself is the one thing checked, since the manual gives `a` no
    default and a bare ^CI written back would be a command with nothing in it.
    """
    stripped = params.strip()
    try:
        int(stripped.split(',')[0])
    except ValueError:
        return None
    return stripped


def read_field_orientation(params: str, current: str) -> str:
    """^FWr - the orientation every later field turns to unless it names its
    own.

    The manual's own example is the case: after ^FWR, ^A0N,25,20 prints
    upright and ^A0,25,20 prints turned. Every barcode command defers the same
    way, and a field with no ^A at all has nothing else to defer to. Skipping
    the command did not merely draw such a field upright - a save wrote ^A0N
    back, pinning the field to a turn the file never gave it.

    Only the four letters change it. A bare ^FW, or one with a letter ZPL does
    not define, keeps the value in force: the ^CF rule for an omitted
    parameter, and the reading that never turns a field the file did not
    spell. The justification ^FW also carries (^FWr,z, x.14 firmware) is not
    read - ^FO's own z is not either. See FUNCTIONAL_SPEC.md section 18.
    """
    letter = params.strip()[:1].upper()
    return letter if letter in _ORIENTATION_LETTERS else current


def _read_barcode_default(params: str, current: dict) -> dict:
    """^BYw,r,h - the module width, ratio and bar height later fields inherit.

    Each parameter is optional and keeps its previous value when omitted, the
    same rule ^CF follows, which is what makes a bare ^BY3 mean "module width 3,
    ratio and height unchanged".

    ZPL calls this a default and means it across fields: "it stays in effect
    until another ^BY command is encountered". Reading it only inside an open
    field dropped every ^BY written at the top of a format - which is where the
    manual's own examples put it, and where most generators emit it - so a
    barcode came back at the power-up module width of 2 and printed at half the
    width it was written at. Nothing was said, because ^BY is modelled.
    """
    parts = [p.strip() for p in params.split(',')]
    default = dict(current)

    def number(index, key, cast):
        if len(parts) > index and parts[index]:
            try:
                default[key] = cast(parts[index])
            except ValueError:
                pass

    number(0, 'module_width', int)
    number(1, 'ratio', float)
    number(2, 'height', int)
    return default


def _read_print_quantity(params: str) -> tuple:
    """^PQq,p,r,o - copies, pause count, RFID replicates, override-pause flag.

    Each is independently optional; a blank or unreadable one falls back to
    ZPL's own default rather than raising, since this is exactly the kind of
    command another tool's ZPL should not be rejected over.
    """
    parts = [p.strip() for p in (params or '').split(',')]

    def integer(index, fallback):
        if len(parts) > index and parts[index]:
            try:
                return int(parts[index])
            except ValueError:
                pass
        return fallback

    quantity = integer(0, 1) or 1
    pause_count = integer(1, 0)
    replicates = integer(2, 0)
    override_pause = len(parts) > 3 and parts[3].strip().upper() == 'Y'
    return quantity, pause_count, replicates, override_pause


def read_font(code: str, params: str, default_font=None,
              default_orientation=DEFAULT_ORIENTATION) -> dict:
    """^A<code><orientation>,<h>,<w> - the font a field names for itself.

    The designator is the command's second character, so every built-in font
    is read the same way; ^A@ additionally names a font downloaded to the
    printer, e.g. ^A@N,53,19,E:DEJAVUSA.TTF.

    Every parameter is optional, and an omitted one keeps the default for that
    position: the sizes ^CF's, the same rule _read_default_font applies to ^CF
    itself, and the orientation ^FW's. Demanding all three is what made ^A0N,40
    come back as ^A0N,36,20, losing the height it did give while the preview,
    which demanded nothing, drew it at 40.

    The orientation is the first parameter, and dropping it is why text was
    the one element that could not be turned: it loaded flat and saved flat.
    Reading an omitted one as N was the quieter half of the same fault: under
    ^FWR the manual's own ^A0,25,20 is turned, and came back upright.
    """
    current = dict(default_font or DEFAULT_FONT)
    # The font file is split off first: its device path carries commas of its
    # own, e.g. ^A@N,53,19,E:DEJAVUSA.TTF.
    parts = [p.strip() for p in params.split(':', 1)[0].split(',')]

    def number(index, fallback):
        if len(parts) > index and parts[index]:
            try:
                return int(parts[index])
            except ValueError:
                pass
        return fallback

    letter = re.match(r'\s*([A-Za-z])', parts[0]) if parts else None
    height = number(1, current['height'])
    # A scalable font given no width is proportional. ^CF still wins when it
    # set one for a scalable font, but inheriting a bitmap font's width would
    # squeeze ^A0N,40 into five dots rather than letting it keep its shape.
    inherited = current['width']
    if code in SCALABLE_FONTS and current['code'] not in SCALABLE_FONTS:
        inherited = height

    name = None
    if code == '@':
        named = re.search(r'[^:,]*:([^.,]+)', params)
        if named:
            name = named.group(1).upper()
    return {'code': code, 'height': height, 'width': number(2, inherited),
            'name': name,
            'orientation': (letter.group(1).upper() if letter
                            else default_orientation)}


def _read_frame(params: str):
    """^GBw,h,t,c,r - as (width, height, thickness, colour, rounding).

    The width and the height both default to the thickness and are clamped up
    to it, which is how ZPL spells a rule: ^GB300,0,4 is a 300 x 4 line, not a
    box with no height. Demanding two numbers dropped ^GB300 and ^GB,,4
    outright, and let a rule through as a box the canvas then drew as nothing.
    """
    parts = [p.strip() for p in params.split(',')]

    def number(index, fallback):
        if len(parts) > index and parts[index]:
            try:
                return int(parts[index])
            except ValueError:
                pass
        return fallback

    thickness = max(1, number(2, 1))
    # The colour is a letter, which is why a digits-only pattern silently
    # dropped it along with the rounding after it.
    colour = parts[3][:1].upper() if len(parts) > 3 and parts[3] else 'B'
    return (max(thickness, number(0, thickness)),
            max(thickness, number(1, thickness)),
            thickness, colour, number(4, 0))


BARCODE_COMMANDS = symbologies.COMMAND_PARAMS


def _read_barcode(cmd: str, params: str, default_height=None,
                  default_orientation=DEFAULT_ORIENTATION,
                  dpi=zpl_fonts.DEFAULT_DPI) -> dict:
    """A barcode command's own parameters, whichever command it is.

    Every parameter is carried through: the flags decide whether the digits
    print under the bars and, where a symbology has one, whether and how a
    check digit is added, and the rest - a QR code's error correction, a
    Data Matrix's quality - decide what the symbol is at all. Re-emitting a
    barcode without them would change the label. Each command spells its own
    subset in its own order, which is what zplcore.symbology's catalogue
    exists to put back into one shape: the (show, above, check, mode) order
    `options` always uses regardless of symbology, plus the rest by name.

    An omitted height is ^BY's, which is what its third parameter is for.
    Hard-coding 100 here turned ^BY3,3.0,150^BCN into a barcode a third
    shorter than the file asked for.

    An omitted magnification is the one the manual gives for the print
    resolution - 2 at 200 dpi, 3 at 300 - since a matrix symbology has no
    ^BY to fall back on. It is resolved here rather than left blank so the
    file this designer writes states the size it is drawing, instead of
    coming back a different size on a printer with a different head.

    An omitted orientation is ^FW's, as an ^A's is. It stays the empty string
    while ^FW is at its power-up N, so a barcode a file never turned is written
    back as it was read - ^BC,100, no letter grown - and only a default that
    actually turns it is spelled out on the way back.
    """
    parts = [p.strip() for p in params.split(',')]
    fallback = DESIGNER_BAR_HEIGHT if default_height is None else default_height
    names = BARCODE_COMMANDS[cmd]
    fields = dict(zip(names, parts))

    orientation = '' if default_orientation == 'N' else default_orientation
    o = fields.get('o', '')
    if o[:1].isalpha():
        orientation = o[:1].upper()
    symbology = symbologies.SYMBOLOGY_OF[cmd]
    try:
        height = int(fields['h']) if fields.get('h') else 0
    except ValueError:
        height = 0
    if not height:
        # Left out. A height measured in modules rather than dots - PDF417's
        # row height - means "divide ^BY's whole-symbol height by however
        # many rows the data needs", which cannot be worked out until the
        # data has been encoded, so it is left at zero for the element to
        # resolve. Everything else takes ^BY's height as it stands.
        height = 0 if symbologies.HEIGHT_UNIT.get(symbology) == 'modules' \
            else fallback

    magnification = None
    if 'w' in names:
        try:
            magnification = int(fields['w']) if fields.get('w') else 0
        except ValueError:
            magnification = 0
        if not magnification:
            # Left out. Data Matrix means "fit the symbol into the height
            # ^BY gives", which cannot be worked out until the data has been
            # encoded, so it is left at zero for the element to resolve;
            # everything else means the manual's default for this head.
            if symbology not in symbologies.SIZED_BY_HEIGHT:
                magnification = symbologies.default_magnification(dpi)

    return {'symbology': symbology,
            'orientation': orientation,
            'height': height,
            'magnification': magnification,
            'options': (fields.get('f', ''), fields.get('g', ''),
                        fields.get('e', ''), fields.get('m', '')),
            'params': {name: fields.get(name, '') for name in names
                       if name not in symbologies.SHARED_PARAMS}}


def _flush(field, doc, renderer, pending) -> tuple:
    """Turn a gathered field into an element. Returns the markers still pending.

    The markers are for the next field, whatever it builds: a field that turns
    out to be something the model cannot hold uses them up all the same, so
    they cannot fall through to the supported field after it and hide or
    group one the file never meant. Only a marker with no field yet at all -
    the ^FO has not arrived - is carried.
    """
    if field is None:
        return pending

    no_print, group = pending
    before = len(doc.elements)
    element = _build_element(field, doc, renderer)
    if element is not None:
        if field['typeset']:
            _apply_typeset(element, doc)
        element.reverse_print = field['reverse']
        doc.elements.append(element)
    for el in doc.elements[before:]:
        if no_print:
            el.print_enabled = False
        el.group = group
    return _NO_PENDING


def _apply_typeset(element, doc) -> None:
    """Move an element placed by ^FT, whose y is a baseline and not a top.

    The offset is kept on the element rather than normalised away, so a save
    writes the ^FT back at the y it came from. Our idea of a font's ascent is
    an estimate, and converting to ^FO would bake that estimate into the file
    every time such a label was opened and saved.
    """
    if element.element_type == 'text':
        from . import textraster
        offset = textraster.baseline_offset(
            element.font_path or doc.font_path, element.font_height)
    else:
        # ^FT names the bottom-left corner of everything that is not text.
        offset = element.height
    element.typeset = offset
    element.y -= offset


def _build_element(field, doc, renderer):
    """The element a field describes, or None if it describes none.

    Decided once the whole field has been read rather than at the first
    command that looks decisive, so a ^FB sitting between the font and the
    data no longer loses the element.
    """
    x, y = field['x'], field['y']

    # True for any field whose value the printer supplies rather than the
    # file - a recalled ^FN, an incrementing ^SN, or a clock-substituted ^FC.
    # Such a field carries no ^FD of its own, so treating it the same as one
    # with none written at all is what stops it from either vanishing (the
    # text branch below) or being handed an invented value (the barcode
    # branch), the same trap ^FN alone used to fall into.
    printer_generated = (field['field_number'] is not None
                        or field['serial_increment'] is not None
                        or field['clock_format'])

    if field['graphic'] is not None:
        # Every format reaches the decoder, so one that cannot be read fails
        # where it can be reported rather than at a regex that matched only
        # the spelling this designer writes.
        return _decode_gfa_image(x, y, field['graphic'],
                                 field['preview'], field['path'])

    if field['stored_graphic'] is not None:
        # ^XG/^IM name an image this file never carries the bytes for - only
        # this session's own ^IS can supply them (see graphic_store) - so
        # this element holds the reference, not pixels, and resolves live
        # whenever it is drawn. If this session already has it, size the box
        # to match - magnified, as ^XG asks - so the canvas lays out the rest
        # of the label the way it will really print; otherwise a placeholder
        # size, since there is nothing yet to measure.
        cmd, params = field['stored_graphic']
        spec, mag_x, mag_y = _read_stored_graphic(cmd, params)
        resolved = graphic_store.recall(spec)
        if resolved is not None:
            width, height = resolved.width * mag_x, resolved.height * mag_y
        else:
            width, height = 200, 200
        return StoredGraphicElement(x, y, width, height, command=cmd[1:],
                                    device_spec=spec, mag_x=mag_x, mag_y=mag_y)

    if field['frame'] is not None:
        return FrameElement(x, y, *_read_frame(field['frame']))

    if field['barcode'] is not None:
        bc = field['barcode']
        # A ^A before the ^BC selects the interpretation line's font, not a
        # text element's, so it belongs to the barcode.
        font = field['font']
        # A field whose data the printer supplies has none of its own and
        # must not be given any: `or "123456789"` is a default for a barcode
        # the user has just created, and applying it here invented a value
        # that appeared nowhere in the file and then wrote it to disk.
        value = field['data']
        if value is None:
            value = '' if printer_generated else "123456789"
        # A matrix symbology carries its own magnification and ignores ^BY's
        # module width entirely, so the command's own number wins where it
        # has one - including a zero, which is ^BX's way of saying "fit the
        # symbol into ^BY's height instead", and which the element resolves
        # once it knows how many rows the data needs.
        module_width = (bc['magnification'] if bc['magnification'] is not None
                        else field['module_width'])
        return BarcodeElement(x, y, height=bc['height'],
                              barcode_value=value,
                              module_width=module_width,
                              params=bc['params'],
                              total_height=field['bar_height'],
                              ratio=field['ratio'],
                              orientation=bc['orientation'],
                              options=bc['options'],
                              symbology=bc['symbology'],
                              field_number=field['field_number'],
                              field_prompt=field['field_prompt'],
                              serial_start=field['serial_start'],
                              serial_increment=field['serial_increment'],
                              serial_leading_zero=field['serial_leading_zero'],
                              clock_format=field['clock_format'],
                              clock_chars=field['clock_chars'],
                              serial_field_raw=field['serial_field_raw'],
                              hex_indicator=field['hex_indicator'],
                              font=(font['code'], font['height'], font['width'])
                              if font else None)

    if field['symbology'] is not None:
        # Already named in the load warning. Dropping it here is what stops a
        # Code 39 sixty dots tall from arriving as nine-dot text.
        return None

    # A field whose data the printer supplies carries no ^FD of its own -
    # that is the point of ^FN/^SN/^FC - so requiring data discarded every
    # such field in a stored format.
    if field['data'] is not None or printer_generated:
        return _build_text(x, y, field, doc, renderer)

    return None


def _build_text(x, y, field, doc, renderer):
    """A text element, with its font found again on this machine if it can be."""
    # No ^A in the field means whatever ^CF last set, which is what the printer
    # would use. Discarding the field for want of an ^A lost it altogether.
    font = field['font'] or field['default_font']
    element = TextElement(x, y, field['data'] or '', font['height'],
                          font['width'], font_code=font['code'],
                          field_number=field['field_number'],
                          field_prompt=field['field_prompt'],
                          serial_start=field['serial_start'],
                          serial_increment=field['serial_increment'],
                          serial_leading_zero=field['serial_leading_zero'],
                          clock_format=field['clock_format'],
                          clock_chars=field['clock_chars'],
                          serial_field_raw=field['serial_field_raw'],
                          hex_indicator=field['hex_indicator'])
    # ^A's letter when the field named a font - read against the ^FW in force
    # at the ^A - and ^FW's own when it relies on ^CF, which carries none.
    element.orientation = (font['orientation'] if field['font']
                           else field['default_orientation'])
    element.height = font['height']
    element.printer_font_name = font['name']
    element.block = field['block']

    # A .zpl records only the printer font name, but that name is derived from
    # the font file, so the installed .ttf can usually be found again - without
    # it the label reopens in a substitute face and at the wrong width.
    local = zpl_fonts.file_for_printer_name(font['name']) if font['name'] else None
    if local:
        element.font_path = local
        try:
            element.font_family = zpl_fonts.family_for_file(local)
        except Exception:
            element.font_family = None
        zpl_fonts.register_app_font(local)
        if renderer is not None:
            renderer.register_font(font['name'], local)
    doc.sync_text_width(element)
    return element
