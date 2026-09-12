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
from . import graphics
from . import transforms as zpl_transforms
from .model import (BarcodeElement, Document, FieldBlock, FrameElement,
                    ImageElement, TextElement)

NOPRINT_KEY = '^FXDESIGNER_NOPRINT:'
NOPRINT_MARKER = '^FXDESIGNER_NOPRINT'
DPI_KEY = '^FXDESIGNER_DPI:'
PREVIEW_KEY = '^FXDESIGNER_PREVIEW:'
PATH_KEY = '^FXDESIGNER_PATH:'

# The same keys as the tokeniser sees them: ^FX is the command, the rest is
# its parameters.
NOPRINT_PARAM = NOPRINT_MARKER[len('^FX'):]
DPI_PARAM = DPI_KEY[len('^FX'):]
PREVIEW_PARAM = PREVIEW_KEY[len('^FX'):]
PATH_PARAM = PATH_KEY[len('^FX'):]


def parse_label_size(zpl_content: str) -> Tuple[Optional[int], Optional[int]]:
    """The ^PW / ^LL label size recorded in the file, if it has one."""
    pw = re.search(r'\^PW(\d+)', zpl_content)
    ll = re.search(r'\^LL(\d+)', zpl_content)
    return (int(pw.group(1)) if pw else None,
            int(ll.group(1)) if ll else None)


COMMAND = re.compile(r'([\^~])([A-Za-z0-9@]{2})([^\^~]*)', re.S)

# ZPL's own factory default font, used by any field that carries neither an ^A
# of its own nor a ^CF before it.
DEFAULT_FONT = {'code': 'A', 'height': 9, 'width': 5, 'name': None,
                'orientation': 'N'}

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
    doc = Document()
    width, height = parse_label_size(zpl_content)
    if width:
        doc.label_width = width
    if height:
        doc.label_height = height

    tokens = tokenise(_expand_hidden(zpl_content))
    doc.fields = read_field_table(tokens)
    loaded_dpi = None
    pending_no_print = False
    field = None            # commands gathered since the last ^FO
    # ^CF sets the font for every field that does not name one of its own, so
    # it has to be carried between fields rather than gathered into one. ^BY is
    # the same kind of command for barcodes.
    default_font = dict(DEFAULT_FONT)
    default_barcode = dict(DEFAULT_BARCODE)
    # The ^LH/^LS offset in force. Elements hold the absolute dot position, so
    # the canvas, dragging and clamping never have to know these exist.
    origin = (0, 0)
    home = (0, 0)           # the ^LH in force, which is not always the first
    seen_home = False

    for cmd, params in tokens:
        if cmd == '^FX':
            key = params.strip()
            if key.startswith(DPI_PARAM):
                try:
                    loaded_dpi = int(key[len(DPI_PARAM):])
                except ValueError:
                    pass
            elif key == NOPRINT_PARAM:
                pending_no_print = True
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

        if cmd == '^CF':
            default_font = _read_default_font(params, default_font)
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
            pending_no_print = _flush(field, doc, renderer, pending_no_print)
            match = re.match(r'\s*(-?\d+),(-?\d+)', params)
            field = _new_field(int(match.group(1)) + origin[0],
                               int(match.group(2)) + origin[1],
                               default_font, default_barcode) if match else None
            # ^FT places a field exactly as ^FO does, but names its baseline
            # rather than its top. Opening no field on it did not degrade such
            # a label - it dropped every field in it, so a file from another
            # tool opened completely empty.
            if field is not None:
                field['typeset'] = (cmd == '^FT')
            continue

        if cmd == '^FS':
            pending_no_print = _flush(field, doc, renderer, pending_no_print)
            field = None
            continue

        if field is None:
            continue

        if cmd.startswith('^A'):
            field['font'] = read_font(cmd[2], params, field['default_font'])
        elif cmd == '^FB':
            field['block'] = FieldBlock.from_zpl(params)
        elif cmd == '^FR':
            field['reverse'] = True
        elif cmd == '^BC':
            field['barcode'] = _read_barcode(params, field['bar_height'])
        elif cmd.startswith('^B') or cmd == '^GS':
            # Code 39, QR, Data Matrix, EAN - a symbology this designer cannot
            # draw. Recorded so the field is dropped, because falling through
            # to the text branch did not merely lose the barcode: it put a text
            # element holding the barcode's data on the label in its place.
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
        elif cmd == '^FN':
            read = zpl_fields.read(params)
            if read is not None:
                field['field_number'], field['field_prompt'] = read
        elif cmd == '^FD':
            field['data'] = params
        elif cmd == '^FV':
            # ^FV is ^FD for a field the printer clears after printing. Reading
            # it as data is what stops such a field vanishing outright.
            field['data'] = params

    _flush(field, doc, renderer, pending_no_print)

    doc.selected_element = None
    return doc, loaded_dpi


