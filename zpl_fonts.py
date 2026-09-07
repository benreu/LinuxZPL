"""
ZPL Fonts - font discovery, naming, and printer font management.

Keeps the rules for turning a local font file into a printer object name in one
place, and wraps the ZPL commands for uploading, listing and deleting fonts on
the printer.
"""

import ctypes
import re
import socket
import time
from pathlib import Path
from typing import Dict, Iterable, Optional, Set

from PIL import ImageFont

# Fonts live in the printer's E: memory as 8.3 TrueType objects
FONT_DEVICE = 'E:'
FONT_EXTENSION = '.TTF'
MAX_NAME_LEN = 8

# Styles preferred when a family ships several faces
_PREFERRED_STYLES = ('regular', 'book', 'roman', 'normal')
_UNSAFE_CHARS = re.compile(r'[^A-Z0-9_-]')
_OBJECT_NAME = re.compile(r'([A-Za-z0-9_\-]{1,8})\.TTF', re.IGNORECASE)

_families_cache: Optional[Dict[str, str]] = None
_paths_cache: Optional[list] = None


# --- installed system fonts -------------------------------------------------

def list_ttf_families(refresh: bool = False) -> Dict[str, str]:
    """Installed families that have a .ttf file, as family -> path.

    Only TrueType can be sent to the printer (~DY ...,TT,), so .otf, .pfb, .t1
    and .ttc families are deliberately left out - offering them in a chooser
    would produce fonts that cannot be uploaded. Cached, because the chooser's
    filter callback runs once per family.
    """
    global _families_cache
    if _families_cache is not None and not refresh:
        return _families_cache

    import subprocess
    try:
        out = subprocess.run(
            ['fc-list', '-f', '%{family[0]}\t%{style[0]}\t%{file}\n'],
            capture_output=True, text=True, timeout=15, check=False).stdout
    except (OSError, subprocess.SubprocessError):
        _families_cache = {}
        return _families_cache

    best: Dict[str, tuple] = {}
    for line in out.splitlines():
        parts = line.split('\t')
        if len(parts) != 3:
            continue
        family, style, path = (p.strip() for p in parts)
        if not family or not path.lower().endswith('.ttf'):
            continue
        try:
            rank = _PREFERRED_STYLES.index(style.lower())
        except ValueError:
            rank = len(_PREFERRED_STYLES)
        if family not in best or rank < best[family][0]:
            best[family] = (rank, path)

    _families_cache = {family: path for family, (_, path) in best.items()}
    return _families_cache


def file_for_family(family: str) -> Optional[str]:
    """The .ttf file for an installed family, or None if it has none.

    Deliberately an exact lookup rather than fc-match, which always returns a
    best guess and would silently substitute a different font for a name that
    is not installed.
    """
    return list_ttf_families().get(family)


def _all_ttf_paths() -> list:
    """Every installed .ttf file, not just one per family."""
    global _paths_cache
    if _paths_cache is not None:
        return _paths_cache
    import subprocess
    try:
        out = subprocess.run(['fc-list', '-f', '%{file}\n'],
                             capture_output=True, text=True, timeout=15,
                             check=False).stdout
    except (OSError, subprocess.SubprocessError):
        out = ''
    _paths_cache = sorted({line.strip() for line in out.splitlines()
                           if line.strip().lower().endswith('.ttf')})
    return _paths_cache


def file_for_printer_name(name: str) -> Optional[str]:
    """The installed .ttf whose printer object name is `name`, if any.

    A saved .zpl only records the printer name (E:ANI.TTF), but that name is
    derived from the file, so it can be mapped back - which is what lets a
    reopened label render in its real font instead of a substitute.
    """
    if not name:
        return None
    wanted = name.upper()
    matches = [p for p in _all_ttf_paths() if printer_font_name(p) == wanted]
    if not matches:
        return None
    # Truncating to 8 characters is lossy, so several faces can share a name
    # (DejaVuSans and DejaVuSans-Bold both give DEJAVUSA). Prefer a face the
    # family listing already picked as canonical, then the least-suffixed
    # filename, so the base face wins over Bold/Italic variants.
    preferred = set(list_ttf_families().values())
    matches.sort(key=lambda p: (p not in preferred, len(Path(p).stem), p))
    return matches[0]


