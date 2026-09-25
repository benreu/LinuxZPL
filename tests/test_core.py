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
        "^FO10,10^BDN,2,3^FDmaxi^FS\n^FO20,20^A0N,30,30^FDafter^FS\n^XZ")
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
      workflow.unsupported_commands("^XA^FO1,1^BDN,2,10^FDmaxi^FS^FH^XZ") == ['^BD'])
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
said = workflow.warn_unsupported("^XA^CC//FO1,1/BDN,2,10/FDmaxi/FS/CC^^XZ",
                                 lambda cmds: heard.append(('dropped', cmds)),
                                 lambda found: heard.append(('redefined', found)))
check("a redefining label hears the redefinition notice, then the ordinary "
      "list - read on the canonical text, so it names the real ^BD",
      heard == [('redefined', ['^CC/', '/CC^']), ('dropped', ['^BD'])]
      and said == ['^CC/', '/CC^', '^BD'], heard)
heard = []
said = workflow.warn_unsupported("^XA^FO1,1^BDN,2,10^FDmaxi^FS^XZ",
                                 lambda cmds: heard.append(('dropped', cmds)),
                                 lambda found: heard.append(('redefined', found)))
check("and one that does not is reported exactly as before",
      heard == [('dropped', ['^BD'])] and said == ['^BD'], heard)
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
      workflow.unsupported_commands("^XA^CC//FO1,1/BDN,2,10/FDmaxi/FS/CC^^XZ")
      == ['^BD'],
      workflow.unsupported_commands("^XA^CC//FO1,1/BDN,2,10/FDmaxi/FS/CC^^XZ"))
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
check("and a matrix symbology offers neither a height nor a line",
      [name for name, feat in BARCODE_FEATURES.items()
       if feat['height'] is None or not feat['text']]
      == sorted(zpl_symbology.MATRIX, key=list(BARCODE_FEATURES).index),
      [(n, f['height'], f['text']) for n, f in BARCODE_FEATURES.items()
       if f['height'] is None or not f['text']])

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
check("the dialog leaves the box equal to the wrap",
      (de.width, de.height) == (220, 2 * (de.font_height + 3)),
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
    zw._default_printer = ('10.0.0.9', zw.printer_port, zw.printer_dpi)
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
    zw._default_printer = (zw.printer_address, zw.printer_port, zw.printer_dpi)
    zw._save_settings()

    real_dialog = qt_dialogs.printer_settings_dialog
    qt_dialogs.printer_settings_dialog = lambda *a, **k: ('10.0.0.5', 9200, 203)
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
    def capture_dialog(parent, address, port, dpi, **kwargs):
        seen['address'], seen['port'], seen['dpi'] = address, port, dpi
        return None  # cancel, so nothing else about window state changes
    qt_dialogs.printer_settings_dialog = capture_dialog
    try:
        zw.on_default_printer()
    finally:
        qt_dialogs.printer_settings_dialog = real_dialog
    check("Default Printer opens pre-filled with the persisted default, not the session override",
          (seen['address'], seen['port']) == ('192.168.1.50', 9100), seen)

    # Contrast: Default Printer, given the same dialog result, does persist.
    qt_dialogs.printer_settings_dialog = lambda *a, **k: ('10.0.0.5', 9200, 203)
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
    zw._default_printer = (zw.printer_address, zw.printer_port, zw.printer_dpi)
    zw._save_settings()

    real_dialog = qt_dialogs.printer_settings_dialog
    qt_dialogs.printer_settings_dialog = lambda *a, **k: ('10.0.0.8', 9400, 203)
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
    zw._default_printer = (zw.printer_address, zw.printer_port, zw.printer_dpi)
    zw._save_settings()

    real_dialog = qt_dialogs.printer_settings_dialog
    qt_dialogs.printer_settings_dialog = lambda *a, **k: ('10.0.0.9', 9500, 203)
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

# ^GS draws a glyph from the symbol font. It is the same trap as an unsupported
# symbology and was missed by the fix for those because it is not a ^B command:
# ^GSN,50,50^FDA saved as ^AAN,9,5^FDA, a 50-dot symbol arriving as 9-dot text.
# ^B3, ^BE and ^BQ are no longer in this list - they draw for real now, checked
# below. ^BD MaxiCode and ^B4 Code 49 are the ones that still do not.
for symbology, source in (("^BD", "^BDN,2,5^FDMM,AHELLO^FS"),
                          ("^B4", "^B4N,6,200^FDdata^FS"),
                          ("^GS", "^GSN,50,50^FDA^FS")):
    page = f"^XA^PW812^LL1218^FO50,50{source}^XZ"
    read = zpl_parser.parse_zpl(page)[0]
    check(f"{symbology} is dropped, not turned into text",
          not read.elements, [e.element_type for e in read.elements])
    check(f"and {symbology} is named as a command a save would drop",
          symbology in workflow.unsupported_commands(page),
          workflow.unsupported_commands(page))

check("the preview draws nothing for one still unsupported either",
      _preview_ink("^XA^PW400^LL300^FO50,50^BDN,2,5^FDMM,AHELLO^FS^XZ",
                   400, 300) is None,
      _preview_ink("^XA^PW400^LL300^FO50,50^BDN,2,5^FDMM,AHELLO^FS^XZ", 400, 300))

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
      _fwd == [_sentinel] * 4, _fwd)

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
          workflow.missing_printer_fonts(_FontDoc({'ARIAL': '/a.ttf'}), 'h', 1) is None)
    zpl_fonts.query_printer_fonts = lambda *a, **k: {'ARIAL'}
    check("missing_printer_fonts(): nothing missing when the printer has them all",
          workflow.missing_printer_fonts(_FontDoc({'arial': '/a.ttf'}), 'h', 1) == ({}, {}))
    _m = workflow.missing_printer_fonts(_FontDoc({'ARIAL': '/a.ttf', 'ROBOTO': '/r.ttf', 'MYSTERY': None}), 'h', 1)
    check("missing_printer_fonts(): missing vs uploadable (only those with a source file)",
          _m == ({'ROBOTO': '/r.ttf', 'MYSTERY': None}, {'ROBOTO': '/r.ttf'}), _m)
    _calls = []
    def _fake_qpf(*a, **k):
        _calls.append('asked'); return set()
    zpl_fonts.query_printer_fonts = _fake_qpf
    check("missing_printer_fonts(): a label with only built-in fonts never asks the printer",
          workflow.missing_printer_fonts(_FontDoc({}), 'h', 1) == ({}, {}) and _calls == [], _calls)

    _t, _d = workflow.font_problem_prompt(None)
    check("font_problem_prompt(None): the could-not-ask wording",
          'could not be asked' in _t and 'substitute' in _d, (_t, _d))
    _t, _d = workflow.font_problem_prompt({'ROBOTO': '/r.ttf', 'MYSTERY': None})
    check("font_problem_prompt(): lists each font, flagging the ones with no source",
          'E:ROBOTO.TTF' in _d and 'E:MYSTERY.TTF   (source file unknown)' in _d, _d)

    _uploaded, _progress = [], []
    def _fake_upload(address, port, path, name, timeout=30, cancel=None):
        _uploaded.append((name, path, cancel))
    zpl_fonts.upload_font = _fake_upload
    workflow.upload_fonts({'B': '/b', 'A': '/a'}, 'h', 1, _progress.append, cancel=_sentinel)
    check("upload_fonts(): uploads each font in name order, reporting each, passing cancel through",
          _uploaded == [('A', '/a', _sentinel), ('B', '/b', _sentinel)]
          and _progress == ['Uploading E:A.TTF...', 'Uploading E:B.TTF...'], (_uploaded, _progress))
    def _failing_upload(address, port, path, name, timeout=30, cancel=None):
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

