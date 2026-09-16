"""
The in-session store `^IS` writes to and `^XG`/`^IM`/`^IL` read from, and -
in the second half of this file - the real printer I/O behind Printer ->
Graphics... (view/store/retrieve/delete).

A real printer holds these objects in its own memory (R:, E:, B:, A:), so a
label can save one and a later label - in a different file entirely - can
recall it. The dict below is that memory, for as long as the app keeps
running: a plain module-level dict, not persisted to disk, not scoped to a
document.

Keyed on name + extension only. A real printer also distinguishes the device
prefix, but this app has no per-device storage areas to run out of or manage,
so modelling that distinction would add bookkeeping without adding anything a
user could observe - see FUNCTIONAL_SPEC.md section 18. (The printer I/O
below talks to a real printer's real, per-device memory directly, and does
distinguish device there - see query_printer_graphics.)
"""

import io
import re
from typing import List, Optional

from PIL import Image as PILImage

from . import graphics
from . import printer_io

_STORE: dict = {}

_SPEC = re.compile(
    r'\s*(?:[A-Za-z]:)?\s*([A-Za-z0-9]{1,8})?\s*(?:\.([A-Za-z0-9]{1,8}))?\s*$')


def key(raw_spec: str) -> str:
    """The canonical NAME.EXT this spec is stored/recalled under.

    Best-effort: a spec that does not look like `d:o.x` at all still becomes a
    key, rather than raising, matching how the rest of this codebase reads
    ZPL - tolerantly, so a stray or unusual spec is never the reason a file
    fails to open.
    """
    raw = (raw_spec or '').strip()
    match = _SPEC.match(raw)
    if not match or (not match.group(1) and not match.group(2)):
        return raw.upper() or 'UNKNOWN.GRF'
    name = (match.group(1) or 'UNKNOWN').upper()
    ext = (match.group(2) or 'GRF').upper()
    return f"{name}.{ext}"


def store(raw_spec: str, image: PILImage.Image) -> None:
    _STORE[key(raw_spec)] = image


def recall(raw_spec: str) -> Optional[PILImage.Image]:
    return _STORE.get(key(raw_spec))


def items():
    """Every stored (name.ext, image) pair, for a manager UI to list."""
    return sorted(_STORE.items())


def delete(raw_spec: str) -> bool:
    """Forget one stored object. True if it was there to forget.

    The UI-level counterpart of `^ID` (Object Delete), which this app does
    not parse from ZPL - see FUNCTIONAL_SPEC.md section 18. A later ^XG/^IM/
    ^IL naming this spec then resolves to nothing again, the same as before
    anything was ever stored under it.
    """
    return _STORE.pop(key(raw_spec), None) is not None


def clear() -> None:
    """Forget every stored object. Tests use this so one case's ^IS cannot
    resolve another case's ^XG/^IM/^IL."""
    _STORE.clear()


def split_device_spec(spec: str):
    """A `d:o.x` spec, taken apart for an editor's separate fields.

    Lives here rather than in zplcore/model.py - where it used to live -
    because the printer I/O below needs it too, and model.py already
    imports this module, so the reverse import would be circular.
    """
    device, rest = 'R', (spec or '')
    if len(rest) > 1 and rest[1] == ':':
        device, rest = rest[0].upper(), rest[2:]
    name, _, ext = rest.partition('.')
    return device, name or 'UNKNOWN', ext or 'GRF'


# --- printer I/O --------------------------------------------------------
# Real printer memory, not this module's own dict above - reached the same
# way zplcore/fonts.py already reaches a printer's fonts: raw ZPL/control
# commands over the print socket (zplcore/printer_io.py). Store and Retrieve
# also mirror their result into the in-session store above, purely so an
# already-placed ^XG/^IM/^IL updates on screen without a second round trip -
# the same way upload_font's caller also registers the font locally. Delete
# mirrors a removal the same way. None of the three functions below touch
# the dict themselves; that is the caller's job, same division as fonts.py.

DEVICES = ('R', 'E', 'B', 'A')
GRAPHIC_EXTENSION = 'GRF'

_OBJECT_SPEC = re.compile(r'([A-Za-z0-9_\-]{1,8})\.([A-Za-z0-9_\-]{1,8})',
                          re.IGNORECASE)


def query_printer_graphics(address: str, port: int,
                           timeout: float = 5) -> Optional[List[str]]:
    """Every `d:o.GRF` object stored on the printer, across R:/E:/B:/A:, or
    None if it could not be asked.

    One ^HW per device - graphics, unlike fonts (always E:), can live in any
    of the four - each scoped to *.GRF, the canonical ZPL graphic extension
    and the one Store here writes, the same way query_printer_fonts is
    scoped to E:*.TTF. An unscoped *.* was tried first and rejected: a
    printer's own memory holds plenty that is not a graphic at all - fonts,
    firmware/config objects, whatever else came from the factory or another
    tool - and a "Printer Graphics" dialog listing all of it read as broken,
    not merely broad. Reachability is judged by the first device alone, the
    same rule query_printer_fonts uses for its one and only query: no reply,
    or the connection itself failing, means unreachable and the other three
    devices are not even tried. A later device failing the same way is not
    proof the printer went away - just that this device has nothing, or does
    not exist on this model - so it is skipped rather than aborting a result
    the first device already established.
    """
    specs: List[str] = []
    for index, device in enumerate(DEVICES):
        payload = f'^XA^HW{device}:*.{GRAPHIC_EXTENSION}^XZ'.encode('ascii')
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
            # Filtered again here, not just in the request: a printer model
            # that ignores the *.GRF pattern and answers with its whole
            # directory anyway must not put fonts and everything else back
            # in front of the user.
            if m.group(2).upper() != GRAPHIC_EXTENSION:
                continue
            specs.append(f"{device}:{m.group(1).upper()}.{m.group(2).upper()}")
    return sorted(set(specs))


