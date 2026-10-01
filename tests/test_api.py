"""The Python API: zplcore.Label."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import _isolate  # noqa: F401

from zplcore import Label, parser, renderer

failures = 0


def check(name, cond):
    global failures
    print(('ok   ' if cond else 'FAIL ') + name)
    if not cond:
        failures += 1


def raises(exc, fn, *args, **kw):
    try:
        fn(*args, **kw)
    except exc:
        return True
    except Exception:
        return False
    return False


import linuxzpl
check("no GUI toolkit was imported",
      not any(m in sys.modules for m in ('gi', 'PySide2', 'tkinter')))
check("linuxzpl re-exports Label", linuxzpl.Label is Label)

TEMPLATE = ("^XA^DFFMT^FS\n^FO10,10^A0N,30,30^FN1\"name\"^FS\n"
            "^FO10,60^A0N,30,30^FN2^FS\n^FO10,110^A0N,30,30^FDFixed^FS\n"
            "^FO10,160^A0N,30,30^FD0001^SN0001,1,Y^FS\n^XZ")
label = Label.from_zpl(TEMPLATE)

check("field_numbers", label.field_numbers == [1, 2])

label[1] = "Hello"
label.fill({2: "World"})
zpl = label.to_zpl()
check("^FN data is written as ^FD", '^FDHello' in zpl and '^FDWorld' in zpl)
check("no ^FN or ^DF is left", '^FN' not in zpl and '^DF' not in zpl)

label.fill(f1="Again")
check("keyword fields: f1", '^FDAgain' in label.to_zpl())
label[1] = 42
check("a non-str is written as text", '^FD42' in label.to_zpl())

other = Label.from_zpl(TEMPLATE)
other[1] = "Other"
check("fill twice does not leak", 'Hello' not in other.to_zpl()
      and '^FN1' in Label.from_zpl(TEMPLATE).to_zpl(template=True))
check("the template is untouched by an output",
      label.to_zpl() == label.to_zpl() and label.field_numbers == [1, 2])

tpl = Label.from_zpl(TEMPLATE)
tpl.set_prompt(1, "who")
out = tpl.to_zpl(template=True)
check("template=True keeps ^FN and ^DF and writes prompts",
      '^FN1"who"' in out and '^DFFMT' in out)

label.copies = 5
check("copies writes ^PQ5", '^PQ5' in label.to_zpl())

label.set_data("Fixed", "Changed")
check("set_data by text", '^FDChanged' in label.to_zpl() and 'Fixed' not in label.to_zpl())
label.set_data(2, "ByIndex")
check("set_data by index", '^FDByIndex' in label.to_zpl())

label.serial(start=100, increment=2, leading_zeros=False)
check("serial writes ^SN", '^SN100,2,N' in label.to_zpl())

check("bad field number", raises(ValueError, label.__setitem__, 0, 'x')
      and raises(ValueError, label.__setitem__, 10000, 'x')
      and raises(ValueError, label.__setitem__, '1', 'x'))
check("None data is refused", raises(ValueError, label.__setitem__, 1, None))
check("bad copies", raises(ValueError, setattr, label, 'copies', 0))
check("set_data finds nothing", raises(KeyError, label.set_data, 'nope', 'x'))
check("set_data on an element with no data",
      raises(IndexError, label.set_data, 99, 'x'))
check("serial on a template with none",
      raises(KeyError, Label.from_zpl("^XA^FO1,1^FDx^FS^XZ").serial, 1))
check("a missing file is OSError", raises(OSError, Label.load, '/nonexistent/l.zpl'))

# round trip, and agreement with the command line
again, _ = parser.parse_zpl(zpl, renderer.ZPLRenderer())
check("the output parses back", len(again.elements) == 4)

with tempfile.TemporaryDirectory() as d:
    src = os.path.join(d, 't.zpl')
    open(src, 'w').write(TEMPLATE)
    cli_out = os.path.join(d, 'cli.zpl')
    sys.argv = ['linuxzpl.py', '--load-file', src, '--field', '1=Hello',
                '--field', '2=World', '--save-file', cli_out]
    check("the CLI succeeds", linuxzpl.main() == 0)
    api = Label.load(src)
    api.fill({1: 'Hello', 2: 'World'})
    check("API output equals --save-file's", api.to_zpl() == open(cli_out).read())
    written = api.save(os.path.join(d, 'api'))
    check("save adds .zpl", written.endswith('api.zpl') and os.path.exists(written))

    image = api.render()
    check("render gives an image of the label's size",
          image.size == (api._template.label_width, api._template.label_height)
          and image.getbbox() is not None)
    png = os.path.join(d, 'l.png')
    api.save_image(png)
    check("save_image writes a file", os.path.getsize(png) > 0)

print(f"\n{failures} failure(s)" if failures else "\nall passed")
sys.exit(1 if failures else 0)