_pq = zpl_parser.parse_zpl("^XA^PQ5,2,1,Y^XZ")[0]
check("^PQ sets quantity, pause count, replicates and the override flag",
      (_pq.print_quantity, _pq.print_pause_count, _pq.print_replicates,
       _pq.print_override_pause) == (5, 2, 1, True),
      (_pq.print_quantity, _pq.print_pause_count, _pq.print_replicates,
       _pq.print_override_pause))

_no_pq = zpl_parser.parse_zpl("^XA^XZ")[0]
check("a format with no ^PQ defaults to one copy and no options",
      (_no_pq.print_quantity, _no_pq.print_pause_count,
       _no_pq.print_replicates, _no_pq.print_override_pause)
      == (1, 0, 0, False))
check("and writes back no ^PQ line at all", '^PQ' not in _no_pq.to_zpl())

check("a quantity on its own round-trips as just ^PQ5",
      '^PQ5' in zpl_parser.parse_zpl("^XA^PQ5^XZ")[0].to_zpl().split('\n'))
check("a later parameter forces the earlier ones to be spelled too",
      '^PQ1,3' in zpl_parser.parse_zpl("^XA^PQ1,3^XZ")[0].to_zpl().split('\n'))
check("and every parameter round-trips together",
      '^PQ5,2,1,Y' in zpl_parser.parse_zpl("^XA^PQ5,2,1,Y^XZ")[0].to_zpl().split('\n'))

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

print()
print(("ALL CHECKS PASSED" if not fails else f"{len(fails)} FAILED: {fails}"))
sys.exit(1 if fails else 0)