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

def box_of(element):
    return (element.x, element.y, element.width, element.height)

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
      workflow.unsupported_commands("^XA^FO1,1^BQN,2,10^FDQR^FS^FH^XZ") == ['^BQ'])
check("and the label transforms are not, now that they survive a save",
      workflow.unsupported_commands(
          "^XA^LH10,10^LS1^LT1^POI^PMY^LRY^FO1,1^A0N,30,30^FDx^FS^XZ") == [],
      workflow.unsupported_commands(
          "^XA^LH10,10^LS1^LT1^POI^PMY^LRY^FO1,1^A0N,30,30^FDx^FS^XZ"))

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

check("both frontends are offered the same five symbologies",
      [v for _l, v in BARCODE_SYMBOLOGIES]
      == ['code128', 'code39', 'ean13', 'interleaved2of5', 'upcean_extension'])
check("only Code 128 and Code 39 offer a mode or a check digit label apiece",
      [(name, feat['mode'], feat['check_digit'] is not None)
       for name, feat in BARCODE_FEATURES.items()]
      == [('code128', True, True), ('code39', False, True),
          ('ean13', False, False), ('interleaved2of5', False, True),
          ('upcean_extension', False, False)])

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

# ^FR flips a frame's own colour, in the preview as on the canvas
fr_frame = ZPLRenderer(400, 300).render(
    "^XA^PW400^LL300^FO20,20^FR^GB360,260,4^FS^XZ").convert('L')
check("the preview draws a ^FR frame's border inverted",
      fr_frame.getpixel((200, 21)) > 200, fr_frame.getpixel((200, 21)))
check("^FR reversed on a foreign file writes back after ^GB and still renders",
      ZPLRenderer(400, 300).render(
          "^XA^PW400^LL300^FO20,20^GB360,260,4^FR^FS^XZ"
      ).convert('L').getpixel((200, 21)) > 200)

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
# ^B3 and ^BE are no longer in this list - they draw for real now, checked below.
for symbology, source in (("^BQ", "^BQN,2,5^FDMM,AHELLO^FS"),
                          ("^BX", "^BXN,6,200^FDdata^FS"),
                          ("^GS", "^GSN,50,50^FDA^FS")):
    page = f"^XA^PW812^LL1218^FO50,50{source}^XZ"
    read = zpl_parser.parse_zpl(page)[0]
    check(f"{symbology} is dropped, not turned into text",
          not read.elements, [e.element_type for e in read.elements])
    check(f"and {symbology} is named as a command a save would drop",
          symbology in workflow.unsupported_commands(page),
          workflow.unsupported_commands(page))

check("the preview draws nothing for one still unsupported either",
      _preview_ink("^XA^PW400^LL300^FO50,50^BQN,2,5^FDMM,AHELLO^FS^XZ",
                   400, 300) is None,
      _preview_ink("^XA^PW400^LL300^FO50,50^BQN,2,5^FDMM,AHELLO^FS^XZ", 400, 300))

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
def _fake_dpo_send(address, port, payload, timeout, read_reply=False):
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

# --- printer_io.send_command(): the console's text-in/text-out wrapper -----
# It should encode the command as UTF-8, pass it straight through to send()
# unmodified (read_reply always on, since a console has no other way to know
# whether anything came back), and decode whatever comes back the same way -
# including a reply that isn't valid UTF-8, which must not raise.
from zplcore import printer_io

_sc_calls = []
def _fake_send(address, port, payload, timeout, read_reply=False):
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