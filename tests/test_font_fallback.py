"""
Font discovery fallback: scan_font_directories(), and the fc-list-first,
scan-second behaviour of list_ttf_families()/_all_ttf_paths().

Pure zplcore - no toolkit import, no display needed.
"""

import shutil, subprocess, sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from zplcore import fonts as zpl_fonts

fails = []
def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + ((" -- " + str(extra)) if extra else ""))
    if not cond: fails.append(name)

REAL_DEJAVU = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
REAL_DEJAVU_BOLD = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")


def _reset_caches():
    zpl_fonts._families_cache = None
    zpl_fonts._paths_cache = None
    zpl_fonts._scan_cache = None


def _no_fc_list(*a, **kw):
    raise FileNotFoundError("fc-list not installed")


def _fake_fc_list_one_family(*a, **kw):
    class Result:
        stdout = f"DejaVu Sans\tBook\t{REAL_DEJAVU}\n"
    return Result()


# --- fc-list succeeds: the scan is never consulted --------------------------

_reset_caches()
_real_run = subprocess.run
subprocess.run = _fake_fc_list_one_family
try:
    families = zpl_fonts.list_ttf_families()
    scan_ran_for_families_alone = zpl_fonts._scan_cache is not None
    # font_discovery_status() runs the scan unconditionally (it's the whole
    # point of the diagnostic dialog), so this must be checked before it's
    # called, not after.
    fc_list_ok, _report = zpl_fonts.font_discovery_status()
finally:
    subprocess.run = _real_run

check("fc-list succeeding: list_ttf_families() reflects it",
      families == {"DejaVu Sans": str(REAL_DEJAVU)}, families)
check("fc-list succeeding: list_ttf_families() alone never ran the scan",
      not scan_ran_for_families_alone)
check("fc-list succeeding: font_discovery_status() reports it as ok",
      fc_list_ok is True)


# --- fc-list fails: both functions fall back to the directory scan ---------

tmp = tempfile.mkdtemp(prefix='linuxzpl-font-scan-')
try:
    fonts_dir = Path(tmp) / "fonts"
    fonts_dir.mkdir()
    shutil.copy(REAL_DEJAVU, fonts_dir / "DejaVuSans.ttf")

    _real_dirs = zpl_fonts.FALLBACK_FONT_DIRS
    _real_root = zpl_fonts.FALLBACK_BUNDLED_ROOT
    zpl_fonts.FALLBACK_FONT_DIRS = (str(fonts_dir),)
    zpl_fonts.FALLBACK_BUNDLED_ROOT = str(Path(tmp) / "opt-empty")

    _reset_caches()
    _real_run = subprocess.run
    subprocess.run = _no_fc_list
    try:
        families = zpl_fonts.list_ttf_families()
        paths = zpl_fonts._all_ttf_paths()
        fc_list_ok, report = zpl_fonts.font_discovery_status()
    finally:
        subprocess.run = _real_run
        zpl_fonts.FALLBACK_FONT_DIRS = _real_dirs
        zpl_fonts.FALLBACK_BUNDLED_ROOT = _real_root

    check("fc-list failing: list_ttf_families() falls back to the scan",
          families == {"DejaVu Sans": str(fonts_dir / "DejaVuSans.ttf")}, families)
    check("fc-list failing: _all_ttf_paths() falls back to the scan too",
          paths == [str(fonts_dir / "DejaVuSans.ttf")], paths)
    check("fc-list failing: font_discovery_status() reports it as not ok",
          fc_list_ok is False)
    check("fc-list failing: the report's own families/files match",
          report.families == families and report.files == paths)
finally:
    shutil.rmtree(tmp, ignore_errors=True)


# --- scan_font_directories(): recursive walk, missing dirs, decoys ---------

tmp = tempfile.mkdtemp(prefix='linuxzpl-font-scan-')
try:
    nested = Path(tmp) / "present" / "sub" / "deeper"
    nested.mkdir(parents=True)
    shutil.copy(REAL_DEJAVU, nested / "DejaVuSans.ttf")
    (Path(tmp) / "present" / "not-a-font.otf").write_bytes(b"not a real font")
    (Path(tmp) / "present" / "no-extension").write_bytes(b"also not a font")
    missing_dir = str(Path(tmp) / "does-not-exist")

    _real_dirs = zpl_fonts.FALLBACK_FONT_DIRS
    _real_root = zpl_fonts.FALLBACK_BUNDLED_ROOT
    zpl_fonts.FALLBACK_FONT_DIRS = (str(Path(tmp) / "present"), missing_dir)
    zpl_fonts.FALLBACK_BUNDLED_ROOT = str(Path(tmp) / "opt-empty")

    _reset_caches()
    try:
        report = zpl_fonts.scan_font_directories()
    finally:
        zpl_fonts.FALLBACK_FONT_DIRS = _real_dirs
        zpl_fonts.FALLBACK_BUNDLED_ROOT = _real_root

    by_path = {d.path: d for d in report.dirs}
    present = by_path[str(Path(tmp) / "present")]
    absent = by_path[missing_dir]

    check("scan finds a .ttf nested several directories deep",
          present.exists and present.font_count == 1, present)
    check("scan excludes non-.ttf files (.otf, extensionless)",
          report.files == [str(nested / "DejaVuSans.ttf")], report.files)
    check("scan reports a configured, missing directory honestly",
          absent.exists is False and absent.font_count == 0, absent)
