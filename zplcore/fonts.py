"""
ZPL Fonts - font discovery, naming, and printer font management.

Keeps the rules for turning a local font file into a printer object name in one
place, and wraps the ZPL commands for uploading, listing and deleting fonts on
the printer.
"""

import ctypes
import re
from pathlib import Path
from typing import Dict, Iterable, List, NamedTuple, Optional, Set, Tuple

from PIL import ImageFont

from . import printer_io
from .graphic_store import DEVICES

# Fonts are 8.3 TrueType objects, and ~DY can write one to any of the four
# devices DEVICES lists - the same four Graphics offers, and for the same
# reason Z: is not among them: it is read-only factory content ~DY cannot
# write and ^ID will not delete. E: is the default because that is where this
# app has always put them, and the value every caller falls back to when no
# device is chosen; ZPL's own default for an omitted one is R:, which is not
# the same thing and is never relied on here - every command below spells the
# device out.
DEFAULT_FONT_DEVICE = 'E'
FONT_EXTENSION = '.TTF'
MAX_NAME_LEN = 8

# ~HI reports head resolution in dots per mm
DOTS_PER_MM_TO_DPI = {6: 152, 8: 203, 12: 300, 24: 600}
SUPPORTED_DPI = (203, 300, 600)
DEFAULT_DPI = 203

# Styles preferred when a family ships several faces
_PREFERRED_STYLES = ('regular', 'book', 'roman', 'normal')
_UNSAFE_CHARS = re.compile(r'[^A-Z0-9_-]')
_OBJECT_NAME = re.compile(r'([A-Za-z0-9_\-]{1,8})\.TTF', re.IGNORECASE)
_FONT_OBJECT_NAME = re.compile(r'([A-Za-z0-9_\-]{1,8})\.FNT', re.IGNORECASE)

# Zebra's standard resident fonts - built into printer firmware, not objects
# that can be uploaded or deleted. Matrix/kind are from the ZPL Programming
# Guide's font table; unlike the TrueType fonts above, there is no local file
# for these, so nothing here can be rendered - only reported. '0' matches
# parser.SCALABLE_FONTS.
RESIDENT_FONTS = [
    {'code': '0', 'name': 'Font 0', 'matrix': 'Scalable',
     'kind': 'Scalable outline (CG Triplet)'},
    {'code': 'A', 'name': 'Font A', 'matrix': '9 x 5', 'kind': 'Fixed bitmap'},
    {'code': 'B', 'name': 'Font B', 'matrix': '11 x 7', 'kind': 'Fixed bitmap'},
    {'code': 'C', 'name': 'Font C', 'matrix': '18 x 10', 'kind': 'Fixed bitmap'},
    {'code': 'D', 'name': 'Font D', 'matrix': '18 x 10', 'kind': 'Fixed bitmap'},
    {'code': 'E', 'name': 'Font E', 'matrix': '28 x 15', 'kind': 'OCR-B'},
    {'code': 'F', 'name': 'Font F', 'matrix': '26 x 13', 'kind': 'Fixed bitmap'},
    {'code': 'G', 'name': 'Font G', 'matrix': '60 x 40', 'kind': 'Fixed bitmap'},
    {'code': 'H', 'name': 'Font H', 'matrix': '21 x 13', 'kind': 'OCR-A'},
    {'code': 'GS', 'name': 'Font GS', 'matrix': '24 x 24', 'kind': 'Symbols'},
]

# Where fonts live when fc-list can't be asked - fontconfig missing, broken,
# or just not installed on a minimal system. Module-level so tests can
# monkeypatch them without touching the real filesystem.
FALLBACK_FONT_DIRS = ('/usr/share/fonts', '/usr/local/share/fonts',
                      '~/.fonts', '~/.local/share/fonts')
FALLBACK_BUNDLED_ROOT = '/opt'
FALLBACK_BUNDLED_GLOB = '*/usr/share/fonts'


class ScannedDir(NamedTuple):
    """One directory the fallback scanner looked at."""
    path: str
    exists: bool
    font_count: int


class FontScanReport(NamedTuple):
    """What the fallback directory scan found, for the fallback itself and
    for the Local Fonts... diagnostic dialog."""
    dirs: List[ScannedDir]
    families: Dict[str, str]   # same shape as list_ttf_families()
    files: List[str]           # same shape as _all_ttf_paths()


_families_cache: Optional[Dict[str, str]] = None
_paths_cache: Optional[list] = None
_scan_cache: Optional[FontScanReport] = None

# Font files this session has registered, and the subset Qt has been given.
# Kept apart because Qt cannot be told about a font until it has an
# application; see register_app_fonts_with_qt().
_app_fonts: Set[str] = set()
_qt_registered: Set[str] = set()


