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
from .model import (BarcodeElement, Document, FrameElement, ImageElement,
                    TextElement)

NOPRINT_KEY = '^FXDESIGNER_NOPRINT:'
NOPRINT_MARKER = '^FXDESIGNER_NOPRINT'
DPI_KEY = '^FXDESIGNER_DPI:'
PREVIEW_KEY = '^FXDESIGNER_PREVIEW:'
PATH_KEY = '^FXDESIGNER_PATH:'


def parse_label_size(zpl_content: str) -> Tuple[Optional[int], Optional[int]]:
    """The ^PW / ^LL label size recorded in the file, if it has one."""
    pw = re.search(r'\^PW(\d+)', zpl_content)
    ll = re.search(r'\^LL(\d+)', zpl_content)
    return (int(pw.group(1)) if pw else None,
            int(ll.group(1)) if ll else None)


def _expand_hidden(zpl_content: str) -> list:
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
    return lines


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

    lines = _expand_hidden(zpl_content)
    loaded_dpi = None
    pending_no_print = False
    i = 0

    while i < len(lines):
        line = lines[i].strip()

        # Skip comments and empty lines
        if line.startswith(';') or not line:
            i += 1
            continue

        if line.startswith(DPI_KEY):
            try:
                loaded_dpi = int(line[len(DPI_KEY):])
            except ValueError:
                pass
            i += 1
            continue

        if line == NOPRINT_MARKER:
            pending_no_print = True
            i += 1
            continue

        if line.startswith('^FO'):
            match = re.match(r'\^FO(\d+),(\d+)', line)
            if match:
                x, y = int(match.group(1)), int(match.group(2))
                before = len(doc.elements)

                # Look ahead for the element type
                i += 1
                module_width = 2    # ^BY, if the field carries one
                preview_b64 = None  # JPEG preview embedded by designer on save
                path_hint = None    # original file path embedded by designer

                while i < len(lines):
                    next_line = lines[i].strip()

                    # Designer metadata - collect and keep looking
                    if next_line.startswith(PREVIEW_KEY):
                        preview_b64 = next_line[len(PREVIEW_KEY):]
                        i += 1
                        continue
                    if next_line.startswith(PATH_KEY):
                        path_hint = next_line[len(PATH_KEY):]
                        i += 1
                        continue

                    if next_line.startswith('^BY'):
                        by_match = re.match(r'\^BY(\d+)', next_line)
                        if by_match:
                            module_width = int(by_match.group(1))
                        i += 1
                        continue

                    if next_line.startswith('^AF') or next_line.startswith('^A@'):
                        # Text. ^A@ names a font downloaded to the printer,
                        # e.g. ^A@N,53,19,E:DEJAVUSA.TTF
                        font_name = None
                        if next_line.startswith('^A@'):
                            match = re.match(
                                r'\^A@[A-Z]?,(\d+),(\d+),[^:]*:([^.,]+)', next_line)
                            if match:
                                font_name = match.group(3).upper()
                        else:
                            match = re.match(r'\^AF[A-Z]?,(\d+),(\d+)', next_line)
                        font_h, font_w = 36, 20
                        if match:
                            font_h = int(match.group(1))
                            font_w = int(match.group(2))

                        i += 1
                        if i < len(lines) and lines[i].strip().startswith('^FD'):
                            text = lines[i].strip()[3:-3]  # strip ^FD and ^FS
                            el = TextElement(x, y, text, font_h, font_w)
                            el.height = font_h
                            el.printer_font_name = font_name
                            # A .zpl records only the printer name, but that
                            # name is derived from the font file, so the
                            # installed .ttf can usually be found again -
                            # without it the label would reopen in a substitute
                            # face and at the wrong width.
                            local = (zpl_fonts.file_for_printer_name(font_name)
                                     if font_name else None)
                            if local:
                                el.font_path = local
                                try:
                                    el.font_family = zpl_fonts.family_for_file(local)
                                except Exception:
                                    el.font_family = None
                                zpl_fonts.register_app_font(local)
                                if renderer is not None:
                                    renderer.register_font(font_name, local)
                            doc.elements.append(el)
                            doc.sync_text_width(el)
                        break

                    if next_line.startswith('^GB'):
                        match = re.match(r'\^GB(\d+),(\d+)(?:,(\d+))?', next_line)
                        if match:
                            w, h = int(match.group(1)), int(match.group(2))
                            t = int(match.group(3)) if match.group(3) else 1
                            doc.elements.append(FrameElement(x, y, w, h, t))
                        break

                    if next_line.startswith('^BC'):
                        match = re.match(r'\^BC[A-Z]?,(\d+)?', next_line)
                        h = int(match.group(1)) if match and match.group(1) else 100
                        barcode_value = "123456789"
                        i += 1
                        if i < len(lines) and lines[i].strip().startswith('^FD'):
                            barcode_value = lines[i].strip()[3:-3]
                        doc.elements.append(
                            BarcodeElement(x, y, height=h, barcode_value=barcode_value,
                                           module_width=module_width))
                        break

                    if next_line.startswith('^GF'):
                        gf_match = re.match(r'\^GFA,(\d+),(\d+),(\d+),(.*)', next_line)
                        if gf_match:
                            img_el = _decode_gfa_image(x, y, gf_match,
                                                       preview_b64, path_hint)
                            if img_el is not None:
                                doc.elements.append(img_el)
                        break

                    if next_line.startswith('^FS'):
                        break

                    i += 1

                if pending_no_print:
                    # Mark whatever this block appended, wherever it was
                    # appended from, rather than touching each branch.
                    for el in doc.elements[before:]:
                        el.print_enabled = False
                    pending_no_print = False

        i += 1

    if doc.elements:
        doc.selected_element = None
    return doc, loaded_dpi
