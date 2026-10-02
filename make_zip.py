#!/usr/bin/env python3
"""Pack everything needed to run LinuxZPL into a compressed LinuxZPL.zip.

Only an explicit list of files and folders goes in, so version-control and
editor folders, caches, scratch labels and the Zebra PDF manuals stay out. Everything unpacks into a single LinuxZPL/ folder.
"""

import argparse
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TOP = "LinuxZPL"

SOURCE_DIRS = ["zplcore", "qtui", "gtkui"]
WHOLE_DIRS = ["tests"]  # every file, fixtures and scripts included
FILES = [
    "linuxzpl.py",
    "run.sh",
    "pyproject.toml",
    "requirements.txt",
    "requirements-qt.txt",
    "requirements-gtk.txt",
    "README.md",
    "LICENSE",
    "FONTS_HOWTO.md",
    "FUNCTIONAL_SPEC.md",
    "CONTRIBUTING.md",
    "sample.zpl",
]
EXECUTABLE = {"linuxzpl.py", "run.sh", "tests/run.sh"}
SKIP_PARTS = {".git", ".vscode", ".claude", "__pycache__"}


def skipped(path):
    rel = path.relative_to(ROOT)
    return (
        bool(SKIP_PARTS & set(rel.parts))
        or path.suffix.lower() in (".pyc", ".pdf")
        or path.name == "settings.ini"
    )


def collect():
    found = []
    for name in FILES:
        path = ROOT / name
        if not path.is_file():
            print("warning: missing %s" % name, file=sys.stderr)
        elif not skipped(path):
            found.append(path)
    for folder in SOURCE_DIRS:
        base = ROOT / folder
        if not base.is_dir():
            print("warning: missing %s/" % folder, file=sys.stderr)
            continue
        found += [p for p in sorted(base.rglob("*.py")) if not skipped(p)]
    for folder in WHOLE_DIRS:
        base = ROOT / folder
        if not base.is_dir():
            print("warning: missing %s/" % folder, file=sys.stderr)
            continue
        found += [p for p in sorted(base.rglob("*")) if p.is_file() and not skipped(p)]
    return found


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("-o", "--output", default=str(ROOT / "LinuxZPL.zip"),
                        help="zip file to write (default: LinuxZPL.zip beside this script)")
    args = parser.parse_args()
    out = Path(args.output).resolve()

    files = [p for p in collect() if p != out]
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for path in files:
            rel = path.relative_to(ROOT)
            info = zipfile.ZipInfo.from_file(path, "%s/%s" % (TOP, rel.as_posix()))
            mode = 0o755 if rel.as_posix() in EXECUTABLE else 0o644
            info.external_attr = (0o100000 | mode) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, path.read_bytes(), compresslevel=9)

    print("wrote %s: %d files, %.1f KiB" % (out, len(files), out.stat().st_size / 1024))


if __name__ == "__main__":
    main()