# --- installed system fonts -------------------------------------------------

def _style_rank(style: str) -> int:
    try:
        return _PREFERRED_STYLES.index(style.lower())
    except ValueError:
        return len(_PREFERRED_STYLES)


def _fc_list_families() -> Dict[str, str]:
    """fc-list's view of installed families, family -> path.

    Only TrueType can be sent to the printer (~DY ...,TT,), so .otf, .pfb, .t1
    and .ttc families are deliberately left out - offering them in a chooser
    would produce fonts that cannot be uploaded.
    """
    import subprocess
    try:
        out = subprocess.run(
            ['fc-list', '-f', '%{family[0]}\t%{style[0]}\t%{file}\n'],
            capture_output=True, text=True, timeout=15, check=False).stdout
    except (OSError, subprocess.SubprocessError):
        return {}

    best: Dict[str, tuple] = {}
    for line in out.splitlines():
        parts = line.split('\t')
        if len(parts) != 3:
            continue
        family, style, path = (p.strip() for p in parts)
        if not family or not path.lower().endswith('.ttf'):
            continue
        rank = _style_rank(style)
        if family not in best or rank < best[family][0]:
            best[family] = (rank, path)

    return {family: path for family, (_, path) in best.items()}


def list_ttf_families(refresh: bool = False) -> Dict[str, str]:
    """Installed families that have a .ttf file, as family -> path.

    fc-list is asked first; if it is missing, broken, or simply reports
    nothing (a minimal or sandboxed install without fontconfig set up),
    scan_font_directories() supplies the fallback instead. Cached, because
    the chooser's filter callback runs once per family.
    """
    global _families_cache
    if _families_cache is not None and not refresh:
        return _families_cache

    families = _fc_list_families()
    if not families:
        families = scan_font_directories().families
    _families_cache = families
    return _families_cache


def file_for_family(family: str) -> Optional[str]:
    """The .ttf file for an installed family, or None if it has none.

    Deliberately an exact lookup rather than fc-match, which always returns a
    best guess and would silently substitute a different font for a name that
    is not installed.
    """
    return list_ttf_families().get(family)


def _fc_list_paths() -> List[str]:
    """fc-list's view of every installed .ttf file, not just one per family."""
    import subprocess
    try:
        out = subprocess.run(['fc-list', '-f', '%{file}\n'],
                             capture_output=True, text=True, timeout=15,
                             check=False).stdout
    except (OSError, subprocess.SubprocessError):
        out = ''
    return sorted({line.strip() for line in out.splitlines()
                   if line.strip().lower().endswith('.ttf')})


def _all_ttf_paths(refresh: bool = False) -> List[str]:
    """Every installed .ttf file, not just one per family.

    Same fc-list-first, scan_font_directories() fallback as
    list_ttf_families(), so the two stay consistent on a system where
    fc-list can't be asked.
    """
    global _paths_cache
    if _paths_cache is not None and not refresh:
        return _paths_cache
    paths = _fc_list_paths()
    if not paths:
        paths = scan_font_directories().files
    _paths_cache = paths
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


def _name_and_style(font_path: str) -> Tuple[str, str]:
    """The family and style recorded inside a font file."""
    return ImageFont.truetype(font_path, 12).getname()


def family_for_file(font_path: str) -> str:
    """The family name recorded inside a font file."""
    return _name_and_style(font_path)[0]


# --- fallback: scanning font directories directly ---------------------------

def _fallback_directories() -> List[str]:
    """Directories the fallback scanner checks, fixed dirs then bundled ones.

    FALLBACK_FONT_DIRS and FALLBACK_BUNDLED_ROOT are module globals so a test
    can point this at a throwaway tree instead of the real filesystem.
    """
    fixed = [str(Path(d).expanduser()) for d in FALLBACK_FONT_DIRS]
    bundled = sorted(str(p) for p in
                     Path(FALLBACK_BUNDLED_ROOT).glob(FALLBACK_BUNDLED_GLOB))
    return fixed + bundled


