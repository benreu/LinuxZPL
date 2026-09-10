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

from . import fonts as zpl_fonts
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

# Commands that carry no element of their own and need no warning
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


def _decode_gfa_image(x: int, y: int, gf_match, preview_b64, path_hint):
    """An image element from a ^GFA field, best source first.

    The 1-bit data is the only thing a printer needs, but it is also the worst
    thing to edit from - it has already been dithered. So the original file
    wins, then the embedded JPEG, and the ^GF data is the last resort (and the
    only option for ZPL that came from another tool).
    """
    total_b = int(gf_match.group(1))
    bpr = int(gf_match.group(3))
    if bpr <= 0:
        return None
    gf_h = total_b // bpr
    gf_w = bpr * 8

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
    hex_data = gf_match.group(4).strip()
    if total_b > 0 and hex_data:
        try:
            import numpy as np
            from PIL import Image
            raw = bytes.fromhex(hex_data)
            arr = np.frombuffer(raw, dtype=np.uint8).reshape(gf_h, bpr)
            unpacked = np.unpackbits(arr, axis=1)[:, :gf_w]
            # A set bit is black, so the bits invert to greyscale levels.
            pixel_data = ((1 - unpacked) * 255).astype(np.uint8)
            pil_img = Image.fromarray(pixel_data, mode='L').convert('RGB')
            return ImageElement(x, y, gf_w, gf_h, _pil_image=pil_img)
        except Exception:
            pass
    return None


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
    loaded_dpi = None
    pending_no_print = False
    field = None            # commands gathered since the last ^FO

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

        if cmd == '^FO':
            # A field that never saw ^FS still ends here, at the next one
            pending_no_print = _flush(field, doc, renderer, pending_no_print)
            match = re.match(r'\s*(\d+),(\d+)', params)
            field = _new_field(int(match.group(1)), int(match.group(2))) if match else None
            continue

        if cmd == '^FS':
            pending_no_print = _flush(field, doc, renderer, pending_no_print)
            field = None
            continue

        if field is None:
            continue

        if cmd == '^BY':
            match = re.match(r'\s*(\d+)', params)
            if match:
                field['module_width'] = int(match.group(1))
        elif cmd.startswith('^A'):
            _read_font(cmd, params, field)
        elif cmd == '^FB':
            field['block'] = FieldBlock.from_zpl(params)
        elif cmd == '^BC':
            field['barcode'] = _read_barcode(params)
        elif cmd == '^GB':
            field['frame'] = params
        elif cmd == '^GF':
            field['graphic'] = params
        elif cmd == '^FD':
            field['data'] = params

    _flush(field, doc, renderer, pending_no_print)

    doc.selected_element = None
    return doc, loaded_dpi


def _new_field(x: int, y: int) -> dict:
    """The state gathered between a ^FO and the ^FS that ends it."""
    return {'x': x, 'y': y, 'module_width': 2, 'font': None, 'block': None,
            'barcode': None, 'frame': None, 'graphic': None, 'data': None,
            'preview': None, 'path': None}


def _read_font(cmd: str, params: str, field: dict) -> None:
    """^A0 / ^AF / ^A@ - the font, its height and its width.

    The designator is the command's second character, so every built-in font
    is read the same way; ^A@ additionally names a font downloaded to the
    printer, e.g. ^A@N,53,19,E:DEJAVUSA.TTF.
    """
    code = cmd[2]
    match = re.match(r'\s*[A-Z]?,(\d+),(\d+)', params)
    font_height = int(match.group(1)) if match else 36
    font_width = int(match.group(2)) if match else 20
    name = None
    if code == '@':
        named = re.search(r'[^:,]*:([^.,]+)', params)
        if named:
            name = named.group(1).upper()
    field['font'] = {'code': code, 'height': font_height,
                     'width': font_width, 'name': name}


def _read_barcode(params: str) -> dict:
    """^BC<orientation>,<height>,<interpretation line>,<above>,<check>,<mode>.

    Everything after the height is carried through untouched: those flags
    decide whether the digits print under the bars and which Code 128 subsets
    the printer may use, and re-emitting a barcode without them would change
    the label.
    """
    parts = [p.strip() for p in params.split(',')]
    orientation = ''
    if parts and parts[0][:1].isalpha():
        orientation = parts[0][:1].upper()
    try:
        height = int(parts[1]) if len(parts) > 1 and parts[1] else 100
    except ValueError:
        height = 100
    return {'orientation': orientation, 'height': height,
            'options': tuple(p for p in parts[2:])}


def _flush(field, doc, renderer, pending_no_print: bool) -> bool:
    """Turn a gathered field into an element. Returns the no-print flag."""
    if field is None:
        return pending_no_print

    before = len(doc.elements)
    element = _build_element(field, doc, renderer)
    if element is not None:
        doc.elements.append(element)
    if pending_no_print and len(doc.elements) > before:
        for el in doc.elements[before:]:
            el.print_enabled = False
        return False
    return pending_no_print


def _build_element(field, doc, renderer):
    """The element a field describes, or None if it describes none.

    Decided once the whole field has been read rather than at the first
    command that looks decisive, so a ^FB sitting between the font and the
    data no longer loses the element.
    """
    x, y = field['x'], field['y']

    if field['graphic'] is not None:
        gf_match = re.match(r'\s*A,(\d+),(\d+),(\d+),(.*)', field['graphic'], re.S)
        if gf_match:
            return _decode_gfa_image(x, y, gf_match, field['preview'], field['path'])
        return None

    if field['frame'] is not None:
        match = re.match(r'\s*(\d+),(\d+)(?:,(\d+))?', field['frame'])
        if match:
            thickness = int(match.group(3)) if match.group(3) else 1
            return FrameElement(x, y, int(match.group(1)), int(match.group(2)),
                                thickness)
        return None

    if field['barcode'] is not None:
        bc = field['barcode']
        # A ^A before the ^BC selects the interpretation line's font, not a
        # text element's, so it belongs to the barcode.
        font = field['font']
        return BarcodeElement(x, y, height=bc['height'],
                              barcode_value=field['data'] or "123456789",
                              module_width=field['module_width'],
                              orientation=bc['orientation'],
                              options=bc['options'],
                              font=(font['code'], font['height'], font['width'])
                              if font else None)

    if field['font'] is not None and field['data'] is not None:
        return _build_text(x, y, field, doc, renderer)

    return None


def _build_text(x, y, field, doc, renderer):
    """A text element, with its font found again on this machine if it can be."""
    font = field['font']
    element = TextElement(x, y, field['data'], font['height'], font['width'],
                          font_code=font['code'])
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
