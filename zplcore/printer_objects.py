"""
Printer -> Objects... - a directory of everything a real printer is holding,
not just the fonts and graphics this app already has dedicated managers for
(zplcore/fonts.py, zplcore/graphic_store.py).

A printer's R:/E:/B:/A:/Z: memory holds whatever landed there - a font, a
graphic, a saved format, firmware's own housekeeping, anything another tool
put there - and a user checking what is actually using space, or cleaning
out an object neither of the type-specific managers recognizes, needs to see
all of it. So this module lists, deletes, retrieves and stores: ^ID (Object
Delete), the file.type Set/Get/Do command and CISDFCRC16 all take or report
any d:o.x regardless of what kind of object it names, unlike ^HG/~DY/~DG,
which are each locked to one format (a graphic's own bitmap shape, a
TrueType font) - only Fonts and Graphics need those, and keep them.

Object names here are handled case-as-given, not forced to upper case the
way fonts.py and graphic_store.py force theirs: those two only ever create
upper-case 8.3 objects, so forcing it is harmless there, but a name reaching
this module may come from CISDFCRC16 (whose own manual examples - privkey.nrd,
feedback.get - are lower case) or already exist on the printer from some
other tool, and file.type's retrieval is documented as case sensitive - a
name normalised the wrong way silently stops matching the real object.
"""

import re
from typing import List, Optional

from . import printer_io
from .graphic_store import split_device_spec

# R:/E:/B:/A: are the devices fonts.py and graphic_store.py already cover;
# Z: is added here because ^HW documents it as a valid listing device and it
# is where a printer's factory-default WML menu (and other read-only
# content, e.g. an RFID recipe file) lives - Graphics deliberately stays off
# Z: (see graphic_store.DEVICES, §18), but Objects' whole purpose is
# completeness, so it gets its own tuple rather than reusing that one.
DEVICES = ('R', 'E', 'B', 'A', 'Z')

_OBJECT_SPEC = re.compile(r'([A-Za-z0-9_\-]{1,8})\.([A-Za-z0-9_\-]{1,8})',
                          re.IGNORECASE)


def query_printer_objects(address: str, port: int,
                          timeout: float = 5) -> Optional[List[str]]:
    """Every object stored on the printer, across R:/E:/B:/A:/Z:, as
    'd:NAME.EXT', or None if it could not be asked.

    One unscoped ^HW per device - *.* rather than the *.TTF/*.GRF the Fonts
    and Graphics managers scope their own requests to - since the point here
    is everything, including extensions neither of those recognizes.
    Reachability is judged by the first device alone, the same rule
    query_printer_fonts and query_printer_graphics already use: no reply, or
    the connection itself failing, means unreachable and the other devices
    are not even tried. A later device failing the same way is not proof the
    printer went away - just that this device has nothing, or does not exist
    on this model.
    """
    specs: List[str] = []
    for index, device in enumerate(DEVICES):
        payload = f'^XA^HW{device}:*.*^XZ'.encode('ascii')
        try:
            reply = printer_io.send(address, port, payload, timeout,
                                    read_reply=True)
        except OSError:
            if index == 0:
                return None
            continue
        if not reply:
            if index == 0:
                return None
            continue
        text = reply.decode('ascii', 'replace')
        for m in _OBJECT_SPEC.finditer(text):
            # Case preserved, not forced to upper - see the module docstring.
            specs.append(f"{device}:{m.group(1)}.{m.group(2)}")
    return sorted(set(specs))


def delete_printer_object(address: str, port: int, raw_spec: str,
                          timeout: float = 10) -> None:
    """Delete any object from the printer via ^ID - the same shape
    fonts.delete_printer_font and graphic_store.delete_printer_graphic
    already send under their own names, and extension-agnostic, so no
    branching by object type is needed here. Raises on failure.

    ^ID's own parameter table only lists R:, E:, B: and A: - a Z: target is
    silently ignored rather than deleted, since that device is read-only
    factory content (the RFID recipe file section of the ZPL manual
    describes a same-named E: file as shadowing the one on Z:, never
    replacing it). Callers should not offer Delete for a Z: spec at all;
    this function does not special-case it, since sending the command and
    having the printer ignore it is the documented behaviour, not an error
    this function could detect or report.
    """
    device, name, ext = split_device_spec(raw_spec)
    payload = f"^XA^ID{device}:{name}.{ext}^FS^XZ".encode('ascii')
    printer_io.send(address, port, payload, timeout)


def download_printer_object(address: str, port: int, raw_spec: str,
                            timeout: float = 30) -> bytes:
    """Fetch `raw_spec`'s raw bytes from the printer, verbatim.

    Sent via the file.type Set/Get/Do command - `! U1 setvar "file.type"
    "d:NAME.EXT"` - documented as displaying a file's contents, unchanged,
    on the same connection, regardless of what kind of object it is. This
    is the one command in the ZPL manual that is genuinely generic: ^HG
    (graphic_store.retrieve_printer_graphic) only makes sense of a graphic's
    own bitmap shape, and there is no equivalent host command for anything
    else - a stored .ZPL format, a .WML menu, anything this app has no
    reader for. Set/Get/Do commands are their own small command language,
    sent standalone rather than wrapped in ^XA/^XZ, the same way
    delete_printer_object's sibling commands (file.dir, file.delete) are
    shown in the manual. Raises OSError on an empty reply, the same shape
    graphic_store.retrieve_printer_graphic already uses.
    """
    device, name, ext = split_device_spec(raw_spec)
    payload = (f'! U1 setvar "file.type" "{device}:{name}.{ext}"'
              '\r\n').encode('ascii')
    reply = printer_io.send(address, port, payload, timeout, read_reply=True)
    if not reply:
        raise OSError(f"No reply retrieving {raw_spec}")
    return reply


def build_object_upload(name: str, ext: str, data: bytes) -> bytes:
    """The CISDFCRC16 payload that stores `data` on the printer's E: drive
    as name.ext - the one command able to write an object of any kind,
    since ~DY and ~DG are each locked to their own format. Files always
    land on E: - CISDFCRC16's own parameter table gives no device choice,
    unlike ^HW/^ID/file.type.

    <crc> and <checksum> are both sent as "0000", which the command's own
    parameter table documents as skipping that field's validation -
    deliberately, not as a shortcut: CRC-16/CCITT itself is unambiguous,
    but the manual's own worked example of the checksum algorithm (an
    8-bit two's-complement byte sum) does not obviously match the 4 hex
    digits its worked example for an actual file shows, and a checksum
    computed the wrong way would make every upload fail outright rather
    than merely go unverified.

    Each line is CR/LF-terminated per the manual; sent standalone, not
    wrapped in ^XA/^XZ, the same as file.type and file.delete.
    """
    header = (f'! CISDFCRC16\r\n0000\r\n{name}.{ext}\r\n'
             f'{len(data):08X}\r\n0000\r\n')
    return header.encode('ascii') + data


def upload_printer_object(address: str, port: int, name: str, ext: str,
                          data: bytes, timeout: float = 30) -> None:
    """Store `data` on the printer as E:name.ext. Raises on failure."""
    printer_io.send(address, port, build_object_upload(name, ext, data), timeout)
