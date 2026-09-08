import base64, math, os, re, sys, tempfile
from pathlib import Path
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide2.QtWidgets import QApplication
from PySide2.QtGui import QImage
from PySide2.QtCore import Qt, QPoint, QEvent
from PySide2.QtGui import QMouseEvent

from zplcore import fonts as zpl_fonts, geometry, parser as zpl_parser, geometry
from zplcore import model as zpl_model
from zplcore.model import Document, TextElement, BarcodeElement, FrameElement, ImageElement

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

# --- derived text width -----------------------------------------------------
narrow = TextElement(0, 0, 'IIII'); narrow.font_path = FONT
wide   = TextElement(0, 0, 'WWWW'); wide.font_path = FONT
check("proportional text: IIII narrower than WWWW",
      narrow.printed_width() < wide.printed_width(),
      f"{narrow.printed_width()} vs {wide.printed_width()}")

builtin = TextElement(0, 0, 'IIII')   # no font -> ^AF, fixed width
check("built-in font width = len*font_width",
      builtin.printed_width() == 4 * builtin.font_width, builtin.printed_width())

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
from PIL import Image
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

# --- printer object naming --------------------------------------------------
check("unsafe chars stripped, truncated to 8",
      zpl_fonts.printer_font_name('/x/Catrina Demo.ttf') == 'CATRINAD',
      zpl_fonts.printer_font_name('/x/Catrina Demo.ttf'))
taken = {'DEJAVUSA'}
second = zpl_fonts.printer_font_name('/x/DejaVuSans-Bold.ttf', taken=taken)
check("collision gets a numeric suffix", second != 'DEJAVUSA' and len(second) <= 8, second)
check("empty result becomes FONT", zpl_fonts.printer_font_name('/x/...ttf') == 'FONT',
      zpl_fonts.printer_font_name('/x/...ttf'))

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
check("failed save reports and keeps the flag",
      w.save_zpl_file('/nonexistent-dir/x.zpl', '^XA^XZ') is False)

print()
print(("ALL CHECKS PASSED" if not fails else f"{len(fails)} FAILED: {fails}"))
sys.exit(1 if fails else 0)