def build_graphic_upload(raw_spec: str, image: PILImage.Image) -> bytes:
    """The ~DG payload that stores `image` on the printer as d:o.x.

    The same bitmap format ^GF fields already carry - 1-bit, Floyd-Steinberg
    dithered, hex-encoded - via graphics.encode(), so an embedded field and
    an uploaded object built from the same source pixels can never disagree
    about what a picture became.
    """
    device, name, ext = split_device_spec(raw_spec)
    dithered = image.convert('1')
    width, height = dithered.size
    bytes_per_row = (width + 7) // 8
    total_bytes = bytes_per_row * height
    data = graphics.encode(dithered, bytes_per_row)
    header = (f"~DG{device}:{name.upper()}.{ext.upper()},"
             f"{total_bytes},{bytes_per_row},")
    return header.encode('ascii') + data.encode('ascii')


def upload_graphic(address: str, port: int, raw_spec: str,
                   image: PILImage.Image, timeout: float = 30) -> None:
    """Store `image` on the printer as `raw_spec`. Raises on failure."""
    printer_io.send(address, port, build_graphic_upload(raw_spec, image), timeout)


_DG_ECHO_PREFIX = b'~DG'


def parse_hg_reply(reply: bytes) -> Optional[PILImage.Image]:
    """A `^HG` reply's pixels, if this is the shape real hardware sends.

    Confirmed against real hardware: `^HG` does not answer with a
    self-contained image file (no PCX, despite older docs and this
    module's own first attempt assuming exactly that) - it answers with the
    same shape Store's own `~DG` upload writes, just missing the device and
    extension a `~DG` carries: `~DG<name>,<total bytes>,<bytes per row>,`
    then a newline, then the bitmap itself ASCII-hex encoded. That is
    exactly what graphics.decode_data() already reads for `^GF` fields
    (including its run-length shorthand, in case a printer applies the same
    compression here too), so the data half is handed straight to it rather
    than to a second, new decoder. The row count comes from the decoded
    length rather than the declared total, the same reasoning decode()
    itself gives - a printer that pads or wraps the byte count differently
    still resolves to whatever whole rows actually decoded.

    None if the reply does not start this way at all, so the caller can
    fall back to something else instead of raising - this is a step in a
    heuristic, not the final word on whether the reply was any good.
    """
    stripped = reply.lstrip()
    if not stripped.startswith(_DG_ECHO_PREFIX):
        return None
    head, _, data = stripped.partition(b'\n')
    fields = head[len(_DG_ECHO_PREFIX):].decode('ascii', 'replace').split(',')
    if len(fields) < 3:
        return None
    try:
        bytes_per_row = int(fields[2].strip())
    except ValueError:
        return None
    if bytes_per_row <= 0:
        return None
    raw = graphics.decode_data(data.decode('ascii', 'replace'), bytes_per_row)
    if not raw:
        return None
    rows = len(raw) // bytes_per_row
    if rows <= 0:
        return None
    return graphics.to_image(raw[:rows * bytes_per_row], bytes_per_row)


def retrieve_printer_graphic(address: str, port: int, raw_spec: str,
                             timeout: float = 30) -> PILImage.Image:
    """Fetch `raw_spec`'s real pixels from the printer via ^HG (Host
    Graphic) - the same request/read-reply shape ^HW already uses.

    parse_hg_reply() is tried first, since that is the shape real hardware
    has actually been observed sending. A reply PIL can open directly (some
    other firmware may genuinely send a self-contained image, as ^HG is
    sometimes documented) is tried second, in case that holds elsewhere.
    Only once neither reading succeeds does this raise, with enough of what
    actually came back - length, and a hex preview of the start - to
    diagnose a third format, rather than only PIL's own "cannot identify
    image file", which says nothing about why.
    """
    device, name, ext = split_device_spec(raw_spec)
    payload = f"^XA^HG{device}:{name.upper()}.{ext.upper()}^XZ".encode('ascii')
    reply = printer_io.send(address, port, payload, timeout, read_reply=True)
    if not reply:
        raise OSError(f"No reply retrieving {raw_spec}")

    image = parse_hg_reply(reply)
    if image is not None:
        return image

    try:
        return PILImage.open(io.BytesIO(reply)).convert('RGB')
    except Exception:
        pass

    preview = reply[:64].hex()
    raise OSError(
        f"{raw_spec}: {len(reply)} bytes back, not decodable as an "
        f"image - first bytes: {preview}") from None


def delete_printer_graphic(address: str, port: int, raw_spec: str,
                           timeout: float = 10) -> None:
    """Delete `raw_spec` from the printer via ^ID - the same shape
    fonts.delete_printer_font already uses. Raises on failure."""
    device, name, ext = split_device_spec(raw_spec)
    payload = f"^XA^ID{device}:{name.upper()}.{ext.upper()}^FS^XZ".encode('ascii')
    printer_io.send(address, port, payload, timeout)
