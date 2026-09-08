#!/usr/bin/env python3
"""
LinuxZPL - a visual designer for Zebra thermal labels.

Two frontends sit on one core: GTK3 and PySide2/Qt5. They answer to the same
FUNCTIONAL_SPEC.md and produce the same ZPL; which one opens is only a question
of which toolkit is installed, or which you prefer.
"""

import argparse
import sys

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


def run(frontend: str) -> int:
    if frontend == 'qt':
        from qtui import main
    else:
        from gtkui import main
    return main() or 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.strip().split('\n')[0])
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--qt', dest='frontend', action='store_const', const='qt',
                       help='use the PySide2/Qt5 frontend')
    group.add_argument('--gtk', dest='frontend', action='store_const', const='gtk',
                       help='use the GTK3 frontend')
    args = parser.parse_args()

    if args.frontend:
        wanted = args.frontend
        module = 'PySide2' if wanted == 'qt' else 'gi'
        if not _have(module):
            install = QT_INSTALL if wanted == 'qt' else GTK_INSTALL
            print(f"The {wanted} frontend needs {module}, which is not installed.\n"
                  f"  install: {install}", file=sys.stderr)
            return 1
        return run(wanted)

    # Nothing asked for, so use what is here - preferring Qt when both are.
    if _have('PySide2'):
        return run('qt')
    if _have('gi'):
        return run('gtk')
    print("No supported GUI toolkit found. Install one of:\n"
          f"  Qt:  {QT_INSTALL}\n"
          f"  GTK: {GTK_INSTALL}", file=sys.stderr)
    return 1


if __name__ == '__main__':
    sys.exit(main())