def scan_font_directories(refresh: bool = False) -> FontScanReport:
    """Find .ttf files directly, for when fc-list can't be asked.

    Used both as the actual fallback inside list_ttf_families()/
    _all_ttf_paths() and, unconditionally, by the Local Fonts... dialog - so
    there is exactly one place that walks the filesystem. Cached like the
    fc-list-backed lookups; refresh=True forces a fresh walk (the dialog's
    Rescan button).
    """
    global _scan_cache
    if _scan_cache is not None and not refresh:
        return _scan_cache

    dirs: List[ScannedDir] = []
    files: List[str] = []
    best: Dict[str, tuple] = {}

    for directory in _fallback_directories():
        path = Path(directory)
        exists = path.is_dir()
        count = 0
        if exists:
            try:
                found = [p for p in path.rglob('*')
                        if p.is_file() and p.suffix.lower() == '.ttf']
            except OSError:
                found = []
            for font_path in found:
                count += 1
                fp = str(font_path)
                files.append(fp)
                try:
                    family, style = _name_and_style(fp)
                except Exception:
                    continue
                if not family:
                    continue
                rank = _style_rank(style)
                if family not in best or rank < best[family][0]:
                    best[family] = (rank, fp)
        dirs.append(ScannedDir(path=directory, exists=exists, font_count=count))

    families = {family: fp for family, (_, fp) in best.items()}
    _scan_cache = FontScanReport(dirs=dirs, families=families,
                                 files=sorted(files))
    return _scan_cache


def font_discovery_status(refresh: bool = False) -> Tuple[bool, FontScanReport]:
    """Whether fc-list is currently supplying fonts, and what the directory
    scan finds regardless - what the Local Fonts... dialog shows.

    fc-list is always re-checked live (cheap, and the point of the dialog is
    to be trustworthy about the system's current state); the directory walk
    itself only re-runs when refresh=True, so opening the dialog is free and
    Rescan is the only thing that costs a fresh walk. The scan runs even when
    fc-list is healthy, so the dialog is useful before fc-list ever breaks,
    not just after.
    """
    fc_list_ok = bool(_fc_list_families())
    return fc_list_ok, scan_font_directories(refresh=refresh)


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


def printer_font_path(name: str, device: str = DEFAULT_FONT_DEVICE) -> str:
    """Full printer path for a font object, e.g. E:DEJAVUSA.TTF."""
    return f"{device}:{name}{FONT_EXTENSION}"


# --- printer I/O ------------------------------------------------------------

def split_font_spec(spec: str) -> Tuple[str, str]:
    """A 'd:NAME.TTF' spec as (device, name), for a caller that has to hand
    the two to upload_font or delete_printer_font separately.

    Its own function rather than graphic_store.split_device_spec, which
    defaults a spec with no device to 'R' and its extension to 'GRF': both
    are right for a graphic and wrong here, where a missing device should
    mean the font default rather than some other drive.
    """
    device, _, rest = (spec or '').partition(':')
    if not rest:
        device, rest = DEFAULT_FONT_DEVICE, (spec or '')
    return device.upper() or DEFAULT_FONT_DEVICE, rest.rsplit('.', 1)[0]


def build_font_upload(font_path: str, name: str,
                      device: str = DEFAULT_FONT_DEVICE) -> bytes:
    """The ~DY payload that stores a font file on the printer.

    ~DYd:f,b,x,t,w,data - only `d` is chosen here. `b` and `x` are left at
    the A,TT this app has always sent, which real hardware accepts, though
    the manual's own tables give B for "uncompressed (.TTE, .TTF, binary)"
    and T for TrueType and list no TT at all. Changing them is a question
    for a printer, not for a reading of the manual, so it is not changed
    here on the way past.
    """
    data = Path(font_path).read_bytes()
    header = f"~DY{device}:{name},A,TT,{len(data)},{len(data)},".encode('ascii')
    return header + data


def upload_font(address: str, port: int, font_path: str, name: str,
                device: str = DEFAULT_FONT_DEVICE, timeout: float = 30,
                cancel=None) -> None:
    """Store a local font file on the printer as <device>:<name>.TTF."""
    printer_io.send(address, port, build_font_upload(font_path, name, device),
                    timeout, cancel=cancel)


def query_printer_fonts(address: str, port: int, timeout: float = 5,
                        cancel=None) -> Optional[Set[str]]:
    """Every font object on the printer as 'd:NAME.TTF', across all of
    DEVICES, or None if it could not be asked.

    Specs rather than bare names, because the same name on two devices is two
    objects and only one of them is the one a label asked for: a ^A@ naming
    E:MYFONT.TTF is not satisfied by an R:MYFONT.TTF, and a check keyed on
    the name alone would call it present and let the print fall back to a
    substitute.

    None and an empty set mean different things and callers rely on the
    difference: an empty set is "the printer has no fonts", None is "the
    printer could not be asked" - unreachable, or no ^HW support.

    A failure to *connect* on the very first attempt is decisive - that is
    the socket, not a drive - and returns None straight away, which is what
    keeps a dead host failing fast rather than timing out once per device.
    Silence is a weaker signal and is not treated the same way: R: is asked
    first, and a printer with nothing on R: - or no R: at all - is not an
    unreachable printer, so a device that says nothing is skipped and only a
    run in which *no* device said anything at all reads as unreachable. A
    device that answers, even to list nothing, is proof the printer is there
    and understood the question.

    graphic_store.query_printer_graphics and
    printer_objects.query_printer_objects judge it the same way, for the
    same reason.
    """
    specs: Set[str] = set()
    answered = False
    for index, device in enumerate(DEVICES):
        payload = f'^XA^HW{device}:*.TTF^XZ'.encode('ascii')
        try:
            reply = printer_io.send(address, port, payload, timeout,
                                    read_reply=True, cancel=cancel)
        except OSError:
            if index == 0:
                return None  # the connection itself failed
            continue
        if not reply:
            continue
        answered = True
        text = reply.decode('ascii', 'replace')
        for m in _OBJECT_NAME.finditer(text):
            specs.add(f"{device}:{m.group(1).upper()}{FONT_EXTENSION}")
    return specs if answered else None


