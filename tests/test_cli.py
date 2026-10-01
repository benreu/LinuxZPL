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

import tempfile
sys.argv = ['linuxzpl.py', '--save-file', 'x.zpl']
check("--save-file without a template is refused", linuxzpl.main() == 1)

with tempfile.TemporaryDirectory() as tmp:
    src, out = os.path.join(tmp, 't.zpl'), os.path.join(tmp, 'out.zpl')
    with open(src, 'w') as f:
        f.write('^XA^DFR:T.ZPL^FS^FO10,10^A0N,30,30^FN1"name"^FS'
                '^FO10,60^A0N,30,30^FN2"other"^FS^XZ')
    sys.argv = ['linuxzpl.py', '--load-file', src, '--field', '1=old',
                '--field', '1=Hello', '--save-file', out]
    check("--save-file succeeds", linuxzpl.main() == 0)
    zpl = open(out).read()
    check("the data is written as ^FD", '^FDHello' in zpl)
    check("no ^FN or ^DF is left", '^FN' not in zpl and '^DF' not in zpl)
    check("an unfilled field is not invented", zpl.count('^FD') == 2
          and '^FD^FS' in zpl)
    again, _ = parser.parse_zpl(zpl, renderer.ZPLRenderer())
    check("the output reads back with the text",
          again.display_text(again.elements[0]) == 'Hello')
    sys.argv = ['linuxzpl.py', '--load-file', src, '--save-file',
                os.path.join(tmp, 'nodir', 'o.zpl')]
    check("an unwritable output is refused", linuxzpl.main() == 1)
    sys.argv = ['linuxzpl.py', '--load-file', src, '--gtk', '--save-file', out]
    check("--save-file with a frontend flag is refused", linuxzpl.main() == 1)

sys.exit(1 if failures else 0)