finally:
    shutil.rmtree(tmp, ignore_errors=True)


# --- /opt/*/usr/share/fonts globbing ----------------------------------------

tmp = tempfile.mkdtemp(prefix='linuxzpl-font-scan-')
try:
    bundled = Path(tmp) / "exampleapp" / "usr" / "share" / "fonts"
    bundled.mkdir(parents=True)
    shutil.copy(REAL_DEJAVU, bundled / "DejaVuSans.ttf")

    _real_dirs = zpl_fonts.FALLBACK_FONT_DIRS
    _real_root = zpl_fonts.FALLBACK_BUNDLED_ROOT
    zpl_fonts.FALLBACK_FONT_DIRS = ()
    zpl_fonts.FALLBACK_BUNDLED_ROOT = str(Path(tmp))

    dirs = zpl_fonts._fallback_directories()
    _reset_caches()
    try:
        report = zpl_fonts.scan_font_directories()
    finally:
        zpl_fonts.FALLBACK_FONT_DIRS = _real_dirs
        zpl_fonts.FALLBACK_BUNDLED_ROOT = _real_root

    check("/opt/*/usr/share/fonts is picked up by _fallback_directories()",
          str(bundled) in dirs, dirs)
    check("a bundled-app font dir's fonts are counted",
          report.files == [str(bundled / "DejaVuSans.ttf")], report.files)
finally:
    shutil.rmtree(tmp, ignore_errors=True)


# --- a malformed .ttf doesn't abort the scan --------------------------------

tmp = tempfile.mkdtemp(prefix='linuxzpl-font-scan-')
try:
    fonts_dir = Path(tmp) / "fonts"
    fonts_dir.mkdir()
    shutil.copy(REAL_DEJAVU, fonts_dir / "DejaVuSans.ttf")
    (fonts_dir / "garbage.ttf").write_bytes(b"this is not a ttf file at all")

    _real_dirs = zpl_fonts.FALLBACK_FONT_DIRS
    _real_root = zpl_fonts.FALLBACK_BUNDLED_ROOT
    zpl_fonts.FALLBACK_FONT_DIRS = (str(fonts_dir),)
    zpl_fonts.FALLBACK_BUNDLED_ROOT = str(Path(tmp) / "opt-empty")

    _reset_caches()
    try:
        report = zpl_fonts.scan_font_directories()
    finally:
        zpl_fonts.FALLBACK_FONT_DIRS = _real_dirs
        zpl_fonts.FALLBACK_BUNDLED_ROOT = _real_root

    check("a malformed .ttf doesn't raise, and both files are counted",
          report.dirs[0].font_count == 2, report.dirs[0])
    check("only the valid file makes it into families",
          report.families == {"DejaVu Sans": str(fonts_dir / "DejaVuSans.ttf")},
          report.families)
finally:
    shutil.rmtree(tmp, ignore_errors=True)


# --- style ranking decides the canonical face, not walk order --------------

if REAL_DEJAVU_BOLD.exists():
    tmp = tempfile.mkdtemp(prefix='linuxzpl-font-scan-')
    try:
        fonts_dir = Path(tmp) / "fonts"
        fonts_dir.mkdir()
        # Bold sorts before the Book face alphabetically ('-' < '.'), so a
        # first-found-wins scan would wrongly pick Bold as canonical.
        shutil.copy(REAL_DEJAVU_BOLD, fonts_dir / "DejaVuSans-Bold.ttf")
        shutil.copy(REAL_DEJAVU, fonts_dir / "DejaVuSans.ttf")

        _real_dirs = zpl_fonts.FALLBACK_FONT_DIRS
        _real_root = zpl_fonts.FALLBACK_BUNDLED_ROOT
        zpl_fonts.FALLBACK_FONT_DIRS = (str(fonts_dir),)
        zpl_fonts.FALLBACK_BUNDLED_ROOT = str(Path(tmp) / "opt-empty")

        _reset_caches()
        try:
            report = zpl_fonts.scan_font_directories()
        finally:
            zpl_fonts.FALLBACK_FONT_DIRS = _real_dirs
            zpl_fonts.FALLBACK_BUNDLED_ROOT = _real_root

        check("style ranking picks the Book/Regular face over Bold",
              report.families["DejaVu Sans"] == str(fonts_dir / "DejaVuSans.ttf"),
              report.families)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
else:
    check("style ranking picks the Book/Regular face over Bold "
         "(skipped - no DejaVuSans-Bold.ttf on this system)", True)


print()
print(("ALL CHECKS PASSED" if not fails else f"{len(fails)} FAILED: {fails}"))
sys.exit(1 if fails else 0)