def _new_field(x: int, y: int, default_font=None, default_barcode=None) -> dict:
    """The state gathered between a ^FO and the ^FS that ends it.

    `font` stays None until an ^A names one, because "this field named a font"
    and "this field inherits the default" are different things: a barcode with
    no ^A of its own must go on writing none, while text with no ^A prints in
    whatever ^CF last set.

    The three ^BY values are not like that: ZPL has a default for each, so a
    field always has one, whether from the last ^BY or from the power-up value.
    """
    inherited = dict(default_barcode or DEFAULT_BARCODE)
    return {'x': x, 'y': y, 'block': None, 'font': None,
            'field_number': None, 'field_prompt': None,
            'module_width': inherited['module_width'],
            'ratio': inherited['ratio'],
            'bar_height': inherited['height'],
            'default_font': dict(default_font or DEFAULT_FONT),
            'barcode': None, 'frame': None, 'graphic': None, 'data': None,
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
        font['orientation'] = 'N'
    for index, key in ((1, 'height'), (2, 'width')):
        if len(parts) > index and parts[index]:
            try:
                font[key] = int(parts[index])
            except ValueError:
                pass
    return font


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


def read_font(code: str, params: str, default_font=None) -> dict:
    """^A<code><orientation>,<h>,<w> - the font a field names for itself.

    The designator is the command's second character, so every built-in font
    is read the same way; ^A@ additionally names a font downloaded to the
    printer, e.g. ^A@N,53,19,E:DEJAVUSA.TTF.

    Every parameter is optional, and an omitted one keeps the ^CF default for
    that position - the same rule _read_default_font applies to ^CF itself.
    Demanding all three is what made ^A0N,40 come back as ^A0N,36,20, losing
    the height it did give while the preview, which demanded nothing, drew it
    at 40.

    The orientation is the first parameter, and dropping it is why text was
    the one element that could not be turned: it loaded flat and saved flat.
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
            'orientation': letter.group(1).upper() if letter else 'N'}


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


def _read_barcode(params: str, default_height=None) -> dict:
    """^BC<orientation>,<height>,<interpretation line>,<above>,<check>,<mode>.

    Everything after the height is carried through untouched: those flags
    decide whether the digits print under the bars and which Code 128 subsets
    the printer may use, and re-emitting a barcode without them would change
    the label.

    An omitted height is ^BY's, which is what its third parameter is for.
    Hard-coding 100 here turned ^BY3,3.0,150^BCN into a barcode a third shorter
    than the file asked for.
    """
    parts = [p.strip() for p in params.split(',')]
    orientation = ''
    if parts and parts[0][:1].isalpha():
        orientation = parts[0][:1].upper()
    fallback = DESIGNER_BAR_HEIGHT if default_height is None else default_height
    try:
        height = int(parts[1]) if len(parts) > 1 and parts[1] else fallback
    except ValueError:
        height = fallback
    return {'orientation': orientation, 'height': height,
            'options': tuple(p for p in parts[2:])}


def _flush(field, doc, renderer, pending_no_print: bool) -> bool:
    """Turn a gathered field into an element. Returns the no-print flag."""
    if field is None:
        return pending_no_print

    before = len(doc.elements)
    element = _build_element(field, doc, renderer)
    if element is not None:
        if field['typeset']:
            _apply_typeset(element, doc)
        element.reverse_print = field['reverse']
        doc.elements.append(element)
    if pending_no_print and len(doc.elements) > before:
        for el in doc.elements[before:]:
            el.print_enabled = False
        return False
    return pending_no_print


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

    if field['graphic'] is not None:
        # Every format reaches the decoder, so one that cannot be read fails
        # where it can be reported rather than at a regex that matched only
        # the spelling this designer writes.
        return _decode_gfa_image(x, y, field['graphic'],
                                 field['preview'], field['path'])

    if field['frame'] is not None:
        return FrameElement(x, y, *_read_frame(field['frame']))

    if field['barcode'] is not None:
        bc = field['barcode']
        # A ^A before the ^BC selects the interpretation line's font, not a
        # text element's, so it belongs to the barcode.
        font = field['font']
        # A numbered field's data comes from the printer, so it has none of
        # its own and must not be given any: `or "123456789"` is a default for a
        # barcode the user has just created, and applying it here invented a
        # value that appeared nowhere in the file and then wrote it to disk.
        value = field['data']
        if value is None:
            value = '' if field['field_number'] is not None else "123456789"
        return BarcodeElement(x, y, height=bc['height'],
                              barcode_value=value,
                              module_width=field['module_width'],
                              ratio=field['ratio'],
                              orientation=bc['orientation'],
                              options=bc['options'],
                              field_number=field['field_number'],
                              field_prompt=field['field_prompt'],
                              font=(font['code'], font['height'], font['width'])
                              if font else None)

    if field['symbology'] is not None:
        # Already named in the load warning. Dropping it here is what stops a
        # Code 39 sixty dots tall from arriving as nine-dot text.
        return None

    # A ^FN field carries no ^FD of its own - that is what ^FN is for - so
    # requiring data discarded every text field in a stored format.
    if field['data'] is not None or field['field_number'] is not None:
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
                          field_prompt=field['field_prompt'])
    element.orientation = font.get('orientation', 'N')
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
