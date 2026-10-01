"""Command-line handling in linuxzpl.py."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import _isolate  # noqa: F401

import linuxzpl

failures = 0


def check(name, cond):
    global failures
    print(('ok   ' if cond else 'FAIL ') + name)
    if not cond:
        failures += 1


sys.argv = ['linuxzpl.py', '--load-file', '/nonexistent/label.zpl']
check("a missing template is refused with status 1", linuxzpl.main() == 1)

sys.exit(1 if failures else 0)