def query_resident_fonts(address: str, port: int, timeout: float = 5,
                         cancel=None) -> Optional[Set[str]]:
    """Resident font codes the printer reports on its read-only Z: memory.

    Best-effort: Zebra's built-in fonts (A-H, 0, GS - see RESIDENT_FONTS)
    conventionally show up as .FNT objects on Z: when a printer answers ^HW
    for it, but this is not guaranteed on every model or firmware. Same
    None-vs-empty-set contract as query_printer_fonts: None means the printer
    could not be asked, not that it reported nothing.
    """
    try:
        reply = printer_io.send(address, port, b'^XA^HWZ:*.FNT^XZ', timeout,
                                read_reply=True, cancel=cancel)
    except OSError:
        return None
    if not reply:
        return None
    text = reply.decode('ascii', 'replace')
    return {m.group(1).upper() for m in _FONT_OBJECT_NAME.finditer(text)}


def query_printer_dpi(address: str, port: int, timeout: float = 5,
                      cancel=None) -> Optional[int]:
    """The printer's resolution in dpi, or None if it could not be asked.

    ~HI answers with model, firmware and the head resolution in dots per mm,
    which is the field of interest: 6 -> 152, 8 -> 203, 12 -> 300, 24 -> 600.
    Returns None rather than a guess so the manual setting stays authoritative
    when the printer is unreachable or answers in an unexpected shape.
    """
    try:
        reply = printer_io.send(address, port, b'~HI', timeout, read_reply=True,
                                cancel=cancel)
    except OSError:
        return None
    if not reply:
        return None
    text = reply.decode('ascii', 'replace')
    for dots_per_mm, dpi in sorted(DOTS_PER_MM_TO_DPI.items()):
        # the resolution appears as its own comma-separated field
        if re.search(rf'(?:^|,)\s*{dots_per_mm}\s*(?:,|$)', text, re.M):
            return dpi
    return None


def delete_printer_font(address: str, port: int, name: str,
                        device: str = DEFAULT_FONT_DEVICE,
                        timeout: float = 10, cancel=None) -> None:
    """Delete a font object from the printer.

    Name and device, not a d:o.x spec: graphic_store.split_device_spec would
    read a bare name as R:, and a delete aimed at the wrong device is one
    that quietly removes nothing, or the wrong thing.
    """
    payload = f"^XA^ID{printer_font_path(name, device)}^FS^XZ".encode('ascii')
    printer_io.send(address, port, payload, timeout, cancel=cancel)


# --- local rendering --------------------------------------------------------

def register_app_font(font_path: str) -> None:
    """Make a font file visible to the toolkits that have to draw with it.

    A font chosen for a label may not be one fontconfig or Qt already knows
    about, and neither will load a face by path on its own. Registering with
    both keeps the canvas drawing the real face instead of a substitute, which
    is the difference between a proof and an approximation.
    """
    try:
        fc = ctypes.CDLL("libfontconfig.so.1")
        fc.FcConfigAppFontAddFile.restype = ctypes.c_int
        fc.FcConfigAppFontAddFile.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
        fc.FcConfigAppFontAddFile(None, font_path.encode())
    except Exception:
        pass

    _app_fonts.add(font_path)
    register_app_fonts_with_qt()


def register_app_fonts_with_qt() -> None:
    """Hand Qt every font registered so far, once there is an application.

    QFontDatabase reaches into the platform integration, which does not exist
    until a QGuiApplication does; calling it before then aborts the process
    rather than raising, so it cannot simply be wrapped in try/except. Fonts
    registered before the application starts are therefore remembered and
    handed over on the first call that finds one running - which is why the
    window calls this during startup.
    """
    try:
        from PySide2.QtGui import QFontDatabase, QGuiApplication
        if QGuiApplication.instance() is None:
            return
        for path in sorted(_app_fonts - _qt_registered):
            QFontDatabase.addApplicationFont(path)
            _qt_registered.add(path)
    except Exception:
        pass
