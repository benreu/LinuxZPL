import base64, math, os, re, sys, tempfile, zlib
from pathlib import Path
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import _isolate  # a throwaway settings file, before any frontend is imported

from PySide2.QtWidgets import QApplication
from PySide2.QtGui import QImage
from PySide2.QtCore import Qt, QPoint, QEvent
from PySide2.QtGui import QMouseEvent

from zplcore import fonts as zpl_fonts, geometry, parser as zpl_parser, geometry
from zplcore import model as zpl_model
from zplcore.model import Document, TextElement, BarcodeElement, FrameElement, ImageElement
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

# --- the name a save chooser hands back -------------------------------------
from zplcore import workflow as _wf
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
from zplcore import textraster
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
      workflow.unsupported_commands("^XA^FO1,1^BQN,2,10^FDQR^FS^LRY^XZ") == ['^BQ', '^LR'])

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
check("the dialog leaves the box equal to the wrap",
      (de.width, de.height) == (220, 2 * (de.font_height + 3)),
      (de.width, de.height))

_drive_text_dialog(de, ddoc,
                   lambda dialog: dialog.findChild(QCheckBox, 'wrap').setChecked(False))
check("unticking wrap joins the lines rather than leaving a break behind",
      de.block is None and de.text == "ACME Widget Model 4400", de.text)

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

# ^CI cannot be modelled, so it has to be reported rather than dropped in
# silence - which is what listing it as modelled did
check("^CI is reported as a command a save would drop",
      workflow.unsupported_commands("^XA^CI28^FO1,1^A0N,9,9^FDx^FS^XZ") == ['^CI'])
check("^CF is not reported, now that it is honoured",
      workflow.unsupported_commands("^XA^CF0,40^FO1,1^FDx^FS^XZ") == [])

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
      sized[2:] == (203, 2.75, 4.25), sized[2:])
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
finally:
    qt_main._config_path = real_config_path

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
pdoc = Document(400, 300, dpi=203)
pt = pdoc.add_text_element('one two three four five six seven eight')
pt.x, pt.y = 0, 0
pt.font_height = pt.font_width = 40
pt.block = FieldBlock(300, 6, 0, 'C', 0)
pdoc.sync_text_width(pt)
preview = ZPLRenderer(400, 300).render(pdoc.to_zpl()).convert('L')
expected = textraster.raster_block(pt.text, ZPLRenderer.DEFAULT_FONT_PATH,
                                   pt.font_height, pt.font_width, pt.block)
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

# A bitmap font is not proportional, so it inherits ^CF's width instead
bitmap = zpl_parser.parse_zpl("^XA^PW812^LL1218^FO50,50^AFN,18^FDHg^FS^XZ")[0].elements[0]
check("a bitmap font with no width inherits ^CF's",
      bitmap.font_width == zpl_parser.DEFAULT_FONT['width'], bitmap.font_width)
inherited = zpl_parser.parse_zpl(
    "^XA^PW812^LL1218^CF0,40,20^FO50,50^A0N,40^FDHg^FS^XZ")[0].elements[0]
check("and ^CF still wins when it set a width for a scalable font",
      inherited.font_width == 20, inherited.font_width)
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

check("the preview reads a partial ^A against the ^CF in force",
      _preview_ink("^XA^PW400^LL300^CF0,40,20^FO50,50^A0N,40^FDHg^FS^XZ", 400, 300)
      == _preview_ink("^XA^PW400^LL300^FO50,50^A0N,40,20^FDHg^FS^XZ", 400, 300),
      (_preview_ink("^XA^PW400^LL300^CF0,40,20^FO50,50^A0N,40^FDHg^FS^XZ", 400, 300),
       _preview_ink("^XA^PW400^LL300^FO50,50^A0N,40,20^FDHg^FS^XZ", 400, 300)))

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
    ZPLRenderer.DEFAULT_FONT_PATH, 40)
check("the preview puts the baseline on the y ^FT named",
      abs(sat_on - 150) <= 1, (sat_on, baseline_ink))

rescaled = zpl_parser.parse_zpl(
    "^XA^PW812^LL1218^FT50,300^GB300,200,4^FS^XZ")[0]
rescaled.rescale(1.5)
check("rescaling carries the ^FT offset with the dots",
      "^FT75,450" in rescaled.elements[0].to_zpl(),
      rescaled.elements[0].to_zpl().replace('\n', ' '))

# --- a symbology this designer cannot draw is not text ----------------------

for symbology, source in (("^B3", "^B3N,N,60,Y,N^FD123ABC^FS"),
                          ("^BQ", "^BQN,2,5^FDMM,AHELLO^FS"),
                          ("^BX", "^BXN,6,200^FDdata^FS"),
                          ("^BE", "^BEN,80,Y,N^FD123456789012^FS")):
    page = f"^XA^PW812^LL1218^FO50,50{source}^XZ"
    read = zpl_parser.parse_zpl(page)[0]
    check(f"{symbology} is dropped, not turned into text",
          not read.elements, [e.element_type for e in read.elements])
    check(f"and {symbology} is named as a command a save would drop",
          symbology in workflow.unsupported_commands(page),
          workflow.unsupported_commands(page))

check("the preview draws nothing for one either",
      _preview_ink("^XA^PW400^LL300^FO50,50^B3N,N,60,Y,N^FD123ABC^FS^XZ",
                   400, 300) is None,
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

print()
print(("ALL CHECKS PASSED" if not fails else f"{len(fails)} FAILED: {fails}"))
sys.exit(1 if fails else 0)
