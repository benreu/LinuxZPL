#!/usr/bin/env python3
"""
LinuxZPL - a visual designer for Zebra thermal labels.

Two frontends sit on one core: GTK3 and PySide2/Qt5. They answer to the same
FUNCTIONAL_SPEC.md and produce the same ZPL; which one opens is only a question
of which toolkit is installed, or which you prefer. With no flag, GTK is used
when it is available and Qt when it is not.
"""

import argparse
import os
import sys

from zplcore import workflow

QT_INSTALL = ("python3-pyside2.qtcore python3-pyside2.qtgui "
              "python3-pyside2.qtwidgets  (or: pip install PySide2)")
GTK_INSTALL = "python3-gi python3-gi-cairo gir1.2-gtk-3.0"


def _have(module: str) -> bool:
    """Whether a toolkit can be imported, without keeping it loaded."""
    import importlib.util
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def run(frontend: str, path: str = None, fields=None) -> int:
    if frontend == 'qt':
        from qtui import main
    else:
        from gtkui import main
    return main(path, fields) or 0


def save_filled(path: str, out: str, pairs) -> int:
    """Write the template at `path`, filled in with `pairs`, to `out`."""
    from zplcore import parser, renderer

    try:
        content, _ = parser.read_file(path)
        document, _ = parser.parse_zpl(content, renderer.ZPLRenderer())
        dropped = workflow.unsupported_commands(content)
        if dropped:
            print(f"Not kept in {out}: {', '.join(dropped)}", file=sys.stderr)
        workflow.fill_template(document, pairs)
        with open(workflow.save_filename(out), 'w', encoding='utf-8') as f:
            f.write(document.to_zpl())
    except (OSError, ValueError) as e:
        print(f"Cannot save {out}: {e}", file=sys.stderr)
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.strip().split('\n')[0])
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--qt', dest='frontend', action='store_const', const='qt',
                       help='use the PySide2/Qt5 frontend')
    group.add_argument('--gtk', dest='frontend', action='store_const', const='gtk',
                       help='use the GTK3 frontend')
    parser.add_argument('--load-file', dest='load_file', metavar='FILE',
                        help='ZPL template to open on start')
    parser.add_argument('--field', dest='fields', action='append', metavar='N=DATA',
                        help='data for ^FN field N in the template; may be repeated '
                             '(needs --load-file)')
    parser.add_argument('--save-file', dest='save_file', metavar='FILE',
                        help='write the template, with any --field data filled in, '
                             'to FILE and exit without opening a window '
                             '(needs --load-file)')
    args = parser.parse_args()

    path = args.load_file
    if path and not os.path.isfile(path):
        print(f"Cannot open {path}: no such file", file=sys.stderr)
        return 1

    fields = None
    if args.fields:
        if not path:
            print("--field needs --load-file: there is no template to fill in",
                  file=sys.stderr)
            return 1
        try:
            fields = workflow.parse_field_args(args.fields)
        except ValueError as e:
            print(e, file=sys.stderr)
            return 1

    if args.save_file:
        if not path:
            print("--save-file needs --load-file: there is no template to save",
                  file=sys.stderr)
            return 1
        if args.frontend:
            print("--save-file opens no window, so --qt and --gtk do not apply",
                  file=sys.stderr)
            return 1
        return save_filled(path, args.save_file, fields or [])

    if args.frontend:
        wanted = args.frontend
        module = 'PySide2' if wanted == 'qt' else 'gi'
        if not _have(module):
            install = QT_INSTALL if wanted == 'qt' else GTK_INSTALL
            print(f"The {wanted} frontend needs {module}, which is not installed.\n"
                  f"  install: {install}", file=sys.stderr)
            return 1
        return run(wanted, path, fields)

    # Nothing asked for, so use what is here. GTK is the default because it is
    # the frontend this project shipped first; Qt takes over only when GTK is
    # not installed.
    if _have('gi'):
        return run('gtk', path, fields)
    if _have('PySide2'):
        return run('qt', path, fields)
    print("No supported GUI toolkit found. Install one of:\n"
          f"  Qt:  {QT_INSTALL}\n"
          f"  GTK: {GTK_INSTALL}", file=sys.stderr)
    return 1


if __name__ == '__main__':
    sys.exit(main())
