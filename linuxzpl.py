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

from zplcore import Label, workflow  # noqa: F401 - Label is the Python API

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


def show_help(frontend: str, topic: str) -> int:
    """LinuxZPL's own help viewer alone, at `topic`, as the designer starts
    it when there is no Yelp."""
    if frontend == 'qt':
        from qtui.helpviewer import main
    else:
        from gtkui.helpviewer import main
    return main(topic) or 0


def check_topic(topic: str) -> str:
    """A message saying why `topic` is not a help page, or a section of one;
    "" when it is."""
    from zplcore import helpview
    try:
        helpview.Help().check(topic)
    except ValueError as e:
        return f"--show-help: {e}"
    return ""


def save_filled(path: str, out: str, pairs) -> int:
    """Write the template at `path`, filled in with `pairs`, to `out`."""
    from zplcore import Label

    try:
        label = Label.load(path)
        if label.dropped:
            print(f"Not kept in {out}: {', '.join(label.dropped)}", file=sys.stderr)
        label.fill(dict(pairs))
        label.save(out)
    except (OSError, ValueError) as e:
        print(f"Cannot save {out}: {e}", file=sys.stderr)
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    """The command line, apart from main() so that the test suite can check
    help/linuxzpl.1 names every option in it."""
    parser = argparse.ArgumentParser(
        description=__doc__.strip().split('\n')[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="examples:\n"
               "  linuxzpl.py --load-file label.zpl\n"
               "      open a template in the designer\n"
               "  linuxzpl.py --load-file label.zpl --field 1=Hello --save-file out.zpl\n"
               "      fill ^FN1 and write the label to out.zpl, with no window")
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
                             'to FILE and exit without opening a window or needing a GUI '
                             'toolkit (needs --load-file)')
    parser.add_argument('--show-help', dest='show_help', nargs='?', const='index',
                        metavar='TOPIC',
                        help="open only the help, in LinuxZPL's own viewer, at TOPIC: a "
                             "page of it, or page#section (the contents if none is given)")
    return parser


def main() -> int:
    args = build_parser().parse_args()

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

    if args.show_help is not None:
        if path or args.fields or args.save_file:
            print("--show-help opens only the help, so --load-file, --field and "
                  "--save-file do not apply", file=sys.stderr)
            return 1
        problem = check_topic(args.show_help)
        if problem:
            print(problem, file=sys.stderr)
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
        if args.show_help is not None:
            return show_help(wanted, args.show_help)
        return run(wanted, path, fields)

    # Nothing asked for, so use what is here. GTK is the default because it is
    # the frontend this project shipped first; Qt takes over only when GTK is
    # not installed.
    for frontend, module in (('gtk', 'gi'), ('qt', 'PySide2')):
        if _have(module):
            if args.show_help is not None:
                return show_help(frontend, args.show_help)
            return run(frontend, path, fields)
    print("No supported GUI toolkit found. Install one of:\n"
          f"  Qt:  {QT_INSTALL}\n"
          f"  GTK: {GTK_INSTALL}", file=sys.stderr)
    return 1


if __name__ == '__main__':
    sys.exit(main())
