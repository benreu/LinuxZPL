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

sys.argv = ['linuxzpl.py', '--field', '1=x']
check("--field without a template is refused", linuxzpl.main() == 1)

for bad in ('nonumber', 'x=1', '0=a', '10000=a'):
    sys.argv = ['linuxzpl.py', '--load-file', __file__, '--field', bad]
    check(f"malformed --field {bad!r} is refused", linuxzpl.main() == 1)

check("data keeps a later '='",
      linuxzpl.workflow.parse_field_args(['2=a=b']) == [(2, 'a=b')])

from zplcore import parser, renderer
content = '^XA^DFR:T.ZPL^FS^FO10,10^A0N,30,30^FN1"name"^FS^XZ'
document, _ = parser.parse_zpl(content, renderer.ZPLRenderer())
linuxzpl.workflow.apply_field_data(document, [(1, 'old'), (1, 'Hello')])
check("applied data reaches the field's display text",
      document.display_text(document.elements[0]) == 'Hello')

sys.exit(1 if failures else 0)
