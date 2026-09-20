"""
Reading the bitmap out of a ^GF field, in every encoding one arrives in.

Uncompressed hex is what this designer writes and almost nothing else does: a
5 KB logo is 100 KB of plain hex, so label software sends :Z64: (base64 over
zlib) or the ASCII run-length form instead. Both decode sites used to be a bare
bytes.fromhex() inside an `except` that returned nothing, so an image from
another tool vanished without a word.

One reading of ^GF, used by the parser and by the preview renderer, so the two
cannot disagree about what a field holds - the same arrangement ^FB and ^GB
already have.
"""

import base64
import zlib
from typing import Optional, Tuple

# ZPL's ASCII run-length counts. G-Y repeat the next hex digit 1 to 19 times,
# g-z repeat it 20 to 400 times in steps of 20, and a lowercase followed by an
# uppercase adds the two - hK is 45.
_COUNTS = {}
for _index, _letter in enumerate('GHIJKLMNOPQRSTUVWXY'):
    _COUNTS[_letter] = _index + 1
for _index, _letter in enumerate('ghijklmnopqrstuvwxyz'):
    _COUNTS[_letter] = (_index + 1) * 20

_HEX = set('0123456789abcdefABCDEF')

# Row-level shorthands, which is why the expansion has to know the row width
_ROW_BLANK = ','        # the rest of this row is white
_ROW_SOLID = '!'        # the rest of this row is black
_ROW_REPEAT = ':'       # this row is the one above again


def decode(params: str) -> Optional[Tuple[bytes, int]]:
    """The bitmap and its bytes-per-row from a whole ^GF parameter string.

    The row count comes from the decoded length rather than from the header.
    ^GFa,b,c,d carries two counts, and for compressed data generators disagree
    about which is the transmitted length and which the uncompressed total;
    len(raw) // bytes_per_row cannot be wrong and needs no adjudication.
    """
    split = header(params)
    if split is None:
        return None
    fmt, _transmitted, _total, bytes_per_row, data = split
    if fmt != 'A' or bytes_per_row <= 0:
        return None

    raw = decode_data(data, bytes_per_row)
    if not raw:
        return None
    rows = len(raw) // bytes_per_row
    if rows <= 0:
        return None
    return raw[:rows * bytes_per_row], bytes_per_row


def decode_data(data: str, bytes_per_row: int) -> Optional[bytes]:
    """Just the data field of a ^GFA, in whichever encoding it uses."""
    text = (data or '').strip()
    if not text:
        return None
    if text.startswith(':'):
        return _decode_base64(text)
    return _decode_hex(text, bytes_per_row)


def unsupported(params: str) -> Optional[str]:
    """What to call this ^GF in a warning, or None when it can be read.

    A field that cannot be turned into an image has to be reported: failing
    into an `except` and returning nothing is how a label quietly lost one.
    """
    split = header(params)
    if split is None:
        return '^GF'
    fmt, _transmitted, _total, bytes_per_row, data = split
    if fmt in ('B', 'C'):
        # Binary, and the file was read as text, so those bytes did not
        # survive the trip and no amount of decoding here will bring them back.
        return f'^GF{fmt}'
    if fmt != 'A':
        return '^GF'
    if bytes_per_row <= 0 or decode_data(data, bytes_per_row) is None:
        return '^GFA'
    return None


def header(params: str):
    """^GFa,b,c,d,<data> as its five parts, or None if it is not one.

    Public because the parser needs the declared row width to place an image
    whose data it could not decode - the embedded JPEG preview and the original
    file are both better sources, and neither depends on the bitmap.
    """
    parts = (params or '').split(',', 4)
    if len(parts) < 5:
        return None

    def number(text):
        try:
            return int(text.strip())
        except ValueError:
            return 0

    fmt = (parts[0].strip() or 'A').upper()
    return fmt, number(parts[1]), number(parts[2]), number(parts[3]), parts[4]


