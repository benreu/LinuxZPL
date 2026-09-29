import base64, math, os, re, sys, tempfile, zlib
from pathlib import Path
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import _isolate  # a throwaway settings file, before any frontend is imported

from PySide2.QtWidgets import QApplication
from PySide2.QtGui import QImage, QPainter
from PySide2.QtCore import Qt, QPoint, QEvent
from PySide2.QtGui import QMouseEvent

from zplcore import (fonts as zpl_fonts, geometry, parser as zpl_parser,
                     textraster, transforms as zpl_transforms, workflow)
from zplcore import model as zpl_model
from zplcore.model import (Document, TextElement, BarcodeElement, FrameElement,
                           EllipseElement, GraphicSymbolElement, ImageElement)
from zplcore import graphic_symbols
from zplcore.renderer import ZPLRenderer

app = QApplication([])
from qtui import canvas as qt_canvas, window as qt_main, dialogs as qt_dialogs

# A headless run must never reach a modal dialog, so the two these checks would
# otherwise trip are stubbed and their messages recorded.
errors = []
qt_dialogs.show_error = lambda parent, message: errors.append(str(message))

fails = []
def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + ((" -- " + str(extra)) if extra else ""))
    if not cond: fails.append(name)

FONT = zpl_fonts.file_for_family('DejaVu Sans') or '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
# Font 0's stand-in, Nimbus Sans Narrow Bold, or None where it is not
# installed. Setting fonts._resident_cache['0'] to None is how a check sees
# font 0 as such a machine does.
STANDIN = zpl_fonts.resident_face('0')

# --- derived text width -----------------------------------------------------
narrow = TextElement(0, 0, 'IIII'); narrow.font_path = FONT
wide   = TextElement(0, 0, 'WWWW'); wide.font_path = FONT
check("proportional text: IIII narrower than WWWW",
      narrow.printed_width() < wide.printed_width(),
      f"{narrow.printed_width()} vs {wide.printed_width()}")

builtin = TextElement(0, 0, 'IIII')   # no font -> ^AF, a bitmap font
check("built-in font F is a row of its cells with its gap between them",
      builtin.printed_width() == 4 * 26 + 3 * 6, builtin.printed_width())
scalable = TextElement(0, 0, 'IIII', font_code='0')   # ^A0, no font file
if STANDIN:
    check("the scalable font 0 with no font file is measured in its stand-in",
          scalable.printed_width() == round(textraster.measurer(
              STANDIN, 26, 26)[0]('IIII')) < 4 * scalable.font_width,
          scalable.printed_width())
else:
    print("SKIPPED: font 0's stand-in is not installed "
          "(apt install fonts-urw-base35) - its widths are not checked")
zpl_fonts._resident_cache['0'] = None
check("and without the stand-in, len*font_width, as it always was",
      scalable.printed_width() == 4 * scalable.font_width,
      scalable.printed_width())
zpl_fonts._resident_cache['0'] = STANDIN

# font_width_for is the inverse of printed_width
t = TextElement(0, 0, 'Hello World'); t.font_path = FONT
t.font_width = t.font_width_for(400)
check("font_width_for inverts printed_width (within 1 dot)",
      abs(t.printed_width() - 400) <= 2, t.printed_width())

# --- barcode width ----------------------------------------------------------
b = BarcodeElement(0, 0, barcode_value='ABC123', module_width=3)
check("barcode width = (35 + len*11) * module_width",
      b.printed_width() == (35 + 6 * 11) * 3, b.printed_width())

# --- ^GFA encoding ----------------------------------------------------------
from PIL import Image, ImageOps
src = Image.new('L', (40, 12), 255)
for x in range(40): src.putpixel((x, 0), 0)          # a black top row
img_el = ImageElement(0, 0, 13, 4, _pil_image=src.convert('RGB'))
zpl = img_el.to_zpl()
m = re.search(r'\^GFA,(\d+),(\d+),(\d+),([0-9A-F]+)', zpl)
bpr = int(m.group(3))
check("bytes_per_row = ceil(width/8)", bpr == math.ceil(13/8), bpr)
check("total bytes = bpr * height", int(m.group(1)) == bpr * 4, m.group(1))
check("hex length = 2 * total bytes", len(m.group(4)) == 2 * bpr * 4, len(m.group(4)))
# a set bit is black: an all-black element must encode as FF..., padding bits 0
black = ImageElement(0, 0, 13, 2, _pil_image=Image.new('RGB', (13, 2), (0, 0, 0)))
hexdata = re.search(r'\^GFA,\d+,\d+,\d+,([0-9A-F]+)', black.to_zpl()).group(1)
row0 = bytes.fromhex(hexdata)[:bpr]
check("a set bit is black (all-black row -> 0xFF)", row0[0] == 0xFF, hex(row0[0]))
check("padding bits are white/0 (13 cols -> last byte 0xF8)", row0[1] == 0xF8, hex(row0[1]))

def _font_written(document):
    """The ^A command `document` writes for its first text element."""
    return next((w for w in document.to_zpl().split() if w.startswith('^A')),
                None)


# --- printer object naming --------------------------------------------------
check("unsafe chars stripped, truncated to 8",
      zpl_fonts.printer_font_name('/x/Catrina Demo.ttf') == 'CATRINAD',
      zpl_fonts.printer_font_name('/x/Catrina Demo.ttf'))
taken = {'DEJAVUSA'}
second = zpl_fonts.printer_font_name('/x/DejaVuSans-Bold.ttf', taken=taken)
check("collision gets a numeric suffix", second != 'DEJAVUSA' and len(second) <= 8, second)
check("empty result becomes FONT", zpl_fonts.printer_font_name('/x/...ttf') == 'FONT',
      zpl_fonts.printer_font_name('/x/...ttf'))

# --- the line under a printer manager's list ---------------------------------
# One function builds it for both frontends (workflow.listing_status), so the
# two cannot word this differently, and a drive that gave nothing is named
# whether it was empty or unreadable.
_LS_DEVICES = ('R', 'E', 'B', 'A')
check("listing_status(): just the count when every drive gave something",
      workflow.listing_status('10.0.0.1', 'object', _LS_DEVICES,
                              ['R:A.GRF', 'E:B.GRF', 'B:C.GRF', 'A:D.GRF'], [])
      == '4 object(s) on 10.0.0.1',
      workflow.listing_status('10.0.0.1', 'object', _LS_DEVICES,
                              ['R:A.GRF', 'E:B.GRF', 'B:C.GRF', 'A:D.GRF'], []))
check("listing_status(): empty drives are named",
      workflow.listing_status('10.0.0.1', 'object', _LS_DEVICES,
                              ['E:B.GRF'], [])
      == '1 object(s) on 10.0.0.1 \u2014 nothing on R:, B:, A:',
      workflow.listing_status('10.0.0.1', 'object', _LS_DEVICES, ['E:B.GRF'], []))
check("listing_status(): an unreadable drive is worded apart from an empty one",
      workflow.listing_status('10.0.0.1', 'object', _LS_DEVICES,
                              ['E:B.GRF'], ['A'])
      == '1 object(s) on 10.0.0.1 \u2014 nothing on R:, B:; A: could not be read',
      workflow.listing_status('10.0.0.1', 'object', _LS_DEVICES, ['E:B.GRF'], ['A']))
# With nothing found at all the count sentence has already said every drive
# gave nothing; repeating it as a list reads as an error rather than an empty
# printer, so only the unreadable drives are worth adding.
check("listing_status(): no empties clause when nothing was found at all",
      workflow.listing_status('10.0.0.1', 'object', _LS_DEVICES, [], ['A'])
      == 'No objects on 10.0.0.1 \u2014 A: could not be read',
      workflow.listing_status('10.0.0.1', 'object', _LS_DEVICES, [], ['A']))
check("listing_status(): and nothing appended when a printer is simply empty",
      workflow.listing_status('10.0.0.1', 'font', _LS_DEVICES, [], [])
      == 'No fonts on 10.0.0.1',
      workflow.listing_status('10.0.0.1', 'font', _LS_DEVICES, [], []))
check("listing_status(): drives are named in the order they were asked",
      workflow.listing_status('10.0.0.1', 'object', _LS_DEVICES, ['E:B.GRF'],
                              ['A', 'R']).endswith('R:, A: could not be read'),
      workflow.listing_status('10.0.0.1', 'object', _LS_DEVICES, ['E:B.GRF'],
                              ['A', 'R']))

# --- which memory a font is written to ---------------------------------------
# ~DYd:f,... and ^A@o,h,w,d:f.x both take a drive, and this app used to spell
# E: into every one of them. The drive is a parameter now, defaulting to E: so
# nothing moves for a caller that does not care.
check("printer_font_path(): E: unless another drive is asked for",
      (zpl_fonts.printer_font_path('ANI'),
       zpl_fonts.printer_font_path('ANI', 'R')) == ('E:ANI.TTF', 'R:ANI.TTF'),
      (zpl_fonts.printer_font_path('ANI'), zpl_fonts.printer_font_path('ANI', 'R')))
check("split_font_spec(): drive and name back apart, defaulting to E: not R:",
      [zpl_fonts.split_font_spec(x) for x in
       ('E:ANI.TTF', 'B:CYRI_UB.FNT', 'ANI.TTF')]
      == [('E', 'ANI'), ('B', 'CYRI_UB'), ('E', 'ANI')],
      [zpl_fonts.split_font_spec(x) for x in ('E:ANI.TTF', 'B:CYRI_UB.FNT', 'ANI.TTF')])

_upl = {d: zpl_fonts.build_font_upload('/etc/hostname', 'ANI', d)
        for d in zpl_fonts.DEVICES}
check("build_font_upload(): the ~DY header names the drive it was given",
      all(v.startswith(f"~DY{d}:ANI,".encode()) for d, v in _upl.items()),
      [v[:14] for v in _upl.values()])
check("build_font_upload(): defaults to E: when no drive is given",
      zpl_fonts.build_font_upload('/etc/hostname', 'ANI').startswith(b'~DYE:ANI,'),
      zpl_fonts.build_font_upload('/etc/hostname', 'ANI')[:14])
# The b and x parameters stay as they were: real hardware accepts A,TT, and
# the manual's own B,T is a question for a printer rather than for a reading.
check("build_font_upload(): the A,TT format parameters are left alone",
      b',A,TT,' in zpl_fonts.build_font_upload('/etc/hostname', 'ANI'),
      zpl_fonts.build_font_upload('/etc/hostname', 'ANI')[:28])

_font_sent = []
from zplcore import printer_io as _font_pio
_font_real_send = _font_pio.send
_font_pio.send = lambda a, p, payload, t, read_reply=False, cancel=None: (
    _font_sent.append(payload) or b'\r\n*ANI.TTF  1024\r\n')
try:
    zpl_fonts.delete_printer_font('h', 1, 'ANI', 'B')
    _listed = zpl_fonts.query_printer_fonts('h', 1)
finally:
    _font_pio.send = _font_real_send
check("delete_printer_font(): ^ID names the drive, not an assumed E:",
      _font_sent[0] == b'^XA^IDB:ANI.TTF^FS^XZ', _font_sent[0])
check("query_printer_fonts(): one ^HW per drive, in DEVICES order",
      _font_sent[1:] == [f'^XA^HW{d}:*.TTF^XZ'.encode()
                         for d in zpl_fonts.DEVICES], _font_sent[1:])
check("query_printer_fonts(): answers with specs, so the drive is not lost",
      _listed[0] == {f'{d}:ANI.TTF' for d in zpl_fonts.DEVICES}, _listed)
check("query_printer_fonts(): no drive is unreadable when every one answers",
      _listed[1] == [], _listed[1])

# A real ^HW reply, captured from a printer rather than composed here: its
# entries carry the drive themselves ("* E:ANI.TTF"), which the manual's own
# format for the command does not show, and one name runs to the full eight
# characters with a trailing underscore.
_REAL_HW = (b"\r\n- DIR E:*.TTF \r\n"
            b"* E:ABYSSINI.TTF    312804          \r\n"
            b"* E:ANI.TTF    120404          \r\n"
            b"* E:TT0003M_.TTF    169188  P       \r\n"
            b"\r\n-  66119680 bytes free E: ONBOARD FLASH \r\n")
_font_real_send = _font_pio.send
_font_pio.send = lambda a, p, payload, t, read_reply=False, cancel=None: (
    _REAL_HW if b'^HWE:' in payload else b'')
try:
    _real_listed = zpl_fonts.query_printer_fonts('h', 1)
    _font_pio.send = lambda a, p, payload, t, read_reply=False, cancel=None: b''
    _silent = zpl_fonts.query_printer_fonts('h', 1)
    _font_pio.send = lambda a, p, payload, t, read_reply=False, cancel=None: (
        b'\r\n- DIR R:*.TTF \r\n\r\n-  1000 bytes free\r\n')
    _empty = zpl_fonts.query_printer_fonts('h', 1)
finally:
    _font_pio.send = _font_real_send
check("query_printer_fonts(): a real ^HW reply parses to its fonts and nothing else",
      _real_listed[0] == {'E:ABYSSINI.TTF', 'E:ANI.TTF', 'E:TT0003M_.TTF'},
      _real_listed)
# Reachability cannot be judged on the first device alone once every device is
# asked: R: comes first, and a printer with nothing on R: is not unreachable.
check("query_printer_fonts(): silence on R: is not unreachable when E: answers",
      _real_listed is not None and len(_real_listed[0]) == 3, _real_listed)
check("query_printer_fonts(): and the silent drives come back named",
      _real_listed[1] == ['R', 'B', 'A'], _real_listed[1])
check("query_printer_fonts(): None only when no device answered at all",
      _silent is None, _silent)
check("query_printer_fonts(): a drive that lists nothing is an empty set, not None",
      _empty[0] == set(), _empty)

# Document.font_device is where a font this app assigns goes; an element that
# carries a path of its own - anything loaded from a file - overrides it.
_fd = Document(400, 400)
_fd.set_font('/x/NewFace.ttf', 'New Face', 'NEWFACE')
_fd.add_text_element('x')
check("font_sources(): specs at the document's own drive, E: by default",
      _fd.font_sources() == {'E:NEWFACE.TTF': '/x/NewFace.ttf'}, _fd.font_sources())
_fd.font_device = 'R'
check("font_sources(): and it follows the Font memory setting when that moves",
      _fd.font_sources() == {'R:NEWFACE.TTF': '/x/NewFace.ttf'}, _fd.font_sources())
check("to_zpl(): the ^A@ follows it too",
      '^A@N,26,26,R:NEWFACE.TTF' in _fd.to_zpl(), _font_written(_fd))
# A loaded element carries a font name *and* a path of its own, and that
# path wins over the setting - which is what keeps a file saying what it said.
_loaded = zpl_parser.parse_zpl(
    '^XA^FO50,50^A@N,53,19,B:CYRI_UB.FNT^FDx^FS^XZ')[0]
_loaded.font_device = 'R'
check("a loaded element's own path wins over the setting, in both",
      _loaded.font_sources() == {'B:CYRI_UB.FNT': None}
      and '^A@N,53,19,B:CYRI_UB.FNT' in _loaded.to_zpl(),
      (_loaded.font_sources(), _font_written(_loaded)))
# ...but an element with no font of its own still follows the document, which
# is why to_zpl only trusts a path when the element names the font too.
check("an element with no font of its own follows the document's drive",
      '^A@N,26,26,R:NEWFACE.TTF' in _fd.to_zpl(), _font_written(_fd))

# --- ^A@'s own d:f.x path ----------------------------------------------------
# ^A@o,h,w,d:f.x names a drive (R:/E:/B:/A:, defaulting to R: - not E:) and an
# extension (.FNT, .TTF or .TTE). Only the font name was ever read back, and
# E:/.TTF was assumed on the way out, so a font anywhere but E: - or in any
# format but TrueType - was rewritten into a different object and then saved
# that way. The path is carried as the file wrote it now, case included.
def _font_roundtrip(command):
    """The ^A@ that `command` comes back as, after a load and a save."""
    _doc = zpl_parser.parse_zpl('^XA^FO50,50' + command + '^FDx^FS^XZ')[0]
    _written = [w for w in _doc.to_zpl().split() if w.startswith('^A')]
    return _written[0] if _written else None

for _path, _why in (('R:MYFONT.TTF', "the manual's own default drive"),
                    ('B:CYRI_UB.FNT', "a .FNT, as the manual's own example"),
                    ('A:MYFONT.TTE', 'a .TTE'),
                    ('MYFONT.TTF', 'no drive named at all'),
                    ('E:myfont.ttf', 'lower case')):
    _cmd = '^A@N,53,19,' + _path
    check(f"^A@ carries {_why} through a save: {_path}",
          _font_roundtrip(_cmd) == _cmd, _font_roundtrip(_cmd))

check("^A@ naming no path at all is still written back naming none",
      _font_roundtrip('^A@N,53,19') == '^A@N,53,19',
      _font_roundtrip('^A@N,53,19'))

# The name stays the lookup key - drive and extension off, upper case - since
# the renderer's font registry, file_for_printer_name and the collision set
# are all keyed on it. Only the written path is verbatim.
_named = {p: zpl_parser.parse_zpl(
              '^XA^FO50,50^A@N,53,19,' + p + '^FDx^FS^XZ')[0]
          .elements[0].printer_font_name
          for p in ('E:MYFONT.TTF', 'R:MYFONT.TTF', 'A:MYFONT.TTE',
                    'MYFONT.TTF', 'E:myfont.ttf')}
check("every path shape still resolves to the same upper-case lookup name",
      set(_named.values()) == {'MYFONT'}, _named)

# A font this app assigns is one it uploads, so it is written at E: as a .TTF
# whatever the element was loaded naming - otherwise the old path would be
# written against the new name.
_reassign = zpl_parser.parse_zpl(
    '^XA^FO50,50^A@N,53,19,R:MYFONT.TTF^FDx^FS^XZ')[0]
_reassign.set_element_font(_reassign.elements[0], '/x/NewFace.ttf',
                           'New Face', 'NEWFACE')
check("reassigning an element's font writes it at E:, not the loaded path",
      _font_written(_reassign) == '^A@N,53,19,E:NEWFACE.TTF',
      _font_written(_reassign))

# And a document-wide font, which this app also uploads, is unaffected by any
# path an element carried.
_designed = Document(400, 400)
_designed.set_font('/x/NewFace.ttf', 'New Face', 'NEWFACE')
_designed.add_text_element('designed here')
check("a font assigned in the designer is still written at E: as a .TTF",
      (_font_written(_designed) or '').endswith(',E:NEWFACE.TTF'),
      _font_written(_designed))

# --- hidden elements --------------------------------------------------------
doc = Document(400, 400)
keep = doc.add_text_element('visible')
hide = doc.add_text_element('hidden')
hide.print_enabled = False
doc.add_frame_element()
out = doc.to_zpl()
payload = re.search(r'\^FXDESIGNER_NOPRINT:(\S+)', out).group(1)
check("hidden payload is base64 with no caret", '^' not in payload)
check("hidden text does not appear as a live field", '^FDhidden^FS' not in out)
check("hidden text is inside the payload",
      '^FDhidden^FS' in base64.b64decode(payload).decode())
back, _ = zpl_parser.parse_zpl(out)
check("hidden element survives round trip",
      len(back.elements) == 3 and back.elements[1].print_enabled is False
      and back.elements[1].text == 'hidden'
      and back.elements[0].print_enabled and back.elements[2].element_type == 'frame',
      [(e.element_type, e.print_enabled) for e in back.elements])

# --- new elements land on the label even when it's too small for their
#     default offset, rather than off the edge where nothing can reach them
small = Document(60, 60)
added = [small.add_text_element('hi'), small.add_frame_element(),
         small.add_barcode_element(), small.add_image_element('unused.png')]
on_label = [(0 <= el.x and 0 <= el.y
             and el.x + el.width <= small.label_width
             and el.y + el.height <= small.label_height) for el in added]
check("a new element on a small label stays within its bounds",
      all(on_label), [(el.x, el.y, el.width, el.height) for el in added])

# the barcode's default y (250) overshoots a label this short; it should be
# moved up to fit at full size, not trimmed down to a sliver at the bottom
natural_barcode_height = BarcodeElement(50, 250).height
roomy = Document(400, 300)
placed_barcode = roomy.add_barcode_element()
check("a barcode that overshoots a short label is repositioned, not trimmed",
      placed_barcode.height == natural_barcode_height
      and placed_barcode.y + placed_barcode.height == roomy.label_height,
      (placed_barcode.y, placed_barcode.height, natural_barcode_height))

# --- full round trip against the reference sample ---------------------------
orig = open(str(Path(__file__).resolve().parent / 'fixtures' / 'sample_300dpi.zpl')).read()
d1, dpi1 = zpl_parser.parse_zpl(orig); d1.dpi = dpi1
once = d1.to_zpl()
d2, dpi2 = zpl_parser.parse_zpl(once); d2.dpi = dpi2
twice = d2.to_zpl()
check("sample.zpl round trip is stable", once == twice)
check("sample dpi preserved", dpi1 == 300 and dpi2 == 300, (dpi1, dpi2))
check("GFA bytes identical to the GTK app's output",
      re.search(r'\^GFA,\d+,\d+,\d+,([0-9A-F]+)', orig).group(1) ==
      re.search(r'\^GFA,\d+,\d+,\d+,([0-9A-F]+)', once).group(1))

# --- empty document ---------------------------------------------------------
check("blank document refuses to save", Document().is_empty())
check("document with an element is savable", not d1.is_empty())

# --- dpi rescale ------------------------------------------------------------
r = Document(812, 1218, dpi=203)
rt = r.add_text_element('scale me'); rt.font_path = FONT; r.sync_text_width(rt)
rb = r.add_barcode_element()
rf = r.add_frame_element()
before_in = r.label_width / 203
r.rescale(300 / 203)
check("rescale keeps physical width", abs(r.label_width / 300 - before_in) < 0.02,
      f"{r.label_width} dots")
check("barcode module width rounds to a whole dot", rb.module_width == 3, rb.module_width)
check("barcode width follows its module width", rb.width == rb.printed_width())
check("frame thickness scaled", rf.thickness == 3, rf.thickness)
check("text width recomputed from metrics", rt.width == rt.printed_width(r.font_path))

# --- undo history -----------------------------------------------------------
w = qt_main.ZPLDesignerWindow()
w.resize(900, 1000)
w.on_add_text(); w.on_add_frame(); w.on_add_barcode()
check("three adds -> three undo entries", len(w._undo_stack) == 3, len(w._undo_stack))
w.on_undo(); w.on_undo()
check("undo removes elements", len(w.document.elements) == 1, len(w.document.elements))
w.on_redo()
check("redo restores", len(w.document.elements) == 2, len(w.document.elements))
w.on_add_frame()
check("new action discards the redo branch", not w._redo_stack)
for i in range(60): w.on_add_text()
check("history capped at 50", len(w._undo_stack) == 50, len(w._undo_stack))

# z-order
w.unsaved_changes = False; w.on_new()
a = w.document.add_text_element('a'); bE = w.document.add_frame_element()
w.document.selected_element = a
check("can_raise for bottom element", w.document.can_raise() and not w.document.can_lower())
w.on_bring_to_front()
check("bring to front reorders", w.document.elements[-1] is a)
check("undo entry recorded for reorder", len(w._undo_stack) >= 1)

# --- selection and alignment ------------------------------------------------
def pair(label=(400, 400), first=(100, 100, 60, 40), second=(300, 250, 80, 20)):
    """A document holding two frames at known boxes, both selected."""
    d = Document(*label)
    made = []
    for x, y, wd, ht in (first, second):
        el = FrameElement(x, y, wd, ht)
        d.elements.append(el)
        made.append(el)
    d.select_many(made)
    return (d, *made)

d, one, two = pair()
check("a selection holds more than one element", len(d.selection) == 2)
check("the primary is the last one picked", d.selected_element is two)
check("selection bounds cover the pair",
      geometry.selection_bounds([one, two]) == (100, 100, 280, 170),
      geometry.selection_bounds([one, two]))
check("bounds of nothing is None, not a box at the origin",
      geometry.selection_bounds([]) is None)

d, one, two = pair()
check("align left takes both to the leftmost edge of the pair",
      d.align_selected('left') and (one.x, two.x) == (100, 100), (one.x, two.x))
check("and leaves the other axis where it was", (one.y, two.y) == (100, 250))
check("aligning what is already aligned moves nothing, so nothing is undone",
      not d.align_selected('left'))

d, one, two = pair()
d.align_selected('right')
check("align right takes both to the rightmost edge",
      (one.x + one.width, two.x + two.width) == (380, 380), (one.x, two.x))
d, one, two = pair()
d.align_selected('center')
check("centre horizontally puts both centres on the pair's centre",
      (one.x + one.width // 2, two.x + two.width // 2) == (240, 240),
      (one.x, two.x))
d, one, two = pair()
d.align_selected('top')
check("align top takes both to the topmost edge", (one.y, two.y) == (100, 100))
d, one, two = pair()
d.align_selected('bottom')
check("align bottom takes both to the bottom edge",
      (one.y + one.height, two.y + two.height) == (270, 270), (one.y, two.y))
d, one, two = pair()
d.align_selected('middle')
check("centre vertically puts both centres on the pair's centre",
      (one.y + one.height // 2, two.y + two.height // 2) == (185, 185),
      (one.y, two.y))

# one element has nothing to line up with but the label
d, one, two = pair()
d.selected_element = one
d.align_selected('right'); d.align_selected('bottom')
check("a single selection aligns to the label, not to itself",
      (one.x + one.width, one.y + one.height) == (400, 400), (one.x, one.y))
d.align_selected('center'); d.align_selected('middle')
check("and centres on the label", (one.x, one.y) == (170, 180), (one.x, one.y))
check("the other element stayed out of it", (two.x, two.y) == (300, 250))

d, one, two = pair(first=(50, 50, 600, 40))
d.selected_element = one
d.align_selected('right')
check("an element wider than the label lands at 0, not at a negative x",
      one.x == 0, one.x)
check("an unknown edge is refused rather than guessed at",
      not d.align_selected('sideways'))

# a group drags as one box, clamped as one box
d, one, two = pair()
geometry.move_selection(d, d.selection, -9999, -9999)
check("a group drag keeps the members' relative offsets",
      (two.x - one.x, two.y - one.y) == (200, 150), (one.x, one.y, two.x, two.y))
check("and stops with the whole group inside the label",
      (one.x, one.y) == (0, 0), (one.x, one.y))
geometry.move_selection(d, d.selection, 9999, 9999)
check("the far edge clamps the group too",
      (two.x + two.width, two.y + two.height) == (400, 400), (two.x, two.y))

# the rubber band's hit rule
d, one, two = pair()
caught = geometry.elements_in_box(d.elements, 90, 90, 170, 170)
check("a band catches what it overlaps", caught == [one], len(caught))
check("a band drawn in reverse catches the same",
      geometry.elements_in_box(d.elements, 170, 170, 90, 90) == [one])
check("a band of no area catches nothing",
      geometry.elements_in_box(d.elements, 90, 90, 90, 90) == [])
check("a band over both catches both, in z-order",
      geometry.elements_in_box(d.elements, 0, 0, 400, 400) == [one, two])

# the selection through the rest of the document
d, one, two = pair()
d.select(one, additive=True)
check("an additive pick of a selected element drops it", d.selection == [two])
d.select(one, additive=True)
check("and an additive pick of an unselected one adds it", d.selection == [two, one])
d.extend_selection([one, two])
check("an additive band adds without dropping what it passed over",
      d.selection == [two, one], len(d.selection))
d.select(two)
check("a plain pick of a member keeps the group, so it can be dragged",
      sorted(map(id, d.selection)) == sorted(map(id, [one, two])), len(d.selection))
check("and makes it the primary, so a right-click acts on what was pointed at",
      d.selected_element is two)
d.selected_element = one
check("assigning the singular name still replaces the whole selection",
      d.selection == [one])

d, one, two = pair()
snap = d.snapshot()
d.clear_selection()
d.restore(snap)
check("a snapshot brings the whole selection back, as the restored elements",
      len(d.selection) == 2 and all(el in d.elements for el in d.selection),
      len(d.selection))

d, one, two = pair()
check("delete takes the whole selection", d.remove_selected() and not d.elements)
check("and leaves nothing selected", not d.selection)

# --- groups -----------------------------------------------------------------
def quad():
    """Four frames in a column, none selected, so grouping can be tried on
    any subset and the z-order read back by position."""
    d = Document(400, 400)
    made = [FrameElement(20, 20 + i * 90, 60, 40) for i in range(4)]
    d.elements.extend(made)
    return (d, *made)

def order(d):
    return [d.elements.index(el) for el in d.selection]

check("an element belongs to no group until put in one",
      FrameElement(0, 0, 10, 10).group is None)

d, a, b, c, e = quad()
d.select_many([a, c])
check("two loose elements can be grouped", d.can_group())
check("group returns True when it did something", d.group_selected())
check("the members share one group id, one level deep",
      a.group is not None and a.group == c.group and len(a.group) == 1
      and b.group is None and e.group is None, (a.group, c.group))
check("grouping makes the members one run, where the topmost member was",
      d.elements == [b, a, c, e], [d.elements.index(x) for x in (a, b, c, e)])
check("exactly one group selected cannot be grouped again", not d.can_group())
check("but can be ungrouped", d.can_ungroup())
check("a lone element has nothing to group with",
      (d.select(b), not d.can_group())[1])

# every way into the selection widens a member to its group
d.select(a)
check("a plain click on a member selects the group, clicked member primary",
      set(d.selection) == {a, c} and d.selected_element is a, order(d))
d.select(c)
check("a click on another member keeps the group and moves the primary",
      set(d.selection) == {a, c} and d.selected_element is c, order(d))
d.select(c, additive=True)
check("a shift-click on a selected member drops the whole group", not d.selection)
d.select(b)
d.select(a, additive=True)
check("a shift-click on a member adds the whole group, clicked member primary",
      d.selection == [b, c, a], order(d))
d.select_many([c])
check("select_many of one member is the group", set(d.selection) == {a, c})
d.selected_element = a
check("assigning the singular name is the group too", set(d.selection) == {a, c})
d.clear_selection()
d.extend_selection([c])
check("extend_selection of one member is the group", set(d.selection) == {a, c})
caught = geometry.elements_in_box(d.elements, 0, 0, 100, 30)
d.select_many(caught)
check("a rubber band touching one member selects the group",
      caught == [a] and set(d.selection) == {a, c}, (len(caught), order(d)))

# the group is one unit for the z-order commands
d.select(a)
check("a group at the bottom cannot go lower", d.can_lower() and d.can_raise())
d.bring_forward()
check("bring forward moves the run past the next unit", d.elements == [b, e, a, c])
check("and now the group is on top", not d.can_raise())
d.send_to_back()
check("send to back moves the run to the bottom", d.elements == [a, c, b, e])
d.select(b); d.bring_forward()
check("a loose element steps over a group as a whole", d.elements == [a, c, e, b])
d.send_backward()
check("and back again", d.elements == [a, c, b, e])

# nest, ungroup, delete
d.select(a); d.select(e, additive=True)
check("a group plus a loose element can be grouped", d.can_group())
inner = a.group
d.group_selected()
check("grouping wraps the old group inside the new one",
      a.group == c.group == (a.group[0], inner[0]) and e.group == (a.group[0],)
      and b.group is None, [x.group for x in d.elements])
check("the new run is contiguous, where the topmost member was",
      d.elements == [b, a, c, e], [d.elements.index(x) for x in (a, b, c, e)])
check("a click on any member selects the whole nest",
      (d.select(e), set(d.selection) == {a, c, e})[1])
d.select(b); d.select(a, additive=True)
check("ungroup returns True and peels the outermost level only",
      d.ungroup_selected() and a.group == c.group == inner and e.group is None
      and b.group is None, [x.group for x in d.elements])
check("ungroup leaves the selection as it was", set(d.selection) == {a, b, c, e})
check("what was inside is a group of its own now",
      (d.select_many([a]), set(d.selection) == {a, c})[1])
d.select(b); d.select(a, additive=True)
check("a second ungroup clears the rest",
      d.ungroup_selected() and all(x.group is None for x in d.elements))
check("nothing grouped cannot be ungrouped", not d.can_ungroup())
d.select_many([a, c]); d.group_selected(); d.select(a)
check("delete takes the whole group", d.remove_selected() and d.elements == [b, e])

# align treats a group as one rigid box
d, a, b, c, e = quad()
a.x, c.x = 50, 120
d.select_many([a, c]); d.group_selected()
d.select(a); d.select(e, additive=True)
shape = (c.x - a.x, c.y - a.y)
check("align left moves the group as one, keeping its shape",
      d.align_selected('left') and (c.x - a.x, c.y - a.y) == shape and a.x == e.x == 20,
      (a.x, c.x, e.x))
d.select_many([a])
d.align_selected('right')
check("a lone group aligns against the label",
      c.x + c.width == 400 and (c.x - a.x, c.y - a.y) == shape, (a.x, c.x))
check("units_of gives z-order groups and loners",
      [len(u) for u in geometry.units_of(d.elements)] == [1, 2, 1] or
      [len(u) for u in geometry.units_of(d.elements)] == [2, 1, 1],
      [len(u) for u in geometry.units_of(d.elements)])

# undo carries the tag, as a scalar copied with the element
d, a, b, c, e = quad()
snap = d.snapshot()
d.select_many([a, c]); d.group_selected()
d.restore(snap)
check("restoring a snapshot from before the group undoes it",
      all(x.group is None for x in d.elements))
w.unsaved_changes = False; w.on_new()
ga = w.document.add_text_element('ga'); gb = w.document.add_frame_element()
w.document.select_many([ga, gb])
depth = len(w._undo_stack)
w.on_group()
check("Group in the window records an undo entry",
      ga.group is not None and len(w._undo_stack) == depth + 1)
w.on_undo()
check("and undo takes the group away", all(x.group is None for x in w.document.elements))
w.on_redo()
check("and redo brings it back, as one group",
      len({x.group for x in w.document.elements}) == 1
      and w.document.elements[0].group is not None)
w.on_ungroup()
check("Ungroup in the window is undoable too",
      all(x.group is None for x in w.document.elements) and len(w._undo_stack) == depth + 2)

# round trip: markers before each member, numbered 1.. by first appearance,
# a singleton not written, a hidden member still hidden
d = Document(400, 400)
t1 = d.add_text_element('one'); t2 = d.add_text_element('two')
f1 = d.add_frame_element(); f2 = d.add_frame_element(); lone = d.add_text_element('lone')
d.select_many([f1, f2]); d.group_selected()
d.select_many([t1, t2]); d.group_selected()
lone.group = (99,)
t2.print_enabled = False
out = d.to_zpl()
markers = re.findall(r'\^FXDESIGNER_GROUP:([\d,]+)', out)
check("a marker precedes each member, numbered from 1 in file order",
      markers == ['1', '1', '2', '2'], markers)
check("a group of one is not written", '^FXDESIGNER_GROUP:99' not in out)
check("the marker sits ahead of a hidden member's payload line",
      re.search(r'\^FXDESIGNER_GROUP:1\n\^FXDESIGNER_NOPRINT:', out) is not None)
back, _ = zpl_parser.parse_zpl(out)
tags = [(el.group, el.print_enabled) for el in back.elements]
check("groups survive a round trip, and so does the hidden member",
      tags == [((1,), True), ((1,), False), ((2,), True), ((2,), True), (None, True)],
      tags)
check("the round trip is stable", zpl_parser.parse_zpl(out)[0].to_zpl() == out)

# markers ahead of a field the model cannot hold are used up by it
leak = ("^XA^PW400^LL400\n^FXDESIGNER_GROUP:7\n^FXDESIGNER_NOPRINT\n"
        "^FO10,10^B4N,6,200^FDdata^FS\n^FO20,20^A0N,30,30^FDafter^FS\n^XZ")
back, _ = zpl_parser.parse_zpl(leak)
check("neither marker leaks onto the next supported field",
      len(back.elements) == 1 and back.elements[0].group is None
      and back.elements[0].print_enabled, [(e.group, e.print_enabled) for e in back.elements])
check("a marker with no number is ignored",
      zpl_parser.parse_zpl("^XA^FXDESIGNER_GROUP:x\n^FO1,1^GB10,10,1^FS^XZ")[0].elements[0].group is None)

# --- nested groups ----------------------------------------------------------
def nest():
    """Four frames; a and c grouped, then that pair grouped with e. Returns
    the document, the four, and the (outer, inner) ids."""
    d, a, b, c, e = quad()
    d.select_many([a, c]); d.group_selected()
    d.select_many([a, e]); d.group_selected()
    return d, a, b, c, e, a.group

d, a, b, c, e, (outer, inner) = nest()
check("paths run from the outermost group in",
      a.group == c.group == (outer, inner) and e.group == (outer,), [x.group for x in d.elements])
check("a fresh id is above every id at every depth",
      d._fresh_group_id() > max(outer, inner))
check("units go by the outermost group",
      [len(u) for u in geometry.units_of(d.elements)] == [1, 3] or
      [len(u) for u in geometry.units_of(d.elements)] == [3, 1],
      [len(u) for u in geometry.units_of(d.elements)])
check("the nest cannot be grouped on its own", (d.select(a), not d.can_group())[1])
check("the members of a nested pair cannot be sub-grouped in place",
      (d.select(a, direct=True), d.select(c, additive=True, direct=True),
       not d.can_group())[2])

# the nest in the file: outer id first, numbered by first appearance
d, a, b, c, e, (outer, inner) = nest()
c.print_enabled = False
out = d.to_zpl()
markers = re.findall(r'\^FXDESIGNER_GROUP:([\d,]+)', out)
check("a nested member writes its whole path, outermost first",
      markers == ['1,2', '1,2', '1'], markers)
check("the marker of a hidden nested member sits ahead of its payload line",
      re.search(r'\^FXDESIGNER_GROUP:1,2\n\^FXDESIGNER_NOPRINT:', out) is not None)
back, _ = zpl_parser.parse_zpl(out)
check("the nest survives a round trip",
      [x.group for x in back.elements] == [None, (1, 2), (1, 2), (1,)]
      and back.elements[2].print_enabled is False, [x.group for x in back.elements])
check("and the round trip is stable", zpl_parser.parse_zpl(out)[0].to_zpl() == out)
check("a file from before nesting reads as a path of one",
      zpl_parser.parse_zpl("^XA^FXDESIGNER_GROUP:3\n^FO1,1^GB10,10,1^FS^XZ")[0].elements[0].group == (3,))
for bad in ('1,x', '1,,2', ',1'):
    check(f"a marker of {bad!r} is ignored as a whole",
          zpl_parser.parse_zpl(f"^XA^FXDESIGNER_GROUP:{bad}\n^FO1,1^GB10,10,1^FS^XZ")[0].elements[0].group is None)

# a level with one member is not a group: a direct delete dissolves it, and
# one a file hands over is not written
d, a, b, c, e, (outer, inner) = nest()
d.select(c, direct=True); d.remove_selected()
check("deleting one of an inner pair directly dissolves that level",
      a.group == (outer,) and e.group == (outer,), [x.group for x in d.elements])
a.group = (outer, inner)
out = d.to_zpl()
markers = re.findall(r'\^FXDESIGNER_GROUP:([\d,]+)', out)
check("an inner level left with one member is dropped from the path",
      markers == ['1', '1'], markers)
check("and that round trip is stable too", zpl_parser.parse_zpl(out)[0].to_zpl() == out)

# a hand-edited file that puts one id at two depths neither raises nor drifts
odd = ("^XA^PW400^LL400\n^FXDESIGNER_GROUP:1,2\n^FO10,10^GB20,20,1^FS\n"
       "^FXDESIGNER_GROUP:2\n^FO50,50^GB20,20,1^FS\n^XZ")
back, _ = zpl_parser.parse_zpl(odd)
once = back.to_zpl()
check("an inconsistent file loads as written",
      [x.group for x in back.elements] == [(1, 2), (2,)])
check("and saves the same way twice", zpl_parser.parse_zpl(once)[0].to_zpl() == once)

# --- direct picks: Ctrl-click reaches one element inside a group ------------
d, a, b, c, e, (outer, inner) = nest()
d.select(a)
d.select(c, direct=True)
check("a direct pick narrows a selected group to the one element",
      d.selection == [c])
d.select(c)
check("a plain pick of a directly picked element keeps the pick", d.selection == [c])
d.select(a)
check("a plain pick of another member widens back to the group",
      set(d.selection) == {a, c, e} and d.selected_element is a)
d.select(c, additive=True, direct=True)
check("an additive direct pick of a selected member drops exactly it",
      set(d.selection) == {a, e}, order(d))
d.select(c, additive=True, direct=True)
check("and adds exactly it back, as the primary",
      set(d.selection) == {a, c, e} and d.selected_element is c)
d.select(b, direct=True)
d.select(c, additive=True, direct=True)
check("a partial pick built directly stays partial", d.selection == [b, c])
d.select(c, additive=True)
check("a widening drop from a partial pick drops the whole group's members that are in it",
      d.selection == [b])
d.select_many([c], direct=True)
check("select_many direct is exactly these", d.selection == [c])
d.extend_selection([a], direct=True)
check("extend_selection direct adds exactly these", d.selection == [c, a])
d.select_many([c])
check("select_many without direct still widens", set(d.selection) == {a, c, e})
d.select(c, direct=True)
at = d.elements.index(c)
d.restore(d.snapshot())
check("a snapshot keeps a direct pick direct",
      d.selection == [d.elements[at]] and d.elements[at].group == (outer, inner))

# what a direct pick can do on its own
d, a, b, c, e, (outer, inner) = nest()
d.select(c, direct=True)
before = (a.x, a.y, e.x, e.y)
geometry.move_selection(d, d.selection, 5, 7)
check("a drag moves the directly picked member alone",
      (a.x, a.y, e.x, e.y) == before and (c.x, c.y) == (25, 27 + 180), (c.x, c.y))
b.x, c.x = 40, 100
d.select(b); d.select(c, additive=True, direct=True)
d.align_selected('left')
check("align treats the directly picked member as a unit of its own",
      (b.x, c.x, a.x, e.x) == (40, 40, 20, 20), (b.x, c.x, a.x, e.x))
d.select(c, direct=True)
check("a direct member's group still is what the z-order moves",
      d.elements == [b, a, c, e] and d.can_lower() and d.send_to_back()
      and d.elements == [a, c, e, b], [d.elements.index(x) for x in (a, b, c, e)])
d.select(c, direct=True)
check("ungroup from a direct member peels the whole outer group",
      d.ungroup_selected() and a.group == c.group == (inner,) and e.group is None)
d.select(c, direct=True); d.select(b, additive=True, direct=True)
check("group with a direct member wraps its whole group",
      d.group_selected() and a.group == c.group and len(a.group) == 2 and b.group == (a.group[0],)
      and set(d.selection) == {a, b, c} and d.selected_element is b,
      ([x.group for x in d.elements], order(d)))
d.select(c, direct=True)
check("delete of a direct member takes it alone",
      d.remove_selected() and c not in d.elements and a in d.elements and b in d.elements)

# outlines: one box per whole selected group, nested boxes inside their parent
d, a, b, c, e, (outer, inner) = nest()
d.select(a)
boxes = d.group_outlines()
ib = geometry.selection_bounds([a, c]); ob = geometry.selection_bounds([a, c, e])
check("a selected nest draws its outer box and its inner box, outer first",
      boxes == [(ob[0] - 4, ob[1] - 4, ob[2] + 8, ob[3] + 8),
                (ib[0] - 2, ib[1] - 2, ib[2] + 4, ib[3] + 4)], boxes)
d.select(c, direct=True)
check("a direct pick of one member draws no group box", d.group_outlines() == [])
d.select(a, direct=True); d.select(c, additive=True, direct=True)
check("a direct pick of every member of the inner pair draws its box alone, at the plain pad",
      d.group_outlines() == [(ib[0] - 2, ib[1] - 2, ib[2] + 4, ib[3] + 4)], d.group_outlines())
d, a, b, c, e = quad()
d.select_many([a, c]); d.group_selected()
pb = geometry.selection_bounds([a, c])
check("a plain pair draws the box it always did",
      d.group_outlines() == [(pb[0] - 2, pb[1] - 2, pb[2] + 4, pb[3] + 4)])
d.elements.remove(c); d.select(a)
check("a group of one draws nothing", a.group is not None and d.group_outlines() == [])

# --- remove from group ------------------------------------------------------
d, a, b, c, e = quad()
d.select_many([a, c]); d.group_selected()             # run [b, a, c, e]
d.select(a, direct=True)
check("a directly picked member can be removed from its group", d.can_remove_from_group())
check("a plain click on the group cannot", (d.select(c), not d.can_remove_from_group())[1])
check("nor a group plus a loose element",
      (d.select(b, additive=True), not d.can_remove_from_group())[1])
check("Ungroup and Remove are never both offered on a plain click",
      (d.select(c), d.can_ungroup() and not d.can_remove_from_group())[1])
d.select(a, direct=True); d.select(b, additive=True, direct=True)
check("a direct member plus a loose element can", d.can_remove_from_group())
d.clear_selection()
check("nothing selected cannot, and nothing happens",
      not d.can_remove_from_group() and not d.remove_from_group())
d.select(b)
check("a loose element alone cannot", not d.can_remove_from_group())
d.select(a, direct=True)
check("remove lifts the member to just above the group it left, selection kept",
      d.remove_from_group() and d.elements == [b, c, a, e] and d.selection == [a],
      ([d.elements.index(x) for x in (a, b, c, e)], order(d)))
check("a pair loses its group when one member leaves", a.group is None and c.group is None)

d, a, b, c, e = quad()
d.select_many([a, b, c]); d.group_selected()          # [a, b, c, e]
d.select(b, direct=True); d.remove_from_group()
check("the middle member lifted lands above the last, the others keep the group",
      d.elements == [a, c, b, e] and b.group is None and a.group == c.group and a.group,
      [d.elements.index(x) for x in (a, b, c, e)])

d, P, X, Q, Y = quad()
d.select_many([P, X, Q, Y]); d.group_selected()
d.select(X, direct=True); d.select(Y, additive=True, direct=True); d.remove_from_group()
check("two members lifted at once keep their order, above the rest",
      d.elements == [P, Q, X, Y] and X.group is None and Y.group is None and P.group == Q.group,
      [d.elements.index(x) for x in (P, X, Q, Y)])

d, a, b, c, e, (outer, inner) = nest()                # [b, a, c, e]
d.select(a, direct=True); d.remove_from_group()
check("lifting a member out of the inner pair re-parents it and dissolves the pair",
      a.group == (outer,) and c.group == (outer,) and e.group == (outer,)
      and d.elements == [b, c, a, e], ([x.group for x in d.elements]))
d, a, b, c, e, (outer, inner) = nest()
d.select(e, direct=True); d.remove_from_group()
check("lifting the loose member out of the nest leaves both ids on the pair",
      e.group is None and a.group == c.group == (outer, inner) and d.elements == [b, a, c, e])
d, a, b, c, e, (outer, inner) = nest()
d.select(a, direct=True); d.select(c, additive=True, direct=True)
check("every member of the inner pair picked directly is the pair, which can be lifted",
      d.can_remove_from_group())
d.remove_from_group()
check("lifting the pair takes it out of the nest whole, keeping its own id",
      a.group == c.group == (inner,) and e.group is None and d.elements == [b, e, a, c],
      ([x.group for x in d.elements], [d.elements.index(x) for x in (a, b, c, e)]))

d = Document(400, 400)
t1, t2, t3, t4 = [FrameElement(20, 20 + i * 90, 60, 40) for i in range(4)]
d.elements.extend([t1, t2, t3, t4])
d.select_many([t1, t2]); d.group_selected(); A = t1.group[0]
d.select_many([t1, t3]); d.group_selected(); B = t1.group[0]
d.select_many([t1, t4]); d.group_selected(); C = t1.group[0]
d.select(t1, direct=True); d.select(t2, additive=True, direct=True); d.remove_from_group()
check("a middle group lifted out of a three-level nest keeps its inner id",
      t1.group == t2.group == (C, A) and t3.group == (C,) and t4.group == (C,),
      [x.group for x in d.elements])

w.unsaved_changes = False; w.on_new()
ra = w.document.add_text_element('ra'); rb = w.document.add_frame_element()
w.document.select_many([ra, rb]); w.on_group()
w.document.select(ra, direct=True)
depth = len(w._undo_stack)
w.on_remove_from_group()
check("Remove from Group in the window records one undo entry",
      len(w._undo_stack) == depth + 1 and ra.group is None and rb.group is None
      and w.document.elements == [rb, ra])
w.on_undo()
check("and undo restores the group and the order",
      [x.group for x in w.document.elements] == [(1,), (1,)]
      and w.document.elements[0].element_type == 'text')
w.document.select_many([ra])
w.on_remove_from_group()
check("Remove from Group does nothing, and records nothing, on a group picked whole",
      len(w._undo_stack) == depth and all(x.group for x in w.document.elements))

# --- select all, deselect all, invert ---------------------------------------
d, a, b, c, e, (outer, inner) = nest()                # [b, a, c, e]; a,c,e nested
check("select all selects everything, the topmost element primary",
      d.select_all() and set(d.selection) == {a, b, c, e} and d.selected_element is e)
check("and says so only when it changed something", not d.select_all())
d.clear_selection()
check("clear_selection is deselect all", not d.selection)
check("invert of nothing is everything", d.invert_selection() and len(d.selection) == 4)
check("invert of everything is nothing", d.invert_selection() and not d.selection)
d.select(b)
d.invert_selection()
check("invert is the complement", set(d.selection) == {a, c, e}, order(d))
d.select(a, direct=True)
d.invert_selection()
check("invert of a member picked directly brings its whole group back with the rest",
      set(d.selection) == {a, b, c, e}, order(d))
empty = Document(400, 400)
check("on an empty document none of the three does anything",
      not empty.select_all() and not empty.invert_selection() and not empty.selection)

w.unsaved_changes = False; w.on_new()
w.document.add_text_element('sa'); w.document.add_frame_element()
depth = len(w._undo_stack)
w.on_select_all()
check("Select All in the window selects everything and records no undo entry",
      len(w.document.selection) == 2 and len(w._undo_stack) == depth)
w.on_invert_selection()
check("Invert Selection records none either",
      not w.document.selection and len(w._undo_stack) == depth)
w.on_select_all(); w.on_deselect_all()
check("nor does Deselect All", not w.document.selection and len(w._undo_stack) == depth)

# --- the resize target ------------------------------------------------------
def box_of(element):
    return (element.x, element.y, element.width, element.height)

d, a, b, c, e, (outer, inner) = nest()
d.select(b)
check("one element is its own resize target", d.resize_target() is b)
d.select(a, direct=True)
check("a directly picked member is the target, not its group", d.resize_target() is a)
d.select_many([a])
t = d.resize_target()
check("a click-selected nest resizes as the outer group",
      isinstance(t, geometry.GroupBox) and t.members == [a, c, e]
      and (t.x, t.y, t.width, t.height) == geometry.selection_bounds([a, c, e]))
check("handles of a group come from its joint box",
      geometry.handles(t)['br'] == (t.x + t.width, t.y + t.height))
d.select(a, direct=True); d.select(c, additive=True, direct=True)
t = d.resize_target()
check("every member of the inner pair picked directly resizes the pair",
      isinstance(t, geometry.GroupBox) and t.members == [a, c])
d.select(a, direct=True); d.select(e, additive=True, direct=True)
check("a partial pick has no target", d.resize_target() is None)
d.clear_selection()
check("nothing selected has none", d.resize_target() is None)
d2, p, q, r, s_ = quad(); d2.select_many([p, q])
check("two loose elements have none", d2.resize_target() is None)

# --- resizing a group -------------------------------------------------------
def frame_pair():
    """Two frames whose joint box is (40, 40, 180, 140), grouped and selected."""
    d = Document(400, 400)
    f1, f2 = FrameElement(40, 40, 60, 40), FrameElement(140, 120, 80, 60)
    d.elements.extend([f1, f2])
    d.select_many([f1, f2]); d.group_selected(); d.select(f1)
    return d, f1, f2

for handle, dx, dy, expect in (
        ('br', 20, 10, (40, 40, 200, 150)), ('tl', 20, 10, (60, 50, 160, 130)),
        ('tr', 20, 10, (40, 50, 200, 130)), ('bl', 20, 10, (60, 40, 160, 150)),
        ('tm', 0, 10, (40, 50, 180, 130)), ('bm', 0, 10, (40, 40, 180, 150)),
        ('ml', 20, 0, (60, 40, 160, 140)), ('mr', 20, 0, (40, 40, 200, 140))):
    d, f1, f2 = frame_pair()
    geometry.resize_by_handle(d, d.resize_target(), handle, dx, dy)
    got = geometry.selection_bounds([f1, f2])
    ex, ey, ew, eh = expect
    fixed_ok = ((got[0] == ex) if handle[1] != 'l' else True) and \
               ((got[1] == ey) if handle[0] != 't' else True) and \
               ((got[0] + got[2] == ex + ew) if handle[1] == 'l' else True) and \
               ((got[1] + got[3] == ey + eh) if handle[0] == 't' else True)
    moving_ok = abs(got[2] - ew) <= 1 and abs(got[3] - eh) <= 1
    check(f"resizing a group by {handle} keeps the fixed edges and moves the others",
          fixed_ok and moving_ok, (got, expect))

d, f1, f2 = frame_pair()
geometry.resize_by_handle(d, d.resize_target(), 'br', 180, 140)   # exactly x2
check("members scale about the anchor, positions and sizes alike",
      (f1.x, f1.y, f1.width, f1.height) == (40, 40, 120, 80)
      and (f2.x, f2.y, f2.width, f2.height) == (240, 200, 160, 120),
      (box_of(f1), box_of(f2)))
d, f1, f2 = frame_pair()
geometry.resize_by_handle(d, d.resize_target(), 'mr', 180, 0)
check("a side handle leaves the other axis alone",
      (f2.y, f2.height, f1.height) == (120, 60, 40) and f2.width == 160)

def text_and_frame(orientation='N'):
    d = Document(812, 1218, dpi=203)
    t = d.add_text_element('Scale me'); t.font_path = FONT
    t.orientation = orientation; d.sync_text_width(t)
    t.x, t.y = 100, 100
    f = FrameElement(100, 300, 200, 100); d.elements.append(f)
    d.select_many([t, f]); d.group_selected(); d.select(t)
    return d, t, f

d, t, f = text_and_frame()
fh, fw, box = t.font_height, t.font_width, geometry.selection_bounds([t, f])
geometry.resize_by_handle(d, d.resize_target(), 'br', box[2], box[3] // 2)   # x2 across, x1.5 down
check("a text member's font scales, height with the stack and width with the run",
      t.font_height == round(fh * 1.5) and t.font_width == fw * 2, (fh, fw, t.font_height, t.font_width))
check("and its box comes back from the metrics",
      t.width == t.printed_width(d.font_path, d.display_text(t)) and t.height == t.font_height)
got = geometry.selection_bounds([t, f])
check("the anchored corner of a snapping group is exact", (got[0], got[1]) == (box[0], box[1]), (got, box))

d, t, f = text_and_frame('R')
fh, fw, box = t.font_height, t.font_width, geometry.selection_bounds([t, f])
geometry.resize_by_handle(d, d.resize_target(), 'mr', box[2], 0)             # x2 across only
check("a rotated text member's font height follows the run across the label",
      t.font_height == fh * 2 and t.font_width == fw, (fh, fw, t.font_height, t.font_width))
check("and its box stays transposed", t.width == t.font_height)

def barcode_and_frame(orientation='N'):
    d = Document(812, 1218, dpi=203)
    bc = d.add_barcode_element(); bc.orientation = orientation
    bc.font = ('0', 20, 10); bc.sync_box()
    f = FrameElement(50, 600, 200, 100); d.elements.append(f)
    d.select_many([bc, f]); d.group_selected(); d.select(bc)
    return d, bc, f

d, bc, f = barcode_and_frame()
mw, bh, font, box = bc.module_width, bc.bar_height, bc.font, geometry.selection_bounds([bc, f])
geometry.resize_by_handle(d, d.resize_target(), 'br', box[2], box[3])       # x2 both
check("a barcode member's modules and bars scale, and its font with them",
      bc.module_width == mw * 2 and bc.bar_height == bh * 2
      and bc.font == (font[0], font[1] * 2, font[2] * 2), (mw, bh, font, bc.module_width, bc.bar_height, bc.font))
check("and its box is what those print", bc.width == bc.printed_width()
      and bc.height == bc.bar_height + bc.text_height())
d, bc, f = barcode_and_frame('R')
mw, bh, box = bc.module_width, bc.bar_height, geometry.selection_bounds([bc, f])
geometry.resize_by_handle(d, d.resize_target(), 'mr', box[2], 0)
check("a rotated barcode's bars follow the run across the label, its modules the stack",
      bc.bar_height == bh * 2 and bc.module_width == mw, (mw, bh, bc.module_width, bc.bar_height))

d = Document(812, 1218, dpi=203)
blk = d.add_text_element('one two three four five six seven eight nine ten eleven twelve')
blk.font_path = FONT; blk.block = zpl_model.FieldBlock(300, 4); blk.block.line_spacing = 4; blk.block.indent = 10
d.sync_text_width(blk)
f = FrameElement(50, 300, 200, 100); d.elements.append(f)
d.select_many([blk, f]); d.group_selected(); d.select(blk)
box = geometry.selection_bounds([blk, f])
geometry.resize_by_handle(d, d.resize_target(), 'br', box[2], box[3])       # x2 both
check("a block member's wrap width, spacing and indent scale and its line count does not",
      blk.block.width == 600 and blk.block.line_spacing == 8 and blk.block.indent == 20
      and blk.block.max_lines == 4, (blk.block.width, blk.block.line_spacing, blk.block.indent, blk.block.max_lines))

d, f1, f2 = frame_pair()
f1.thickness = 10; f2.thickness = 25
geometry.resize_by_handle(d, d.resize_target(), 'br', 180, 70)              # x2 across, x1.5 down
check("a frame's thickness scales by the smaller factor and stays within its box",
      f1.thickness == 15 and f2.thickness <= f2.max_thickness(), (f1.thickness, f2.thickness, f2.max_thickness()))

d = Document(812, 1218, dpi=203)
im = ImageElement(20, 20, 60, 60, _pil_image=Image.new('RGB', (60, 60), (128, 128, 128)))
d.elements.append(im); f = FrameElement(200, 200, 100, 100); d.elements.append(f)
d.select_many([im, f]); d.group_selected(); d.select(im)
im.get_print_render(lambda rgba: rgba)
geometry.resize_by_handle(d, d.resize_target(), 'br', 280, 280)             # x2
check("an image member's box scales and its bitmap re-dithers at the new size",
      (im.width, im.height) == (120, 120) and im.get_print_render(lambda rgba: rgba).size == (120, 120)
      and im._pil_image.size == (60, 60), (im.width, im.height))

d, t, f = text_and_frame()
t.typeset = 30; box = geometry.selection_bounds([t, f])
geometry.resize_by_handle(d, d.resize_target(), 'bm', 0, box[3])            # x2 down
check("a ^FT baseline offset scales down the label", t.typeset == 60, t.typeset)

d, a, b, c, e, (outer, inner) = nest()
d.select(a); was = box_of(c)
geometry.resize_by_handle(d, d.resize_target(), 'br', 100, 100)
check("resizing a nest scales the members of the inner group too", box_of(c) != was)

d, f1, f2 = frame_pair()
geometry.resize_by_handle(d, d.resize_target(), 'br', -5000, -5000)
got = geometry.selection_bounds([f1, f2])
check("a group cannot be dragged below the minimum size",
      got[2] >= geometry.MIN_SIZE and got[3] >= geometry.MIN_SIZE
      and all(el.width >= 1 and el.height >= 1 for el in (f1, f2)), got)
d, f1, f2 = frame_pair()
geometry.resize_by_handle(d, d.resize_target(), 'br', 99999, 99999)
got = geometry.selection_bounds([f1, f2])
check("nor past the label", got[0] + got[2] <= 400 and got[1] + got[3] <= 400 and got[0] >= 0, got)
d, f1, f2 = frame_pair()
geometry.resize_by_handle(d, d.resize_target(), 'ml', -500, 0)
got = geometry.selection_bounds([f1, f2])
check("a left handle dragged past the edge keeps the right edge where it was",
      got[0] == 0 and got[0] + got[2] == 220, got)
d = Document(812, 1218, dpi=203)
tiny = d.add_text_element('tiny'); tiny.font_path = FONT; tiny.font_height = 12; tiny.font_width = 12
d.sync_text_width(tiny); tiny.x, tiny.y = 100, 100
f = FrameElement(100, 200, 200, 100); d.elements.append(f)
d.select_many([tiny, f]); d.group_selected(); d.select(tiny)
geometry.resize_by_handle(d, d.resize_target(), 'br', -100, -100)
check("a group holding a 12-dot text still shrinks", tiny.font_height < 12 and f.height < 100,
      (tiny.font_height, f.height))

# paint every element type without exceptions
w.unsaved_changes = False; w.on_new()
w.document.add_text_element('paint me')
w.document.add_frame_element()
w.document.add_barcode_element()
img_src = Image.new('RGB', (60, 60), (128, 128, 128))
w.document.elements.append(ImageElement(20, 20, 60, 60, _pil_image=img_src))
w.document.selected_element = w.document.elements[0]
canvas = w.canvas; canvas.resize(600, 900)
target = QImage(600, 900, QImage.Format_ARGB32); target.fill(Qt.white)
canvas.render(target)
check("all element types paint, with a selection and handles", True)

# the group outline and the rubber band are their own paint paths
w.document.select_many(w.document.elements[:2])
canvas.band_origin, canvas.band_now = (10, 10), (300, 400)
canvas.render(target)
canvas.band_origin = canvas.band_now = None
w.document.selected_element = w.document.elements[0]
check("a group selection and a rubber band paint too", True)

# an unprintable element still paints (dimmed) and stays hittable
w.document.elements[0].print_enabled = False
canvas.render(target)
hit = w.document.element_at(w.document.elements[0].x + 2, w.document.elements[0].y + 2)
check("a non-printing element is still selectable", hit is not None)

# --- resize rules -----------------------------------------------------------
w.unsaved_changes = False; w.on_new()
el = w.document.add_frame_element()
w.document.selected_element = el
geometry.resize_by_handle(w.document, el, 'br', -5000, -5000)
check("resize keeps a 20x20 minimum", el.width >= 20 and el.height >= 20, (el.width, el.height))
check("frame thickness clamped to min(w,h)/2",
      el.thickness <= max(1, min(el.width, el.height) // 2), el.thickness)
te = w.document.add_text_element('snap'); te.font_path = FONT
w.document.sync_text_width(te); w.document.selected_element = te
geometry.resize_by_handle(w.document, te, 'br', 120, 20)
check("text box snaps to its printed width",
      te.width == te.printed_width(w.document.font_path), (te.width, te.printed_width(None)))
check("text height follows font height", te.height == te.font_height)

big = w.document.add_barcode_element(); w.document.selected_element = big
geometry.resize_by_handle(w.document, big, 'br', 99999, 99999)
check("resize clamps to the label",
      big.x + big.width <= w.document.label_width and big.y + big.height <= w.document.label_height)

# A drag arrives as a stream of small deltas, and a resize snaps the box back to
# what it will print. Measured from the previous event, every delta smaller than
# that snap is discarded and a slow drag moves nothing at all - which is what a
# pointer produces and what a single scripted resize can never show. So the same
# distance is dragged both ways and the two must land in the same place.
def drag_slowly(document, element, handle, dx, dy, steps=20):
    """The same gesture a pointer makes: one press, then many small motions."""
    origin = geometry.resize_origin(element)
    for step in range(1, steps + 1):
        geometry.resize_by_handle(document, element, handle,
                                  dx * step // steps, dy * step // steps,
                                  origin=origin)

def dragged_both_ways(build, handle, dx, dy):
    """The same drag delivered both ways, on two copies of the one element."""
    slow_doc, slow_el = build()
    drag_slowly(slow_doc, slow_el, handle, dx, dy)
    fast_doc, fast_el = build()
    geometry.resize_by_handle(fast_doc, fast_el, handle, dx, dy)
    return (slow_el, fast_el)

def plain_text():
    document = Document(812, 1218, dpi=203)
    element = document.add_text_element('Stretch')
    element.font_path = FONT
    document.sync_text_width(element)
    return document, element

def wrapped_text():
    document = Document(812, 1218, dpi=203)
    element = document.add_text_element(
        'one two three four five six seven eight nine ten eleven twelve')
    element.font_path = FONT
    # Long enough at this width to fill the allowance, so the block is showing
    # every line it is allowed and a drag upward has one to cut.
    element.block = zpl_model.FieldBlock(300, 4)
    document.sync_text_width(element)
    return document, element

def a_barcode():
    document = Document(812, 1218, dpi=203)
    return document, document.add_barcode_element()

for name, build, handle, dx, dy in (
        ("a text element's width", plain_text, 'mr', 120, 0),
        ("a text element's corner", plain_text, 'br', 90, 40),
        ("a barcode's width", a_barcode, 'mr', 200, 0),
        ("a block's line count", wrapped_text, 'bm', 0, -80)):
    slow, fast = dragged_both_ways(build, handle, dx, dy)
    check(f"dragging {name} slowly lands where dragging it fast does",
          box_of(slow) == box_of(fast), (box_of(slow), box_of(fast)))

# Landing in the same place is only worth something if the place moved: two
# drags that both did nothing would agree perfectly.
_unmoved_doc, unmoved = plain_text()
widened, _fast = dragged_both_ways(plain_text, 'mr', 120, 0)
check("a slow drag of a text element's width actually widens it",
      widened.width > unmoved.width, (unmoved.width, widened.width))

# A group is dragged the same way: from the press, every member put back and
# scaled again on every event.
def text_barcode_group():
    d = Document(812, 1218, dpi=203)
    t = d.add_text_element('Slowly'); t.font_path = FONT; d.sync_text_width(t)
    bc = d.add_barcode_element()
    d.select_many([t, bc]); d.group_selected(); d.select(t)
    return d, d.resize_target()

slow_doc, slow_target = text_barcode_group()
drag_slowly(slow_doc, slow_target, 'br', 150, 90)
fast_doc, fast_target = text_barcode_group()
geometry.resize_by_handle(fast_doc, fast_target, 'br', 150, 90)
check("dragging a group slowly lands where dragging it fast does",
      [box_of(el) for el in slow_target.members] == [box_of(el) for el in fast_target.members],
      ([box_of(el) for el in slow_target.members], [box_of(el) for el in fast_target.members]))
still_doc, still_target = text_barcode_group()
check("and the slow drag actually moved something",
      [box_of(el) for el in slow_target.members] != [box_of(el) for el in still_target.members])


# The bottom handle of a block asks for a line count, and the block keeps every
# line it is left with. Recomputed from a height that had already snapped back,
# it collapsed to one line on the first motion event of any drag at all.
doc_block, block_el = wrapped_text()
pitch = textraster.pitch(block_el.font_height, block_el.block)
lines_before = block_el.height // pitch
drag_slowly(doc_block, block_el, 'bm', 0, -pitch)
check("a slow drag of the bottom handle cuts one line, not all of them",
      block_el.height // pitch == lines_before - 1
      and block_el.block.max_lines == lines_before - 1,
      (lines_before, block_el.height // pitch, block_el.block.max_lines))

# A block's box is its wrap, so the height snaps back after every event. The top
# handle has to resize against that, not carry the element along with it.
doc_top, top_el = wrapped_text()
bottom_before = top_el.y + top_el.height
drag_slowly(doc_top, top_el, 'tm', 0, top_el.font_height)
check("the top handle of a block resizes it rather than moving it",
      top_el.y + top_el.height == bottom_before and top_el.y > 0,
      (top_el.y, top_el.height, bottom_before))

# At a quarter turn the height is the run of the text, not its font height. The
# barcode branch transposes; the text branch used to read the height either way,
# which set the font to the length of the string the moment a rotated element
# was dragged.
rot_doc = Document(812, 1218, dpi=203)
rot = rot_doc.add_text_element('Turned'); rot.font_path = FONT
rot.orientation = 'R'; rot_doc.sync_text_width(rot)
was_height, was_width = rot.font_height, rot.font_width
geometry.resize_by_handle(rot_doc, rot, 'br', 10, 40)
check("a rotated text element takes its font height across the text, not along it",
      rot.font_height == was_height + 10 and rot.font_width > was_width,
      (was_height, rot.font_height, was_width, rot.font_width))
check("a rotated text box stays transposed after a resize",
      rot.height == rot.printed_width(rot_doc.font_path)
      and rot.width == rot.font_height, (rot.width, rot.height))

# The toy-font fallback (^AF, i.e. no downloaded font) used to scale a field
# by its on-screen footprint width - which sync_text_width transposes with
# height at a quarter turn - instead of the run along the text. A rotated
# field's ink therefore stopped growing with font_width and tracked its
# (untouched) footprint width, itself just font_height, instead.
def fallback_ink_span(font_width, orientation):
    fb_doc = Document(300, 300, dpi=203)
    fb_el = fb_doc.add_text_element('IIIIIIIIII')
    fb_el.font_path = fb_el.font_family = None
    fb_el.orientation = orientation
    fb_el.font_height, fb_el.font_width = 30, font_width
    fb_doc.sync_text_width(fb_el)
    image = QImage(300, 60, QImage.Format_ARGB32); image.fill(Qt.white)
    painter = QPainter(image)
    qt_canvas.DesignCanvas(fb_doc)._draw_text_fallback(painter, fb_el, None)
    painter.end()
    left = right = -1
    for x in range(image.width()):
        for y in range(image.height()):
            c = image.pixelColor(x, y)
            if c.red() < 100 and c.green() < 100 and c.blue() < 100:
                if left == -1: left = x
                right = x
                break
    return (right - left) if left != -1 else 0

narrow_span = fallback_ink_span(10, 'R')
wide_span = fallback_ink_span(60, 'R')
check("a rotated fallback field still stretches with font_width",
      wide_span > narrow_span * 1.5, (narrow_span, wide_span))

# The canvas has to hand the geometry the box from the press. Everything above
# proves the geometry is right when it is given one; this drives the real
# press/motion/release path, because a canvas that went back to measuring from
# the previous event would pass every check above and still lose the drag.
def pointer_drag(canvas, from_x, from_y, dx, dy, steps=8):
    """Press, move in steps and release, in label dots."""
    scale = canvas._scale()
    def at(lx, ly, kind):
        return QMouseEvent(kind, QPoint(int(lx * scale), int(ly * scale)),
                           Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
    canvas.last_click_time = 0
    canvas.last_click_element = None
    canvas.mousePressEvent(at(from_x, from_y, QEvent.MouseButtonPress))
    for step in range(1, steps + 1):
        canvas.mouseMoveEvent(at(from_x + dx * step / steps,
                                 from_y + dy * step / steps, QEvent.MouseMove))
    canvas.mouseReleaseEvent(at(from_x + dx, from_y + dy,
                                QEvent.MouseButtonRelease))

pdoc = Document(812, 1218, dpi=203)
ptext = pdoc.add_text_element('Stretch'); ptext.font_path = FONT
pdoc.sync_text_width(ptext)
pcanvas = qt_canvas.DesignCanvas(pdoc)
pcanvas.set_view_size(812, 1218)
pcanvas.set_zoom(1.0)
pdoc.select(ptext)
expected_doc = Document(812, 1218, dpi=203)
expected = expected_doc.add_text_element('Stretch'); expected.font_path = FONT
expected_doc.sync_text_width(expected)
geometry.resize_by_handle(expected_doc, expected, 'br', 60, 24)
corner = geometry.handles(ptext)['br']
pointer_drag(pcanvas, corner[0], corner[1], 60, 24)
check("a handle dragged with the pointer lands where the geometry says",
      (ptext.width, ptext.height, ptext.font_height, ptext.font_width) ==
      (expected.width, expected.height, expected.font_height, expected.font_width),
      ((ptext.width, ptext.height, ptext.font_height, ptext.font_width),
       (expected.width, expected.height, expected.font_height, expected.font_width)))

# Zoomed out the hit radius grows past half a text element's height - its height
# being its font height - so the hit squares overlap. The nearest handle has to
# win, or the bottom corners answer for the side handles and the user grabs one
# edge while another moves.
short = TextElement(50, 50, 'Label')
short.width, short.height = 100, 36
for zoom in (1.0, 0.5, 0.25, 0.2):
    misrouted = {name: geometry.handle_at_point(hx, hy, short, zoom)
                 for name, (hx, hy) in geometry.handles(short).items()
                 if geometry.handle_at_point(hx, hy, short, zoom) != name}
    check(f"at {zoom:g}x every handle of a short element answers for itself",
          not misrouted, misrouted)

# --- save/load through the window ------------------------------------------
tmp = tempfile.mkdtemp()
path = os.path.join(tmp, 'out.zpl')
w.unsaved_changes = False; w.on_new()
w.document.add_text_element('persisted')
w.canvas.commit()
check("editing sets the unsaved flag", w.unsaved_changes)
check("save writes the file", w.save_zpl_file(path, w.document.to_zpl()))
check("save clears the unsaved flag", not w.unsaved_changes)
w.load_zpl_file(path)
check("load restores the element",
      any(getattr(e, 'text', '') == 'persisted' for e in w.document.elements))
check("load clears the unsaved flag", not w.unsaved_changes)
check("load clears the history", not w._undo_stack and not w._redo_stack)

# A rescale on load is not parsing - it is an answer the user gave, and it moves
# every element. Clearing the flag for it discarded that answer on close without
# a word, and the file went on recording the resolution it was drawn for, so the
# same prompt came back on the next open, and the next.
_rescale_src = os.path.join(tmp, 'other_dpi.zpl')
open(_rescale_src, 'w').write(
    "^XA^PW600^LL400\n^FXDESIGNER_DPI:300\n^FO50,50^A0N,40,40^FDscaled^FS\n^XZ")
_real_reconcile = workflow.reconcile_dpi
def _answer(reply):
    def patched(document, printer_dpi, ask, file_dpi=workflow._FROM_DOCUMENT):
        return _real_reconcile(document, printer_dpi, lambda *a: reply,
                               file_dpi=file_dpi)
    return patched
w.printer_dpi = 203
try:
    workflow.reconcile_dpi = _answer('rescale')
    w.load_zpl_file(_rescale_src)
    check("a rescale on load is an unsaved change", w.unsaved_changes)
    _moved = [(e.x, e.y) for e in w.document.elements]
    # Saving is what settles it: the stamp moves to the printer's resolution,
    # so reopening asks nothing and the loop ends.
    _settled = os.path.join(tmp, 'settled.zpl')
    w.save_zpl_file(_settled, w._document_zpl())
    _asked = []
    def _counting(document, printer_dpi, ask, file_dpi=workflow._FROM_DOCUMENT):
        return _real_reconcile(document, printer_dpi,
                               lambda *a: _asked.append(a) or 'keep',
                               file_dpi=file_dpi)
    workflow.reconcile_dpi = _counting
    w.load_zpl_file(_settled)
    check("and once saved the prompt does not come back", not _asked, _asked)
    check("the rescaled positions are what got saved",
          [(e.x, e.y) for e in w.document.elements] == _moved,
          ([(e.x, e.y) for e in w.document.elements], _moved))
    # Keeping the dots leaves every element exactly as the file has them, so
    # there is nothing unsaved to report.
    workflow.reconcile_dpi = _answer('keep')
    w.load_zpl_file(_rescale_src)
    check("keeping the dots is not", not w.unsaved_changes)
finally:
    workflow.reconcile_dpi = _real_reconcile
w.printer_dpi = zpl_fonts.DEFAULT_DPI
# Put the window back on the file the checks after this one are about
w.load_zpl_file(path)
check("failed save reports and keeps the flag",
      w.save_zpl_file('/nonexistent-dir/x.zpl', '^XA^XZ') is False)

# --- what the titlebar says --------------------------------------------------
# The file being edited, or the program's name - not the toolkit, and not a
# name from before the program had the one it has.
check("a loaded file is named in the titlebar", w.windowTitle() == 'out.zpl',
      w.windowTitle())
w.unsaved_changes = False; w.on_new()
check("New goes back to the program's name", w.windowTitle() == 'LinuxZPL',
      w.windowTitle())
w.document.add_text_element('titled')
check("saving adopts the name it wrote",
      w.save_zpl_file(os.path.join(tmp, 'adopted.zpl'), w.document.to_zpl())
      and w.windowTitle() == 'adopted.zpl', w.windowTitle())
check("a failed save leaves the title on the file still being edited",
      w.save_zpl_file('/nonexistent-dir/x.zpl', '^XA^XZ') is False
      and w.windowTitle() == 'adopted.zpl', w.windowTitle())

# --- the name a save chooser hands back -------------------------------------
from zplcore import workflow as _wf
check("a title is the file's name, not its path",
      _wf.window_title('/home/someone/labels/box.zpl') == 'box.zpl')
check("no file means the program's name",
      _wf.window_title(None) == 'LinuxZPL' and _wf.window_title('') == 'LinuxZPL')
check("a bare name gets .zpl", _wf.save_filename('/t/label') == '/t/label.zpl')
check("an extension is left alone", _wf.save_filename('/t/label.zpl') == '/t/label.zpl'
      and _wf.save_filename('/t/label.ZPL') == '/t/label.ZPL'
      and _wf.save_filename('/t/label.txt') == '/t/label.txt')
check("a dotted directory is not an extension",
      _wf.save_filename('/t.d/label') == '/t.d/label.zpl')

# The reason the suffix is settled first: the chooser confirmed "label", but
# the bytes land in "label.zpl", which nobody has been asked about yet.
asked = []
check("adding a suffix asks before clobbering",
      _wf.confirm_save_path('/t/label', lambda p: asked.append(p) or True,
                            exists=lambda p: True) == '/t/label.zpl'
      and asked == ['/t/label.zpl'])
check("declining returns to the chooser",
      _wf.confirm_save_path('/t/label', lambda p: False,
                            exists=lambda p: True) is None)
check("a suffix over free ground does not ask",
      _wf.confirm_save_path('/t/label', lambda p: 1 / 0,
                            exists=lambda p: False) == '/t/label.zpl')
check("a name the chooser already confirmed is not asked about twice",
      _wf.confirm_save_path('/t/label.zpl', lambda p: 1 / 0,
                            exists=lambda p: True) == '/t/label.zpl')

# --- ZPL as other tools write it -------------------------------------------
# Two real templates, kept verbatim. They are the regression test for a parser
# that used to read line by line and know nine commands.
FIXTURES = Path(__file__).resolve().parent / 'fixtures'

product = (FIXTURES / 'product_barcode.zpl').read_text()
doc, _dpi = zpl_parser.parse_zpl(product)
kinds = [e.element_type for e in doc.elements]
check("product_barcode.zpl yields both elements", kinds == ['barcode', 'text'], kinds)

serial = (FIXTURES / 'serial_barcode.zpl').read_text()
doc_s, _dpi = zpl_parser.parse_zpl(serial)
check("serial_barcode.zpl yields its barcode",
      [e.element_type for e in doc_s.elements] == ['barcode'])
# ^FO45,50^BY3 - two commands on one line, which the old scan could not see
bar = doc_s.elements[0]
check("both commands on a shared line are read",
      (bar.x, bar.y, bar.module_width) == (45, 50, 3),
      (bar.x, bar.y, bar.module_width))

text = doc.elements[1]
check("^A0 is read as the scalable font", text.font_code == '0', text.font_code)
check("^FB is read onto the element",
      text.block is not None and (text.block.width, text.block.max_lines,
                                  text.block.justification) == (182, 4, 'C'),
      text.block)
check("a block sizes the element box, not the string",
      text.width == 182, (text.width, text.height))

# nothing that changes the label may be lost between opening and saving
for name, source in (('product', product), ('serial', serial)):
    reparsed = zpl_parser.parse_zpl(source)[0].to_zpl()
    original = [c for c in zpl_parser.tokenise(source)
                if c[0] not in ('^XA', '^XZ', '^FS', '^PW', '^LL')]
    written = [c for c in zpl_parser.tokenise(reparsed)
               if c[0] not in ('^XA', '^XZ', '^FS', '^PW', '^LL', '^FX')]
    check(f"{name}_barcode.zpl round-trips its commands",
          [(c, p.strip()) for c, p in original] == [(c, p.strip()) for c, p in written],
          f"{original} != {written}")

# ^FB wrapping, measured with the metrics the box is derived from
from zplcore.model import FieldBlock
block = FieldBlock(182, 4, 1, 'C', 0)
lines = textraster.wrap("Stainless Steel Hex Head Bolt 10mm", FONT, 40, 40, block)
measure, _f = textraster.measurer(FONT, 40, 40)
check("every wrapped line fits the block",
      lines and all(measure(l) <= block.width for l in lines), lines)
check("wrapping stops at max_lines",
      len(textraster.wrap("one two three four five six seven eight",
                          FONT, 40, 40, FieldBlock(182, 2, 1, 'L', 0))) == 2)
check(r"\& forces a line break",
      textraster.wrap(r"top\&bottom", FONT, 40, 40, block) == ['top', 'bottom'])
check("a block with no font file still wraps",
      len(textraster.wrap("a b c d e f g h", None, 40, 20, FieldBlock(60, 4))) > 1)

# what a save would drop is reported rather than discovered on a label
from zplcore import workflow
check("nothing is reported for the templates",
      workflow.unsupported_commands(product) == []
      and workflow.unsupported_commands(serial) == [])
check("unmodelled commands are reported",
      workflow.unsupported_commands("^XA^FO1,1^B4N,6,200^FDdata^FS^FH^XZ") == ['^B4'])
check("and the label transforms are not, now that they survive a save",
      workflow.unsupported_commands(
          "^XA^LH10,10^LS1^LT1^POI^PMY^LRY^FO1,1^A0N,30,30^FDx^FS^XZ") == [],
      workflow.unsupported_commands(
          "^XA^LH10,10^LS1^LT1^POI^PMY^LRY^FO1,1^A0N,30,30^FDx^FS^XZ"))

# ^CC/^CT/^CD move the characters the tokeniser is built on. Rather than teach
# the tokeniser, canonicalise() rewrites such a label into the one it would
# have been with the defaults - redefinitions left out, any literal ^ or ~ the
# data was hiding behind them turned into ^FH escapes - and the load notice
# says what a save will do. The scan has to find the restoring command too,
# which is spelled with the new character.
redefs = zpl_parser.control_redefinitions
check("a label that never redefines anything reports none",
      redefs(product) == [] and redefs(serial) == []
      and redefs("^XA^FO1,1^A0N,30,30^FDx^FS^XZ") == [])
check("^CC is found, and so is the /CC^ that puts it back - spelled with the "
      "new prefix, which is why this is a scan and not a regex",
      redefs("^XA^CC//FO50,50/A0N,40,40/FDCtrl^Alt/FS/CC^^XZ")
      == ['^CC/', '/CC^'],
      redefs("^XA^CC//FO50,50/A0N,40,40/FDCtrl^Alt/FS/CC^^XZ"))
check("the control prefix is tracked the same way",
      redefs("^XA^CT!!SD15!CT~~SD15^XZ") == ['^CT!', '!CT~'],
      redefs("^XA^CT!!SD15!CT~~SD15^XZ"))
check("the delimiter change and its restore are both found",
      redefs("^XA^CD;^FO50;50^A0N;40;40^FDSmith, John^FS^CD,^XZ")
      == ['^CD;', '^CD,'])
check("the ~ spellings count too",
      redefs("^XA~CC/~CD;/FO1,1/XZ") == ['~CC/', '~CD;']
      and redefs("^XA~CT!^XZ") == ['~CT!'])
check("a redefinition with nothing after it is recorded bare and breaks nothing",
      redefs("^XA^FO1,1^FDx^FS^CC") == ['^CC']
      and redefs("^XA^FO1,1^FDx^FS^CC\n^XZ") == ['^CC'])

heard = []
said = workflow.warn_unsupported("^XA^CC//FO1,1/B4N,6,200/FDdata/FS/CC^^XZ",
                                 lambda cmds: heard.append(('dropped', cmds)),
                                 lambda found: heard.append(('redefined', found)))
check("a redefining label hears the redefinition notice, then the ordinary "
      "list - read on the canonical text, so it names the real ^B4",
      heard == [('redefined', ['^CC/', '/CC^']), ('dropped', ['^B4'])]
      and said == ['^CC/', '/CC^', '^B4'], heard)
heard = []
said = workflow.warn_unsupported("^XA^FO1,1^B4N,6,200^FDdata^FS^XZ",
                                 lambda cmds: heard.append(('dropped', cmds)),
                                 lambda found: heard.append(('redefined', found)))
check("and one that does not is reported exactly as before",
      heard == [('dropped', ['^B4'])] and said == ['^B4'], heard)
heard = []
check("a clean label says nothing either way",
      workflow.warn_unsupported(product, lambda c: heard.append(c),
                                lambda f: heard.append(f)) == [] and heard == [])
check("parsing a redefining label gives the real element, not an empty label",
      [(e.x, e.y, e.text) for e in
       zpl_parser.parse_zpl("^XA^CC//FO1,1/FDx/FS/CC^^XZ")[0].elements]
      == [(1, 1, 'x')])

canon = zpl_parser.canonicalise
_plain = "^XA^FO1,1^A0N,30,30^FDx^FS^XZ"
check("a label with no redefinition comes back as the very same object",
      canon(_plain)[0] is _plain and canon(product)[0] is product
      and canon(serial)[0] is serial)

_cc = "^XA^CC//FO50,50/A0N,40,40/FDCtrl^Alt/FS/CC^^XZ"
check("^CC: the label is rewritten with ^ back in charge, and the literal "
      "caret in the data becomes a ^FH escape under a ^FH supplied for it",
      canon(_cc)[0] == "^XA^FO50,50^A0N,40,40^FH_^FDCtrl_5EAlt^FS^XZ",
      canon(_cc)[0])
_ccd = zpl_parser.parse_zpl(_cc)[0]
check("and parses to one text element that shows Ctrl^Alt",
      len(_ccd.elements) == 1 and _ccd.display_text(_ccd.elements[0]) == 'Ctrl^Alt'
      and (_ccd.elements[0].x, _ccd.elements[0].y) == (50, 50),
      [(e.x, e.y, _ccd.display_text(e)) for e in _ccd.elements])
_ccz = _ccd.to_zpl()
check("a save writes the standard characters and no redefinition",
      'CC' not in _ccz and '^FH_^FDCtrl_5EAlt' in _ccz, _ccz)
check("and what it wrote reads back to the same label",
      zpl_parser.parse_zpl(_ccz)[0].display_text(
          zpl_parser.parse_zpl(_ccz)[0].elements[0]) == 'Ctrl^Alt')

_cd = "^XA^CD;^FO50;50^A0N;40;40^FDSmith, John^FS^CD,^XZ"
check("^CD: parameters are re-delimited, and the comma in the data is data",
      canon(_cd)[0] == "^XA^FO50,50^A0N,40,40^FDSmith, John^FS^XZ", canon(_cd)[0])
_cdd = zpl_parser.parse_zpl(_cd)[0]
check("so the field lands where it was put, comma intact",
      [(e.x, e.y, e.text) for e in _cdd.elements] == [(50, 50, 'Smith, John')],
      [(e.x, e.y, e.text) for e in _cdd.elements])

check("^CT: a control command spelled with the new prefix comes back as ~",
      canon("^XA^CT!!SD15!CT~~SD15^XZ")[0] == "^XA~SD15~SD15^XZ",
      canon("^XA^CT!!SD15!CT~~SD15^XZ")[0])
check("a tilde in the data while ~ is not the control prefix is escaped",
      canon("^XA^CT!^FO1,1^FDa~b^FS!CT~^XZ")[0]
      == "^XA^FO1,1^FH_^FDa_7Eb^FS^XZ",
      canon("^XA^CT!^FO1,1^FDa~b^FS!CT~^XZ")[0])
check("but a bare ~ while it is the control prefix is data, as the tokeniser "
      "already reads it - parity, so ^FC's trigger survives the rewrite",
      canon("^XA^FO1,1^FC%,#,~^FD%m^FS^XZ^CC^")[0]
      == "^XA^FO1,1^FC%,#,~^FD%m^FS^XZ",
      canon("^XA^FO1,1^FC%,#,~^FD%m^FS^XZ^CC^")[0])

check("the ~ spellings and a nested pair, each restored in turn",
      canon("^XA~CC/~CD;/FO1;1/FDa,b;c/FS/CD,/CC^^XZ")
      == ("^XA^FO1,1^FDa,b;c^FS^XZ", ['~CC/', '~CD;', '/CD,', '/CC^']),
      canon("^XA~CC/~CD;/FO1;1/FDa,b;c/FS/CD,/CC^^XZ"))

check("a field with its own ^FH escapes under that indicator, no second ^FH",
      canon("^XA^CC//FO1,1/FH#/FDa^b/FS/CC^^XZ")[0]
      == "^XA^FO1,1^FH#^FDa#5Eb^FS^XZ",
      canon("^XA^CC//FO1,1/FH#/FDa^b/FS/CC^^XZ")[0])
check("a supplied ^FH_ also escapes the underscores already in the data, so "
      "the new indicator cannot invent an escape",
      canon("^XA^CC//FO1,1/FDa_b^c/FS/CC^^XZ")[0]
      == "^XA^FO1,1^FH_^FDa_5Fb_5Ec^FS^XZ",
      canon("^XA^CC//FO1,1/FDa_b^c/FS/CC^^XZ")[0])
check("the indicator is the field's: the next field starts without one",
      canon("^XA^CC//FO1,1/FH#/FDa/FS/FO2,2/FDb^c/FS/CC^^XZ")[0]
      == "^XA^FO1,1^FH#^FDa^FS^FO2,2^FH_^FDb_5Ec^FS^XZ",
      canon("^XA^CC//FO1,1/FH#/FDa/FS/FO2,2/FDb^c/FS/CC^^XZ")[0])
check("data with nothing to escape gets no ^FH",
      canon("^XA^CC//FO1,1/FDplain/FS/CC^^XZ")[0]
      == "^XA^FO1,1^FDplain^FS^XZ")

check("a comment holding a literal caret no longer swallows the field after it",
      [(e.x, e.y, e.text) for e in zpl_parser.parse_zpl(
          "^XA^CC//FXsee ^FD here/FO1,1/FDx/FS/CC^^XZ")[0].elements]
      == [(1, 1, 'x')])

_gf_default = "^XA^FO1,1^GFA,8,8,1,FF00FF00FF00FF00^FS^XZ"
_gf_moved = "^XA^CD;^FO1;1^GFA;8;8;1;FF00FF00FF00FF00^FS^CD,^XZ"
check("^GF under a moved delimiter: the four counts are re-delimited and the "
      "payload is left alone",
      canon(_gf_moved)[0] == _gf_default, canon(_gf_moved)[0])
check("and the image it decodes to is the same one",
      zpl_parser.parse_zpl(_gf_moved)[0].elements[0].to_zpl()
      == zpl_parser.parse_zpl(_gf_default)[0].elements[0].to_zpl())

check("the redefinitions themselves are not in the unsupported list - the "
      "notice covers them - and what follows them is read for real",
      workflow.unsupported_commands("^XA^CC//FO1,1/B4N,6,200/FDdata/FS/CC^^XZ")
      == ['^B4'],
      workflow.unsupported_commands("^XA^CC//FO1,1/B4N,6,200/FDdata/FS/CC^^XZ"))
check("a stray prefix left by the restore is skipped, as a stray ^ is today",
      canon("^XA^CC//FO1,1/FDx/FS/CC^^^XZ")[0] == "^XA^FO1,1^FDx^FS^^XZ"
      and [(e.text) for e in zpl_parser.parse_zpl(
          "^XA^CC//FO1,1/FDx/FS/CC^^^XZ")[0].elements] == ['x'])

# --- every ^BC parameter ----------------------------------------------------
from zplcore.model import BARCODE_MODES, BARCODE_ORIENTATIONS
from zplcore import code128

bc = BarcodeElement(0, 0, 100, '12345678', 2)
check("the box is the summed symbol, not a character count",
      bc.width == sum(bc.modules()) * 2, bc.width)
check("the box includes the interpretation line",
      bc.height == 100 + bc.text_height(), bc.height)

silent = BarcodeElement(0, 0, 100, '12345678', 2, '', ('N',))
check("no line, no extra height", silent.height == 100)
check("a barcode with no line writes ^BC,<h>,N",
      '^BC,100,N\n' in silent.to_zpl(), silent.to_zpl())

# subset C: the reason a numeric barcode was drawn twice its printed width
plain = BarcodeElement(0, 0, 100, '1234567890', 2, '', ('Y', 'N', 'N', 'N'))
auto = BarcodeElement(0, 0, 100, '1234567890', 2, '', ('Y', 'N', 'N', 'A'))
check("mode A packs digit pairs, and the width follows",
      auto.width < plain.width * 0.7, f"{plain.width} -> {auto.width}")
check("mode A is never wider than subset B",
      all(sum(code128.encode(v, 'A')) <= sum(code128.encode(v, 'N'))
          for v in ('123456789', 'ABC123', 'A1B2', '12', 'PART-12345678-X')))
check("subset B is unchanged", code128.encode('12345', 'N') == code128.encode_b('12345'))

checked = BarcodeElement(0, 0, 100, '12345678', 2, '', ('Y', 'N', 'Y'))
check("the UCC check digit joins both the symbol and the text",
      len(checked.encoded_value()) == 9
      and checked.encoded_value()[:-1] == '12345678', checked.encoded_value())

for code in ('R', 'B'):
    turned = BarcodeElement(0, 0, 100, '12345678', 2, code)
    check(f"orientation {code} transposes the footprint",
          (turned.width, turned.height) == (bc.height, bc.width))
    lay = geometry.barcode_layout(turned)
    check(f"orientation {code} turns the frame", lay['angle'] in (90, 270))

# resizing asks for a module width and a bar height, not a rectangle
rdoc = Document(812, 1218, dpi=203)
rb = rdoc.add_barcode_element()
geometry.resize_by_handle(rdoc, rb, 'br', 200, 60)
check("resize leaves the box equal to what prints",
      rb.width == rb.printed_width() and rb.height == rb.bar_height + rb.text_height(),
      f"{rb.width}x{rb.height}, module {rb.module_width}, bars {rb.bar_height}")

# the round trip carries every parameter
full = BarcodeElement(5, 6, 80, '9876', 3, 'R', ('N', 'Y', 'Y', 'A'),
                      font=('0', 24, 24))
reparsed = zpl_parser.parse_zpl(f"^XA{full.to_zpl()}^XZ")[0].elements[0]
check("every ^BC parameter survives a round trip",
      (reparsed.orientation, reparsed.bar_height, reparsed.show_text,
       reparsed.text_above, reparsed.check_digit, reparsed.mode,
       reparsed.module_width, reparsed.font)
      == ('R', 80, False, True, True, 'A', 3, ('0', 24, 24)),
      reparsed.to_zpl())

check("both frontends are offered the same modes",
      [c for _l, c in BARCODE_MODES] == ['N', 'A', 'U', 'D'])
check("both frontends are offered the same orientations",
      [c for _l, c in BARCODE_ORIENTATIONS] == ['N', 'R', 'I', 'B'])

# --- the other symbologies: ^B3, ^BE, ^B2, ^BS -------------------------------
from zplcore import code39, ean13, i2of5, upcext
from zplcore.model import BARCODE_FEATURES, BARCODE_SYMBOLOGIES

for cmd in ('^B3', '^BE', '^B2', '^BS'):
    check(f"{cmd} is no longer reported as unsupported",
          cmd not in workflow.unsupported_commands(f"^XA^FO0,0{cmd}N^FD1^FS^XZ"),
          workflow.unsupported_commands(f"^XA^FO0,0{cmd}N^FD1^FS^XZ"))

from zplcore import symbology as zpl_symbology

check("both frontends are offered every symbology the catalogue holds, and "
      "the same one for each command",
      [v for _l, v in BARCODE_SYMBOLOGIES] == list(zpl_symbology.SYMBOLOGIES)
      and set(zpl_symbology.COMMAND) == set(zpl_symbology.SYMBOLOGIES)
      and all(zpl_symbology.SYMBOLOGY_OF[cmd] == key
              for key, cmd in zpl_symbology.COMMAND.items()),
      [v for _l, v in BARCODE_SYMBOLOGIES])
check("every symbology has a dialog entry, and every command a parameter list",
      set(BARCODE_FEATURES) == set(zpl_symbology.SYMBOLOGIES)
      and set(zpl_symbology.COMMAND.values()) <= set(zpl_symbology.COMMAND_PARAMS),
      (sorted(set(BARCODE_FEATURES) ^ set(zpl_symbology.SYMBOLOGIES)),
       sorted(set(zpl_symbology.COMMAND.values()) - set(zpl_symbology.COMMAND_PARAMS))))
check("only Code 128 offers a mode, since no other symbology has one",
      [name for name, feat in BARCODE_FEATURES.items() if feat['mode']] == ['code128'],
      [n for n, f in BARCODE_FEATURES.items() if f['mode']])
check("a symbology offers a check-digit row exactly when its command has an e",
      [name for name, feat in BARCODE_FEATURES.items()
       if feat['check_digit'] is not None]
      == [name for name in BARCODE_FEATURES
          if zpl_symbology.varies(name, 'e')],
      [(n, f['check_digit'] is not None, zpl_symbology.varies(n, 'e'))
       for n, f in BARCODE_FEATURES.items()])
check("Codabar spells a check digit its own command fixes at N",
      'e' in zpl_symbology.COMMAND_PARAMS['^BK']
      and not zpl_symbology.varies('codabar', 'e')
      and BARCODE_FEATURES['codabar']['check_digit'] is None,
      "the manual gives it as a fixed value; Codabar has no checksum")
check("a symbology offers a line exactly when it has one to print",
      [name for name, feat in BARCODE_FEATURES.items() if not feat['text']]
      == [name for name in BARCODE_FEATURES if name in zpl_symbology.NO_TEXT],
      [(n, f['text']) for n, f in BARCODE_FEATURES.items()
       if (not f['text']) != (n in zpl_symbology.NO_TEXT)])
check("and a height exactly when its size is not its grid alone",
      [name for name, feat in BARCODE_FEATURES.items()
       if feat['height'] is None]
      == [name for name in BARCODE_FEATURES
          if name in zpl_symbology.HEIGHT_UNIT
          and zpl_symbology.HEIGHT_UNIT[name] is None],
      [(n, f['height']) for n, f in BARCODE_FEATURES.items()
       if f['height'] is None])
check("a symbology offers an orientation row exactly when its command has an o",
      [name for name, feat in BARCODE_FEATURES.items() if feat['orientation']]
      == [name for name in BARCODE_FEATURES if zpl_symbology.varies(name, 'o')],
      [n for n, f in BARCODE_FEATURES.items()
       if f['orientation'] != zpl_symbology.varies(n, 'o')])
check("and only MaxiCode offers the GS, RS and EOT buttons",
      [name for name, feat in BARCODE_FEATURES.items()
       if feat['control_chars']] == ['maxicode'])

# Code 39: self-checking, so every character costs the same twelve modules -
# three wide (2) plus six narrow (1) plus the inter-character gap.
c39 = BarcodeElement(0, 0, 60, 'CODE-39', symbology='code39')
check("Code 39 draws the value between two start/stop asterisks",
      # Twelve modules a character (nine elements, three of them twice a
      # narrow one) plus a one-module gap between every pair of them,
      # asterisks included.
      sum(code39.encode('CODE-39')) == 12 * (len('CODE-39') + 2) + (len('CODE-39') + 1),
      sum(code39.encode('CODE-39')))
check("its own mod-43 check digit is a single extra character",
      code39.mod43_check_digit('CODE-39') == code39.mod43_check_digit('code-39'),
      "case is folded, the way encode() folds it too")
c39_checked = BarcodeElement(0, 0, 60, 'CODE-39', symbology='code39', options=('Y', 'N', 'Y'))
check("Code 39's own check digit joins the text, like Code 128's",
      c39_checked.encoded_value() == 'CODE-39' + code39.mod43_check_digit('CODE-39'),
      c39_checked.encoded_value())
c39_wide = BarcodeElement(0, 0, 60, 'A', symbology='code39', ratio=2.0)
check("Code 39's ratio scales its wide elements, unlike Code 128's",
      set(c39_wide.modules()) == {1, 2} and set(code39.encode('A')) == {1, 2},
      c39_wide.modules())
c39_wider = BarcodeElement(0, 0, 60, 'A', symbology='code39', ratio=3.0)
check("a bigger ratio widens the symbol without changing its narrow modules",
      c39_wider.printed_width() > c39_wide.printed_width(),
      (c39_wide.printed_width(), c39_wider.printed_width()))

# EAN-13: always 95 modules, always thirteen digits including its own check
# digit, which is not optional - there is no ^BE parameter for it at all.
ean = BarcodeElement(0, 0, 60, '400638133393', symbology='ean13')
check("EAN-13 is always ninety-five modules",
      sum(ean.modules()) == 95, sum(ean.modules()))
check("its check digit is always appended, with no flag to ask for it",
      ean.encoded_value() == '4006381333931', ean.encoded_value())
ean_short = BarcodeElement(0, 0, 60, '123', symbology='ean13')
check("a short value is padded with zeros on the left, not on the right",
      ean_short.encoded_value().startswith('000000000123'), ean_short.encoded_value())
ean_long = BarcodeElement(0, 0, 60, '1' * 20, symbology='ean13')
check("a long one is truncated to its last twelve digits",
      ean_long.encoded_value()[:-1] == '1' * 12, ean_long.encoded_value())

# Interleaved 2 of 5: numeric, and always an even number of digits.
i25 = BarcodeElement(0, 0, 60, '123456', symbology='interleaved2of5')
check("an even value round-trips unpadded",
      i25.encoded_value() == '123456', i25.encoded_value())
i25_odd = BarcodeElement(0, 0, 60, '12345', symbology='interleaved2of5')
check("an odd one gets a leading zero, not a trailing one",
      i25_odd.encoded_value() == '012345', i25_odd.encoded_value())
i25_checked = BarcodeElement(0, 0, 60, '123456', symbology='interleaved2of5',
                             options=('Y', 'N', 'Y'))
check("its own Mod-10 check digit is added before the even-length padding",
      i25_checked.encoded_value() == '0' + '123456' + code128.ucc_check_digit('123456'),
      i25_checked.encoded_value())

# UPC/EAN Extension: a 2-digit or 5-digit add-on, no check digit at all.
ext2 = BarcodeElement(0, 0, 60, '05', symbology='upcean_extension')
check("two digits stay a two-digit extension",
      ext2.encoded_value() == '05' and sum(ext2.modules()) == 21,
      (ext2.encoded_value(), sum(ext2.modules())))
ext5 = BarcodeElement(0, 0, 60, '12345', symbology='upcean_extension')
check("five digits are a five-digit extension",
      ext5.encoded_value() == '12345' and sum(ext5.modules()) == 48,
      (ext5.encoded_value(), sum(ext5.modules())))
check("the extension's own default prints its line above the bars, not below",
      BarcodeElement(0, 0, 60, '05', symbology='upcean_extension').text_above,
      "^BS's own default for that parameter is Y, unlike every other symbology")

# every one of the four round-trips through its own command, options and all
for symbology, value, options, ratio, expect in (
        ('code39', 'ABC-1', ('N', 'Y', 'Y'), 2.5, '^B3N,Y,60,N,Y\n'),
        ('ean13', '400638133393', ('N', 'Y'), 3.0, '^BEN,60,N,Y\n'),
        ('interleaved2of5', '1234', ('Y', 'N', 'Y'), 2.0, '^B2N,60,Y,N,Y\n'),
        ('upcean_extension', '12', ('N', 'N'), 3.0, '^BSN,60,N,N\n')):
    made = BarcodeElement(1, 2, 60, value, orientation='N', options=options,
                          ratio=ratio, symbology=symbology)
    zpl = made.to_zpl()
    check(f"{symbology} writes its own command", expect in zpl, zpl)
    back = zpl_parser.parse_zpl(f"^XA{zpl}^XZ")[0].elements[0]
    check(f"{symbology} reads back the same element it wrote",
          (back.symbology, back.barcode_value, back.show_text, back.text_above,
           back.check_digit if symbology != 'ean13' and symbology != 'upcean_extension'
           else False)
          == (symbology, value, options[0] != 'N', options[1] == 'Y',
              (options[2] == 'Y') if len(options) > 2 else False),
          (back.symbology, back.barcode_value, back.show_text, back.text_above))

def _qr_square():
    """Drag a QR code's corner handle into a wide, short box."""
    doc = Document()
    el = BarcodeElement(10, 10, barcode_value='MM,AAC-42', symbology='qr',
                        module_width=4)
    doc.elements.append(el)
    was = el.module_width
    el.width, el.height = 400, 120
    geometry.resize_by_handle(doc, el, 'br', 0, 0,
                              {'x': 10, 'y': 10, 'width': 400, 'height': 120})
    return (el.width == el.height, el.module_width != was)


# --- the UPC/EAN family and the rest of the 1-D set --------------------------
from zplcore import (code11 as zpl_code11, code93 as zpl_code93,
                     codabar as zpl_codabar, ean8 as zpl_ean8,
                     msi as zpl_msi, plessey as zpl_plessey,
                     twoof5 as zpl_twoof5, upca as zpl_upca, upce as zpl_upce)

# Each symbology's own fixed length or module count - the thing that is wrong
# first when a guard pattern or a digit table is off by one.
for name, value, modules, shows in (
        ('upca', '03600029145', 95, '036000291452'),
        ('ean8', '9638507', 67, '96385074'),
        ('upce', '4210000526', 51, '04252614'),
        ('code93', 'TEST93', 9 * (6 + 2 + 2) + 1, 'TEST93'),
):
    el = BarcodeElement(0, 0, 60, value, symbology=name)
    check(f"{name} is {modules} modules",
          sum(el.modules()) == modules, sum(el.modules()))
    check(f"and its line shows {shows!r}",
          el.encoded_value() == shows, el.encoded_value())

check("UPC-A pads a short value on the left, as EAN-13 does",
      zpl_upca.normalize('45').startswith('000000000'), zpl_upca.normalize('45'))
check("EAN-8 and UPC-A share one mod-10 check digit, at different lengths",
      zpl_ean8.normalize('1234567')[-1] == code128.ucc_check_digit('1234567')
      and zpl_upca.normalize('03600029145')[-1]
      == code128.ucc_check_digit('03600029145'))

# UPC-E's zero suppression is a table that runs both ways, so a number it
# cannot shorten has no symbol at all: drawing its last six digits would give
# a perfectly readable barcode for a different product code.
for full, short in (('1230000045', '123453'), ('4210000526', '425261'),
                    ('0000000015', '000150'), ('0210000005', '020051')):
    check(f"UPC-E shortens {full} to {short}",
          zpl_upce.compress(full) == short, zpl_upce.compress(full))
_bad = BarcodeElement(0, 0, 60, '0425000026', symbology='upce')
check("a number UPC-E cannot shorten draws nothing and says why",
      _bad.symbol() == ('linear', []) and 'UPC-E' in (_bad.symbol_error or '')
      and geometry.barcode_layout(_bad)['rects'] == [], _bad.symbol_error)
check("and it still has a footprint, so it can be selected and corrected",
      _bad.width > 0 and _bad.height > 0, (_bad.width, _bad.height))

# Code 93's two check characters are not optional the way Code 39's is: ^BA's
# own e says whether the line shows them, not whether the symbol carries them.
check("Code 93's check characters for TEST93 are +6",
      zpl_code93.check_characters('TEST93') == '+6',
      zpl_code93.check_characters('TEST93'))
_c93 = BarcodeElement(0, 0, 60, 'TEST93', symbology='code93')
_c93_shown = BarcodeElement(0, 0, 60, 'TEST93', symbology='code93',
                            options=('Y', 'N', 'Y'))
check("the symbol carries them either way, and only the line changes",
      _c93.modules() == _c93_shown.modules()
      and _c93.encoded_value() == 'TEST93'
      and _c93_shown.encoded_value() == 'TEST93+6',
      (_c93.encoded_value(), _c93_shown.encoded_value()))

# Code 11 is the one command whose e reads backwards: Y is one check
# character and N - the default - is two.
check("^B1's N means two check characters and Y means one",
      (len(zpl_code11.check_characters('123456', 2)),
       len(zpl_code11.check_characters('123456', 1))) == (2, 1))
check("and 123456 checks out to 11, by the C then K weightings",
      zpl_code11.check_characters('123456') == '11',
      zpl_code11.check_characters('123456'))
_c11 = BarcodeElement(0, 0, 60, '123456', symbology='code11')
check("the element defaults to two, as the manual does",
      _c11.code11_check == 'N' and _c11.encoded_value() == '12345611',
      _c11.encoded_value())

# Codabar names its start and stop separately - the whole point of having
# four of them - and they are not data.
_cb = BarcodeElement(0, 0, 60, '12-34', symbology='codabar',
                     params={'start_char': 'B', 'stop_char': 'C'})
check("Codabar's start and stop round-trip through its own command",
      '^BK,N,60,Y,N,B,C\n' in _cb.to_zpl(), _cb.to_zpl())
check("a start character typed into the data is dropped, not drawn",
      zpl_codabar.normalize('A12A') == '12',
      "a second start character mid-symbol is not something a reader gets past")

# MSI's four schemes, and the flag that says whether the line shows what they
# added.
check("MSI's schemes add none, one, two, and a mod 11 then a mod 10",
      [len(zpl_msi.check_digits('1234', s)) for s in 'ABCD'] == [0, 1, 2, 2],
      [zpl_msi.check_digits('1234', s) for s in 'ABCD'])
_msi_hidden = BarcodeElement(0, 0, 60, '1234', symbology='msi')
_msi_shown = BarcodeElement(0, 0, 60, '1234', symbology='msi',
                            params={'msi_show_check': 'Y'})
check("the symbol carries the check digit either way; the line may not",
      _msi_hidden.modules() == _msi_shown.modules()
      and (_msi_hidden.encoded_value(), _msi_shown.encoded_value())
      == ('1234', '12344'),
      (_msi_hidden.encoded_value(), _msi_shown.encoded_value()))

# Plessey's CRC is eight bits over the whole message, and is never optional.
check("Plessey's check is eight bits, shown as two hex digits when asked",
      len(zpl_plessey.check_bits('1234')) == 8
      and BarcodeElement(0, 0, 60, '1234', symbology='plessey',
                         options=('Y', 'N', 'Y')).encoded_value() == '12340B',
      BarcodeElement(0, 0, 60, '1234', symbology='plessey',
                     options=('Y', 'N', 'Y')).encoded_value())

# Both 2 of 5 variants put every bit of information in the bars, and differ
# only in how they start and stop.
_ind = zpl_twoof5.encode('1234')
_std = zpl_twoof5.encode('1234', 'standard2of5')
check("Industrial and Standard 2 of 5 share a digit table and differ at the ends",
      _ind[6:-5] == _std[4:-3] and _ind != _std,
      (len(_ind), len(_std)))
check("every space in both is narrow - all the information is in the bars",
      set(_ind[1::2]) == {1} and set(_std[1::2]) == {1})

# LOGMARS is Code 39 with a check digit that is not optional, and no way to
# switch its interpretation line off.
_log = BarcodeElement(0, 0, 60, '12ab', symbology='logmars')
check("LOGMARS folds to upper case and always adds its Mod-43 digit",
      _log.encoded_value() == '12AB' + code39.mod43_check_digit('12AB'),
      _log.encoded_value())
check("and its command has no f at all, so the line always prints",
      'f' not in zpl_symbology.COMMAND_PARAMS['^BL']
      and BARCODE_FEATURES['logmars']['text'] == 'always'
      and _log.show_text)

# Every new command round-trips through its own parameter order.
for zpl_text, expect in (
        ('^BUN,60,Y,N,N', ('upca', False)),
        ('^B9N,60,Y,N,Y', ('upce', True)),
        ('^B8N,60', ('ean8', False)),
        ('^BAN,60,Y,N,Y', ('code93', True)),
        ('^BKN,N,60,Y,N,D,D', ('codabar', False)),
        ('^B1Y,60', ('code11', False)),
        ('^BMN,C,60,Y,N,Y', ('msi', False)),
        ('^BPN,Y,60', ('plessey', True)),
        ('^BIN,60', ('industrial2of5', False)),
        ('^BJN,60', ('standard2of5', False)),
        ('^BLN,60,N', ('logmars', False))):
    page = f"^XA^PW812^LL1218^FO10,10^BY2\n{zpl_text}\n^FD1234^FS^XZ"
    read = zpl_parser.parse_zpl(page)[0].elements[0]
    check(f"{zpl_text} reads as {expect[0]}",
          (read.symbology, read.check_digit) == expect,
          (read.symbology, read.check_digit))
    check(f"and {zpl_text} writes back the same command",
          zpl_text.split(',')[0] in read.to_zpl(), read.to_zpl().replace(chr(10), ' '))
    again = zpl_parser.parse_zpl(f"^XA{read.to_zpl()}^XZ")[0].elements[0]
    check(f"and {zpl_text} is stable across a second save",
          again.to_zpl() == read.to_zpl(), (read.to_zpl(), again.to_zpl()))

for command in ('^BU', '^B9', '^B8', '^BA', '^BK', '^B1', '^BM', '^BP',
                '^BI', '^BJ', '^BL'):
    check(f"{command} is no longer a command a save would drop",
          workflow.unsupported_commands(
              f"^XA^FO0,0{command}N,60^FD1234^FS^XZ") == [])

# --- ^BZ and ^B5, the postal codes ------------------------------------------
from zplcore import postal as zpl_postal

# The one family drawn as bars of differing height rather than differing
# width: every bar is narrow, every gap the same, and what carries the data is
# how tall each bar is and where it sits.
_post = BarcodeElement(0, 0, 40, '12345', symbology='postal', module_width=3)
check("Postnet is a frame bar, five bars a digit, and a frame bar",
      _post.symbol()[0] == 'postal' and len(_post.symbol()[1]) == 2 + 5 * 6,
      len(_post.symbol()[1]))
check("its check digit brings the digit sum to a multiple of ten",
      zpl_postal.normalize('12345') == '123455'
      and sum(int(c) for c in zpl_postal.normalize('12345')) % 10 == 0,
      zpl_postal.normalize('12345'))
check("two of every digit's five bars are full height",
      all(sum(1 for top, _b in _post.symbol()[1][1 + n * 5:6 + n * 5] if top == 0.0) == 2
          for n in range(6)))
_planet = BarcodeElement(0, 0, 40, '12345', symbology='postal', module_width=3,
                         params={'postal_type': '1'})
check("PLANET is Postnet inverted - three full bars a digit, not two",
      all(sum(1 for top, _b in _planet.symbol()[1][1 + n * 5:6 + n * 5] if top == 0.0) == 3
          for n in range(6)))
check("^B5 is PLANET too, without a type to choose",
      BarcodeElement(0, 0, 40, '12345', symbology='planet').symbol()[1]
      == _planet.symbol()[1])

_imb = BarcodeElement(0, 0, 40, '00123123456123456789', symbology='postal',
                      module_width=3, params={'postal_type': '3'})
check("the Intelligent Mail barcode is always sixty-five bars",
      len(_imb.symbol()[1]) == 65, len(_imb.symbol()[1]))
check("and uses all four of its states, which are four distinct rectangles",
      len({(r[1], r[3]) for r in geometry.barcode_layout(_imb)['rects']}) == 4,
      sorted({(r[1], r[3]) for r in geometry.barcode_layout(_imb)['rects']}))

_reserved = BarcodeElement(0, 0, 40, '12345', symbology='postal',
                           params={'postal_type': '2'})
check("^BZ's reserved type draws nothing rather than a Postnet it never asked for",
      _reserved.symbol() == ('postal', []) and _reserved.symbol_error
      and geometry.barcode_layout(_reserved)['rects'] == [],
      _reserved.symbol_error)

check("a postal barcode's bars are one module wide at a one-to-one pitch",
      all(r[2] == 3 for r in geometry.barcode_layout(_post)['rects'])
      and [r[0] for r in geometry.barcode_layout(_post)['rects'][:3]] == [0, 6, 12])
check("and its footprint is the bars and the gaps between them",
      _post.width == (2 * 32 - 1) * 3, _post.width)
check("the postal codes print no interpretation line unless asked",
      not _post.show_text and not _planet.show_text
      and BarcodeElement(0, 0, 40, '1', symbology='postal',
                         options=('Y',)).show_text)

for zpl_text, expect in (('^BZN,40', ('postal', '0')),
                         ('^BZN,40,N,N,1', ('postal', '1')),
                         ('^BZN,40,Y,N,3', ('postal', '3')),
                         ('^B5N,40', ('planet', '0'))):
    page = f"^XA^PW812^LL1218^FO10,10^BY3\n{zpl_text}\n^FD12345^FS^XZ"
    read = zpl_parser.parse_zpl(page)[0].elements[0]
    check(f"{zpl_text} reads as {expect[0]} type {expect[1]}",
          (read.symbology, read.postal_type) == expect,
          (read.symbology, read.postal_type))
    check(f"and {zpl_text} writes itself back unchanged",
          zpl_text + '\n' in read.to_zpl(), read.to_zpl().replace(chr(10), ' '))

for command in ('^BZ', '^B5'):
    check(f"{command} is no longer a command a save would drop",
          workflow.unsupported_commands(
              f"^XA^FO0,0{command}N,40^FD12345^FS^XZ") == [])

# --- ^BX, Data Matrix -------------------------------------------------------
from zplcore import datamatrix as zpl_dm

_dm = BarcodeElement(0, 0, 60, 'HELLO', symbology='datamatrix', module_width=8)
check("a Data Matrix is a square grid, sized by the data",
      _dm.symbol()[0] == 'grid' and len(_dm.symbol()[1]) == 12
      and len(_dm.symbol()[1][0]) == 12, len(_dm.symbol()[1]))
check("and its box is that grid at its own module size",
      (_dm.width, _dm.height) == (96, 96), (_dm.width, _dm.height))
check("every symbol's corner is the solid finder pattern",
      _dm.symbol()[1][0][0] and _dm.symbol()[1][-1][0]
      and all(row[0] for row in _dm.symbol()[1])
      and all(_dm.symbol()[1][-1]),
      "left column and bottom row solid, which is what a reader finds it by")
check("and the other two sides alternate",
      [row[-1] for row in _dm.symbol()[1]][:4] == [False, True, False, True]
      and _dm.symbol()[1][0][:4] == [True, False, True, False])

check("only ECC 200 sizes exist, and every one's capacity matches its grid",
      all((rows - 2 * rr) * (cols - 2 * rc) // 8 == data + ecc
          for rows, cols, rr, rc, data, ecc, _b in zpl_dm.SIZES + zpl_dm.RECTANGULAR),
      [(r, c) for r, c, rr, rc, d, e, _b in zpl_dm.SIZES + zpl_dm.RECTANGULAR
       if (r - 2 * rr) * (c - 2 * rc) // 8 != d + e])
check("a rectangular symbol is one of the six ZPL offers",
      (lambda g: (len(g), len(g[0])))(
          zpl_dm.encode('ZEBRA TECH', rectangular=True)) in
      [(r, c) for r, c, *_rest in zpl_dm.RECTANGULAR])
check("forcing rows and columns takes the next size up, never a smaller one",
      len(zpl_dm.encode('AB', rows=26, columns=26)) == 26
      and len(zpl_dm.encode('AB')) < 26)
try:
    zpl_dm.encode('x' * 20, rows=10, columns=10)
    _too_small = False
except ValueError:
    _too_small = True
check("and data that will not fit a forced size is refused, not truncated",
      _too_small, "the manual: 'no symbol is printed'")

check("the encodation is whichever scheme is shortest for this data",
      len(zpl_dm.encode('ZEBRA TECHNOLOGIES CORPORATION'))
      < len(zpl_dm.encode('Zebra Technologies Corporation!')),
      "upper case packs three characters into two codewords; mixed case does not")

# ^BX's escapes, which the manual introduces with an underscore on current
# firmware and a tilde before it.
check("_d065 is the character with that decimal value, and __ a literal one",
      zpl_dm.encode('AB_d065CD', escape='_') == zpl_dm.encode('ABACD', escape='')
      and zpl_dm.encode('AB__CD', escape='_') == zpl_dm.encode('AB_CD', escape=''))
check("a trailing escape character with nothing after it is just a character",
      zpl_dm.encode('TRAILING_', escape='_') == zpl_dm.encode('TRAILING_', escape=''))

# ^BX with no module size of its own means "fit the symbol into ^BY's
# height", which cannot be known until the data has been encoded.
_sized = zpl_parser.parse_zpl(
    "^XA^PW600^LL600^FO20,20^BY3,3,240^BXN,,200^FDZEBRA TECHNOLOGIES^FS^XZ"
)[0].elements[0]
check("^BX with no module size takes ^BY's height, divided by its own rows",
      _sized.module_width == round(240 / len(_sized.symbol()[1]))
      and abs(_sized.height - 240) <= len(_sized.symbol()[1]),
      (_sized.module_width, _sized.height))
check("and the size it worked out is written back, not left to the printer",
      f"^BXN,{_sized.module_width},200" in _sized.to_zpl(),
      _sized.to_zpl().replace(chr(10), ' '))

_dm_round = BarcodeElement(1, 2, 60, 'ZEBRA TECH', symbology='datamatrix',
                           module_width=6, orientation='N',
                           params={'aspect': '2', 'quality_dm': '200'})
_dm_back = zpl_parser.parse_zpl(f"^XA{_dm_round.to_zpl()}^XZ")[0].elements[0]
check("^BX round-trips its shape and quality",
      (_dm_back.aspect, _dm_back.quality_dm, _dm_back.module_width) == (2, 200, 6),
      (_dm_back.aspect, _dm_back.quality_dm, _dm_back.module_width))
check("a Data Matrix has no interpretation line either",
      not _dm.show_text and geometry.barcode_layout(_dm)['text'] is None)
check("^BX is no longer a command a save would drop",
      workflow.unsupported_commands("^XA^FO0,0^BXN,8,200^FDHI^FS^XZ") == [])

# --- ^B7, PDF417 ------------------------------------------------------------
from zplcore import pdf417 as zpl_pdf417
from zplcore import pdf417_patterns as zpl_pdf417_patterns

# The low-level table is the standard's own, so it is checked against the
# standard's own rules rather than taken on trust: seventeen modules, eight
# elements alternating bar and space, none wider than six, and each pattern in
# the cluster whose number its bar widths give.
def _pattern_widths(value, bits=17):
    text = format(value, f'0{bits}b')
    runs, current, count = [], text[0], 0
    for bit in text:
        if bit == current:
            count += 1
        else:
            runs.append(count); current = bit; count = 1
    runs.append(count)
    return runs

_wrong = []
for _cluster, _patterns in enumerate(zpl_pdf417_patterns.PATTERNS):
    for _value, _pattern in enumerate(_patterns):
        _w = _pattern_widths(_pattern)
        _bars = _w[0::2]
        if (sum(_w) != 17 or len(_w) != 8 or max(_w) > 6
                or (_bars[0] - _bars[1] + _bars[2] - _bars[3]) % 9 != _cluster * 3):
            _wrong.append((_cluster, _value))
check("every one of the 2787 low-level patterns obeys the standard's rules",
      not _wrong and all(len(p) == 929 for p in zpl_pdf417_patterns.PATTERNS),
      _wrong[:4])

check("each of the four text submodes holds exactly thirty values",
      all(len(table) == 30 for table in
          (zpl_pdf417._UPPER, zpl_pdf417._LOWER, zpl_pdf417._MIXED,
           zpl_pdf417._PUNCT)),
      [len(t) for t in (zpl_pdf417._UPPER, zpl_pdf417._LOWER,
                        zpl_pdf417._MIXED, zpl_pdf417._PUNCT)])
check("and their last three values are switches, not characters",
      zpl_pdf417._UPPER[27:] == '\0\0\0' and zpl_pdf417._LOWER[27:] == '\0\0\0',
      "putting a full stop at 27 made a.b decode as aAk")

_pdf = BarcodeElement(0, 0, 3, 'PDF417 test', symbology='pdf417',
                      module_width=2)
_kind, _grid = _pdf.symbol()
check("PDF417 is a grid of rows, each drawn its own height in modules",
      _kind == 'grid' and len(_grid) % _pdf.bar_height == 0,
      (len(_grid), _pdf.bar_height))
check("every row is the same width, and starts and ends on a bar",
      len({len(row) for row in _grid}) == 1
      and all(row[0] and row[-1] for row in _grid))
check("a row is the start, an indicator, the data, an indicator and the stop",
      (len(_grid[0]) - zpl_pdf417.STOP_WIDTH) % zpl_pdf417.CODEWORD == 0,
      len(_grid[0]))

_trunc = BarcodeElement(0, 0, 3, 'PDF417 test', symbology='pdf417',
                        module_width=2, params={'truncate': 'Y'})
check("truncation drops the right indicator and the stop pattern",
      len(_trunc.symbol()[1][0]) < len(_grid[0])
      and len(_trunc.symbol()[1][0]) == len(_grid[0]) - 17 - 18 + 1,
      (len(_grid[0]), len(_trunc.symbol()[1][0])))

check("more security means more codewords, so more rows at the same width",
      len(BarcodeElement(0, 0, 3, 'PDF417 test', symbology='pdf417',
                         params={'security': '5'}).symbol()[1])
      > len(_grid))
check("the columns asked for are the columns drawn",
      len(BarcodeElement(0, 0, 3, 'PDF417 test', symbology='pdf417',
                         module_width=2, params={'columns': '5'}).symbol()[1][0])
      # start, left indicator, five data codewords, right indicator, stop -
      # and the stop is the one pattern that is eighteen modules, not seventeen
      == (5 + 3) * zpl_pdf417.CODEWORD + zpl_pdf417.STOP_WIDTH)

for params, why in (({'columns': '30', 'rows': '31'},
                     "thirty by thirty-one is over the 928 codeword limit"),
                    ({'columns': '1', 'rows': '3'},
                     "one column and three rows holds almost nothing")):
    _over = BarcodeElement(0, 0, 3, 'x' * 300, symbology='pdf417', params=params)
    check(f"a symbol that cannot be built draws nothing - {why}",
          _over.symbol() == ('grid', []) and _over.symbol_error,
          _over.symbol_error)

# ^B7's h is a row height in modules, not a length in dots - so ^BY's height
# divided by the rows is what an omitted one means, and a rescale must leave
# it alone or the module width it multiplies is counted twice.
_row_sized = zpl_parser.parse_zpl(
    "^XA^PW700^LL500^FO20,20^BY2,3,100^B7N^FDPDF417 test^FS^XZ")[0].elements[0]
check("^B7 with no row height divides ^BY's height by the rows it needs",
      abs(_row_sized.height - 100) <= _row_sized.module_width * 2
      and _row_sized.bar_height > 1,
      (_row_sized.bar_height, _row_sized.height))
_scaled_doc = zpl_parser.parse_zpl(
    "^XA^PW812^LL1218^FO20,20^BY2^B7N,4,3^FDPDF417^FS^XZ")[0]
_before = _scaled_doc.elements[0].bar_height
_scaled_doc.rescale(300 / 203)
check("a rescale leaves a row height in modules alone, and scales the module",
      _scaled_doc.elements[0].bar_height == _before
      and _scaled_doc.elements[0].module_width == 3,
      (_before, _scaled_doc.elements[0].bar_height,
       _scaled_doc.elements[0].module_width))

check("PDF417 writes a ^BY, whose module width it really is drawn at",
      '^BY3' in zpl_parser.parse_zpl(
          "^XA^PW700^LL500^FO20,20^BY3^B7N,8,5^FDHELLO^FS^XZ"
      )[0].elements[0].to_zpl(),
      "only the symbologies carrying their own w in the command leave it out")
check("^B7 is no longer a command a save would drop",
      workflow.unsupported_commands("^XA^FO0,0^B7N,3,5^FDHI^FS^XZ") == [])

# --- ^BF, MicroPDF417 -------------------------------------------------------
from zplcore import micropdf417 as zpl_micropdf417

# The manual's Table 10, p.107: each mode's columns, rows, share of error
# correction codewords, and the most upper-case letters and digits it holds.
# Four figures a mode, all checked, because the table's order is the
# standard's with one size moved - mode 33 is four by four - and because the
# digits only fit if a short run of them goes into numeric mode.
_TABLE_10 = (
    (1, 11, 64, 6, 8), (1, 14, 50, 12, 17), (1, 17, 41, 18, 26),
    (1, 20, 40, 22, 32), (1, 24, 33, 30, 44), (1, 28, 29, 38, 55),
    (2, 8, 50, 14, 20), (2, 11, 41, 24, 35), (2, 14, 32, 36, 52),
    (2, 17, 29, 46, 67), (2, 20, 28, 56, 82), (2, 23, 28, 64, 93),
    (2, 26, 29, 72, 105), (3, 6, 67, 10, 14), (3, 8, 58, 18, 26),
    (3, 10, 53, 26, 38), (3, 12, 50, 34, 49), (3, 15, 47, 46, 67),
    (3, 20, 43, 66, 96), (3, 26, 41, 90, 132), (3, 32, 40, 114, 167),
    (3, 38, 39, 138, 202), (3, 44, 38, 162, 237), (4, 6, 50, 22, 32),
    (4, 8, 44, 34, 49), (4, 10, 40, 46, 67), (4, 12, 38, 58, 85),
    (4, 15, 35, 76, 111), (4, 20, 33, 106, 155), (4, 26, 31, 142, 208),
    (4, 32, 30, 178, 261), (4, 38, 29, 214, 313), (4, 44, 28, 250, 366),
    (4, 4, 50, 14, 20))


def _micro_fits(data, mode):
    return (len(zpl_micropdf417.data_codewords(data))
            <= zpl_micropdf417.capacity(mode))


_table_wrong = []
for _mode, (_c, _r, _pct, _alpha, _digits) in enumerate(_TABLE_10):
    _cols, _rows, _ec = zpl_micropdf417.size(_mode)
    _letters = ('ABCDEFGHIJKLMNOPQRSTUVWXYZ' * 10)[:_alpha + 1]
    _numbers = ('1234567890' * 40)[:_digits + 1]
    if not ((_cols, _rows) == (_c, _r)
            and int(_ec * 100 / (_cols * _rows) + 0.5) == _pct
            and _micro_fits(_letters[:-1], _mode)
            and not _micro_fits(_letters, _mode)
            and _micro_fits(_numbers[:-1], _mode)
            and not _micro_fits(_numbers, _mode)):
        _table_wrong.append(_mode)
check("every ^BF mode is Table 10's size, correction and capacity exactly",
      zpl_micropdf417.MODES == 34 and not _table_wrong, _table_wrong)

# The row address patterns are ten modules, three bars and three spaces, a
# bar first - checked against the standard's rule, as PDF417's are.
_rap_wrong = [value for value in zpl_micropdf417._RAP_SIDE
              + zpl_micropdf417._RAP_CENTRE
              if len(_pattern_widths(value, 10)) != 6
              or not format(value, '010b').startswith('1')]
check("every one of the 104 row address patterns is three bars and three "
      "spaces in ten modules",
      len(zpl_micropdf417._RAP_SIDE) == len(zpl_micropdf417._RAP_CENTRE) == 52
      and not _rap_wrong, [hex(v) for v in _rap_wrong])

# Symbols zint drew (backend/pdf417.c, a separate implementation), one size of
# each column count, as each row's modules in hexadecimal.
_ZINT_MICRO = (
    (0, 'HELLO', ('3228632735', '3a2f94c7b5', '3b2fcdd795', '332cc90715',
                  '372f6d0615', '37a9e88635', '33ae391625', '3bab8be725',
                  '39acf897a5', '3daa8207ad', '3caf58c7a9')),
    (6, 'MICRO PDF', ('6450c648b60645', '745e902b0f9745', '7657046da0f765',
                      '6650c648632665', '6e5c134ec396e5', '6f5afd08c176f5',
                      '675b6c0b8f6675', '775c262fb21775')),
    (13, 'ZEBRA 13', ('322863259d39ecd20c645', '3a2eb0249de08aa7ee745',
                      '3b29e0a4dd2178df12765', '332f35c45dc34ce51c665',
                      '372df2f44dfb6e81796e5', '37ade9846d1cce8f226f5')),
    (33, 'LABEL', ('69d7eb093c14edd718f236691', '6998f92c7c94e98f92c7c96b1',
                   '68938e4c44c4c90cc2b3206b9', '68dc9eef4244cdd32097906bd')))
for _mode, _data, _rows_hex in _ZINT_MICRO:
    _ours = zpl_micropdf417.encode(_data, _mode)
    _width = zpl_micropdf417.dimensions(_mode)[0]
    check(f"^BF mode {_mode} draws {_data!r} module for module as zint does",
          [format(int(''.join('1' if b else '0' for b in row), 2), 'x')
           for row in _ours] == list(_rows_hex)
          and all(len(row) == _width for row in _ours),
          len(_ours))

# And every mode, filled to capacity with letters: a digest of zint's 34
# symbols, since 34 symbols of up to 44 rows are too many to spell out.
import hashlib as _hashlib
_micro_digest = _hashlib.sha256()
for _mode in range(zpl_micropdf417.MODES):
    _data = ('ABCDEFGHIJKLMNOPQRSTUVWXYZ' * 20)[
        :2 * (zpl_micropdf417.capacity(_mode) - 1)]
    for _row in zpl_micropdf417.encode(_data, _mode):
        _micro_digest.update(bytes(int(b) for b in _row) + b'\n')
check("all 34 modes, each full, are the symbols zint draws for the same data",
      _micro_digest.hexdigest()
      == '25e5d05763a3c8cf8016ee154825a4a3e6715a9f7ef5947e537fb6ca48a2d59b')

check("a message that opens with text latches into it: MicroPDF417 starts in "
      "byte mode",
      zpl_micropdf417.data_codewords('AB')[0] == 900
      and zpl_micropdf417.data_codewords('12345678')[0] == 902,
      (zpl_micropdf417.data_codewords('AB'),
       zpl_micropdf417.data_codewords('12345678')))

# The manual's own example: ^BY6^BFN,8,3. Its drawing has modules 6 dots
# wide and rows 8 tall, so h is each row's height in dots, not a multiple of
# the module as ^B7's is.
_bf = zpl_parser.parse_zpl(
    "^XA^PW812^LL1218^FO100,100^BY6^BFN,8,3^FDABCDEFGHIJKLMNOPQRSTUV^FS^XZ"
)[0].elements[0]
_bf_kind, _bf_rows = _bf.symbol()
check("^BF is read as a MicroPDF417: one column, twenty rows, for mode 3",
      _bf.symbology == 'micropdf417' and _bf.micro_mode == 3
      and _bf_kind == 'stacked' and len(_bf_rows) == 20
      and len(_bf_rows[0]) == 38 and not _bf.symbol_error,
      (_bf.symbology, _bf_kind, len(_bf_rows), _bf.symbol_error))
check("each row is h dots tall and each module ^BY's w dots wide",
      (_bf.width, _bf.height) == (38 * 6, 20 * 8)
      and _bf.module_width == 6 and _bf.bar_height == 8,
      (_bf.width, _bf.height))
check("the rectangles fill the footprint: rows h dots apart",
      max(y + h for _x, y, _w, h in geometry.barcode_rects(_bf)) == 160
      and {y % 8 for _x, y, _w, _h in geometry.barcode_rects(_bf)} == {0})
check("^BF writes itself back with the ^BY it is drawn at",
      "^FO100,100\n^BY6\n^BFN,8,3\n^FDABCDEFGHIJKLMNOPQRSTUV^FS"
      in _bf.to_zpl(), _bf.to_zpl().replace('\n', ' '))

for _source, _height, _why in (
        ("^BY2,3,30^BF", 30, "^BY's height, when ^BF leaves its own out"),
        ("^BF", 10, "10, the manual's figure when there is no ^BY either"),
        ("^BFN,,5", 10, "and the same with its mode given")):
    _read = zpl_parser.parse_zpl(
        f"^XA^PW812^LL1218^FO10,10{_source}^FDHELLO^FS^XZ")[0].elements[0]
    check(f"an omitted ^BF height is {_why}",
          _read.bar_height == _height, _read.bar_height)
check("and an omitted mode is 0, left off when written back",
      "^BF,30\n" in zpl_parser.parse_zpl(
          "^XA^PW812^LL1218^FO10,10^BY2,3,30^BF^FDHELLO^FS^XZ"
      )[0].elements[0].to_zpl())

_bf_turned = BarcodeElement(0, 0, 4, 'HELLO', module_width=2,
                            orientation='R', symbology='micropdf417',
                            params={'micro_mode': '33'})
check("a turned ^BF has its footprint turned",
      (_bf_turned.width, _bf_turned.height) == (4 * 4, 99 * 2),
      (_bf_turned.width, _bf_turned.height))

_bf_full = BarcodeElement(0, 0, 4, 'X' * 7, module_width=2,
                          symbology='micropdf417')
check("data too long for the mode draws nothing, and keeps that size's "
      "footprint",
      _bf_full.symbol() == ('stacked', []) and _bf_full.symbol_error
      and (_bf_full.width, _bf_full.height) == (38 * 2, 11 * 4),
      (_bf_full.symbol_error, _bf_full.width, _bf_full.height))

_bf_drag = BarcodeElement(0, 0, 4, 'HELLO', module_width=2,
                          symbology='micropdf417')
geometry._resize_barcode(_bf_drag, 38 * 3, 11 * 7)
check("a drag asks for a module width and a row height separately",
      (_bf_drag.module_width, _bf_drag.bar_height) == (3, 7),
      (_bf_drag.module_width, _bf_drag.bar_height))

_bf_doc = zpl_parser.parse_zpl(
    "^XA^PW812^LL1218^FO20,20^BY2^BFN,4,0^FDHELLO^FS^XZ")[0]
_bf_doc.rescale(300 / 203)
check("a rescale scales the row height, which is in dots, and the module",
      (_bf_doc.elements[0].module_width, _bf_doc.elements[0].bar_height)
      == (3, 6),
      (_bf_doc.elements[0].module_width, _bf_doc.elements[0].bar_height))
check("^BF is no longer a command a save would drop",
      workflow.unsupported_commands("^XA^FO0,0^BFN,8,3^FDHI^FS^XZ") == [])

# --- ^B0, Aztec Code --------------------------------------------------------
from zplcore import aztec as zpl_aztec

# The Reed-Solomon behind Aztec runs over four different fields depending on
# how big the symbol is, and a fifth for the mode message. A systematic
# codeword must vanish at each of the generator's roots, which is a check on
# all five that does not depend on drawing anything.
_rs_bad = []
for _width in (4, 6, 8, 10, 12):
    _exp, _log = zpl_aztec._field(_width)
    _mul = lambda a, b: 0 if a == 0 or b == 0 else _exp[_log[a] + _log[b]]
    _data = [(i * 37 + 5) % ((1 << _width) - 1) + 1 for i in range(6)]
    for _count in (3, 5, 8):
        _word = _data + zpl_aztec.error_codewords(_data, _count, _width)
        for _power in range(1, _count + 1):
            _total = 0
            for _value in _word:
                _total = _mul(_total, _exp[_power]) ^ _value
            if _total:
                _rs_bad.append((_width, _count, _power))
check("every Aztec field's Reed-Solomon leaves no syndrome behind",
      not _rs_bad, _rs_bad[:4])

# No codeword may be all ones or all zeros, since a reader uses those to find
# its way. Getting the stuffing wrong shifts every bit after it, which is a
# symbol that decodes to nothing at all.
for _width in (6, 8, 10, 12):
    _stuffed = zpl_aztec._stuffed([0] * (_width * 4), _width)
    _words = [_stuffed[i:i + _width] for i in range(0, len(_stuffed), _width)]
    check(f"a run of zeros is stuffed at width {_width}",
          all(any(word) for word in _words) and len(_stuffed) > _width * 4,
          (len(_stuffed), _width * 4))
    _stuffed = zpl_aztec._stuffed([1] * (_width * 4), _width)
    _words = [_stuffed[i:i + _width] for i in range(0, len(_stuffed), _width)]
    check(f"and a run of ones is too, at width {_width}",
          all(not all(word) for word in _words))

_az = BarcodeElement(0, 0, 60, 'Aztec test', symbology='aztec', module_width=6)
check("an Aztec symbol is a square grid of odd size, so it has a centre",
      _az.symbol()[0] == 'grid' and len(_az.symbol()[1]) % 2 == 1
      and len(_az.symbol()[1]) == len(_az.symbol()[1][0]),
      len(_az.symbol()[1]))
_centre = len(_az.symbol()[1]) // 2
check("its bullseye is rings of alternating dark and light about that centre",
      _az.symbol()[1][_centre][_centre]
      and not _az.symbol()[1][_centre][_centre + 1]
      and _az.symbol()[1][_centre][_centre + 2]
      and not _az.symbol()[1][_centre][_centre + 3]
      and _az.symbol()[1][_centre][_centre + 4])
check("and its box is that grid at the magnification the command gives",
      (_az.width, _az.height) == (len(_az.symbol()[1]) * 6,) * 2,
      (_az.width, _az.height))

# ^B0's d carries two different things in one number.
for value, shape in ((0, (0, None)), (50, (0, None)), (101, (1, True)),
                     (104, (4, True)), (201, (1, False)), (232, (32, False))):
    _el = BarcodeElement(0, 0, 60, 'x', symbology='aztec',
                         params={'aztec_size': str(value)})
    check(f"^B0's d of {value} asks for layers {shape[0]}, compact {shape[1]}",
          _el._aztec_shape()[:2] == shape, _el._aztec_shape())
check("a percentage asks for more correction, not a bigger symbol outright",
      BarcodeElement(0, 0, 60, 'x', symbology='aztec',
                     params={'aztec_size': '95'})._aztec_shape()[2] == 95)
check("forcing four compact layers gives a symbol of exactly that size",
      len(BarcodeElement(0, 0, 60, 'x', symbology='aztec',
                         params={'aztec_size': '104'}).symbol()[1]) == 11 + 4 * 4)

_rune = BarcodeElement(0, 0, 60, '42', symbology='aztec',
                       params={'aztec_size': '300'})
check("an Aztec Rune is carried but not drawn - it holds a number, not a message",
      _rune.symbol() == ('grid', []) and 'Rune' in (_rune.symbol_error or ''),
      _rune.symbol_error)

# ^BO is ^B0 spelled with the letter, which the manual lists twice.
_alias = zpl_parser.parse_zpl(
    "^XA^PW700^LL500^FO20,20^BON,4^FDalias^FS^XZ")[0].elements[0]
check("^BO reads as the same symbology as ^B0",
      _alias.symbology == 'aztec' and _alias.module_width == 4)
check("and comes back spelled ^B0, the one this designer writes",
      '^B0N,4' in _alias.to_zpl(), _alias.to_zpl().replace(chr(10), ' '))

_az_round = zpl_parser.parse_zpl(
    "^XA^PW700^LL500^FO20,20^B0R,7,N,0,N,1,0^FDlabel 7^FS^XZ")[0].elements[0]
check("the manual's own ^B0 example round-trips every parameter",
      '^B0R,7,N,0,N,1,0' in _az_round.to_zpl(),
      _az_round.to_zpl().replace(chr(10), ' '))
check("^B0 carries no ^BY, whose module width it is not drawn at",
      '^BY' not in _az_round.to_zpl())
for command in ('^B0', '^BO'):
    check(f"{command} is no longer a command a save would drop",
          workflow.unsupported_commands(
              f"^XA^FO0,0{command}N,4^FDHI^FS^XZ") == [])

# --- ^BD, UPS MaxiCode -----------------------------------------------------
from zplcore import fields as zpl_fields, maxicode as zpl_maxicode
from zplcore.maxicode_map import MODULE_BITS as _MAXI_BITS, DARK as _MAXI_DARK
from PySide2.QtWidgets import (QComboBox as _QComboBox,
                               QDialogButtonBox as _QDialogButtonBox,
                               QLineEdit as _QLineEdit,
                               QPushButton as _QPushButton)

# The manual's own example: a US parcel's sorting code - class of service 001,
# country 840, ZIP 15238-2802 - and then the UPS message, fields separated by
# GS, formats by RS and ended with EOT, all written as ^FH escapes.
_UPS = ("001840152382802[)>_1E01_1D961Z00004951_1DUPSN_1D_06X610_1D159_1D1234567"
        "_1D1/1_1D_1DY_1D634 ALPHA DR_1DPITTSBURGH_1DPA_1E_04")
_UPS_LABEL = f"^XA^PW812^LL1218^FO50,50^BD^FH^FD{_UPS}^FS^XZ"

# Every one of the 864 codeword bits has exactly one module, and the modules
# a reader orients itself by are none of them.
_placed = sorted(bit for row in _MAXI_BITS for bit in row if bit >= 0)
check("the MaxiCode module map places every codeword bit exactly once",
      _placed == list(range(864)) and len(_MAXI_BITS) == 33
      and all(len(row) == 30 for row in _MAXI_BITS))
check("and the thirteen orientation and filler modules are always dark",
      sum(row.count(_MAXI_DARK) for row in _MAXI_BITS) == 13
      and all(zpl_maxicode.encode('X', 4)[r][c]
              for r, row in enumerate(_MAXI_BITS)
              for c, bit in enumerate(row) if bit == _MAXI_DARK))

# The primary's correction covers its ten codewords; the secondary's is split
# between its odd and even codewords, twenty each in the standard modes and
# twenty-eight in mode 5. Each must vanish at every root of its generator.
_exp6, _log6 = zpl_aztec._field(6)


def _syndromes(word, count):
    bad = []
    for power in range(1, count + 1):
        total = 0
        for value in word:
            total = (0 if total == 0 else _exp6[_log6[total] + power]) ^ value
        if total:
            bad.append(power)
    return bad


for _mode, _correction in ((2, 40), (3, 40), (4, 40), (5, 56), (6, 40)):
    _data = {2: zpl_fields.decode_hex(_UPS, '_'),
             3: "066826ABC123[)>\x1e01\x1d96INTL\x1e\x04"}.get(
                 _mode, "MAXICODE 123456789 mixed Case, Été")
    _words = zpl_maxicode.codewords(_data, _mode)
    _body = _words[20:]
    check(f"mode {_mode}: 144 codewords whose three corrections all check out",
          len(_words) == 144 and not _syndromes(_words[:20], 10)
          and not _syndromes(_body[0::2], _correction // 2)
          and not _syndromes(_body[1::2], _correction // 2),
          (_syndromes(_words[:20], 10), _syndromes(_body[0::2], _correction // 2),
           _syndromes(_body[1::2], _correction // 2)))


# The sorting code is packed as bits across the primary's first ten
# codewords, in an order the standard fixes. Read back through those bit
# numbers - 1 is the top bit of codeword 0 - it has to be what was written.
def _primary_bits(words, positions):
    value = 0
    for position in positions:
        index = position - 1
        value = (value << 1) | ((words[index // 6] >> (5 - index % 6)) & 1)
    return value


_POSTCODE = (33, 34, 35, 36, 25, 26, 27, 28, 29, 30, 19, 20, 21, 22, 23, 24,
             13, 14, 15, 16, 17, 18, 7, 8, 9, 10, 11, 12, 1, 2)
_COUNTRY = (53, 54, 43, 44, 45, 46, 47, 48, 37, 38)
_SERVICE = (55, 56, 57, 58, 59, 60, 49, 50, 51, 52)
_us = zpl_maxicode.codewords(zpl_fields.decode_hex(_UPS, '_'), 2)
check("mode 2 packs the ZIP+4, the country and the class of service",
      (_us[0] & 0x0F, _primary_bits(_us, _POSTCODE),
       _primary_bits(_us, (39, 40, 41, 42, 31, 32)),
       _primary_bits(_us, _COUNTRY), _primary_bits(_us, _SERVICE))
      == (2, 152382802, 9, 840, 1),
      (_us[0] & 0x0F, _primary_bits(_us, _POSTCODE), _primary_bits(_us, _COUNTRY),
       _primary_bits(_us, _SERVICE)))
_intl = zpl_maxicode.codewords("066826ab1 c2", 3)
_letters = [_primary_bits(_intl, bits) for bits in (
    (39, 40, 41, 42, 31, 32), (33, 34, 35, 36, 25, 26), (27, 28, 29, 30, 19, 20),
    (21, 22, 23, 24, 13, 14), (15, 16, 17, 18, 7, 8), (9, 10, 11, 12, 1, 2))]
check("mode 3 packs six letters and digits, upper-cased, in code set A",
      (_intl[0] & 0x0F, ''.join(zpl_maxicode.CODE_SETS['A'][v] for v in _letters),
       _primary_bits(_intl, _COUNTRY), _primary_bits(_intl, _SERVICE))
      == (3, 'AB1 C2', 826, 66),
      (_letters, _primary_bits(_intl, _COUNTRY), _primary_bits(_intl, _SERVICE)))

# Structured append is a PAD and one codeword - which symbol of how many - at
# the head of the message, and only when there is more than one symbol.
_sa = zpl_maxicode.codewords("PART", 4, 3, 5)
check("symbol 3 of 5 opens its message with PAD and (3-1)<<3 | (5-1)",
      _sa[1:3] == [33, (2 << 3) | 4], _sa[:4])
check("and a symbol that is one of one says nothing of the kind",
      zpl_maxicode.codewords("PART", 4, 1, 1)[1] != 33)

# Nine digits in a row go in as one number, five codewords after an NS, which
# is a third of what writing them out costs.
check("nine digits are packed behind a Numeric Shift",
      zpl_maxicode.codewords("123456789", 4)[1:7]
      == [31] + [(123456789 >> s) & 0x3F for s in (24, 18, 12, 6, 0)],
      zpl_maxicode.codewords("123456789", 4)[1:7])
check("every character from 0 to 255 has a code set that carries it",
      all(chr(c) in zpl_maxicode._WHERE for c in range(256)))
try:
    zpl_maxicode.codewords("x" * 100, 5)
    _too_long = None
except ValueError as exc:
    _too_long = str(exc)
check("a message too long for the mode says so rather than truncating",
      _too_long is not None and 'mode 5 holds 77' in _too_long, _too_long)

# Reading it: the manual's example, with no orientation, height, magnification
# or ^BY, comes back written the same way.
_maxi = zpl_parser.parse_zpl(_UPS_LABEL)[0].elements
check("^BD is read as a MaxiCode barcode, mode 2 by default",
      len(_maxi) == 1 and _maxi[0].symbology == 'maxicode'
      and (_maxi[0].maxi_mode, _maxi[0].symbol_number, _maxi[0].symbol_count)
      == (2, 1, 1) and _maxi[0].symbol_error is None,
      [(e.symbology, getattr(e, 'symbol_error', None)) for e in _maxi])
_maxi_zpl = _maxi[0].to_zpl() if _maxi else ''
check("and written back as ^BD and the ^FH data it came with, no ^BY",
      f"^BD\n^FH_^FD{_UPS}^FS" in _maxi_zpl and '^BY' not in _maxi_zpl,
      _maxi_zpl.replace('\n', ' '))
_maxi_out = zpl_parser.parse_zpl(_UPS_LABEL)[0].to_zpl()
check("a label holding one round-trips unchanged",
      zpl_parser.parse_zpl(_maxi_out)[0].to_zpl() == _maxi_out)
check("^BD is no longer a command a save would drop",
      workflow.unsupported_commands(_UPS_LABEL) == [],
      workflow.unsupported_commands(_UPS_LABEL))
_three = zpl_parser.parse_zpl("^XA^FO10,10^BD3,2,4^FDx^FS^XZ")[0].elements[0]
check("every parameter is kept: mode 3, symbol 2 of 4",
      (_three.maxi_mode, _three.symbol_number, _three.symbol_count) == (3, 2, 4)
      and "^BD3,2,4\n" in _three.to_zpl(), _three.to_zpl().replace('\n', ' '))
_lettered = zpl_parser.parse_zpl("^XA^FO10,10^BDN,2,3^FDx^FS^XZ")[0].elements[0]
check("an orientation letter written out of habit is an unreadable mode, so 2",
      (_lettered.maxi_mode, _lettered.symbol_number, _lettered.symbol_count)
      == (2, 2, 3), (_lettered.maxi_mode, _lettered.symbol_number))
_glued = zpl_parser.parse_zpl("^XA^FO10,10^BD4,3,5^FDx^FS^XZ")[0].elements[0]
check("and a glued first parameter is not split as if it were one",
      (_glued.maxi_mode, _glued.symbol_number, _glued.symbol_count) == (4, 3, 5))
_turned = zpl_parser.parse_zpl(
    "^XA^FWR^FO10,10^BD4^FDturn me^FS^XZ")[0].elements[0]
check("^FW does not turn a MaxiCode, which has no orientation to take",
      _turned.orientation == '' and not _turned.rotated()
      and (_turned.width, _turned.height) == zpl_maxicode.size(203),
      (_turned.orientation, _turned.width, _turned.height))

# One size on paper, whatever the head: 28.14 mm across.
for _dpi, _dots in ((203, (225, 213)), (300, (333, 315)), (600, (665, 630))):
    _sized = zpl_parser.parse_zpl(
        f"^XA^FXDESIGNER_DPI:{_dpi}\n^FO10,10^BD4^FDsize^FS^XZ")[0].elements[0]
    check(f"at {_dpi} dpi a MaxiCode is {_dots[0]} x {_dots[1]} dots, "
          f"28.14 mm across",
          (_sized.width, _sized.height) == _dots
          and abs(_sized.width / _dpi * 25.4 - 28.14) < 0.15,
          (_sized.width, _sized.height))
_bad = BarcodeElement(0, 0, 60, '12345', symbology='maxicode')
check("a mode 2 message with no 15-digit sorting code draws nothing, and says "
      "why, over the ground the symbol will take",
      _bad.symbol()[1] == [] and 'mode 2' in (_bad.symbol_error or '')
      and (_bad.width, _bad.height) == zpl_maxicode.size(203),
      (_bad.symbol_error, _bad.width, _bad.height))
check("the rectangles are one dot tall and inside the symbol",
      all(h == 1 and 0 <= x and x + w <= 225 and 0 <= y < 213
          for x, y, w, h in geometry.barcode_rects(_maxi[0])))

# Following the printer: kept dots, rescaled dots and undo all leave it the
# same size on paper, since that is the only size a printer draws it at.
_kept = zpl_parser.parse_zpl(_UPS_LABEL)[0]
_before = _kept.snapshot()
workflow.reconcile_dpi(_kept, 300, lambda *a: 'keep')
check("keeping the dots on a 300 dpi printer re-draws it at 300 dpi",
      (_kept.elements[0].dpi, box_of(_kept.elements[0])) == (300, (50, 50, 333, 315)),
      (_kept.elements[0].dpi, box_of(_kept.elements[0])))
_kept.restore(_before)
check("and an undo across the change brings it back at the new resolution",
      (_kept.elements[0].dpi, box_of(_kept.elements[0])) == (300, (50, 50, 333, 315)),
      (_kept.elements[0].dpi, box_of(_kept.elements[0])))
_scaled = zpl_parser.parse_zpl(_UPS_LABEL)[0]
workflow.reconcile_dpi(_scaled, 600, lambda *a: 'rescale')
check("rescaling to 600 dpi moves it and re-draws it, not scales it",
      box_of(_scaled.elements[0]) == (148, 148, 665, 630)
      and '^BD\n' in _scaled.to_zpl() and '^BY' not in _scaled.to_zpl(),
      box_of(_scaled.elements[0]))
_new_doc = Document(812, 1218, dpi=300)
check("a barcode added to a 300 dpi label is drawn for it",
      _new_doc.add_barcode_element().dpi == 300)
# A paste reads its elements through the parser, which draws them for the
# resolution the text names - or 203 when it names none. Either way they land
# on this label, so a MaxiCode is re-drawn for this label's head.
_copied_from = zpl_parser.parse_zpl(_UPS_LABEL)[0]
_copied_from.selection = list(_copied_from.elements)
for _how, _text in (("copied from a 203 dpi label", _copied_from.copy_zpl()),
                    ("from ZPL that names no resolution", _UPS_LABEL)):
    _into = Document(1200, 1800, dpi=300)
    _into.paste_zpl(_text)
    check(f"a MaxiCode pasted into a 300 dpi label {_how} is drawn for it",
          (_into.elements[0].dpi, _into.elements[0].width,
           _into.elements[0].height) == (300, 333, 315),
          (_into.elements[0].dpi, _into.elements[0].width,
           _into.elements[0].height))

# No handles, and nothing a drag or a group resize could ask of it.
_fixed_doc = zpl_parser.parse_zpl(_UPS_LABEL)[0]
_fixed = _fixed_doc.elements[0]
_fixed_doc.selection = [_fixed]
check("a MaxiCode offers no resize handles",
      _fixed_doc.resize_target() is None and not _fixed.resizable)
geometry.resize_by_handle(_fixed_doc, _fixed, 'br', 60, 40)
check("and a drag at where one would be changes nothing",
      box_of(_fixed) == (50, 50, 225, 213), box_of(_fixed))
geometry.scale_element(_fixed_doc, _fixed, 0, 0, 2.0, 2.0)
check("a group scaled round it moves it and leaves its size alone",
      box_of(_fixed) == (100, 100, 225, 213), box_of(_fixed))

# Made a MaxiCode in an editor, a barcode drops what ^BD cannot say.
_switched = BarcodeElement(10, 10, 80, 'ABC', orientation='R')
_switched.symbology = 'maxicode'
_switched.maxi_mode = 4
_switched.sync_box()
check("a rotated Code 128 made a MaxiCode is no longer turned or lined",
      (_switched.orientation, _switched.show_text, box_of(_switched))
      == ('', False, (10, 10, 225, 213)),
      (_switched.orientation, _switched.show_text, box_of(_switched)))
check("and is written as ^BD4 with no ^BY",
      "^BD4\n^FDABC^FS" in _switched.to_zpl() and '^BY' not in _switched.to_zpl(),
      _switched.to_zpl().replace('\n', ' '))

# The Insert buttons' one rule.
check("an escape goes in at the cursor, switching ^FH on",
      zpl_fields.insert_escape('AB', 1, 0x1D) == ('A_1DB', 4, '_'))
check("switching ^FH on escapes the underscores already typed, and the cursor "
      "moves with them",
      zpl_fields.insert_escape('A_B', 2, 0x04) == ('A_5F_04B', 7, '_'))
check("a field that has ^FH keeps its own indicator",
      zpl_fields.insert_escape('A_1DB', 5, 0x1E, '_') == ('A_1DB_1E', 8, '_')
      and zpl_fields.insert_escape('X', 1, 0x1D, '\\') == ('X\\1D', 4, '\\'))

# Edit Barcode: MaxiCode's own rows, and the buttons, in the Qt dialog.
_qt_maxi = BarcodeElement(40, 40, 80, 'A_B', orientation='R')
_qt_accepted = []
_qt_dialog = qt_dialogs.edit_barcode_dialog(
    None, _qt_maxi, on_accept=lambda: _qt_accepted.append(True))
_qt_symbology = _qt_dialog.findChild(_QComboBox, 'symbology')
_qt_symbology.setCurrentIndex(_qt_symbology.findData('maxicode'))
_qt_orientation = _qt_dialog.findChild(_QComboBox, 'orientation')
_qt_insert = _qt_dialog.findChild(_QPushButton, 'insert_GS')
check("Edit Barcode hides Orientation for a MaxiCode and shows Insert",
      not _qt_orientation.isVisibleTo(_qt_dialog)
      and _qt_insert.isVisibleTo(_qt_dialog))
_qt_value = _qt_dialog.findChild(_QLineEdit, 'value')
_qt_value.setCursorPosition(3)
_qt_insert.click()
_qt_dialog.findChild(_QPushButton, 'insert_EOT').click()
check("Insert GS then EOT write their escapes at the cursor",
      _qt_value.text() == 'A_5FB_1D_04' and _qt_value.cursorPosition() == 11,
      (_qt_value.text(), _qt_value.cursorPosition()))
_qt_maxi.hex_indicator = None
_qt_dialog.findChild(_QDialogButtonBox).button(_QDialogButtonBox.Ok).click()
check("OK makes it a MaxiCode with ^FH on, unturned",
      _qt_accepted and _qt_maxi.symbology == 'maxicode'
      and _qt_maxi.hex_indicator == '_' and _qt_maxi.orientation == ''
      and "^BD\n^FH_^FDA_5FB_1D_04^FS" in _qt_maxi.to_zpl(),
      _qt_maxi.to_zpl().replace('\n', ' '))
_qt_cancelled = BarcodeElement(40, 40, 80, 'AB')
_qt_cancel = qt_dialogs.edit_barcode_dialog(None, _qt_cancelled)
_qt_cancel.findChild(_QPushButton, 'insert_RS').click()
_qt_cancel.findChild(_QDialogButtonBox).button(_QDialogButtonBox.Cancel).click()
check("Cancel leaves both the value and ^FH as they were",
      (_qt_cancelled.barcode_value, _qt_cancelled.hex_indicator) == ('AB', None))
_qt_code128 = qt_dialogs.edit_barcode_dialog(None, BarcodeElement(0, 0, 80, 'AB'))
check("and a Code 128 keeps its Orientation row and has no Insert",
      _qt_code128.findChild(_QComboBox, 'orientation').isVisibleTo(_qt_code128)
      and not _qt_code128.findChild(_QPushButton, 'insert_GS').isVisibleTo(_qt_code128))
# Closed, or the checks that drive the next dialog by finding whichever one is
# on screen would find this one.
_qt_code128.findChild(_QDialogButtonBox).button(_QDialogButtonBox.Cancel).click()

# Edit Barcode: MicroPDF417's Size row, and a row height under the 20 dots a
# linear barcode's row starts at, which OK used to clamp up to 20.
_qt_micro = BarcodeElement(40, 40, 4, 'HELLO', module_width=2,
                           symbology='micropdf417',
                           params={'micro_mode': '33'})
_qt_micro_dialog = qt_dialogs.edit_barcode_dialog(None, _qt_micro)
_qt_size = _qt_micro_dialog.findChild(_QComboBox, 'micro_mode')
check("Edit Barcode offers a MicroPDF417 its Size, set to its own mode",
      _qt_size.isVisibleTo(_qt_micro_dialog) and _qt_size.currentData() == 33
      and _qt_size.count() == 34,
      (_qt_size.currentData(), _qt_size.count()))
_qt_size.setCurrentIndex(_qt_size.findData(18))
_qt_micro_dialog.findChild(_QDialogButtonBox).button(_QDialogButtonBox.Ok).click()
check("OK makes it mode 18 and keeps its rows 4 dots tall",
      (_qt_micro.micro_mode, _qt_micro.bar_height) == (18, 4)
      and "^BY2\n^BFN,4,18\n" in _qt_micro.to_zpl(),
      _qt_micro.to_zpl().replace('\n', ' '))
_qt_pdf = BarcodeElement(40, 40, 3, 'HELLO', symbology='pdf417')
_qt_pdf_dialog = qt_dialogs.edit_barcode_dialog(None, _qt_pdf)
_qt_pdf_dialog.findChild(_QDialogButtonBox).button(_QDialogButtonBox.Ok).click()
check("and a PDF417's row height of 3 modules survives an OK unchanged",
      _qt_pdf.bar_height == 3, _qt_pdf.bar_height)

# --- ^BR, the GS1 DataBar family and its relations --------------------------
from zplcore import databar as zpl_databar

# ^BR's field data is the linear value, a bar, and a composite component that
# would print above it. Only the linear part is drawn; the rest round-trips.
check("^BR splits its field data at the bar the manual uses",
      zpl_databar.split('12345678901|this is composite info')
      == ('12345678901', 'this is composite info'),
      zpl_databar.split('12345678901|this is composite info'))
check("and a field with no bar in it is all linear",
      zpl_databar.split('12345678901') == ('12345678901', ''))

# Six of the twelve are not DataBar at all, and are drawn by the encoders
# those symbologies already have.
for kind, value, modules in (('upca', '12345678901', 95), ('upce', '4210000526', 51),
                             ('ean13', '400638133393', 95), ('ean8', '9638507', 67)):
    _el = BarcodeElement(0, 0, 60, value + '|composite', symbology='databar',
                         params={'databar_type': str(
                             [k for k, v in zpl_databar.TYPES.items() if v == kind][0])})
    check(f"^BR's {kind} is the same {modules} modules that symbology always was",
          sum(_el.modules()) == modules, sum(_el.modules()))
    check(f"and its composite half is not drawn into the {kind} symbol",
          _el.modules() == BarcodeElement(0, 0, 60, value, symbology='databar',
                                          params={'databar_type': str(
                                              [k for k, v in zpl_databar.TYPES.items()
                                               if v == kind][0])}).modules())

_gs1 = BarcodeElement(0, 0, 60, '0112345678901231', symbology='databar',
                      params={'databar_type': '11'})
_plain = BarcodeElement(0, 0, 60, '0112345678901231', symbology='code128',
                        options=('Y', 'N', 'N', 'A'))
check("^BR's types 11 and 12 are GS1-128: Code 128 with an FNC1 in front",
      sum(_gs1.modules()) == sum(_plain.modules()) + 11,
      (sum(_gs1.modules()), sum(_plain.modules())))
check("and the FNC1 is what a reader takes as an application identifier",
      code128.FNC1 == 102)

# The six that are DataBar are carried but not drawn.
for number in ('1', '2', '3', '4', '5', '6'):
    _not_yet = BarcodeElement(0, 0, 60, '12345678901|composite',
                              symbology='databar',
                              params={'databar_type': number})
    check(f"^BR type {number} keeps its footprint and says it is not drawn",
          _not_yet.symbol() == ('linear', []) and _not_yet.symbol_error
          and 'DataBar' in _not_yet.symbol_error and _not_yet.width > 0,
          (_not_yet.symbol_error or '')[:60])

_br = zpl_parser.parse_zpl(
    "^XA^PW700^LL500^FO10,10^BRN,7,5,2,100"
    "^FD12345678901|this is composite info^FS^XZ")[0].elements[0]
check("^BR round-trips every parameter, and the composite half of its data",
      '^BRN,7,5,2,100' in _br.to_zpl()
      and _br.barcode_value == '12345678901|this is composite info',
      _br.to_zpl().replace(chr(10), ' '))
check("^BR carries its own magnification, so it writes no ^BY",
      '^BY' not in _br.to_zpl() and _br.module_width == 5)
check("^BR is no longer a command a save would drop",
      workflow.unsupported_commands(
          "^XA^FO0,0^BRN,7,3^FD12345678901^FS^XZ") == [])

# --- ^BQ, the QR code -------------------------------------------------------
from zplcore import qr as zpl_qr

# Most of what ^BQ encodes is in the field data, not the command: the manual's
# own examples put the error correction, the input mode and the character mode
# in front of the value, and the value itself starts after them.
for data, expect in (
        # the manual's Example 2: standard reliability, manual, alphanumeric
        ('MM,AAC-42', ('M', 'M', None, [('A', 'AC-42')])),
        # its Example 1: high reliability, automatic
        ('QA,0123456789ABCD 2D code',
         ('Q', 'A', None, [(None, '0123456789ABCD 2D code')])),
        # manual input, numeric
        ('HM,N123456789012345', ('H', 'M', None, [('N', '123456789012345')])),
        # mixed mode: code 03 of 04 divisions, parity 8F, three segments
        ('D03048F,LM,N0123456789,A12AABB,B0006qrcode',
         ('L', 'M', ('03', '04', '8F'),
          [('N', '0123456789'), ('A', '12AABB'), ('B', 'qrcode')])),
        # a field with no switches at all is the whole value, automatic
        ('www.example.com', (None, 'A', None, [(None, 'www.example.com')]))):
    read = zpl_qr.read_switches(data)
    check(f"^BQ reads the switches in {data!r}",
          (read['ecc'], read['input'], read['mixed'], read['segments']) == expect,
          read)

check("a value that only looks like a switch keeps its first characters",
      zpl_qr.read_switches('HELLO')['segments'] == [(None, 'HELLO')],
      zpl_qr.read_switches('HELLO'))
check("the switch's own error correction wins over the command's",
      zpl_qr.error_correction('HM,Nx', 'L') == 'H',
      "the mandatory one is the switch; a file that spells both means it")
check("an omitted level is Q and an unreadable one is M, which are different",
      (zpl_qr.error_correction('x', ''), zpl_qr.error_correction('x', 'Z'))
      == ('Q', 'M'),
      "the manual is explicit: 'Q = if empty, M = invalid values'")

_qr = BarcodeElement(0, 0, barcode_value='MM,AAC-42', symbology='qr',
                     module_width=10)
check("the manual's own example is a 21-module symbol",
      _qr.symbol()[0] == 'grid' and len(_qr.symbol()[1]) == 21
      and len(_qr.symbol()[1][0]) == 21, len(_qr.symbol()[1]))
check("and its box is the grid at the magnification, square",
      (_qr.width, _qr.height) == (210, 210), (_qr.width, _qr.height))
check("a QR code has no interpretation line to draw its own switches in",
      not _qr.show_text and _qr.text_height() == 0
      and geometry.barcode_layout(_qr)['text'] is None)
check("more data needs a bigger grid, at the same magnification",
      len(BarcodeElement(0, 0, barcode_value='QA,' + 'x' * 200,
                         symbology='qr').symbol()[1]) > 21)
check("a higher error correction level needs a bigger grid than a lower one",
      len(BarcodeElement(0, 0, barcode_value='HA,' + 'x' * 100,
                         symbology='qr').symbol()[1])
      > len(BarcodeElement(0, 0, barcode_value='LA,' + 'x' * 100,
                           symbology='qr').symbol()[1]))

# ^BQ's magnification has no ^BY to fall back on, so an omitted one is the
# manual's own default for the resolution the label was made for.
for dpi, expect in ((150, 1), (203, 2), (300, 3), (600, 6)):
    page = (f"^XA^PW400^LL400^FXDESIGNER_DPI:{dpi}\n"
            f"^FO20,20^BQ^FDLA,hi^FS^XZ")
    read = zpl_parser.parse_zpl(page)[0].elements[0]
    check(f"an omitted magnification at {dpi} dpi is {expect}",
          read.module_width == expect, read.module_width)
    check(f"and it is written back, not left for the printer to guess at {dpi}",
          f",{expect}" in read.to_zpl().split('^BQ')[1].split(chr(10))[0],
          read.to_zpl().replace(chr(10), ' '))

_qr_round = BarcodeElement(1, 2, barcode_value='MM,AAC-42', symbology='qr',
                           module_width=10, orientation='N',
                           params={'quality': 'H', 'qr_model': '1',
                                   'qr_mask': '3'})
check("^BQ writes every parameter it was given, in the manual's order",
      '^BQN,1,10,H,3\n' in _qr_round.to_zpl(), _qr_round.to_zpl())
_qr_back = zpl_parser.parse_zpl(f"^XA{_qr_round.to_zpl()}^XZ")[0].elements[0]
check("and reads them all back",
      (_qr_back.symbology, _qr_back.quality, _qr_back.qr_model,
       _qr_back.qr_mask, _qr_back.module_width, _qr_back.barcode_value)
      == ('qr', 'H', 1, 3, 10, 'MM,AAC-42'),
      (_qr_back.quality, _qr_back.qr_model, _qr_back.qr_mask))
check("the field data round-trips byte for byte, switches and all",
      _qr_back.barcode_value == 'MM,AAC-42',
      "the switches are the encoder's business, never the parser's")

check("a defaulted ^BQ writes only what it must",
      BarcodeElement(1, 2, barcode_value='x', symbology='qr',
                     module_width=2).to_zpl().split(chr(10))[1] == '^BQ,2,2',
      BarcodeElement(1, 2, barcode_value='x', symbology='qr',
                     module_width=2).to_zpl().replace(chr(10), ' '))
check("^BQ carries no ^BY, whose module width it would not be drawn at",
      '^BY' not in BarcodeElement(0, 0, barcode_value='x', symbology='qr').to_zpl())

check("a QR code resized by a handle stays square",
      _qr_square() == (True, True), _qr_square())

check("^BQ is no longer reported as a command a save would drop",
      workflow.unsupported_commands("^XA^FO0,0^BQ,2,4^FDMM,AHI^FS^XZ") == [])
# Data no QR version can hold draws nothing and says why, the way a printer
# prints no symbol - rather than losing every other field on the label.
_qr_huge = BarcodeElement(0, 0, barcode_value='HA,' + 'x' * 5000, symbology='qr')
check("data too long for any version draws nothing and keeps a footprint",
      _qr_huge.symbol() == ('grid', []) and _qr_huge.symbol_error
      and _qr_huge.width > 0 and geometry.barcode_layout(_qr_huge)['rects'] == [],
      _qr_huge.symbol_error)

# --- every symbology reaches the canvas as plain rectangles ------------------

# The preview and both canvases draw `rects` and nothing else, so a symbology
# they cannot walk the bar-and-space widths of - a matrix code, a postal code -
# reaches all three without any of them learning a second shape.
for symbology, value in (('code128', '12345'), ('code39', 'AB'),
                         ('ean13', '400638133393'), ('interleaved2of5', '1234'),
                         ('upcean_extension', '12')):
    el = BarcodeElement(0, 0, 60, value, module_width=3, symbology=symbology)
    layout = geometry.barcode_layout(el)
    mods = el.modules()
    # What the three sinks used to compute for themselves, from the widths.
    # A symbology whose line prints above the bars - the UPC/EAN extension -
    # starts them that far down, which is the offset barcode_layout applies.
    top = el.text_height() if el.text_above else 0
    expected, x = [], 0
    for index, width in enumerate(mods):
        if index % 2 == 0 and width:
            expected.append((x, top, width * 3, 60))
        x += width * 3
    check(f"{symbology}'s rects are the bars it always drew",
          layout['rects'] == expected,
          (layout['rects'][:3], expected[:3]))
    check(f"and {symbology}'s run and stack still bound them",
          layout['run'] == sum(mods) * 3 and layout['stack'] == 60
          and max(rx + rw for rx, ry, rw, rh in layout['rects']) <= layout['run'],
          (layout['run'], layout['stack']))

_above = BarcodeElement(0, 0, 60, '12345', module_width=3, options=('Y', 'Y'))
check("an interpretation line above the bars moves the rects down, not the box",
      all(ry == _above.text_height() for _rx, ry, _rw, _rh in
          geometry.barcode_layout(_above)['rects'])
      and _above.height == 60 + _above.text_height(),
      (geometry.barcode_layout(_above)['rects'][0], _above.height))

# --- ^FR (reverse print) -----------------------------------------------------
for make, describe in (
        (lambda: TextElement(0, 0, 'Reversed'), 'text'),
        (lambda: FrameElement(0, 0, 100, 50), 'frame'),
        (lambda: EllipseElement(0, 0, 100, 50), 'ellipse'),
        (lambda: BarcodeElement(0, 0, 80, '12345'), 'barcode')):
    plain = make()
    check(f"an untouched {describe} element writes no ^FR",
          '^FR' not in plain.to_zpl(), plain.to_zpl())

    was_reversed = make()
    was_reversed.reverse_print = True
    check(f"a reversed {describe} element writes ^FR",
          '^FR' in was_reversed.to_zpl(), was_reversed.to_zpl())
    reparsed = zpl_parser.parse_zpl(f"^XA{was_reversed.to_zpl()}^XZ")[0].elements[0]
    check(f"^FR survives a round trip on a {describe} element",
          reparsed.reverse_print is True, reparsed.to_zpl())

check("^FR is modelled, not reported as an unsupported command",
      '^FR' not in workflow.unsupported_commands(was_reversed.to_zpl()))

# ^FR must sit immediately before the command it reverses - a real printer
# was seen not to honour it at all when it sat right after ^FO instead, ahead
# of the field's own setup commands.
for make, describe, marker in (
        (lambda: TextElement(0, 0, 'Reversed'), 'text', '^FD'),
        (lambda: BarcodeElement(0, 0, 80, '12345'), 'barcode', '^FD')):
    el = make()
    el.reverse_print = True
    zpl = el.to_zpl()
    check(f"^FR immediately precedes {marker} on a {describe} element",
          f'^FR\n{marker}' in zpl, zpl)

numbered = TextElement(0, 0, field_number=1)
numbered.reverse_print = True
check("^FR immediately precedes ^FN on a numbered text element",
      '^FR\n^FN' in numbered.to_zpl(), numbered.to_zpl())

# --- wrapped text (^FB) -----------------------------------------------------

# the editor's line breaks and ZPL's are the same thing, spelled differently
check(r"a typed line break becomes \&",
      textraster.from_editor("top\nbottom") == r"top\&bottom")
check(r"\& comes back into the box as a line break",
      textraster.to_editor(r"top\&bottom") == "top\nbottom")
check("switching wrapping off joins the lines rather than leaving a break",
      textraster.join_lines(r"top\&bottom") == "top bottom")

# switching wrapping on must not move the element
wdoc = Document(812, 1218, dpi=203)
wt = wdoc.add_text_element('Stainless Steel Hex Head Bolt 10mm')
wt.font_path = FONT
wdoc.sync_text_width(wt)
unwrapped = wt.width
wt.block = wt.default_block(wdoc.font_path)
wdoc.sync_text_width(wt)
check("a default block wraps the text where it already ended",
      abs(wt.width - unwrapped) <= 2 and wt.height == wt.font_height,
      (unwrapped, wt.width, wt.height))

# the box is the block by its lines, not the string by its glyphs
wt.block = FieldBlock(200, 4, 0, 'L', 0)
wdoc.sync_text_width(wt)
wrapped = textraster.wrap(wt.text, FONT, wt.font_height, wt.font_width, wt.block)
check("the box is the block by however many lines it wraps into",
      (wt.width, wt.height) == (200, len(wrapped) * wt.font_height),
      (wt.width, wt.height, len(wrapped)))

# every parameter reaches the file and comes back
wt.block = FieldBlock(240, 3, 4, 'J', 6)
wdoc.sync_text_width(wt)
check("^FB is written between the font and the data",
      re.search(r"\^A[^\n]*\n\^FB[^\n]*\n\^FD", wt.to_zpl()) is not None,
      wt.to_zpl().replace("\n", " "))
reblocked = zpl_parser.parse_zpl(f"^XA{wt.to_zpl()}^XZ")[0].elements[0]
check("every ^FB parameter survives a round trip",
      reblocked.block == wt.block, reblocked.block)

broken = TextElement(0, 0, textraster.from_editor("ACME Widget\nModel 4400"))
broken.block = FieldBlock(400, 4)
rebroken = zpl_parser.parse_zpl(f"^XA{broken.to_zpl()}^XZ")[0].elements[0]
check("a typed break survives the file",
      textraster.to_editor(rebroken.text) == "ACME Widget\nModel 4400",
      rebroken.text)

# dragging a wrapped element asks for a wrap width and a line count
rd = Document(812, 1218, dpi=203)
rw = rd.add_text_element('one two three four five six seven eight nine ten')
rw.font_path = FONT
rw.block = FieldBlock(300, 8)
rd.sync_text_width(rw)
geometry.resize_by_handle(rd, rw, 'mr', -120, 0)
check("a side handle sets the wrap width",
      (rw.block.width, rw.width) == (180, 180), (rw.block.width, rw.width))
narrowed = len(textraster.wrap(rw.text, FONT, rw.font_height, rw.font_width, rw.block))
check("the box still equals the wrap after dragging it narrower",
      rw.height == narrowed * rw.font_height, (rw.height, narrowed))
geometry.resize_by_handle(rd, rw, 'bm', 0, -rw.font_height)
check("a bottom handle sets the line count, and the surplus is dropped",
      rw.block.max_lines == narrowed - 1
      and rw.height == (narrowed - 1) * rw.font_height,
      (rw.block.max_lines, rw.height, narrowed))

# a block is an object, so a snapshot must not be holding the live one
udoc = Document(812, 1218, dpi=203)
ut = udoc.add_text_element('wrap me around for a while')
ut.block = FieldBlock(300, 4)
udoc.sync_text_width(ut)
snap = udoc.snapshot()
ut.block.width = 120
udoc.restore(snap)
check("undo restores the wrap width a drag changed",
      udoc.elements[0].block.width == 300, udoc.elements[0].block.width)

# a block is in dots like everything else, so it scales with the head
sdoc = Document(812, 1218, dpi=203)
st = sdoc.add_text_element('wrap me')
st.block = FieldBlock(400, 4, 2, 'C', 10)
sdoc.rescale(300 / 203)
check("rescale carries the wrap width to the new resolution",
      st.block.width == max(1, round(400 * 300 / 203)), st.block.width)

check("both frontends are offered the same justifications",
      [c for _l, c in zpl_model.TEXT_JUSTIFICATIONS] == ['L', 'C', 'R', 'J'])

# --- justified text ---------------------------------------------------------
JTEXT = 'one two three four five six seven eight nine'
jblock = FieldBlock(300, 6, 0, 'J', 0)
jmeasure, _jf = textraster.measurer(FONT, 40, 40)
jmarked = textraster.wrap_marked(JTEXT, FONT, 40, 40, jblock)
jstretch = [line for line, last in jmarked if not last]
check("a justified block has a line to stretch", bool(jstretch), jmarked)
jplaces = textraster.placements(jstretch[0], jmeasure, jblock, False)
check("a justified line starts at the left edge", jplaces[0][1] == 0, jplaces[0])
check("a justified line ends at the right edge",
      abs(jplaces[-1][1] + jmeasure(jplaces[-1][0]) - jblock.width) <= 1,
      (jplaces[-1], jblock.width))
check("the line that ends the text is not stretched",
      len(textraster.placements(jmarked[-1][0], jmeasure, jblock, True)) == 1)

jimage = textraster.raster_block(JTEXT, FONT, 40, 40, jblock)
jband = jimage.crop((0, 0, jimage.width, textraster.pitch(40, jblock)))
jcols = [x for x in range(jband.width)
         if any(jband.getpixel((x, y))[3] for y in range(jband.height))]
check("the drawn ink spans the block, not just the words that fit",
      jcols and jcols[0] <= textraster.MARGIN + 2
      and jcols[-1] >= jblock.width - 6,
      (jcols[0], jcols[-1], jblock.width))

# --- wrapping shows before a font is chosen ---------------------------------
def _ink_bands(image, element):
    """Separate horizontal bands of black ink inside an element's box.

    The canvas paints the box a translucent blue and outlines it in blue, so
    only the glyphs are dark in all three channels.
    """
    x0, y0 = max(0, element.x), max(0, element.y)
    x1 = min(image.width(), element.x + element.width)
    y1 = min(image.height(), element.y + element.height + 4)
    bands, inside = 0, False
    for y in range(y0, y1):
        dark = False
        for x in range(x0, x1):
            rgb = image.pixel(x, y)
            if (((rgb >> 16) & 0xFF) < 100 and ((rgb >> 8) & 0xFF) < 100
                    and (rgb & 0xFF) < 100):
                dark = True
                break
        if dark and not inside:
            bands += 1
        inside = dark
    return bands

# Pinned to 1:1, so one pixel is one dot and the element's own coordinates
# index the rendered image directly.
nw = qt_main.ZPLDesignerWindow()
nw.unsaved_changes = False
nw.on_new()
nw.document.set_label_size(812, 1218)
nofont = nw.document.add_text_element('one two three four five six')
nofont.x, nofont.y = 20, 20
nofont.block = FieldBlock(200, 6)
nw.document.sync_text_width(nofont)
nw.canvas.set_zoom(1.0)
plain = QImage(812, 1218, QImage.Format_ARGB32); plain.fill(Qt.white)
nw.canvas.render(plain)
check("a block with no font chosen still draws as several lines",
      _ink_bands(plain, nofont) > 1, _ink_bands(plain, nofont))
nofont.block = None
nw.document.sync_text_width(nofont)
flat = QImage(812, 1218, QImage.Format_ARGB32); flat.fill(Qt.white)
nw.canvas.render(flat)
check("the same text unwrapped draws as one",
      _ink_bands(flat, nofont) == 1, _ink_bands(flat, nofont))

# --- the dialog, driven -----------------------------------------------------
from PySide2.QtCore import QTimer
from PySide2.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                               QPlainTextEdit, QSpinBox)

def _drive_text_dialog(element, document, fill):
    """Open the text editor, let `fill` set its fields, then accept it.

    The editor is non-modal, so it can be filled in and accepted on this same
    stack rather than through a timer that waits for a blocked call to yield.
    """
    accepted = []
    dialog = qt_dialogs.edit_text_dialog(
        None, element, document, on_accept=lambda: accepted.append(True))
    fill(dialog)
    dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.Ok).click()
    return bool(accepted)

ddoc = Document(812, 1218, dpi=203)
de = ddoc.add_text_element('one two three four five six seven eight')

def _fill_wrap(dialog):
    dialog.findChild(QPlainTextEdit, 'text').setPlainText("ACME Widget\nModel 4400")
    dialog.findChild(QCheckBox, 'wrap').setChecked(True)
    dialog.findChild(QSpinBox, 'block_width').setValue(220)
    dialog.findChild(QSpinBox, 'max_lines').setValue(5)
    dialog.findChild(QSpinBox, 'line_spacing').setValue(3)
    dialog.findChild(QComboBox, 'justification').setCurrentIndex(1)   # Centred
    dialog.findChild(QSpinBox, 'indent').setValue(4)

check("the text dialog reports the change", _drive_text_dialog(de, ddoc, _fill_wrap))
check("the wrap set in the dialog reaches the element",
      de.block == FieldBlock(220, 5, 3, 'C', 4), de.block)
check("a break typed in the dialog reaches the field data",
      de.text == r"ACME Widget\&Model 4400", de.text)
# Font F's 26-dot cells are 32 dots apart, so "ACME Widget" (346) no longer
# fits 220 and breaks at its space: four lines, not two
check("the dialog leaves the box equal to the wrap",
      (de.width, de.height) == (220, 4 * (de.font_height + 3)),
      (de.width, de.height))

_drive_text_dialog(de, ddoc,
                   lambda dialog: dialog.findChild(QCheckBox, 'wrap').setChecked(False))
check("unticking wrap joins the lines rather than leaving a break behind",
      de.block is None and de.text == "ACME Widget Model 4400", de.text)

_drive_text_dialog(
    de, ddoc,
    lambda dialog: dialog.findChild(QCheckBox, 'reverse_print').setChecked(True))
check("reverse print set in the text dialog reaches the element",
      de.reverse_print is True)
check("a reversed field written from the dialog carries ^FR",
      '^FR' in de.to_zpl(), de.to_zpl())

from PySide2.QtGui import QColor

# The canvas must invert against what a *previous* element really put down,
# not against its own translucent "you can select this" affordance box - the
# affordance is drawn first (so a reversed field stays visible with nothing
# to invert yet) and was, for one build, drawn before the ink instead of
# after, so the invert picked up its own light-blue tint rather than the
# black frame underneath.
rw = qt_main.ZPLDesignerWindow()
rw.unsaved_changes = False
rw.on_new()
rw.document.set_label_size(200, 150)
rbox = rw.document.add_frame_element()
rbox.x, rbox.y, rbox.width, rbox.height, rbox.thickness = 10, 10, 180, 130, 180
rtext = rw.document.add_text_element('Hi')
rtext.x, rtext.y, rtext.font_height, rtext.font_width = 20, 20, 40, 40
rtext.font_path = FONT
rtext.reverse_print = True
rw.document.sync_text_width(rtext)
rw.document.clear_selection()
rw.canvas.set_zoom(1.0)
reversed_canvas = QImage(200, 150, QImage.Format_ARGB32); reversed_canvas.fill(Qt.white)
rw.canvas.render(reversed_canvas)
check("the canvas inverts a reversed field's ink against the real box beneath it",
      QColor(reversed_canvas.pixel(30, 30)).red() > 200,
      QColor(reversed_canvas.pixel(30, 30)).getRgb())
check("...and leaves the rest of the box untouched",
      QColor(reversed_canvas.pixel(15, 30)).red() < 50,
      QColor(reversed_canvas.pixel(15, 30)).getRgb())

# --- the editors are non-modal child windows --------------------------------
# Non-modal means an editor can still be up when the element under it is
# replaced or removed, which is the one way an edit can be silently lost.

ew = qt_main.ZPLDesignerWindow()
etext = ew.document.add_text_element("editable")
eframe = ew.document.add_frame_element()

ew.on_element_double_clicked(etext)
first = ew._editors[id(etext)]
check("an element editor opens without blocking its caller", first.isVisible())
ew.on_element_double_clicked(etext)
check("a second double-click raises the open editor rather than opening another",
      ew._editors[id(etext)] is first and len(ew._editors) == 1,
      len(ew._editors))

ew.on_element_double_clicked(eframe)
check("a different element gets an editor of its own alongside",
      len(ew._editors) == 2, len(ew._editors))

ew._apply_snapshot(ew.document.snapshot())
check("undo closes the editors, whose elements it has just replaced",
      not ew._editors, len(ew._editors))

# Closed, not merely forgotten: an editor still on screen over a replaced
# element is exactly how an OK press writes into a detached copy.
survivor = ew.document.elements[0]
ew.on_element_double_clicked(survivor)
stale = ew._editors[id(survivor)]
ew._apply_snapshot(ew.document.snapshot())
check("the editor is taken off screen, not just dropped from the register",
      not stale.isVisible() and not ew._editors)
check("restoring really did replace the element it was editing",
      all(el is not survivor for el in ew.document.elements))

ew.document.selected_element = ew.document.elements[0]
ew.on_element_double_clicked(ew.document.selected_element)
ew.on_delete()
check("deleting an element closes the editor open on it",
      not ew._editors, len(ew._editors))

# --- commands that used to lose what they carried ---------------------------

# ^CF: a field with no ^A of its own prints in whatever ^CF last set. Requiring
# an explicit ^A dropped the element altogether.
cf_doc = zpl_parser.parse_zpl(
    "^XA^PW812^LL1218^CF0,40,40\n^FO50,50^FDdefault font^FS\n"
    "^FO50,150^A0N,30,30^FDits own^FS\n^XZ")[0]
check("a field using ^CF's font is kept, not dropped",
      [e.text for e in cf_doc.elements] == ['default font', 'its own'],
      [(e.element_type, getattr(e, 'text', None)) for e in cf_doc.elements])
check("and it takes ^CF's font and size",
      (cf_doc.elements[0].font_code, cf_doc.elements[0].font_height,
       cf_doc.elements[0].font_width) == ('0', 40, 40),
      (cf_doc.elements[0].font_code, cf_doc.elements[0].font_height))
bare = zpl_parser.parse_zpl("^XA^PW812^LL1218^FO50,50^FDbare^FS^XZ")[0]
check("with no ^CF at all, ZPL's own default font applies",
      (bare.elements[0].font_code, bare.elements[0].font_height,
       bare.elements[0].font_width) == ('A', 9, 5),
      (bare.elements[0].font_code, bare.elements[0].font_height))
# a barcode names no font of its own and must go on naming none
bc_cf = zpl_parser.parse_zpl(
    "^XA^PW812^LL1218^CF0,40,40\n^FO50,50^BY2^BC,100^FD123^FS^XZ")[0]
check("^CF does not put an ^A on a barcode that had none",
      '^A' not in bc_cf.elements[0].to_zpl(),
      bc_cf.elements[0].to_zpl().replace('\n', ' '))

# ^CI used to be reported as a command a save would drop, which it was; it
# is carried now (see the ^CI section further down), so it must not be
check("^CI is no longer reported, now that it is carried",
      workflow.unsupported_commands("^XA^CI28^FO1,1^A0N,9,9^FDx^FS^XZ") == [])
check("^CF is not reported, now that it is honoured",
      workflow.unsupported_commands("^XA^CF0,40^FO1,1^FDx^FS^XZ") == [])

# ^FW: the orientation every field takes when it leaves its own out. The
# manual's own example - after ^FWR, ^A0N,25,20 prints upright and ^A0,25,20
# prints turned - opened with both upright, and a save wrote ^A0N back,
# pinning the second to a turn the file never gave it. A field with no ^A and
# a barcode with no letter defer to it the same way.
fw_doc = zpl_parser.parse_zpl(
    "^XA^PW812^LL1218^FWR\n"
    "^FO150,90^A0N,25,20^FDZebra Technologies^FS\n"
    "^FO115,75^A0,25,20^FD0123456789^FS\n"
    "^CF0,30,30^FO20,300^FDdefault font^FS\n"
    "^FO200,300^BY2^BC,80^FD12345^FS\n"
    "^FO200,500^BY2^BCN,80^FD12345^FS\n"
    "^FWN^FO20,500^A0,25,20^FDupright^FS\n^XZ")[0]
fw_turns = [e.orientation for e in fw_doc.elements]
check("an ^A that spells its letter keeps it under ^FW", fw_turns[0] == 'N', fw_turns)
check("an ^A that leaves its letter out takes ^FW's", fw_turns[1] == 'R', fw_turns)
check("a field with no ^A takes ^FW's - ^CF has no orientation of its own",
      fw_turns[2] == 'R', fw_turns)
check("a barcode that leaves its letter out takes ^FW's", fw_turns[3] == 'R', fw_turns)
check("and one that spells it keeps it", fw_turns[4] == 'N', fw_turns)
check("^FW is a running default, like ^CF", fw_turns[5] == 'N', fw_turns)
fw_saved = fw_doc.to_zpl()
check("a save spells every turn ^FW gave, and writes no ^FW",
      '^FW' not in fw_saved and '^A0R,25,20\n' in fw_saved
      and '^A0R,30,30\n' in fw_saved and '^BCR,80\n' in fw_saved,
      fw_saved.replace('\n', ' '))
check("and reads back with the same turns",
      [e.orientation for e in zpl_parser.parse_zpl(fw_saved)[0].elements] == fw_turns,
      [e.orientation for e in zpl_parser.parse_zpl(fw_saved)[0].elements])
# a barcode a file never turned goes on writing no letter, so every existing
# fixture still saves byte-identical
for fw_case, fw_prefix in (("no ^FW", ""), ("^FWN", "^FWN")):
    fw_bc = zpl_parser.parse_zpl(
        f"^XA^PW812^LL1218{fw_prefix}^FO50,50^BY2^BC,100^FD123^FS^XZ")[0].elements[0]
    check(f"a ^BC with no letter and {fw_case} still writes none",
          fw_bc.orientation == '' and '^BC,100\n' in fw_bc.to_zpl(),
          (fw_bc.orientation, fw_bc.to_zpl().replace('\n', ' ')))
# only the four letters change it: a bare ^FW, an undefined letter, or the
# x.14 justification on its own all keep the value in force
for fw_spelling in ("^FW", "^FWX", "^FW,1"):
    fw_kept = zpl_parser.parse_zpl(
        f"^XA^PW812^LL1218^FWR{fw_spelling}^FO50,50^A0,25,20^FDx^FS^XZ")[0].elements[0]
    check(f"{fw_spelling} after ^FWR keeps R in force",
          fw_kept.orientation == 'R', fw_kept.orientation)
check("^FW is not reported, now that it is honoured",
      workflow.unsupported_commands("^XA^FWR^FO1,1^FDx^FS^XZ") == [],
      workflow.unsupported_commands("^XA^FWR^FO1,1^FDx^FS^XZ"))

# ^GB's colour and rounding: the colour is a letter, which is why a
# digits-only pattern dropped it and the rounding after it
painted = zpl_parser.parse_zpl("^XA^PW812^LL1218^FO50,50^GB300,200,4,W,5^FS^XZ")[0]
frame = painted.elements[0]
check("^GB's colour and rounding are read",
      (frame.colour, frame.rounding) == ('W', 5), (frame.colour, frame.rounding))
check("and written back",
      "^GB300,200,4,W,5" in frame.to_zpl(), frame.to_zpl().replace('\n', ' '))
plain_frame = Document().add_frame_element()
check("a frame the designer created still writes no colour or rounding",
      f"^GB{plain_frame.width},{plain_frame.height},{plain_frame.thickness}\n"
      in plain_frame.to_zpl(), plain_frame.to_zpl().replace('\n', ' '))
check("rounding 8 is half the shorter side",
      FrameElement(0, 0, 300, 200, 2, 'B', 8).corner_radius() == 100,
      FrameElement(0, 0, 300, 200, 2, 'B', 8).corner_radius())
check("rounding 0 is square", FrameElement(0, 0, 300, 200).corner_radius() == 0)

# the preview draws the frame the canvas draws, rounding and all
rounded = ZPLRenderer(400, 300).render(
    "^XA^PW400^LL300^FO20,20^GB360,260,4,B,8^FS^XZ").convert('L')
check("the preview rounds a rounded frame's corners",
      rounded.getpixel((22, 22)) > 200 and rounded.getpixel((200, 21)) < 100,
      (rounded.getpixel((22, 22)), rounded.getpixel((200, 21))))

# ^FR inverts whatever is already there under a frame's own shape, ignoring
# colour entirely - nothing is behind this one, so it inverts blank (white)
# to black. Coloured 'W' (which an unreversed frame draws as invisible white
# ink) is what tells a correctly-reversed frame apart from one that silently
# fell back to drawing its own colour instead of inverting.
fr_frame = ZPLRenderer(400, 300).render(
    "^XA^PW400^LL300^FO20,20^FR^GB360,260,4,W^FS^XZ").convert('L')
check("the preview draws a ^FR frame as a true invert, ignoring its colour",
      fr_frame.getpixel((200, 21)) < 100, fr_frame.getpixel((200, 21)))
check("^FR reversed on a foreign file writes back after ^GB and still renders",
      ZPLRenderer(400, 300).render(
          "^XA^PW400^LL300^FO20,20^GB360,260,4,W^FR^FS^XZ"
      ).convert('L').getpixel((200, 21)) < 100)

# --- text turns the way barcodes already do ---------------------------------

turned = zpl_parser.parse_zpl("^XA^PW812^LL1218^FO50,50^A0R,40,40^FDturned^FS^XZ")[0]
check("^A's orientation letter is read, not discarded",
      turned.elements[0].orientation == 'R', turned.elements[0].orientation)
check("and written back",
      "^A0R,40,40" in turned.elements[0].to_zpl(),
      turned.elements[0].to_zpl().replace('\n', ' '))
flat = zpl_parser.parse_zpl("^XA^PW812^LL1218^FO50,50^A0N,40,40^FDturned^FS^XZ")[0]
check("a quarter turn transposes the footprint",
      (turned.elements[0].width, turned.elements[0].height)
      == (flat.elements[0].height, flat.elements[0].width),
      ((turned.elements[0].width, turned.elements[0].height),
       (flat.elements[0].width, flat.elements[0].height)))
check("an upright element is unchanged",
      flat.elements[0].orientation == 'N' and not flat.elements[0].rotated())
check("text and barcodes are offered the same turns",
      [c for _l, c in zpl_model.ORIENTATIONS] == ['N', 'R', 'I', 'B'])

# a wrapped block turns with its text
rot_block = Document(812, 1218, dpi=203)
rb = rot_block.add_text_element('one two three four five six')
rb.font_path = FONT
rb.block = FieldBlock(200, 6)
rot_block.sync_text_width(rb)
upright_box = (rb.width, rb.height)
rb.orientation = 'R'
rot_block.sync_text_width(rb)
check("a wrapped block's footprint transposes too",
      (rb.width, rb.height) == (upright_box[1], upright_box[0]),
      (upright_box, (rb.width, rb.height)))

# drawn, not merely stored: the ink has to land inside the turned box
def _ink_box(image, element):
    """The bounds of the black ink inside an element's box, or None."""
    xs, ys = [], []
    for y in range(max(0, element.y), min(image.height(), element.y + element.height)):
        for x in range(max(0, element.x), min(image.width(), element.x + element.width)):
            rgb = image.pixel(x, y)
            if (((rgb >> 16) & 0xFF) < 100 and ((rgb >> 8) & 0xFF) < 100
                    and (rgb & 0xFF) < 100):
                xs.append(x)
                ys.append(y)
    return (min(xs), min(ys), max(xs), max(ys)) if xs else None

rw = qt_main.ZPLDesignerWindow()
rw.unsaved_changes = False
rw.on_new()
rw.document.set_label_size(812, 1218)
rt = rw.document.add_text_element('Turned')
rt.x, rt.y = 40, 40
rt.font_path = FONT
rw.document.sync_text_width(rt)
rw.canvas.set_zoom(1.0)

shapes = {}
for facing in ('N', 'R', 'I', 'B'):
    rt.orientation = facing
    rw.document.sync_text_width(rt)
    rw.canvas.set_zoom(1.0)
    surface = QImage(812, 1218, QImage.Format_ARGB32); surface.fill(Qt.white)
    rw.canvas.render(surface)
    box = _ink_box(surface, rt)
    shapes[facing] = box
    check(f"text at {facing} draws ink inside its own box", box is not None, box)

def _shape(box):
    return (box[2] - box[0], box[3] - box[1]) if box else None

check("turning 90 degrees transposes the drawn ink",
      _shape(shapes['R']) == tuple(reversed(_shape(shapes['N']))),
      (_shape(shapes['N']), _shape(shapes['R'])))
check("and 270 too",
      _shape(shapes['B']) == tuple(reversed(_shape(shapes['N']))),
      (_shape(shapes['N']), _shape(shapes['B'])))
check("upside down keeps the upright shape",
      _shape(shapes['I']) == _shape(shapes['N']),
      (_shape(shapes['N']), _shape(shapes['I'])))
rt.orientation = 'R'
rw.document.sync_text_width(rt)
check("a click inside a turned element still selects it",
      rw.document.element_at(rt.x + 2, rt.y + 2) is rt)

# the preview turns it the same way
def _preview_shape(zpl):
    img = ZPLRenderer(300, 300).render(zpl).convert('L')
    marks = [(x, y) for x in range(300) for y in range(300)
             if img.getpixel((x, y)) < 128]
    if not marks:
        return None
    xs = [x for x, _ in marks]; ys = [y for _, y in marks]
    return (max(xs) - min(xs), max(ys) - min(ys))

up = _preview_shape("^XA^PW300^LL300^FO20,20^A0N,30,30^FDTurn^FS^XZ")
side = _preview_shape("^XA^PW300^LL300^FO20,20^A0R,30,30^FDTurn^FS^XZ")
check("the preview turns text too, transposing its ink",
      up and side and side == tuple(reversed(up)), (up, side))

# --- looking at the label: zoom, fit and the window -------------------------
from zplcore import view as zpl_view

check("zooming in from a fitted scale lands on the next step above it",
      zpl_view.zoom_in(0.62) == 0.67, zpl_view.zoom_in(0.62))
check("zooming out from 1:1 lands on the step below",
      zpl_view.zoom_out(1.0) == 0.75, zpl_view.zoom_out(1.0))
check("the steps stop at both ends",
      (zpl_view.zoom_in(zpl_view.ZOOM_MAX), zpl_view.zoom_out(zpl_view.ZOOM_MIN))
      == (zpl_view.ZOOM_MAX, zpl_view.ZOOM_MIN))
check("a zoom outside the range is clamped into it",
      zpl_view.clamp_zoom(99) == zpl_view.ZOOM_MAX
      and zpl_view.clamp_zoom(0.001) == zpl_view.ZOOM_MIN)

# a fit puts the whole label in view; fit-width is the rule that was there
fitted = zpl_view.fit_scale(500, 400, 812, 1218)
check("fitting the label puts all of it inside the view",
      812 * fitted <= 500 + 1 and 1218 * fitted <= 400 + 1,
      (812 * fitted, 1218 * fitted))
check("fitting the width is the old scale_factor rule",
      abs(zpl_view.fit_width(600, 812)
          - geometry.scale_factor(600, 812)) < 1e-9)

# zooming about the pointer keeps the dot that was under it under it
before, after = 0.5, 1.0
offset = zpl_view.zoom_anchor(pointer_in_canvas=300, pointer_in_view=120,
                              old_scale=before, new_scale=after)
dot_before = 300 / before
dot_after = (offset + 120) / after
check("a zoom about the pointer keeps the same dot under it",
      abs(dot_before - dot_after) <= 1, (dot_before, dot_after))

# the window opens onto the monitor it is opening on, not off the bottom of it
WORK = (0, 0, 1600, 900)
x, y, w, h = zpl_view.place_window(WORK)
check("a first run fits the work area",
      w <= WORK[2] and h <= WORK[3] and x >= 0 and y >= 0, (x, y, w, h))
check("a first run is centred on it",
      abs((x + w // 2) - WORK[2] // 2) <= 1 and abs((y + h // 2) - WORK[3] // 2) <= 1,
      (x, y, w, h))
for saved in ((0, 900, 1900, 1040),        # saved on a bigger monitor
              (1850, 60, 900, 700),        # saved off the right-hand edge
              (-400, -200, 900, 700)):     # saved off the top left
    brought = zpl_view.place_window(WORK, saved=saved)
    check(f"a geometry saved at {saved[:2]} is brought onto this monitor",
          brought[0] >= WORK[0] and brought[1] >= WORK[1]
          and brought[0] + brought[2] <= WORK[0] + WORK[2]
          and brought[1] + brought[3] <= WORK[1] + WORK[3], brought)
kept = zpl_view.place_window(WORK, saved=(120, 60, 1000, 680))
check("a geometry that already fits comes back unchanged",
      kept == (120, 60, 1000, 680), kept)

# --- the canvas under zoom --------------------------------------------------
zw = qt_main.ZPLDesignerWindow()
zw.unsaved_changes = False
zw.on_new()
zcanvas = zw.canvas

zcanvas.set_view_size(500, 400)
zcanvas.set_fit(zpl_view.FIT_LABEL)
check("the canvas defaults to fitting the whole label",
      zcanvas.width() <= 500 and zcanvas.height() <= 400,
      (zcanvas.width(), zcanvas.height()))
check("fitting leaves no zoom pinned", zcanvas.zoom is None)

doc = zw.document
for zoom in (0.25, 1.0, 4.0):
    zcanvas.set_zoom(zoom)
    check(f"at {zoom:g}x the widget is the label at that scale",
          (zcanvas.width(), zcanvas.height())
          == (round(doc.label_width * zoom), round(doc.label_height * zoom)),
          (zcanvas.width(), zcanvas.height()))
    # a pointer at a known dot comes back as that dot
    lx, ly = 200, 300
    back = zcanvas._screen_to_label(int(lx * zoom), int(ly * zoom))
    check(f"at {zoom:g}x a pointer maps back to the dot it was over",
          abs(back[0] - lx) <= 1 and abs(back[1] - ly) <= 1, back)

# a handle is a constant size on screen, or it cannot be grabbed zoomed out
box = FrameElement(100, 100, 200, 150)
check("zoomed out, a handle is grabbable well beyond 8 dots",
      geometry.handle_at_point(100 + 20, 100, box, 0.25) == 'tl',
      geometry.handle_size(0.25))
check("zoomed in, a handle does not swallow the element it resizes",
      geometry.handle_at_point(100 + 6, 100, box, 4.0) is None,
      geometry.handle_size(4.0))
check("at 1:1 the handle radius is what it always was",
      geometry.handle_size(1.0) == geometry.HANDLE_SIZE)

# Ctrl+wheel: the window measures the pointer, zooms, then scrolls. Only an
# axis that actually scrolls can hold the anchor - an axis where the canvas is
# narrower than the view is centred, and there is nothing to offset.
from PySide2.QtCore import QPoint
zw.resize(700, 500)
zw.show()
app.processEvents()
zcanvas.set_zoom(1.0)
app.processEvents()
bar = zw.scroller.verticalScrollBar()
bar.setValue(200)
app.processEvents()
point = QPoint(150, 400)
in_view_y = zcanvas.mapTo(zw.scroller.viewport(), point).y()
dot_before = (bar.value() + in_view_y) / zcanvas._scale()
zw._zoom_at(point, True)
app.processEvents()
dot_after = (bar.value() + in_view_y) / zcanvas._scale()
check("Ctrl+wheel zooms in", zcanvas.zoom > 1.0, zcanvas.zoom)
check("and keeps the dot that was under the pointer under it",
      abs(dot_after - dot_before) <= 2, (dot_before, dot_after))

# The pointer's place in the view has to be read before the zoom moves
# everything. Reading it afterwards is right only by luck - it agrees whenever
# the canvas happens not to move within the view - so the order is asserted
# rather than inferred from a position.
seen = []
real_map_to = zcanvas.mapTo
zcanvas.mapTo = lambda widget, point: (seen.append(zcanvas._scale())
                                       or real_map_to(widget, point))
try:
    scale_before = zcanvas._scale()
    zw._zoom_at(QPoint(150, 400), True)
finally:
    del zcanvas.mapTo
check("the pointer is measured before the zoom, not after",
      seen and all(s == scale_before for s in seen),
      (scale_before, seen, zcanvas._scale()))
zw.hide()

# The label size is entered in inches and stored in dots, so the number of
# decimals it accepts is the resolution a user can actually ask for: at 203 dpi
# a tenth of an inch is 20 dots.
from PySide2.QtWidgets import QDoubleSpinBox

def _drive_label_size(document, dpi, fill):
    def act():
        dialog = next((widget for widget in app.topLevelWidgets()
                       if isinstance(widget, QDialog) and widget.isVisible()), None)
        if dialog is None:
            QTimer.singleShot(50, act)
            return
        fill(dialog)
        dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.Ok).click()

    QTimer.singleShot(100, act)
    return qt_dialogs.label_size_dialog(None, document, dpi)

def _spins(dialog):
    return dialog.findChildren(QDoubleSpinBox)

sized = _drive_label_size(Document(812, 1218, dpi=203), 203,
                          lambda d: (_spins(d)[0].setValue(2.75),
                                     _spins(d)[1].setValue(4.25)))
check("a label size keeps two decimals rather than rounding to one",
      sized[:2] == (558, 863), f"{sized[:2]}, expected (558, 863)")
check("and reports back the inches that were typed, for the settings file",
      sized[2:5] == (203, 2.75, 4.25), sized[2:5])
check("and the label transform, which the dialog also carries",
      sized[5] == zpl_transforms.LabelTransform(), sized[5])
reopened = {}
_drive_label_size(Document(*sized[:2], dpi=203), 203,
                  lambda d: reopened.update(w=round(_spins(d)[0].value(), 2),
                                            h=round(_spins(d)[1].value(), 2)))
check("and shows that size again to the hundredth",
      (reopened['w'], reopened['h']) == (2.75, 4.25), reopened)

# The resolution sits in the same dialog as the inches, because the dots are a
# consequence of both: 4x6in is 812x1218 dots at 203dpi and 1200x1800 at 300.
def _set_dpi(d, text):
    d.findChild(QComboBox).setCurrentText(text)

at300 = _drive_label_size(Document(812, 1218, dpi=203), 203,
                          lambda d: (_spins(d)[0].setValue(4.0),
                                     _spins(d)[1].setValue(6.0),
                                     _set_dpi(d, '300')))
check("the resolution chosen in the dialog is what the inches convert with",
      at300[:3] == (1200, 1800, 300), at300[:3])

# the settings file carries the window geometry beside the printer
import configparser as _cfg
geo_dir = tempfile.mkdtemp()
geo_path = Path(geo_dir) / 'settings.ini'
real_config_path = qt_main._config_path
real_fallback_path = qt_main._fallback_config_path
qt_main._config_path = lambda: geo_path
try:
    zw.saved_geometry = (140, 60, 1000, 680)
    zw._save_settings()
    written = _cfg.ConfigParser(); written.read(geo_path)
    check("the window geometry is written beside the printer settings",
          written.has_section('window') and written.has_section('printer')
          and written.getint('window', 'width') == 1000,
          dict(written['window']) if written.has_section('window') else None)
    zw.saved_geometry = None
    zw._load_settings()
    check("and is read back", zw.saved_geometry == (140, 60, 1000, 680),
          zw.saved_geometry)

    # The label size is remembered too, in inches: dots only mean a physical
    # size once a resolution is fixed, and the resolution beside them is itself
    # a setting that can change between sessions.
    zw.label_inches = (2.75, 4.25)
    zw._save_settings()
    written = _cfg.ConfigParser(); written.read(geo_path)
    check("the label size is persisted in inches, not dots",
          written.getfloat('label', 'width_in') == 2.75
          and written.getfloat('label', 'height_in') == 4.25,
          dict(written['label']) if written.has_section('label') else None)
    zw.label_inches = qt_main.DEFAULT_LABEL_INCHES
    zw._load_settings()
    check("and is read back", zw.label_inches == (2.75, 4.25), zw.label_inches)

    # A hand-edited file must never open the designer onto a one-dot label, or
    # stop it starting at all.
    for bad in ('wide', '0', '900'):
        written.set('label', 'width_in', bad)
        with open(geo_path, 'w') as f:
            written.write(f)
        zw.label_inches = qt_main.DEFAULT_LABEL_INCHES
        zw._load_settings()
        check(f"a label width of {bad!r} falls back rather than being used",
              zw.label_inches == qt_main.DEFAULT_LABEL_INCHES, zw.label_inches)

    # Some Linux environments refuse writes under the user config directory
    # outright. Stand that in with a *file* where the settings directory
    # needs to go, so mkdir(parents=True, exist_ok=True) fails deterministically
    # without touching real permission bits.
    blocked_dir = Path(tempfile.mkdtemp()) / 'blocked'
    blocked_dir.write_text('')
    fallback_path = Path(tempfile.mkdtemp()) / 'settings.ini'
    qt_main._config_path = lambda: blocked_dir / 'settings.ini'
    qt_main._fallback_config_path = lambda: fallback_path
    zw._default_printer = ('10.0.0.9', zw.printer_port, zw.printer_dpi,
                           zw.printer_font_device)
    zw._save_settings()
    written = _cfg.ConfigParser(); written.read(fallback_path)
    check("a settings file the user config directory won't take is written to the project fallback instead",
          written.has_section('printer') and written.get('printer', 'address') == '10.0.0.9',
          dict(written['printer']) if written.has_section('printer') else None)
    zw.printer_address = qt_main.DEFAULT_ADDRESS
    zw._load_settings()
    check("and is read back from the fallback location",
          zw.printer_address == '10.0.0.9', zw.printer_address)
finally:
    qt_main._config_path = real_config_path
    qt_main._fallback_config_path = real_fallback_path

# --- Set Printer for This Session never touches the persisted default ------
# The DPI is held fixed across both dialogs below so neither one takes the
# rescale-prompt path, which would otherwise open a real (blocking) dialog.
session_path = Path(tempfile.mkdtemp()) / 'settings.ini'
qt_main._config_path = lambda: session_path
qt_main._fallback_config_path = lambda: session_path
try:
    zw.printer_address, zw.printer_port, zw.printer_dpi = '192.168.1.50', 9100, 203
    zw._default_printer = (zw.printer_address, zw.printer_port,
                           zw.printer_dpi, zw.printer_font_device)
    zw._save_settings()

    real_dialog = qt_dialogs.printer_settings_dialog
    qt_dialogs.printer_settings_dialog = lambda *a, **k: ('10.0.0.5', 9200, 203, 'E')
    try:
        zw.on_session_printer()
    finally:
        qt_dialogs.printer_settings_dialog = real_dialog

    check("Set Printer for This Session changes the printer in effect",
          (zw.printer_address, zw.printer_port) == ('10.0.0.5', 9200),
          (zw.printer_address, zw.printer_port))

    written = _cfg.ConfigParser(); written.read(session_path)
    check("but never writes it to the settings file",
          written.get('printer', 'address', fallback=None) == '192.168.1.50',
          dict(written['printer']) if written.has_section('printer') else None)

    # Default Printer must open on the persisted default, not the session
    # override just applied above - otherwise clicking OK on an unedited
    # dialog would silently promote the override into the new default.
    seen = {}
    def capture_dialog(parent, address, port, dpi, font_device=None, **kwargs):
        seen['address'], seen['port'], seen['dpi'] = address, port, dpi
        seen['font_device'] = font_device
        return None  # cancel, so nothing else about window state changes
    qt_dialogs.printer_settings_dialog = capture_dialog
    try:
        zw.on_default_printer()
    finally:
        qt_dialogs.printer_settings_dialog = real_dialog
    check("Default Printer opens pre-filled with the persisted default, not the session override",
          (seen['address'], seen['port']) == ('192.168.1.50', 9100), seen)

    # Contrast: Default Printer, given the same dialog result, does persist.
    qt_dialogs.printer_settings_dialog = lambda *a, **k: ('10.0.0.5', 9200, 203, 'E')
    try:
        zw.on_default_printer()
    finally:
        qt_dialogs.printer_settings_dialog = real_dialog

    written = _cfg.ConfigParser(); written.read(session_path)
    check("while Default Printer does persist the new address",
          written.get('printer', 'address', fallback=None) == '10.0.0.5',
          dict(written['printer']) if written.has_section('printer') else None)
finally:
    qt_main._config_path = real_config_path
    qt_main._fallback_config_path = real_fallback_path

# --- quitting must not promote an active session override to the default ---
# closeEvent calls _save_settings() only to persist window geometry, but that
# used to re-save whichever printer was active, silently adopting a session
# override as the new default the moment the app quit.
quit_path = Path(tempfile.mkdtemp()) / 'settings.ini'
qt_main._config_path = lambda: quit_path
qt_main._fallback_config_path = lambda: quit_path
try:
    zw.printer_address, zw.printer_port, zw.printer_dpi = '192.168.1.70', 9100, 203
    zw._default_printer = (zw.printer_address, zw.printer_port,
                           zw.printer_dpi, zw.printer_font_device)
    zw._save_settings()

    real_dialog = qt_dialogs.printer_settings_dialog
    qt_dialogs.printer_settings_dialog = lambda *a, **k: ('10.0.0.8', 9400, 203, 'E')
    try:
        zw.on_session_printer()
    finally:
        qt_dialogs.printer_settings_dialog = real_dialog

    # Stand in for what closeEvent does: it never touches _default_printer,
    # it just calls _save_settings() again on the way out.
    zw._save_settings()
    written = _cfg.ConfigParser(); written.read(quit_path)
    check("quitting with a session override active does not promote it to the default",
          written.get('printer', 'address', fallback=None) == '192.168.1.70',
          dict(written['printer']) if written.has_section('printer') else None)
finally:
    qt_main._config_path = real_config_path
    qt_main._fallback_config_path = real_fallback_path

# --- Label Settings persists its own DPI, never a session-overridden address
label_settings_path = Path(tempfile.mkdtemp()) / 'settings.ini'
qt_main._config_path = lambda: label_settings_path
qt_main._fallback_config_path = lambda: label_settings_path
try:
    zw.printer_address, zw.printer_port, zw.printer_dpi = '192.168.1.80', 9100, 203
    zw._default_printer = (zw.printer_address, zw.printer_port,
                           zw.printer_dpi, zw.printer_font_device)
    zw._save_settings()

    real_dialog = qt_dialogs.printer_settings_dialog
    qt_dialogs.printer_settings_dialog = lambda *a, **k: ('10.0.0.9', 9500, 203, 'E')
    try:
        zw.on_session_printer()
    finally:
        qt_dialogs.printer_settings_dialog = real_dialog

    qt_dialogs.ask_dpi_rescale = lambda *a, **k: 'keep'
    zw.apply_label_settings(900, 600, 300, 3.0, 2.0)

    check("Label Settings updates the persisted default's DPI",
          zw._default_printer[2] == 300, zw._default_printer)
    check("but leaves the persisted default's address alone",
          zw._default_printer[0] == '192.168.1.80', zw._default_printer)

    written = _cfg.ConfigParser(); written.read(label_settings_path)
    check("the settings file reflects the new DPI but the original, non-overridden address",
          (written.get('printer', 'address', fallback=None),
           written.get('printer', 'dpi', fallback=None)) == ('192.168.1.80', '300'),
          dict(written['printer']) if written.has_section('printer') else None)
finally:
    qt_main._config_path = real_config_path
    qt_main._fallback_config_path = real_fallback_path

# --- one visit to Label Settings can move the resolution and the size -------
# They interact: reconciling rescales the whole design, label included, and the
# size typed in the dialog then has to win over the one the rescale produced.
lw = qt_main.ZPLDesignerWindow()
lw._save_settings = lambda *a: None
# Pinned rather than left at the default: this check is about a resolution
# that changes, so the one it starts from has to be stated, not inherited.
lw.printer_dpi = lw.document.dpi = 203
lel = lw.document.add_text_element('scaled')
lel.x, lel.y = 100, 200
lw.canvas.commit()
undo_before = len(lw._undo_stack)
qt_dialogs.ask_dpi_rescale = lambda *a, **k: 'rescale'
lw.apply_label_settings(900, 600, 300, 3.0, 2.0)
moved = lw.document.elements[0]
check("the size typed in the dialog wins over the one a rescale produced",
      (lw.document.label_width, lw.document.label_height) == (900, 600),
      (lw.document.label_width, lw.document.label_height))
check("and the rescale still ran, and ran first",
      (moved.x, moved.y) == (round(100 * 300 / 203), round(200 * 300 / 203)),
      (moved.x, moved.y))
check("the document is stamped with the resolution chosen there",
      lw.document.dpi == 300 and lw.printer_dpi == 300,
      (lw.document.dpi, lw.printer_dpi))
check("the whole visit is one undo entry",
      len(lw._undo_stack) == undo_before + 1,
      (undo_before, len(lw._undo_stack)))
check("and the size is remembered for the next new label",
      lw.label_inches == (3.0, 2.0), lw.label_inches)

# Keep Dots leaves the elements alone - but the label still takes the size the
# dialog was accepted on, which is the whole content of that dialog.
kw = qt_main.ZPLDesignerWindow()
kw._save_settings = lambda *a: None
kw.printer_dpi = kw.document.dpi = 203
kel = kw.document.add_text_element('kept')
kel.x, kel.y = 100, 200
qt_dialogs.ask_dpi_rescale = lambda *a, **k: 'keep'
kw.apply_label_settings(900, 600, 300, 3.0, 2.0)
check("Keep Dots leaves the elements where they were",
      (kw.document.elements[0].x, kw.document.elements[0].y) == (100, 200),
      (kw.document.elements[0].x, kw.document.elements[0].y))
check("but the label still takes the size the dialog was accepted on",
      (kw.document.label_width, kw.document.label_height) == (900, 600),
      (kw.document.label_width, kw.document.label_height))

# --- the preview draws the design, it does not merely agree with it ---------
# Tall enough for six lines of font F at 40 x 40, whose cell is 52 dots tall
pdoc = Document(400, 400, dpi=203)
pt = pdoc.add_text_element('one two three four five six seven eight')
pt.x, pt.y = 0, 0
pt.font_height = pt.font_width = 40
pt.block = FieldBlock(300, 6, 0, 'C', 0)
pdoc.sync_text_width(pt)
preview = ZPLRenderer(400, 400).render(pdoc.to_zpl()).convert('L')
# Laid out by what the canvas lays it out by - font F's cells, since the
# field has no font file - and drawn in the preview's stand-in face
_pcell = pt.cell(pdoc.font_path, pdoc.dpi)
expected = textraster.raster_block(
    pt.text, ZPLRenderer.DEFAULT_FONT_PATH, _pcell.height, _pcell.width,
    pt.block, gap=_pcell.row_gap,
    measure=textraster.measurer(None, _pcell.height, _pcell.width,
                                _pcell.row_gap)[0])
def _rows(get, width, height):
    return [y for y in range(height) if any(get(x, y) for x in range(width))]
preview_rows = _rows(lambda x, y: preview.getpixel((x, y)) < 128,
                     expected.width, expected.height)
raster_rows = _rows(lambda x, y: expected.getpixel((x, y))[3] > 0,
                    expected.width, expected.height)
check("the preview puts a block's lines where the canvas does",
      preview_rows == raster_rows,
      (preview_rows[:6], raster_rows[:6]))

# --- an omitted parameter means what ZPL says it means ----------------------

# ^A0N,40 gave 36 x 20, losing the height it did give, while the preview drew
# it at 40: the model and the preview read the same command differently.
partial = zpl_parser.parse_zpl("^XA^PW812^LL1218^FO50,50^A0N,40^FDHg^FS^XZ")[0].elements[0]
check("^A keeps a height given without a width",
      partial.font_height == 40, partial.font_height)
check("and a scalable font with no width stays proportional",
      partial.font_width == 40, partial.font_width)
full = zpl_parser.parse_zpl("^XA^PW812^LL1218^FO50,50^A0N,40,40^FDHg^FS^XZ")[0].elements[0]
check("so ^A0N,40 and ^A0N,40,40 are the same element",
      (partial.width, partial.height) == (full.width, full.height),
      ((partial.width, partial.height), (full.width, full.height)))

# One size given decides the other, through the font's own cell - for a
# bitmap font too, and whatever width ^CF last set. Printed on a 203 dpi
# printer from the console, the ZPL as written: ^ADN,36 came out 90.7 dots
# wide with an H every 24 (font D doubled both ways - the old reading, ^CF's
# width of 5, is 46 wide with an H every 12, which is what this app then
# sent); ^ADN,54 after ^CFD,36,20 came out 136 wide with an H every 36,
# tripled both ways and not ^CF's 20; and a field under ^CF0,89 printed to
# the dot as ^A0N,89,89 did, 203.7 x 65, where this wrote ^A0N,89,5. A
# second label printed each remaining case within a dot of the one spelling
# its sizes out: ^A0N,40 after ^CF0,40,20 as ^A0N,40,40, ^ADN,,20 as
# ^ADN,36,20, and ^GSN,,60 as ^GSN,60,60. A third printed a 0 as a size left
# out, as the manual says it is: after ^CF0,60,60, ^A0N,40,0 and ^A0N,0,40
# came out an H every 24 as ^A0N,40,40 did, ^ADN,36,0 and ^ADN,0,20 the same
# as ^ADN,36,20, and ^A0N,0,0 and ^ADN,0,0 at ^CF's 60,60 - 140 x 45 with an
# H every 37, and 275 wide with an H every 72.
def _font_of(zpl):
    """(height, width) of the first field `zpl` holds, and the ^A it saves."""
    _doc = zpl_parser.parse_zpl("^XA^PW812^LL1218" + zpl + "^XZ")[0]
    _el = _doc.elements[0]
    return ((_el.font_height, _el.font_width),
            next(l for l in _doc.to_zpl().splitlines() if l.startswith('^A')))

for zpl, want, written, why in (
        ("^FO50,50^ADN,36^FDHHHH^FS", (36, 20), "^ADN,36,20",
         "a bitmap height alone magnifies the width as much - printed 90.7 wide"),
        ("^CFD,36,20^FO50,150^ADN,54^FDHHHH^FS", (54, 30), "^ADN,54,30",
         "and ^CF's width takes no part - printed 136 wide, not 91"),
        ("^CF0,89^FO50,300^FDHHHH^FS", (89, 89), "^A0N,89,89",
         "a ^CF naming only a height is square in font 0 - printed as ^A0N,89,89"),
        ("^CFD,36,20^CF0,89^FO50,300^FDHHHH^FS", (89, 89), "^A0N,89,89",
         "even after a ^CF that set a width, as on the printed label"),
        ("^FO50,50^AFN,18^FDHg^FS", (18, 13), "^AFN,18,13",
         "font F's 18 rounds to one cell, so the width is one cell's 13"),
        ("^CFD,36^FO50,50^FDHg^FS", (36, 20), "^ADN,36,20",
         "^CF follows the same rule for a bitmap font"),
        ("^CF0,40,20^FO50,50^A0N,40^FDHg^FS", (40, 40), "^A0N,40,40",
         "an ^A height alone after a ^CF width is square in font 0"),
        ("^FO50,50^ADN,,20^FDHg^FS", (36, 20), "^ADN,36,20",
         "a width alone decides the height the same way"),
        ("^FO50,50^A0N,,89^FDHg^FS", (89, 89), "^A0N,89,89",
         "and a scalable font given a width alone is square"),
        ("^CF0,30,30^FO50,50^A0N^FDHg^FS", (30, 30), "^A0N,30,30",
         "^A naming no size at all still takes both from ^CF"),
        ("^CFD,36,20^CFE^FO50,50^FDHg^FS", (36, 20), "^AEN,36,20",
         "a ^CF naming neither size keeps both, in its new font"),
        ("^FO50,50^A0N,40,0^FDHHHH^FS", (40, 40), "^A0N,40,40",
         "a width of 0 is one left out - printed as ^A0N,40,40"),
        ("^FO50,50^A0N,0,40^FDHHHH^FS", (40, 40), "^A0N,40,40",
         "and so is a height of 0"),
        ("^FO50,50^ADN,36,0^FDHHHH^FS", (36, 20), "^ADN,36,20",
         "in a bitmap font too - printed as ^ADN,36,20"),
        ("^FO50,50^ADN,0,20^FDHHHH^FS", (36, 20), "^ADN,36,20",
         "whichever of its sizes is 0"),
        ("^CF0,60,60^FO50,50^A0N,0,0^FDHHHH^FS", (60, 60), "^A0N,60,60",
         "both 0 are both ^CF's - printed as ^A0N,60,60"),
        ("^CF0,60,60^FO50,50^ADN,0,0^FDHHHH^FS", (60, 60), "^ADN,60,60",
         "in a bitmap font as well - six times as wide, three times as tall")):
    got, saved = _font_of(zpl)
    check(f"{zpl}: {why}", (got, saved) == (want, written), (got, saved))

# The preview reads ^A through the same function, so it draws each 0 as the
# size the printer printed it at - checked against the ^A spelling it out.
def _ink_of(zpl):
    _image = ZPLRenderer(812, 300).render(
        "^XA^PW812^LL300" + zpl + "^XZ").convert('L')
    return ImageOps.invert(_image).point(lambda v: 255 if v > 128 else 0).getbbox()
for _given, _spelled in (("^A0N,0,40", "^A0N,40,40"), ("^ADN,36,0", "^ADN,36,20"),
                         ("^ADN,0,20", "^ADN,36,20"),
                         ("^CF0,60,60^A0N,0,0", "^A0N,60,60"),
                         ("^CF0,60,60^ADN,0,0", "^ADN,60,60")):
    _got = _ink_of(f"^FO50,50{_given}^FDHHHH^FS")
    _want = _ink_of(f"^FO50,50{_spelled}^FDHHHH^FS")
    check(f"the preview draws {_given} as {_spelled}", _got == _want, (_got, _want))

# The width a file leaves out is written back resolved, so it has to print
# the same: the magnification it names is the one the height gave.
for code, height in (('D', 36), ('D', 54), ('F', 18), ('E', 56), ('A', 30)):
    _h, _w = zpl_fonts.other_size(code, height=height)
    _given = zpl_fonts.bitmap_cell(code, height, _w)
    check(f"^A{code},{height} resolved to width {_w} prints one magnification "
          "both ways",
          _given.height // zpl_fonts._bitmap_base(code)[0]
          == _given.width // zpl_fonts._bitmap_base(code)[1],
          _given)
# At 300 dpi font E's cell is bigger, so the same height is a smaller
# magnification - the resolution the file records has to reach the reader.
_e300 = zpl_parser.parse_zpl("^XA^PW812^LL1218^FXDESIGNER_DPI:300\n"
                             "^FO50,50^AEN,56^FDHg^FS^XZ")[0].elements[0]
check("a 300 dpi label resolves font E against its 300 dpi cell",
      (_e300.font_height, _e300.font_width) == (56, 20),
      (_e300.font_height, _e300.font_width))
_cf300 = zpl_parser.parse_zpl("^XA^PW812^LL1218^FXDESIGNER_DPI:300\n"
                              "^CFE,56^FO50,50^FDHg^FS^XZ")[0].elements[0]
check("and so does a ^CF naming only a height",
      (_cf300.font_height, _cf300.font_width) == (56, 20),
      (_cf300.font_height, _cf300.font_width))

# The preview reads the same two commands with the resolution it has been
# told, so a 300 dpi label's font E is drawn at its 300 dpi cell there too -
# ^A and ^CF each against the ^A that spells the width out.
def _e300_ink(zpl):
    _image = ZPLRenderer(812, 300).render(
        "^XA^PW812^LL300^FXDESIGNER_DPI:300" + zpl + "^XZ").convert('L')
    return ImageOps.invert(_image).point(lambda v: 255 if v > 128 else 0).getbbox()
for _given in ("^FO50,50^AEN,56^FDHHHH^FS", "^CFE,56^FO50,50^FDHHHH^FS"):
    check(f"the preview draws {_given} at 300 dpi as ^AEN,56,20",
          _e300_ink(_given) == _e300_ink("^FO50,50^AEN,56,20^FDHHHH^FS"),
          (_e300_ink(_given), _e300_ink("^FO50,50^AEN,56,20^FDHHHH^FS")))
sizeless = zpl_parser.parse_zpl(
    "^XA^PW812^LL1218^CF0,30,30^FO50,50^A0N^FDHg^FS^XZ")[0].elements[0]
check("^A naming no size at all takes both from ^CF",
      (sizeless.font_height, sizeless.font_width) == (30, 30),
      (sizeless.font_height, sizeless.font_width))

# ^GB's width and height both default to the thickness and clamp up to it,
# which is how ZPL spells a rule. Demanding two numbers dropped the element.
for source, want in (("^GB300", (300, 1, 1)), ("^GB,,4", (4, 4, 4)),
                     ("^GB300,0,4", (300, 4, 4)), ("^GB0,200,4", (4, 200, 4))):
    built = zpl_parser.parse_zpl(f"^XA^PW812^LL1218^FO50,50{source}^FS^XZ")[0].elements
    check(f"{source} is a frame of {want[0]}x{want[1]}",
          len(built) == 1 and (built[0].width, built[0].height,
                               built[0].thickness) == want,
          [(e.width, e.height, e.thickness) for e in built])

offside = zpl_parser.parse_zpl("^XA^PW812^LL1218^FO-10,50^A0N,40,40^FDoff^FS^XZ")[0]
check("^FO keeps a negative coordinate instead of dropping the field",
      len(offside.elements) == 1 and offside.elements[0].x == -10,
      [(e.x, e.y) for e in offside.elements])

# measured, not asserted: the preview's ink has to fall inside the box the
# model claims, for a partial ^A and for a field relying on ^CF
def _preview_ink(zpl, width, height):
    """The bounds of the preview's black ink, as (x, y, w, h)."""
    image = ZPLRenderer(width, height).render(zpl).convert('L')
    dots = [(x, y) for y in range(height) for x in range(width)
            if image.getpixel((x, y)) < 128]
    if not dots:
        return None
    xs = [d[0] for d in dots]
    ys = [d[1] for d in dots]
    return (min(xs), min(ys), max(xs) - min(xs) + 1, max(ys) - min(ys) + 1)

def _inside(ink, element, slack=2):
    if ink is None:
        return False
    return (ink[0] >= element.x - slack and ink[1] >= element.y - slack
            and ink[0] + ink[2] <= element.x + element.width + slack
            and ink[1] + ink[3] <= element.y + element.height + slack)

# Both are compared against the ^A that spells the same font out in full, not
# merely checked for landing inside the box: ink far too small for a box is
# inside it too, which is how a preview that had stopped reading ^CF at all
# went on passing.
spelled_out = _preview_ink("^XA^PW400^LL300^FO50,50^A0N,40,40^FDHg^FS^XZ", 400, 300)
for name, source in (("a partial ^A", "^FO50,50^A0N,40^FDHg^FS"),
                     ("a ^CF default font", "^CF0,40,40^FO50,50^FDHg^FS")):
    page = f"^XA^PW400^LL300{source}^XZ"
    shown = zpl_parser.parse_zpl(page)[0].elements[0]
    drawn = _preview_ink(page, 400, 300)
    check(f"the preview draws {name} inside the box the model gives it",
          _inside(drawn, shown),
          (drawn, (shown.x, shown.y, shown.width, shown.height)))
    check(f"and draws {name} exactly as the ^A that spells it out",
          drawn == spelled_out, (drawn, spelled_out))

# The two read together: a partial ^A inheriting a width ^CF set. Reading ^A
# against anything but the ^CF in force is a divergence only this combination
# shows, since either command alone comes out right by accident.
# ^A names a character width, and the preview threw it away: ^A0N,40,10 and
# ^A0N,40,80 drew the same 178 dots, where the design said 70 and 560. Compared
# against the element rather than between themselves, so drawing all three
# wrong by the same factor does not pass.
for _cw in (10, 40, 80):
    _page = f"^XA^PW700^LL200^FO50,50^A0N,40,{_cw}^FDHamburg^FS^XZ"
    _modelled = zpl_parser.parse_zpl(_page)[0].elements[0].width
    _drawn = _preview_ink(_page, 700, 200)[2]
    # ink is measured, and a glyph does not reach the end of its own advance,
    # so the last character's side bearing is the slack here
    check(f"the preview draws ^A's character width of {_cw}",
          abs(_drawn - _modelled) <= _modelled * 0.06, (_drawn, _modelled))

check("the preview reads a partial ^A as the model does, not against ^CF's width",
      _preview_ink("^XA^PW400^LL300^CF0,40,20^FO50,50^A0N,40^FDHg^FS^XZ", 400, 300)
      == _preview_ink("^XA^PW400^LL300^FO50,50^A0N,40,40^FDHg^FS^XZ", 400, 300),
      (_preview_ink("^XA^PW400^LL300^CF0,40,20^FO50,50^A0N,40^FDHg^FS^XZ", 400, 300),
       _preview_ink("^XA^PW400^LL300^FO50,50^A0N,40,40^FDHg^FS^XZ", 400, 300)))
for _pair in (("^CFD,36,20^FO50,50^ADN,54^FDHHHH^FS", "^FO50,50^ADN,54,30^FDHHHH^FS"),
              ("^CF0,89^FO50,50^FDHHHH^FS", "^FO50,50^A0N,89,89^FDHHHH^FS")):
    _left, _right = (_preview_ink(f"^XA^PW800^LL300{z}^XZ", 800, 300) for z in _pair)
    check(f"the preview draws {_pair[0]} as {_pair[1]}, as the printer did",
          _left == _right, (_left, _right))

# ^FW in the preview, each case against the command that spells the turn out
# - the same comparison ^CF gets above, and for the same reason: ink that is
# merely inside the box would pass an upright field too.
for fw_name, fw_deferring, fw_spelled in (
        ("an ^A with no letter",
         "^FWR^FO50,50^A0,40,40^FDHg^FS", "^FO50,50^A0R,40,40^FDHg^FS"),
        ("a ^CF field",
         "^FWR^CF0,40,40^FO50,50^FDHg^FS", "^FO50,50^A0R,40,40^FDHg^FS"),
        ("a barcode with no letter",
         "^FWR^FO50,50^BY2^BC,80^FD123^FS", "^FO50,50^BY2^BCR,80^FD123^FS"),
        ("an ^A that keeps its own letter",
         "^FWR^FO50,50^A0N,40,40^FDHg^FS", "^FO50,50^A0N,40,40^FDHg^FS")):
    fw_drawn = _preview_ink(f"^XA^PW400^LL300{fw_deferring}^XZ", 400, 300)
    fw_expected = _preview_ink(f"^XA^PW400^LL300{fw_spelled}^XZ", 400, 300)
    check(f"the preview turns {fw_name} under ^FW as the command spelling it out",
          fw_drawn == fw_expected, (fw_drawn, fw_expected))
check("the preview's turned barcode is not simply the upright one",
      _preview_ink("^XA^PW400^LL300^FWR^FO50,50^BY2^BC,80^FD123^FS^XZ", 400, 300)
      != _preview_ink("^XA^PW400^LL300^FO50,50^BY2^BC,80^FD123^FS^XZ", 400, 300))

# a rule is the case where a zero side used to leave nothing to draw at all
check("the preview draws a ^GB rule at its full thickness",
      _preview_ink("^XA^PW400^LL300^FO50,50^GB300,0,4^FS^XZ", 400, 300)
      == (50, 50, 300, 4),
      _preview_ink("^XA^PW400^LL300^FO50,50^GB300,0,4^FS^XZ", 400, 300))

fw = qt_main.ZPLDesignerWindow()
fw.unsaved_changes = False
fw.on_new()
fw.document.set_label_size(400, 300)
fw.document.elements.append(zpl_parser.parse_zpl(
    "^XA^PW400^LL300^FO50,50^GB300,0,4^FS^XZ")[0].elements[0])
fw.canvas.set_zoom(1.0)
ruled = QImage(400, 300, QImage.Format_ARGB32); ruled.fill(Qt.white)
fw.canvas.render(ruled)
ruled_rows = [y for y in range(300)
              if any((ruled.pixel(x, y) & 0xFFFFFF) < 0x646464 for x in range(400))]
check("and the canvas draws it too, four dots thick",
      len(ruled_rows) == 4 and ruled_rows[0] == 50, ruled_rows)

# --- ^GC, the circle ---------------------------------------------------------
# Every parameter optional, as ^GB's are: the diameter defaults to 3 and is
# held to 3-4095, the thickness defaults to 1, and the colour is a letter.
from zplcore.model import CircleElement

for source, want in (("^GC250,10,B", (250, 10, 'B')), ("^GC", (3, 1, 'B')),
                     ("^GC5000", (4095, 1, 'B')), ("^GC1,3", (3, 3, 'B')),
                     ("^GC100,4,W", (100, 4, 'W'))):
    built = zpl_parser.parse_zpl(f"^XA^PW812^LL1218^FO50,50{source}^FS^XZ")[0].elements
    check(f"{source} is a circle of diameter {want[0]}, thickness {want[1]}, colour {want[2]}",
          len(built) == 1 and isinstance(built[0], CircleElement)
          and (built[0].diameter, built[0].thickness, built[0].colour) == want
          and built[0].width == built[0].height == want[0],
          [(type(e).__name__, getattr(e, 'diameter', None),
            getattr(e, 'thickness', None), getattr(e, 'colour', None)) for e in built])
check("^GC is not reported as dropped, now that it is modelled",
      workflow.unsupported_commands("^XA^FO50,50^GC250,10,B^FS^XZ") == [],
      workflow.unsupported_commands("^XA^FO50,50^GC250,10,B^FS^XZ"))

_made_circle = Document().add_circle_element()
check("a circle the designer created writes ^GC with no colour",
      _made_circle.to_zpl()
      == f"^FO{_made_circle.x},{_made_circle.y}\n^GC150,2\n^FS\n",
      _made_circle.to_zpl().replace('\n', ' '))
check("its box is its diameter",
      (_made_circle.width, _made_circle.height) == (150, 150),
      (_made_circle.width, _made_circle.height))
check("a white circle is written back white",
      "^GC100,4,W\n" in zpl_parser.parse_zpl(
          "^XA^FO50,50^GC100,4,W^FS^XZ")[0].elements[0].to_zpl())
_fr_circle = zpl_parser.parse_zpl("^XA^FO50,50^GC100,4^FR^FS^XZ")[0].elements[0]
check("a ^FR after the ^GC reverses the circle, and is written before it",
      _fr_circle.reverse_print and "^FR\n^GC100,4\n" in _fr_circle.to_zpl(),
      _fr_circle.to_zpl().replace('\n', ' '))
_typed_circle = zpl_parser.parse_zpl(
    "^XA^PW812^LL1218^FT50,250^GC100,4^FS^XZ")[0].elements[0]
check("^FT gives a circle its bottom-left corner, as it does a frame",
      (_typed_circle.y, _typed_circle.y + _typed_circle.height) == (150, 250)
      and "^FT50,250\n" in _typed_circle.to_zpl(),
      (_typed_circle.y, _typed_circle.to_zpl().replace('\n', ' ')))
_hidden_doc = Document(400, 400)
_hidden_circle = _hidden_doc.add_circle_element()
_hidden_circle.print_enabled = False
_hidden_back = zpl_parser.parse_zpl(_hidden_doc.to_zpl())[0].elements
check("a hidden circle survives a round trip, still hidden",
      len(_hidden_back) == 1 and isinstance(_hidden_back[0], CircleElement)
      and not _hidden_back[0].print_enabled and _hidden_back[0].diameter == 150,
      [(type(e).__name__, e.print_enabled) for e in _hidden_back])

# the preview draws the circle through the frame's own drawing
_ring_zpl = "^XA^PW400^LL400^FO50,50^GC250,10,B^FS^XZ"
_ring = ZPLRenderer(400, 400).render(_ring_zpl).convert('L')
check("the preview draws ^GC as a ring: ink at the top, none in the corner or the middle",
      _ring.getpixel((175, 52)) < 100 and _ring.getpixel((52, 52)) > 200
      and _ring.getpixel((175, 175)) > 200,
      (_ring.getpixel((175, 52)), _ring.getpixel((52, 52)), _ring.getpixel((175, 175))))
check("and its ink fills exactly the box the model claims",
      _preview_ink(_ring_zpl, 400, 400) == (50, 50, 250, 250),
      _preview_ink(_ring_zpl, 400, 400))
_disc = ZPLRenderer(400, 400).render(
    "^XA^PW400^LL400^FO50,50^GC120,60^FS^XZ").convert('L')
check("a border as thick as the radius fills the circle",
      _disc.getpixel((110, 110)) < 100, _disc.getpixel((110, 110)))
_cutout = ZPLRenderer(400, 400).render(
    "^XA^PW400^LL400^FO50,50^GC250,125^FS^FO100,100^GC150,6,W^FS^XZ").convert('L')
check("a white circle shows only over black, as a white frame does",
      _cutout.getpixel((175, 102)) > 200 and _cutout.getpixel((175, 90)) < 100,
      (_cutout.getpixel((175, 102)), _cutout.getpixel((175, 90))))
_fr_ring = ZPLRenderer(400, 400).render(
    "^XA^PW400^LL400^FO50,50^GC250,125^FS^FO100,100^FR^GC150,6,W^FS^XZ").convert('L')
check("^FR inverts under the circle's own ring, ignoring its colour",
      _fr_ring.getpixel((175, 102)) > 200 and _fr_ring.getpixel((175, 175)) < 100,
      (_fr_ring.getpixel((175, 102)), _fr_ring.getpixel((175, 175))))

cw = qt_main.ZPLDesignerWindow()
cw.unsaved_changes = False
cw.on_new()
cw.document.set_label_size(400, 400)
cw.document.elements.append(CircleElement(50, 50, 250, 10))
cw.canvas.set_zoom(1.0)
ringed = QImage(400, 400, QImage.Format_ARGB32); ringed.fill(Qt.white)
cw.canvas.render(ringed)

def _dark(x, y):
    return (ringed.pixel(x, y) & 0xFFFFFF) < 0x646464

check("and the canvas draws the same ring",
      _dark(175, 52) and not _dark(52, 52) and not _dark(175, 175),
      (_dark(175, 52), _dark(52, 52), _dark(175, 175)))

# a circle has one size, so every drag and scale has to leave it one
for handle, dx, dy, want in (('mr', 40, 10, (100, 100, 190)),
                             ('bm', 10, 40, (100, 100, 190)),
                             ('br', 40, 10, (100, 100, 160)),
                             ('tl', 40, 10, (140, 140, 110)),
                             ('ml', 40, 10, (140, 100, 110)),
                             ('tm', 10, 40, (100, 140, 110))):
    _c = CircleElement(100, 100, 150, 70)
    geometry.resize_by_handle(Document(400, 400), _c, handle, dx, dy)
    check(f"resizing a circle by {handle} keeps it round, from the edge left alone",
          (_c.x, _c.y, _c.diameter) == want and _c.width == _c.height == _c.diameter
          and _c.thickness <= _c.diameter // 2,
          (_c.x, _c.y, _c.width, _c.height, _c.diameter, _c.thickness))
_c = CircleElement(300, 300, 90)
geometry.resize_by_handle(Document(400, 400), _c, 'mr', 500, 0)
check("and a drag past the label edge stops at the label",
      _c.width == _c.height == _c.diameter and _c.x + _c.width <= 400
      and _c.y + _c.height <= 400,
      (_c.x, _c.y, _c.diameter))

def _circle_pair():
    """A circle and a frame, joint box (40, 40, 180, 140), grouped and selected."""
    d = Document(400, 400)
    c, f = CircleElement(40, 40, 60), FrameElement(140, 120, 80, 60)
    d.elements.extend([c, f])
    d.select_many([c, f]); d.group_selected(); d.select(c)
    return d, c, f

d, _c, _f = _circle_pair()
geometry.resize_by_handle(d, d.resize_target(), 'br', 180, 140)   # exactly x2
check("a group scaled x2 doubles its circle",
      (_c.x, _c.y, _c.diameter, _c.width, _c.height) == (40, 40, 120, 120, 120),
      box_of(_c))
d, _c, _f = _circle_pair()
geometry.resize_by_handle(d, d.resize_target(), 'mr', 180, 0)
check("a group stretched along one axis keeps its circle round, at the smaller factor",
      _c.width == _c.height == _c.diameter == 60 and _f.width == 160,
      (box_of(_c), box_of(_f)))

_rescaled = Document()
_rc = _rescaled.add_circle_element()
_rescaled.rescale(300 / 203)
check("a change of resolution scales the diameter and the thickness",
      (_rc.diameter, _rc.width, _rc.height, _rc.thickness) == (222, 222, 222, 3),
      (_rc.diameter, _rc.width, _rc.height, _rc.thickness))
_shrunk = Document(400, 400)
_sc = _shrunk.add_circle_element()
_shrunk.set_label_size(200, 400)
check("a label shrunk under a circle leaves a smaller circle, not an oval",
      (_sc.x, _sc.width, _sc.height, _sc.diameter) == (100, 100, 100, 100),
      (_sc.x, _sc.width, _sc.height, _sc.diameter))

# the editor: one diameter, a thickness held under its radius
_edited = CircleElement(50, 50, 150, 2)
_circle_accepted = []
_circle_dialog = qt_dialogs.edit_circle_dialog(
    None, _edited, on_accept=lambda: _circle_accepted.append(True))
_circle_dialog.findChild(QSpinBox, 'diameter').setValue(80)
_circle_dialog.findChild(QSpinBox, 'thickness').setValue(60)
_circle_dialog.findChild(QComboBox, 'colour').setCurrentIndex(1)
_circle_dialog.findChild(QCheckBox, 'reverse_print').setChecked(True)
_circle_dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.Ok).click()
check("Edit Circle writes a round box, a thickness under the radius, the colour and ^FR",
      _circle_accepted
      and (_edited.diameter, _edited.width, _edited.height, _edited.thickness,
           _edited.colour, _edited.reverse_print) == (80, 80, 80, 40, 'W', True),
      (_edited.diameter, _edited.width, _edited.height, _edited.thickness,
       _edited.colour, _edited.reverse_print))
_small = CircleElement(50, 50, 3, 1)
_small_dialog = qt_dialogs.edit_circle_dialog(None, _small)
_small_dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.Ok).click()
check("a 3-dot circle from a file is still 3 dots after its editor is accepted",
      _small.diameter == 3, _small.diameter)
_cancelled = CircleElement(50, 50, 150, 2)
_cancel_dialog = qt_dialogs.edit_circle_dialog(None, _cancelled)
_cancel_dialog.findChild(QSpinBox, 'diameter').setValue(90)
_cancel_dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.Cancel).click()
check("and Cancel leaves the circle alone", _cancelled.diameter == 150,
      _cancelled.diameter)

# --- ^GD, the diagonal line --------------------------------------------------
# Every parameter optional, as ^GB's are: the thickness defaults to 1, the
# sides to the thickness and are held to 3-32000, and the colour and the
# direction are letters - the direction also spelled as the slash it draws.
from zplcore.model import DiagonalLineElement

for source, want in (("^GD330,183,10,,R", (330, 183, 10, 'B', 'R')),
                     ("^GD100,50,4,W,L", (100, 50, 4, 'W', 'L')),
                     ("^GD100,50,4,,/", (100, 50, 4, 'B', 'R')),
                     ("^GD100,50,4,,\\", (100, 50, 4, 'B', 'L')),
                     ("^GD", (3, 3, 1, 'B', 'R')), ("^GD,,5", (5, 5, 5, 'B', 'R')),
                     ("^GD300", (300, 3, 1, 'B', 'R')),
                     ("^GD40000,1,2", (32000, 3, 2, 'B', 'R'))):
    built = zpl_parser.parse_zpl(f"^XA^PW812^LL1218^FO50,50{source}^FS^XZ")[0].elements
    check(f"{source} is a diagonal line {want}",
          len(built) == 1 and isinstance(built[0], DiagonalLineElement)
          and (built[0].width, built[0].height, built[0].thickness,
               built[0].colour, built[0].direction) == want,
          [(type(e).__name__, box_of(e), getattr(e, 'thickness', None),
            getattr(e, 'colour', None), getattr(e, 'direction', None)) for e in built])
check("^GD is not reported as dropped, now that it is modelled",
      workflow.unsupported_commands("^XA^FO50,50^GD100,50,4^FS^XZ") == [],
      workflow.unsupported_commands("^XA^FO50,50^GD100,50,4^FS^XZ"))

def _diagonal_written(source):
    return zpl_parser.parse_zpl(
        f"^XA^PW812^LL1218^FO50,50{source}^FS^XZ")[0].elements[0].to_zpl()

check("the manual's own ^GD comes back without the defaults it spelled",
      "^GD330,183,10\n" in _diagonal_written("^GD330,183,10,,R"),
      _diagonal_written("^GD330,183,10,,R").replace('\n', ' '))
check("a white, left-leaning line is written back as it was read",
      "^GD100,50,4,W,L\n" in _diagonal_written("^GD100,50,4,W,L"))
check("a backslash is written back as the L it means",
      "^GD100,50,4,B,L\n" in _diagonal_written("^GD100,50,4,,\\"),
      _diagonal_written("^GD100,50,4,,\\").replace('\n', ' '))
_made_diagonal = Document().add_diagonal_element()
check("a line the designer created writes ^GD with no colour or direction",
      _made_diagonal.to_zpl()
      == f"^FO{_made_diagonal.x},{_made_diagonal.y}\n^GD200,150,4\n^FS\n",
      _made_diagonal.to_zpl().replace('\n', ' '))
_fr_diagonal = zpl_parser.parse_zpl("^XA^FO50,50^GD100,50,4^FR^FS^XZ")[0].elements[0]
check("a ^FR after the ^GD reverses the line, and is written before it",
      _fr_diagonal.reverse_print and "^FR\n^GD100,50,4\n" in _fr_diagonal.to_zpl(),
      _fr_diagonal.to_zpl().replace('\n', ' '))
_typed_diagonal = zpl_parser.parse_zpl(
    "^XA^PW812^LL1218^FT50,250^GD100,50,4^FS^XZ")[0].elements[0]
check("^FT gives a diagonal its bottom-left corner, as it does a frame",
      (_typed_diagonal.y, _typed_diagonal.y + _typed_diagonal.height) == (200, 250)
      and "^FT50,250\n" in _typed_diagonal.to_zpl(),
      (_typed_diagonal.y, _typed_diagonal.to_zpl().replace('\n', ' ')))
_unplaced = zpl_parser.parse_zpl("^XA^FO10,20^FS^GD100,50,4^FS^XZ")[0].elements
check("a ^GD with no ^FO of its own opens at the last origin",
      len(_unplaced) == 1 and box_of(_unplaced[0]) == (10, 20, 100, 50),
      [box_of(e) for e in _unplaced])
_kept_doc = Document(400, 400)
_hidden_diagonal = _kept_doc.add_diagonal_element()
_hidden_diagonal.print_enabled = False
_kept_doc.add_frame_element()
_kept_doc.select_many(_kept_doc.elements); _kept_doc.group_selected()
_kept_back = zpl_parser.parse_zpl(_kept_doc.to_zpl())[0].elements
check("a hidden, grouped diagonal survives a round trip, still hidden and grouped",
      isinstance(_kept_back[0], DiagonalLineElement)
      and not _kept_back[0].print_enabled and _kept_back[0].group == (1,)
      and _kept_back[1].group == (1,),
      [(type(e).__name__, e.print_enabled, e.group) for e in _kept_back])

# the preview: a run of `thickness` dots on every row, corner to corner
def _runs(image, x0, x1, rows):
    """For each row, the (first, last) dark column between x0 and x1."""
    runs = []
    for y in rows:
        dark = [x for x in range(x0, x1) if image.getpixel((x, y)) < 128]
        runs.append((dark[0], dark[-1]) if dark else None)
    return runs

_leaning_zpl = "^XA^PW400^LL300^FO50,50^GD200,100,10^FS^XZ"
_leaning = ZPLRenderer(400, 300).render(_leaning_zpl).convert('L')
check("the preview leans ^GD right: its runs run from the bottom-left up to the top-right",
      _runs(_leaning, 0, 400, (149, 50)) == [(50, 59), (240, 249)],
      _runs(_leaning, 0, 400, (149, 50)))
check("with every row exactly as thick as the line",
      all(run is not None and run[1] - run[0] + 1 == 10
          for run in _runs(_leaning, 0, 400, range(50, 150))),
      [run for run in _runs(_leaning, 0, 400, range(50, 150))
       if run is None or run[1] - run[0] + 1 != 10])
check("and its ink fills exactly the box the model claims",
      _preview_ink(_leaning_zpl, 400, 300) == (50, 50, 200, 100),
      _preview_ink(_leaning_zpl, 400, 300))
_backslash = ZPLRenderer(400, 300).render(
    "^XA^PW400^LL300^FO50,50^GD200,100,10,,L^FS^XZ").convert('L')
check("and leans an L the other way, top-left down to bottom-right",
      _runs(_backslash, 0, 400, (50, 149)) == [(50, 59), (240, 249)],
      _runs(_backslash, 0, 400, (50, 149)))
_solid = ZPLRenderer(400, 300).render(
    "^XA^PW400^LL300^FO50,50^GD200,100,500^FS^XZ").convert('L')
check("a line as thick as its box is wide fills the box",
      _runs(_solid, 0, 400, (50, 100, 149)) == [(50, 249)] * 3,
      _runs(_solid, 0, 400, (50, 100, 149)))
_slashed = ZPLRenderer(400, 300).render(
    "^XA^PW400^LL300^FO50,50^GB200,100,100^FS"
    "^FO50,50^GD200,100,10,W^FS^XZ").convert('L')
check("a white line shows only over black, cutting through a solid box",
      _slashed.getpixel((54, 149)) > 200 and _slashed.getpixel((54, 50)) < 100,
      (_slashed.getpixel((54, 149)), _slashed.getpixel((54, 50))))
_fr_line = ZPLRenderer(400, 300).render(
    "^XA^PW400^LL300^FO50,50^GB100,100,100^FS"
    "^FO50,50^FR^GD200,100,10,W^FS^XZ").convert('L')
check("^FR inverts under the line's own run, ignoring its colour",
      _fr_line.getpixel((54, 149)) > 200 and _fr_line.getpixel((245, 50)) < 100
      and _fr_line.getpixel((100, 50)) < 100,
      (_fr_line.getpixel((54, 149)), _fr_line.getpixel((245, 50)),
       _fr_line.getpixel((100, 50))))

dw = qt_main.ZPLDesignerWindow()
dw.unsaved_changes = False
dw.on_new()
dw.document.set_label_size(400, 300)
dw.document.elements.append(DiagonalLineElement(50, 50, 200, 100, 10))
dw.canvas.set_zoom(1.0)
slashed = QImage(400, 300, QImage.Format_ARGB32); slashed.fill(Qt.white)
dw.canvas.render(slashed)

def _slash_dark(x, y):
    return (slashed.pixel(x, y) & 0xFFFFFF) < 0x646464

check("and the canvas draws the same line, the other two corners empty",
      _slash_dark(64, 145) and _slash_dark(241, 52)
      and not _slash_dark(55, 55) and not _slash_dark(245, 145),
      (_slash_dark(64, 145), _slash_dark(241, 52),
       _slash_dark(55, 55), _slash_dark(245, 145)))

# the thickness is a run along each row, so it is held to the width and
# scales with it
_narrowed = DiagonalLineElement(100, 100, 200, 150, 150)
geometry.resize_by_handle(Document(400, 400), _narrowed, 'mr', -120, 0)
check("narrowing a diagonal by its handle holds the thickness to the new width",
      (_narrowed.width, _narrowed.thickness) == (80, 80),
      (_narrowed.width, _narrowed.thickness))
d = Document(400, 400)
_gd, _gf = DiagonalLineElement(40, 40, 60, 60, 10), FrameElement(140, 120, 80, 60)
d.elements.extend([_gd, _gf])
d.select_many([_gd, _gf]); d.group_selected(); d.select(_gd)
geometry.resize_by_handle(d, d.resize_target(), 'mr', 180, 0)      # x2 across
check("a group stretched across scales a diagonal's thickness with its width",
      (box_of(_gd), _gd.thickness) == ((40, 40, 120, 60), 20),
      (box_of(_gd), _gd.thickness))
_rescaled_line = Document()
_rl = _rescaled_line.add_diagonal_element()
_rescaled_line.rescale(300 / 203)
check("a change of resolution scales the box and the thickness",
      (_rl.width, _rl.height, _rl.thickness) == (296, 222, 6),
      (_rl.width, _rl.height, _rl.thickness))

# the editor: a thickness held to the width, the colour, the direction, ^FR
_edited_line = DiagonalLineElement(50, 50, 200, 150, 4)
_line_accepted = []
_line_dialog = qt_dialogs.edit_diagonal_dialog(
    None, _edited_line, on_accept=lambda: _line_accepted.append(True))
_line_dialog.findChild(QSpinBox, 'width').setValue(60)
_line_dialog.findChild(QSpinBox, 'height').setValue(90)
_line_dialog.findChild(QSpinBox, 'thickness').setValue(100)
_line_dialog.findChild(QComboBox, 'colour').setCurrentIndex(1)
_line_dialog.findChild(QComboBox, 'direction').setCurrentIndex(1)
_line_dialog.findChild(QCheckBox, 'reverse_print').setChecked(True)
_line_dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.Ok).click()
check("Edit Diagonal Line writes the box, a thickness held to the width, "
      "the colour, the direction and ^FR",
      _line_accepted
      and (box_of(_edited_line), _edited_line.thickness, _edited_line.colour,
           _edited_line.direction, _edited_line.reverse_print)
      == ((50, 50, 60, 90), 60, 'W', 'L', True),
      (box_of(_edited_line), _edited_line.thickness, _edited_line.colour,
       _edited_line.direction, _edited_line.reverse_print))
check("and the line it leaves is written that way",
      "^FR\n^GD60,90,60,W,L\n" in _edited_line.to_zpl(),
      _edited_line.to_zpl().replace('\n', ' '))
_tiny_line = DiagonalLineElement(50, 50, 3, 3, 1)
_tiny_dialog = qt_dialogs.edit_diagonal_dialog(None, _tiny_line)
_tiny_dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.Ok).click()
_wide_line = DiagonalLineElement(50, 50, 1500, 40, 20)
_wide_dialog = qt_dialogs.edit_diagonal_dialog(None, _wide_line)
_wide_dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.Ok).click()
check("a line from a file keeps its size through an editor accepted unchanged",
      box_of(_tiny_line)[2:] == (3, 3) and box_of(_wide_line)[2:] == (1500, 40),
      (box_of(_tiny_line), box_of(_wide_line)))
_kept_line = DiagonalLineElement(50, 50, 200, 150, 4)
_kept_dialog = qt_dialogs.edit_diagonal_dialog(None, _kept_line)
_kept_dialog.findChild(QComboBox, 'direction').setCurrentIndex(1)
_kept_dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.Cancel).click()
check("and Cancel leaves the line alone", _kept_line.direction == 'R',
      _kept_line.direction)

# + Diagonal, and a double-click onto its editor
dw.on_new()
_before = len(dw._undo_stack)
dw.on_add_diagonal()
_added_line = dw.document.selected_element
check("+ Diagonal adds a selected line and one undo entry",
      isinstance(_added_line, DiagonalLineElement)
      and len(dw._undo_stack) == _before + 1,
      (type(_added_line).__name__, len(dw._undo_stack) - _before))
dw.on_element_double_clicked(_added_line)
check("and a double-click opens Edit Diagonal Line on it",
      id(_added_line) in dw._editors
      and dw._editors[id(_added_line)].windowTitle() == "Edit Diagonal Line",
      list(dw._editors))
dw._close_element_editors()

# --- ^GE, the ellipse -------------------------------------------------------
# Every parameter optional, as ^GD's are: the thickness defaults to 1, the
# sides to the thickness and are held to 3-4095, and the colour is a letter.
for source, want in (("^GE300,100,10,B", (300, 100, 10, 'B')),
                     ("^GE", (3, 3, 1, 'B')), ("^GE,,4", (4, 4, 4, 'B')),
                     ("^GE300", (300, 3, 1, 'B')),
                     ("^GE5000,1,2,W", (4095, 3, 2, 'W'))):
    built = zpl_parser.parse_zpl(f"^XA^PW812^LL1218^FO50,50{source}^FS^XZ")[0].elements
    check(f"{source} is an ellipse {want}",
          len(built) == 1 and isinstance(built[0], EllipseElement)
          and (built[0].width, built[0].height, built[0].thickness,
               built[0].colour) == want,
          [(type(e).__name__, box_of(e), getattr(e, 'thickness', None),
            getattr(e, 'colour', None)) for e in built])
check("^GE is not reported as dropped, now that it is modelled",
      workflow.unsupported_commands("^XA^FO50,50^GE300,100,10^FS^XZ") == [],
      workflow.unsupported_commands("^XA^FO50,50^GE300,100,10^FS^XZ"))

def _ellipse_written(source):
    return zpl_parser.parse_zpl(
        f"^XA^PW812^LL1218^FO50,50{source}^FS^XZ")[0].elements[0].to_zpl()

check("the manual's own ^GE comes back without the colour it spelled",
      "^GE300,100,10\n" in _ellipse_written("^GE300,100,10,B"),
      _ellipse_written("^GE300,100,10,B").replace('\n', ' '))
check("a white ellipse is written back white",
      "^GE100,50,4,W\n" in _ellipse_written("^GE100,50,4,W"))
_made_ellipse = Document().add_ellipse_element()
check("an ellipse the designer created writes ^GE with no colour",
      _made_ellipse.to_zpl()
      == f"^FO{_made_ellipse.x},{_made_ellipse.y}\n^GE200,150,2\n^FS\n",
      _made_ellipse.to_zpl().replace('\n', ' '))
_fr_ellipse = zpl_parser.parse_zpl("^XA^FO50,50^GE100,50,4^FR^FS^XZ")[0].elements[0]
check("a ^FR after the ^GE reverses the ellipse, and is written before it",
      _fr_ellipse.reverse_print and "^FR\n^GE100,50,4\n" in _fr_ellipse.to_zpl(),
      _fr_ellipse.to_zpl().replace('\n', ' '))
_typed_ellipse = zpl_parser.parse_zpl(
    "^XA^PW812^LL1218^FT50,250^GE100,50,4^FS^XZ")[0].elements[0]
check("^FT gives an ellipse its bottom-left corner, as it does a frame",
      (_typed_ellipse.y, _typed_ellipse.y + _typed_ellipse.height) == (200, 250)
      and "^FT50,250\n" in _typed_ellipse.to_zpl(),
      (_typed_ellipse.y, _typed_ellipse.to_zpl().replace('\n', ' ')))
_unplaced = zpl_parser.parse_zpl("^XA^FO10,20^FS^GE100,50,4^FS^XZ")[0].elements
check("a ^GE with no ^FO of its own opens at the last origin",
      len(_unplaced) == 1 and box_of(_unplaced[0]) == (10, 20, 100, 50),
      [box_of(e) for e in _unplaced])
_unended = zpl_parser.parse_zpl(
    "^XA^FO10,10^GE100,50,2^FO200,200^FDnext^FS^XZ")[0].elements
check("a ^GE that never saw ^FS ends at the next ^FO",
      [type(e).__name__ for e in _unended] == ['EllipseElement', 'TextElement']
      and box_of(_unended[0]) == (10, 10, 100, 50),
      [(type(e).__name__, box_of(e)) for e in _unended])
_kept_doc = Document(400, 400)
_hidden_ellipse = _kept_doc.add_ellipse_element()
_hidden_ellipse.print_enabled = False
_kept_doc.add_frame_element()
_kept_doc.select_many(_kept_doc.elements); _kept_doc.group_selected()
_kept_back = zpl_parser.parse_zpl(_kept_doc.to_zpl())[0].elements
check("a hidden, grouped ellipse survives a round trip, still hidden and grouped",
      isinstance(_kept_back[0], EllipseElement)
      and not _kept_back[0].print_enabled and _kept_back[0].group == (1,)
      and _kept_back[1].group == (1,),
      [(type(e).__name__, e.print_enabled, e.group) for e in _kept_back])

# the ring: the outer box inset by the thickness, until it meets in the middle
check("an ellipse's hole is its box inset by the thickness on every side",
      geometry.ellipse_hole(EllipseElement(0, 0, 200, 100, 10)) == (10, 10, 180, 80),
      geometry.ellipse_hole(EllipseElement(0, 0, 200, 100, 10)))
check("and there is none once the border reaches half the shorter side",
      geometry.ellipse_hole(EllipseElement(0, 0, 200, 100, 50)) is None,
      geometry.ellipse_hole(EllipseElement(0, 0, 200, 100, 50)))

# the preview cuts the same ring
_oval_zpl = "^XA^PW400^LL300^FO50,50^GE200,100,10^FS^XZ"
_oval = ZPLRenderer(400, 300).render(_oval_zpl).convert('L')
check("the preview draws ^GE as a ring, the thickness deep at the ends of both axes",
      _runs(_oval, 0, 400, (100,)) == [(50, 249)]
      and [_oval.getpixel((150, y)) < 128 for y in (50, 59, 60)] == [True, True, False]
      and [_oval.getpixel((x, 100)) < 128 for x in (59, 60, 239, 240)]
      == [True, False, False, True],
      (_runs(_oval, 0, 400, (100,)),
       [_oval.getpixel((150, y)) for y in (50, 59, 60)],
       [_oval.getpixel((x, 100)) for x in (59, 60, 239, 240)]))
check("with nothing in the corners of its box or the middle",
      _oval.getpixel((52, 52)) > 200 and _oval.getpixel((150, 100)) > 200,
      (_oval.getpixel((52, 52)), _oval.getpixel((150, 100))))
check("and its ink fills exactly the box the model claims",
      _preview_ink(_oval_zpl, 400, 300) == (50, 50, 200, 100),
      _preview_ink(_oval_zpl, 400, 300))
_solid_oval = ZPLRenderer(400, 300).render(
    "^XA^PW400^LL300^FO50,50^GE200,100,50^FS^XZ").convert('L')
check("a border as thick as half the shorter side fills the ellipse",
      _solid_oval.getpixel((150, 100)) < 100 and _solid_oval.getpixel((52, 52)) > 200,
      (_solid_oval.getpixel((150, 100)), _solid_oval.getpixel((52, 52))))
_white_oval = ZPLRenderer(400, 300).render(
    "^XA^PW400^LL300^FO50,50^GE200,100,50^FS"
    "^FO75,75^GE150,50,4,W^FS^XZ").convert('L')
check("a white ellipse shows only over black, as a white frame does",
      _white_oval.getpixel((150, 76)) > 200 and _white_oval.getpixel((150, 90)) < 100,
      (_white_oval.getpixel((150, 76)), _white_oval.getpixel((150, 90))))
_fr_oval = ZPLRenderer(400, 300).render(
    "^XA^PW400^LL300^FO50,50^GB100,100,100^FS"
    "^FO50,50^FR^GE200,100,10,W^FS^XZ").convert('L')
check("^FR inverts under the ellipse's own ring, ignoring its colour",
      _fr_oval.getpixel((54, 100)) > 200 and _fr_oval.getpixel((245, 100)) < 100
      and _fr_oval.getpixel((100, 100)) < 100,
      (_fr_oval.getpixel((54, 100)), _fr_oval.getpixel((245, 100)),
       _fr_oval.getpixel((100, 100))))

ew = qt_main.ZPLDesignerWindow()
ew.unsaved_changes = False
ew.on_new()
ew.document.set_label_size(400, 300)
ew.document.elements.append(EllipseElement(50, 50, 200, 100, 10))
ew.canvas.set_zoom(1.0)
ovalled = QImage(400, 300, QImage.Format_ARGB32); ovalled.fill(Qt.white)
ew.canvas.render(ovalled)

def _oval_dark(x, y):
    return (ovalled.pixel(x, y) & 0xFFFFFF) < 0x646464

check("and the canvas draws the same ring, the corners and the middle empty",
      _oval_dark(150, 54) and _oval_dark(54, 100) and _oval_dark(245, 100)
      and not _oval_dark(52, 52) and not _oval_dark(150, 100),
      (_oval_dark(150, 54), _oval_dark(54, 100), _oval_dark(245, 100),
       _oval_dark(52, 52), _oval_dark(150, 100)))

# two sides, each free, and a border held under half the shorter of them
_squashed = EllipseElement(100, 100, 200, 150, 70)
geometry.resize_by_handle(Document(400, 400), _squashed, 'bm', 0, -100)
check("squashing an ellipse by its handle holds the thickness under half its height",
      (box_of(_squashed), _squashed.thickness) == ((100, 100, 200, 50), 25),
      (box_of(_squashed), _squashed.thickness))

def _ellipse_pair():
    """An ellipse and a frame, joint box (40, 40, 180, 140), grouped and selected."""
    d = Document(400, 400)
    e, f = EllipseElement(40, 40, 60, 40, 4), FrameElement(140, 120, 80, 60)
    d.elements.extend([e, f])
    d.select_many([e, f]); d.group_selected(); d.select(e)
    return d, e, f

d, _e, _f = _ellipse_pair()
geometry.resize_by_handle(d, d.resize_target(), 'br', 180, 140)   # exactly x2
check("a group scaled x2 doubles its ellipse and the border",
      (box_of(_e), _e.thickness) == ((40, 40, 120, 80), 8),
      (box_of(_e), _e.thickness))
d, _e, _f = _ellipse_pair()
geometry.resize_by_handle(d, d.resize_target(), 'mr', 180, 0)
check("a group stretched along one axis stretches the ellipse into a longer oval, "
      "its border by the smaller factor",
      (box_of(_e), _e.thickness) == ((40, 40, 120, 40), 4),
      (box_of(_e), _e.thickness))
_rescaled_oval = Document()
_ro = _rescaled_oval.add_ellipse_element()
_rescaled_oval.rescale(300 / 203)
check("a change of resolution scales the box and the thickness",
      (_ro.width, _ro.height, _ro.thickness) == (296, 222, 3),
      (_ro.width, _ro.height, _ro.thickness))

# the editor: two sides, a thickness held under half the shorter, colour, ^FR
_edited_oval = EllipseElement(50, 50, 200, 150, 2)
_oval_accepted = []
_oval_dialog = qt_dialogs.edit_ellipse_dialog(
    None, _edited_oval, on_accept=lambda: _oval_accepted.append(True))
_oval_dialog.findChild(QSpinBox, 'width').setValue(60)
_oval_dialog.findChild(QSpinBox, 'height').setValue(90)
_oval_dialog.findChild(QSpinBox, 'thickness').setValue(100)
_oval_dialog.findChild(QComboBox, 'colour').setCurrentIndex(1)
_oval_dialog.findChild(QCheckBox, 'reverse_print').setChecked(True)
_oval_dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.Ok).click()
check("Edit Ellipse writes the box, a thickness under half the shorter side, "
      "the colour and ^FR",
      _oval_accepted
      and (box_of(_edited_oval), _edited_oval.thickness, _edited_oval.colour,
           _edited_oval.reverse_print) == ((50, 50, 60, 90), 30, 'W', True),
      (box_of(_edited_oval), _edited_oval.thickness, _edited_oval.colour,
       _edited_oval.reverse_print))
check("and the ellipse it leaves is written that way",
      "^FR\n^GE60,90,30,W\n" in _edited_oval.to_zpl(),
      _edited_oval.to_zpl().replace('\n', ' '))
_tiny_oval = EllipseElement(50, 50, 3, 3, 1)
_tiny_dialog = qt_dialogs.edit_ellipse_dialog(None, _tiny_oval)
_tiny_dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.Ok).click()
_wide_oval = EllipseElement(50, 50, 1500, 40, 20)
_wide_dialog = qt_dialogs.edit_ellipse_dialog(None, _wide_oval)
_wide_dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.Ok).click()
check("an ellipse from a file keeps its size through an editor accepted unchanged",
      (box_of(_tiny_oval)[2:], _tiny_oval.thickness) == ((3, 3), 1)
      and (box_of(_wide_oval)[2:], _wide_oval.thickness) == ((1500, 40), 20),
      (box_of(_tiny_oval), _tiny_oval.thickness,
       box_of(_wide_oval), _wide_oval.thickness))
_kept_oval = EllipseElement(50, 50, 200, 150, 2)
_kept_dialog = qt_dialogs.edit_ellipse_dialog(None, _kept_oval)
_kept_dialog.findChild(QSpinBox, 'width').setValue(90)
_kept_dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.Cancel).click()
check("and Cancel leaves the ellipse alone", _kept_oval.width == 200,
      _kept_oval.width)

# + Ellipse, and a double-click onto its editor
ew.on_new()
_before = len(ew._undo_stack)
ew.on_add_ellipse()
_added_oval = ew.document.selected_element
check("+ Ellipse adds a selected ellipse and one undo entry",
      isinstance(_added_oval, EllipseElement)
      and len(ew._undo_stack) == _before + 1,
      (type(_added_oval).__name__, len(ew._undo_stack) - _before))
ew.on_element_double_clicked(_added_oval)
check("and a double-click opens Edit Ellipse on it",
      id(_added_oval) in ew._editors
      and ew._editors[id(_added_oval)].windowTitle() == "Edit Ellipse",
      list(ew._editors))
ew._close_element_editors()

# --- ^GS, the graphic symbol ------------------------------------------------
# A symbol from the printer's GS font, chosen by the field data: A (R), B (C),
# C TM, D UL, E CSA. It used to be dropped by the parser - and drawn by the
# preview as nine-dot text reading "A", the opposite mistake. GS is a bitmap
# font: h and w are kept as written, and print as the 24 dot cell at the whole
# magnification each rounds to.

def _symbol(source, header="^PW812^LL1218"):
    built = zpl_parser.parse_zpl(f"^XA{header}^FO50,50{source}^FS^XZ")[0].elements
    return built[0] if len(built) == 1 else built

_gs = _symbol("^GSN,50,50^FDA")
check("^GSN,50,50^FDA is a graphic symbol in a 48 dot cell, the whole "
      "magnification 50 rounds to",
      isinstance(_gs, GraphicSymbolElement)
      and (_gs.text, _gs.font_height, _gs.font_width, box_of(_gs))
      == ('A', 50, 50, (50, 50, 48, 48)),
      _gs if isinstance(_gs, list) else (_gs.text, box_of(_gs)))
check("and is written back as it came",
      _gs.to_zpl() == "^FO50,50\n^GSN,50,50\n^FDA^FS\n",
      _gs.to_zpl().replace('\n', ' '))
check("^GS is not reported as dropped, now that it is modelled",
      workflow.unsupported_commands("^XA^FO50,50^GSN,50,50^FDA^FS^XZ") == [],
      workflow.unsupported_commands("^XA^FO50,50^GSN,50,50^FDA^FS^XZ"))

# The manual's own example: a bare ^GS after ^CF takes ^CF's two sizes, and a
# save writes them, since ^CF is folded in on the way in.
_manual_gs = zpl_parser.parse_zpl(
    "^XA^CFD,18,10^FO50,50^FDZEBRA PROGRAMMING^FS"
    "^FO50,75^FDLANGUAGE II (ZPL II )^FS^FO280,75^GS^FDC^FS^XZ")[0].elements
check("the manual's bare ^GS^FDC takes ^CF's height and width",
      isinstance(_manual_gs[2], GraphicSymbolElement)
      and (_manual_gs[2].text, _manual_gs[2].font_height,
           _manual_gs[2].font_width) == ('C', 18, 10)
      and "^GSN,18,10\n^FDC^FS" in _manual_gs[2].to_zpl(),
      [(type(e).__name__, getattr(e, 'font_height', None),
        getattr(e, 'font_width', None)) for e in _manual_gs])
# A 0 in ^GS is a size left out, as ^A's is - printed on the fourth label.
for source, want in (("^GSN,40", (40, 40)), ("^GSN,,30", (30, 30)),
                     ("^GS,20,10", (20, 10)), ("^GSN,0,40", (40, 40))):
    _sized = _symbol(source + "^FDA")
    check(f"{source} is sized {want} under the default font",
          (_sized.font_height, _sized.font_width) == want,
          (_sized.font_height, _sized.font_width))
_turned_gs = _symbol("^GS,40,30^FDAB", header="^PW812^LL1218^FWR")
check("a ^GS that leaves its orientation out turns the way ^FW says, its box "
      "transposed",
      (_turned_gs.orientation, box_of(_turned_gs)) == ('R', (50, 50, 48, 50))
      and "^GSR,40,30\n" in _turned_gs.to_zpl(),
      (_turned_gs.orientation, box_of(_turned_gs)))
check("and a letter that is not a quarter turn is ^FW's too",
      _symbol("^GSX,40,40^FDA", header="^FWI").orientation == 'I',
      _symbol("^GSX,40,40^FDA", header="^FWI").orientation)
check("a cell per character with the font's gap between, so ^FDAB at x1 is "
      "two 24 dot cells and a 2 dot gap",
      _symbol("^GSN,40,30^FDAB").width == 50, _symbol("^GSN,40,30^FDAB").width)
check("a ^GS with no data is no field, as a text field with none is not",
      _symbol("^GSN,40,40") == [], _symbol("^GSN,40,40"))
_typed_gs = zpl_parser.parse_zpl(
    "^XA^PW812^LL1218^FT50,250^GSN,40,40^FDA^FS^XZ")[0].elements[0]
check("^FT names the bottom of the symbol's cell, not Table 33's three "
      "quarters: 48 below ^GSN,40,40's top",
      (_typed_gs.y, _typed_gs.typeset) == (202, 48)
      and _typed_gs.to_zpl().startswith("^FT50,250\n"),
      (_typed_gs.y, _typed_gs.typeset, _typed_gs.to_zpl().replace('\n', ' ')))
# The same label: ^FT50,250^GSN,48,48^FDA, then ^FT^A0N,40,40^FDX with both
# coordinates left out. The (R)'s top printed at 203.2 and the X's bottom at
# 250.4, so the whole 48 dot cell sits on the baseline; and the X's ink began
# at 102.2, so the pen moved on past the cell and the 4 dot gap after it.
_pen_gs = zpl_parser.parse_zpl(
    "^XA^PW812^LL1218^FT50,250^GSN,48,48^FDA^FS^FT^A0N,40,40^FDX^FS^XZ")[0]
check("a printed ^FT^GS: its cell's top 48 above the baseline, and the next "
      "field's ^FT point past the cell and its gap",
      (_pen_gs.elements[0].y, geometry.typeset_point(_pen_gs.elements[1]))
      == (202, (102, 250)),
      (_pen_gs.elements[0].y, geometry.typeset_point(_pen_gs.elements[1])))
_fr_gs = _symbol("^GSN,40,40^FR^FDA")
check("^FR reverses the symbol, and is written just before its data",
      _fr_gs.reverse_print and "^GSN,40,40\n^FR\n^FDA^FS" in _fr_gs.to_zpl(),
      _fr_gs.to_zpl().replace('\n', ' '))
check("^FV stays ^FV",
      "^FVB^FS" in _symbol("^GSN,40,40^FVB").to_zpl(),
      _symbol("^GSN,40,40^FVB").to_zpl().replace('\n', ' '))
_hex_gs = _symbol("^FH^GSN,40,40^FD_43")
check("^FH escapes are decoded to the letter drawn, and written back raw",
      _hex_gs.glyphs() == 'C' and "^FH_^FD_43^FS" in _hex_gs.to_zpl(),
      (_hex_gs.glyphs(), _hex_gs.to_zpl().replace('\n', ' ')))
_kept_doc = Document(400, 400)
_hidden_gs = _kept_doc.add_graphic_symbol_element('B')
_hidden_gs.print_enabled = False
_kept_doc.add_frame_element()
_kept_doc.select_many(_kept_doc.elements); _kept_doc.group_selected()
_kept_back = zpl_parser.parse_zpl(_kept_doc.to_zpl())[0].elements
check("a hidden, grouped symbol survives a round trip, still hidden and grouped",
      isinstance(_kept_back[0], GraphicSymbolElement)
      and _kept_back[0].text == 'B'
      and not _kept_back[0].print_enabled and _kept_back[0].group == (1,),
      [(type(e).__name__, e.print_enabled, e.group) for e in _kept_back])

# the raster every drawing path blits, against a 203 dpi printer: ^GSN,30,30,
# ^GSN,60,60 and ^GSN,90,90^FDABCDE printed at x1, x3 and x4 of a 24 dot cell,
# each symbol 26 dots on from the last at each step - 104.2 apart at x4 - with
# the (R) and (C) 15 across in the cell's top left, the TM 19 x 10 there too,
# the UL mark the whole cell and the CSA mark 22 of its 24 across. The
# strokes are this designer's own; the boxes they fill are the printer's, to
# within one of its dots at each step.
_PRINTED_GS = {'A': (15, 15), 'B': (15, 15), 'C': (19, 10), 'D': (24, 24),
               'E': (22, 24)}
for _size, _times in ((30, 1), (60, 3), (90, 4)):
    _row = graphic_symbols.raster('ABCDE', _size, _size).getchannel('A')
    check(f"^GSN,{_size},{_size}^FDABCDE prints five {24 * _times} dot cells, "
          f"{26 * _times} apart",
          _row.size == (5 * 24 * _times + 4 * 2 * _times, 24 * _times), _row.size)
    for _index, _code in enumerate('ABCDE'):
        _left = _index * 26 * _times
        _ink = _row.crop((_left, 0, _left + 24 * _times, 24 * _times)).point(
            lambda v: 255 if v >= 128 else 0).getbbox()
        _want = tuple(side * _times for side in _PRINTED_GS[_code])
        check(f"...its {_code} inks {_want[0]} x {_want[1]} at the cell's top "
              f"left, as printed",
              _ink is not None and _ink[:2] == (0, 0)
              and abs(_ink[2] - _want[0]) <= _times
              and abs(_ink[3] - _want[1]) <= _times,
              (_ink, _want))
# A second label: ^GSN,24,72^FDABCDE printed each cell three times across and
# once down - the (R) 44.7 x 14.9, the UL mark 71.7 x 23.7, each symbol 77.7
# on from the last - so an h and a w that round to different magnifications
# stretch the cell, as a bitmap font's are.
_wide = graphic_symbols.raster('ABCDE', 24, 72).getchannel('A')
_wide_inks = [_wide.crop((i * 78, 0, i * 78 + 72, 24)).point(
    lambda v: 255 if v >= 128 else 0).getbbox() for i in range(5)]
check("^GSN,24,72^FDABCDE prints 72 x 24 cells, 78 apart, the (R) 45 x 15 and "
      "the UL 72 x 24 at their tops left",
      _wide.size == (5 * 72 + 4 * 6, 24)
      and abs(_wide_inks[0][2] - 45) <= 3 and abs(_wide_inks[0][3] - 15) <= 1
      and _wide_inks[3] == (0, 0, 72, 24),
      (_wide.size, _wide_inks))
check("the magnification stops at x10, as the bitmap fonts' does",
      graphic_symbols.raster('A', 1000, 1000).size == (240, 240),
      graphic_symbols.raster('A', 1000, 1000).size)
_icon_ink = graphic_symbols.icon('A').getchannel('A').getbbox()
check("a menu's (R) is centred in its icon, not left at the cell's top left",
      graphic_symbols.icon('A').size == (24, 24)
      and abs(_icon_ink[0] - (24 - _icon_ink[2])) <= 1
      and abs(_icon_ink[1] - (24 - _icon_ink[3])) <= 1, _icon_ink)
check("a character that is not A to E is a blank cell",
      graphic_symbols.raster('Z', 48, 48).getchannel('A').getbbox() is None
      and graphic_symbols.raster('AZ', 48, 48).getchannel('A').crop(
          (52, 0, 100, 48)).getbbox() is None,
      graphic_symbols.raster('Z', 48, 48).getchannel('A').getbbox())
check("an h and a w that round to different magnifications stretch the cell, "
      "as ^GS's do",
      graphic_symbols.raster('A', 40, 80).size == (72, 48),
      graphic_symbols.raster('A', 40, 80).size)

# the preview draws the symbol - not the letter as text - in every direction
check("the preview draws ^GSN,50,50^FDA as a symbol, not a nine-dot 'A': a (R) "
      "30 across, 15 at x2",
      _preview_ink("^XA^PW400^LL300^FO50,50^GSN,50,50^FDA^FS^XZ", 400, 300)
      == (50, 50, 30, 30),
      _preview_ink("^XA^PW400^LL300^FO50,50^GSN,50,50^FDA^FS^XZ", 400, 300))
for turn in 'NRIB':
    for code, _shown, _name in graphic_symbols.SYMBOLS:
        zpl = f"^XA^PW400^LL300^FO50,50^GS{turn},60,30^FD{code}C^FS^XZ"
        placed = zpl_parser.parse_zpl(zpl)[0].elements[0]
        ink = _preview_ink(zpl, 400, 300)
        check(f"the preview's ^GS{turn} {code} lies inside the box the canvas shows",
              _inside(ink, placed, slack=1), (ink, box_of(placed)))
_inverted_gs = ZPLRenderer(400, 300).render(
    "^XA^PW400^LL300^FO50,50^GB100,100,100^FS"
    "^FO50,50^GSN,100,100^FR^FDB^FS^XZ").convert('L')
check("^FR inverts under the symbol's own ink: its ring is white over black, "
      "and the gap inside it stays black",
      _inverted_gs.getpixel((54, 80)) > 200 and _inverted_gs.getpixel((62, 80)) < 100,
      (_inverted_gs.getpixel((54, 80)), _inverted_gs.getpixel((62, 80))))

# the canvas blits the same raster
gw = qt_main.ZPLDesignerWindow()
gw.unsaved_changes = False
gw.on_new()
gw.document.set_label_size(400, 300)
_drawn_gs = GraphicSymbolElement(50, 50, 'A', 96, 96)
gw.document.elements.append(_drawn_gs)
gw.canvas.set_zoom(1.0)
_gs_image = QImage(400, 300, QImage.Format_ARGB32); _gs_image.fill(Qt.white)
gw.canvas.render(_gs_image)
_gs_alpha = graphic_symbols.raster('A', 96, 96).getchannel('A')
_agree = sum(((_gs_image.pixel(50 + x, 50 + y) & 0xFF) < 128)
             == (_gs_alpha.getpixel((x, y)) >= 128)
             for y in range(96) for x in range(96))
check("the Qt canvas draws the symbol the raster holds",
      _agree / (96 * 96) > 0.97, _agree / (96 * 96))

# a new one, and the sizes a drag or a scale asks for
_made_gs = Document().add_graphic_symbol_element('D')
check("+ Symbol's UL adds a ^GS 48 dots square, the cell doubled, so the file "
      "names the size it prints",
      _made_gs.to_zpl() == f"^FO{_made_gs.x},{_made_gs.y}\n^GSN,48,48\n^FDD^FS\n"
      and box_of(_made_gs)[2:] == (48, 48),
      _made_gs.to_zpl().replace('\n', ' '))
_dragged_gs = GraphicSymbolElement(100, 100, 'AB', 36, 36)
geometry.resize_by_handle(Document(400, 400), _dragged_gs, 'br', 40, 20)
check("a drag short of the next magnification keeps the one there is",
      (_dragged_gs.font_height, _dragged_gs.font_width, box_of(_dragged_gs))
      == (48, 48, (100, 100, 100, 48)),
      (_dragged_gs.font_height, _dragged_gs.font_width, box_of(_dragged_gs)))
geometry.resize_by_handle(Document(400, 400), _dragged_gs, 'br', 60, 30)
check("dragging a corner asks for the largest magnification whose cells fit, "
      "and writes h and w as that many cells",
      (_dragged_gs.font_height, _dragged_gs.font_width, box_of(_dragged_gs))
      == (72, 72, (100, 100, 150, 72)),
      (_dragged_gs.font_height, _dragged_gs.font_width, box_of(_dragged_gs)))
_dragged_up = GraphicSymbolElement(100, 100, 'A', 40, 30, orientation='R')
geometry.resize_by_handle(Document(400, 400), _dragged_up, 'bm', 0, 30)
check("a turned symbol's height is its run, so the bottom handle widens it",
      (_dragged_up.font_height, _dragged_up.font_width, box_of(_dragged_up))
      == (48, 48, (100, 100, 48, 48)),
      (_dragged_up.font_height, _dragged_up.font_width, box_of(_dragged_up)))
_dragged_top = GraphicSymbolElement(100, 100, 'A', 40, 40)
geometry.resize_by_handle(Document(400, 400), _dragged_top, 'tl', -30, -30)
check("and a top-left drag grows it up and left, the far corner held",
      box_of(_dragged_top) == (76, 76, 72, 72), box_of(_dragged_top))
_rescaled_gs = Document()
_rg = _rescaled_gs.add_graphic_symbol_element()
_rescaled_gs.rescale(300 / 203)
check("a change of resolution scales h and w, and the box is the cell they "
      "round to",
      (_rg.font_height, _rg.font_width, _rg.width, _rg.height) == (71, 71, 72, 72),
      (_rg.font_height, _rg.font_width, _rg.width, _rg.height))
_clamped = Document(400, 400)
_cg = _clamped.add_graphic_symbol_element('A')
_cg.font_height = _cg.font_width = 200; _cg.sync_box()
_clamped.set_label_size(150, 400)
check("a label shrunk under a symbol leaves smaller symbols, not a box that "
      "only claims to be smaller",
      _cg.x + _cg.width <= 150 and _cg.width == _cg.font_width,
      (box_of(_cg), _cg.font_width))
_snap_doc = Document()
_sg = _snap_doc.add_graphic_symbol_element('A')
_saved = _snap_doc.snapshot()
_sg.text = 'E'
_snap_doc.restore(_saved)
check("undo puts a symbol's letter back",
      _snap_doc.elements[0].text == 'A', _snap_doc.elements[0].text)

# the editor: which symbol, h, w, orientation and ^FR
_edited_gs = GraphicSymbolElement(50, 50, 'A', 36, 36)
_gs_accepted = []
_gs_dialog = qt_dialogs.edit_graphic_symbol_dialog(
    None, _edited_gs, on_accept=lambda: _gs_accepted.append(True))
_gs_combo = _gs_dialog.findChild(QComboBox, 'symbol')
check("Edit Symbol offers the five", _gs_combo.count() == 5, _gs_combo.count())
_gs_combo.setCurrentIndex(2)
_gs_dialog.findChild(QSpinBox, 'font_height').setValue(60)
_gs_dialog.findChild(QSpinBox, 'font_width').setValue(30)
_gs_dialog.findChild(QComboBox, 'orientation').setCurrentIndex(1)
_gs_dialog.findChild(QCheckBox, 'reverse_print').setChecked(True)
_gs_dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.Ok).click()
check("Edit Symbol writes the symbol, both sizes, the turn and ^FR",
      _gs_accepted
      and (_edited_gs.text, _edited_gs.font_height, _edited_gs.font_width,
           _edited_gs.orientation, _edited_gs.reverse_print, box_of(_edited_gs))
      == ('C', 60, 30, 'R', True, (50, 50, 72, 24)),
      (_edited_gs.text, _edited_gs.font_height, _edited_gs.font_width,
       _edited_gs.orientation, _edited_gs.reverse_print, box_of(_edited_gs)))
check("and the symbol it leaves is written that way",
      "^GSR,60,30\n^FR\n^FDC^FS" in _edited_gs.to_zpl(),
      _edited_gs.to_zpl().replace('\n', ' '))
_pair_gs = GraphicSymbolElement(50, 50, 'AB', 5, 7000)
_pair_dialog = qt_dialogs.edit_graphic_symbol_dialog(None, _pair_gs)
check("data that is not one of the five is offered first, as written",
      _pair_dialog.findChild(QComboBox, 'symbol').count() == 6
      and _pair_dialog.findChild(QComboBox, 'symbol').currentText()
      == "As written: AB",
      _pair_dialog.findChild(QComboBox, 'symbol').currentText())
_pair_dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.Ok).click()
check("so a symbol from a file keeps its data and sizes through an editor "
      "accepted unchanged",
      (_pair_gs.text, _pair_gs.font_height, _pair_gs.font_width) == ('AB', 5, 7000),
      (_pair_gs.text, _pair_gs.font_height, _pair_gs.font_width))
_kept_gs = GraphicSymbolElement(50, 50, 'A', 36, 36)
_kept_gs_dialog = qt_dialogs.edit_graphic_symbol_dialog(None, _kept_gs)
_kept_gs_dialog.findChild(QComboBox, 'symbol').setCurrentIndex(4)
_kept_gs_dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.Cancel).click()
check("and Cancel leaves the symbol alone", _kept_gs.text == 'A', _kept_gs.text)

# + Symbol, its five actions, and a double-click onto its editor
gw.on_new()
check("+ Symbol offers the five, each with its picture beside its name",
      [a.text() for a in gw.symbol_actions]
      == [name for _code, _shown, name in graphic_symbols.SYMBOLS]
      and all(not a.icon().isNull() for a in gw.symbol_actions),
      [a.text() for a in gw.symbol_actions])
_before = len(gw._undo_stack)
gw.symbol_actions[4].trigger()
_added_gs = gw.document.selected_element
check("choosing CSA adds a selected ^GS^FDE and one undo entry",
      isinstance(_added_gs, GraphicSymbolElement) and _added_gs.text == 'E'
      and len(gw._undo_stack) == _before + 1,
      (type(_added_gs).__name__, getattr(_added_gs, 'text', None),
       len(gw._undo_stack) - _before))
gw.on_element_double_clicked(_added_gs)
check("and a double-click opens Edit Symbol on it",
      id(_added_gs) in gw._editors
      and gw._editors[id(_added_gs)].windowTitle() == "Edit Symbol",
      list(gw._editors))
gw._close_element_editors()

# --- ^FT names a baseline where ^FO names a top -----------------------------

typeset = zpl_parser.parse_zpl(
    "^XA^PW812^LL1218^FT50,150^A0N,40,40^FDHg^FS^XZ")[0]
check("^FT opens a field, where it used to drop the whole label",
      len(typeset.elements) == 1, len(typeset.elements))
typed = typeset.elements[0]
check("and its y is a baseline, so the box sits above it",
      typed.y + typed.typeset == 150, (typed.y, typed.typeset))
check("written back as the ^FT it came from, at the same y",
      "^FT50,150" in typed.to_zpl(), typed.to_zpl().replace('\n', ' '))

typed_frame = zpl_parser.parse_zpl(
    "^XA^PW812^LL1218^FT50,250^GB300,200,4^FS^XZ")[0].elements[0]
check("^FT gives everything but text its bottom-left corner",
      (typed_frame.y, typed_frame.y + typed_frame.height) == (50, 250),
      (typed_frame.y, typed_frame.height))

# the baseline is where the file said, not merely somewhere above the y
baseline_page = "^XA^PW400^LL300^FT50,150^A0N,40,40^FDHxy^FS^XZ"
baseline_ink = _preview_ink(baseline_page, 400, 300)
sat_on = baseline_ink[1] + textraster.baseline_offset(
    TextElement(font_code='0').face() or ZPLRenderer.DEFAULT_FONT_PATH, 40)
check("the preview puts the baseline on the y ^FT named",
      abs(sat_on - 150) <= 1, (sat_on, baseline_ink))

rescaled = zpl_parser.parse_zpl(
    "^XA^PW812^LL1218^FT50,300^GB300,200,4^FS^XZ")[0]
rescaled.rescale(1.5)
check("rescaling carries the ^FT offset with the dots",
      "^FT75,450" in rescaled.elements[0].to_zpl(),
      rescaled.elements[0].to_zpl().replace('\n', ' '))

# --- the field origin is printer state, not part of one field ---------------

# The manual's own ^IS example (page 243), as printed there. Its border has no
# ^FO before it, and ARTICLE# has none of its own: it prints at the ^FO15,180
# an empty field before it set. Both used to be dropped without a word.
_is_example = ("^XA\n^LH10,15^FWN^BY3,3,85^CFD,36\n^GB430,750,4^FS\n"
               "^FO10,170^GB200,144,2^FS\n^FO10,318^GB410,174,2^FS\n"
               "^FO212,170^GB206,144,2^FS\n^FO10,498^GB200,120,2^FSR\n"
               "^FO212,498^GB209,120,2^FS\n^FO4,150^GB422,10,10^FS\n"
               "^FO135,20^A0,70,60\n^FDZEBRA^FS\n"
               "^FO80,100^A0,40,30\n^FDTECHNOLOGIES CORP^FS\n"
               "^FO15,180^CFD,18,10^FS\n^FDARTICLE#^FS\n"
               "^FO218,180\n^FDLOCATION^FS\n^FO15,328\n^FDDESCRIPTION^FS\n"
               "^FO15,508\n^FDREQ.NO.^FS\n^FO220,508\n^FDWORK NUMBER^FS\n"
               "^FO15,630^AD,36,20\n^FDCOMMENTS:^FS\n^XZ")
_isd = zpl_parser.parse_zpl(_is_example)[0]
check("the manual's ^IS example opens with all 15 of its fields",
      len(_isd.elements) == 15, len(_isd.elements))
_border = _isd.elements[0]
check("the border with no ^FO is at 0,0, plus ^LH",
      isinstance(_border, FrameElement)
      and (_border.x, _border.y, _border.width) == (10, 15, 430),
      (type(_border).__name__, _border.x, _border.y))
_article = [e for e in _isd.elements
            if getattr(e, 'text', None) == 'ARTICLE#']
check("ARTICLE# prints at the origin an earlier, empty field set",
      len(_article) == 1 and (_article[0].x, _article[0].y) == (25, 195)
      and _article[0].font_code == 'D',
      [(e.x, e.y, e.font_code) for e in _article])
_is_saved = _isd.to_zpl()
check("and saves every one with its origin spelled out, stably",
      zpl_parser.parse_zpl(_is_saved)[0].to_zpl() == _is_saved
      and len(zpl_parser.parse_zpl(_is_saved)[0].elements) == 15)
check("the preview draws the border where the model has it",
      _preview_ink("^XA^GB100,80,4^FS^XZ", 200, 200)
      == _preview_ink("^XA^FO0,0^GB100,80,4^FS^XZ", 200, 200) == (0, 0, 100, 80),
      _preview_ink("^XA^GB100,80,4^FS^XZ", 200, 200))

# Page 127's spelling: an omitted coordinate is 0, not a reason to drop the field
_qr = zpl_parser.parse_zpl("^XA^FO,20,20^BQ,2,10^FDMM,Atest^FS^XZ")[0]
check("^FO,20,20 opens a field at 0,20",
      len(_qr.elements) == 1 and isinstance(_qr.elements[0], BarcodeElement)
      and (_qr.elements[0].x, _qr.elements[0].y) == (0, 20),
      [(type(e).__name__, e.x, e.y) for e in _qr.elements])

# A barcode command before its ^FO belongs to the field the ^FO places. Ending
# the field at the ^FO kept the ^FD and lost the ^BC: nine-dot text.
_early = zpl_parser.parse_zpl("^XA^BCN,80^FO10,10^FD123^FS^XZ")[0]
check("a ^BC ahead of its ^FO is still a barcode, at the ^FO",
      len(_early.elements) == 1
      and isinstance(_early.elements[0], BarcodeElement)
      and (_early.elements[0].x, _early.elements[0].y) == (10, 10),
      [(type(e).__name__, e.x, e.y) for e in _early.elements])
check("and the preview draws the same barcode",
      _preview_ink("^XA^BCN,80^FO10,10^FD123^FS^XZ", 300, 200)
      == _preview_ink("^XA^FO10,10^BCN,80^FD123^FS^XZ", 300, 200),
      _preview_ink("^XA^BCN,80^FO10,10^FD123^FS^XZ", 300, 200))

# A field that lost its ^FS still ends at the next ^FO
_unended = zpl_parser.parse_zpl("^XA^FO10,10^FDA^FO20,20^FDB^FS^XZ")[0]
check("a field with data ends at the next ^FO, as before",
      [(e.x, e.y, e.text) for e in _unended.elements]
      == [(10, 10, 'A'), (20, 20, 'B')],
      [(e.x, e.y, e.text) for e in _unended.elements])

_two = zpl_parser.parse_zpl(
    "^XA^FO50,60^FDa^FS^XZ\n^XA^GB40,40,2^FS^XZ")[0]
check("^XA puts the field origin back at 0,0",
      (_two.elements[1].x, _two.elements[1].y) == (0, 0),
      [(e.x, e.y) for e in _two.elements])

_homed = zpl_parser.parse_zpl("^XA^FO10,10^FDa^FS^LH30,40^FDb^FS^XZ")[0]
check("a field with no ^FO takes the ^LH in force when it opens",
      (_homed.elements[1].x, _homed.elements[1].y) == (40, 50),
      [(e.x, e.y) for e in _homed.elements])

# ^FN#^FD with no origin is a value for the fields of that number - a recall
# call is nothing else - so it must not also become a field of its own.
_values = "^XA^XFR:SAMPLE.GRF^FN1^FDhello^FS^XZ"
check("an origin-less ^FN value is not drawn as a field",
      zpl_parser.parse_zpl(_values)[0].elements == []
      and _preview_ink(_values, 200, 200) is None,
      zpl_parser.parse_zpl(_values)[0].elements)

# "Once a value for ^A@ is defined, it represents that font until a new font
# name is specified by ^A@."
_named_font = zpl_parser.parse_zpl(
    "^XA^FO0,0^A@N,30,30,E:FOO.TTF^FDa^FS"
    "^FO0,50^A@N,20,20^FDb^FS^FO0,90^A0N,20,20^FDc^FS^XZ")[0]
check("an ^A@ with no path goes on meaning the last one named",
      [e.printer_font_spec for e in _named_font.elements]
      == ['E:FOO.TTF', 'E:FOO.TTF', None],
      [e.printer_font_spec for e in _named_font.elements])

# --- ^FT with a coordinate left out follows the last field ------------------

# The manual's own example (page 200), as printed there: after the first, each
# field strings along the baseline from where the one before it ended.
CHAIN = ("^XA\n^FT10,200^A0N,30,20^FDACME ^FS\n^FT^GS^FDC^FS\n"
         "^FT^A0N,30,20^FDSummer ^FS\n^FT^A0N,60,50^FDClearance ^FS\n"
         "^FT^A0N,120,100^FDSale ^FS\n^XZ")


def _chained(zpl=CHAIN):
    return zpl_parser.parse_zpl(zpl)[0]


def _pen(element):
    """How far an upright field moves the pen along: a line's width, and a
    ^GS's run and the gap after its last symbol, as a printer showed."""
    if isinstance(element, GraphicSymbolElement):
        return element.advance()
    return element.width


def _ends(elements):
    """Where each element's ^FT point is, and where the one before it ends,
    for an upright line: its right end on its baseline."""
    return [(geometry.typeset_point(after),
             (before.x + _pen(before), geometry.typeset_point(before)[1]))
            for before, after in zip(elements, elements[1:])]


_chain = _chained()
_links = _chain.elements
check("the manual's ^FT example opens with all five of its fields",
      [e.element_type for e in _links]
      == ['text', 'graphic_symbol', 'text', 'text', 'text'],
      [e.element_type for e in _links])
check("each after the first starts where the one before it ends",
      all(at == end for at, end in _ends(_links)), _ends(_links))
check("all on the first one's baseline, so none is above the label",
      [geometry.typeset_point(e)[1] for e in _links] == [200] * 5
      and min(e.y for e in _links) >= 0,
      [(e.y, geometry.typeset_point(e)) for e in _links])
_chain_zpl = _chain.to_zpl()
check("a save leaves out the coordinates the file left out",
      _chain_zpl.count("^FT\n") == 4 and "^FT10,200\n" in _chain_zpl,
      _chain_zpl.replace('\n', ' '))
check("and opens again with every field where it was",
      [(e.x, e.y) for e in _chained(_chain_zpl).elements]
      == [(e.x, e.y) for e in _links])

# The preview reads the file for itself, so it has to string the fields the
# same way the model does: against the same label with each one written where
# the model put it.
_page = CHAIN.replace("^XA\n", "^XA^PW600^LL300\n", 1)
_pinned = _chained(_page)
for _link in _pinned.elements:
    _link.follows = None
_pinned_zpl = _pinned.to_zpl()
check("the preview strings them where the model does, dot for dot",
      "^FT\n" not in _pinned_zpl
      and ZPLRenderer(600, 300).render(_page).tobytes()
      == ZPLRenderer(600, 300).render(_pinned_zpl).tobytes()
      == ZPLRenderer(600, 300).render(_chained(_page).to_zpl()).tobytes(),
      _pinned_zpl.replace('\n', ' '))
# ...and after a box, a bar code, and a line turned down the label, which
# carry on along the way they read
_mixed = ("^XA^PW600^LL400^FT20,100^A0N,30,30^FDAB^FS^FT^GB40,20,20^FS"
          "^FT^A0N,30,30^FDCD^FS^FT^BY2^BCN,40^FD12^FS"
          "^FT^A0R,30,30^FDEF^FS^FT^A0R,30,30^FDGH^FS^XZ")
_mixed_doc = _chained(_mixed)
for _link in _mixed_doc.elements:
    _link.follows = None
check("and after a box, a bar code and turned text too",
      len(_mixed_doc.elements) == 6
      and ZPLRenderer(600, 400).render(_mixed).tobytes()
      == ZPLRenderer(600, 400).render(_mixed_doc.to_zpl()).tobytes(),
      _mixed_doc.to_zpl().replace('\n', ' '))
_ef, _gh = _chained(_mixed).elements[4:]
check("a line turned to R is followed down the label, not across it",
      (_gh.x, _gh.y) == (_ef.x, _ef.y + _ef.height),
      [(e.x, e.y, e.width, e.height) for e in (_ef, _gh)])
_ab, _box, _cd = _chained(_mixed).elements[:3]
check("a box leaves the pen where its ^FT put it, not after it",
      geometry.typeset_point(_box) == (_ab.x + _ab.width, 100)
      and geometry.typeset_point(_cd) == geometry.typeset_point(_box),
      [geometry.typeset_point(e) for e in (_ab, _box, _cd)])

# What a 203 dpi printer made of each thing the manual leaves open, printed
# from the console as tests/fixtures/ft_chain_rules.zpl, in dots off the scan
_rules = _chained((FIXTURES / 'ft_chain_rules.zpl').read_text())
(_fo, _next, _text, _frame, _after, _xonly, _y, _yonly, _x, _rot,
 _ated) = _rules.elements
check("after ^FO text the pen is at its right end, on its baseline",
      geometry.typeset_point(_next) == (_fo.x + _fo.width, 70),
      geometry.typeset_point(_next))
# "after" printed with its baseline on the box's top edge and its a 1.4 dots
# right of the box's left edge: not after the box, nor after "text"
check("after an ^FO box the pen is at the box's ^FO, as printed",
      geometry.typeset_point(_after) == (300, 120),
      geometry.typeset_point(_after))
check("^FT400 took the baseline before it, and ^FT,420 the x it ended at",
      geometry.typeset_point(_y) == (400, 260)
      and geometry.typeset_point(_x) == (_yonly.x + _yonly.width, 420),
      [geometry.typeset_point(e) for e in (_y, _x)])
check("and ATED carried on down the label from the end of ROT",
      (_ated.x, _ated.y) == (_rot.x, _rot.y + _rot.height),
      [(e.x, e.y, e.width, e.height) for e in (_rot, _ated)])
_rules_zpl = _rules.to_zpl()
check("the label saves its bare ^FTs bare",
      _rules_zpl.count("^FT\n") == 3 and "^FT400\n" in _rules_zpl
      and "^FT,420\n" in _rules_zpl
      and _chained(_rules_zpl).to_zpl() == _rules_zpl,
      _rules_zpl.replace('\n', ' '))
for _link in _rules.elements:
    _link.follows = None
check("and the preview puts every field where the model does",
      ZPLRenderer(812, 1218).render(
          (FIXTURES / 'ft_chain_rules.zpl').read_text()).tobytes()
      == ZPLRenderer(812, 1218).render(_rules.to_zpl()).tobytes(),
      _rules.to_zpl().replace('\n', ' '))

# A field turned by ^A is placed from the baseline in its own frame, turned
# with it. The same printer, sent ^FT100,500^A0R,40,40^FDROTATED^FS, put its
# first character from y = 502.5 and its ink from x = 97.7 to 129.5, running
# down the label: the baseline down the label at x, the run from y.
_turned_src = "^XA^PW812^LL1218^FT100,500^A0R,40,40^FDROTATED^FS^XZ"
_turned = zpl_parser.parse_zpl(_turned_src)[0]
_rotated = _turned.elements[0]
check("^FT at R puts the baseline down the label at x, and the run from y",
      (_rotated.x, _rotated.y)
      == (100 - (_rotated.width - _rotated.typeset), 500)
      and geometry.typeset_point(_rotated) == (100, 500)
      and "^FT100,500\n" in _turned.to_zpl(),
      (_rotated.x, _rotated.y, _rotated.width, _rotated.typeset))
_printed = ZPLRenderer(812, 1218).render(_turned_src).convert('L').point(
    lambda v: 255 if v < 128 else 0).getbbox()
check("and the preview draws it within 3 dots of where it printed",
      _printed is not None and abs(_printed[0] - 97.7) <= 3
      and abs(_printed[2] - 1 - 129.5) <= 3 and abs(_printed[1] - 502.5) <= 3,
      _printed)
_turned_canvas = qt_canvas.DesignCanvas(_turned)
_turned_canvas.set_zoom(1.0)
_turned_canvas.resize(812, 1218)
_surface = QImage(812, 1218, QImage.Format_ARGB32); _surface.fill(Qt.white)
_turned_canvas.render(_surface)
_drawn = _ink_box(_surface, _rotated)
# The canvas starts its ink a few dots along the run, as it does upright
check("so does the canvas, across the label, and within 5 dots down it",
      _drawn is not None and abs(_drawn[0] - 97.7) <= 3
      and abs(_drawn[2] - 129.5) <= 3 and abs(_drawn[1] - 502.5) <= 5,
      _drawn)

# Tables 45-48 draw where ^FT names at each turn: where the first baseline
# starts - its left end upright, its top at R, its right end at I, its
# bottom at B - or where it ends when right justified
_charted = {'N': (lambda e, b: (e.x, e.y + b),
                  lambda e, b: (e.x + e.width, e.y + b)),
            'R': (lambda e, b: (e.x + e.width - b, e.y),
                  lambda e, b: (e.x + e.width - b, e.y + e.height)),
            'I': (lambda e, b: (e.x + e.width, e.y + e.height - b),
                  lambda e, b: (e.x, e.y + e.height - b)),
            'B': (lambda e, b: (e.x + b, e.y + e.height),
                  lambda e, b: (e.x + b, e.y))}
for _facing, (_left_point, _right_point) in _charted.items():
    _found = []
    for _justify, _point in (('', _left_point), (',1', _right_point)):
        _src = (f"^XA^PW812^LL1218^FT300,400{_justify}^A0{_facing},40,40"
                "^FDTurned^FS^XZ")
        _doc = zpl_parser.parse_zpl(_src)[0]
        _el = _doc.elements[0]
        _back = zpl_parser.parse_zpl(_doc.to_zpl())[0].elements[0]
        _ink = ZPLRenderer(812, 1218).render(_src).convert('L').point(
            lambda v: 255 if v < 128 else 0).getbbox()
        _found.append(
            _point(_el, _el.typeset) == (300, 400)
            and geometry.typeset_point(_el) == (300, 400)
            and (_back.x, _back.y, _back.width, _back.height)
            == (_el.x, _el.y, _el.width, _el.height)
            and _ink is not None and _ink[0] >= _el.x - 1
            and _ink[1] >= _el.y - 1 and _ink[2] <= _el.x + _el.width + 1
            and _ink[3] <= _el.y + _el.height + 1)
    check(f"^FT at {_facing} names the charts' point, left and right "
          "justified, saves back there, and the preview draws inside the box",
          all(_found), _found)

# At R the baseline gap lies across the label, so a stretch across it
# stretches the gap with the font's height
_stretched = zpl_parser.parse_zpl(_turned_src)[0]
_wide = _stretched.elements[0]
geometry.scale_element(_stretched, _wide, 0, 0, 2.0, 1.0)
check("a field turned to R stretched across the label keeps its baseline "
      "in proportion",
      (_wide.font_height, _wide.typeset) == (80, 60),
      (_wide.font_height, _wide.typeset))

# The third label, tests/fixtures/ft_turned.zpl, printed from the console on
# the same printer, with tick marks at each ^FT's own coordinates. Measured
# in dots off a 300 ppi scan, registered on its two rules: each field's
# baseline, and the end of its run its ^FT names, at every turn. The run's
# end sits in from the ticks by the glyph's side bearing, as upright.
_label3_src = (FIXTURES / 'ft_turned.zpl').read_text()
_label3 = _chained(_label3_src)
_words = {e.text: e for e in _label3.elements if e.element_type == 'text'}
_label3_ink = ZPLRenderer(812, 1218).render(_label3_src).convert('L').point(
    lambda v: 255 if v < 128 else 0)


def _label3_edges(x0, y0, x1, y1):
    """The preview's ink inside a window clear of the ticks, as absolute
    left, top, right and bottom edges."""
    box = _label3_ink.crop((x0, y0, x1, y1)).getbbox()
    return (x0 + box[0], y0 + box[1], x0 + box[2], y0 + box[3])


# word: (window, (edge, printed) for its baseline, and for the end its ^FT
# names), edges numbered left, top, right, bottom
_label3_printed = {
    'LEFT': ((95, 80, 200, 126), (3, 120.9), (0, 103.6)),
    'TILE': ((95, 245, 140, 340), (0, 100.5), (1, 251.7)),
    'LIFE': ((340, 245, 405, 290), (1, 250.1), (2, 397.6)),
    'FILE': ((610, 280, 655, 345), (2, 650.7), (3, 337.1)),
    'HILT': ((95, 470, 140, 565), (0, 100.3), (3, 559.3)),
    'FELT': ((295, 445, 400, 490), (1, 449.6), (0, 301.7)),
    'HEFT': ((610, 445, 655, 550), (2, 651.1), (1, 450.8)),
    'TELL': ((255, 245, 320, 290), (1, 250.4), None),
    'HELL': ((610, 185, 655, 262), (2, 650.6), None),
}
_off = {}
for _word, (_window, *_marks) in _label3_printed.items():
    _edges = _label3_edges(*_window)
    _off[_word] = [round(_edges[edge] - printed, 1)
                   for edge, printed in filter(None, _marks)]
check("the third label: at N, R, I and B, left and right justified, the "
      "preview puts each baseline and each run's named end within 2 dots of "
      "the print",
      all(abs(miss) <= 2 for misses in _off.values() for miss in misses),
      _off)
# TELL carried on leftward from LIFE and HELL up the label from FILE, each on
# its leader's baseline: the gap between the two words printed 2.7 and 3.4
# dots, and the chain puts each follower's ^FT at its leader's end
_life, _tell = _words['LIFE'], _words['TELL']
_file, _hell = _words['FILE'], _words['HELL']
_gaps = (_label3_edges(326, 245, 405, 290)[0]
         - _label3_edges(240, 245, 326, 290)[2],
         _label3_edges(610, 266, 655, 345)[1]
         - _label3_edges(610, 170, 655, 266)[3])
check("a line at I is followed leftward along its baseline, and one at B up "
      "the label, as printed",
      geometry.typeset_point(_tell) == (_life.x, 250)
      and geometry.typeset_point(_hell) == (650, _file.y)
      and abs(_gaps[0] - 2.7) <= 1.5 and abs(_gaps[1] - 3.4) <= 1.5,
      (geometry.typeset_point(_tell), geometry.typeset_point(_hell), _gaps))
_label3_zpl = _label3.to_zpl()
check("and the third label saves its two bare ^FTs bare",
      _label3_zpl.count("^FT\n") == 2
      and _chained(_label3_zpl).to_zpl() == _label3_zpl,
      _label3_zpl.replace('\n', ' '))

# The fourth label, tests/fixtures/gs_turned_sizes.zpl, printed from the
# console on the same printer: the ^GS cases no print had settled, with the
# third label's two rules and tick marks at each turned ^FT's coordinates.
# The UL mark throughout, since it fills its whole cell. Measured in dots off
# a 300 ppi scan, registered on the rules, each tick within a dot of where
# the file put it:
# - a ^GS turned by its own o and placed by ^FT turned its frame about the
#   point, as text does - its cell from x = 249.6 and y = 149.6 down at R,
#   hanging below the line at I and standing left of it at B - where keeping
#   the offset down the label had drawn each 48 dots away;
# - the X after each, placed by a bare ^FT, carried on along the symbol's
#   run, past its cell and the gap after it;
# - a 0 in ^GS is a size left out, as ^A's is: ^GSN,0,48 and ^GSN,48,0 printed
#   as ^GSN,48,48 beside them, and ^GSN,0,0 as ^CF0,72,72's 72;
# - ^FT stands the magnified cell on its line, not h: ^GSN,40, ,30 and ,60
#   each printed level with ^GSN,48, ,24 and ,72.
_gs4_src = (FIXTURES / 'gs_turned_sizes.zpl').read_text()
_gs4 = _chained(_gs4_src)
_gs4_ink = ZPLRenderer(812, 1218).render(_gs4_src).convert('L').point(
    lambda v: 255 if v < 128 else 0)
# what: (a window clear of the ticks and the rest, the printed left, top,
# right and bottom edges)
_gs4_printed = {
    'N': ((75, 95, 129, 155), (79.8, 103.1, 127.3, 151.0)),
    'X after N': ((130, 95, 170, 155), (132.1, 120.2, 152.4, 150.3)),
    'R': ((245, 140, 305, 200), (249.6, 149.6, 297.8, 198.1)),
    'X after R': ((245, 200, 305, 240), (249.6, 202.9, 280.1, 222.8)),
    'I': ((470, 190, 575, 255), (473.0, 199.5, 521.2, 248.0)),
    'X after I': ((430, 190, 470, 240), (447.9, 199.5, 468.2, 230.3)),
    'B': ((645, 200, 705, 255), (653.6, 202.9, 701.9, 250.8)),
    'X after B': ((660, 160, 712, 199), (670.6, 177.6, 700.5, 197.5)),
    '^GSN,0,48': ((50, 330, 120, 420), (59.4, 340.3, 107.6, 388.2)),
    '^GSN,48,0': ((250, 330, 320, 420), (259.7, 340.3, 308.0, 388.2)),
    '^GSN,0,0': ((450, 330, 545, 420), (460.1, 339.6, 532.8, 412.1)),
    '^FT^GSN,40': ((50, 480, 120, 570), (60.1, 513.3, 107.6, 561.1)),
    '^FT^GSN,30': ((250, 480, 300, 570), (260.4, 536.5, 284.2, 561.1)),
    '^FT^GSN,60': ((390, 470, 485, 570), (400.3, 488.6, 472.3, 560.4)),
}
_gs4_off = {}
for _what, ((_x0, _y0, _x1, _y1), _edges) in _gs4_printed.items():
    _box = _gs4_ink.crop((_x0, _y0, _x1, _y1)).getbbox()
    if _box is None:            # drawn somewhere else entirely
        _gs4_off[_what] = [float('inf')]
        continue
    _drawn = (_x0 + _box[0], _y0 + _box[1], _x0 + _box[2], _y0 + _box[3])
    _gs4_off[_what] = [round(d - e, 1) for d, e in zip(_drawn, _edges)]
check("the fourth label: turned ^FT symbols, the fields after them, 0 sizes "
      "and ^FT heights between cells, the preview puts every edge within 2 "
      "dots of the print",
      all(abs(miss) <= 2 for misses in _gs4_off.values() for miss in misses),
      _gs4_off)
_gs4_marks = [e for e in _gs4.elements if e.element_type == 'graphic_symbol']
_gs4_after = [e for e in _gs4.elements if e.element_type == 'text']
check("and the canvas turns an ^FT symbol's frame about its point, as text's",
      [box_of(e) for e in _gs4_marks[:4]]
      == [(80, 102, 48, 48), (250, 150, 48, 48), (472, 200, 48, 48),
          (652, 202, 48, 48)],
      [box_of(e) for e in _gs4_marks[:4]])
check("and strings each bare ^FT after one 52 dots on along its run",
      [geometry.typeset_point(e) for e in _gs4_after]
      == [(132, 150), (250, 202), (468, 200), (700, 198)],
      [geometry.typeset_point(e) for e in _gs4_after])
check("and reads ^GS's 0 as a size left out, as ^A's",
      [(e.font_height, e.font_width) for e in _gs4_marks[4:10:2]]
      == [(48, 48), (48, 48), (72, 72)],
      [(e.font_height, e.font_width) for e in _gs4_marks[4:10:2]])
_gs4_zpl = _gs4.to_zpl()
check("and the fourth label saves its four bare ^FTs bare",
      _gs4_zpl.count("^FT\n") == 4
      and _chained(_gs4_zpl).to_zpl() == _gs4_zpl,
      _gs4_zpl.replace('\n', ' '))
# Not printed, and not printable on the 203 dpi printer these labels go to:
# at 300 dpi GS is taken to keep its 24 dot cell, as fonts A-D, F and G do.
_gs_300 = ZPLRenderer(400, 300, dpi=300).render(
    "^XA^FO50,50^GSN,48,48^FDD^FS^XZ").convert('L').point(
    lambda v: 255 if v < 128 else 0).getbbox()
check("not printed: at 300 dpi a ^GSN,48,48 UL is still a 48 dot cell",
      _gs_300 == (50, 50, 98, 98), _gs_300)

# One coordinate left out follows on that axis alone
_axes = _chained("^XA^FT10,200^A0N,30,30^FDAB^FS^FT,300^A0N,30,30^FDCD^FS"
                 "^FT500^A0N,30,30^FDEF^FS^FT,,1^A0N,30,30^FDGH^FS^XZ")
_ab, _cd, _ef, _gh = _axes.elements
check("^FT,300 follows across, on the baseline it gave",
      geometry.typeset_point(_cd) == (_ab.x + _ab.width, 300),
      geometry.typeset_point(_cd))
check("^FT500 stands where it said, on the baseline before it",
      geometry.typeset_point(_ef) == (500, 300), geometry.typeset_point(_ef))
check("^FT,,1 follows, right justified from the end of the one before",
      _gh.justify == 1 and _gh.x + _gh.width == _ef.x + _ef.width
      and geometry.typeset_point(_gh) == (_ef.x + _ef.width, 300),
      (_gh.justify, geometry.typeset_point(_gh)))
_axes_zpl = _axes.to_zpl()
check("each written back leaving out what it left out",
      all(f"{given}\n" in _axes_zpl
          for given in ("^FT10,200", "^FT,300", "^FT500", "^FT,,1")),
      _axes_zpl.replace('\n', ' '))

_bare = _chained("^XA^FT20,100^A0N,30,30^FDAB^FS^FT^A0N,30,30^FDCD^FS"
                 "^A0N,30,30^FDEF^FS^XZ")
check("a field with no origin after one that follows follows too",
      all(at == end for at, end in _ends(_bare.elements))
      and _bare.to_zpl().count("^FT\n") == 2, _ends(_bare.elements))

_home = _chained("^XA^LH30,40^FT^A0N,30,30^FDAB^FS^XZ")
_home_zpl = _home.to_zpl()
check("a first field that follows nothing is on the label home",
      geometry.typeset_point(_home.elements[0]) == (30, 40)
      and "^LH30,40\n" in _home_zpl and "^FT\n" in _home_zpl,
      (geometry.typeset_point(_home.elements[0]), _home_zpl.replace('\n', ' ')))
check("and a save does not pull the home up over its box",
      _chained(_home_zpl).to_zpl() == _home_zpl)

# A file whose Summer is hidden: the printer is never sent it, so Clearance
# follows the ™ in front of it
_blob = base64.b64encode(b"^FT\n^A0N,30,20\n^FDSummer ^FS\n").decode()
_hidden = _chained(CHAIN.replace("^FT^A0N,30,20^FDSummer ^FS\n",
                                 f"^FXDESIGNER_NOPRINT:{_blob}\n"))
check("a hidden field is not followed: the printer is never sent it",
      not _hidden.elements[2].print_enabled
      and geometry.typeset_point(_hidden.elements[3])
      == (_hidden.elements[1].x + _pen(_hidden.elements[1]), 200),
      geometry.typeset_point(_hidden.elements[3]))

# Editing and moving
_edit = _chained()
_edit.elements[0].text = "ACME CORPORATION "
_edit.sync_text_width(_edit.elements[0])
_edit.follow_chains()
check("lengthening a field moves the ones strung after it",
      all(at == end for at, end in _ends(_edit.elements))
      and geometry.typeset_point(_edit.elements[1])[0]
      > geometry.typeset_point(_links[1])[0],
      _ends(_edit.elements))
check("which are still written following it",
      _edit.to_zpl().count("^FT\n") == 4)

_moved = _chained()
_summer = _moved.elements[2]
_was = geometry.typeset_point(_summer)
geometry.move_element(_moved, _summer, 5, 0)
_moved.follow_chains()
check("a follower moved on its own stays where it was put",
      geometry.typeset_point(_summer) == (_was[0] + 5, 200),
      geometry.typeset_point(_summer))
check("written there across, its baseline still following",
      f"^FT{_was[0] + 5}\n" in _moved.to_zpl(),
      _moved.to_zpl().replace('\n', ' '))
check("and the fields after it follow it to its new place",
      all(at == end for at, end in _ends(_moved.elements[2:])),
      _ends(_moved.elements[2:]))
_moved.elements[0].text = "ACME CORPORATION "
_moved.sync_text_width(_moved.elements[0])
_moved.follow_chains()
check("it no longer moves with the field before it, across",
      geometry.typeset_point(_summer)[0] == _was[0] + 5,
      geometry.typeset_point(_summer))
geometry.move_element(_moved, _summer, 0, 5)
_moved.follow_chains()
check("moved down as well, it is written with both coordinates",
      f"^FT{_was[0] + 5},205\n" in _moved.to_zpl(),
      _moved.to_zpl().replace('\n', ' '))

# A follower follows one field, not whichever comes before it. Once that one
# stops coming right before it, following the new one would move it - and
# after a first field brought to front, deleted or hidden, the rest went to
# the label home, above the top edge.
def _stays_put(doc, before):
    """Whether every field is still where it was, and the one that lost its
    leader is written there."""
    return ([geometry.typeset_point(e) for e in doc.elements if e in before]
            == [before[e] for e in doc.elements if e in before])


for _edit_name, _edit in (
        ("brought to front", lambda d: (d.select(d.elements[0]),
                                        d.bring_to_front())),
        ("deleted", lambda d: (d.select(d.elements[0]), d.remove_selected())),
        ("hidden", lambda d: setattr(d.elements[0], 'print_enabled', False))):
    _lost = _chained()
    _points = {e: geometry.typeset_point(e) for e in _lost.elements}
    _tm = _lost.elements[1]
    _before_edit = _lost.snapshot()
    _edit(_lost)
    _lost_zpl = _lost.to_zpl()
    check(f"the field the others follow {_edit_name}, none of them moves",
          _stays_put(_lost, _points),
          [(getattr(e, 'text', '?'), geometry.typeset_point(e))
           for e in _lost.elements])
    check(f"and the one it led is written where it is, the rest still following",
          _tm.follows is None and "^FT64,200\n" in _lost_zpl
          and _lost_zpl.count("^FT\n") == 3, _lost_zpl.replace('\n', ' '))
    _lost.restore(_before_edit)
    _lost.follow_chains()
    check(f"and an undo puts the chain back",
          _lost.to_zpl() == _chain_zpl, _lost.to_zpl().replace('\n', ' '))

_between = _chained()
_wedge = _between.add_frame_element()
_between.elements.insert(1, _between.elements.pop())
_between_zpl = _between.to_zpl()
check("a field put between a follower and its leader leaves it where it is",
      _between.elements[2].follows is None and "^FT64,200\n" in _between_zpl
      and _between_zpl.count("^FT\n") == 3, _between_zpl.replace('\n', ' '))

_dragged = _chained()
geometry.move_selection(_dragged, _dragged.elements, 30, 40)
_dragged_zpl = _dragged.to_zpl()
check("dragged along with the field before it, a follower goes on following",
      _dragged_zpl.count("^FT\n") == 4 and "^FT40,240\n" in _dragged_zpl,
      _dragged_zpl.replace('\n', ' '))

_scaled_chain = _chained()
_scaled_chain.rescale(1.5)
_scaled_zpl = _scaled_chain.to_zpl()
check("a rescale keeps the chain, on the scaled baseline",
      _scaled_zpl.count("^FT\n") == 4 and "^FT15,300\n" in _scaled_zpl
      and all(at == end for at, end in _ends(_scaled_chain.elements)),
      (_scaled_zpl.replace('\n', ' '), _ends(_scaled_chain.elements)))

_pinned_then_scaled = _chained()
_was = geometry.typeset_point(_pinned_then_scaled.elements[2])
geometry.move_element(_pinned_then_scaled, _pinned_then_scaled.elements[2],
                      5, 0)
_pinned_then_scaled.rescale(1.5)
check("one moved off the chain before a rescale is scaled where it was put",
      f"^FT{round((_was[0] + 5) * 1.5)}\n" in _pinned_then_scaled.to_zpl(),
      _pinned_then_scaled.to_zpl().replace('\n', ' '))

_undone = _chained()
_before_edit = _undone.snapshot()
_undone.elements[0].text = "ACME CORPORATION "
_undone.sync_text_width(_undone.elements[0])
_undone.follow_chains()
_undone.restore(_before_edit)
_undone.follow_chains()
check("an undo puts the followers back after the field as it was",
      [(e.x, e.y) for e in _undone.elements] == [(e.x, e.y) for e in _links]
      and _undone.to_zpl() == _chain_zpl)

_copied = _chained()
_copied.select(_copied.elements[3])
_shown = geometry.typeset_point(_copied.elements[3])
check("a copy of a follower is written where it is shown",
      f"^FT{_shown[0]},{_shown[1]}\n" in _copied.copy_zpl(),
      _copied.copy_zpl().replace('\n', ' '))
_copied.duplicate_selected()
check("and so is a duplicate, while the original goes on following",
      _copied.elements[-1].follows is None
      and _copied.to_zpl().count("^FT\n") == 4)
_copied.select(_copied.elements[0])
_copied.duplicate_selected()
_copied.select(_copied.elements[0])
_copied.remove_selected()
_copied.follow_chains()
check("a copy of the field the others follow is not that field to them",
      _copied.elements[0].follows is None
      and _copied.to_zpl().count("^FT\n") == 3,
      _copied.to_zpl().replace('\n', ' '))

# The canvas catches a follower up as it paints, whatever edited the field
# before it
_painted_chain = _chained()
_chain_canvas = qt_canvas.DesignCanvas(_painted_chain)
_chain_canvas.set_view_size(812, 1218)
_chain_canvas.set_zoom(1.0)
_painted_chain.elements[0].text = "ACME CORPORATION "
_painted_chain.sync_text_width(_painted_chain.elements[0])
_chain_target = QImage(812, 1218, QImage.Format_ARGB32)
_chain_canvas.render(_chain_target)
check("the Qt canvas strings the followers along as it paints",
      all(at == end for at, end in _ends(_painted_chain.elements)),
      _ends(_painted_chain.elements))

# --- a symbology this designer cannot draw is not text ----------------------

# ^B3, ^BE and ^BQ are no longer in this list - they draw for real now, checked
# below - and nor is ^GS, the symbol font, which is an element of its own now
# (see "^GS, the graphic symbol"), and nor are ^BD, MaxiCode (see "^BD, UPS
# MaxiCode"), or ^BF, MicroPDF417. ^B4 Code 49 still does not, and ^BW is no
# ZPL command at all - a ^B the printer has never heard of must not become
# text either.
for symbology, source in (("^B4", "^B4N,6,200^FDdata^FS"),
                          ("^BW", "^BWN,8,3^FDdata^FS")):
    page = f"^XA^PW812^LL1218^FO50,50{source}^XZ"
    read = zpl_parser.parse_zpl(page)[0]
    check(f"{symbology} is dropped, not turned into text",
          not read.elements, [e.element_type for e in read.elements])
    check(f"and {symbology} is named as a command a save would drop",
          symbology in workflow.unsupported_commands(page),
          workflow.unsupported_commands(page))

check("the preview draws nothing for one still unsupported either",
      _preview_ink("^XA^PW400^LL300^FO50,50^B4N,6,200^FDdata^FS^XZ",
                   400, 300) is None,
      _preview_ink("^XA^PW400^LL300^FO50,50^B4N,6,200^FDdata^FS^XZ", 400, 300))

check("the preview draws a MicroPDF417 for real, where the canvas does",
      _preview_ink("^XA^PW400^LL400^FO50,50^BY2^BFN,4,33^FDHELLO^FS^XZ",
                   400, 400) == (50, 50, 99 * 2, 4 * 4),
      _preview_ink("^XA^PW400^LL400^FO50,50^BY2^BFN,4,33^FDHELLO^FS^XZ",
                   400, 400))

check("the preview draws a QR code for real, at the magnification the command gives",
      _preview_ink("^XA^PW400^LL400^FO50,50^BQ,2,4^FDMM,AAC-42^FS^XZ", 400, 400)
      == (50, 50, 21 * 4, 21 * 4),
      _preview_ink("^XA^PW400^LL400^FO50,50^BQ,2,4^FDMM,AAC-42^FS^XZ", 400, 400))

check("but the preview draws ^B3 for real, the same as the canvas would",
      _preview_ink("^XA^PW400^LL300^FO50,50^B3N,N,60,Y,N^FD123ABC^FS^XZ",
                   400, 300) is not None,
      _preview_ink("^XA^PW400^LL300^FO50,50^B3N,N,60,Y,N^FD123ABC^FS^XZ", 400, 300))

check("a ^BC is still a barcode",
      [e.element_type for e in zpl_parser.parse_zpl(
          "^XA^PW812^LL1218^FO50,50^BY3^BCN,100^FD12345^FS^XZ")[0].elements]
      == ['barcode'])

# --- ^GF in the encodings real labels carry ---------------------------------

from zplcore import graphics as zpl_graphics

def _run_length(raw, bytes_per_row):
    """ZPL ASCII run-length, so the decoder is fed data it has to match."""
    out = []
    for start in range(0, len(raw), bytes_per_row):
        digits = raw[start:start + bytes_per_row].hex().upper()
        row, i = [], 0
        while i < len(digits):
            char, run = digits[i], 1
            while i + run < len(digits) and digits[i + run] == char:
                run += 1
            left = run
            while left >= 20:
                take = min(400, left - left % 20)
                row.append('ghijklmnopqrstuvwxyz'[take // 20 - 1])
                left -= take
            if left:
                row.append('GHIJKLMNOPQRSTUVWXY'[left - 1])
            row.append(char)
            i += run
        out.append(''.join(row))
    return ''.join(out)

# Ground truth: the logo this designer itself wrote into a fixture, whose
# correct bytes are known without reading a word of the compression format.
_logo = re.search(r'\^GFA,(\d+),(\d+),(\d+),([0-9A-Fa-f]+)',
                  open(FIXTURES / 'sample_203dpi.zpl').read())
GF_TOTAL, GF_BPR = int(_logo.group(1)), int(_logo.group(3))
GF_TRUTH = bytes.fromhex(_logo.group(4))

GF_FORMS = {
    'plain hex': _logo.group(4),
    ':Z64:': ':Z64:' + base64.b64encode(zlib.compress(GF_TRUTH)).decode() + ':ABCD',
    ':B64:': ':B64:' + base64.b64encode(GF_TRUTH).decode() + ':ABCD',
    'run-length': _run_length(GF_TRUTH, GF_BPR),
}
for name, data in GF_FORMS.items():
    decoded = zpl_graphics.decode(f"A,{GF_TOTAL},{GF_TOTAL},{GF_BPR},{data}")
    check(f"^GF as {name} decodes to the very same bitmap",
          decoded is not None and decoded[0] == GF_TRUTH,
          f"{len(decoded[0]) if decoded else None} vs {len(GF_TRUTH)} bytes")
    page = (f"^XA^PW812^LL1218^FO50,50"
            f"^GFA,{GF_TOTAL},{GF_TOTAL},{GF_BPR},{data}^FS^XZ")
    read = zpl_parser.parse_zpl(page)[0]
    check(f"and opens as one image, {name}",
          [(e.element_type, e.width, e.height) for e in read.elements]
          == [('image', GF_BPR * 8, len(GF_TRUTH) // GF_BPR)],
          [(e.element_type, e.width, e.height) for e in read.elements])

check(":Z64: is worth using: it is far shorter than the hex",
      len(GF_FORMS[':Z64:']) < len(GF_FORMS['plain hex']) / 4,
      f"{len(GF_FORMS[':Z64:'])} vs {len(GF_FORMS['plain hex'])} characters")

# the run-length table, case by case, so it can be read against the manual
for source, width, expected in (("MF", 4, "FFFFFFF0"),
                                ("hKF", 32, "F" * 45 + "0" * 19),
                                ("FF,", 4, "FF000000"),
                                ("00!", 4, "00FFFFFF"),
                                ("FF00FF00:", 4, "FF00FF00FF00FF00")):
    got = zpl_graphics.decode_data(source, width)
    check(f"run-length {source!r} expands to {expected}",
          got is not None and got.hex().upper() == expected,
          got.hex().upper() if got else None)
check("a repeat with no row above it is not decodable",
      zpl_graphics.decode_data(":FF", 4) is None)
check("a character that is not hex is not decodable",
      zpl_graphics.decode_data("FF@0", 4) is None)

# what cannot be read is named, rather than leaving the label short an image
for name, params in (('^GFB', "B,40,40,4,binary"),
                     ('^GFC', "C,40,40,4,binary"),
                     ('^GFA', "A,40,40,4,:Z64:####:AB"),
                     ('^GFA', "A,40,40,4,:B64:not base64 at all:AB")):
    page = f"^XA^PW812^LL1218^FO50,50^GF{params}^FS^XZ"
    check(f"{name} is named when it cannot be read: {params[:14]}",
          workflow.unsupported_commands(page) == [name],
          workflow.unsupported_commands(page))
check("a ^GF that can be read is not named",
      workflow.unsupported_commands(
          f"^XA^FO1,1^GFA,{GF_TOTAL},{GF_TOTAL},{GF_BPR},{GF_FORMS[':Z64:']}^FS^XZ") == [])

# the preview draws the same ink for every encoding, not just the one we write
_gf_ink = {}
for name, data in GF_FORMS.items():
    _gf_ink[name] = _preview_ink(
        f"^XA^PW400^LL300^FO10,10^GFA,{GF_TOTAL},{GF_TOTAL},{GF_BPR},{data}^FS^XZ",
        400, 300)
check("the preview draws every encoding the same",
      len(set(map(str, _gf_ink.values()))) == 1 and _gf_ink['plain hex'] is not None,
      _gf_ink)

# --- ^BY is a default, and it applies across fields -------------------------
# "It stays in effect until another ^BY command is encountered" - ZPL manual
# p142, where ^BYw,r,h is also defined. The parser read it only inside an open
# field and only its first parameter, so a ^BY at the top of a format - the
# manual's own placement, and what most generators emit - was dropped outright.
# Nothing was said either, because ^BY is listed in workflow.MODELLED.

def _saved(zpl):
    """The commands a load-then-save leaves, minus the frame every file has."""
    out = zpl_parser.parse_zpl(zpl)[0].to_zpl().split('\n')
    return ' '.join(l for l in out if l.strip() and not l.startswith('^FX')
                    and l not in ('^XA', '^XZ', '^PW812', '^LL1218'))

check("a ^BY inside the field still works",
      '^BY3' in _saved("^XA^FO50,50^BY3^BCN,100^FD123^FS^XZ"),
      _saved("^XA^FO50,50^BY3^BCN,100^FD123^FS^XZ"))

# The case that was silently wrong: the barcode printed at half the width.
leading = _saved("^XA^BY4^FO50,50^BCN,100^FD123456^FS^XZ")
check("a ^BY before the first ^FO is not dropped", '^BY4' in leading, leading)
before = zpl_parser.parse_zpl("^XA^BY4^FO50,50^BCN,100^FD123456^FS^XZ")[0].elements[0]
inside = zpl_parser.parse_zpl("^XA^FO50,50^BY4^BCN,100^FD123456^FS^XZ")[0].elements[0]
check("and the two spellings print the same width",
      before.printed_width() == inside.printed_width() == 404,
      (before.printed_width(), inside.printed_width()))

two = _saved("^XA^BY4^FO50,50^BCN,100^FD1^FS^FO50,300^BCN,100^FD2^FS^XZ")
check("one ^BY reaches every barcode after it", two.count('^BY4') == 2, two)
carried = _saved("^XA^FO50,50^BY4^BCN,100^FD1^FS^FO50,300^BCN,100^FD2^FS^XZ")
check("including from inside an earlier field", carried.count('^BY4') == 2, carried)
overridden = _saved("^XA^BY4^FO50,50^BCN,100^FD1^FS^BY2^FO50,300^BCN,100^FD2^FS^XZ")
check("and a later ^BY changes only what follows it",
      overridden.count('^BY4') == 1 and overridden.count('^BY2') == 1, overridden)

# ^BY's third parameter is the height a ^BC that gives none inherits
check("^BY's h supplies a ^BC with no height of its own",
      '^BCN,150' in _saved("^XA^FO50,50^BY3,3.0,150^BCN^FD12345^FS^XZ"),
      _saved("^XA^FO50,50^BY3,3.0,150^BCN^FD12345^FS^XZ"))
check("a ^BC's own height still wins over it",
      '^BCN,80' in _saved("^XA^BY3,3.0,150^FO50,50^BCN,80^FD12345^FS^XZ"),
      _saved("^XA^BY3,3.0,150^FO50,50^BCN,80^FD12345^FS^XZ"))
check("and with no ^BY anywhere the designer's own height is used",
      f"^BCN,{zpl_parser.DESIGNER_BAR_HEIGHT}" in _saved("^XA^FO50,50^BCN^FD1^FS^XZ"),
      _saved("^XA^FO50,50^BCN^FD1^FS^XZ"))

# Each parameter is optional and keeps its previous value, the ^CF rule
kept = _saved("^XA^BY2,2.5,150^FO50,50^BY3^BCN^FD1^FS^XZ")
check("a bare ^BY3 keeps the ratio and height already set",
      '^BY3,2.5' in kept and '^BCN,150' in kept, kept)

# The ratio has no effect on a fixed-ratio symbology, so it is carried, not
# modelled - but carried means a save does not quietly drop it.
check("a ratio a file gave comes back",
      '^BY2,2.5' in _saved("^XA^FO50,50^BY2,2.5^BCN,80^FD1^FS^XZ"),
      _saved("^XA^FO50,50^BY2,2.5^BCN,80^FD1^FS^XZ"))
check("and the default ratio is trimmed, so existing files do not move",
      _saved("^XA^FO50,50^BY2,3.0^BCN,80^FD1^FS^XZ").count('^BY2 ') == 1,
      _saved("^XA^FO50,50^BY2,3.0^BCN,80^FD1^FS^XZ"))

# The fixture: ^BY at the top, two barcodes, neither giving a height.
shared = (FIXTURES / 'shared_barcode.zpl').read_text()
bars = zpl_parser.parse_zpl(shared)[0].elements
check("the fixture's two barcodes both inherit the leading ^BY",
      len(bars) == 2 and all(b.module_width == 3 and b.bar_height == 120
                             and abs(b.ratio - 2.5) < 1e-9 for b in bars),
      [(b.module_width, b.bar_height, b.ratio) for b in bars])
check("which is 402 dots wide, not the 268 a dropped ^BY drew",
      bars[0].printed_width() == 402, bars[0].printed_width())
check("and nothing in it is reported as unsupported",
      workflow.unsupported_commands(shared) == [],
      workflow.unsupported_commands(shared))

# The preview has to agree, or the canvas and the printer part company
ink = _preview_ink(shared.replace('^PW406', '^PW500').replace('^LL406', '^LL500'),
                   500, 500)
check("the preview draws the inherited width too",
      ink is not None and ink[2] >= 400, ink)



# --- stored formats: ^FN as a real variable field ---------------------------
# A ^DF template is a normal design whose variable fields carry ^FN instead of
# ^FD. Those fields used to vanish, and a ^FN barcode field was handed the
# string "123456789" by a fallback meant for newly created barcodes - so the
# designer invented label content and wrote it to disk.

from zplcore import fields as zpl_fields

# The manual's canonical example, p51, kept verbatim: it is ground truth for
# what a stored format is, published rather than inferred.
_stored = (FIXTURES / 'stored_format.zpl').read_text()
_doc = zpl_parser.parse_zpl(_stored)[0]
check("a ^DF names the format the file describes",
      _doc.stored_format == 'R:SAMPLE.GRF', _doc.stored_format)
check("the manual's template opens as all fourteen of its fields",
      len(_doc.elements) == 14, len(_doc.elements))
check("and its five ^FN fields - one of them a ^B3 barcode - are numbered, not dropped",
      [e.field_number for e in _doc.elements
       if getattr(e, 'field_number', None) is not None] == [1, 2, 3, 4, 5],
      [getattr(e, 'field_number', None) for e in _doc.elements])
check("^FN4 is the barcode, Code 39 read straight off the manual's own ^B3",
      [e.element_type for e in _doc.elements if getattr(e, 'field_number', None) == 4]
      == ['barcode'],
      [(e.element_type, e.symbology) for e in _doc.elements
       if getattr(e, 'field_number', None) == 4])
_saved = _doc.to_zpl()
check("every ^FN is written back",
      [l for l in _saved.split('\n') if '^FN' in l]
      == ['^FN1^FS', '^FN2^FS', '^FN3^FS', '^FN4^FS', '^FN5^FS'],
      [l for l in _saved.split('\n') if '^FN' in l])
check("and the ^DF comes straight after the ^XA, as ZPL requires",
      _saved.split('\n')[:2] == ['^XA', '^DFR:SAMPLE.GRF^FS'],
      _saved.split('\n')[:2])

# The defect that mattered most: a value that appears nowhere in the source.
check("no ^FN field is handed an invented value",
      '123456789' not in _saved, 
      [l for l in _saved.split('\n') if '123456789' in l])
_bc = zpl_parser.parse_zpl(
    "^XA^PW812^LL1218^FO50,50^BY3^BCN,100^FN4^FS^XZ")[0].elements[0]
check("a ^FN barcode field is a barcode with no value of its own",
      _bc.element_type == 'barcode' and _bc.barcode_value == ''
      and _bc.field_number == 4,
      (_bc.element_type, _bc.barcode_value, _bc.field_number))

# A recall call is data, not geometry. Opening one used to empty the file.
_recall = (FIXTURES / 'recall_format.zpl').read_text()
_rdoc = zpl_parser.parse_zpl(_recall)[0]
check("an ^XF is recorded", _rdoc.recalls == ['R:SAMPLE.GRF'], _rdoc.recalls)
check("nothing is drawn for it, because the geometry is on the printer",
      not _rdoc.elements, [e.element_type for e in _rdoc.elements])
check("all five of its values are read",
      _rdoc.fields.pairs() == [(1, 'Acme Printing'), (2, '14042'),
                               (3, 'Screw'), (4, '12345678'),
                               (5, 'Macks Fabricating')],
      _rdoc.fields.pairs())
_rsaved = _rdoc.to_zpl()
check("and the whole call is written back rather than emptied",
      '^XFR:SAMPLE.GRF' in _rsaved
      and all(f'^FN{n}^FD' in _rsaved for n in (1, 2, 3, 4, 5)),
      _rsaved)

# ZPL's sharing rule: "the data in that field prints for any other field
# containing the same ^FN value."
_named = (FIXTURES / 'named_fields.zpl').read_text()
_ndoc = zpl_parser.parse_zpl(_named)[0]
_shown = [_ndoc.display_text(e) for e in _ndoc.elements]
check("a field with no value shows the name it gave itself",
      _shown[0] == '\u00abCustomer\u00bb', _shown)
check("one ^FN value reaches every field sharing the number",
      _shown[1] == 'A-1000' and _shown[2] == 'A-1000', _shown)
check("an unnamed, unvalued field still shows its number",
      zpl_fields.FieldTable().display(4) == '\u00abFN4\u00bb',
      zpl_fields.FieldTable().display(4))
check("and none of the four is reported as unsupported",
      workflow.unsupported_commands(_named) == [],
      workflow.unsupported_commands(_named))
check("the prompt round-trips in the quotes that make it a prompt",
      '^FN1"Customer"^FS' in _ndoc.to_zpl(),
      [l for l in _ndoc.to_zpl().split('\n') if '^FN1' in l])

# ^FV is ^FD for a field the printer clears after printing. Its text used to
# disappear entirely, element and all.
_fv = zpl_parser.parse_zpl(
    "^XA^PW812^LL1218^FO50,50^A0N,30,30^FVvariable^FS^XZ")[0].elements
check("^FV no longer loses the field it carries",
      len(_fv) == 1 and _fv[0].text == 'variable',
      [(e.element_type, getattr(e, 'text', None)) for e in _fv])

# And it is written back as ^FV. Saving it as ^FD turned a field the printer
# clears after each label into one that persists, which changes what the
# second label of a run prints - silently, since ^FV is modelled.
def _fv_saved(zpl):
    return zpl_parser.parse_zpl(zpl)[0].to_zpl()

_fvz = _fv_saved("^XA^PW812^LL1218^FO50,50^A0N,30,30^FVvariable^FS^XZ")
check("^FV is written back as ^FV, not ^FD",
      '^FVvariable^FS' in _fvz and '^FD' not in _fvz, _fvz)
_fdz = _fv_saved("^XA^PW812^LL1218^FO50,50^A0N,30,30^FDfixed^FS^XZ")
check("and ^FD is still written as ^FD",
      '^FDfixed^FS' in _fdz and '^FV' not in _fdz, _fdz)
_fvn = _fv_saved('^XA^PW812^LL1218^FO50,50^A0N,30,30^FN1"Lot"^FVabc^FS^XZ')
check("a numbered ^FV field keeps ^FV beside its ^FN",
      '^FN1"Lot"^FVabc^FS' in _fvn, _fvn)
_fvh = _fv_saved("^XA^PW812^LL1218^FO50,50^A0N,30,30^FH^FVa_41b^FS^XZ")
check("^FH still precedes a ^FV it escapes",
      '^FH_^FVa_41b^FS' in _fvh, _fvh)
_fvb = _fv_saved("^XA^PW812^LL1218^FO50,50^BY2^BCN,80^FV12345^FS^XZ")
check("a barcode's ^FV is kept too", '^FV12345^FS' in _fvb, _fvb)
_fvr = _fv_saved("^XA^XFR:SAMPLE.GRF^FN1^FVabc^FS^FN2^FDdef^FS^XZ")
check("a recall call's ^FN#^FV pair keeps ^FV, and its ^FD pair ^FD",
      '^FN1^FVabc^FS' in _fvr and '^FN2^FDdef^FS' in _fvr, _fvr)
_fv_png = ZPLRenderer(400, 200).render(
    "^XA^PW400^LL200^FO20,20^A0N,40,40^FVSHOWN^FS^XZ").convert('L')
_fd_png = ZPLRenderer(400, 200).render(
    "^XA^PW400^LL200^FO20,20^A0N,40,40^FDSHOWN^FS^XZ").convert('L')
check("the preview draws a ^FV field exactly as it draws ^FD",
      _fv_png.tobytes() == _fd_png.tobytes()
      and _fv_png.getextrema()[0] < 128, _fv_png.getextrema())

# The box has to match what is drawn, or a visible placeholder cannot be
# clicked on the element it belongs to.
_ph = zpl_parser.parse_zpl(
    "^XA^PW406^LL203^FO20,20^A0N,30,30^FN2\"Part number\"^FS^XZ")[0]
check("a placeholder's box is measured from what the canvas shows",
      _ph.elements[0].width > 100, _ph.elements[0].width)

# The one deliberate divergence: the canvas shows the placeholder because it
# answers "what am I editing"; the preview draws nothing because it answers
# "what will print", and an unfilled ^FN prints nothing.
_unfilled = "^XA^PW300^LL200^FO20,20^A0N,30,30^FN2\"Part number\"^FS^XZ"
check("the preview draws no ink for an unfilled ^FN",
      _preview_ink(_unfilled, 300, 200) is None,
      _preview_ink(_unfilled, 300, 200))
_filled = ("^XA^PW300^LL200^FO20,20^A0N,30,30^FN2\"Part number\"^FS"
           "^FN2^FDFilled^FS^XZ")
check("and draws the value once something supplies one",
      _preview_ink(_filled, 300, 200) is not None,
      _preview_ink(_filled, 300, 200))

# Undo holds whole documents, so a shared field table would rewrite every entry
# on the stack - the trap a text element's block already avoids.
_udoc = zpl_parser.parse_zpl(_named)[0]
_usnap = _udoc.snapshot()
_udoc.fields.set_value(7, 'CHANGED')
_udoc.restore(_usnap)
check("an undo snapshot does not share the field table",
      _udoc.fields.value(7) == 'A-1000', _udoc.fields.value(7))


# --- stored graphics: ^IM, ^XG, ^IL, ^IS -------------------------------------
# The graphic counterpart of the stored-format family above. ^DG's role -
# putting a named image into printer storage - is played here by ^IS, which
# saves everything a format has drawn so far; ^XG, ^IM and ^IL each recall one
# back. None of the four existed at all before this: a label using any of them
# had its image silently absent on screen and silently dropped on save.
#
# Unlike ^DF/^XF, resolution is real within one session: zplcore.graphic_store
# is a plain module-level dict, so parsing a file with ^IS actually captures a
# renderable image, and a later ^XG/^IM/^IL - in the same file or a different
# one parsed afterwards - can recall the real pixels rather than only a
# placeholder. Each block below clears the store first, so one case's ^IS
# cannot leak into another's expectations.

from zplcore import graphic_store

_sg_save = (FIXTURES / 'stored_graphic_save.zpl').read_text()
_sg_recall = (FIXTURES / 'stored_graphic_recall.zpl').read_text()
_sg_load = (FIXTURES / 'stored_graphic_load.zpl').read_text()

# Cold session: nothing has been parsed yet, so a recall or a load has
# nothing to resolve - and must still round-trip exactly, not vanish.
graphic_store.clear()
_cold_recall = zpl_parser.parse_zpl(_sg_recall)[0]
check("a cold ^XG/^IM resolve to nothing yet",
      all(e.resolve() is None for e in _cold_recall.elements),
      [e.resolve() for e in _cold_recall.elements])
_cold_saved = _cold_recall.to_zpl()
check("and the file still round-trips exactly, unresolved",
      '^XGR:LOGO.GRF,2,2' in _cold_saved and '^IMR:LOGO.GRF' in _cold_saved
      and '^GF' not in _cold_saved,
      _cold_saved)

_cold_load = zpl_parser.parse_zpl(_sg_load)[0]
check("a cold ^IL is recorded but resolves to nothing",
      _cold_load.image_load == 'R:LOGO.GRF'
      and graphic_store.recall(_cold_load.image_load) is None,
      _cold_load.image_load)
check("and it round-trips right after ^XA, ahead of the fields it underlies",
      _cold_load.to_zpl().split('\n')[:2] == ['^XA', '^ILR:LOGO.GRF'],
      _cold_load.to_zpl().split('\n')[:2])

check("none of the four commands are reported as unsupported",
      workflow.unsupported_commands(_sg_save) == []
      and workflow.unsupported_commands(_sg_recall) == []
      and workflow.unsupported_commands(_sg_load) == [],
      (workflow.unsupported_commands(_sg_save),
       workflow.unsupported_commands(_sg_recall),
       workflow.unsupported_commands(_sg_load)))

# Warm session: parsing the save fixture first captures a real image under
# R:LOGO.GRF, so parsing the recall/load fixtures afterwards resolves for real.
graphic_store.clear()
_sg_save_doc = zpl_parser.parse_zpl(_sg_save)[0]
check("^IS is recorded for round-tripping",
      _sg_save_doc.image_saves == ['R:LOGO.GRF,Y'], _sg_save_doc.image_saves)
check("and it captures a real image into this session's store",
      graphic_store.recall('R:LOGO.GRF') is not None,
      graphic_store.recall('R:LOGO.GRF'))
check("^IS round-trips after the elements it captured",
      _sg_save_doc.to_zpl().endswith('^ISR:LOGO.GRF,Y^FS\n^XZ'),
      _sg_save_doc.to_zpl())

_warm_recall = zpl_parser.parse_zpl(_sg_recall)[0]
_xg, _im = _warm_recall.elements
check("a warm ^XG now resolves to the real image",
      _xg.resolve() is not None, _xg.resolve())
check("magnified by the factor it named",
      (_xg.width, _xg.height) == (_xg.resolve().width * 2, _xg.resolve().height * 2),
      (_xg.width, _xg.height, _xg.resolve().size))
check("a warm ^IM resolves too, unmagnified",
      _im.resolve() is not None and (_im.width, _im.height) == _im.resolve().size,
      (_im.width, _im.height, _im.resolve() and _im.resolve().size))
check("saving a resolved ^XG/^IM still writes the reference, never the pixels",
      '^GF' not in _warm_recall.to_zpl(), _warm_recall.to_zpl())

_warm_load = zpl_parser.parse_zpl(_sg_load)[0]
check("a warm ^IL resolves to the real image too",
      graphic_store.recall(_warm_load.image_load) is not None,
      graphic_store.recall(_warm_load.image_load))

# The preview renderer never repeats the capture itself - see
# zplcore/renderer.py - it only recalls what the parser already stored, so
# this exercises that same warm-then-forgotten store rather than parsing again.
check("the preview draws real ink for a resolved ^XG",
      _preview_ink(_sg_recall, 812, 1218) is not None,
      _preview_ink(_sg_recall, 812, 1218))
graphic_store.clear()
check("and no ink at all once the session forgets the source",
      _preview_ink(_sg_recall, 812, 1218) is None,
      _preview_ink(_sg_recall, 812, 1218))

# graphic_store.key() normalisation: device is not distinguished, and a bare
# or partial spec still resolves - see FUNCTIONAL_SPEC.md section 18.
check("device prefix is not distinguished",
      graphic_store.key('R:SAMPLE.GRF') == graphic_store.key('E:SAMPLE.GRF'),
      (graphic_store.key('R:SAMPLE.GRF'), graphic_store.key('E:SAMPLE.GRF')))
check("case does not matter",
      graphic_store.key('r:sample.grf') == graphic_store.key('R:SAMPLE.GRF'),
      graphic_store.key('r:sample.grf'))
check("a bare name defaults to a .GRF extension",
      graphic_store.key('R:SAMPLE') == 'SAMPLE.GRF', graphic_store.key('R:SAMPLE'))
check("no name at all is not an error",
      graphic_store.key('') == 'UNKNOWN.GRF', graphic_store.key(''))
graphic_store.clear()

# graphic_store.items(): what a "Printer Graphics" manager lists - every
# stored (name.ext, image) pair, sorted so both frontends' lists agree with
# each other and with repeated calls.
check("items() is empty before anything is stored",
      graphic_store.items() == [], graphic_store.items())
graphic_store.store('R:B.GRF', Image.new('RGB', (3, 3)))
graphic_store.store('E:A.GRF', Image.new('RGB', (5, 5)))
check("items() lists every stored image, sorted by key",
      [k for k, _img in graphic_store.items()] == ['A.GRF', 'B.GRF'],
      graphic_store.items())
graphic_store.clear()
check("and clear() empties it",
      graphic_store.items() == [], graphic_store.items())

# graphic_store.delete(): the UI-level counterpart of ^ID, which this app
# does not parse from ZPL - see FUNCTIONAL_SPEC.md section 18.
graphic_store.store('R:LOGO.GRF', Image.new('RGB', (7, 7)))
check("delete() removes a stored image and says it was there",
      graphic_store.delete('R:LOGO.GRF') is True, graphic_store.recall('R:LOGO.GRF'))
check("recall() finds nothing afterwards",
      graphic_store.recall('R:LOGO.GRF') is None, graphic_store.recall('R:LOGO.GRF'))
check("deleting again says there was nothing to delete",
      graphic_store.delete('R:LOGO.GRF') is False, graphic_store.delete('R:LOGO.GRF'))
graphic_store.store('E:SAMPLE.GRF', Image.new('RGB', (2, 2)))
check("delete() normalises its spec the same way store()/recall() do",
      graphic_store.delete('e:sample.grf') is True, graphic_store.items())
graphic_store.clear()

# graphic_store.split_device_spec(): a `d:o.x` spec taken apart for an
# editor's separate fields - moved here from zplcore/model.py so the network
# builders below can use it without a circular import.
check("split_device_spec: no colon defaults to device R",
      graphic_store.split_device_spec('LOGO.GRF') == ('R', 'LOGO', 'GRF'),
      graphic_store.split_device_spec('LOGO.GRF'))
check("split_device_spec: an explicit device is upper-cased, name/ext are not",
      graphic_store.split_device_spec('e:sample.png') == ('E', 'sample', 'png'),
      graphic_store.split_device_spec('e:sample.png'))
check("split_device_spec: missing name/ext default to UNKNOWN/GRF",
      graphic_store.split_device_spec('B:') == ('B', 'UNKNOWN', 'GRF'),
      graphic_store.split_device_spec('B:'))

# zplcore.graphics.encode(): the encode-side counterpart of decode_data(),
# extracted from ImageElement.to_zpl() so the ~DG builder below can reuse the
# same packer rather than a second, untested one - see zplcore/graphics.py.
# The existing ^GFA checks earlier in this file already guard to_zpl()'s
# output stayed byte-identical after that extraction.
mono = Image.new('1', (8, 1))
for x in range(4):
    mono.putpixel((x, 0), 0)      # black
for x in range(4, 8):
    mono.putpixel((x, 0), 255)    # white
check("graphics.encode(): MSB-first, a set bit is black",
      zpl_graphics.encode(mono, 1) == 'F0', zpl_graphics.encode(mono, 1))
padded = Image.new('1', (5, 1), 0)   # all black, narrower than one byte
check("graphics.encode(): a short row pads white up to bytes_per_row",
      zpl_graphics.encode(padded, 1) == 'F8', zpl_graphics.encode(padded, 1))

# graphic_store.build_graphic_upload(): the ~DG payload Store sends to the
# real printer - same bitmap graphics.encode() already produces, so this
# only needs to check the header and that the two agree on the data.
upload_img = Image.new('RGB', (16, 8), (0, 0, 0))
upload_payload = graphic_store.build_graphic_upload('R:LOGO.GRF', upload_img)
check("build_graphic_upload(): ~DG header names device, object and sizes",
      upload_payload.startswith(b'~DGR:LOGO.GRF,16,2,'), upload_payload[:24])
upload_hex = upload_payload.decode('ascii').split(',', 3)[-1]
check("build_graphic_upload()'s hex is exactly graphics.encode()'s own output",
      upload_hex == zpl_graphics.encode(upload_img.convert('1'), 2), upload_hex)
check("and it decodes back via the existing graphics.decode_data()",
      zpl_graphics.decode_data(upload_hex, 2) == b'\xff' * 16,
      zpl_graphics.decode_data(upload_hex, 2))

# graphic_store.parse_hg_reply(): real hardware answers ^HG not with a
# self-contained image file but with the same shape a ~DG upload writes,
# minus the device/extension - name,total,bytes_per_row, a newline, then
# the bitmap itself. Round-trip through graphics.encode() the way the
# printer's own reply would be shaped, and confirm the pixels survive.
rt_src = Image.new('1', (16, 2), 255)
for x in range(8):
    rt_src.putpixel((x, 0), 0)   # top row half black, bottom row all white
rt_bpr = 2
rt_hex = zpl_graphics.encode(rt_src, rt_bpr)
rt_reply = f'~DGPHOTO,{rt_bpr * 2},{rt_bpr},\r\n{rt_hex}'.encode('ascii')
rt_out = graphic_store.parse_hg_reply(rt_reply)
check("parse_hg_reply(): reconstructs the ~DG-echoed bitmap",
      rt_out is not None and rt_out.size == (16, 2), rt_out)
check("parse_hg_reply(): round-tripped pixels match the source",
      rt_out is not None
      and list(rt_out.convert('L').getdata()) == list(rt_src.convert('L').getdata()),
      rt_out and list(rt_out.convert('L').getdata()))
check("parse_hg_reply(): a reply that isn't ~DG-shaped falls through as None",
      graphic_store.parse_hg_reply(b'\x0a\x05\x01\x01\x01\x00garbage') is None,
      graphic_store.parse_hg_reply(b'\x0a\x05\x01\x01\x01\x00garbage'))

# printer_objects.build_object_upload(): the CISDFCRC16 payload Store sends
# for an arbitrary file - unlike fonts/graphics, case is preserved exactly
# (see the module docstring), and validation is deliberately skipped via
# "0000"/"0000" rather than a guessed checksum algorithm.
from zplcore import printer_objects
obj_payload = printer_objects.build_object_upload('privkey', 'nrd', b'hello')
check("build_object_upload(): CISDFCRC16 header, CRC/checksum both 0000",
      obj_payload.startswith(b'! CISDFCRC16\r\n0000\r\nprivkey.nrd\r\n00000005\r\n0000\r\n'),
      obj_payload)
check("build_object_upload(): case preserved, not forced to upper",
      b'privkey.nrd' in obj_payload and b'PRIVKEY.NRD' not in obj_payload,
      obj_payload)
check("build_object_upload(): the data itself follows the header verbatim",
      obj_payload.endswith(b'hello'), obj_payload)

# graphic_store.DEVICE_NAMES / device_name(): what a device letter means in
# words, for the Memory column of the Printer Objects list and the device
# choices in the stored-graphic editors. Names come from the ZPL manual's
# Table 67; the point of the checks below is that nothing here ever invents
# a memory type for a letter it does not know.
_unnamed = sorted({d for d in printer_objects.DEVICES
                   if d not in graphic_store.DEVICE_NAMES}
                  | {d for d in graphic_store.DEVICES
                     if d not in graphic_store.DEVICE_NAMES})
check("every device Objects and Graphics list has a human-readable name",
      _unnamed == [], _unnamed)
check("device_name(): R: is DRAM, E: is Flash, per the manual's Table 67",
      (graphic_store.device_name('R:LABEL.ZPL'),
       graphic_store.device_name('E:SAMPLE.GRF')) == ("DRAM", "Flash"),
      (graphic_store.device_name('R:LABEL.ZPL'),
       graphic_store.device_name('E:SAMPLE.GRF')))
check("device_name(): Z: is named read-only Zebra content, not left as 'Z:'",
      graphic_store.device_name('Z:INDEX.WML') == "Zebra read-only",
      graphic_store.device_name('Z:INDEX.WML'))
# split_device_spec() defaults a prefix-less spec to 'R', which is right for
# a filename and wrong for a memory column - device_name must not inherit it.
check("device_name(): a spec with no device prefix is blank, not 'DRAM'",
      graphic_store.device_name('SAMPLE.GRF') == "",
      graphic_store.device_name('SAMPLE.GRF'))
check("device_name(): an unknown letter comes back as itself, not a guess",
      graphic_store.device_name('D:THING.DAT') == "D:",
      graphic_store.device_name('D:THING.DAT'))
# The stored-graphic device combos are generated from the same map, so their
# codes - the half that reaches the ZPL - must be untouched by that.
check("STORED_GRAPHIC_DEVICES still offers R/E/B/A, in that order",
      tuple(code for _label, code in zpl_model.STORED_GRAPHIC_DEVICES)
      == ('R', 'E', 'B', 'A'),
      zpl_model.STORED_GRAPHIC_DEVICES)
check("STORED_GRAPHIC_DEVICES labels name real memory types, no placeholders",
      not any("memory)" in label
              for label, _code in zpl_model.STORED_GRAPHIC_DEVICES),
      zpl_model.STORED_GRAPHIC_DEVICES)

# printer_objects.download_printer_object(): a printer answering a .TTF
# retrieval with total silence (blocked to protect font distribution
# rights - see zplcore/fonts.py) must not be treated as broken for every
# other object afterward - each retrieval is judged on its own reply, and
# every request in this module uses a short (5s) timeout so a silent one
# fails fast rather than blocking or tying up the caller.
import inspect
from zplcore import printer_io as zpl_printer_io

_dpo_calls = []
def _fake_dpo_send(address, port, payload, timeout, read_reply=False, cancel=None):
    _dpo_calls.append((address, port, payload, timeout, read_reply))
    return b'' if b'FIRST.TTF' in payload else b'second-object-bytes'

_real_send = zpl_printer_io.send
zpl_printer_io.send = _fake_dpo_send
try:
    _dpo_raised = False
    try:
        printer_objects.download_printer_object('10.0.0.1', 9100, 'E:FIRST.TTF')
    except printer_objects.ObjectNotRetrievable:
        _dpo_raised = True
    _dpo_second = printer_objects.download_printer_object(
        '10.0.0.1', 9100, 'E:SECOND.GRF')
finally:
    zpl_printer_io.send = _real_send

check("download_printer_object(): an empty reply raises ObjectNotRetrievable",
      _dpo_raised, _dpo_raised)
check("download_printer_object(): a later object is still attempted, not skipped",
      len(_dpo_calls) == 2, _dpo_calls)
check("download_printer_object(): the second attempt succeeds with real data",
      _dpo_second == b'second-object-bytes', _dpo_second)
check("download_printer_object(): every attempt uses the short 5s timeout",
      _dpo_calls[0][3] == 5 and _dpo_calls[1][3] == 5, _dpo_calls)
check("delete_printer_object(): defaults to the same 5s timeout",
      inspect.signature(printer_objects.delete_printer_object)
      .parameters['timeout'].default == 5)
check("upload_printer_object(): defaults to the same 5s timeout",
      inspect.signature(printer_objects.upload_printer_object)
      .parameters['timeout'].default == 5)

# printer_io.CancelToken / Cancelled: the one way a dialog abandons a call
# that is out on a worker thread. Cancelled must not be an OSError, or the
# query functions would turn a cancel into "printer unreachable".
import socket, threading, time
from zplcore import graphic_store as zpl_graphic_store

check("Cancelled is not an OSError, so query_* can't swallow it",
      not issubclass(zpl_printer_io.Cancelled, OSError))

def _raise_cancelled(*a, **k):
    raise zpl_printer_io.Cancelled("x")
zpl_printer_io.send = _raise_cancelled
try:
    try:
        _q = printer_objects.query_printer_objects('10.0.0.1', 9100)
        _q_outcome = f"returned {_q!r}"
    except zpl_printer_io.Cancelled:
        _q_outcome = "raised Cancelled"
finally:
    zpl_printer_io.send = _real_send
check("a cancel propagates out of query_printer_objects rather than becoming None",
      _q_outcome == "raised Cancelled", _q_outcome)

# How all three device queries decide a printer is unreachable. Nothing
# covered this before, which is how the rule drifted between them unnoticed:
# silence on the first device (R:) used to condemn the whole query, so a
# printer with nothing on R: reported itself unreachable while answering
# perfectly well on E:.
def _hw_reply(device, entries):
    """A ^HW listing in the shape a real printer sends one."""
    body = ''.join(f"* {device}:{name}    {size}          \r\n"
                   for name, size in entries)
    return (f"\r\n- DIR {device}:*.* \r\n{body}"
            f"\r\n-  66119680 bytes free {device}: ONBOARD FLASH \r\n"
            ).encode('ascii')

def _only_e_answers(_a, _p, payload, _t, read_reply=False, cancel=None):
    """A printer whose R: says nothing at all, but whose E: has objects."""
    if b'^HWE:' in payload:
        return _hw_reply('E', [('LOGO.GRF', 4488), ('ANI.TTF', 120404)])
    return b''

def _nothing_answers(*a, **k):
    return b''

def _empty_listing(_a, _p, payload, _t, read_reply=False, cancel=None):
    device = payload.split(b'^HW')[1][:1].decode()
    return _hw_reply(device, [])

_attempts = []
def _dead_host(_a, _p, payload, _t, read_reply=False, cancel=None):
    _attempts.append(payload)
    raise OSError("no route to host")

for _label, _query, _devices, _wanted in (
        ("query_printer_graphics", zpl_graphic_store.query_printer_graphics,
         zpl_graphic_store.DEVICES, ['E:LOGO.GRF']),
        ("query_printer_objects", printer_objects.query_printer_objects,
         printer_objects.DEVICES, ['E:ANI.TTF', 'E:LOGO.GRF'])):
    zpl_printer_io.send = _only_e_answers
    try:
        _found = _query('10.0.0.1', 9100)
    finally:
        zpl_printer_io.send = _real_send
    check(f"{_label}(): silence on R: is not an unreachable printer",
          _found is not None and _found[0] == _wanted, _found)
    check(f"{_label}(): and every drive that gave nothing is named",
          _found is not None and _found[1] == [d for d in _devices if d != 'E'],
          _found)

    zpl_printer_io.send = _nothing_answers
    try:
        _silent = _query('10.0.0.1', 9100)
    finally:
        zpl_printer_io.send = _real_send
    check(f"{_label}(): None only when no device answered at all",
          _silent is None, _silent)

    zpl_printer_io.send = _empty_listing
    try:
        _none_stored = _query('10.0.0.1', 9100)
    finally:
        zpl_printer_io.send = _real_send
    check(f"{_label}(): a printer that lists nothing is empty, not unreachable",
          _none_stored is not None and _none_stored[0] == [], _none_stored)
    check(f"{_label}(): a drive that answers but holds nothing is not unreadable",
          _none_stored is not None and _none_stored[1] == [], _none_stored)

    # The fast path: a host that cannot be connected to at all must still
    # cost one attempt, not one timeout per device.
    _attempts.clear()
    zpl_printer_io.send = _dead_host
    try:
        _dead = _query('10.0.0.1', 9100)
    finally:
        zpl_printer_io.send = _real_send
    check(f"{_label}(): a dead host is one attempt, not one per device",
          _dead is None and len(_attempts) == 1, (_dead, len(_attempts)))

# cancel= is threaded through every wrapper the same way timeout= is.
_fwd = []
def _capture_send(address, port, payload, timeout, read_reply=False, cancel=None):
    _fwd.append(cancel)
    return b'~DGX,1,1,\r\n00'
_sentinel = object()
zpl_printer_io.send = _capture_send
try:
    printer_objects.download_printer_object('h', 1, 'E:A.B', cancel=_sentinel)
    zpl_fonts.query_printer_fonts('h', 1, cancel=_sentinel)
    zpl_graphic_store.delete_printer_graphic('h', 1, 'R:A.GRF', cancel=_sentinel)
    zpl_printer_io.send_command('h', 1, '~HS', cancel=_sentinel)
finally:
    zpl_printer_io.send = _real_send
check("cancel= reaches printer_io.send from objects, fonts, graphics and the console",
      # Seven, not four: query_printer_fonts asks each of the four devices in
      # turn now, the way query_printer_graphics already did.
      _fwd == [_sentinel] * 7, _fwd)

# The real thing: a listener that accepts and never answers, cancelled from
# another thread, must let send() out promptly with Cancelled - not after
# its 10s timeout.
_srv = socket.socket(); _srv.bind(('127.0.0.1', 0)); _srv.listen(1)
_srv_port = _srv.getsockname()[1]
_token = zpl_printer_io.CancelToken()
_outcome = {}
def _blocked_send():
    try:
        zpl_printer_io.send('127.0.0.1', _srv_port, b'hi', 10, read_reply=True,
                            cancel=_token)
        _outcome['r'] = 'returned'
    except zpl_printer_io.Cancelled:
        _outcome['r'] = 'cancelled'
    except Exception as e:
        _outcome['r'] = f'other {e!r}'
_worker = threading.Thread(target=_blocked_send, daemon=True); _worker.start()
_conn, _ = _srv.accept()
_t0 = time.monotonic()
while _token._sock is None and time.monotonic() - _t0 < 2:
    time.sleep(0.01)
_t0 = time.monotonic()
_token.cancel()
_worker.join(2)
_took = time.monotonic() - _t0
_conn.close(); _srv.close()
check("cancel() unblocks a recv() stuck on a silent printer",
      not _worker.is_alive() and _outcome.get('r') == 'cancelled', (_outcome, _took))
check("and does so promptly, not after the timeout", _took < 1, f"{_took:.2f}s")

# A token cancelled before the call never opens a socket: with nothing
# listening, an attempt would surface as ConnectionRefusedError instead.
_dead = socket.socket(); _dead.bind(('127.0.0.1', 0)); _dead_port = _dead.getsockname()[1]; _dead.close()
_pre = zpl_printer_io.CancelToken(); _pre.cancel()
try:
    zpl_printer_io.send('127.0.0.1', _dead_port, b'x', 5, cancel=_pre)
    _pre_outcome = 'returned'
except zpl_printer_io.Cancelled:
    _pre_outcome = 'Cancelled'
except Exception as e:
    _pre_outcome = type(e).__name__
check("a pre-cancelled token raises Cancelled before connecting", _pre_outcome == 'Cancelled', _pre_outcome)

# workflow's pre-print font check, now in three pieces the frontends chain:
# what's missing (network), the prompt wording (pure), the uploads (network).
class _FontDoc:
    def __init__(self, sources): self._s = sources
    def font_sources(self): return self._s
_real_qpf, _real_upload = zpl_fonts.query_printer_fonts, zpl_fonts.upload_font
try:
    zpl_fonts.query_printer_fonts = lambda *a, **k: None
    check("missing_printer_fonts(): None when the printer could not be asked",
          workflow.missing_printer_fonts(_FontDoc({'E:ARIAL.TTF': '/a.ttf'}), 'h', 1) is None)
    zpl_fonts.query_printer_fonts = lambda *a, **k: ({'E:ARIAL.TTF'}, [])
    check("missing_printer_fonts(): nothing missing when the printer has them all",
          workflow.missing_printer_fonts(_FontDoc({'e:arial.ttf': '/a.ttf'}), 'h', 1) == ({}, {}, []))
    _m = workflow.missing_printer_fonts(_FontDoc(
        {'E:ARIAL.TTF': '/a.ttf', 'E:ROBOTO.TTF': '/r.ttf', 'E:MYSTERY.TTF': None}), 'h', 1)
    check("missing_printer_fonts(): missing vs uploadable (only those with a source file)",
          _m == ({'E:ROBOTO.TTF': '/r.ttf', 'E:MYSTERY.TTF': None},
                 {'E:ROBOTO.TTF': '/r.ttf'}, []), _m)
    # The check the whole device design turns on: the right font on the wrong
    # drive is not the font the label asked for. Matching on the bare name
    # would call this present and let the print fall back to a substitute.
    zpl_fonts.query_printer_fonts = lambda *a, **k: ({'R:ARIAL.TTF'}, [])
    _wrong = workflow.missing_printer_fonts(_FontDoc({'E:ARIAL.TTF': '/a.ttf'}), 'h', 1)
    check("missing_printer_fonts(): a font on another drive does not satisfy the label",
          _wrong == ({'E:ARIAL.TTF': '/a.ttf'}, {'E:ARIAL.TTF': '/a.ttf'}, []), _wrong)
    zpl_fonts.query_printer_fonts = lambda *a, **k: ({'R:ARIAL.TTF'}, [])
    check("missing_printer_fonts(): and on the drive it does name, it is present",
          workflow.missing_printer_fonts(_FontDoc({'R:ARIAL.TTF': '/a.ttf'}), 'h', 1) == ({}, {}, []))

    # A drive that could not be read leaves its fonts unjudged. Calling them
    # missing would offer to upload them - to the very drive that is failing -
    # on no evidence that they are not already there.
    zpl_fonts.query_printer_fonts = lambda *a, **k: ({'E:ARIAL.TTF'}, ['B'])
    _unknown = workflow.missing_printer_fonts(
        _FontDoc({'B:CYRI_UB.TTF': '/c.ttf', 'E:ROBOTO.TTF': '/r.ttf'}), 'h', 1)
    check("missing_printer_fonts(): a font on an unreadable drive is unknown, not missing",
          _unknown == ({'E:ROBOTO.TTF': '/r.ttf'}, {'E:ROBOTO.TTF': '/r.ttf'},
                       ['B']), _unknown)
    _calls = []
    def _fake_qpf(*a, **k):
        _calls.append('asked'); return (set(), [])
    zpl_fonts.query_printer_fonts = _fake_qpf
    check("missing_printer_fonts(): a label with only built-in fonts never asks the printer",
          workflow.missing_printer_fonts(_FontDoc({}), 'h', 1) == ({}, {}, [])
          and _calls == [], _calls)

    _t, _d = workflow.font_problem_prompt(None)
    check("font_problem_prompt(None): the could-not-ask wording",
          'could not be asked' in _t and 'substitute' in _d, (_t, _d))
    # A drive that went unchecked is said so, rather than leaving a reader to
    # assume every drive was looked at.
    _t, _du = workflow.font_problem_prompt({'E:ROBOTO.TTF': '/r.ttf'}, ['B', 'A'])
    check("font_problem_prompt(): names the drives that could not be checked",
          'B:, A: could not be read' in _du and 'not checked' in _du, _du)
    _t, _dn = workflow.font_problem_prompt({'E:ROBOTO.TTF': '/r.ttf'})
    check("font_problem_prompt(): and says nothing about drives when all were read",
          'could not be read' not in _dn, _dn)

    _t, _d = workflow.font_problem_prompt(
        {'E:ROBOTO.TTF': '/r.ttf', 'B:MYSTERY.TTF': None})
    check("font_problem_prompt(): lists each font, flagging the ones with no source",
          'E:ROBOTO.TTF' in _d and 'B:MYSTERY.TTF   (source file unknown)' in _d, _d)
    check("font_problem_prompt(): names the drive a font is wanted on, not an assumed E:",
          'B:MYSTERY.TTF' in _d and 'E:MYSTERY.TTF' not in _d, _d)

    _uploaded, _progress = [], []
    def _fake_upload(address, port, path, name, device='E', timeout=30, cancel=None):
        _uploaded.append((name, path, cancel))
    zpl_fonts.upload_font = _fake_upload
    workflow.upload_fonts({'E:B.TTF': '/b', 'E:A.TTF': '/a'}, 'h', 1,
                          _progress.append, cancel=_sentinel)
    check("upload_fonts(): uploads each font in name order, reporting each, passing cancel through",
          _uploaded == [('A', '/a', _sentinel), ('B', '/b', _sentinel)]
          and _progress == ['Uploading E:A.TTF...', 'Uploading E:B.TTF...'], (_uploaded, _progress))
    # Each font goes to the drive its own spec names, not all to one.
    _devices = []
    def _device_upload(address, port, path, name, device='E', timeout=30, cancel=None):
        _devices.append((device, name))
    zpl_fonts.upload_font = _device_upload
    workflow.upload_fonts({'R:ONE.TTF': '/1', 'B:TWO.TTF': '/2'}, 'h', 1)
    check("upload_fonts(): each font goes to the drive its own spec names",
          sorted(_devices) == [('B', 'TWO'), ('R', 'ONE')], _devices)
    def _failing_upload(address, port, path, name, device='E', timeout=30, cancel=None):
        raise OSError("boom")
    zpl_fonts.upload_font = _failing_upload
    try:
        workflow.upload_fonts({'A': '/a'}, 'h', 1); _up_err = None
    except OSError as e:
        _up_err = str(e)
    check("upload_fonts(): a failure names the font", _up_err == "Upload of A failed: boom", _up_err)
    def _cancelled_upload(address, port, path, name, timeout=30, cancel=None):
        raise zpl_printer_io.Cancelled("x")
    zpl_fonts.upload_font = _cancelled_upload
    try:
        workflow.upload_fonts({'A': '/a'}, 'h', 1); _up_err = 'returned'
    except zpl_printer_io.Cancelled:
        _up_err = 'Cancelled'
    except Exception as e:
        _up_err = type(e).__name__
    check("upload_fonts(): a cancel passes through, not reported as a failed upload",
          _up_err == 'Cancelled', _up_err)
finally:
    zpl_fonts.query_printer_fonts, zpl_fonts.upload_font = _real_qpf, _real_upload

# qtui.busy.BusyBar: the worker thread's result must land back on the GUI
# thread, with the bar hidden again and the blocked buttons restored to the
# state they had - not blindly enabled.
from qtui.busy import BusyBar
from PySide2.QtWidgets import QPushButton
_bb_btn, _bb_off = QPushButton("a"), QPushButton("b")
_bb_off.setEnabled(False)
_bb_msgs, _bb_got = [], []
_bb = BusyBar((_bb_btn, _bb_off), _bb_msgs.append)
def _bb_work(cancel):
    _bb.report("halfway")
    return 42
_bb.run(_bb_work, lambda r, e: _bb_got.append((r, e)))
check("BusyBar.run(): shows the bar and disables the blocked buttons while out",
      _bb.running and not _bb.isHidden() and not _bb_btn.isEnabled())
_t0 = time.monotonic()
while not _bb_got and time.monotonic() - _t0 < 3:
    app.processEvents(); time.sleep(0.01)
check("BusyBar.run(): the result comes back on the GUI thread",
      _bb_got == [(42, None)], _bb_got)
check("BusyBar.report(): progress text lands on the message target", _bb_msgs == ['halfway'], _bb_msgs)
check("BusyBar: afterwards the bar hides and each button is restored to its prior state",
      not _bb.running and _bb.isHidden() and _bb_btn.isEnabled() and not _bb_off.isEnabled())
_bb_got.clear()
_bb.run(lambda c: (_ for _ in ()).throw(ValueError("nope")), lambda r, e: _bb_got.append((r, type(e).__name__)))
_t0 = time.monotonic()
while not _bb_got and time.monotonic() - _t0 < 3:
    app.processEvents(); time.sleep(0.01)
check("BusyBar.run(): an exception in the worker arrives as `error`", _bb_got == [(None, 'ValueError')], _bb_got)


# --- ^SN, ^SF, ^FC: the other ways a printer supplies a field's value -------
# ^SN (serialization) and ^FC (real-time clock) used to be dropped entirely,
# silently, with nothing on screen suggesting a field was ever dynamic - the
# same "vanishes or invents data" trap ^FN alone used to fall into. ^SF, the
# deprecated predecessor to ^SN, is kept only as an opaque, unparsed
# passthrough so a file carrying one still round-trips.

# The finding's own example: a literal '001' the printer increments by 1 each
# label, with leading zeros restored.
_sn = zpl_parser.parse_zpl(
    "^XA^PW812^LL1218^FO50,50^A0N,30,30^FD001^SN001,1,Y^FS^XZ")[0]
_sne = _sn.elements[0]
check("^SN's start, increment and leading-zero flag are all read",
      (_sne.serial_start, _sne.serial_increment, _sne.serial_leading_zero)
      == ('001', 1, True),
      (_sne.serial_start, _sne.serial_increment, _sne.serial_leading_zero))
check("the canvas shows the real value plus a marker it auto-increments",
      _sn.display_text(_sne) == '001«+1»', _sn.display_text(_sne))
check("^SN round-trips byte-identical",
      '^FD001^SN001,1,Y^FS' in _sn.to_zpl(), _sn.to_zpl())
check("^SN is no longer reported as unsupported",
      workflow.unsupported_commands(_sn.to_zpl()) == [],
      workflow.unsupported_commands(_sn.to_zpl()))

# ^SN with no ^FD of its own - its first parameter is the only value the file
# gives this field, so it must not vanish for want of a literal.
_sn_bare = zpl_parser.parse_zpl(
    "^XA^PW812^LL1218^FO50,90^A0N,20,20^SN5,2,N^FS^XZ")[0]
check("a bare ^SN with no ^FD still produces a visible element",
      len(_sn_bare.elements) == 1 and _sn_bare.elements[0].text == '',
      [(e.element_type, getattr(e, 'text', None)) for e in _sn_bare.elements])
check("and shows its own start value, not an empty box",
      _sn_bare.display_text(_sn_bare.elements[0]) == '5«+2»',
      _sn_bare.display_text(_sn_bare.elements[0]))

# The same "don't invent data" rule ^FN already enforces for a barcode.
_sn_bc = zpl_parser.parse_zpl(
    "^XA^PW812^LL1218^FO50,50^BY3^BCN,100^SN1,1,Y^FS^XZ")[0].elements[0]
check("a ^SN barcode field is a barcode with no invented value",
      _sn_bc.element_type == 'barcode' and _sn_bc.barcode_value == ''
      and _sn_bc.serial_start == '1',
      (_sn_bc.element_type, _sn_bc.barcode_value, _sn_bc.serial_start))

# ^FC, with the manual's own default trigger characters - the third of which,
# a bare ~, the tokenizer used to swallow because it looks like the start of a
# tilde command.
_fc = zpl_parser.parse_zpl(
    "^XA^PW812^LL1218^FO50,50^A0N,30,30^FC%,#,~^FD%m/%d/%y^FS^XZ")[0]
_fce = _fc.elements[0]
check("^FC's three trigger characters all survive, tilde included",
      _fce.clock_chars == ('%', '#', '~'), _fce.clock_chars)
check("the canvas wraps the format string rather than showing it as fixed text",
      _fc.display_text(_fce) == '«%m/%d/%y»', _fc.display_text(_fce))
check("^FC round-trips byte-identical, tilde and all",
      '^FC%,#,~^FD%m/%d/%y^FS' in _fc.to_zpl(), _fc.to_zpl())
check("^FC is no longer reported as unsupported",
      workflow.unsupported_commands(_fc.to_zpl()) == [],
      workflow.unsupported_commands(_fc.to_zpl()))

# Only the primary trigger character has a default - the manual gives b and c
# "Default: none". Defaulting them to characters registered two indicators
# the file never asked for, so "Part number #" (the manual's own ^DF example)
# printed its # as a clock substitution instead of a literal character.
_fc_one = zpl_parser.parse_zpl(
    "^XA^PW812^LL1218^FO20,20^A0N,20,20^FC%^FDPart number #%d^FS^XZ")[0]
_fc_one_out = _fc_one.to_zpl()
check("a file registering only the primary indicator keeps # a plain character",
      '^FC%^FD' in _fc_one_out and '^FC%,#' not in _fc_one_out,
      _fc_one_out)
check("a single indicator round-trips as one, not padded to three",
      '^FC%^FD' in _fc_one_out, _fc_one_out)

_fc_two = zpl_parser.parse_zpl(
    "^XA^PW812^LL1218^FO20,20^A0N,20,20^FC%,{^FDx^FS^XZ")[0]
check("two indicators round-trip as two",
      '^FC%,{^FD' in _fc_two.to_zpl(), _fc_two.to_zpl())

_fc_gap = zpl_parser.parse_zpl(
    "^XA^PW812^LL1218^FO20,20^A0N,20,20^FC%,,#^FDx^FS^XZ")[0]
check("a gap between indicators stays a gap, proving the trim is trailing-only",
      '^FC%,,#^FD' in _fc_gap.to_zpl(), _fc_gap.to_zpl())

# The designer's own path: neither editor ever sets clock_chars, so a
# newly-created clock field must not inherit the two absent indicators either.
_fc_new = TextElement(50, 50, "%m/%d/%y")
_fc_new.clock_format = True
check("a clock field created in the designer writes one indicator, not three",
      _fc_new.data_zpl().startswith('^FC%^FD'), _fc_new.data_zpl())

# Custom trigger characters, none of which happen to be the tilde that catches
# the default set.
_fc_custom = zpl_parser.parse_zpl(
    "^XA^PW812^LL1218^FO50,50^A0N,30,30^FC*,&,!^FDtest^FS^XZ")[0].elements[0]
check("custom ^FC trigger characters are read as given",
      _fc_custom.clock_chars == ('*', '&', '!'), _fc_custom.clock_chars)

# The tokenizer fix, checked directly: a lone ~ inside ^FC's own params must
# not be mistaken for the start of a genuine tilde command, and a genuine
# tilde command right after must still split out on its own.
_tokens = zpl_parser.tokenise("^FC%,#,~^FD%m/%d/%y^FS~JR")
check("^FC keeps its trailing tilde parameter",
      ('^FC', '%,#,~') in _tokens, _tokens)
check("a real tilde command straight after still tokenises on its own",
      ('~JR', '') in _tokens, _tokens)

# ^SF is deprecated and not modelled - only preserved, so a file carrying one
# does not lose it on the next save.
_sf = zpl_parser.parse_zpl(
    "^XA^PW812^LL1218^FO50,50^A0N,30,30^FDABC^SF1,999^FS^XZ")[0]
_sfe = _sf.elements[0]
check("^SF's params are kept, unparsed",
      _sfe.serial_field_raw == '1,999', _sfe.serial_field_raw)
check("^SF round-trips byte-identical",
      '^FDABC^SF1,999^FS' in _sf.to_zpl(), _sf.to_zpl())
check("^SF is no longer reported as unsupported either",
      workflow.unsupported_commands(_sf.to_zpl()) == [],
      workflow.unsupported_commands(_sf.to_zpl()))

# ^FH: a hex indicator marking indicatorXX escapes in the ^FD that follows.
# decode_hex() is the pure substitution, checked directly first.
check("decode_hex: a well-formed escape is substituted",
      zpl_fields.decode_hex('_48ello', '_') == 'Hello',
      zpl_fields.decode_hex('_48ello', '_'))
check("decode_hex: a custom indicator character works the same way",
      zpl_fields.decode_hex('~48ello', '~') == 'Hello',
      zpl_fields.decode_hex('~48ello', '~'))
check("decode_hex: a non-hex second digit leaves the escape literal",
      zpl_fields.decode_hex('_4Zello', '_') == '_4Zello',
      zpl_fields.decode_hex('_4Zello', '_'))
check("decode_hex: a lone trailing indicator is left as-is",
      zpl_fields.decode_hex('abc_', '_') == 'abc_',
      zpl_fields.decode_hex('abc_', '_'))
check("decode_hex: no indicator is a no-op",
      zpl_fields.decode_hex('_48ello', None) == '_48ello',
      zpl_fields.decode_hex('_48ello', None))
check("read_hex_indicator: bare ^FH defaults to underscore",
      zpl_fields.read_hex_indicator('') == '_',
      zpl_fields.read_hex_indicator(''))
check("read_hex_indicator: ^FH's own character is read as given",
      zpl_fields.read_hex_indicator('~') == '~',
      zpl_fields.read_hex_indicator('~'))

# End to end: the literal stays raw for storage and round-tripping, and is
# only decoded where it is actually turned into pixels or bars.
_fh = zpl_parser.parse_zpl(
    "^XA^PW812^LL1218^FO50,50^A0N,30,30^FH_^FD_48ello^FS^XZ")[0]
_fhe = _fh.elements[0]
check("^FH's indicator is read onto the element",
      _fhe.hex_indicator == '_', _fhe.hex_indicator)
check("the stored literal stays raw, not decoded",
      _fhe.text == '_48ello', _fhe.text)
check("display_text() decodes the hex escape",
      _fhe.display_text() == 'Hello', _fhe.display_text())
check("^FH round-trips byte-identical",
      '^FH_^FD_48ello^FS' in _fh.to_zpl(), _fh.to_zpl())
check("^FH is no longer reported as unsupported",
      workflow.unsupported_commands(_fh.to_zpl()) == [],
      workflow.unsupported_commands(_fh.to_zpl()))

# A barcode's value is decoded the same way, through the one method both
# canvases already draw bars and the interpretation line from.
_fh_bc = BarcodeElement(50, 50, barcode_value='_48ello', hex_indicator='_')
check("a barcode's encoded_value() decodes its hex escape too",
      _fh_bc.encoded_value() == 'Hello', _fh_bc.encoded_value())

# The print-preview renderer is a second, independent interpreter, so it is
# checked separately: a ^FH-escaped field must render pixel-identical to the
# plain field it decodes to.
_fh_escaped = ZPLRenderer(300, 150).render(
    "^XA^PW300^LL150^FO20,20^A0N,30,30^FH_^FD_48ello^FS^XZ").convert('L')
_fh_plain = ZPLRenderer(300, 150).render(
    "^XA^PW300^LL150^FO20,20^A0N,30,30^FDHello^FS^XZ").convert('L')
check("the renderer decodes ^FH the same way the model does",
      list(_fh_escaped.getdata()) == list(_fh_plain.getdata()))

# add_time_element is its own creation path - the "+ Time" button - rather
# than a mode of add_text_element, and has to size its box against the
# wrapped marker like any other clock field does.
_time_doc = Document()
_time_el = _time_doc.add_time_element()
check("add_time_element makes a clock field, not a mode of a text one",
      _time_el.clock_format and _time_el.text == '%m/%d/%y',
      (_time_el.clock_format, _time_el.text))
check("its box is measured from the wrapped marker, not the bare format string",
      _time_el.width > _time_el.printed_width(None, _time_el.text),
      (_time_el.width, _time_el.printed_width(None, _time_el.text)))

# Likewise, add_serial_element is its own creation path - the "+ Serial"
# button - rather than a mode of add_text_element.
_serial_doc = Document()
_serial_el = _serial_doc.add_serial_element()
check("add_serial_element makes a serial field, not a mode of a text one",
      (_serial_el.serial_increment, _serial_el.serial_start, _serial_el.text)
      == (1, '1', '1'),
      (_serial_el.serial_increment, _serial_el.serial_start, _serial_el.text))
check("its box is measured from the wrapped marker, not the bare start value",
      _serial_el.width > _serial_el.printed_width(None, _serial_el.text),
      (_serial_el.width, _serial_el.printed_width(None, _serial_el.text)))

# And add_numbered_element is its own creation path too now - the
# "+ Numbered" button - rather than a mode of add_text_element. No literal
# by default: inventing one would be the same trap a newly-created ^FN
# barcode used to fall into, so the box has to be measured from the
# placeholder it shows instead of an empty string.
_numbered_doc = Document()
_numbered_el = _numbered_doc.add_numbered_element(7, 'Batch')
check("add_numbered_element makes a numbered field with no invented literal",
      (_numbered_el.field_number, _numbered_el.field_prompt, _numbered_el.text)
      == (7, 'Batch', ''),
      (_numbered_el.field_number, _numbered_el.field_prompt, _numbered_el.text))
check("its box is measured from the placeholder, not an empty literal",
      _numbered_el.width > 0, _numbered_el.width)


# --- the commands that move or flip a whole label ---------------------------
# ^LH and ^LS displace every field: a label carrying one was drawn where its ^FO
# said and printed somewhere else, and a save dropped the command, so it then
# printed where the canvas had been showing it all along.

def _saved_body(zpl):
    out = zpl_parser.parse_zpl(zpl)[0].to_zpl().split('\n')
    return ' '.join(l for l in out if l.strip() and not l.startswith('^FX')
                    and l not in ('^XA', '^XZ', '^PW812', '^LL1218'))

_lh = zpl_parser.parse_zpl(
    "^XA^PW812^LL1218^LH100,100^FO50,50^A0N,30,30^FDx^FS^XZ")[0]
check("^LH lands the element where it will print",
      (_lh.elements[0].x, _lh.elements[0].y) == (150, 150),
      (_lh.elements[0].x, _lh.elements[0].y))
check("and comes back out of a save unchanged",
      _saved_body("^XA^PW812^LL1218^LH100,100^FO50,50^A0N,30,30^FDx^FS^XZ")
      == '^LH100,100 ^FO50,50 ^A0N,30,30 ^FDx^FS',
      _saved_body("^XA^PW812^LL1218^LH100,100^FO50,50^A0N,30,30^FDx^FS^XZ"))

# ^LS shifts fields left, so it subtracts where ^LH adds
_ls = zpl_parser.parse_zpl(
    "^XA^PW812^LL1218^LS30^FO50,50^A0N,30,30^FDx^FS^XZ")[0]
check("^LS shifts a field to the left", _ls.elements[0].x == 20, _ls.elements[0].x)
_both = zpl_parser.parse_zpl(
    "^XA^PW812^LL1218^LH100,100^LS30^FO50,50^A0N,30,30^FDx^FS^XZ")[0]
check("and the two compose, ^LS against ^LH",
      (_both.elements[0].x, _both.elements[0].y) == (120, 150),
      (_both.elements[0].x, _both.elements[0].y))

# ^LT registers the label against the media; it does not lay fields out on it
_lt = zpl_parser.parse_zpl(
    "^XA^PW812^LL1218^LT10^FO50,50^A0N,30,30^FDx^FS^XZ")[0]
check("^LT moves nothing on the label",
      (_lt.elements[0].x, _lt.elements[0].y) == (50, 50),
      (_lt.elements[0].x, _lt.elements[0].y))
check("but is still written back rather than dropped",
      '^LT10' in _saved_body("^XA^PW812^LL1218^LT10^FO50,50^A0N,30,30^FDx^FS^XZ"),
      _saved_body("^XA^PW812^LL1218^LT10^FO50,50^A0N,30,30^FDx^FS^XZ"))

# "This command affects only fields that come after it"
_running = zpl_parser.parse_zpl(
    "^XA^PW812^LL1218^FO10,10^A0N,30,30^FDa^FS"
    "^LH100,100^FO50,50^A0N,30,30^FDb^FS^XZ")[0]
check("a ^LH part-way through leaves the fields before it alone",
      [(e.x, e.y) for e in _running.elements] == [(10, 10), (150, 150)],
      [(e.x, e.y) for e in _running.elements])
check("and no ^FO is written back negative, which ZPL has no room for",
      all(not part.startswith('-')
          for line in _running.to_zpl().split('\n') if line.startswith('^FO')
          for part in line[3:].split(',')),
      [l for l in _running.to_zpl().split('\n') if l.startswith('^FO')])

# The three flips round-trip, and the preview applies the two that are whole-
# image operations.
_flips = _saved_body("^XA^PW812^LL1218^POI^PMY^LRY^FO50,50^A0N,30,30^FDx^FS^XZ")
check("^PO, ^PM and ^LR all survive a save",
      '^POI' in _flips and '^PMY' in _flips and '^LRY' in _flips, _flips)
check("and none of the six is reported as unsupported any more",
      workflow.unsupported_commands(
          "^XA^LH1,1^LS1^LT1^POI^PMY^LRY^FO1,1^A0N,30,30^FDx^FS^XZ") == [])

# Printing must not depend on the printer already being clean of a previous
# job's ^PO/^PM/^LR - unlike ^XA...^XZ, these three are sticky at the printer
# and outlive the job that set them, so the print path asks to_zpl to say so
# even when a flag is at ZPL's own default.
_lt_default = zpl_transforms.LabelTransform()
check("to_zpl(explicit_flips=True) states all three flags even at default",
      _lt_default.to_zpl(explicit_flips=True) == "^PON\n^PMN\n^LRN\n",
      _lt_default.to_zpl(explicit_flips=True))
check("and the save path is unchanged: still nothing at default",
      _lt_default.to_zpl() == "", _lt_default.to_zpl())

_lt_flipped = zpl_transforms.LabelTransform()
_lt_flipped.invert = _lt_flipped.mirror = _lt_flipped.reverse = True
check("an already-flipped label states the flip, not both forms",
      _lt_flipped.to_zpl(explicit_flips=True) == "^POI\n^PMY\n^LRY\n",
      _lt_flipped.to_zpl(explicit_flips=True))

_print_doc = zpl_parser.parse_zpl(
    "^XA^PW812^LL1218^FO50,50^A0N,30,30^FDx^FS^XZ")[0]
check("Document.to_zpl(explicit_flips=True) threads through to the transform",
      all(tok in _print_doc.to_zpl(explicit_flips=True)
          for tok in ('^PON', '^PMN', '^LRN')),
      _print_doc.to_zpl(explicit_flips=True))
check("but Document.to_zpl() (the save path) is untouched",
      not any(tok in _print_doc.to_zpl()
              for tok in ('^PON', '^PMN', '^LRN', '^POI', '^PMY', '^LRY')),
      _print_doc.to_zpl())

_plain = _preview_ink("^XA^PW300^LL200^FO20,20^A0N,30,30^FDHg^FS", 300, 200)
check("the preview moves the ink by ^LH",
      _preview_ink("^XA^PW300^LL200^LH100,50^FO20,20^A0N,30,30^FDHg^FS", 300, 200)
      == (_plain[0] + 100, _plain[1] + 50, _plain[2], _plain[3]),
      _preview_ink("^XA^PW300^LL200^LH100,50^FO20,20^A0N,30,30^FDHg^FS", 300, 200))
check("^POI turns the finished label end for end",
      _preview_ink("^XA^PW300^LL200^POI^FO20,20^A0N,30,30^FDHg^FS", 300, 200)
      == (300 - _plain[0] - _plain[2], 200 - _plain[1] - _plain[3],
          _plain[2], _plain[3]),
      _preview_ink("^XA^PW300^LL200^POI^FO20,20^A0N,30,30^FDHg^FS", 300, 200))
check("and ^PMY mirrors it left to right",
      _preview_ink("^XA^PW300^LL200^PMY^FO20,20^A0N,30,30^FDHg^FS", 300, 200)
      == (300 - _plain[0] - _plain[2], _plain[1], _plain[2], _plain[3]),
      _preview_ink("^XA^PW300^LL200^PMY^FO20,20^A0N,30,30^FDHg^FS", 300, 200))
check("^LT moves the preview no more than it moves the model",
      _preview_ink("^XA^PW300^LL200^LT10^FO20,20^A0N,30,30^FDHg^FS", 300, 200)
      == _plain)

# ^FX runs only to the next caret, so prose naming a command becomes that
# command. Junk parameters must not pass themselves off as an origin of 0,0.
_prose = zpl_parser.parse_zpl(
    "^XA^PW812^LL1218^FX see ^LH for details^LH20,100"
    "^FO0,0^A0N,30,30^FDx^FS^XZ")[0]
check("prose naming ^LH in a comment does not become the origin",
      _prose.transform.home == (20, 100)
      and (_prose.elements[0].x, _prose.elements[0].y) == (20, 100),
      (_prose.transform.home, (_prose.elements[0].x, _prose.elements[0].y)))

# An ^FX the author wrote is content: a save keeps it, near where it was.
_noted = zpl_parser.parse_zpl(
    "^XA^FXSHIPPING LABEL^PW400^LL400^FO10,10^GB20,20,1^FS"
    "^FXsender\n^FO50,50^FXinside^A0N,30,30^FDx^FS"
    "^FO90,90^ZZ1^FS^FXorphan^FO70,70^GB5,5,1^FS^FXthe end^XZ")[0]
_noted_out = _noted.to_zpl()
check("a comment ahead of every field is the label's, written after ^XA",
      _noted.comments == ['SHIPPING LABEL']
      and _noted_out.startswith("^XA\n^FXSHIPPING LABEL\n"), _noted_out)
check("a comment ahead of or inside a field travels with that element",
      _noted.elements[1].comments == ('sender', 'inside')
      and "^FXsender\n^FXinside\n^FO50,50" in _noted_out,
      [e.comments for e in _noted.elements])
check("a field that builds nothing does not swallow the comment after it",
      _noted.elements[2].comments == ('orphan',),
      [e.comments for e in _noted.elements])
check("a comment after the last field is written before ^XZ",
      _noted.trailing_comments == ['the end']
      and _noted_out.endswith("^FXthe end\n^XZ"), _noted_out)
check("designer keys are not taken for comments",
      not any('DESIGNER' in c for c in _noted.comments + _noted.trailing_comments)
      and _noted_out.count('^FXDESIGNER_DPI') == 1, _noted_out)
check("a label with comments saves the same twice",
      zpl_parser.parse_zpl(_noted_out)[0].to_zpl() == _noted_out)
_hidden_note = zpl_parser.parse_zpl(
    "^XA^PW400^LL400^FO1,1^GB5,5,1^FS"
    "^FXhidden one\n^FXDESIGNER_GROUP:1\n^FXDESIGNER_NOPRINT:"
    + base64.b64encode(b"^FO10,10^GB20,20,1^FS\n").decode()
    + "\n^FXDESIGNER_GROUP:1\n^FO30,30^GB9,9,1^FS^XZ")[0]
_hidden_out = _hidden_note.to_zpl()
check("a hidden grouped element keeps its comment ahead of its markers",
      "^FXhidden one\n^FXDESIGNER_GROUP:1\n^FXDESIGNER_NOPRINT:" in _hidden_out
      and [(e.comments, e.group, e.print_enabled)
           for e in zpl_parser.parse_zpl(_hidden_out)[0].elements]
      == [((), None, True), (('hidden one',), (1,), False),
          ((), (1,), True)], _hidden_out)
check("a label that is only a note is still empty",
      zpl_parser.parse_zpl("^XA^FX only a note^XZ")[0].is_empty())

# The fixtures, as whole files
_home_raw = (FIXTURES / 'label_home.zpl').read_text()
_home_doc = zpl_parser.parse_zpl(_home_raw)[0]
check("the preprinted-stock fixture places every field below the header",
      [(e.x, e.y) for e in _home_doc.elements]
      == [(20, 100), (20, 140), (20, 180), (20, 260)],
      [(e.x, e.y) for e in _home_doc.elements])
check("and writes its ^LH back with the ^FO it came in with",
      '^LH20,100' in _home_doc.to_zpl() and '^FO0,0' in _home_doc.to_zpl(),
      [l for l in _home_doc.to_zpl().split('\n') if l.startswith(('^LH', '^FO'))])
_flip_raw = (FIXTURES / 'flipped_label.zpl').read_text()
_flip_doc = zpl_parser.parse_zpl(_flip_raw)[0]
check("the flipped fixture keeps both flips",
      _flip_doc.transform.invert and _flip_doc.transform.mirror,
      (_flip_doc.transform.invert, _flip_doc.transform.mirror))
check("and neither fixture reports anything unsupported",
      workflow.unsupported_commands(_home_raw) == []
      and workflow.unsupported_commands(_flip_raw) == [])

# Undo holds whole documents, so a shared transform would rewrite every entry
_tdoc = zpl_parser.parse_zpl(_home_raw)[0]
_tsnap = _tdoc.snapshot()
_tdoc.transform.home = (999, 999)
_tdoc.restore(_tsnap)
check("an undo snapshot does not share the transform",
      _tdoc.transform.home == (20, 100), _tdoc.transform.home)

# --- ^PQ: print quantity -----------------------------------------------------
# The most common command this designer had never modelled: a label saying
# "print 5 copies" opened and saved (or printed) came back saying "print 1".

_pq = zpl_parser.parse_zpl("^XA^PQ5,2,1,Y,N^XZ")[0]
check("^PQ sets quantity, pause count, replicates and both flags",
      (_pq.print_quantity, _pq.print_pause_count, _pq.print_replicates,
       _pq.print_override_pause, _pq.print_cut_on_error)
      == (5, 2, 1, True, False),
      (_pq.print_quantity, _pq.print_pause_count, _pq.print_replicates,
       _pq.print_override_pause, _pq.print_cut_on_error))

_no_pq = zpl_parser.parse_zpl("^XA^XZ")[0]
check("a format with no ^PQ defaults to one copy and no options",
      (_no_pq.print_quantity, _no_pq.print_pause_count,
       _no_pq.print_replicates, _no_pq.print_override_pause,
       _no_pq.print_cut_on_error)
      == (1, 0, 0, False, True))
check("and writes back no ^PQ line at all", '^PQ' not in _no_pq.to_zpl())

check("a quantity on its own round-trips as just ^PQ5",
      '^PQ5' in zpl_parser.parse_zpl("^XA^PQ5^XZ")[0].to_zpl().split('\n'))
check("a later parameter forces the earlier ones to be spelled too",
      '^PQ1,3' in zpl_parser.parse_zpl("^XA^PQ1,3^XZ")[0].to_zpl().split('\n'))
check("and every parameter round-trips together",
      '^PQ5,2,1,Y' in zpl_parser.parse_zpl("^XA^PQ5,2,1,Y^XZ")[0].to_zpl().split('\n'))
# The fifth, cut-on-error, defaults to Y: dropping an N sent the printer back to
# cutting after every voided RFID label.
check("cut-on-error N survives a save",
      '^PQ50,10,5,Y,N' in
      zpl_parser.parse_zpl("^XA^PQ50,10,5,Y,N^XZ")[0].to_zpl().split('\n'))
check("and on its own forces the four before it to be spelled",
      '^PQ1,0,0,N,N' in
      zpl_parser.parse_zpl("^XA^PQ1,0,0,N,N^XZ")[0].to_zpl().split('\n'))
check("while an explicit Y, the default, is trimmed away",
      '^PQ5' in zpl_parser.parse_zpl("^XA^PQ5,0,0,N,Y^XZ")[0].to_zpl().split('\n'))

check("^PQ is no longer reported as something a save would drop",
      workflow.unsupported_commands("^XA^PQ5,1,0,Y^XZ") == [])

# --- ^CV: code validation ----------------------------------------------------
# A switch asking the printer to check each barcode's data as it prints. It
# says nothing about where anything sits, so there is nothing to draw - but a
# file carrying it was met with the "does not understand" dialog and lost it
# on save. The manual's own example, with the EAN-13 that is valid.

_cv_example = "^XA^CVY^FO50,50^BEN,100,Y,N^FD9782345678907^FS^XZ"
_cv = zpl_parser.parse_zpl(_cv_example)[0]
check("^CVY turns code validation on", _cv.code_validation is True)
check("^CVN, a bare ^CV and no ^CV at all leave it off",
      not zpl_parser.parse_zpl("^XA^CVN^XZ")[0].code_validation
      and not zpl_parser.parse_zpl("^XA^CV^XZ")[0].code_validation
      and not zpl_parser.parse_zpl("^XA^XZ")[0].code_validation)
check("the switch is read wherever it appears, and the last one wins",
      zpl_parser.parse_zpl(
          "^XA^CVN^FO1,1^BCN,50^FD1^FS^CVY^XZ")[0].code_validation is True)

check("^CVY round-trips through a save", '^CVY' in _cv.to_zpl().split('\n'))
check("and is written before the barcodes it checks",
      _cv.to_zpl().index('^CVY') < _cv.to_zpl().index('^FO'))
check("a format that never said ^CV writes none back",
      '^CV' not in zpl_parser.parse_zpl("^XA^XZ")[0].to_zpl())
check("nor does one that said ^CVN, which is ZPL's own default",
      '^CV' not in zpl_parser.parse_zpl("^XA^CVN^XZ")[0].to_zpl())

# Sticky at the printer "from format to format", like ^PO/^PM/^LR, so the
# print path has to say so even when it is off.
_cv_plain = zpl_parser.parse_zpl("^XA^PW812^LL1218^FO50,50^A0N,30,30^FDx^FS^XZ")[0]
check("printing states ^CVN for a label that does not validate",
      '^CVN' in _cv_plain.to_zpl(explicit_flips=True).split('\n'),
      _cv_plain.to_zpl(explicit_flips=True))
check("and ^CVY for one that does",
      '^CVY' in _cv.to_zpl(explicit_flips=True).split('\n'))
check("while the save path goes on writing nothing at default",
      '^CV' not in _cv_plain.to_zpl())

check("^CV is no longer reported as something a save would drop",
      workflow.unsupported_commands(_cv_example) == [])
_cv_raw = (FIXTURES / 'code_validation.zpl').read_text()
check("the fixture reads as one barcode and reports nothing",
      len(zpl_parser.parse_zpl(_cv_raw)[0].elements) == 1
      and zpl_parser.parse_zpl(_cv_raw)[0].code_validation
      and workflow.unsupported_commands(_cv_raw) == [])

# The manual's second label is a QR code spelled ^BQN2,3, with the comma after
# the orientation missing (page 161). The orientation is one letter, so the 2
# is the model and the 3 the magnification. Reading "N2" as the orientation
# opened the symbol at the default magnification, and a save wrote that back.
_cv_qr = zpl_parser.parse_zpl(
    "^XA^CVY^FO50,50^BQN2,3^FDHM,BQRCODE-22^FS^XZ")[0].elements[0]
check("the manual's ^BQN2,3 reads as orientation N, model 2, magnification 3",
      (_cv_qr.orientation, _cv_qr.qr_model, _cv_qr.module_width)
      == ('N', 2, 3),
      (_cv_qr.orientation, _cv_qr.qr_model, _cv_qr.module_width))
check("and a save writes the comma it was missing",
      '^BQN,2,3' in _cv_qr.to_zpl().split('\n'), _cv_qr.to_zpl())
check("the same holds for any barcode command: ^BCN100 is 100 dots tall",
      zpl_parser.parse_zpl(
          "^XA^FO0,0^BCN100^FD123^FS^XZ")[0].elements[0].bar_height == 100)

check("^CV draws nothing in the preview",
      _preview_ink("^XA^PW300^LL200^CVY^FO20,20^A0N,30,30^FDHg^FS", 300, 200)
      == _preview_ink("^XA^PW300^LL200^FO20,20^A0N,30,30^FDHg^FS", 300, 200))

# --- ^CI, ^CW, ^FL: encoding and font identity ------------------------------
# Which bytes mean which glyphs: the encoding the field data is in, a letter
# assigned to a downloaded font, and a font linked to another for the glyphs
# it lacks. None of them draws anything, and all three were met with the
# "does not understand" dialog and lost on save - after which a ^CI28 file's
# UTF-8 text printed as CP850 mojibake.

def _header(zpl):
    """The lines a save writes ahead of the first field."""
    out = zpl_parser.parse_zpl(zpl)[0].to_zpl().split('\n')
    return out[:next((i for i, l in enumerate(out) if l.startswith('^FO')),
                     len(out))]

check("^CI28 is carried as the encoding",
      zpl_parser.parse_zpl("^XA^CI28^XZ")[0].encoding == '28')
check("and a remap table rides along verbatim - the manual's own example",
      zpl_parser.parse_zpl(
          "^XA^CI0,21,36^FO100,200^A0N50,50^FD$0123^FS^XZ")[0].encoding
      == '0,21,36')
check("a bare ^CI, one that names no number, and no ^CI at all carry nothing",
      zpl_parser.parse_zpl("^XA^CI^XZ")[0].encoding is None
      and zpl_parser.parse_zpl("^XA^CIx^XZ")[0].encoding is None
      and zpl_parser.parse_zpl("^XA^XZ")[0].encoding is None)
check("the first ^CI wins: a trailing reset is not what the fields were "
      "written under",
      zpl_parser.parse_zpl(
          "^XA^CI28^FO1,1^A0N,9,9^FDx^FS^CI0^XZ")[0].encoding == '28')

check("an ASCII label under ^CI6 goes on carrying it, at the top",
      '^CI6' in _header("^XA^CI6^FO1,1^A0N,9,9^FD[x]^FS^XZ"),
      _header("^XA^CI6^FO1,1^A0N,9,9^FD[x]^FS^XZ"))
check("a label holding non-ASCII is written ^CI28 - the bytes are UTF-8",
      '^CI28' in _header("^XA^FO1,1^A0N,9,9^FDGrüße^FS^XZ"),
      _header("^XA^FO1,1^A0N,9,9^FDGrüße^FS^XZ"))
_ci6_edited = _header("^XA^CI6^FO1,1^A0N,9,9^FDGrüße^FS^XZ")
check("whatever the file said: ^CI6 with non-ASCII in it becomes ^CI28",
      '^CI28' in _ci6_edited and '^CI6' not in _ci6_edited, _ci6_edited)
check("a plain ASCII label saves with no ^CI, byte-identical to before",
      '^CI' not in _cv_plain.to_zpl())
check("but printing states ^CI28 for it - sticky at the printer like ^CV",
      '^CI28' in _cv_plain.to_zpl(explicit_flips=True).split('\n'))
check("and states the file's own ^CI when it carried one",
      '^CI6' in zpl_parser.parse_zpl(
          "^XA^CI6^FO1,1^A0N,9,9^FDx^FS^XZ")[0].to_zpl(
              explicit_flips=True).split('\n'))
_hidden_unicode = zpl_parser.parse_zpl("^XA^FO1,1^A0N,9,9^FDGrüße^FS^XZ")[0]
_hidden_unicode.elements[0].print_enabled = False
check("a hidden non-ASCII element does not force ^CI28: it does not print",
      '^CI' not in _hidden_unicode.to_zpl(), _hidden_unicode.to_zpl())

_cw = zpl_parser.parse_zpl(
    "^XA^CWQ,E:MYFONT.TTF^FO20,20^AQN,30,30^FDx^FS^XZ")[0]
check("^CW is carried by letter, verbatim",
      _cw.font_identifiers == {'Q': 'Q,E:MYFONT.TTF'}, _cw.font_identifiers)
check("and the ^A that calls the letter still writes the letter",
      '^AQN,30,30' in _cw.to_zpl().split('\n'), _cw.to_zpl())
check("a letter assigned twice keeps its place and takes the last",
      list(zpl_parser.parse_zpl(
          "^XA^CWQ,E:ONE.TTF^CWA,R:TWO.FNT^CWQ,R:THREE.FNT^XZ"
      )[0].font_identifiers.items())
      == [('Q', 'Q,R:THREE.FNT'), ('A', 'A,R:TWO.FNT')])
check("the manual's built-in replacement round-trips as it was",
      '^CWA,R:MYFONT.FNT' in _saved_body("^XA^CWA,R:MYFONT.FNT^XZ"),
      _saved_body("^XA^CWA,R:MYFONT.FNT^XZ"))
check("a ^CW with no letter is ignored",
      zpl_parser.parse_zpl("^XA^CW^XZ")[0].font_identifiers == {}
      and zpl_parser.parse_zpl("^XA^CW,E:X.TTF^XZ")[0].font_identifiers == {})

_fl_example = "^XA^FLE:ANMDJ.TTF,E:SWISS721.TTF,1^FS^XZ"
check("^FL round-trips with the ^FS the manual gives it",
      '^FLE:ANMDJ.TTF,E:SWISS721.TTF,1^FS' in _saved_body(_fl_example),
      _saved_body(_fl_example))
check("and two of them keep their order - an unlink after a link is not "
      "the same as neither",
      zpl_parser.parse_zpl(
          "^XA^FLE:A.TTF,E:B.TTF,1^FS^FLE:A.TTF,E:B.TTF,0^FS^FL^XZ"
      )[0].font_links == ['E:A.TTF,E:B.TTF,1', 'E:A.TTF,E:B.TTF,0'])

_identity_raw = (FIXTURES / 'font_identity.zpl').read_text()
_identity = zpl_parser.parse_zpl(_identity_raw)[0]
_identity_lines = _identity.to_zpl().split('\n')
check("the fixture reads as one text element reading Grüße",
      len(_identity.elements) == 1 and _identity.elements[0].text == 'Grüße')
check("and writes all three back ahead of ^PW and the first field",
      _identity_lines.index('^CI28') < _identity_lines.index('^PW406')
      and _identity_lines.index('^CWQ,E:MYFONT.TTF') < _identity_lines.index('^PW406')
      and (_identity_lines.index('^FLE:ANMDJ.TTF,E:SWISS721.TTF,1^FS')
           < _identity_lines.index('^PW406')),
      _identity_lines)
check("none of the three is reported as something a save would drop",
      workflow.unsupported_commands(_identity_raw) == []
      and workflow.unsupported_commands("^XA^CWA,R:MYFONT.FNT^XZ") == []
      and workflow.unsupported_commands(_fl_example) == []
      and workflow.unsupported_commands(
          "^XA^CI0,21,36^FO100,200^A0N50,50^FD$0123^FS^XZ") == [])
check("a format that is only a ^CW is not empty - it is the manual's example",
      not zpl_parser.parse_zpl("^XA^CWA,R:MYFONT.FNT^XZ")[0].is_empty()
      and not zpl_parser.parse_zpl(_fl_example)[0].is_empty())

check("^CI, ^CW and ^FL draw nothing in the preview",
      _preview_ink("^XA^PW300^LL200^CI28^CWQ,E:X.TTF^FLE:A.TTF,E:B.TTF,1^FS"
                   "^FO20,20^A0N,30,30^FDHg^FS", 300, 200)
      == _preview_ink("^XA^PW300^LL200^FO20,20^A0N,30,30^FDHg^FS", 300, 200))

# A file that is not UTF-8 used to fail to open outright - every read was
# open(..., encoding='utf-8'). It is read by the ^CI it declares now, and a
# save then converts it to UTF-8 with ^CI28, which prints the same glyphs.
_decode = zpl_parser.decode_file
check("a UTF-8 file decodes as itself, with no code page to report",
      _decode('^XA^FDGrüße^FS^XZ'.encode('utf-8')) == ('^XA^FDGrüße^FS^XZ', None))
check("a UTF-8 BOM - the manual's alternative to ^CI28 - is dropped, not kept "
      "as a stray character ahead of ^XA",
      _decode(b'\xef\xbb\xbf' + '^XA^FDx^FS^XZ'.encode('utf-8'))
      == ('^XA^FDx^FS^XZ', None))
check("a UTF-16 file is read by its BOM",
      _decode('^XA^FDGrüße^FS^XZ'.encode('utf-16')) == ('^XA^FDGrüße^FS^XZ', None))
check("a ^CI0 file with é as the CP850 byte 0x82 opens as é and says so",
      _decode(b'^XA^CI0^FO1,1^A0N,9,9^FDcaf\x82^FS^XZ')
      == ('^XA^CI0^FO1,1^A0N,9,9^FDcafé^FS^XZ', 'cp850'))
check("no ^CI at all reads as ^CI0 - the power-up value a printer would use",
      _decode(b'^XA^FDcaf\x82^FS^XZ') == ('^XA^FDcafé^FS^XZ', 'cp850'))
check("a ^CI27 file with é as the CP1252 byte 0xE9 opens as é",
      _decode(b'^XA^CI27^FDcaf\xe9^FS^XZ') == ('^XA^CI27^FDcafé^FS^XZ', 'cp1252'))
check("a ^CI15 file reads as Shift-JIS",
      _decode(b'^XA^CI15^FD\x93\xfa\x96\x7b^FS^XZ') == ('^XA^CI15^FD日本^FS^XZ', 'shift_jis'))
_refused = []
for _bad in (b'^XA^CI16^FDcaf\xe9^FS^XZ', b'^XA^CI28^FDcaf\xe9^FS^XZ'):
    try:
        _decode(_bad)
    except ValueError as e:
        _refused.append(str(e))
check("a table-driven ^CI16, or a ^CI28 that is not UTF-8, is refused by "
      "name rather than guessed at",
      len(_refused) == 2 and '^CI16' in _refused[0] and '^CI28' in _refused[1],
      _refused)

_cp850_doc = zpl_parser.parse_zpl(
    _decode(b'^XA^CI0^FO1,1^A0N,9,9^FDcaf\x82^FS^XZ')[0])[0]
check("and once open, such a file carries its ^CI0 and its é",
      _cp850_doc.encoding == '0' and _cp850_doc.elements[0].text == 'café')
check("so a save converts it: UTF-8 bytes under ^CI28, the same glyphs",
      '^CI28' in _cp850_doc.to_zpl().split('\n')
      and '^CI0' not in _cp850_doc.to_zpl()
      and 'caf\xc3\xa9'.encode('latin-1') in _cp850_doc.to_zpl().encode('utf-8'),
      _cp850_doc.to_zpl())

_cp850_path = os.path.join(tmp, 'cp850.zpl')
with open(_cp850_path, 'wb') as f:
    f.write(b'^XA^PW300^LL200^CI0^FO20,20^A0N,30,30^FDcaf\x82^FS^XZ')
check("read_file() reads from disk the same way",
      zpl_parser.read_file(_cp850_path)
      == ('^XA^PW300^LL200^CI0^FO20,20^A0N,30,30^FDcafé^FS^XZ', 'cp850'))
check("and the file-chooser preview renders such a file rather than failing",
      ZPLRenderer(300, 200).render_from_file(_cp850_path).size == (300, 200))
check("the notice names the code page and what a save will do",
      'cp850' in workflow.decoded_notice('cp850')[0]
      and '^CI28' in workflow.decoded_notice('cp850')[1])

# --- ^PR, ^MD, ^MM, ^MN, ^MT: print and media settings ---------------------
# The printer's speed, darkness, print mode, media tracking and media type -
# what a generator's header sets ahead of the first field. None draws
# anything, and all five were met with the "does not understand" dialog and
# lost on save, after which the printer ran the label on whatever it had last
# been told.

_media = zpl_parser.parse_zpl(
    "^XA^PR6,6,2^MD-9^MMC,Y^MNM,20^MTD^FO1,1^A0N,9,9^FDx^FS^XZ")[0]
check("each of the five is carried verbatim, in the order it came",
      list(_media.media_settings.items())
      == [('^PR', '6,6,2'), ('^MD', '-9'), ('^MM', 'C,Y'), ('^MN', 'M,20'),
          ('^MT', 'D')], _media.media_settings)
check("a second ^MD keeps the first one's place and takes its value - two "
      "^MDs do not add up",
      list(zpl_parser.parse_zpl(
          "^XA^MD-6^PR4^FO1,1^A0N,9,9^FDx^FS^MD2^XZ"
      )[0].media_settings.items()) == [('^MD', '2'), ('^PR', '4')])
check("one with no value is ignored, as the printer ignores it, and does not "
      "wipe out an earlier one",
      zpl_parser.parse_zpl("^XA^MD10^MD^MM^XZ")[0].media_settings
      == {'^MD': '10'})

_media_header = _header(
    "^XA^PR6,6,2^MD-9^MMC,Y^MNM,20^MTD^PW812^LL1218"
    "^FO1,1^A0N,9,9^FDx^FS^XZ")
check("a save writes them back ahead of ^PW, one line each, as spelled",
      _media_header[_media_header.index('^PR6,6,2'):
                    _media_header.index('^PW812')]
      == ['^PR6,6,2', '^MD-9', '^MMC,Y', '^MNM,20', '^MTD'], _media_header)
check("a format that carried none writes none - existing files unchanged",
      not any(l[:3] in zpl_parser.MEDIA_SETTINGS
              for l in _cv_plain.to_zpl().split('\n')))
_media_lines = [l for l in _media.to_zpl(explicit_flips=True).split('\n')
                if l[:3] in zpl_parser.MEDIA_SETTINGS]
check("printing sends the same lines - none of the five has an off to state",
      _media_lines == ['^PR6,6,2', '^MD-9', '^MMC,Y', '^MNM,20', '^MTD'],
      _media_lines)
check("and adds none for a label that carried none",
      not any(l[:3] in zpl_parser.MEDIA_SETTINGS
              for l in _cv_plain.to_zpl(explicit_flips=True).split('\n')))
check("a label with them saves the same twice",
      zpl_parser.parse_zpl(_media.to_zpl())[0].to_zpl() == _media.to_zpl())

# ZebraDesigner's own shape: a set-up format of its own ahead of the label,
# then ^MMT at the top of it. What is still left behind is still named.
_designer_header = (
    "^XA~TA000~JSN^LT0^MNW^MTT^PON^PMN^LH0,0^JMA^PR6,6~SD15^JUS^LRN^CI0^XZ"
    "^XA^MMT^PW812^LL1218^LS0^FT10,40^A0N,30,30^FDx^FS^PQ1,0,1,Y^XZ")
check("a generator's set-up header keeps all four of its settings",
      [l for l in _header(_designer_header) if l[:3] in zpl_parser.MEDIA_SETTINGS]
      == ['^MNW', '^MTT', '^PR6,6', '^MMT'], _header(_designer_header))
check("and is warned about only for what a save still drops",
      workflow.unsupported_commands(_designer_header)
      == ['~TA', '~JS', '^JM', '~SD', '^JU'],
      workflow.unsupported_commands(_designer_header))

_media_raw = (FIXTURES / 'media_settings.zpl').read_text()
_media_doc = zpl_parser.parse_zpl(_media_raw)[0]
_media_fixture_lines = _media_doc.to_zpl().split('\n')
check("the fixture reads as one text element and reports nothing",
      len(_media_doc.elements) == 1
      and _media_doc.elements[0].text == 'SHIP TO'
      and workflow.unsupported_commands(_media_raw) == [])
check("and writes all five back ahead of ^PW and the first field",
      _media_fixture_lines[_media_fixture_lines.index('^MMT'):
                           _media_fixture_lines.index('^PW812')]
      == ['^MMT', '^MNW', '^MTT', '^PR6,6', '^MD10'], _media_fixture_lines)
check("a format that is only a print rate is not empty",
      not zpl_parser.parse_zpl("^XA^PR6,6^XZ")[0].is_empty())

check("^PR, ^MD, ^MM, ^MN and ^MT draw nothing in the preview",
      _preview_ink("^XA^PW300^LL200^PR2^MD30^MMC^MNN^MTD"
                   "^FO20,20^A0N,30,30^FDHg^FS", 300, 200)
      == _preview_ink("^XA^PW300^LL200^FO20,20^A0N,30,30^FDHg^FS", 300, 200))

# --- printer_io.send_command(): the console's text-in/text-out wrapper -----
# It should encode the command as UTF-8, pass it straight through to send()
# unmodified (read_reply always on, since a console has no other way to know
# whether anything came back), and decode whatever comes back the same way -
# including a reply that isn't valid UTF-8, which must not raise.
from zplcore import printer_io

_sc_calls = []
def _fake_send(address, port, payload, timeout, read_reply=False, cancel=None):
    _sc_calls.append((address, port, payload, timeout, read_reply))
    return b'ok: \xff\xfe'  # deliberately invalid UTF-8

_real_send = printer_io.send
printer_io.send = _fake_send
try:
    _sc_reply = printer_io.send_command('10.0.0.1', 9100, '~HS')
finally:
    printer_io.send = _real_send

check("send_command(): encodes the command as UTF-8 and asks for a reply",
      _sc_calls == [('10.0.0.1', 9100, b'~HS', 5, True)], _sc_calls)
check("send_command(): a non-UTF-8 reply decodes with replacement chars, not a raise",
      _sc_reply == 'ok: ��', _sc_reply)


# --- ^FO/^FT's third parameter: which edge the origin names -----------------
# Left is ZPL's default and the only reading this had, so a right justified
# field was drawn - and then saved - a whole field width to the right of where
# it prints. The manual's Field Interactions chart (Table 45, Normal
# Orientation) is the picture: the origin crosshair sits at the top left of a
# left justified field and at the top right of a right justified one.

_rj = zpl_parser.parse_zpl(
    "^XA^PW812^LL1218^FO300,50,1^A0N,30,30^FDhello^FS^XZ")[0]
_rje = _rj.elements[0]
check("a right justified ^FO names the field's right edge",
      _rje.x + _rje.width == 300, (_rje.x, _rje.width))
check("and the element holds its left edge like any other",
      _rje.x == 300 - _rje.width, _rje.x)
check("^FO's z comes back out of a save",
      '^FO300,50,1' in _rj.to_zpl(),
      [l for l in _rj.to_zpl().split('\n') if l.startswith('^FO')])

_lj = zpl_parser.parse_zpl(
    "^XA^PW812^LL1218^FO300,50^A0N,30,30^FDhello^FS^XZ")[0]
check("a field with no z is left justified, and writes none",
      _lj.elements[0].x == 300 and '^FO300,50\n' in _lj.to_zpl(),
      (_lj.elements[0].x, [l for l in _lj.to_zpl().split('\n') if l.startswith('^FO')]))
check("an explicit left z is trimmed, being ZPL's own default",
      '^FO300,50\n' in zpl_parser.parse_zpl(
          "^XA^PW812^LL1218^FO300,50,0^A0N,30,30^FDhello^FS^XZ")[0].to_zpl())

# ^FT justifies the same way, at the baseline rather than the top
_ft = zpl_parser.parse_zpl(
    "^XA^PW812^LL1218^FT300,150,1^A0N,30,30^FDhello^FS^XZ")[0]
check("^FT carries the same justification, and keeps its baseline",
      _ft.elements[0].x + _ft.elements[0].width == 300
      and '^FT300,150,1' in _ft.to_zpl(),
      [l for l in _ft.to_zpl().split('\n') if l.startswith('^FT')])

# ^FWr,z sets the default for every field after it. ^FW is folded into each
# field rather than written back, so an inherited justification is folded in
# the same way its orientation already is.
_fwj = zpl_parser.parse_zpl(
    "^XA^PW812^LL1218^FW,1^FO300,50^A0N,30,30^FDhello^FS^XZ")[0]
check("^FW,1 right justifies a field that names no z of its own",
      _fwj.elements[0].x + _fwj.elements[0].width == 300
      and '^FO300,50,1' in _fwj.to_zpl(),
      (_fwj.elements[0].x, [l for l in _fwj.to_zpl().split('\n') if l.startswith('^FO')]))
check("and a field's own z still wins over ^FW's",
      zpl_parser.parse_zpl(
          "^XA^PW812^LL1218^FW,1^FO300,50,0^A0N,30,30^FDhello^FS^XZ"
      )[0].elements[0].x == 300)
check("a ^FW with only an orientation leaves the justification alone",
      zpl_parser.parse_zpl(
          "^XA^PW812^LL1218^FW,1^FWR^FO300,50^A0N,30,30^FDhello^FS^XZ"
      )[0].elements[0].justify == 1)

# Every element type is placed by ^FO, so every one of them justifies
_mixed = zpl_parser.parse_zpl(
    "^XA^PW812^LL1218^FO300,50,1^GB100,40,4^FS"
    "^FO300,150,1^BY2^BCN,60^FD123^FS^XZ")[0]
check("a frame and a barcode justify too",
      [e.x + e.width for e in _mixed.elements] == [300, 300],
      [(e.element_type, e.x, e.width) for e in _mixed.elements])

# The right edge is what justification pins, so a string that grows grows
# leftward rather than dragging the ^FO the file named.
_grow = zpl_parser.parse_zpl(
    "^XA^PW812^LL1218^FO700,50,1^A0N,30,30^FDhi^FS^XZ")[0]
_ge = _grow.elements[0]
_ge.text = 'much longer now'
_grow.sync_text_width(_ge)
check("a right justified field grows leftward, keeping its right edge",
      _ge.x + _ge.width == 700, (_ge.x, _ge.width))
check("and still writes the same ^FO it came in with",
      '^FO700,50,1' in _grow.to_zpl(),
      [l for l in _grow.to_zpl().split('\n') if l.startswith('^FO')])
_ge.text = 'hi'
_grow.sync_text_width(_ge)
check("and shrinks back to the same right edge",
      _ge.x + _ge.width == 700, (_ge.x, _ge.width))

# With no room to grow into, the left edge wins: clamped at the label like any
# other move, rather than taking the ^FO negative.
_tight = zpl_parser.parse_zpl(
    "^XA^PW812^LL1218^FO100,50,1^A0N,30,30^FDhi^FS^XZ")[0]
_te = _tight.elements[0]
_te.text = 'far too long to fit to the left of that origin'
_tight.sync_text_width(_te)
check("a field with no room left of its origin clamps at the label edge",
      _te.x == 0 and '^FO' in _tight.to_zpl()
      and '^FO-' not in _tight.to_zpl(),
      (_te.x, [l for l in _tight.to_zpl().split('\n') if l.startswith('^FO')]))

# ZPL has no room for a negative ^LH, and an element pushed off the left edge
# used to produce one through the transform fit.
_off = zpl_parser.parse_zpl(
    "^XA^PW406^LL203^FO380,50,1^A0N,24,24^FDAcme Printing Co^FS^XZ")[0]
check("a field overflowing the left edge still round-trips exactly",
      '^FO380,50,1' in _off.to_zpl()
      and '^LH' not in _off.to_zpl(),
      [l for l in _off.to_zpl().split('\n') if l.startswith(('^FO', '^LH'))])

# The preview has to agree with the canvas about which edge is named.
_plain_ink = _preview_ink("^XA^PW500^LL200^FO300,50^A0N,30,30^FDhello^FS", 500, 200)
_right_ink = _preview_ink("^XA^PW500^LL200^FO300,50,1^A0N,30,30^FDhello^FS", 500, 200)
check("the preview draws a right justified field leftward of its origin",
      _right_ink[0] + _right_ink[2] <= 300 and _right_ink[2] == _plain_ink[2],
      (_plain_ink, _right_ink))
check("and ^FW's default reaches the preview too",
      _preview_ink("^XA^PW500^LL200^FW,1^FO300,50^A0N,30,30^FDhello^FS", 500, 200)
      == _right_ink)
_frame_ink = _preview_ink("^XA^PW500^LL200^FO300,50,1^GB100,40,4^FS", 500, 200)
check("a right justified frame ends exactly on its origin",
      _frame_ink == (200, 50, 100, 40), _frame_ink)

# The fixture, as a whole file: three strings of different lengths lining up
_just_raw = (FIXTURES / 'justified.zpl').read_text()
_just = zpl_parser.parse_zpl(_just_raw)[0]
check("every right justified field in the fixture shares one right edge",
      [e.x + e.width for e in _just.elements if e.justify == 1]
      == [380, 380, 380, 380],
      [(e.text, e.x + e.width) for e in _just.elements])
check("and nothing in it is reported as unsupported",
      workflow.unsupported_commands(_just_raw) == [],
      workflow.unsupported_commands(_just_raw))

# --- ^FP: which way a text field's characters run, and how far apart -------
# ^FPd,g belongs to one font field: H runs its characters left to right, V
# top to bottom, R right to left, and g puts that many extra dots between
# them. The manual's Field Interactions charts (Tables 45-48) are the picture
# of where each lands. ^A0 with no font file is laid out here with the
# fixed-width estimate - every character font_width dots wide - which keeps
# the arithmetic plain: so for this section font 0 is measured as it is where
# its stand-in is not installed. A bitmap font's own cells are the next
# section's, and the stand-in's advances are checked with the rest of font 0.
zpl_fonts._resident_cache['0'] = None

def _fp(fields):
    return zpl_parser.parse_zpl("^XA^PW812^LL1218" + fields + "^XZ")[0]

def _fp_lines(doc):
    return [l for l in doc.to_zpl().split('\n')
            if l.startswith(('^FO', '^FT', '^FP'))]

for _spelled in ('^FPV', '^FPR,10', '^FPH,5', '^FPV,3'):
    _d = _fp(f"^FO100,50{_spelled}^A0N,30,20^FDABCD^FS")
    check(f"{_spelled} comes back out of a save as it went in",
          _spelled in _d.to_zpl().split('\n'), _fp_lines(_d))
check("^FPH with no gap is ZPL's own default, and writes nothing",
      '^FP' not in _fp("^FO100,50^FPH^A0N,30,20^FDABCD^FS").to_zpl())
_plain_fp = zpl_parser.parse_zpl((FIXTURES / 'sample_203dpi.zpl').read_text())[0]
check("a label that never used ^FP gains none on a save",
      '^FP' not in _plain_fp.to_zpl()
      and all(e.direction == 'H' and e.char_gap == 0
              for e in _plain_fp.elements if e.element_type == 'text'))
check("^FP is no longer reported as something a save would drop",
      '^FP' not in workflow.unsupported_commands(
          "^XA^FO100,50^FPV,10^A0N,30,20^FDABCD^FS^XZ"))

check("^FP's letter is read in either case",
      zpl_parser.read_field_parameter('v,4') == ('V', 4))
check("a letter ZPL does not define is read as its default, H",
      zpl_parser.read_field_parameter('X') == ('H', 0))
check("the gap is held to ZPL's 0-9999",
      [zpl_parser.read_field_parameter(p)[1]
       for p in ('V,99999', 'R,-5', 'R,abc', 'R,')] == [9999, 0, 0, 0])
_two = _fp("^FO10,10^FPV,5^A0N,30,20^FDA^FS^FO10,300^A0N,30,20^FDB^FS")
check("^FP is a field's own: the next field is back to H with no gap",
      [(e.direction, e.char_gap) for e in _two.elements] == [('V', 5), ('H', 0)],
      [(e.direction, e.char_gap) for e in _two.elements])

# The box is whatever the characters fill
_spaced = _fp("^FO100,50^FPH,5^A0N,30,20^FDABCD^FS").elements[0]
check("a gap widens a row by the gaps between its characters, not after them",
      (_spaced.width, _spaced.height) == (4 * 20 + 3 * 5, 30),
      (_spaced.width, _spaced.height))
_column = _fp("^FO100,50^FPV,10^A0N,30,20^FDABCD^FS").elements[0]
check("top to bottom the box is one character wide and a row per character",
      (_column.width, _column.height) == (20, 4 * 30 + 3 * 10),
      (_column.width, _column.height))
_turned_column = _fp("^FO100,50^FPV,10^A0R,30,20^FDABCD^FS").elements[0]
check("and a quarter turn transposes it, as it does any text",
      (_turned_column.width, _turned_column.height) == (150, 20),
      (_turned_column.width, _turned_column.height))
_backward = _fp("^FO100,50^FPR,5^A0N,30,20^FDABCD^FS").elements[0]
check("right to left the box is the same row, run the other way",
      (_backward.width, _backward.height) == (95, 30),
      (_backward.width, _backward.height))
_accent = TextElement(0, 0, "éa", 30, 20, direction='V')
check("a combining mark shares its letter's cell rather than taking a row",
      textraster.clusters(_accent.text) == ["é", "a"])

# Where the ^FO or ^FT lands, from the charts. Each case is the field, the
# check that it is placed where the chart puts it, and it must also write
# back the origin it came in with.
_placements = (
    ("^FO100,50^FPV^A0N,30,20^FDABCD^FS",
     "top to bottom, ^FO names the column's top-left (Table 45)",
     lambda e: (e.x, e.y) == (100, 50)),
    ("^FO300,50,1^FPV^A0N,30,20^FDABCD^FS",
     "and right justified, its top-right",
     lambda e: (e.x + e.width, e.y) == (300, 50)),
    ("^FT100,80^FPV^A0N,30,20^FDABCD^FS",
     "and ^FT names the first character's baseline",
     lambda e: (e.x, e.y + e.typeset) == (100, 80)),
    ("^FO300,50^FPR^A0N,30,20^FDABCD^FS",
     "right to left, ^FO names the first character's top-left, at the right end",
     lambda e: (e.x + e.width - 20, e.y) == (300, 50)),
    ("^FO300,50,1^FPR^A0N,30,20^FDABCD^FS",
     "and right justified, the right edge of the last, at the left end",
     lambda e: (e.x + 20, e.y) == (300, 50)),
    ("^FT300,80^FPR^A0N,30,20^FDABCD^FS",
     "and ^FT names the first character's baseline",
     lambda e: (e.x + e.width - 20, e.y + e.typeset) == (300, 80)),
    ("^FO100,300^FPV^A0R,30,20^FDABCD^FS",
     "turned 90 degrees, a column still hangs from its ^FO (Table 46)",
     lambda e: (e.x, e.y) == (100, 300)),
    ("^FO100,300^FPR^A0R,30,20^FDABCD^FS",
     "and a reversed row, read downward, has its first character at the bottom",
     lambda e: (e.x, e.y + e.height - 20) == (100, 300)),
    ("^FO100,300^FPR^A0I,30,20^FDABCD^FS",
     "upside down, a reversed row starts at the left, where ^FO is (Table 48)",
     lambda e: (e.x, e.y) == (100, 300)),
    ("^FO100,300^FPR^A0B,30,20^FDABCD^FS",
     "and read upward, at the top, where ^FO is (Table 47)",
     lambda e: (e.x, e.y) == (100, 300)),
)
for _fields, _name, _placed in _placements:
    _d = _fp(_fields)
    _e = _d.elements[0]
    _origin = re.match(r"\^F[OT][\d,]+", _fields).group(0)
    check(_name, _placed(_e), (_e.x, _e.y, _e.width, _e.height, _e.typeset))
    check(f"  and writes {_origin} back as it came in",
          _origin in _d.to_zpl().split('\n'), _fp_lines(_d))

# The point a ^FO names stays put when the text changes, which for a reversed
# field is its first character - so it grows leftward
_grow_fp = _fp("^FO300,50^FPR^A0N,30,20^FDAB^FS")
_ge_fp = _grow_fp.elements[0]
_ge_fp.text = 'ABCDEF'
_grow_fp.sync_text_width(_ge_fp)
check("a right to left field grows leftward from its first character",
      _ge_fp.x + _ge_fp.width - 20 == 300 and '^FO300,50' in _grow_fp.to_zpl(),
      (_ge_fp.x, _ge_fp.width, _fp_lines(_grow_fp)))

# A rescale moves the point a reversed field's ^FO names with the label, as it
# does every other origin - to the dot, give or take the rounding every
# rescale has
_rescaled_fp = _fp("^FO350,50^FPR,10^A0N,30,20^FDreverse^FS"
                   "^FO100,300^FPR^A0R,30,20^FDABCD^FS")
_rescaled_fp.rescale(300 / 203)
_scaled_origins = [tuple(int(n) for n in l[3:].split(','))
                   for l in _rescaled_fp.to_zpl().split('\n') if l.startswith('^FO')]
check("a rescale carries a right to left field's ^FO with the label",
      all(abs(got - want) <= 1 for pair in zip(_scaled_origins,
                                               [(517, 74), (148, 443)])
          for got, want in zip(*pair)),
      _scaled_origins)

# Switching direction is an edit of the field, not a move: the box stays where
# the user sees it, and the ^FO follows it
_switch = Document(812, 1218, dpi=203)
_sw = _switch.add_text_element('ABCD')
_sw.x, _sw.y, _sw.font_height, _sw.font_width = 100, 50, 30, 20
_sw.font_code = '0'
_switch.sync_text_width(_sw)
_sw.direction = 'R'
_switch.sync_text_width(_sw)
check("turning a field right to left leaves its box where it was",
      (_sw.x, _sw.y, _sw.width) == (100, 50, 80), (_sw.x, _sw.y, _sw.width))
check("and writes the ^FO its first character now needs",
      '^FO160,50' in _switch.to_zpl(), _fp_lines(_switch))
_sw.direction = 'V'
_switch.sync_text_width(_sw)
check("and a column hangs from the same top-left",
      (_sw.x, _sw.y, _sw.width, _sw.height) == (100, 50, 20, 120),
      (_sw.x, _sw.y, _sw.width, _sw.height))

# A drag asks a column for a row height, not a font as tall as the column
_sw.char_gap = 10
_switch.sync_text_width(_sw)
geometry.resize_by_handle(_switch, _sw, 'bm', 0, 40)
check("dragging a column taller makes each row taller, gaps unchanged",
      (_sw.font_height, _sw.char_gap, _sw.height) == (40, 10, 4 * 40 + 30),
      (_sw.font_height, _sw.char_gap, _sw.height))
_narrow = _switch.add_text_element('ABCD')
_narrow.font_height, _narrow.font_width = 13, 7
_narrow.font_code = '0'
_narrow.direction, _narrow.char_gap = 'V', 4
_switch.sync_text_width(_narrow)
geometry.resize_by_handle(_switch, _narrow, 'bm', 0, 40)
check("and leaves the font width alone, however narrow the column",
      (_narrow.font_width, _narrow.width, _narrow.font_height) == (7, 7, 23),
      (_narrow.font_width, _narrow.width, _narrow.font_height))
_switch.elements.remove(_narrow)
_switch.rescale(2)
check("and a rescale scales the gap with the font",
      (_sw.font_height, _sw.char_gap) == (80, 20), (_sw.font_height, _sw.char_gap))

# The gap goes into a block's wrap as well
# "AB CD EF" is 160 dots on one line; 20 more between each of its eight
# characters makes it 300, and "AB CD" alone 180
_unwrapped = _fp("^FO50,50^FB200,4,0,L^A0N,30,20^FDAB CD EF^FS").elements[0]
_wrapped = _fp("^FO50,50^FB200,4,0,L^FPH,20^A0N,30,20^FDAB CD EF^FS").elements[0]
check("a block wraps a gapped line sooner",
      (_unwrapped.height, _wrapped.height) == (30, 2 * 30),
      (_unwrapped.height, _wrapped.height))
_blocked_v = _fp("^FO50,50^FB200,4,0,L^FPV^A0N,30,20^FDAB CD^FS")
check("a direction a block leaves undefined is carried, and placed as H",
      '^FPV' in _blocked_v.to_zpl() and _blocked_v.elements[0].x == 50,
      _fp_lines(_blocked_v))

# The Qt editor
_qd = Document(812, 1218, dpi=203)
_qe = _qd.add_text_element('ABCD')

def _fill_direction(dialog):
    dialog.findChild(QComboBox, 'direction').setCurrentIndex(1)   # Top to bottom
    dialog.findChild(QSpinBox, 'char_gap').setValue(7)

check("the text dialog sets ^FP's direction and gap",
      _drive_text_dialog(_qe, _qd, _fill_direction)
      and (_qe.direction, _qe.char_gap) == ('V', 7),
      (_qe.direction, _qe.char_gap))
check("and the box follows them",
      _qe.height == 4 * _qe.font_height + 3 * 7 and '^FPV,7' in _qe.to_zpl(),
      (_qe.width, _qe.height))
_qdlg = qt_dialogs.edit_text_dialog(None, _qe, _qd)
_qdir = _qdlg.findChild(QComboBox, 'direction')
_was_enabled = _qdir.isEnabled()
_qdlg.findChild(QCheckBox, 'wrap').setChecked(True)
check("the direction is offered only while the text is not a block",
      _was_enabled and not _qdir.isEnabled())
_qdlg.reject()

# The preview draws what the canvas boxes. The preview has no font file of the
# field's own either, so it lays out by the same fixed-width estimate.
for _fields in ("^FO100,50^FPV,10^A0N,30,24^FDABCD^FS",
                "^FO300,50^FPR,10^A0N,30,24^FDABCD^FS",
                "^FO150,250^FPR^A0R,30,24^FDABCD^FS",
                "^FO100,50^FPH,8^A0N,30,24^FDABCD^FS"):
    _e = _fp(_fields).elements[0]
    _ink = _preview_ink(f"^XA^PW400^LL400{_fields}^XZ", 400, 400)
    _how = re.search(r"\^FP[^^]*\^A0(.)", _fields)
    check(f"the preview draws {_how.group(0)[:-4]} turned {_how.group(1)} "
          f"inside the box the canvas shows",
          _inside(_ink, _e), (_ink, (_e.x, _e.y, _e.width, _e.height)))
_col_ink = _preview_ink("^XA^PW400^LL400^FO100,50^FPV,10^A0N,30,24^FDABCD^FS^XZ",
                        400, 400)
check("and a column's ink runs down every row",
      _col_ink is not None and _col_ink[3] > 3 * (30 + 10), _col_ink)
_back_ink = _preview_ink("^XA^PW400^LL400^FO300,50^FPR,10^A0N,30,24^FDABCD^FS^XZ",
                         400, 400)
check("and a reversed row runs left from its first character",
      _back_ink is not None and _back_ink[0] < 300 - 2 * 24
      and 300 < _back_ink[0] + _back_ink[2] <= 300 + 24 + 2, _back_ink)
_rev_img = ZPLRenderer(400, 200).render(
    "^XA^PW400^LL200^FO50,50^GB200,60,60^FS"
    "^FO60,60^FR^FPH,6^A0N,30,24^FDAB^FS^XZ").convert('L')
check("a reversed ^FP field inverts under its glyphs only",
      any(_rev_img.getpixel((x, y)) > 128 for x in range(60, 130)
          for y in range(60, 90))
      and _rev_img.getpixel((245, 105)) < 128)

# The Qt canvas, with a font file and without
_fw = qt_main.ZPLDesignerWindow()
_fw.unsaved_changes = False
_fw.on_new()
_fw.document.set_label_size(812, 1218)
_fw.canvas.set_zoom(1.0)
for _path in (FONT, None):
    _fe = _fw.document.add_text_element('ABCD')
    _fe.x, _fe.y, _fe.font_height, _fe.font_width = 60, 60, 30, 24
    _fe.font_path = _path
    _fe.direction, _fe.char_gap = 'V', 10
    _fw.document.sync_text_width(_fe)
    _surface = QImage(812, 1218, QImage.Format_ARGB32); _surface.fill(Qt.white)
    _fw.canvas.render(_surface)
    _box = _ink_box(_surface, _fe)
    check(f"the Qt canvas draws a column down its box "
          f"({'with' if _path else 'without'} a font file)",
          _box is not None and _box[3] - _box[1] > 3 * (30 + 10), _box)
    _fw.document.elements.remove(_fe)

zpl_fonts._resident_cache['0'] = STANDIN

# --- the resident bitmap fonts print in whole-number magnifications -------
# A bitmap font can only be magnified by whole numbers, 1 to 10 on each axis,
# with its own fixed gap after each character. The designer drew ^AFN,36,20
# as 20 dots a character at 36 tall; a 203 dpi printer printed it as 26 x 26
# cells 32 dots apart - left to right, right to left and top to bottom alike -
# which is what these numbers are taken from.

check("^AFN,36,20 is font F once down and twice across, with twice its gap",
      tuple(zpl_fonts.bitmap_cell('F', 36, 20, 203)) == (26, 26, 6, 21),
      zpl_fonts.bitmap_cell('F', 36, 20, 203))
check("^AF,52 and ^AF,54 are the same 52, as the manual's own ^AD example is",
      zpl_fonts.bitmap_cell('F', 52, 13).height
      == zpl_fonts.bitmap_cell('F', 54, 13).height == 52)
check("magnification stops at 10 and never falls below 1",
      tuple(zpl_fonts.bitmap_cell('F', 999, 1)) == (260, 13, 3, 210),
      zpl_fonts.bitmap_cell('F', 999, 1))
check("the scalable font 0 and a downloaded font are not bitmap fonts",
      zpl_fonts.bitmap_cell('0', 36, 20) is None
      and zpl_fonts.bitmap_cell('@', 36, 20) is None)
check("E has a bigger cell at 300 dpi",
      zpl_fonts.bitmap_cell('E', 28, 15, 203)[:2] == (28, 15)
      and zpl_fonts.bitmap_cell('E', 42, 20, 300)[:2] == (42, 20))

# The calibration label, as printed and scanned
_scanned = zpl_parser.parse_zpl(
    "^XA^PW812\n"
    "^FO40,40^AFN,36,20^FDHHHHHHHHHH^FS\n"
    "^FO700,120^FPR^AFN,36,20^FDHHHHHHHHHH^FS\n"
    "^FO40,220^FPV^AFN,36,20^FDHHHHH^FS\n"
    "^FO200,220^FPV^A0N,36,20^FDHHHHH^FS\n^XZ")[0]
_row, _back, _col, _col0 = _scanned.elements
check("a row of ^AFN,36,20 is ten 26-dot cells with 6 dots between",
      (_row.x, _row.y, _row.width, _row.height) == (40, 40, 10 * 26 + 9 * 6, 26),
      (_row.x, _row.y, _row.width, _row.height))
check("right to left is the same row, its first cell starting at the ^FO",
      (_back.width, _back.x + _back.width - 26, _back.y) == (314, 700, 120),
      (_back.x, _back.y, _back.width))
check("top to bottom the rows are the cell's height apart, with no gap",
      (_col.x, _col.y, _col.width, _col.height) == (40, 220, 26, 5 * 26),
      (_col.x, _col.y, _col.width, _col.height))
check("and the scalable font 0 is a column as wide as its H, not a cell",
      (_col0.width, _col0.height)
      == (round(textraster.measurer(STANDIN, 36, 20)[0]('H')), 5 * 36),
      (_col0.width, _col0.height))
check("the sizes the file gave are written back, not the ones that print",
      _scanned.to_zpl().count('^AFN,36,20') == 3, _font_written(_scanned))
_ft_bitmap = zpl_parser.parse_zpl("^XA^FT40,80^AFN,36,20^FDH^FS^XZ")[0].elements[0]
check("^FT names font F's own baseline, 21 dots down its cell",
      (_ft_bitmap.typeset, _ft_bitmap.y) == (21, 59),
      (_ft_bitmap.typeset, _ft_bitmap.y))
_with_file = TextElement(0, 0, 'HHHH', 36, 20)
_with_file.font_path = FONT
check("a field with a font file writes ^A@ and is not a bitmap font",
      _with_file.cell().baseline is None
      and _with_file.cell()[:2] == (36, 20))

# New text starts at a size font F prints exactly
_new_doc = Document(812, 1218, dpi=203)
_new_text = _new_doc.add_text_element('New Text')
check("a new text field is ^AFN,26,26, and its box is what that prints",
      '^AFN,26,26' in _new_text.to_zpl()
      and (_new_text.width, _new_text.height) == (8 * 26 + 7 * 6, 26),
      (_new_text.width, _new_text.height))

# A drag steps through whole magnifications
geometry.resize_by_handle(_new_doc, _new_text, 'bm', 0, 30)
check("dragging a bitmap field taller snaps it to twice the cell",
      _new_text.height == 52, (_new_text.font_height, _new_text.height))
geometry.resize_by_handle(_new_doc, _new_text, 'mr', -120, 0)
check("and narrower, to a whole number of the base width",
      _new_text.font_width % 13 == 0 and _new_text.width == 8 * 13 + 7 * 3,
      (_new_text.font_width, _new_text.width))

# A bitmap cell belongs to the resolution, so settling a design on another
# printer re-sizes it even when no dot is rescaled
_e_doc = Document(812, 1218, dpi=203)
_e_el = _e_doc.add_text_element('E')
_e_el.font_code, _e_el.font_height, _e_el.font_width = 'E', 42, 20
_e_doc.sync_text_width(_e_el)
_e_before = _e_el.height
workflow.reconcile_dpi(_e_doc, 300, lambda *a: 'keep')
# 42 is one and a half of E's 28 at 203 dpi, which rounds up to two; at 300
# dpi it is E's own 42
check("keeping the dots on a 300 dpi printer takes E's bigger cell",
      (_e_before, _e_el.height) == (56, 42), (_e_before, _e_el.height))

# The preview and the canvas draw the cells the box holds
for _el, _name in ((_row, "a row"), (_col, "a column")):
    _box_ink = _preview_ink(
        "^XA^PW812^LL400" + ("^FO40,40^AFN,36,20^FDHHHHHHHHHH^FS" if _el is _row
                             else "^FO40,220^FPV^AFN,36,20^FDHHHHH^FS") + "^XZ",
        812, 400)
    check(f"the preview draws {_name} of font F inside its box, filling it",
          _inside(_box_ink, _el)
          and _box_ink[2] >= _el.width - 12 and _box_ink[3] >= _el.height - 12,
          (_box_ink, (_el.x, _el.y, _el.width, _el.height)))
_bw = qt_main.ZPLDesignerWindow()
_bw.unsaved_changes = False
_bw.on_new()
_bw.document.set_label_size(812, 1218)
_bw.canvas.set_zoom(1.0)
_brow = _bw.document.add_text_element('HHHHHHHHHH')
_brow.x, _brow.y, _brow.font_height, _brow.font_width = 40, 40, 36, 20
_bw.document.sync_text_width(_brow)
_surface = QImage(812, 1218, QImage.Format_ARGB32); _surface.fill(Qt.white)
_bw.canvas.render(_surface)
_bbox = _ink_box(_surface, _brow)
check("the Qt canvas draws a row of font F across the whole of its box",
      _bbox is not None and _bbox[2] - _bbox[0] >= _brow.width - 12,
      (_bbox, _brow.width))

# --- font 0 is measured and drawn in its stand-in ---------------------------
# Font 0 is CG Triumvirate Bold Condensed, and len x font_width drew it two to
# four times too wide. Printed on a 203 dpi printer at ^A0N,40,40 and scanned,
# these rows' ink measured as below, in dots; Nimbus Sans Narrow Bold, opened
# at font 0's cap height, stands in for it.

_printed_rows = {'IIIIIIIIII': 104.2, 'WWWWWWWWWW': 328.9,
                 '0123456789': 185.4, 'abcdefghij': 163.1, 'HHHH': 90.0}

def _font0(fields, width=900, height=300):
    page = f"^XA^PW{width}^LL{height}{fields}^XZ"
    return (zpl_parser.parse_zpl(page)[0].elements[0],
            _preview_ink(page, width, height))

if not STANDIN:
    print("SKIPPED: font 0's stand-in is not installed "
          "(apt install fonts-urw-base35) - nothing to check it against")
else:
    check("the stand-in is Nimbus Sans Narrow Bold's OpenType file",
          STANDIN.endswith('.otf')
          and zpl_fonts._name_and_style(STANDIN) == ('Nimbus Sans Narrow',
                                                     'Bold'), STANDIN)
    check("and it stands in for font 0 alone: not a bitmap font, not P-V, "
          "not a font ^A@ names",
          [zpl_fonts.resident_face(c) for c in 'FAP@'] == [None] * 4)

    for _row, _printed in _printed_rows.items():
        _el, _ink = _font0(f"^FO50,50^A0N,40,40^FD{_row}^FS")
        _off = abs(_ink[2] - _printed) / _printed
        check(f"the preview draws {_row} within "
              f"{15 if _row[0] == 'I' else 5}% of the printed width",
              _off <= (0.15 if _row[0] == 'I' else 0.05),
              (_ink[2], _printed, f"{_off:.1%}"))
        check(f"  inside the box the canvas draws for it",
              _inside(_ink, _el, slack=0), (_ink, (_el.x, _el.y, _el.width)))
    _el, _ink = _font0("^FO50,50^A0N,89,89^FDHHHH^FS")
    check("^A0N,89,89 HHHH is drawn the 203.7 x 65.0 it printed, within 3%",
          abs(_ink[2] - 203.7) <= 0.03 * 203.7 and abs(_ink[3] - 65) <= 2,
          _ink)
    _el, _ink = _font0("^FO50,50^A0N,40,40^FDHHHH^FS")
    check("and at 40 its H is the 29.8 dots tall it printed",
          abs(_ink[3] - 29.8) <= 1, _ink)
    _el, _ink = _font0("^FO50,20^A0N,200,200^FDH^FS")
    check("which is 0.745 of the height at any size, not the stand-in's 0.718",
          abs(_ink[3] - 0.745 * 200) <= 1, _ink)

    # ^FT names the baseline, which for font 0 Table 33 puts 3/4 of the way
    # down the cell - not at the stand-in's own ascent, 0.718 of it
    _el, _ink = _font0("^FT50,200^A0N,60,60^FDHH^FS", 600, 400)
    check("^FT puts a font 0 field's baseline 3/4 of its height down",
          (_el.typeset, _el.y) == (45, 155), (_el.typeset, _el.y))
    check("and the preview stands its H on the y ^FT named",
          _ink[1] + _ink[3] == 200, _ink)

    # One face for every path: each of these reads it from TextElement.face
    _gapped, _ = _font0("^FO50,50^FPH,5^A0N,30,20^FDABCD^FS")
    _each = textraster.measurer(STANDIN, 30, 20)[0]
    check("a gapped font 0 row is its characters' advances and the gaps",
          _gapped.width == round(sum(_each(c) for c in 'ABCD') + 3 * 5),
          _gapped.width)
    _column, _ = _font0("^FO50,50^FPV^A0N,30,20^FDAWI^FS")
    check("a font 0 column is as wide as its widest character",
          _column.width == round(_each('W')), _column.width)
    _wrapped, _ink = _font0(
        "^FO50,160^FB300,3,0,L^A0N,40,40^FDwrap this text across three "
        "lines^FS", 600, 400)
    check("a font 0 block wraps where the stand-in's widths put the breaks",
          _wrapped.height == 2 * 40, _wrapped.height)
    check("and the preview wraps it into the same two lines",
          _inside(_ink, _wrapped, slack=0) and _ink[3] > 40,
          (_ink, (_wrapped.y, _wrapped.height)))
    _fitted = TextElement(0, 0, 'Fit me', 40, 40, font_code='0')
    _fitted.font_width = _fitted.font_width_for(300)
    check("font_width_for inverts a font 0 field's width",
          abs(_fitted.printed_width() - 300) <= 2, _fitted.printed_width())
    _unwrapped = TextElement(0, 0, 'Fit me', 40, 40, font_code='0')
    check("and switching wrapping on keeps it on one line",
          abs(_unwrapped.default_block().width
              - _unwrapped.printed_width()) <= 1,
          (_unwrapped.default_block().width, _unwrapped.printed_width()))
    check("the preview draws font 0 in the stand-in, not DejaVu Sans",
          ZPLRenderer(10, 10)._text_face(TextElement(font_code='0'))
          == STANDIN)

    # The canvas's H stands where the preview's does, within the canvas's
    # own two-dot margin: the toy-font fallback it used to take instead puts
    # its baseline just above the foot of the cell, 8 dots lower.
    _fw = qt_main.ZPLDesignerWindow()
    _fw.unsaved_changes = False
    _fw.on_new()
    _fw.document.set_label_size(600, 300)
    _fw.canvas.set_zoom(1.0)
    _f0 = _fw.document.add_text_element('HHHH')
    _f0.x, _f0.y, _f0.font_height, _f0.font_width = 50, 50, 40, 40
    _f0.font_code = '0'
    _fw.document.sync_text_width(_f0)
    _fw.document.clear_selection()
    _surface = QImage(600, 300, QImage.Format_ARGB32); _surface.fill(Qt.white)
    _fw.canvas.render(_surface)
    _qbox = _ink_box(_surface, _f0)
    _pink = _preview_ink("^XA^PW600^LL300^FO50,50^A0N,40,40^FDHHHH^FS^XZ",
                         600, 300)
    check("the Qt canvas draws font 0 in the stand-in, standing where the "
          "preview's does",
          _qbox is not None
          and abs(_qbox[3] + 1 - (_pink[1] + _pink[3])) <= 2, (_qbox, _pink))

    # Font 0 is in the printer: its stand-in is never uploaded, never
    # reported missing, and never written as a font of the field's own
    _resident = zpl_parser.parse_zpl(
        "^XA^FO50,50^A0N,40,40^FDresident^FS^XZ")[0]
    check("a font 0 field has no font of its own, and the label none to send",
          _resident.elements[0].font_path is None
          and _resident.font_sources() == {}
          and '^A0N,40,40' in _resident.to_zpl(),
          (_resident.elements[0].font_path, _resident.font_sources()))
    _resident.set_font(FONT, 'DejaVu Sans', 'DEJAVUSA')
    check("with a label font, the field is written ^A@ and measured in that",
          _resident.elements[0].face(_resident.font_path) == FONT
          and '^A@N,40,40,E:DEJAVUSA.TTF' in _resident.to_zpl(),
          _resident.to_zpl())

# A line break in ^FD is not a character: the printer discards it, and a face
# will not measure it
_broken = zpl_parser.parse_zpl(
    (FIXTURES / 'default_font.zpl').read_text())[0].elements[0]
check("a field whose ^FD ends at a line break loads, measured without it",
      _broken.text.endswith('\n') and _broken.width
      == TextElement(0, 0, _broken.text.rstrip('\n'), 36, 36,
                     font_code='0').printed_width()
      if STANDIN else _broken.width == len(_broken.text) * 36,
      (repr(_broken.text), _broken.width))

# Where the stand-in is not installed, nothing about font 0 changes
zpl_fonts._resident_cache['0'] = None
_without = zpl_parser.parse_zpl(
    "^XA^FT50,200^A0N,40,40^FDHHHH^FS^XZ")[0].elements[0]
check("without the stand-in, font 0 is len x w with a baseline 4/5 down",
      (_without.width, _without.typeset) == (4 * 40, 32),
      (_without.width, _without.typeset))
zpl_fonts._resident_cache['0'] = STANDIN

# --- printer_status: what the printer reports about itself -------------------
# Every fixture below is either the ZPL manual's own worked example or a reply
# captured from real hardware, never one composed to match the parser.
from zplcore import printer_status as zpl_status

# ~HQES, from the manual: group 1's nibble 1 holds Media Out (1) | Head Open
# (4) = 5, and the warnings' nibble 1 holds Clean Printhead (2).
_ES_TEXT = ("PRINTER STATUS\r\n"
            "    ERRORS:              1 00000000 00000005\r\n"
            "    WARNINGS:            1 00000000 00000002\r\n")
_es = zpl_status.parse_error_status(_ES_TEXT)
check("parse_error_status(): ~HQES nibbles become the errors they stand for",
      _es.errors == {'Media out', 'Head open'}, _es.errors)
check("parse_error_status(): and the warning nibbles likewise",
      _es.warnings == {'Clean printhead'}, _es.warnings)
# The manual's zpl.system_status example: leading flag 1 = paused, error
# group 1 = 4 = head open.
_es_csv = zpl_status.parse_error_status('1,1,00000000,00000004,0,00000000,00000000')
check("parse_error_status(): the zpl.system_status CSV parses to the same flags",
      _es_csv == (True, {'Head open'}, set()), _es_csv)
check("parse_error_status(): a clean printer is empty sets, not None",
      zpl_status.parse_error_status('0,0,00000000,00000000,0,00000000,00000000')
      == (False, set(), set()),
      zpl_status.parse_error_status('0,0,00000000,00000000,0,00000000,00000000'))
# '?' is how a printer refuses an attribute it does not have - the signal to
# fall back to ~HQES, so it must not read as "no faults".
check("parse_error_status(): '?' is None, not a clean bill of health",
      zpl_status.parse_error_status('?') is None,
      zpl_status.parse_error_status('?'))
check("parse_error_status(): STX/ETX framing is stripped, not parsed",
      zpl_status.parse_error_status('\x02' + _ES_TEXT + '\x03') == _es)
# A nibble the manual leaves unassigned names nothing rather than a number.
check("parse_error_status(): an unassigned bit is ignored, not invented",
      zpl_status.parse_error_status('0,1,00000000,00000000,0,00000000,00000000')
      == (False, set(), set()))

# ~HS, in the three-string shape the manual documents. String 1 here says
# paper out, not paused, a 1218-dot label, two formats queued, partial format
# in progress; string 2 says ribbon out, thermal transfer, tear-off mode,
# eighteen labels left in the batch, three images stored.
_HS_REPLY = ('\x02030,1,0,1218,002,0,0,1,000,0,0,0\x03\r\n'
             '\x02001,0,0,1,1,2,6,0,00000018,1,003\x03\r\n'
             '\x020000,0\x03\r\n')
_hs = zpl_status.parse_host_status(_HS_REPLY)
check("parse_host_status(): string 1's flags and counts land in their fields",
      (_hs.paper_out, _hs.paused, _hs.label_length, _hs.formats_in_buffer,
       _hs.partial_format) == (True, False, 1218, 2, True), _hs)
check("parse_host_status(): string 2's too",
      (_hs.ribbon_out, _hs.thermal_transfer, _hs.print_mode,
       _hs.labels_remaining, _hs.images_stored)
      == (True, True, 'Tear-off', 18, 3), _hs)
check("parse_host_status(): die-cut vs continuous comes out of packed mmm",
      _hs.continuous_media is False, _hs.continuous_media)
check("parse_host_status(): m7 set reads as continuous media",
      zpl_status.parse_host_status(
          _HS_REPLY.replace('\x02001,', '\x02129,')).continuous_media is True)
# A reply cut short by a read timeout must not become a record of zeros
# indistinguishable from a healthy printer.
check("parse_host_status(): a truncated reply is None, not zeros",
      zpl_status.parse_host_status(
          '\x02030,1,0,1218,002,0,0,1,000,0,0,0\x03\r\n\x020000,0\x03') is None)
check("parse_host_status(): nothing at all is None",
      zpl_status.parse_host_status('') is None)

# ~HI, the same reply fonts.query_printer_dpi reads for its own purpose.
_hi = zpl_status.parse_host_identification('ZT230,V53.17.1Z,8,4096KB,X')
check("parse_host_identification(): dots/mm maps through the fonts table",
      _hi.dpi == zpl_fonts.DOTS_PER_MM_TO_DPI[8] == 203, _hi)
check("parse_host_identification(): memory is reported in the KB ~HI sends",
      _hi.memory_kb == 4096, _hi)
check("parse_host_identification(): an unknown dots/mm leaves dpi None",
      zpl_status.parse_host_identification('ZT230,V1,7,4096KB,X').dpi is None)

# ~HM, the manual's own example.
check("parse_ram_status(): total, maximum and free, in kilobytes",
      zpl_status.parse_ram_status('1024,0780,0780') == (1024, 780, 780))
check("parse_ram_status(): a reply that is not three numbers is None",
      zpl_status.parse_ram_status('1024') is None)

# The ^HW footer all three existing parsers throw away. _REAL_HW above is a
# reply captured from hardware, so this is checked against the real shape as
# well as the manual's formal one.
check("parse_free_space(): the footer of a real captured ^HW reply",
      zpl_status.parse_free_space(_REAL_HW.decode())
      == (66119680, 'E: ONBOARD FLASH'),
      zpl_status.parse_free_space(_REAL_HW.decode()))
check("parse_free_space(): and the manual's bare formal shape",
      zpl_status.parse_free_space('-794292 bytes free R:RAM')
      == (794292, 'R:RAM'))
check("parse_free_space(): a listing with no footer is None",
      zpl_status.parse_free_space('- DIR R:*.*\r\n') is None)
check("_object_count(): counts the listing's own entry lines",
      zpl_status._object_count(_REAL_HW.decode()) == 3,
      zpl_status._object_count(_REAL_HW.decode()))

# ~HQOD, in both unit systems the manual shows. The unit is the printer's,
# never converted.
check("parse_meters(): the odometer's labelled lines, units as sent",
      zpl_status.parse_meters(
          'PRINT METERS\r\n    TOTAL NONRESETTABLE:    8560 "\r\n'
          '    USER RESETTABLE CNTR1:     9 "\r\n')
      == [('Total Nonresettable', '8560 "'), ('User Resettable Cntr1', '9 "')])
check("parse_meters(): centimetres are left as centimetres",
      zpl_status.parse_meters('PRINT METERS\r\n  TOTAL NONRESETTABLE: 21744 cm\r\n')
      == [('Total Nonresettable', '21744 cm')])
# parse_getvar: the manual shows every getvar reply quoted, and documents '?'
# as the answer for a setting that does not exist or is not configured.
check("parse_getvar(): the quotes the manual's own examples show are stripped",
      zpl_status.parse_getvar('"00 days 02 hours 45 mins 30 secs"')
      == '00 days 02 hours 45 mins 30 secs')
check("parse_getvar(): a two-unit odometer value survives intact",
      zpl_status.parse_getvar('"8560 INCHES, 21744 CENTIMETERS"')
      == '8560 INCHES, 21744 CENTIMETERS')
# This is what makes an attribute cheap to try: a printer without it refuses
# at once, where an unsupported ~HQ sub-command is ignored and costs the whole
# read timeout. A '?' read as a value would put nonsense on the panel.
check("parse_getvar(): '?' is a refusal, not a value",
      zpl_status.parse_getvar('?') is None)
# A real printer answers memory.flash_free with its own wording rather than
# the bare number the manual's format line implies - captured from hardware as
# "66369536 Bytes Free". Left verbatim it collides with the sentence it goes
# in, reading "66369536 Bytes Free free of ...", so only the count is used.
# _group_digits(): the odometer readings arrive as the printer worded them and
# are long enough to be hard to read at a glance, which is the whole reason the
# panel shows them. Only the digits are touched - never the unit, its spelling
# or the order of the two halves.
check("_distance(): both counts of a two-unit odometer reply are grouped",
      zpl_status._distance('8560 INCHES, 21744 CENTIMETERS')
      == '8,560 INCHES; 21,744 CENTIMETERS',
      zpl_status._distance('8560 INCHES, 21744 CENTIMETERS'))
# Once the numbers carry commas, the printer's own comma between the two halves
# reads as though it might be one list of four numbers - so the halves are
# separated by a semicolon, which no grouped number contains.
check("_distance(): the two halves are separated by a semicolon, not a comma",
      zpl_status._distance('8560 INCHES, 21744 CENTIMETERS').count(';') == 1
      and zpl_status._distance('8560 INCHES, 21744 CENTIMETERS')
      .split(';')[0].count(',') == 1)
check("_distance(): a grouping separator is never mistaken for the unit one",
      zpl_status._distance('1234567 INCHES, 3134234 CENTIMETERS')
      == '1,234,567 INCHES; 3,134,234 CENTIMETERS',
      zpl_status._distance('1234567 INCHES, 3134234 CENTIMETERS'))
check("_distance(): a ~HQOD figure in the printer's own single unit too",
      (zpl_status._distance('8560 \"'), zpl_status._distance('21744 cm'))
      == ('8,560 \"', '21,744 cm'))
# Three digits gain nothing from a separator, and the threshold is what keeps a
# version string from being punctuated into nonsense.
check("_group_digits(): short counts and version strings are left alone",
      (zpl_status._group_digits('257 \"'), zpl_status._group_digits('999'),
       zpl_status._group_digits('V53.17.7Z'))
      == ('257 \"', '999', 'V53.17.7Z'))
check("_group_digits(): a decimal's fractional half is not grouped",
      zpl_status._group_digits('1234.5678') == '1,234.5678',
      zpl_status._group_digits('1234.5678'))

check("_bytes(): the printer's own 'Bytes Free' wording is not repeated",
      zpl_status._bytes('66369536 Bytes Free') == '66,369,536 bytes',
      zpl_status._bytes('66369536 Bytes Free'))
check("_bytes(): a bare count and a 'Bytes' suffix both group the same way",
      (zpl_status._bytes('66119680'), zpl_status._bytes('67108864 Bytes'))
      == ('66,119,680 bytes', '67,108,864 bytes'))
# A figure already stated in some other unit must not be relabelled as bytes.
check("_bytes(): a value in another unit is left exactly as it came",
      (zpl_status._bytes('8 MB'), zpl_status._bytes('512 KB'),
       zpl_status._bytes('unknown')) == ('8 MB', '512 KB', 'unknown'))

# silence_note(): ~HS is silent in five states, but of those five a full
# rewinder has no flag in either table - so the "which one" claim is only made
# when a flag was actually raised.
check("silence_note(): claims the flags say which only when one was raised",
      'the faults above say which' in zpl_status.silence_note(True)
      and 'the faults above say which' not in zpl_status.silence_note(False))
check("silence_note(): with no flag raised it names the rewinder, the one "
      "state with no flag of its own",
      'rewinder' in zpl_status.silence_note(False).rsplit('.', 2)[-2])
check("silence_note(): both wordings name all five silencing states",
      all(state in zpl_status.silence_note(flag)
          for flag in (True, False)
          for state in zpl_status.HOST_STATUS_SILENT_STATES))

# --- printer_status queries: what is asked, in what order, and when it gives up
# Every fake below has printer_io.send's exact signature, positional timeout
# included, and is restored in a finally - a fake left installed would corrupt
# every check after it in this script.

_HS_OK = (b'\x02030,0,0,1218,000,0,0,0,000,0,0,0\x03\r\n'
          b'\x02001,0,0,0,1,2,6,0,00000000,1,000\x03\r\n'
          b'\x020000,0\x03\r\n')
_SGD_CLEAN = b'"0,0,00000000,00000000,0,00000000,00000000"'


def _status_fake(replies, log):
    """A printer that answers whatever `replies` maps its payload to."""
    def send(address, port, payload, timeout, read_reply=False, cancel=None):
        text = payload.decode('latin-1')
        log.append(text)
        for needle, reply in replies.items():
            if needle in text:
                return reply
        return b''
    return send


# The order is the point: the fault flags are asked BEFORE ~HS, because ~HS is
# documented as answering nothing at all in five states and those are the very
# ones worth reporting. Asked the other way round, a printer with its head open
# gives a blank panel and no explanation.
_order_log = []
_status_real_send = zpl_printer_io.send
zpl_printer_io.send = _status_fake(
    {'zpl.system_status': _SGD_CLEAN, '~HS': _HS_OK, '~HM': b'1024,0780,0780'},
    _order_log)
try:
    _act = zpl_status.query_printer_activity('h', 9100)
finally:
    zpl_printer_io.send = _status_real_send
check("query_printer_activity(): asks the fault flags before ~HS",
      _order_log.index('~HS') > 0 and 'zpl.system_status' in _order_log[0],
      _order_log)
check("query_printer_activity(): a healthy printer reports no faults and "
      "nothing unanswered",
      _act.unanswered == [] and _act.sections[0].readings[0].value
      == 'none reported', _act)
check("query_printer_activity(): every section came back",
      [s.title for s in _act.sections] == ['Faults', 'Work', 'Memory'],
      [s.title for s in _act.sections])

# The headline case: the flags answer, ~HS does not. The report must still name
# the fault, mark ~HS unanswered, and carry the explaining note - not read as a
# dead printer.
_silent_log = []
zpl_printer_io.send = _status_fake(
    {'zpl.system_status': b'"0,1,00000000,00000004,0,00000000,00000000"',
     '~HM': b'1024,0780,0780'}, _silent_log)   # ~HS falls through to b''
try:
    _silent_act = zpl_status.query_printer_activity('h', 9100)
finally:
    zpl_printer_io.send = _status_real_send
check("query_printer_activity(): ~HS going silent is reported, not fatal",
      _silent_act is not None and _silent_act.unanswered == ['~HS'],
      _silent_act and _silent_act.unanswered)
check("query_printer_activity(): and the fault it was silent about is named",
      _silent_act.sections[0].readings[0].label == 'Head open'
      and _silent_act.sections[0].readings[0].level == zpl_status.ERROR,
      _silent_act.sections[0].readings)
check("query_printer_activity(): with the note explaining why it said nothing",
      'does not answer one when' in _silent_act.sections[0].note,
      _silent_act.sections[0].note)

# An old printer refuses the SGD attribute with '?' and answers ~HQES instead.
# The fallback is not reported as a fault: what was asked for did arrive.
_fallback_log = []
zpl_printer_io.send = _status_fake(
    {'zpl.system_status': b'?',
     '~HQES': (b'\x02PRINTER STATUS\r\n    ERRORS:   1 00000000 00000002\r\n'
               b'    WARNINGS: 0 00000000 00000000\r\n\x03'),
     '~HS': _HS_OK, '~HM': b'1024,0780,0780'}, _fallback_log)
try:
    _fb = zpl_status.query_printer_activity('h', 9100)
finally:
    zpl_printer_io.send = _status_real_send
check("query_printer_activity(): a refused SGD attribute falls back to ~HQES",
      '~HQES' in _fallback_log and _fb.sections[0].readings[0].label
      == 'Ribbon out', (_fallback_log, _fb.sections[0].readings))
check("query_printer_activity(): and the refused half is not called unanswered",
      _fb.unanswered == [], _fb.unanswered)

# Reachability, judged the way all three device queries judge it.
_attempts = []
def _dead_status_host(address, port, payload, timeout, read_reply=False, cancel=None):
    _attempts.append(payload)
    raise OSError('refused')
zpl_printer_io.send = _dead_status_host
try:
    _dead_act = zpl_status.query_printer_activity('h', 9100)
    _dead_spec = zpl_status.query_printer_specs('h', 9100)
finally:
    zpl_printer_io.send = _status_real_send
check("query_printer_activity(): a dead host is None, not a blank report",
      _dead_act is None and _dead_spec is None, (_dead_act, _dead_spec))
check("query_printer_status(): a dead host costs one attempt per query, not "
      "one timeout per command",
      len(_attempts) == 2, len(_attempts))

# Connected but mute throughout: asked, answered nothing, so None - the same
# distinction the font and object listings draw.
_mute_log = []
zpl_printer_io.send = _status_fake({}, _mute_log)
try:
    _mute = zpl_status.query_printer_activity('h', 9100)
finally:
    zpl_printer_io.send = _status_real_send
check("query_printer_activity(): a printer that answers nothing at all is None",
      _mute is None, _mute)

# The specs half, and the commands that must never be sent from this panel:
# ~JD puts the printer into diagnostics mode, printing every byte it receives;
# ~WC, ~WQ and ^WD print a label instead of replying. A rule that is only a
# comment is a rule that gets broken, so it is checked.
_spec_log = []
zpl_printer_io.send = _status_fake(
    {'~HI': b'ZTC ZT230-203dpi ZPL,V53.17.7Z,8,8192KB,XML',
     'memory.flash_size': b'"68157440"', 'memory.flash_free': b'"66119680"',
     'device.uptime': b'"00 days 02 hours 45 mins 30 secs"',
     'odometer.total_print_length': b'"8560 INCHES, 21744 CENTIMETERS"'},
    _spec_log)
try:
    _spec = zpl_status.query_printer_specs('h', 9100)
finally:
    zpl_printer_io.send = _status_real_send
check("query_printer_specs(): model, firmware and resolution come off ~HI",
      [r.value for r in _spec.sections[0].readings[:3]]
      == ['ZTC ZT230-203dpi ZPL', 'V53.17.7Z', '203 dpi'],
      _spec.sections[0].readings)
# The wear readings, grouped on whichever family the printer speaks.
_wear_log = []
zpl_printer_io.send = _status_fake(
    {'~HI': b'ZTC ZT230-203dpi ZPL,V53.17.7Z,8,8192KB,XML',
     'odometer.total_print_length': b'"8560 INCHES, 21744 CENTIMETERS"'},
    _wear_log)
try:
    _wear_modern = zpl_status.query_printer_specs('h', 9100)
finally:
    zpl_printer_io.send = _status_real_send
check("query_printer_specs(): the odometer reads grouped, units untouched",
      [s.title for s in _wear_modern.sections if s.title == 'Wear']
      and _wear_modern.sections[-1].readings[0].value
      == '8,560 INCHES; 21,744 CENTIMETERS',
      _wear_modern.sections[-1].readings)

_wear_old_log = []
zpl_printer_io.send = _status_fake(
    {'~HI': b'ZM400,V53.17.1Z,8,2048KB,',
     '~HQOD': (b'\x02PRINT METERS\r\n    TOTAL NONRESETTABLE: 8560 "\r\n\x03')},
    _wear_old_log)
try:
    _wear_old = zpl_status.query_printer_specs('h', 9100)
finally:
    zpl_printer_io.send = _status_real_send
check("query_printer_specs(): and so does the ~HQOD fallback",
      _wear_old.sections[-1].readings[0].value == '8,560 "',
      _wear_old.sections[-1].readings)

check("query_printer_specs(): holds only what does not move",
      [s.title for s in _spec.sections] == ['Printer', 'Wear'],
      [s.title for s in _spec.sections])

# --- the two memory bars -----------------------------------------------------
# RAM and Flash are one section and both re-asked every refresh: they answer the
# same question, and both move for the same reason - a font or graphic uploaded
# from this very app comes out of one or the other.
_mem_log = []
zpl_printer_io.send = _status_fake(
    {'zpl.system_status': _SGD_CLEAN, '~HS': _HS_OK, '~HM': b'1024,0780,0032',
     'memory.flash_size': b'"67108864 Bytes"',
     'memory.flash_free': b'"66369536 Bytes Free"'}, _mem_log)
try:
    _mem_act = zpl_status.query_printer_activity('h', 9100)
finally:
    zpl_printer_io.send = _status_real_send
_mem = [s for s in _mem_act.sections if s.title == 'Memory'][0]
_mem_rows = {r.label: r for r in _mem.readings}
check("memory section: RAM and Flash are reported together",
      [r.label for r in _mem.readings] == ['RAM', 'Flash', 'RAM installed'],
      [r.label for r in _mem.readings])
check("memory section: a real printer's 'Bytes Free' reads once, not twice",
      _mem_rows['Flash'].value == '66,369,536 bytes free of 67,108,864 bytes',
      _mem_rows['Flash'].value)
# The bar is what is USED, while the text says what is free - a disk gauge.
check("memory section: RAM's bar is the fraction in use, not the fraction free",
      round(_mem_rows['RAM'].fraction, 3) == round((780 - 32) / 780, 3),
      _mem_rows['RAM'].fraction)
check("memory section: and RAM is measured against what the user may have, "
      "not against what is installed",
      _mem_rows['RAM'].value == '32 KB free of 780 KB', _mem_rows['RAM'].value)
check("memory section: Flash's bar comes off its own two attributes",
      round(_mem_rows['Flash'].fraction, 4)
      == round((67108864 - 66369536) / 67108864, 4),
      _mem_rows['Flash'].fraction)
check("memory section: a memory under a tenth free is marked, one with room is not",
      (_mem_rows['RAM'].level, _mem_rows['Flash'].level)
      == (zpl_status.WARN, zpl_status.PLAIN),
      (_mem_rows['RAM'].level, _mem_rows['Flash'].level))
check("memory section: the installed figure is context, so it carries no bar",
      _mem_rows['RAM installed'].fraction is None)

# _usage(): the fraction both bars are drawn from.
check("_usage(): is unit-free, so the printer's own wording does not matter",
      zpl_status._usage('66369536 Bytes Free', '67108864 Bytes')
      == zpl_status._usage('66369536', '67108864'))
# A free figure larger than its total would draw a bar past its own end.
check("_usage(): a free figure larger than the total is clamped, not trusted",
      zpl_status._usage('900', '780') == 0.0)
check("_usage(): no total, or a zero one, means no bar rather than a divide",
      (zpl_status._usage('700', None), zpl_status._usage('700', '0'),
       zpl_status._usage(None, '780')) == (None, None, None))

# ^HW's footer gives what is free but never the total it is free out of, so the
# fallback reading must not carry a bar drawn against a guessed denominator.
_hw_only_log = []
zpl_printer_io.send = _status_fake(
    {'zpl.system_status': _SGD_CLEAN, '~HS': _HS_OK, '~HM': b'1024,0780,0700',
     '^HWE:': (b'\r\n- DIR E:*.*\r\n* E:ANI.TTF 120404\r\n'
               b'\r\n-  66119680 bytes free E: ONBOARD FLASH\r\n')},
    _hw_only_log)
try:
    _hw_only = zpl_status.query_printer_activity('h', 9100)
finally:
    zpl_printer_io.send = _status_real_send
_hw_rows = [s for s in _hw_only.sections
            if s.title == 'Memory'][0].readings
check("memory section: the ^HW fallback reports free space with no bar, "
      "since that footer never gives the total",
      any(r.fraction is None and 'bytes free' in r.value
          and 'object(s)' in r.value for r in _hw_rows),
      [(r.label, r.value, r.fraction) for r in _hw_rows])
check("query_printer_specs(): storage is asked as attributes, so an unfitted "
      "drive costs no ^HW timeout",
      not any('^HW' in payload for payload in _spec_log), _spec_log)
_banned = [c for c in ('~JD', '~WC', '~WQ', '^WD')
           if any(c in payload for payload in _spec_log + _order_log)]
check("printer_status: never sends ~JD, ~WC, ~WQ or ^WD", _banned == [], _banned)

# cancel= and Cancelled, in a block of their own rather than folded into the
# exact-count assertion above.
_status_tokens = []
def _status_capture(address, port, payload, timeout, read_reply=False, cancel=None):
    _status_tokens.append(cancel)
    return _HS_OK
_status_sentinel = object()
zpl_printer_io.send = _status_capture
try:
    zpl_status.query_printer_activity('h', 9100, cancel=_status_sentinel)
    zpl_status.query_printer_specs('h', 9100, cancel=_status_sentinel)
finally:
    zpl_printer_io.send = _status_real_send
check("printer_status: cancel= reaches send from both queries",
      _status_tokens and set(_status_tokens) == {_status_sentinel},
      len(_status_tokens))

def _status_cancelling(address, port, payload, timeout, read_reply=False, cancel=None):
    raise zpl_printer_io.Cancelled('stopped')
zpl_printer_io.send = _status_cancelling
try:
    _propagated = []
    for query in (zpl_status.query_printer_activity, zpl_status.query_printer_specs):
        try:
            query('h', 9100)
        except zpl_printer_io.Cancelled:
            _propagated.append(True)
finally:
    zpl_printer_io.send = _status_real_send
check("printer_status: Cancelled propagates rather than reading as unreachable",
      _propagated == [True, True], _propagated)

# report_text(): the clipboard text both frontends copy, built here so the two
# cannot word it differently.
_text = zpl_status.report_text(_spec, _silent_act)
check("report_text(): carries every section title of every report given",
      all(t in _text for t in ('Printer', 'Wear', 'Faults', 'Work', 'Memory')),
      _text[:120])
check("report_text(): marks a fault and names what went unanswered",
      '<-- error' in _text and 'Not answered: ~HS' in _text, _text[-200:])
check("report_text(): is deterministic for one input",
      zpl_status.report_text(_spec, _silent_act) == _text)
check("report_text(): says so rather than returning nothing for an "
      "unreachable printer",
      zpl_status.report_text(None).strip() == '(the printer could not be asked)',
      zpl_status.report_text(None))

# --- copy, paste and duplicate -----------------------------------------------
# The clipboard holds ZPL - the copied elements written out as a label of their
# own - and a paste reads it back with the parser Open uses. Duplicate copies
# in place without the round trip. What both have to get right is where the
# copies land and which groups they belong to.

check("paste_step(): a tenth of an inch at every head resolution",
      [Document(dpi=d).paste_step() for d in (203, 300, 600)] == [20, 30, 60],
      [Document(dpi=d).paste_step() for d in (203, 300, 600)])

_cp_png = os.path.join(tempfile.mkdtemp(), 'logo.png')
_cp_img = Image.new('L', (60, 40), 255)
for _x in range(0, 60, 3):
    for _y in range(40):
        _cp_img.putpixel((_x, _y), 0)
_cp_img.save(_cp_png)

_cp_src = Document(812, 1218)
_cp_hidden = _cp_src.add_text_element("Copy me")
_cp_hidden.print_enabled = False
_cp_src.add_text_element("Grüße")
_cp_src.add_frame_element()
_cp_src.add_circle_element()
_cp_src.add_ellipse_element()
_cp_src.add_diagonal_element()
_cp_src.add_graphic_symbol_element('B')
_cp_turned = _cp_src.add_barcode_element()
_cp_turned.orientation = 'R'
_cp_turned.sync_box()
_cp_src.add_image_element(_cp_png)
_cp_src.add_stored_graphic_element()
_cp_src.select_all()
_cp_clip = _cp_src.copy_zpl()
_cp_into = Document(812, 1218)
_cp_count, _cp_drawn = _cp_into.paste_zpl(_cp_clip)[:2]
check("copy/paste: every element type pasted into an empty label writes the "
      "label it was copied from", _cp_into.to_zpl() == _cp_src.to_zpl(),
      [line for line in _cp_into.to_zpl().splitlines()
       if line not in _cp_src.to_zpl().splitlines()][:6])
check("copy/paste: counts what it added, and nothing was rescaled",
      (_cp_count, _cp_drawn) == (len(_cp_src.elements), None),
      (_cp_count, _cp_drawn))
check("copy/paste: what was pasted is the selection",
      _cp_into.selection == _cp_into.elements)
check("copy: a label with anything outside ASCII says it is UTF-8",
      '^CI28' in _cp_clip)
check("copy: a copy made here brings nothing a paste has to leave out",
      workflow.left_out_of_paste(_cp_clip) == [],
      workflow.left_out_of_paste(_cp_clip))
check("copy: nothing selected is nothing to copy",
      Document().copy_zpl() == '')

# Where a paste lands: where it was copied from, unless that spot is taken by
# what it copied - then a step down and right, and a step more each time.
_cp_doc = Document(812, 1218)
_cp_f = _cp_doc.add_frame_element()
_cp_doc.select(_cp_f)
_cp_one = _cp_doc.copy_zpl()
_cp_doc.paste_zpl(_cp_one)
_cp_p1 = _cp_doc.selected_element
_cp_doc.paste_zpl(_cp_one)
_cp_p2 = _cp_doc.selected_element
check("paste: beside what it copied, one step down and right",
      (_cp_p1.x - _cp_f.x, _cp_p1.y - _cp_f.y) == (20, 20),
      (_cp_p1.x, _cp_p1.y, _cp_f.x, _cp_f.y))
check("paste: and the next paste a step beyond that",
      (_cp_p2.x - _cp_f.x, _cp_p2.y - _cp_f.y) == (40, 40),
      (_cp_p2.x, _cp_p2.y))
check("paste: on top of the z-order", _cp_doc.elements[-1] is _cp_p2)

_cp_cut = Document(812, 1218)
_cp_cut.add_frame_element()
_cp_cut.add_barcode_element()
_cp_before = _cp_cut.to_zpl()
_cp_cut.select(_cp_cut.elements[-1])
_cp_moved = _cp_cut.copy_zpl()
_cp_cut.remove_selected()
_cp_cut.paste_zpl(_cp_moved)
check("cut then paste: back where it was", _cp_cut.to_zpl() == _cp_before)

# Groups: a copy of a group is a group of its own, never another member of
# the group it was copied from; a group only partly copied is not copied.
_cp_g = Document(812, 1218)
_cp_ga, _cp_gb = _cp_g.add_frame_element(), _cp_g.add_circle_element()
_cp_g.select_many([_cp_ga, _cp_gb])
_cp_g.group_selected()
_cp_g.paste_zpl(_cp_g.copy_zpl())
_cp_gp = list(_cp_g.selection)
check("paste: a copied group comes back a group of its own",
      len(_cp_gp) == 2 and _cp_gp[0].group and _cp_gp[0].group == _cp_gp[1].group
      and _cp_gp[0].group[0] != _cp_ga.group[0],
      [(el.group) for el in _cp_g.elements])
_cp_g.select(_cp_gp[0])
check("paste: and a click on one member of it selects the pasted pair",
      set(map(id, _cp_g.selection)) == set(map(id, _cp_gp)))

_cp_n = Document(812, 1218)
_cp_na, _cp_nb, _cp_nc = (_cp_n.add_frame_element(), _cp_n.add_circle_element(),
                          _cp_n.add_ellipse_element())
_cp_n.select_many([_cp_na, _cp_nb])
_cp_n.group_selected()
_cp_n.select_many([_cp_na, _cp_nc])
_cp_n.group_selected()
_cp_n.select(_cp_na)
_cp_nold = {gid for el in _cp_n.elements for gid in el.group}
check("duplicate: a nest makes three copies", _cp_n.duplicate_selected() == 3)
_cp_nd = {el.element_type: el for el in _cp_n.selection}
_cp_nnew = {gid for el in _cp_n.selection for gid in el.group}
check("duplicate: a nest comes back a nest - the pair inside, the third beside it",
      len(_cp_nd['frame'].group) == 2 and _cp_nd['frame'].group == _cp_nd['circle'].group
      and _cp_nd['ellipse'].group == _cp_nd['frame'].group[:1],
      [el.group for el in _cp_n.selection])
check("duplicate: with ids of its own at every depth",
      len(_cp_nnew) == 2 and not (_cp_nnew & _cp_nold), (_cp_nold, _cp_nnew))

_cp_d = Document(812, 1218)
_cp_da, _cp_db = _cp_d.add_frame_element(), _cp_d.add_circle_element()
_cp_d.select_many([_cp_da, _cp_db])
_cp_d.group_selected()
_cp_d.select(_cp_da, direct=True)
_cp_d.duplicate_selected()
check("duplicate: a directly picked member comes out loose",
      len(_cp_d.selection) == 1 and _cp_d.selected_element.group is None
      and _cp_da.group == _cp_db.group and _cp_da.group is not None,
      [el.group for el in _cp_d.elements])

_cp_t = Document(812, 1218)
_cp_ta, _cp_tb, _cp_tc = (_cp_t.add_frame_element(), _cp_t.add_circle_element(),
                          _cp_t.add_ellipse_element())
_cp_t.select_many([_cp_ta, _cp_tb, _cp_tc])
_cp_t.group_selected()
_cp_t.select(_cp_ta, direct=True)
_cp_t.select(_cp_tb, additive=True, direct=True)
_cp_tp = Document(812, 1218)
_cp_tp.paste_zpl(_cp_t.copy_zpl())
check("copy: two of a group's three members picked directly paste loose, "
      "not as a group of two",
      len(_cp_tp.elements) == 2 and all(el.group is None for el in _cp_tp.elements),
      [el.group for el in _cp_tp.elements])

# Resolution: a copy from a 300 dpi label keeps its size on paper in a 203
# dpi one. Text that records none is taken dot for dot.
_cp_hi = Document(1200, 1800, 300)
_cp_hf = _cp_hi.add_frame_element()
_cp_hf.x, _cp_hf.y, _cp_hf.width, _cp_hf.height = 300, 600, 300, 300
_cp_hi.select(_cp_hf)
_cp_lo = Document(812, 1218, 203)
_cp_message = workflow.paste_zpl(_cp_lo, _cp_hi.copy_zpl())
_cp_lp = _cp_lo.selected_element
check("paste: a copy drawn at 300 dpi keeps its size and place on paper at 203",
      (_cp_lp.x, _cp_lp.y, _cp_lp.width, _cp_lp.height) == (203, 406, 203, 203),
      (_cp_lp.x, _cp_lp.y, _cp_lp.width, _cp_lp.height))
check("paste: and says it rescaled",
      _cp_message == "Pasted 1 element - rescaled from 300 to 203 dpi", _cp_message)
_cp_raw = Document(1200, 1800, 300)
_cp_raw.paste_zpl("^XA^FO10,20^GB100,50,3^FS^XZ")
_cp_rp = _cp_raw.selected_element
check("paste: ZPL that records no resolution is taken dot for dot",
      (_cp_rp.x, _cp_rp.y, _cp_rp.width, _cp_rp.height) == (10, 20, 100, 50),
      (_cp_rp.x, _cp_rp.y, _cp_rp.width, _cp_rp.height))

# The edge: a duplicate of something against the bottom-right corner cannot
# step further, and stops looking rather than looking forever.
_cp_e = Document(400, 400)
_cp_ef = _cp_e.add_frame_element()
_cp_ef.x, _cp_ef.y = 400 - _cp_ef.width, 400 - _cp_ef.height
_cp_e.select(_cp_ef)
_cp_e.duplicate_selected()
_cp_e.duplicate_selected()
check("duplicate: held inside the label at its edge, without looping",
      len(_cp_e.elements) == 3 and all(
          el.x + el.width <= 400 and el.y + el.height <= 400 for el in _cp_e.elements),
      [(el.x, el.y) for el in _cp_e.elements])

# ^LH: an element holds its absolute position, so a copy is written against
# no home at all and lands at the same absolute place.
_cp_lh = Document(812, 1218)
_cp_lf = _cp_lh.add_frame_element()
_cp_lf.x, _cp_lf.y = 150, 160
_cp_lh.transform.home = (100, 100)
_cp_lh.select(_cp_lf)
_cp_lclip = _cp_lh.copy_zpl()
check("copy: written at absolute positions, with the label's ^LH left behind",
      '^FO150,160' in _cp_lclip and '^LH' not in _cp_lclip, _cp_lclip)

# The font table: a pasted field may call a ^CW letter only the pasted text
# defines.
_cp_cw = Document(812, 1218)
_cp_cw.paste_zpl("^XA^CWQ,E:FOO.TTF^FO10,10^AQN,30,30^FDHi^FS^XZ")
check("paste: brings the ^CW letter its field calls",
      _cp_cw.font_identifiers.get('Q') == 'Q,E:FOO.TTF' and '^CWQ,E:FOO.TTF' in _cp_cw.to_zpl(),
      _cp_cw.font_identifiers)
_cp_cw2 = Document(812, 1218)
_cp_cw2.font_identifiers['Q'] = 'Q,E:BAR.TTF'
_cp_cw2.paste_zpl("^XA^CWQ,E:FOO.TTF^FO10,10^AQN,30,30^FDHi^FS^XZ")
check("paste: but a letter the label already assigns keeps its own assignment",
      _cp_cw2.font_identifiers['Q'] == 'Q,E:BAR.TTF', _cp_cw2.font_identifiers)

# A pasted field meeting a font letter or a field number that means something
# else here takes on this label's, and the status bar says so - before, it
# said only "Pasted 1 element".
def _clash(into, text):
    _doc = zpl_parser.parse_zpl(into)[0]
    return workflow.paste_zpl(_doc, text), _doc
for _into, _text, _said, _why in (
        ("^XA^CWZ,E:ARIAL.TTF^FO50,50^AZN,30,30^FDmine^FS^XZ",
         "^XA^CWZ,E:TIMES.TTF^FO50,150^AZN,30,30^FDpasted^FS^XZ",
         "Pasted 1 element - kept this label's font Z",
         "a letter assigned another font here"),
        ("^XA^FO50,50^AAN,18,10^FDmine^FS^XZ",
         "^XA^CWA,E:ARIAL.TTF^FO50,150^AAN,18,10^FDpasted^FS^XZ",
         "Pasted 1 element - kept this label's font A",
         "a letter this label's fields call as the built-in font"),
        ("^XA^CWA,E:ARIAL.TTF^FO50,50^AAN,18,10^FDmine^FS^XZ",
         "^XA^FO50,150^AAN,18,10^FDbitmap^FS^XZ",
         "Pasted 1 element - kept this label's font A",
         "a built-in letter this label assigns a font"),
        ("^XA^CWZ,E:ARIAL.TTF^FO50,50^AZN,30,30^FDmine^FS^XZ",
         "^XA^CWZ,E:ARIAL.TTF^FO50,150^AZN,30,30^FDpasted^FS^XZ",
         "Pasted 1 element", "but not a letter meaning the same font"),
        ("^XA^FO50,50^FN1\"Name\"^FS^XZ", "^XA^FO50,150^FN1\"Price\"^FS^XZ",
         "Pasted 1 element - shared field 1 with this label",
         "a field number named otherwise here"),
        ("^XA^FO50,50^FN1\"Name\"^FDBob^FS^XZ",
         "^XA^FO50,150^FN1\"Name\"^FDAl^FS^XZ",
         "Pasted 1 element - shared field 1 with this label",
         "a field number given other data here"),
        ("^XA^FO50,50^FN1\"Name\"^FS^XZ", "^XA^FO50,150^FN1\"Name\"^FS^XZ",
         "Pasted 1 element", "but not the same field again, as a copy within "
         "a label is"),
        ("^XA^CWZ,E:A.TTF^CWY,E:B.TTF^FO5,5^AZN,30,30^FN1\"a\"^FS"
         "^FO5,50^AYN,30,30^FN3\"c\"^FS^XZ",
         "^XA^CWZ,E:C.TTF^CWY,E:D.TTF^FO5,90^AZN,30,30^FN1\"b\"^FS"
         "^FO5,150^AYN,30,30^FN3\"d\"^FS^XZ",
         "Pasted 2 elements - kept this label's fonts Y, Z - shared fields 1, 3 "
         "with this label", "several, in order")):
    _got, _ = _clash(_into, _text)
    check(f"paste: names {_why}", _got == _said, _got)
_got, _doc = _clash("^XA^FO50,50^AAN,18,10^FDmine^FS^XZ",
                    "^XA^CWA,E:ARIAL.TTF^FO50,150^AAN,18,10^FDpasted^FS^XZ")
check("paste: and a letter this label's fields call as the built-in font is "
      "not reassigned under them", '^CW' not in _doc.to_zpl(), _doc.to_zpl())
_got, _doc = _clash("^XA^FO50,50^AAN,18,10^FDmine^FS^XZ",
                    "^XA^CWA,E:ARIAL.TTF^FO50,150^GB50,50,2^FS^XZ")
check("paste: a ^CW letter no pasted field calls is not brought, since it could "
      "reach only this label's own fields - a box once turned every ^AA here "
      "into Arial", (_got, '^CW' in _doc.to_zpl()) == ("Pasted 1 element", False),
      (_got, _doc.to_zpl()))

# What the status bar is told.
_cp_empty = Document(812, 1218)
check("paste: text that describes no element is nothing to paste, and changes nothing",
      workflow.paste_zpl(_cp_empty, "hello") is None and _cp_empty.elements == [])
check("paste: names what belongs to the label it came from and was left out",
      workflow.paste_zpl(Document(812, 1218), "^XA^PQ5^FO10,10^GB50,50,2^FS^XZ")
      == "Pasted 1 element - left out ^PQ",
      workflow.paste_zpl(Document(812, 1218), "^XA^PQ5^FO10,10^GB50,50,2^FS^XZ"))
check("elements_phrase(): one element, two elements",
      (workflow.elements_phrase(1), workflow.elements_phrase(2))
      == ('1 element', '2 elements'))

# Through the Qt window: Copy records no undo entry, and Paste, Duplicate and
# Cut record one each.
_cw_win = qt_main.ZPLDesignerWindow()
_cw_win._save_settings = lambda *a: None
_cw_win.document.add_frame_element()
_cw_win.canvas.commit()
_cw_win.document.select(_cw_win.document.elements[0])
_cw_undo = len(_cw_win._undo_stack)
_cw_win.on_copy()
check("Qt Copy: puts ZPL on the clipboard and records no undo entry",
      app.clipboard().text().startswith('^XA') and len(_cw_win._undo_stack) == _cw_undo)
_cw_win.on_paste()
check("Qt Paste: adds the copy, one undo entry",
      len(_cw_win.document.elements) == 2 and len(_cw_win._undo_stack) == _cw_undo + 1)
_cw_win.on_duplicate()
check("Qt Duplicate: one more, one undo entry",
      len(_cw_win.document.elements) == 3 and len(_cw_win._undo_stack) == _cw_undo + 2)
_cw_win.on_cut()
check("Qt Cut: takes the selection out, one undo entry",
      len(_cw_win.document.elements) == 2 and len(_cw_win._undo_stack) == _cw_undo + 3)
_cw_win.on_undo()
check("Qt Cut: and Undo puts it back", len(_cw_win.document.elements) == 3)
_cw_win.document.clear_selection()
_cw_win._update_edit_menu()
_cw_greyed = not _cw_win.copy_action.isEnabled()
_cw_win._release_edit_menu()
check("Qt Edit menu: greys Copy out with nothing selected, and lets go of it "
      "once closed, so Ctrl+C works on whatever is picked next",
      _cw_greyed and _cw_win.copy_action.isEnabled())

# CONTRIBUTING rule 4: no module in zplcore may import a GUI toolkit. Checked
# by reading the source, since this suite imports PySide2 itself for other
# reasons and so sys.modules proves nothing.
_status_src = (Path(__file__).resolve().parent.parent
               / 'zplcore' / 'printer_status.py').read_text()
check("printer_status.py names no GUI toolkit",
      not any(t in _status_src for t in
              ('PySide2', 'import gi', 'from gi.', 'PyQt5', 'tkinter')))

print()
print(("ALL CHECKS PASSED" if not fails else f"{len(fails)} FAILED: {fails}"))
sys.exit(1 if fails else 0)