def family_for_file(font_path: str) -> str:
    """The family name recorded inside a font file."""
    return ImageFont.truetype(font_path, 12).getname()[0]


# --- printer object naming --------------------------------------------------

def printer_font_name(font_path: str, taken: Iterable[str] = ()) -> str:
    """ZPL object name for a font file: at most 8 upper-case safe characters.

    The name is embedded in a ~DY header and in every ^A@ reference, so any
    character outside [A-Z0-9_-] would corrupt those commands - a space in a
    family like "Catrina Demo" being the common case. Truncation also makes
    collisions easy (DejaVuSans and DejaVuSans-Bold both start DEJAVUSA), so a
    name already in `taken` gets a numeric suffix instead of overwriting it.
    """
    stem = Path(font_path).stem.upper()
    stem = stem.encode('ascii', 'ignore').decode('ascii')
    name = _UNSAFE_CHARS.sub('', stem)[:MAX_NAME_LEN] or 'FONT'

    used = {t.upper() for t in taken}
    if name not in used:
        return name
    for n in range(2, 1000):
        suffix = str(n)
        candidate = name[:MAX_NAME_LEN - len(suffix)] + suffix
        if candidate not in used:
            return candidate
    return name


def printer_font_path(name: str) -> str:
    """Full printer path for a font object, e.g. E:DEJAVUSA.TTF."""
    return f"{FONT_DEVICE}{name}{FONT_EXTENSION}"


# --- printer I/O ------------------------------------------------------------

def build_font_upload(font_path: str, name: str) -> bytes:
    """The ~DY payload that stores a font file on the printer."""
    data = Path(font_path).read_bytes()
    header = f"~DY{FONT_DEVICE}{name},A,TT,{len(data)},{len(data)},".encode('ascii')
    return header + data


def _send(address: str, port: int, payload: bytes, timeout: float,
          read_reply: bool = False) -> bytes:
    """Send a payload to the printer, optionally reading whatever it replies."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect((address, port))
        sock.sendall(payload)
        if not read_reply:
            return b''
        chunks = []
        deadline = time.monotonic() + timeout
        while True:
            try:
                chunk = sock.recv(4096)
            except (socket.timeout, TimeoutError):
                break
            if not chunk:
                break
            chunks.append(chunk)
            # Once the printer starts talking it sends the rest promptly, so
            # drop to a short timeout rather than waiting out the full one.
            sock.settimeout(0.5)
            if time.monotonic() > deadline:
                break
        return b''.join(chunks)
    finally:
        sock.close()


def upload_font(address: str, port: int, font_path: str, name: str,
                timeout: float = 30) -> None:
    """Store a local font file on the printer as E:<name>.TTF."""
    _send(address, port, build_font_upload(font_path, name), timeout)


def query_printer_fonts(address: str, port: int,
                        timeout: float = 5) -> Optional[Set[str]]:
    """Font object names present on the printer, or None if it did not answer.

    None and an empty set mean different things and callers rely on the
    difference: an empty set is "the printer has no fonts", None is "the
    printer could not be asked" - unreachable, or no ^HW support.
    """
    try:
        reply = _send(address, port, b'^XA^HWE:*.TTF^XZ', timeout, read_reply=True)
    except OSError:
        return None
    if not reply:
        return None
    text = reply.decode('ascii', 'replace')
    return {m.group(1).upper() for m in _OBJECT_NAME.finditer(text)}


def delete_printer_font(address: str, port: int, name: str,
                        timeout: float = 10) -> None:
    """Delete a font object from the printer."""
    payload = f"^XA^ID{printer_font_path(name)}^FS^XZ".encode('ascii')
    _send(address, port, payload, timeout)


# --- local rendering --------------------------------------------------------

def register_app_font(font_path: str) -> None:
    """Make a font file visible to fontconfig, so Pango/Cairo can find it."""
    try:
        fc = ctypes.CDLL("libfontconfig.so.1")
        fc.FcConfigAppFontAddFile.restype = ctypes.c_int
        fc.FcConfigAppFontAddFile.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
        fc.FcConfigAppFontAddFile(None, font_path.encode())
    except Exception:
        pass