def _decode_base64(text: str) -> Optional[bytes]:
    """:Z64:<base64>:<crc> or :B64:<base64>:<crc>.

    The trailing CRC is read past rather than checked. Zebra does not publish
    which CRC-16 variant it is, and rejecting a label because this module
    guessed the wrong initial value would be a worse failure than the one being
    fixed. Z64 carries zlib's own checksum, which is stronger anyway, and a
    corrupt B64 payload fails the row-length check instead.
    """
    parts = text.split(':')     # base64 holds no colon, so this is unambiguous
    if len(parts) < 3:
        return None
    scheme = parts[1].strip().upper()
    if scheme not in ('Z64', 'B64'):
        return None

    try:
        raw = base64.b64decode(parts[2].strip(), validate=True)
    except Exception:
        return None
    if scheme == 'Z64':
        try:
            raw = zlib.decompress(raw)
        except zlib.error:
            return None
    return raw or None


def encode(image, bytes_per_row: int) -> str:
    """Pack a 1-bit image into ZPL's own bitmap format: hex-encoded,
    MSB-first, 8 pixels per byte, a short final row padded white.

    The inverse of decode_data() - together one reading and one writing of
    the same bitmap, so ^GF and the printer's own stored objects (~DG) can
    never disagree about what a picture became. `image` must already be mode
    '1' (dithered - Floyd-Steinberg via .convert('1') is the caller's job,
    not this function's); `bytes_per_row` is the caller's, too, since it is
    sometimes an element's box width rather than the image's own, and a
    short row is padded to it rather than recomputed from image.size.

    A SET bit is black - the inverse of the usual 1-bit convention, which is
    why this compares against 0 rather than casting the array directly.
    """
    import numpy as np
    width, height = image.size
    arr = np.array(image, dtype=np.uint8)
    padded_w = bytes_per_row * 8
    if padded_w > width:
        # Padding is white, i.e. an unset bit, so it prints nothing.
        pad = np.full((height, padded_w - width), 255, dtype=np.uint8)
        arr = np.concatenate([arr, pad], axis=1)
    arr = arr.reshape(height, bytes_per_row, 8)
    bits = (arr == 0).astype(np.uint8)
    weights = np.array([128, 64, 32, 16, 8, 4, 2, 1], dtype=np.uint8)
    packed = (bits * weights).sum(axis=2).astype(np.uint8)
    return packed.tobytes().hex().upper()


def to_image(raw: bytes, bytes_per_row: int):
    """Raw ZPL bitmap bytes - what decode_data() returns - back to a real
    image. The inverse of encode(), minus the dithering it has no way to
    undo: a set bit is still black, so the bits invert to greyscale before
    Pillow sees them.

    The same unpacking parser.py's own last-resort ^GF reading does inline;
    lives here, alongside encode()/decode_data(), so retrieve_printer_graphic
    can turn a printer's own bitmap reply into pixels without a second,
    untested copy of the bit order.
    """
    import numpy as np
    from PIL import Image as PILImage
    rows = len(raw) // bytes_per_row
    arr = np.frombuffer(raw, dtype=np.uint8).reshape(rows, bytes_per_row)
    unpacked = np.unpackbits(arr, axis=1)
    pixel_data = ((1 - unpacked) * 255).astype(np.uint8)
    return PILImage.fromarray(pixel_data, mode='L').convert('RGB')


def _decode_hex(text: str, bytes_per_row: int) -> Optional[bytes]:
    """Hex digits, with ZPL's run-length shorthands expanded.

    Plain uncompressed hex passes through unchanged: it is simply a stream that
    happens to use none of the shorthands.
    """
    row_digits = max(2, bytes_per_row * 2)
    rows = []
    row = []
    count = 0

    def fill(digit):
        row.extend(digit * max(0, row_digits - len(row)))

    for char in text:
        if char.isspace():
            continue

        if char in _COUNTS:
            count += _COUNTS[char]
            continue

        if char == _ROW_BLANK or char == _ROW_SOLID:
            fill('0' if char == _ROW_BLANK else 'F')
            rows.append(''.join(row))
            row = []
            count = 0
            continue

        if char == _ROW_REPEAT:
            if not rows:
                return None
            rows.append(rows[-1])
            row = []
            count = 0
            continue

        if char not in _HEX:
            return None

        row.extend(char.upper() * max(1, count))
        count = 0
        # A repeat can run past the end of a row, so complete rows come off as
        # soon as they are full rather than at the next shorthand.
        while len(row) >= row_digits:
            rows.append(''.join(row[:row_digits]))
            row = row[row_digits:]

    if row:
        rows.append(''.join(row).ljust(row_digits, '0'))
    if not rows:
        return None

    try:
        return bytes.fromhex(''.join(rows))
    except ValueError:
        return None
