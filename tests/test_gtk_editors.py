#!/usr/bin/env python3
"""
The GTK element editors.

The conformance suite drives the model directly and never opens an editor, and
the other two suites are Qt-only - so without this the GTK half of the editors
has no cover at all. What it checks is the shape they were given: non-modal
children of the designer, one per element, applying on OK.

Needs a display, like the conformance suite: run it under DISPLAY, or xvfb-run.
"""

import configparser
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import _isolate  # a throwaway settings file, before any frontend is imported

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk

from gtkui import window as gtk_main
from gtkui.window import ZPLViewerWindow
from zplcore import geometry
from zplcore.connection import Connection

fails = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + ((" -- " + str(extra)) if extra else ""))
    if not cond:
        fails.append(name)


window = ZPLViewerWindow()
document = window.design_canvas.document
text = document.add_text_element("editable")
frame = document.add_frame_element()

# --- a child of the designer, and not a modal one ---------------------------

window.open_editor_window(text)
first = window._editors[id(text)]
check("an editor opens without blocking its caller", first.get_visible())
check("it is transient for the designer, so it floats above it",
      first.get_transient_for() is window)
check("and it is not modal, so the designer stays usable",
      not first.get_modal())

# --- one editor per element -------------------------------------------------

window.open_editor_window(text)
check("opening a second editor on one element raises the open one instead",
      window._editors[id(text)] is first and len(window._editors) == 1,
      len(window._editors))

window.open_editor_window(frame)
check("a different element gets an editor of its own alongside",
      len(window._editors) == 2, len(window._editors))

# --- applying on OK, and not before -----------------------------------------

before = len(window._undo_stack)
window._editors[id(frame)].response(Gtk.ResponseType.OK)
check("OK applies the edit and records one history entry",
      len(window._undo_stack) == before + 1,
      (before, len(window._undo_stack)))
check("and accepting closes that editor",
      id(frame) not in window._editors)

window.open_editor_window(frame)
before = len(window._undo_stack)
window._editors[id(frame)].response(Gtk.ResponseType.CANCEL)
check("Cancel leaves the document untouched and records no history",
      len(window._undo_stack) == before and id(frame) not in window._editors)

# --- the reverse print (^FR) checkbox reaches the element -------------------
# Only the model layer is exercised elsewhere - this is what would catch a
# dialog that adds the checkbox but forgets to read it back in on_response.


def _find_checkbutton(container, label):
    for child in container.get_children():
        if isinstance(child, Gtk.CheckButton) and child.get_label() == label:
            return child
        if isinstance(child, Gtk.Container):
            found = _find_checkbutton(child, label)
            if found is not None:
                return found
    return None


for build, describe in ((lambda: document.add_text_element('reversible'), 'text'),
                        (lambda: document.add_frame_element(), 'frame'),
                        (lambda: document.add_circle_element(), 'circle'),
                        (lambda: document.add_ellipse_element(), 'ellipse'),
                        (lambda: document.add_diagonal_element(), 'diagonal line'),
                        (lambda: document.add_graphic_symbol_element('A'), 'symbol'),
                        (lambda: document.add_barcode_element(), 'barcode')):
    element = build()
    window.open_editor_window(element)
    dialog = window._editors[id(element)]
    fr_check = _find_checkbutton(dialog.get_content_area(), "Reverse print (^FR)")
    check(f"the {describe} editor offers a reverse print checkbox",
          fr_check is not None)
    fr_check.set_active(True)
    dialog.response(Gtk.ResponseType.OK)
    check(f"ticking it in the {describe} dialog reaches the element",
          element.reverse_print is True)
    document.elements.remove(element)

# --- Edit Circle: one diameter, and a thickness held under its radius -------


def _editor_body(dialog):
    """The box the editor's rows are packed into: _open_editor wraps them in a
    scrolled window so a tall editor stays on a small screen."""
    scroller = dialog.get_content_area().get_children()[0]
    return scroller.get_child().get_child()


def _spin_buttons(dialog):
    return [child for child in _editor_body(dialog).get_children()
            if isinstance(child, Gtk.SpinButton)]


circle = document.add_circle_element()
window.open_editor_window(circle)
circle_dialog = window._editors[id(circle)]
diameter_spin, thickness_spin = _spin_buttons(circle_dialog)
diameter_spin.set_value(80)
thickness_spin.set_value(60)
circle_dialog.response(Gtk.ResponseType.OK)
check("OK in Edit Circle writes a round box and a thickness under the radius",
      (circle.diameter, circle.width, circle.height, circle.thickness)
      == (80, 80, 80, 40),
      (circle.diameter, circle.width, circle.height, circle.thickness))

window.open_editor_window(circle)
circle_dialog = window._editors[id(circle)]
_spin_buttons(circle_dialog)[0].set_value(120)
circle_dialog.response(Gtk.ResponseType.CANCEL)
check("and Cancel leaves the circle alone", circle.diameter == 80, circle.diameter)
document.elements.remove(circle)

# --- Edit Diagonal Line: a thickness held to the width, and a direction -----


def _combos(dialog):
    return [child for child in _editor_body(dialog).get_children()
            if isinstance(child, Gtk.ComboBoxText)]


line = document.add_diagonal_element()
window.open_editor_window(line)
line_dialog = window._editors[id(line)]
width_spin, height_spin, line_thickness_spin = _spin_buttons(line_dialog)
width_spin.set_value(60)
height_spin.set_value(90)
line_thickness_spin.set_value(100)
colour_combo, direction_combo = _combos(line_dialog)
colour_combo.set_active(1)
direction_combo.set_active(1)
line_dialog.response(Gtk.ResponseType.OK)
check("OK in Edit Diagonal Line writes the box, a thickness held to the width, "
      "the colour and the direction",
      (line.width, line.height, line.thickness, line.colour, line.direction)
      == (60, 90, 60, 'W', 'L'),
      (line.width, line.height, line.thickness, line.colour, line.direction))

window.open_editor_window(line)
line_dialog = window._editors[id(line)]
_combos(line_dialog)[1].set_active(0)
line_dialog.response(Gtk.ResponseType.CANCEL)
check("and Cancel leaves the line alone", line.direction == 'L', line.direction)
document.elements.remove(line)

# --- Edit Ellipse: two sides, and a thickness under half the shorter --------

oval = document.add_ellipse_element()
window.open_editor_window(oval)
oval_dialog = window._editors[id(oval)]
check("an ellipse's editor is Edit Ellipse",
      oval_dialog.get_title() == "Edit Ellipse", oval_dialog.get_title())
width_spin, height_spin, oval_thickness_spin = _spin_buttons(oval_dialog)
width_spin.set_value(60)
height_spin.set_value(90)
oval_thickness_spin.set_value(100)
_combos(oval_dialog)[0].set_active(1)
oval_dialog.response(Gtk.ResponseType.OK)
check("OK in Edit Ellipse writes the box, a thickness under half the shorter "
      "side, and the colour",
      (oval.width, oval.height, oval.thickness, oval.colour) == (60, 90, 30, 'W'),
      (oval.width, oval.height, oval.thickness, oval.colour))

window.open_editor_window(oval)
oval_dialog = window._editors[id(oval)]
_spin_buttons(oval_dialog)[0].set_value(120)
oval_dialog.response(Gtk.ResponseType.CANCEL)
check("and Cancel leaves the ellipse alone", oval.width == 60, oval.width)
document.elements.remove(oval)

# --- the canvas cuts the ellipse's ring where the preview does -------------
# The conformance suite compares ZPL, which says nothing about how the ring
# is painted, so this is the GTK canvas's only cover for it.

import cairo
from zplcore.model import EllipseElement


def _painted(element, under=None):
    """The element drawn on its own, over white or a black box, as a dark(x, y)."""
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 300, 200)
    ctx = cairo.Context(surface)
    ctx.set_source_rgb(1, 1, 1); ctx.paint()
    if under is not None:
        ctx.set_source_rgb(0, 0, 0); ctx.rectangle(*under); ctx.fill()
    window.design_canvas._draw_ellipse_element(ctx, element, False)
    surface.flush()
    data, stride = surface.get_data(), surface.get_stride()

    def dark(x, y):
        at = y * stride + 4 * x
        return all(channel < 100 for channel in data[at:at + 3])
    return dark


dark = _painted(EllipseElement(50, 50, 200, 100, 10))
check("the GTK canvas paints ^GE as a ring, the corners and the middle empty",
      dark(150, 54) and dark(54, 100) and dark(245, 100)
      and not dark(52, 52) and not dark(150, 100),
      (dark(150, 54), dark(54, 100), dark(245, 100), dark(52, 52), dark(150, 100)))
dark = _painted(EllipseElement(50, 50, 200, 100, 50))
check("and fills it once the border reaches half the shorter side",
      dark(150, 100) and not dark(52, 52), (dark(150, 100), dark(52, 52)))
reversed_oval = EllipseElement(50, 50, 200, 100, 10, 'W')
reversed_oval.reverse_print = True
dark = _painted(reversed_oval, under=(50, 50, 100, 100))
check("and inverts under the ring for ^FR, ignoring its colour",
      not dark(54, 100) and dark(245, 100) and dark(100, 100),
      (dark(54, 100), dark(245, 100), dark(100, 100)))

# --- + Symbol and Edit Symbol: the five ^GS symbols ------------------------

from zplcore import graphic_symbols
from zplcore.model import GraphicSymbolElement


def _find_all(container, kind):
    """Every widget of `kind` under `container`, in order - the symbol
    editor's rows are boxes, so its widgets are not the content area's own
    children."""
    found = []
    for child in container.get_children():
        if isinstance(child, kind):
            found.append(child)
        elif isinstance(child, Gtk.Container):
            found += _find_all(child, kind)
    return found


symbol_items = window.add_symbol_button.get_popup().get_children()
check("+ Symbol opens the five, each with its picture beside its name",
      [_find_all(item, Gtk.Label)[0].get_text() for item in symbol_items]
      == [name for _code, _shown, name in graphic_symbols.SYMBOLS]
      and all(_find_all(item, Gtk.Image) for item in symbol_items),
      [_find_all(item, Gtk.Label)[0].get_text() for item in symbol_items])
_before = len(window._undo_stack)
symbol_items[3].activate()
mark = document.selected_element
check("choosing UL adds a selected ^GS^FDD and one undo entry",
      isinstance(mark, GraphicSymbolElement) and mark.text == 'D'
      and len(window._undo_stack) == _before + 1,
      (type(mark).__name__, getattr(mark, 'text', None),
       len(window._undo_stack) - _before))

window.open_editor_window(mark)
mark_dialog = window._editors[id(mark)]
check("a symbol's editor is Edit Symbol",
      mark_dialog.get_title() == "Edit Symbol", mark_dialog.get_title())
symbol_combo, turn_combo = _find_all(_editor_body(mark_dialog), Gtk.ComboBoxText)
mark_height, mark_width = _find_all(_editor_body(mark_dialog), Gtk.SpinButton)
symbol_combo.set_active(1)
mark_height.set_value(60)
mark_width.set_value(30)
turn_combo.set_active(3)
_before = len(window._undo_stack)
mark_dialog.response(Gtk.ResponseType.OK)
check("OK in Edit Symbol writes the symbol, both sizes and the turn, as one "
      "undo entry",
      (mark.text, mark.font_height, mark.font_width, mark.orientation,
       mark.width, mark.height) == ('B', 60, 30, 'B', 72, 24)
      and len(window._undo_stack) == _before + 1,
      (mark.text, mark.font_height, mark.font_width, mark.orientation,
       mark.width, mark.height, len(window._undo_stack) - _before))

window.open_editor_window(mark)
mark_dialog = window._editors[id(mark)]
_find_all(_editor_body(mark_dialog), Gtk.ComboBoxText)[0].set_active(4)
_before = len(window._undo_stack)
mark_dialog.response(Gtk.ResponseType.CANCEL)
check("and Cancel leaves the symbol alone, recording nothing",
      mark.text == 'B' and len(window._undo_stack) == _before,
      (mark.text, len(window._undo_stack) - _before))
document.elements.remove(mark)

pair = GraphicSymbolElement(50, 50, 'AB', 5, 7000)
document.elements.append(pair)
window.open_editor_window(pair)
pair_dialog = window._editors[id(pair)]
check("data that is not one of the five is offered first, as written",
      _find_all(_editor_body(pair_dialog),
                Gtk.ComboBoxText)[0].get_active_text() == "As written: AB")
pair_dialog.response(Gtk.ResponseType.OK)
check("so accepting it unchanged keeps the data and both sizes",
      (pair.text, pair.font_height, pair.font_width) == ('AB', 5, 7000),
      (pair.text, pair.font_height, pair.font_width))
document.elements.remove(pair)


def _painted_symbol(element, under=None):
    """The symbol drawn on its own, over white or a black box, as a dark(x, y)."""
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 300, 200)
    ctx = cairo.Context(surface)
    ctx.set_source_rgb(1, 1, 1); ctx.paint()
    if under is not None:
        ctx.set_source_rgb(0, 0, 0); ctx.rectangle(*under); ctx.fill()
    window.design_canvas._draw_graphic_symbol_element(ctx, element, False)
    surface.flush()
    data, stride = surface.get_data(), surface.get_stride()

    def dark(x, y):
        at = y * stride + 4 * x
        return all(channel < 100 for channel in data[at:at + 3])
    return dark


dark = _painted_symbol(GraphicSymbolElement(50, 50, 'A', 96, 96))
alpha = graphic_symbols.raster('A', 96, 96).getchannel('A')
agree = sum(dark(50 + x, 50 + y) == (alpha.getpixel((x, y)) >= 128)
            for y in range(96) for x in range(96))
check("the GTK canvas paints the symbol the raster holds",
      agree / (96 * 96) > 0.97, agree / (96 * 96))
turned = GraphicSymbolElement(50, 50, 'C', 96, 48, orientation='R')
dark = _painted_symbol(turned)
check("turned a quarter, the TM runs down its box, along the right-hand edge",
      any(dark(x, y) for x in range(130, 146) for y in range(50, 98))
      and not any(dark(x, y) for x in range(50, 80) for y in range(50, 98)))
reversed_mark = GraphicSymbolElement(50, 50, 'B', 100, 100)
reversed_mark.reverse_print = True
dark = _painted_symbol(reversed_mark, under=(50, 50, 100, 100))
check("and inverts under its own ink for ^FR",
      not dark(54, 80) and dark(62, 80), (dark(54, 80), dark(62, 80)))

# --- Edit Barcode: MaxiCode's own rows, and the Insert buttons -------------

from zplcore import model as zpl_model


def _row_for(content, label_text):
    """The dialog row whose label reads `label_text`."""
    for row in content.get_children():
        if isinstance(row, Gtk.Box):
            labels = [c for c in row.get_children() if isinstance(c, Gtk.Label)]
            if labels and labels[0].get_text() == label_text:
                return row
    return None


def _named_button(content, name):
    return next((b for b in _find_all(content, Gtk.Button) if b.get_name() == name),
                None)


maxi = document.add_barcode_element()
maxi.barcode_value, maxi.orientation = 'A_B', 'R'
maxi.sync_box()
window.open_editor_window(maxi)
maxi_dialog = window._editors[id(maxi)]
maxi_content = _editor_body(maxi_dialog)
_find_all(maxi_content, Gtk.ComboBoxText)[0].set_active(
    [code for _l, code in zpl_model.BARCODE_SYMBOLOGIES].index('maxicode'))
check("Edit Barcode hides Orientation, Bar Height and Module Width for a MaxiCode",
      not any(_row_for(maxi_content, label).get_visible()
              for label in ("Orientation:", "Bar Height:", "Module Width:")))
check("and shows its Mode, Symbol Number, Total Symbols and Insert rows",
      all(_row_for(maxi_content, label).get_visible()
          for label in ("MaxiCode Mode:", "Symbol Number:", "Total Symbols:",
                        "Insert:")))
maxi_entry = next(e for e in _find_all(maxi_content, Gtk.Entry)
                  if not isinstance(e, Gtk.SpinButton))
maxi_entry.set_position(3)
_named_button(maxi_content, 'insert_GS').clicked()
_named_button(maxi_content, 'insert_EOT').clicked()
check("Insert GS then EOT write their escapes at the cursor, escaping the "
      "underscore already there",
      (maxi_entry.get_text(), maxi_entry.get_position()) == ('A_5FB_1D_04', 11),
      (maxi_entry.get_text(), maxi_entry.get_position()))
maxi_dialog.response(Gtk.ResponseType.OK)
check("OK makes it a MaxiCode with ^FH on, unturned",
      maxi.symbology == 'maxicode' and maxi.hex_indicator == '_'
      and maxi.orientation == ''
      and "^BD\n^FH_^FDA_5FB_1D_04^FS" in maxi.to_zpl(),
      maxi.to_zpl().replace('\n', ' '))

window.open_editor_window(maxi)
_cancelled = window._editors[id(maxi)]
_named_button(_editor_body(_cancelled), 'insert_RS').clicked()
_cancelled.response(Gtk.ResponseType.CANCEL)
check("Cancel leaves the value as it was",
      maxi.barcode_value == 'A_5FB_1D_04', maxi.barcode_value)

plain = document.add_barcode_element()
window.open_editor_window(plain)
plain_content = _editor_body(window._editors[id(plain)])
check("a Code 128 keeps its Orientation row and has no Insert",
      _row_for(plain_content, "Orientation:").get_visible()
      and not _row_for(plain_content, "Insert:").get_visible())
window._editors[id(plain)].response(Gtk.ResponseType.CANCEL)
document.elements.remove(maxi)
document.elements.remove(plain)

# --- Edit Barcode: MicroPDF417's Size, and a row height under 20 ------------

micro = document.add_barcode_element()
micro.symbology, micro.micro_mode, micro.bar_height = 'micropdf417', 33, 4
micro.barcode_value = 'HELLO'
micro.sync_box()
window.open_editor_window(micro)
micro_dialog = window._editors[id(micro)]
micro_content = _editor_body(micro_dialog)
micro_size = next(c for c in _find_all(micro_content, Gtk.ComboBoxText)
                  if c.get_name() == 'micro_mode')
check("Edit Barcode shows a MicroPDF417's Size row, set to its own mode, and "
      "no line",
      _row_for(micro_content, "Size:").get_visible()
      and micro_size.get_active_text() == "4 columns × 4 rows (mode 33)"
      and not _row_for(micro_content, "Value Text:").get_visible(),
      micro_size.get_active_text())
micro_size.set_active(18)
micro_dialog.response(Gtk.ResponseType.OK)
check("OK makes it mode 18 and keeps its rows 4 dots tall, which a range "
      "starting at 20 used to clamp",
      (micro.micro_mode, micro.bar_height) == (18, 4),
      (micro.micro_mode, micro.bar_height))
document.elements.remove(micro)

# A Data Matrix and a PDF417 share the columns and rows rows. Each must show
# its own label and choices: a PDF417 shown Data Matrix's lost its columns
# to "Fit the data" on the next edit, and the panel applies every edit.
cr = document.add_barcode_element()
cr.symbology, cr.columns, cr.rows, cr.bar_height = 'pdf417', 3, 5, 3
cr.barcode_value = 'HELLO'
cr.sync_box()
window.open_editor_window(cr)
cr_dialog = window._editors[id(cr)]
cr_content = _editor_body(cr_dialog)
cr_combo = lambda name: next(c for c in _find_all(cr_content, Gtk.ComboBoxText)
                             if c.get_name() == name)
cr_cols, cr_rows = cr_combo('columns'), cr_combo('rows')
check("a PDF417's Columns and Rows rows are its own, set to 3 and 5",
      _row_for(cr_content, "Columns:").get_visible()
      and _row_for(cr_content, "Rows:").get_visible()
      and (cr_cols.get_active_text(), cr_rows.get_active_text()) == ('3', '5'),
      (cr_cols.get_active_text(), cr_rows.get_active_text()))
next(e for e in _find_all(cr_content, Gtk.Entry)
     if not isinstance(e, Gtk.SpinButton)).set_text('HELLO AGAIN')
cr_dialog.response(Gtk.ResponseType.OK)
check("and OK after an edit to another row leaves its columns and rows alone",
      (cr.columns, cr.rows) == (3, 5), (cr.columns, cr.rows))
window.open_editor_window(cr)
cr_content = _editor_body(window._editors[id(cr)])
cr_cols = cr_combo('columns')
cr_symbology = _find_all(cr_content, Gtk.ComboBoxText)[0]
cr_codes = [code for _l, code in zpl_model.BARCODE_SYMBOLOGIES]
cr_symbology.set_active(cr_codes.index('datamatrix'))
cr_dm = (_row_for(cr_content, "Least Columns:") is not None,
         cr_cols.get_model()[1][0])
cr_symbology.set_active(cr_codes.index('pdf417'))
check("switching to a Data Matrix and back relabels and refills them each way",
      cr_dm == (True, '10')
      and _row_for(cr_content, "Columns:") is not None
      and cr_cols.get_model()[1][0] == '1',
      (cr_dm, cr_cols.get_model()[1][0]))
window._editors[id(cr)].response(Gtk.ResponseType.CANCEL)
document.elements.remove(cr)

# --- Edit Barcode: ^FM's Positions -------------------------------------------

from zplcore import parser as zpl_parser
series = zpl_parser.parse_zpl(
    "^XA^PW1218^LL2436^FM100,100,e,e,100,1200^BY2^BFN,4,22"
    f"^FD{'X' * 400}^FS^XZ")[0].elements[0]
document.elements.append(series)
window.open_editor_window(series)
series_dialog = window._editors[id(series)]
series_content = _editor_body(series_dialog)
positions_view = next(v for v in _find_all(series_content, Gtk.TreeView)
                      if v.get_name() == 'positions')
positions_store = positions_view.get_model()
check("Edit Barcode lists a series' origins, the one excluded not printing",
      _row_for(series_content, "Positions (^FM):").get_visible()
      and [tuple(row) for row in positions_store]
      == [(100, 100, True), (0, 0, False), (100, 1200, True)],
      [tuple(row) for row in positions_store])
_named_button(series_content, 'add_position').clicked()
positions_store[1][0], positions_store[1][1], positions_store[1][2] = \
    100, 700, True
series_dialog.response(Gtk.ResponseType.OK)
check("OK writes them back as ^FM, the added one under the last",
      "^FM100,100,100,700,100,1200,100,1396\n" in series.to_zpl(),
      series.to_zpl()[:60].replace('\n', ' '))
document.elements.remove(series)

# --- Edit Barcode: a TLC39's MicroPDF417 sizes, as spin buttons -------------

tlc = zpl_parser.parse_zpl(
    "^XA^FO10,10^BT^FD123456,ABCd12345678901234,5551212,88899^FS^XZ"
)[0].elements[0]
document.elements.append(tlc)
window.open_editor_window(tlc)
tlc_dialog = window._editors[id(tlc)]
tlc_content = _editor_body(tlc_dialog)
row_height = next(s for s in _find_all(tlc_content, Gtk.SpinButton)
                  if s.get_name() == 'micro_height')
check("Edit Barcode offers a TLC39's MicroPDF417 row height as a number",
      _row_for(tlc_content, "MicroPDF417 Row Height:").get_visible()
      and row_height.get_value() == 4, row_height.get_value())
row_height.set_value(7)
tlc_dialog.response(Gtk.ResponseType.OK)
check("and OK writes it back, the Code 39's own sizes kept",
      "^BTN,2,2.0,40,2,7\n" in tlc.to_zpl(), tlc.to_zpl().replace('\n', ' '))
document.elements.remove(tlc)

# --- the editors must not outlive the elements they hold --------------------
# Restoring a snapshot replaces every element object. An editor left on screen
# over one would write its fields into a copy the document no longer has, and
# the edit would vanish with nothing to show for it.

window.open_editor_window(text)
stale = window._editors[id(text)]
window._apply_snapshot(window.design_canvas.snapshot())
check("undo closes the editors, whose elements it has just replaced",
      not window._editors, len(window._editors))
check("the editor is taken off screen, not just dropped from the register",
      not stale.get_visible())
check("restoring really did replace the element it was editing",
      all(element is not text for element in document.elements))

live = document.elements[0]
document.selected_element = live
window.open_editor_window(live)
window.on_delete_clicked(None)
check("deleting an element closes the editor open on it",
      not window._editors, len(window._editors))

# --- and a group delete closes every editor it orphans ----------------------
# Delete takes the whole selection, so one editor left open over one of the
# deleted elements would be exactly the detached editor the rule above forbids.

pair = [document.add_text_element('one'), document.add_text_element('two')]
for element in pair:
    window.open_editor_window(element)
check("an editor is open on each of the two", len(window._editors) == 2,
      len(window._editors))
document.select_many(pair)
window.on_delete_clicked(None)
check("deleting a group takes every element in it",
      all(element not in document.elements for element in pair))
check("and closes every editor that was open on one",
      not window._editors, len(window._editors))

# --- the canvas paints a group and a rubber band ----------------------------
# Neither path has any other cover: the conformance suite compares ZPL, and ZPL
# says nothing about what a selection looks like.

import cairo

canvas = window.design_canvas
canvas.set_zoom(1.0)
document.select_many(document.elements[:2])
canvas.band_origin, canvas.band_now = (10, 10), (300, 400)
surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 400, 400)
canvas.on_draw(canvas, cairo.Context(surface))
canvas.band_origin = canvas.band_now = None
check("a group selection and a rubber band paint without raising", True)

# --- Group and Ungroup: the Edit menu's rules, and the outline paints -------
# The conformance suite proves the two frontends agree on what grouping does;
# the sensitivity of the two items is one thing the ZPL cannot show.

grouped = [document.add_text_element('one'), document.add_frame_element()]
document.select_many(grouped)
window._update_edit_menu(None)
check("Group is offered for two loose elements, Ungroup is not",
      window.group_item.get_sensitive() and not window.ungroup_item.get_sensitive())
window.on_group_clicked(None)
window._update_edit_menu(None)
check("once grouped, Ungroup is offered and Group is not",
      window.ungroup_item.get_sensitive() and not window.group_item.get_sensitive())
check("the group's z-order items come from the model",
      window.zorder_items[0].get_sensitive() == document.can_raise())
canvas.on_draw(canvas, cairo.Context(surface))
check("a selected group's outline paints without raising", True)
third = document.add_barcode_element()
document.select_many([grouped[0], third])
window.on_group_clicked(None)
check("grouping a group with another element nests it",
      len(grouped[0].group) == 2 and third.group == (grouped[0].group[0],))
document.select_many([third])
check("a nested selection has an outline per level", len(document.group_outlines()) == 2)
canvas.on_draw(canvas, cairo.Context(surface))
check("a nested group's outlines paint without raising", True)
window.on_ungroup_clicked(None)
check("Ungroup peels the outer level and leaves the pair grouped",
      third.group is None and len(grouped[0].group) == 1)
window.on_ungroup_clicked(None)
check("and a second Ungroup clears the tags",
      all(element.group is None for element in document.elements))

# --- Remove from Group, and the handles a group gets ------------------------
document.select_many(grouped)
window._update_edit_menu(None)
check("Remove from Group is not offered for two loose elements",
      not window.remove_from_group_item.get_sensitive())
window.on_group_clicked(None)
window._update_edit_menu(None)
check("nor for a group picked whole, where Ungroup is",
      window.ungroup_item.get_sensitive() and not window.remove_from_group_item.get_sensitive())
check("a selected group is the resize target",
      isinstance(document.resize_target(), geometry.GroupBox))
canvas.on_draw(canvas, cairo.Context(surface))
check("a selected group's handles paint without raising", True)
document.select(grouped[0], direct=True)
window._update_edit_menu(None)
check("a directly picked member can be removed from its group",
      window.remove_from_group_item.get_sensitive())
depth = len(window._undo_stack)
window.on_remove_from_group_clicked(None)
check("Remove from Group records one undo entry and leaves both loose",
      len(window._undo_stack) == depth + 1
      and all(element.group is None for element in grouped))
window.on_undo()
check("and undo puts the group back",
      sum(1 for element in document.elements if element.group) == 2)

# --- Select All, Deselect All, Invert Selection -----------------------------
document.select_many([document.elements[0]])
window._update_edit_menu(None)
check("Select All is offered while something is left unselected, Deselect All too",
      window.select_all_item.get_sensitive() and window.deselect_all_item.get_sensitive()
      and window.invert_selection_item.get_sensitive())
depth = len(window._undo_stack)
window.on_select_all_clicked(None)
window._update_edit_menu(None)
check("once everything is selected Select All is not, and no undo entry was recorded",
      not window.select_all_item.get_sensitive()
      and len(document.selection) == len(document.elements)
      and len(window._undo_stack) == depth)
window.on_invert_selection_clicked(None)
window._update_edit_menu(None)
check("inverting a full selection leaves nothing, so Deselect All is not offered",
      not document.selection and not window.deselect_all_item.get_sensitive()
      and len(window._undo_stack) == depth)
window.on_select_all_clicked(None); window.on_deselect_all_clicked(None)
check("Deselect All clears it and records nothing",
      not document.selection and len(window._undo_stack) == depth)
for element in list(document.elements):
    document.elements.remove(element)

# --- the Properties panel ----------------------------------------------------
# One panel shows the selection's rows and edits them as they are changed; a
# burst of edits is one undo entry. A window of its own, so the editor windows
# above have nothing to do with it.

import configparser as _pconfig
import time
from gi.repository import Gdk as _PGdk
from gtkui import properties as gtk_props
from zplcore.model import ImageElement as _PImage
from PIL import Image as _PILImage

pw = ZPLViewerWindow()
pw._save_settings = lambda *a: None
pp = pw.properties
pdoc = pw.design_canvas.document


def _ppump(rounds=20):
    """Let the main loop run a while: a paint waits for the frame clock."""
    for _ in range(rounds):
        while Gtk.events_pending():
            Gtk.main_iteration()
        time.sleep(0.01)


def _pdepth():
    return len(pw._undo_stack)


def _pall(kind):
    return _find_all(pp._area, kind)


def _pnamed(name):
    return next((w for w in gtk_props._descendants(pp._area) if w.get_name() == name), None)


def _pkey(window, keyval, focus):
    """A key pressed in `window` with the focus on `focus`, by the path a real
    one takes: the window's own key handler first."""
    if window.get_focus() is not focus:
        # Not again if it is there already: an entry taking the focus
        # selects all of itself, even from itself
        window.set_focus(focus)
    event = _PGdk.Event.new(_PGdk.EventType.KEY_PRESS)
    event.keyval = keyval
    event.window = window.get_window()
    event.send_event = True
    # Key bindings and shortcuts are matched by the hardware key, not the
    # symbol, so an event without one would match nothing either way
    keymap = _PGdk.Keymap.get_for_display(window.get_display())
    found, keys = keymap.get_entries_for_keyval(keyval)
    if found:
        event.hardware_keycode = keys[0].keycode
        event.group = keys[0].group
    return window.emit("key-press-event", event)


_ppump()
check("the Properties panel starts docked beside the canvas, shown, its View "
      "item ticked", pp.get_parent() is pw.paned and not pp.floating and pp.shown
      and pw.panel_item.get_active())
pp.sync()
check("with nothing selected it says so",
      pp._title.get_text() == "Nothing selected" and _pnamed('prop_x') is None)

p_frame = pdoc.add_frame_element()
pw.on_canvas_changed()
pw.design_canvas.queue_draw()
_ppump()
check("a paint moves it to the selection: the frame just added, with X and Y "
      "and the frame's own rows", pp._title.get_text() == "Frame"
      and pp._bound == (p_frame,) and _pnamed('prop_x').get_value() == p_frame.x,
      pp._title.get_text())

# Live, and one undo entry a burst. The frame's rows after X and Y: width,
# height, thickness, rounding; its one list is the colour.
_pd = _pdepth()
_pall(Gtk.ComboBoxText)[0].set_active(1)
_pall(Gtk.SpinButton)[5].set_value(3)
check("an edit reaches the element as it is made",
      (p_frame.colour, p_frame.rounding) == ('W', 3))
check("...and is not an undo entry yet, while a pause would end it",
      _pdepth() == _pd and pp.pending and pp._quiet_source is not None)
pp.finish_edit()
check("the burst is one undo entry", _pdepth() == _pd + 1 and not pp.pending)
pw.on_undo()
pp.sync()
check("undo takes the whole burst back, and the panel follows the restored element",
      (pdoc.elements[0].colour, pdoc.elements[0].rounding) == ('B', 0)
      and pp._bound == (pdoc.elements[0],) and pdoc.elements[0] is not p_frame)
p_frame = pdoc.elements[0]

_pd = _pdepth()
_pall(Gtk.ComboBoxText)[0].set_active(1)
_pkey(pw, _PGdk.KEY_Escape, _pnamed('prop_x'))
pp.sync()
check("Escape throws the burst away, records nothing, and stays on the element",
      pdoc.elements[0].colour == 'B' and _pdepth() == _pd and not pp.pending
      and pp._bound == (pdoc.elements[0],))
p_frame = pdoc.elements[0]

_pall(Gtk.ComboBoxText)[0].set_active(1)
_pnamed('prop_x').activate()
check("Enter ends the burst", _pdepth() == _pd + 1 and not pp.pending)
_pall(Gtk.SpinButton)[5].set_value(2)
pp._on_quiet()
check("so does a pause", _pdepth() == _pd + 2 and not pp.pending)
_pall(Gtk.SpinButton)[5].set_value(4)
pw.set_focus(pw.design_canvas)
check("so does the focus leaving the panel", _pdepth() == _pd + 3 and not pp.pending)
_pall(Gtk.SpinButton)[5].set_value(5)
_press = _PGdk.Event.new(_PGdk.EventType.BUTTON_PRESS)
_press.button, _press.x, _press.y = 1, 2, 2
_press.window = pw.design_canvas.get_window()
pw.design_canvas.on_button_press(pw.design_canvas, _press)
check("so does a click on the canvas, before it acts",
      _pdepth() == _pd + 4 and not pp.pending, _pdepth() - _pd)
pdoc.select(p_frame)
pp.sync()
_pall(Gtk.SpinButton)[5].set_value(6)
pw._edit_menu.emit("show")
pw._edit_menu.emit("hide")
check("so does a menu opening, before any command in it",
      _pdepth() == _pd + 5 and not pp.pending)
_pall(Gtk.SpinButton)[5].set_value(7)
pw.set_focus(pw.design_canvas)
_pd = _pdepth()
_pall(Gtk.SpinButton)[5].set_value(8)
check("a key that goes on to the window's shortcuts ends the burst first",
      pw._on_window_key_press(pw, _PGdk.Event.new(_PGdk.EventType.KEY_PRESS)) is False
      and _pdepth() == _pd + 1 and not pp.pending)

# The window's shortcuts come after a panel box's own keys
p_text = pdoc.add_text_element("words")
pw.design_canvas.queue_draw()
_ppump()
_p_id = next(e for e in _pall(Gtk.Entry)
             if e.get_placeholder_text()
             and e.get_placeholder_text().startswith("optional - Python"))
_p_id.set_text("abc")
pp.finish_edit()
_pd = _pdepth()
# Focused first - a GTK entry selects all of itself when it takes the focus
pw.set_focus(_p_id)
_p_id.set_position(0)
_pkey(pw, _PGdk.KEY_Delete, _p_id)
check("Delete typed in a docked entry deletes a character, not the element",
      _p_id.get_text() == "bc" and p_text in pdoc.elements,
      (_p_id.get_text(), p_text in pdoc.elements))
pp.finish_edit()
_p_spare = pdoc.add_frame_element()
pw.on_canvas_changed()
_pkey(pw, _PGdk.KEY_Delete, pw.design_canvas)
check("...where on the canvas the same key deletes the selection",
      _p_spare not in pdoc.elements and p_text in pdoc.elements)
pdoc.select(p_text)
pp.sync()

_p_buffer = _pall(Gtk.TextView)[0].get_buffer()
_p_buffer.set_text("one\ntwo")
_p_wrap = [b for b in _pall(Gtk.CheckButton)
           if b.get_label() == "Wrap the text into a block"][0]
check("a line break typed gets the text a block, and ticks Wrap",
      p_text.block is not None and _p_wrap.get_active())
_p_buffer.set_text("one\ntwos")
check("...which the next keystroke keeps",
      p_text.block is not None and p_text.text == 'one\\&twos', p_text.text)
_p_wrap.set_active(False)
_p_shown = _p_buffer.get_text(_p_buffer.get_start_iter(), _p_buffer.get_end_iter(), False)
check("clearing Wrap joins the lines, and the text box shows them joined",
      p_text.block is None and '\n' not in _p_shown, _p_shown)
pp.finish_edit()

# X and Y, on one element and on a group
pdoc.select(p_frame)
pp.sync()
p_frame.x += 10           # as a drag in progress would
pp.sync()
check("X follows the element as it is dragged",
      _pnamed('prop_x').get_value() == p_frame.x)
_pnamed('prop_x').set_value(150)
check("typing an X moves the element", p_frame.x == 150)
pp.finish_edit()

p_oval = pdoc.add_ellipse_element()
p_oval.x, p_oval.y = 400, 400
pdoc.select_many([p_frame, p_oval])
pdoc.group_selected()
pdoc.select(p_frame)
pp.sync()
check("a whole group is titled as one", pp._title.get_text() == "Group of 2",
      pp._title.get_text())
_p_before = [(e.x, e.y) for e in (p_frame, p_oval)]
_pnamed('prop_x').set_value(190)
check("X moves the whole group by the same amount",
      [(e.x, e.y) for e in (p_frame, p_oval)]
      == [(_p_before[0][0] + 40, _p_before[0][1]),
          (_p_before[1][0] + 40, _p_before[1][1])],
      [(e.x, e.y) for e in (p_frame, p_oval)])
pp.finish_edit()
check("the shared rows are what a frame and an ellipse both have",
      _pnamed('prop_thickness') is not None and _pnamed('prop_rounding') is None
      and _pnamed('properties_hint') is not None)
pdoc.ungroup_selected()

p_other = pdoc.add_frame_element()
p_frame.thickness, p_other.thickness = 2, 6
p_frame.print_enabled = False
pdoc.select_many([p_frame, p_other])
pw.on_canvas_changed()
pp.sync()
check("loose elements are counted, not called a group",
      pp._title.get_text() == "2 elements", pp._title.get_text())
check("a value they disagree on shows as Mixed",
      _pnamed('prop_thickness').get_text() == "Mixed",
      _pnamed('prop_thickness').get_text())
check("...and so does Print This Element, as a half-ticked box",
      _pnamed('prop_print_enabled').get_inconsistent())
_pd = _pdepth()
_pnamed('prop_thickness').set_value(4)
_pnamed('prop_print_enabled').set_active(True)
check("setting a shared row writes it to every element",
      (p_frame.thickness, p_other.thickness) == (4, 4)
      and p_frame.print_enabled and p_other.print_enabled)
pp.finish_edit()
check("...as one undo entry", _pdepth() == _pd + 1)

# A clock field's rows give way to plain text's
p_time = pdoc.add_time_element()
pw.design_canvas.queue_draw()
_ppump()
check("a clock field shows its own rows", pp._title.get_text() == "Time Field",
      pp._title.get_text())
[b for b in _pall(Gtk.CheckButton)
 if b.get_label() == "Comes from the printer's clock (^FC)"][0].set_active(False)
_ppump()
check("unticking the clock shows plain text's rows in their place",
      pp._title.get_text() == "Text" and pp._bound == (p_time,), pp._title.get_text())
pp.finish_edit()

# A change made elsewhere is read back in
pdoc.select(p_oval)
pp.sync()
p_oval.width = 333
pw.on_canvas_changed()
_ppump()
check("a change made on the canvas shows in the rows",
      _pall(Gtk.SpinButton)[2].get_value() == 333, _pall(Gtk.SpinButton)[2].get_value())

# An image's one setting is its file
p_image = _PImage(10, 10, 40, 40, _pil_image=_PILImage.new('RGB', (40, 40), (255, 255, 255)))
pdoc.elements.append(p_image)
pdoc.select(p_image)
pp.sync()
_p_replaced = []
pw.replace_image = lambda element: _p_replaced.append(element)
_pnamed('replace_image').clicked()
del pw.replace_image
check("an image shows Replace Image, which asks for the file of that image",
      pp._title.get_text() == "Image" and _p_replaced == [p_image],
      pp._title.get_text())

pdoc.select(p_text)
pw.on_delete_clicked(None)
pp.sync()
check("deleting the element empties the panel", pp._title.get_text() == "Nothing selected")

# Floating, it waits to be asked for
pp.set_floating(True)
pp.set_shown(False)
_ppump()
check("floated, it is a window of its own beside the designer",
      pp.floating and isinstance(pp.get_parent(), Gtk.Window)
      and pp.get_parent().get_transient_for() is pw)
check("closed, View > Properties Panel is unticked", not pw.panel_item.get_active())
pdoc.clear_selection()
pw.on_properties_clicked(None)
check("Properties with nothing selected leaves a hidden panel hidden", not pp.shown)
pdoc.select(p_oval)
pw.on_properties_clicked(None)
check("...and with a selection shows it, on that selection",
      pp.shown and pp._bound == (p_oval,) and pw.panel_item.get_active())
pp.set_shown(False)
pw.on_element_double_clicked(None, p_oval)
check("a double click shows it too", pp.shown)
pw.panel_item.set_active(False)
check("and View > Properties Panel hides it", not pp.shown)
pp.set_floating(False)
pw.panel_item.set_active(True)
_ppump()
check("docked again, it is back beside the canvas",
      pp.get_parent() is pw.paned and pp.shown)

# Floating it, docking it and reopening it closed show its window with
# show_all(), which shows every widget under it - the rows an editor keeps
# hidden too. They stay hidden: a Code 128 has none of the other
# symbologies' rows, and a field block no Block Height.
def _p_rows():
    """The labels of the editor rows on show, in order."""
    return [row.get_children()[0].get_text()
            for row in pp._form.content.get_children()
            if isinstance(row, Gtk.Box) and row.get_visible()
            and row.get_children()
            and isinstance(row.get_children()[0], Gtk.Label)]


p_code, p_block = pdoc.add_barcode_element(), pdoc.add_text_element()
pw.on_canvas_changed()
p_shown, p_depth = {}, _pdepth()
for p_kind, p_element in (('Code 128', p_code), ('text', p_block)):
    pdoc.select(p_element)
    pp.sync()
    _ppump()
    p_seen = [('docked', _p_rows())]
    pp.set_floating(True)
    _ppump()
    p_seen.append(('floating', _p_rows()))
    pp.set_shown(False)
    pp.set_shown(True)
    _ppump()
    p_seen.append(('reopened', _p_rows()))
    pp.set_floating(False)
    _ppump()
    p_seen.append(('docked again', _p_rows()))
    p_shown[p_kind] = p_seen
check("floated, reopened and docked again, the panel shows the rows it did "
      "docked, none of those its editor hides",
      all(rows == seen[0][1] for seen in p_shown.values() for _w, rows in seen)
      and 'Error Correction:' not in p_shown['Code 128'][0][1]
      and 'Block Height:' not in p_shown['text'][0][1],
      dict((kind, [(where, len(rows)) for where, rows in seen])
           for kind, seen in p_shown.items()))
check("and showing it so makes no edit", _pdepth() == p_depth and not pp._pending,
      (_pdepth() - p_depth, pp._pending))
for p_element in (p_code, p_block):
    pdoc.elements.remove(p_element)
pdoc.clear_selection()
pw.on_canvas_changed()
pp.sync()

# The right-click menu is popped up and left to the main loop, so it is caught
# on its way up and its Properties item activated by hand.
pdoc.select_many([p_frame, p_other])
pdoc.group_selected()
pdoc.clear_selection()
pdoc.select(p_other)                # what the right click itself does
pp.set_shown(False)
p_menus = []
p_real_popup = Gtk.Menu.popup_at_pointer
Gtk.Menu.popup_at_pointer = lambda menu, _event: p_menus.append(menu)
try:
    pw.design_canvas._show_context_menu(None, p_other)
finally:
    Gtk.Menu.popup_at_pointer = p_real_popup
p_items = [item for item in p_menus[0].get_children() if item.get_label()]
check("the right-click menu ends with Properties",
      [item.get_label() for item in p_items][-1:] == ["Properties"],
      [item.get_label() for item in p_items])
p_items[-1].activate()
check("...which shows the panel on the selection the right click made",
      pp.shown and pp._title.get_text() == "Group of 2", pp._title.get_text())
check("Edit > Properties is on Alt+Enter",
      pw.accelerators.get(pw.properties_item) == "<Alt>Return",
      pw.accelerators.get(pw.properties_item))
pdoc.clear_selection()
pw._update_edit_menu(None)
p_greyed = not pw.properties_item.get_sensitive()
pw._release_edit_menu(None)
check("the Edit menu greys Properties with nothing selected, and lets go of "
      "it once closed", p_greyed and pw.properties_item.get_sensitive())

# Where it was left is where it comes back - and floating, hidden
_p_saver = ZPLViewerWindow()
_p_saver.properties.set_floating(True)
_p_saver._save_settings()
_p_next = ZPLViewerWindow()
_ppump()
check("a panel left floating comes back floating, and hidden until asked for",
      _p_next.properties.floating and not _p_next.properties.shown)
_p_cfg = _pconfig.ConfigParser()
_p_cfg.read(gtk_main._config_path())
_p_cfg.remove_section('docks')
with open(gtk_main._config_path(), 'w') as _p_out:
    _p_cfg.write(_p_out)
_p_fresh = ZPLViewerWindow()
_ppump()
check("...and with nothing saved it is docked and shown",
      not _p_fresh.properties.floating and _p_fresh.properties.shown)
for _p_window in (_p_saver, _p_next, _p_fresh, pw):
    _p_window.destroy()
_ppump()

# --- a wrapped block paints its lines where it wraps them -------------------
# The conformance suite compares ZPL, and the ZPL for a block is right whether
# or not the canvas draws it wrapped - which is how a block came to print
# wrapped while the designer showed it as one line running off the label. So
# this measures the ink: it has to stay inside the block the element claims.

from zplcore.model import FieldBlock

block_el = document.add_text_element(
    "The quick brown fox jumps over the lazy dog again and again")
block_el.x, block_el.y = 20, 20
block_el.font_height, block_el.font_width = 30, 30
block_el.font_path, block_el.font_family = None, None   # the toy-font fallback
block_el.block = FieldBlock(300, 6, 0, 'L', 0)
document.sync_text_width(block_el)

surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 812, 400)
context = cairo.Context(surface)
context.set_source_rgb(1, 1, 1)
context.paint()
canvas._draw_text_element(context, block_el, False)

data, stride = surface.get_data(), surface.get_stride()
right = bottom = -1
for y in range(400):
    row = data[y * stride:(y + 1) * stride]
    for x in range(812):
        if row[4 * x] < 100 and row[4 * x + 1] < 100 and row[4 * x + 2] < 100:
            right, bottom = max(right, x), max(bottom, y)

# The glyphs may overhang the block slightly - the fallback face is stretched
# to the width the printer will use, not measured at it - but a line that did
# not wrap runs to the edge of the label, and a block drawn as one line is one
# line tall rather than six.
check("a block's ink stays within the wrap width",
      0 < right <= block_el.x + block_el.block.width + 20,
      f"ink reaches x={right}, block ends at {block_el.x + block_el.block.width}")
check("and fills the lines it wraps into, rather than drawing one of them",
      bottom > block_el.y + 3 * block_el.font_height,
      f"ink reaches y={bottom}, six lines end at "
      f"{block_el.y + 6 * block_el.font_height}")

document.elements.remove(block_el)

# --- a rotated fallback field still honours font_width -----------------------
# The toy-font fallback (^AF, i.e. no downloaded font) used to scale a field
# by its on-screen footprint width, which sync_text_width transposes with
# height at a quarter turn - so a rotated field's ink stopped growing with
# font_width and tracked its (untouched) footprint width, itself just
# font_height, instead.

def fallback_ink_height(font_width):
    fb_el = document.add_text_element("IIIIIIIIII")
    fb_el.font_path = fb_el.font_family = None
    fb_el.orientation = 'R'
    fb_el.font_height, fb_el.font_width = 30, font_width
    fb_el.x, fb_el.y = 20, 20
    document.sync_text_width(fb_el)
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 300, 300)
    ctx = cairo.Context(surface)
    ctx.set_source_rgb(1, 1, 1); ctx.paint()
    canvas._draw_text_element(ctx, fb_el, False)
    document.elements.remove(fb_el)
    data, stride = surface.get_data(), surface.get_stride()
    top = bottom = -1
    for y in range(300):
        row = data[y * stride:(y + 1) * stride]
        for x in range(300):
            if row[4 * x] < 100 and row[4 * x + 1] < 100 and row[4 * x + 2] < 100:
                if top == -1:
                    top = y
                bottom = y
                break
    return (bottom - top) if top != -1 else 0

narrow_height = fallback_ink_height(10)
wide_height = fallback_ink_height(60)
check("a rotated fallback field still stretches with font_width",
      wide_height > narrow_height * 1.5, (narrow_height, wide_height))

# --- ^FP: the direction and gap, in the editor and on the canvas ------------
# The Qt half is in test_core. Both halves have to draw the column the shared
# layout gives and write what the dialog was told.

fp_el = document.add_text_element("ABCD")
# The scalable font 0, whose rows are the height asked, which keeps the
# arithmetic plain; the bitmap fonts' own cells are test_core's
fp_el.font_code, fp_el.font_height, fp_el.font_width = '0', 30, 20
window.open_editor_window(fp_el)
fp_dialog = window._editors[id(fp_el)]
fp_combos = _find_all(_editor_body(fp_dialog), Gtk.ComboBoxText)
fp_spins = _find_all(_editor_body(fp_dialog), Gtk.SpinButton)
fp_wrap = [b for b in _find_all(_editor_body(fp_dialog), Gtk.CheckButton)
           if b.get_label() == "Wrap the text into a block"][0]
# Orientation, then Direction; Font Height, Font Width, then Character Gap
fp_combos[1].set_active(1)
fp_spins[2].set_value(7)
fp_offered = fp_combos[1].get_sensitive()
fp_wrap.set_active(True)
check("the direction is offered only while the text is not a block",
      fp_offered and not fp_combos[1].get_sensitive())
fp_wrap.set_active(False)
fp_dialog.response(Gtk.ResponseType.OK)
check("OK in Edit Text writes ^FP's direction and gap",
      (fp_el.direction, fp_el.char_gap) == ('V', 7)
      and '^FPV,7' in fp_el.to_zpl(),
      (fp_el.direction, fp_el.char_gap))
check("and the box follows them",
      fp_el.height == 4 * fp_el.font_height + 3 * 7, (fp_el.width, fp_el.height))

def fp_ink(font_path):
    """The bounds of the dark ink a column draws, or None."""
    fp_el.font_path = font_path
    fp_el.x, fp_el.y = 20, 20
    document.sync_text_width(fp_el)
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 300, 300)
    ctx = cairo.Context(surface)
    ctx.set_source_rgb(1, 1, 1); ctx.paint()
    canvas._draw_text_element(ctx, fp_el, False)
    data, stride = surface.get_data(), surface.get_stride()
    xs, ys = [], []
    for y in range(300):
        row = data[y * stride:(y + 1) * stride]
        for x in range(300):
            if row[4 * x] < 100 and row[4 * x + 1] < 100 and row[4 * x + 2] < 100:
                xs.append(x)
                ys.append(y)
    return (min(xs), min(ys), max(xs), max(ys)) if xs else None

from zplcore import fonts as zpl_fonts
STANDIN = zpl_fonts.resident_face('0')
# Without a font file, font 0 is drawn in its stand-in where that is
# installed, and in the toy font where it is not - which is how this machine
# is made to look for the second
for fp_path, fp_standin, fp_how in (
        (zpl_fonts.file_for_family('DejaVu Sans'), STANDIN, "with a font file"),
        (None, STANDIN, "without one"),
        (None, None, "without one or the stand-in")):
    zpl_fonts._resident_cache['0'] = fp_standin
    fp_box = fp_ink(fp_path)
    check(f"the GTK canvas draws a column down its box ({fp_how})",
          fp_box is not None and fp_box[3] - fp_box[1] > 3 * (30 + 7)
          and fp_box[2] <= fp_el.x + fp_el.width + 4, (fp_box, fp_el.width))
zpl_fonts._resident_cache['0'] = STANDIN
document.elements.remove(fp_el)

# --- ^TB: the Block choice in Edit Text --------------------------------------
# The Qt half is in test_core. The same element, the same choices, and the
# same block on OK.

from zplcore.model import FieldBlock, TextBlock
zpl_fonts._resident_cache['0'] = None
tb_el = document.add_text_element("aaa bbb ccc ddd")
tb_el.font_code, tb_el.font_height, tb_el.font_width = '0', 30, 20
document.sync_text_width(tb_el)
window.open_editor_window(tb_el)
tb_dialog = window._editors[id(tb_el)]
tb_content = _editor_body(tb_dialog)

def _tb_named(kind, name):
    return next(w for w in _find_all(tb_content, kind) if w.get_name() == name)

tb_kind = _tb_named(Gtk.ComboBoxText, 'block_kind')
tb_height = _tb_named(Gtk.SpinButton, 'block_height')
tb_justify = _tb_named(Gtk.ComboBoxText, 'text_justification')
# Freshly opened, before Wrap or Block is touched: _open_editor's show_all()
# must not leave both kinds' rows visible at once.
check("freshly opened, only the ^FB's rows show, not the ^TB's too",
      not tb_height.get_parent().get_visible()
      and not tb_justify.get_parent().get_visible())
tb_wrap = [b for b in _find_all(tb_content, Gtk.CheckButton)
           if b.get_label() == "Wrap the text into a block"][0]
tb_wrap.set_active(True)
check("a ^FB's rows show while the block is a ^FB",
      not tb_height.get_parent().get_visible()
      and not tb_justify.get_parent().get_visible())
tb_kind.set_active(1)
check("and a ^TB's, height and justification, once it is a ^TB",
      tb_height.get_parent().get_visible()
      and tb_justify.get_parent().get_visible())
# Wrap Width is the spin after the three above the wrap - Font Height, Font
# Width and Character Gap
[w for w in _find_all(tb_content, Gtk.SpinButton)][3].set_value(150)
tb_height.set_value(75)
tb_justify.set_active(1)
tb_dialog.response(Gtk.ResponseType.OK)
check("OK in Edit Text makes a ^TB, right justified by the field's z",
      tb_el.block == TextBlock(150, 75, 'R') and tb_el.justify == 1
      and "^TBN,150,75" in tb_el.to_zpl(), (tb_el.block, tb_el.justify))
check("and the box is the block",
      (tb_el.width, tb_el.height) == (150, 75), (tb_el.width, tb_el.height))
# A ^TB breaks no line at \&, so the dialog shows it as written, and a line
# break typed into one is a space
tb_el.text = r"aaa\&bbb"
window.open_editor_window(tb_el)
tb_dialog = window._editors[id(tb_el)]
tb_buffer = _find_all(_editor_body(tb_dialog), Gtk.TextView)[0].get_buffer()
check(r"a ^TB's \& is shown as written",
      tb_buffer.get_text(tb_buffer.get_start_iter(), tb_buffer.get_end_iter(),
                         False) == r"aaa\&bbb")
tb_buffer.set_text("top\nbottom")
tb_dialog.response(Gtk.ResponseType.OK)
check("and a line break typed into a ^TB is a space",
      tb_el.text == "top bottom" and isinstance(tb_el.block, TextBlock),
      tb_el.text)
window.open_editor_window(tb_el)
tb_dialog = window._editors[id(tb_el)]
tb_content = _editor_body(tb_dialog)
check("reopened, the dialog shows the ^TB it made",
      _tb_named(Gtk.ComboBoxText, 'block_kind').get_active() == 1
      and _tb_named(Gtk.SpinButton, 'block_height').get_value() == 75
      and _tb_named(Gtk.ComboBoxText, 'text_justification').get_active() == 1)
_tb_named(Gtk.ComboBoxText, 'block_kind').set_active(0)
tb_dialog.response(Gtk.ResponseType.OK)
check("and choosing a ^FB there makes it a ^FB of the same width",
      isinstance(tb_el.block, FieldBlock) and tb_el.block.width == 150,
      tb_el.block)
zpl_fonts._resident_cache['0'] = STANDIN
document.elements.remove(tb_el)

# --- Edit Time Field: the same font, direction and wrap rows ----------------
# The Qt half is in test_core. A clock field is styled like any other text, so
# its editor carries the rows Edit Text does, and OK writes the same block.

from zplcore import parser as zpl_parser
tf_el = document.add_time_element()
window.open_editor_window(tf_el)
tf_dialog = window._editors[id(tf_el)]
tf_content = _editor_body(tf_dialog)
tf_wrap = [b for b in _find_all(tf_content, Gtk.CheckButton)
           if b.get_label() == "Wrap the text into a block"]
tf_font = [b for b in _find_all(tf_content, Gtk.Button)
           if b.get_label() == "Choose\u2026"]
check("the time editor offers a font chooser and a wrap checkbox",
      len(tf_wrap) == 1 and len(tf_font) == 1)
tf_kind = next(w for w in _find_all(tf_content, Gtk.ComboBoxText)
               if w.get_name() == 'block_kind')
tf_height = next(w for w in _find_all(tf_content, Gtk.SpinButton)
                 if w.get_name() == 'block_height')
check("freshly opened, the wrap fields are greyed out and the ^TB's rows hidden",
      not tf_kind.get_sensitive() and not tf_height.get_parent().get_visible())
tf_wrap[0].set_active(True)
tf_spins = _find_all(tf_content, Gtk.SpinButton)
tf_spins[3].set_value(300)              # Wrap Width, after height/width/gap
# orientation, direction, block kind, then the ^FB's justification
_find_all(tf_content, Gtk.ComboBoxText)[3].set_active(1)
tf_dialog.response(Gtk.ResponseType.OK)
tf_zpl = tf_el.to_zpl()
check("OK writes a wrapped, centred clock field as ^FB before ^FC",
      isinstance(tf_el.block, FieldBlock) and tf_el.block.width == 300
      and tf_el.block.justification == 'C' and tf_el.clock_format
      and tf_zpl.index('^FB300') < tf_zpl.index('^FC')
      and '^FC%^FD%m/%d/%y^FS' in tf_zpl, tf_zpl)
tf_back = zpl_parser.parse_zpl(document.to_zpl())[0]
check("and it round-trips with its block",
      any(getattr(e, 'clock_format', False) and isinstance(e.block, FieldBlock)
          and e.block.width == 300 for e in tf_back.elements))
window.open_editor_window(tf_el)
tf_dialog = window._editors[id(tf_el)]
tf_wrap = [b for b in _find_all(_editor_body(tf_dialog), Gtk.CheckButton)
           if b.get_label() == "Wrap the text into a block"][0]
check("reopened, the time editor shows the block", tf_wrap.get_active())
tf_wrap.set_active(False)
tf_dialog.response(Gtk.ResponseType.OK)
check("unticking wrap removes the block and keeps the clock field",
      tf_el.block is None and tf_el.clock_format and tf_el.text == '%m/%d/%y')
document.elements.remove(tf_el)

# --- Edit Serial Field: the same font, direction and wrap rows --------------

sf_el = document.add_serial_element()
window.open_editor_window(sf_el)
sf_dialog = window._editors[id(sf_el)]
sf_content = _editor_body(sf_dialog)
sf_wrap = [b for b in _find_all(sf_content, Gtk.CheckButton)
           if b.get_label() == "Wrap the text into a block"]
sf_font = [b for b in _find_all(sf_content, Gtk.Button)
           if b.get_label() == "Choose\u2026"]
check("the serial editor offers a font chooser and a wrap checkbox",
      len(sf_wrap) == 1 and len(sf_font) == 1)
sf_gap = next(w for w in _find_all(sf_content, Gtk.SpinButton)
              if w.get_name() == 'char_gap')
sf_gap.set_value(7)
sf_dialog.response(Gtk.ResponseType.OK)
sf_zpl = sf_el.to_zpl()
check("OK writes the gap as ^FP and keeps the ^SN",
      sf_el.char_gap == 7 and '^FPH,7' in sf_zpl and '^SN1,1,N' in sf_zpl,
      sf_zpl)
document.elements.remove(sf_el)

# --- Edit Numbered Field: the same font, direction and wrap rows ------------

nf_el = document.add_numbered_element(7, 'Batch')
window.open_editor_window(nf_el)
nf_dialog = window._editors[id(nf_el)]
nf_content = _editor_body(nf_dialog)
nf_wrap = [b for b in _find_all(nf_content, Gtk.CheckButton)
           if b.get_label() == "Wrap the text into a block"]
nf_font = [b for b in _find_all(nf_content, Gtk.Button)
           if b.get_label() == "Choose\u2026"]
check("the numbered editor offers a font chooser and a wrap checkbox",
      len(nf_wrap) == 1 and len(nf_font) == 1)
nf_gap = next(w for w in _find_all(nf_content, Gtk.SpinButton)
              if w.get_name() == 'char_gap')
nf_gap.set_value(7)
nf_dialog.response(Gtk.ResponseType.OK)
nf_zpl = nf_el.to_zpl()
check("OK writes the gap as ^FP and keeps the ^FN",
      nf_el.char_gap == 7 and '^FPH,7' in nf_zpl and '^FN7"Batch"' in nf_zpl,
      nf_zpl)
document.elements.remove(nf_el)

# Every editor of an element that carries data has an ID row, for Label.fill.
for nm_label, nm_el in (
        ("text", document.add_text_element("T")),
        ("numbered", document.add_numbered_element(8, 'Lot'))):
    window.open_editor_window(nm_el)
    nm_dialog = window._editors[id(nm_el)]
    nm_row = _row_for(_editor_body(nm_dialog), "ID:")
    check(f"the {nm_label} editor has an ID row", nm_row is not None)
    if nm_row is not None:
        next(w for w in nm_row.get_children()
             if isinstance(w, Gtk.Entry)).set_text('n_' + nm_label)
    nm_dialog.response(Gtk.ResponseType.OK)
    check(f"the {nm_label} editor's ID row gives the element its ID",
          nm_el.element_id == 'n_' + nm_label, nm_el.comments)
    document.elements.remove(nm_el)


# --- font 0 is drawn in its stand-in ----------------------------------------
# The Qt half is in test_core. With the stand-in installed a font 0 field has
# a face, so the canvas draws it with the shared raster, its H standing where
# the preview's does, within the canvas's own two-dot margin; the toy font it
# used to be drawn in stands just above the foot of the cell, 8 dots lower.

if STANDIN:
    from zplcore.renderer import ZPLRenderer
    f0_el = document.add_text_element("HHHH")
    f0_el.font_code, f0_el.font_height, f0_el.font_width = '0', 40, 40
    f0_el.font_path = f0_el.font_family = None
    f0_el.x, f0_el.y = 50, 50
    document.sync_text_width(f0_el)
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 300, 200)
    ctx = cairo.Context(surface)
    ctx.set_source_rgb(1, 1, 1); ctx.paint()
    canvas._draw_text_element(ctx, f0_el, False)
    document.elements.remove(f0_el)
    data, stride = surface.get_data(), surface.get_stride()
    f0_bottom = max((y for y in range(200) for x in range(300)
                     if max(data[y * stride + 4 * x:y * stride + 4 * x + 3])
                     < 100), default=None)
    preview = ZPLRenderer(300, 200).render(
        "^XA^PW300^LL200^FO50,50^A0N,40,40^FDHHHH^FS^XZ").convert('L')
    p_bottom = max(y for y in range(200) for x in range(300)
                   if preview.getpixel((x, y)) < 100)
    check("the GTK canvas draws font 0 in the stand-in, standing where the "
          "preview's does",
          f0_bottom is not None and abs(f0_bottom - p_bottom) <= 2,
          (f0_bottom, p_bottom))
else:
    print("SKIPPED: font 0's stand-in is not installed "
          "(apt install fonts-urw-base35) - the GTK canvas's is not checked")

# --- a field whose ^FT leaves a coordinate out follows as the canvas paints -
# The Qt half is in test_core. Whatever edits the field before it, the
# follower catches up in on_draw, where the printer will put it.

from zplcore import parser as zpl_parser
chain_doc = zpl_parser.parse_zpl("^XA^FT10,200^A0N,30,20^FDACME ^FS"
                                 "^FT^A0N,30,20^FDSummer ^FS^XZ")[0]
lead, follower = chain_doc.elements
canvas.set_document(chain_doc)
lead.text = "ACME CORPORATION "
chain_doc.sync_text_width(lead)
canvas.on_draw(canvas, cairo.Context(
    cairo.ImageSurface(cairo.FORMAT_ARGB32, 400, 400)))
canvas.set_document(document)
check("the GTK canvas strings a follower along as it paints",
      geometry.typeset_point(follower) == (lead.x + lead.width, 200),
      (geometry.typeset_point(follower), lead.x + lead.width))

# --- the resolution a rescale is measured against ---------------------------
# Called with no argument, _offer_dpi_rescale is settling the open design
# against a resolution that changed underneath it, so the design's own dpi is
# what it scales from. Defaulting to the "file recorded nothing, assume 203"
# branch instead is silent: it scales by 600/203 where it should scale by
# 600/300, and the label merely prints the wrong size. Qt has always passed the
# document; this is the GTK half, which the conformance suite cannot reach
# because it replaces this method wholesale.

from zplcore import workflow as _wf

asked = []
window.printer_dpi = 600
window.design_canvas.document.dpi = 300
window.design_canvas.document.add_text_element("scaled")

# reconcile_dpi rather than the prompt inside it: the real method builds a
# Gtk.MessageDialog, and the point here is which file_dpi reaches the core.
real_reconcile = _wf.reconcile_dpi
def _spy(document, printer_dpi, ask, file_dpi=_wf._FROM_DOCUMENT):
    return real_reconcile(document, printer_dpi,
                          lambda old, new, assumed, wi, hi: (
                              asked.append((old, new, assumed)) or 'keep'),
                          file_dpi=file_dpi)
_wf.reconcile_dpi = _spy
try:
    window._offer_dpi_rescale()          # the real method, with its real default
finally:
    _wf.reconcile_dpi = real_reconcile

check("a resolution change reads the design's own dpi, not an assumed 203",
      asked == [(300, 600, False)],
      f"{asked}, expected [(300, 600, False)]")

# A rescale on load moves every element, so it is an unsaved change. Clearing
# the flag for it threw the user's answer away on close and asked again on the
# next open - and the two frontends cleared it in the same place, which is why
# comparing their emitted ZPL could not see it.
import os as _os, tempfile as _tempfile
_tmp = _tempfile.mkdtemp()
_src = _os.path.join(_tmp, 'other_dpi.zpl')
open(_src, 'w').write(
    "^XA^PW600^LL400\n^FXDESIGNER_DPI:300\n^FO50,50^A0N,40,40^FDscaled^FS\n^XZ")
window.printer_dpi = 203

def _answer(reply):
    def patched(document, printer_dpi, ask, file_dpi=_wf._FROM_DOCUMENT):
        return real_reconcile(document, printer_dpi, lambda *a: reply,
                              file_dpi=file_dpi)
    return patched

try:
    _wf.reconcile_dpi = _answer('rescale')
    window.load_zpl_file(_src)
    check("gtk: a rescale on load is an unsaved change", window.unsaved_changes)
    _wf.reconcile_dpi = _answer('keep')
    window.load_zpl_file(_src)
    check("gtk: keeping the dots is not", not window.unsaved_changes)
finally:
    _wf.reconcile_dpi = real_reconcile
# Back to a blank document, which is what the checks after this one are about
window.printer_dpi = 203
window.unsaved_changes = False
window.on_new_clicked()

# --- a palette button dropped on the canvas lands centred where it was let go.
# GTK hands a drop over as a Gtk.SelectionData, which only a real drag can
# build; the canvas reads nothing from it but the bytes.
class _PaletteDrop:
    def __init__(self, kind):
        self.kind = kind

    def get_data(self):
        return self.kind.encode()


def _drop_on_canvas(kind, x, y):
    drop_canvas.on_drag_data_received(drop_canvas, None, x, y, _PaletteDrop(kind), 0, 0)
    while Gtk.events_pending():      # the element is added once the drag has finished
        Gtk.main_iteration()


from zplcore import view as _view
drop_canvas = window.design_canvas
drop_canvas.set_zoom(0.5)
aim = (drop_canvas.label_width // 4 * 2, drop_canvas.label_height // 4 * 2)
before_drop, undo_before = len(drop_canvas.elements), len(window._undo_stack)
_drop_on_canvas('frame', aim[0] / 2, aim[1] / 2)
dropped_frame = drop_canvas.elements[-1]
check("a frame dropped on the GTK canvas is centred where it was let go, in dots",
      len(drop_canvas.elements) == before_drop + 1
      and dropped_frame.element_type == 'frame'
      and drop_canvas.document.selected_element is dropped_frame
      and abs(dropped_frame.x + dropped_frame.width / 2 - aim[0]) <= 1
      and abs(dropped_frame.y + dropped_frame.height / 2 - aim[1]) <= 1,
      (aim, dropped_frame.element_type, dropped_frame.x, dropped_frame.y))
check("a drop on the GTK canvas is one undo entry",
      len(window._undo_stack) == min(undo_before + 1, window.UNDO_LIMIT),
      (undo_before, len(window._undo_stack)))
check("every GTK palette button can be dropped", sorted(window._palette) == sorted(
      ['text', 'time', 'serial', 'numbered', 'frame', 'circle', 'ellipse',
       'diagonal', 'barcode', 'image', 'stored_graphic']), sorted(window._palette))

# What a drag shows under the pointer: the element at the canvas's zoom, held
# at its centre, which is where a drop puts the pointer.
import math as _math
undo_before_picture = len(window._undo_stack)
count_before_picture = len(drop_canvas.elements)
picture_sizes = {}
for zoom in (0.5, 2.0):
    drop_canvas.set_zoom(zoom)
    for kind in ('text', 'frame', 'barcode'):
        shown = drop_canvas.document.drop_preview(kind, (0, 0))
        picture = drop_canvas.drag_picture(kind)
        width = _math.ceil(shown.width * zoom) + 4
        height = _math.ceil(shown.height * zoom) + 4
        picture_sizes[(zoom, kind)] = (
            (picture.get_width(), picture.get_height(), picture.get_device_offset()),
            (width, height, (-width / 2, -height / 2)))
check("the GTK drag picture is the element's box at the canvas's zoom, held at its centre",
      all(got == want for got, want in picture_sizes.values()), picture_sizes)
drop_canvas.set_zoom(0.5)
drawn = drop_canvas.drag_picture('frame')
check("and the element is drawn on it", max(bytes(drawn.get_data())[3::4]) > 0)
check("drawing it adds nothing to the design or its history",
      len(window._undo_stack) == undo_before_picture
      and len(drop_canvas.elements) == count_before_picture)
drop_canvas.set_fit(_view.FIT_LABEL)

window.destroy()

print()

# --- what the titlebar says -------------------------------------------------
# The header bar is the titlebar here, so it carries the title the user reads;
# the window's own title is what the task switcher shows. Both name the file.
_tmp = os.path.join(tempfile.mkdtemp(), 'titled.zpl')
check("a new document shows the program's name",
      window.get_title() == 'LinuxZPL' and window.header_bar.get_title() == 'LinuxZPL',
      f'{window.get_title()!r} / {window.header_bar.get_title()!r}')
window.save_zpl_file(_tmp, '^XA\n^FO10,10^A0N,30,30^FDtitled^FS\n^XZ')
check("saving names the file in both titles",
      window.get_title() == 'titled.zpl' and window.header_bar.get_title() == 'titled.zpl',
      f'{window.get_title()!r} / {window.header_bar.get_title()!r}')
window.load_zpl_file(_tmp)
check("loading names the file it opened",
      window.header_bar.get_title() == 'titled.zpl', window.header_bar.get_title())
window.unsaved_changes = False
window.on_new_clicked()
check("New goes back to the program's name",
      window.get_title() == 'LinuxZPL' and window.header_bar.get_title() == 'LinuxZPL',
      f'{window.get_title()!r} / {window.header_bar.get_title()!r}')

# --- printer config falls back to the project directory ---------------------
# Some Linux environments refuse writes under the user config directory
# outright. Stand that in with a *file* where the settings directory needs to
# go, so mkdir(parents=True, exist_ok=True) fails deterministically without
# touching real permission bits.
real_config_path = gtk_main._config_path
real_fallback_path = gtk_main._fallback_config_path
blocked_dir = Path(tempfile.mkdtemp()) / 'blocked'
blocked_dir.write_text('')
fallback_path = Path(tempfile.mkdtemp()) / 'settings.ini'
gtk_main._config_path = lambda: blocked_dir / 'settings.ini'
gtk_main._fallback_config_path = lambda: fallback_path
try:
    window._default_printer = (Connection.network('10.0.0.9', 9100),
                               window.printer_dpi, window.printer_font_device)
    window._save_settings()
    written = configparser.ConfigParser(); written.read(fallback_path)
    check("a settings file the user config directory won't take is written to the project fallback instead",
          written.has_section('printer') and written.get('printer', 'address') == '10.0.0.9',
          dict(written['printer']) if written.has_section('printer') else None)
    window.printer_connection = Connection.network(gtk_main.DEFAULT_PRINTER_ADDRESS, 9100)
    window._load_settings()
    check("and is read back from the fallback location",
          window.printer_connection == Connection.network('10.0.0.9', 9100),
          window.printer_connection)
finally:
    gtk_main._config_path = real_config_path
    gtk_main._fallback_config_path = real_fallback_path

# --- the extra font folder is a persisted global setting -------------------
extra_path = Path(tempfile.mkdtemp()) / 'settings.ini'
gtk_main._config_path = lambda: extra_path
gtk_main._fallback_config_path = lambda: extra_path
try:
    gtk_main.zpl_fonts.set_extra_font_dir('/tmp/my-fonts')
    window._save_settings()
    gtk_main.zpl_fonts.set_extra_font_dir('')
    window._load_settings()
    check("the extra font folder round-trips through the settings file",
          gtk_main.zpl_fonts.EXTRA_FONT_DIR == '/tmp/my-fonts',
          gtk_main.zpl_fonts.EXTRA_FONT_DIR)
finally:
    gtk_main.zpl_fonts.set_extra_font_dir('')
    gtk_main._config_path = real_config_path
    gtk_main._fallback_config_path = real_fallback_path

# --- the Print window's printer never touches the persisted default --------
# The DPI is held fixed across the dialogs below so none of them takes the
# rescale-prompt path, which would otherwise open a real (blocking) dialog.
from zplcore.print_settings import PrintSettings as _PS

def _print_window_returns(printer, action='ok', settings=None):
    """Stand in for the Print window: accepted with this printer, and the
    document's own print settings unless others are given."""
    def fake(parent, document, current, default):
        return action, printer, settings or _PS.read(document)
    return fake

real_print_dialog = gtk_main._print_dialog
session_path = Path(tempfile.mkdtemp()) / 'settings.ini'
gtk_main._config_path = lambda: session_path
gtk_main._fallback_config_path = lambda: session_path
try:
    window.printer_connection, window.printer_dpi = Connection.network('192.168.1.50', 9100), 203
    window._default_printer = (window.printer_connection,
                               window.printer_dpi, window.printer_font_device)
    window._save_settings()

    real_dialog = gtk_main._printer_picker_dialog
    gtk_main._print_dialog = _print_window_returns((Connection.network('10.0.0.5', 9200), 203, 'E'))
    try:
        window.on_print_dialog_clicked(None)
    finally:
        gtk_main._print_dialog = real_print_dialog

    check("the Print window changes the printer in effect",
          window.printer_connection == Connection.network('10.0.0.5', 9200),
          window.printer_connection)

    written = configparser.ConfigParser(); written.read(session_path)
    check("but never writes it to the settings file",
          written.get('printer', 'address', fallback=None) == '192.168.1.50',
          dict(written['printer']) if written.has_section('printer') else None)

    # Default Printer must open on the persisted default, not the session
    # override just applied above - otherwise clicking OK on an unedited
    # dialog would silently promote the override into the new default.
    seen = {}
    def capture_dialog(parent, title, printer, dpi, font_device=None):
        seen['address'], seen['port'], seen['dpi'] = (printer.address,
                                                      printer.port, dpi)
        seen['font_device'] = font_device
        return None  # cancel, so nothing else about window state changes
    gtk_main._printer_picker_dialog = capture_dialog
    try:
        window.on_default_printer_clicked(None)
    finally:
        gtk_main._printer_picker_dialog = real_dialog
    check("Default Printer opens pre-filled with the persisted default, not the session override",
          (seen['address'], seen['port']) == ('192.168.1.50', 9100), seen)

    # Contrast: Default Printer, given the same dialog result, does persist.
    gtk_main._printer_picker_dialog = lambda *a, **k: (
        Connection.network('10.0.0.5', 9200), 203, 'E')
    try:
        window.on_default_printer_clicked(None)
    finally:
        gtk_main._printer_picker_dialog = real_dialog

    written = configparser.ConfigParser(); written.read(session_path)
    check("while Default Printer does persist the new address",
          written.get('printer', 'address', fallback=None) == '10.0.0.5',
          dict(written['printer']) if written.has_section('printer') else None)
finally:
    gtk_main._config_path = real_config_path
    gtk_main._fallback_config_path = real_fallback_path

# --- quitting must not promote an active session override to the default ---
# close_app calls _save_settings() only to persist window geometry, but that
# used to re-save whichever printer was active, silently adopting a session
# override as the new default the moment the app quit.
quit_path = Path(tempfile.mkdtemp()) / 'settings.ini'
gtk_main._config_path = lambda: quit_path
gtk_main._fallback_config_path = lambda: quit_path
try:
    window.printer_connection, window.printer_dpi = Connection.network('192.168.1.70', 9100), 203
    window._default_printer = (window.printer_connection,
                               window.printer_dpi, window.printer_font_device)
    window._save_settings()

    gtk_main._print_dialog = _print_window_returns((Connection.network('10.0.0.8', 9400), 203, 'E'))
    try:
        window.on_print_dialog_clicked(None)
    finally:
        gtk_main._print_dialog = real_print_dialog

    # Stand in for what close_app does: it never touches _default_printer,
    # it just calls _save_settings() again on the way out.
    window._save_settings()
    written = configparser.ConfigParser(); written.read(quit_path)
    check("quitting with a session override active does not promote it to the default",
          written.get('printer', 'address', fallback=None) == '192.168.1.70',
          dict(written['printer']) if written.has_section('printer') else None)
finally:
    gtk_main._config_path = real_config_path
    gtk_main._fallback_config_path = real_fallback_path

# --- Label Settings persists its own DPI, never a session-overridden address
label_settings_path = Path(tempfile.mkdtemp()) / 'settings.ini'
gtk_main._config_path = lambda: label_settings_path
gtk_main._fallback_config_path = lambda: label_settings_path
try:
    window.printer_connection, window.printer_dpi = Connection.network('192.168.1.80', 9100), 203
    window._default_printer = (window.printer_connection,
                               window.printer_dpi, window.printer_font_device)
    window._save_settings()

    gtk_main._print_dialog = _print_window_returns((Connection.network('10.0.0.9', 9500), 203, 'E'))
    try:
        window.on_print_dialog_clicked(None)
    finally:
        gtk_main._print_dialog = real_print_dialog

    # Take the Keep Dots branch without a real dialog, the same trick
    # conformance_driver.py uses.
    window._offer_dpi_rescale = \
        lambda loaded_dpi=_wf._FROM_DOCUMENT: _wf.reconcile_dpi(
            window.design_canvas.document, window.printer_dpi,
            lambda *a: 'keep', file_dpi=loaded_dpi)
    window.apply_label_settings(900, 600, 300, 3.0, 2.0)

    check("Label Settings updates the persisted default's DPI",
          window._default_printer[1] == 300, window._default_printer)
    check("but leaves the persisted default's address alone",
          window._default_printer[0].address == '192.168.1.80',
          window._default_printer)

    written = configparser.ConfigParser(); written.read(label_settings_path)
    check("the settings file reflects the new DPI but the original, non-overridden address",
          (written.get('printer', 'address', fallback=None),
           written.get('printer', 'dpi', fallback=None)) == ('192.168.1.80', '300'),
          dict(written['printer']) if written.has_section('printer') else None)
finally:
    gtk_main._config_path = real_config_path
    gtk_main._fallback_config_path = real_fallback_path

# --- the Print window -------------------------------------------------------
# Its rows filled in and read back through finish(), the half of the window
# that runs once dialog.run() has returned, so no nested main loop is needed.
import time as _pw_time
from zplcore.print_settings import AsWritten as _AW

_PW_PRINTER = (Connection.network('192.168.1.50', 9100), 203, 'E')
_PW_DEFAULT = (Connection.network('10.0.0.1', 9300), 203, 'R')
pw = ZPLViewerWindow()

def _pw_widgets(container):
    for child in container.get_children():
        yield child
        if isinstance(child, Gtk.Container):
            yield from _pw_widgets(child)

def _pw_named(dialog, name):
    return next(w for w in _pw_widgets(dialog) if w.get_name() == name)

def _pw_build(document):
    return gtk_main._build_print_dialog(pw, document, _PW_PRINTER, _PW_DEFAULT)

def _pw_select(dialog, name, text):
    combo = _pw_named(dialog, name)
    combo.set_active([row[0] for row in combo.get_model()].index(text))

_pw_doc = zpl_parser.parse_zpl(
    "^XA^PRC,D,A^MD8.25^MMU^MNM,30^MTD^PQ50,10,1,Y^POI^CVY"
    "^FO1,1^A0N,9,9^FDx^FS^XZ")[0]
_pw_dialog, _pw_finish = _pw_build(_pw_doc)
check("the Print window shows the label's settings as it carries them",
      (_pw_dialog.get_title(), _pw_named(_pw_dialog, 'quantity').get_value_as_int(),
       _pw_named(_pw_dialog, 'pause_count').get_value_as_int(),
       _pw_named(_pw_dialog, 'invert').get_active(),
       _pw_named(_pw_dialog, 'code_validation').get_active(),
       _pw_named(_pw_dialog, 'print_speed').get_active_text(),
       _pw_named(_pw_dialog, 'print_mode').get_active_text(),
       _pw_named(_pw_dialog, 'mark_offset').get_value_as_int(),
       _pw_named(_pw_dialog, 'mark_offset').get_sensitive())
      == ('Print', 50, 10, True, True, '4 in/s (C)', 'As written: U', 30, True))
_pw_notes = [w.get_text() for w in _pw_widgets(_pw_dialog)
             if isinstance(w, Gtk.Label) and w.get_text().startswith('As written')
             and w.get_visible()]
check("a darkness no box can show is noted beside it, as written",
      _pw_notes == ['As written: 8.25'], _pw_notes)
check("it opens on the printer in effect, with Print as the default button",
      _pw_named(_pw_dialog, 'address').get_text() == '192.168.1.50'
      and _pw_named(_pw_dialog, 'print').has_default())
_pw_plain = _pw_finish(gtk_main.PRINT_RESPONSE)
check("Print pressed unchanged gives 'print', the same printer and the same "
      "settings", _pw_plain[0] == 'print' and _pw_plain[1] == _PW_PRINTER
      and _pw_plain[2] == _PS.read(_pw_doc), _pw_plain)
check("and those settings, applied, change nothing",
      not _pw_plain[2].apply(_pw_doc))

for _pw_response in (Gtk.ResponseType.CANCEL, Gtk.ResponseType.DELETE_EVENT):
    _pw_dialog, _pw_finish = _pw_build(_pw_doc)
    check(f"{_pw_response.value_nick} gives None",
          _pw_finish(_pw_response) is None)

_pw_dialog, _pw_finish = _pw_build(_pw_doc)
_pw_named(_pw_dialog, 'quantity').set_value(5)
_pw_named(_pw_dialog, 'pause_count').set_value(0)
check("the pause row says what 0 means, which Qt shows as Never",
      'labels (0 = never)' in [w.get_text() for w in _pw_widgets(_pw_dialog)
                               if isinstance(w, Gtk.Label)])
_pw_named(_pw_dialog, 'mirror').set_active(True)
_pw_named(_pw_dialog, 'cut_on_error').set_active(False)
_pw_named(_pw_dialog, 'darkness').set_value(-12.5)
_pw_select(_pw_dialog, 'print_speed', '6 in/s (D)')
_pw_select(_pw_dialog, 'print_mode', 'Cutter (C)')
_pw_named(_pw_dialog, 'prepeel').set_active(True)
_pw_select(_pw_dialog, 'media_tracking', 'Non-continuous, web sensing (W)')
_pw_select(_pw_dialog, 'media_type', "Printer's setting")
_pw_edited = _pw_finish(Gtk.ResponseType.OK)
_pw_set = _pw_edited[2]
check("OK gives 'ok' and every row as edited",
      _pw_edited[0] == 'ok'
      and (_pw_set.quantity, _pw_set.pause_count, _pw_set.mirror,
           _pw_set.cut_on_error, _pw_set.darkness, _pw_set.print_speed,
           _pw_set.print_mode, _pw_set.prepeel, _pw_set.media_tracking,
           _pw_set.media_type)
      == (5, 0, True, False, -12.5, 6, 'C', True, 'W', None), _pw_set)
_pw_target = zpl_parser.parse_zpl(_pw_doc.to_zpl())[0]
_pw_set.apply(_pw_target)
check("and applied, rewrites what changed and keeps the slew and backfeed",
      list(_pw_target.media_settings.items())
      == [('^PR', '6,6,2'), ('^MD', '-12.5'), ('^MM', 'C,Y'), ('^MN', 'W')],
      _pw_target.media_settings)

_pw_dialog, _pw_finish = _pw_build(_pw_doc)
_pw_select(_pw_dialog, 'print_speed', "Printer's setting")
_pw_select(_pw_dialog, 'media_tracking', 'Continuous (N)')
_pw_named(_pw_dialog, 'darkness_set').set_active(False)
_pw_chain = {name: _pw_named(_pw_dialog, name).get_sensitive()
             for name in ('slew_speed', 'backfeed_speed', 'darkness',
                          'mark_offset')}
check("with no print speed, slew and backfeed are greyed out; so is the "
      "darkness box with its tick clear, and the offset off mark sensing",
      _pw_chain == dict.fromkeys(_pw_chain, False), _pw_chain)
_pw_cleared = _pw_finish(Gtk.ResponseType.OK)[2]
_pw_target = zpl_parser.parse_zpl(_pw_doc.to_zpl())[0]
_pw_cleared.apply(_pw_target)
check("and the printer's setting, applied, takes ^PR and ^MD out",
      list(_pw_target.media_settings.items())
      == [('^MM', 'U'), ('^MN', 'N'), ('^MT', 'D')], _pw_target.media_settings)

_pw_dialog, _pw_finish = _pw_build(_pw_doc)
_pw_named(_pw_dialog, 'use_default').clicked()
check("Use Default refills the printer from the persisted default",
      _pw_finish(Gtk.ResponseType.OK)[1] == _PW_DEFAULT)

# --- the printer rows: a network printer or a USB one ----------------------
# The USB printers present are stood in for, and the window's error dialog
# only records, so a refusal does not wait for a click.
import pty as _ur_pty, tty as _ur_tty, threading as _ur_threading
from zplcore import local_port as _ur_local, connection as _ur_conn
from zplcore import firmware as _ur_firmware
from gtkui.busy import BusyBar as _UrBusy

_ur_real = (_ur_local.list_printers, _ur_local.list_detached,
            pw.show_error_dialog)
_ur_present, _ur_loose, _ur_errors = [], [], []
_ur_local.list_printers = lambda *a: list(_ur_present)
_ur_local.list_detached = lambda *a: list(_ur_loose)
pw.show_error_dialog = _ur_errors.append
_UR_NAME = 'Zebra Technologies ZTC ZT410-203dpi ZPL'
_UR_ZEBRA = Connection.usb('18J181107685', _UR_NAME)
_UR_HERE = [_ur_local.UsbPrinter('/dev/usb/lp2', _UR_NAME, '18J181107685')]

def _ur_pump_until(done, seconds=5):
    until = _pw_time.monotonic() + seconds
    while not done() and _pw_time.monotonic() < until:
        while Gtk.events_pending():
            Gtk.main_iteration()
        _pw_time.sleep(0.01)
    while Gtk.events_pending():
        Gtk.main_iteration()

def _ur_busy_done(container):
    bars = [w for w in _pw_widgets(container) if isinstance(w, _UrBusy)]
    return lambda: not any(bar.running for bar in bars)

def _ur_shown(dialog):
    """Whether Address, Port, the USB Printer list and Refresh are shown:
    the widget and every box around it up to the dialog."""
    def shown(widget):
        while widget is not None and widget is not dialog:
            if not widget.get_visible():
                return False
            widget = widget.get_parent()
        return True
    return tuple(shown(_pw_named(dialog, name))
                 for name in ('address', 'port', 'usb_printer', 'usb_refresh'))

def _ur_build(printer, default=_PW_DEFAULT):
    return gtk_main._build_print_dialog(pw, _pw_doc, (printer, 203, 'E'), default)

try:
    _ur_present[:] = _UR_HERE
    _ur_dialog, _ur_finish = _ur_build(_PW_PRINTER[0])
    _ur_network_rows = _ur_shown(_ur_dialog)
    _pw_named(_ur_dialog, 'connection_usb').set_active(True)
    _ur_usb_rows = _ur_shown(_ur_dialog)
    _ur_listed = [row[0] for row in _pw_named(_ur_dialog, 'usb_printer').get_model()]
    _ur_chosen = _ur_finish(Gtk.ResponseType.OK)
    check("GTK: the printer rows show Address and Port for a network printer, "
          "and only the USB Printer list and Refresh once USB is chosen",
          _ur_network_rows == (True, True, False, False)
          and _ur_usb_rows == (False, False, True, True),
          (_ur_network_rows, _ur_usb_rows))
    check("GTK: the USB Printer list names each printer with its node",
          _ur_listed == [f'{_UR_NAME} (lp2)'], _ur_listed)
    check("GTK: choosing USB gives that printer, the network address carried",
          _ur_chosen[1] == (_PW_PRINTER[0].as_usb(_UR_ZEBRA.usb_serial, _UR_NAME),
                            203, 'E'), _ur_chosen[1])

    _ur_dialog, _ur_finish = _ur_build(
        _PW_PRINTER[0].as_usb(_UR_ZEBRA.usb_serial, _UR_NAME))
    _ur_usb_first = _ur_shown(_ur_dialog)
    _pw_named(_ur_dialog, 'connection_network').set_active(True)
    _ur_net = _ur_finish(Gtk.ResponseType.OK)
    check("GTK: a USB printer opens on the USB rows, and Network gives back "
          "the address it carried",
          _ur_usb_first == (False, False, True, True)
          and _ur_net[1] == (_PW_PRINTER[0].as_usb(_UR_ZEBRA.usb_serial, _UR_NAME)
                             .as_network('192.168.1.50', 9100), 203, 'E'),
          (_ur_usb_first, _ur_net))

    _ur_present[:] = []
    _ur_away = Connection.usb('OLD1', 'Zebra old')
    _ur_dialog, _ur_finish = _ur_build(_ur_away)
    _ur_text = _pw_named(_ur_dialog, 'usb_printer').get_active_text()
    _ur_kept = _ur_finish(Gtk.ResponseType.OK)
    check("GTK: a USB printer that is not plugged in stays chosen, said to be "
          "not connected", _ur_text == 'Zebra old (not connected)'
          and _ur_kept[1] == (_ur_away, 203, 'E'), (_ur_text, _ur_kept))

    _ur_dialog, _ur_finish = _ur_build(_PW_PRINTER[0])
    _pw_named(_ur_dialog, 'connection_usb').set_active(True)
    del _ur_errors[:]
    _ur_none = _ur_finish(Gtk.ResponseType.OK)
    check("GTK: USB with no USB printer to choose is refused, saying so",
          _ur_none is None and _ur_errors == [_ur_conn.NO_USB_CHOSEN],
          (_ur_none, _ur_errors))
    _ur_dialog, _ur_finish = _ur_build(Connection.network('', 9100))
    del _ur_errors[:]
    _ur_finish(Gtk.ResponseType.OK)
    check("GTK: and Network with no address, as before",
          _ur_errors == [_ur_conn.NO_ADDRESS], _ur_errors)

    _ur_present[:] = _UR_HERE
    _UR_DEFAULT = (Connection.network('10.0.0.1', 9300)
                   .as_usb(_UR_ZEBRA.usb_serial, _UR_NAME), 203, 'R')
    _ur_dialog, _ur_finish = _ur_build(_PW_PRINTER[0], _UR_DEFAULT)
    _pw_named(_ur_dialog, 'use_default').clicked()
    _ur_default_rows = _ur_shown(_ur_dialog)
    check("GTK: Use Default brings back a USB default printer, on the USB rows",
          _ur_finish(Gtk.ResponseType.OK)[1] == _UR_DEFAULT
          and _ur_default_rows == (False, False, True, True), _ur_default_rows)

    # Test Connection by USB: a pty plays the printer, answering ~HI.
    _ur_master, _ur_slave = _ur_pty.openpty()
    _ur_tty.setraw(_ur_slave)
    _ur_present[:] = [_ur_local.UsbPrinter(os.ttyname(_ur_slave), _UR_NAME,
                                           '18J181107685')]
    _ur_heard = []
    def _ur_answer():
        _ur_heard.append(os.read(_ur_master, 64))
        os.write(_ur_master, b'\x02ZT410-200dpi,V75.20.21,8,4096KB\x03\r\n')
    _ur_th = _ur_threading.Thread(target=_ur_answer, daemon=True)
    _ur_th.start()
    _ur_dialog, _ur_finish = gtk_main._build_print_dialog(
        pw, _pw_doc, (_UR_ZEBRA, 300, 'E'), _PW_DEFAULT)
    _pw_named(_ur_dialog, 'test_connection').clicked()
    _ur_pump_until(_ur_busy_done(_ur_dialog))
    _ur_th.join(3)
    _ur_result = _pw_named(_ur_dialog, 'test_result').get_text()
    _ur_finish(Gtk.ResponseType.CANCEL)
    os.close(_ur_master); os.close(_ur_slave)
    check("GTK: Test Connection opens a USB printer and asks its resolution",
          _ur_heard == [b'~HI']
          and _ur_result == f"✓ Connected to {_UR_NAME} (USB) — 203 dpi",
          (_ur_heard, _ur_result))

    _ur_present[:] = []
    _ur_loose[:] = [_ur_local.UsbPrinter('', _UR_NAME, '18J181107685',
                                         '3-1.1.2.1:1.0')]
    _ur_dialog, _ur_finish = _ur_build(_UR_ZEBRA)
    _pw_named(_ur_dialog, 'test_connection').clicked()
    _ur_pump_until(_ur_busy_done(_ur_dialog))
    _ur_result = _pw_named(_ur_dialog, 'test_result').get_text()
    _ur_finish(Gtk.ResponseType.CANCEL)
    check("GTK: and for one with no usblp driver, says how to give it back",
          _ur_result.startswith(f"✗ {_UR_NAME} (serial number 18J181107685) "
                                "is plugged in")
          and 'echo 3-1.1.2.1:1.0 | sudo tee' in _ur_result, _ur_result)
    _ur_loose[:] = []

    # The managers say what is wrong with a USB port, not "could not reach".
    _ur_said = {}
    _ur_run = Gtk.Dialog.run
    def _ur_read_status(dialog):
        _ur_pump_until(_ur_busy_done(dialog))
        _ur_said[dialog.get_title()] = [
            w.get_text() for w in _pw_widgets(dialog)
            if isinstance(w, Gtk.Label) and w.get_text().startswith(_UR_NAME)]
        return Gtk.ResponseType.CLOSE
    Gtk.Dialog.run = _ur_read_status
    _ur_was = pw.printer_connection
    pw.printer_connection = _UR_ZEBRA
    try:
        pw.on_printer_fonts_clicked(None)
        pw.on_printer_graphics_clicked(None)
        pw.on_printer_objects_clicked(None)
    finally:
        Gtk.Dialog.run = _ur_run
    _ur_message = f"{_UR_NAME} (serial number 18J181107685) is not connected by USB."
    check("GTK: the printer's managers say a USB printer is not connected, "
          "rather than that it could not be reached",
          sorted(_ur_said) == ['Printer Fonts', 'Printer Graphics',
                               'Printer Objects']
          and all(said == [_ur_message] for said in _ur_said.values()), _ur_said)

    # Firmware Update: the network printer first, then each USB printer by
    # path, opening on the session's USB printer wherever it is now.
    _ur_present[:] = [_ur_local.UsbPrinter('/dev/usb/lp1', 'Brother HL', ''),
                      _ur_local.UsbPrinter('/dev/usb/lp4', _UR_NAME,
                                           '18J181107685')]
    _ur_fw = {}
    def _ur_read_connection(dialog):
        combo = _pw_named(dialog, 'firmware_connection')
        _ur_fw[len(_ur_fw)] = ([row[0] for row in combo.get_model()],
                               combo.get_active_id())
        _ur_pump_until(_ur_busy_done(dialog))
        return Gtk.ResponseType.CLOSE
    _ur_ask = _ur_firmware.current_firmware
    _ur_firmware.current_firmware = lambda *a, **k: None
    Gtk.Dialog.run = _ur_read_connection
    try:
        pw.printer_connection = _PW_PRINTER[0].as_usb(_UR_ZEBRA.usb_serial,
                                                      _UR_NAME)
        pw.on_printer_firmware_clicked(None)
        pw.printer_connection = _PW_PRINTER[0]
        pw.on_printer_firmware_clicked(None)
    finally:
        Gtk.Dialog.run = _ur_run
        _ur_firmware.current_firmware = _ur_ask
        pw.printer_connection = _ur_was
    check("GTK: Firmware Update lists the network printer, then each USB "
          "printer by path",
          _ur_fw[0][0] == ['Network: 192.168.1.50:9100',
                           'USB: /dev/usb/lp1 (Brother HL)',
                           f'USB: /dev/usb/lp4 ({_UR_NAME})'], _ur_fw)
    check("GTK: and opens on the USB printer in use, and on the network for a "
          "network one",
          _ur_fw[0][1] == '/dev/usb/lp4' and _ur_fw[1][1] == '', _ur_fw)

    # The window: Quick Print goes down the USB printer's node, and says so.
    with tempfile.TemporaryDirectory() as _ur_dir:
        _ur_fifo = os.path.join(_ur_dir, 'lp2')
        os.mkfifo(_ur_fifo)
        _ur_present[:] = [_ur_local.UsbPrinter(_ur_fifo, _UR_NAME,
                                               '18J181107685')]
        _ur_got = []
        def _ur_read():
            with open(_ur_fifo, 'rb') as f:
                _ur_got.append(f.read())
        _ur_reader = _ur_threading.Thread(target=_ur_read, daemon=True)
        _ur_reader.start()
        uw = ZPLViewerWindow()
        uw.design_canvas.add_frame_element()
        uw.printer_connection = _UR_ZEBRA
        uw.quick_print_item.activate()
        _ur_pump_until(lambda: _ur_got and not uw._busy.running)
        _ur_reader.join(3)
        _ur_status = uw.status_bar.get_message_area().get_children()[0].get_text()
        check("GTK: Quick Print sends the label to a USB printer, and says where",
              _ur_got and b'^GB' in _ur_got[0]
              and _ur_status == f"Sent to {_UR_NAME} (USB)",
              (_ur_status, _ur_got[:1]))
        uw.unsaved_changes = False
        uw.destroy()
finally:
    (_ur_local.list_printers, _ur_local.list_detached,
     pw.show_error_dialog) = _ur_real

# Label Settings has lost Copies and the flips, and keeps the home. Its
# dialog is run modally, so run() is stood in for while it is looked at.
_ls_seen = {}
def _ls_peek(dialog):
    widgets = list(_pw_widgets(dialog))
    _ls_seen['labels'] = [w.get_text() for w in widgets
                          if isinstance(w, Gtk.Label)]
    _ls_seen['checks'] = [w.get_label() for w in widgets
                          if isinstance(w, Gtk.CheckButton)]
    return Gtk.ResponseType.CANCEL
_ls_real_run = Gtk.Dialog.run
Gtk.Dialog.run = _ls_peek
try:
    pw.on_label_settings_clicked(None)
finally:
    Gtk.Dialog.run = _ls_real_run
check("Label Settings no longer has Copies or ^PO/^PM/^LR, but keeps Home",
      'Copies (^PQ):' not in _ls_seen['labels'] and not _ls_seen['checks']
      and 'Home X (dots):' in _ls_seen['labels'], _ls_seen)

# The window: one undo entry for a visit that changed the label, none for
# one that did not, and Print sends after the settings go on.
pw._save_settings = lambda *a: None
pw.printer_connection, pw.printer_dpi = Connection.network('192.168.1.50', 9100), 203
pw.design_canvas.document.dpi = 203
pw.design_canvas.add_frame_element()
pw.on_canvas_changed()
pw.unsaved_changes = False
_pw_undo = len(pw._undo_stack)
_pw_printed = []
pw.on_print_clicked = lambda _w: _pw_printed.append(
    pw.design_canvas.document.print_quantity)

gtk_main._print_dialog = _print_window_returns(_PW_PRINTER, 'print')
try:
    pw.on_print_dialog_clicked(None)
finally:
    gtk_main._print_dialog = real_print_dialog
check("Print with nothing changed records no undo entry and leaves the file "
      "saved", len(pw._undo_stack) == _pw_undo and not pw.unsaved_changes,
      (len(pw._undo_stack), pw.unsaved_changes))
check("and prints", _pw_printed == [1], _pw_printed)

_pw_more = _PS.read(pw.design_canvas.document)
_pw_more.quantity, _pw_more.darkness, _pw_more.invert = 3, 4.0, True
gtk_main._print_dialog = _print_window_returns(_PW_PRINTER, 'print', _pw_more)
try:
    pw.on_print_dialog_clicked(None)
finally:
    gtk_main._print_dialog = real_print_dialog
check("a visit that changes the label is one undo entry and unsaved",
      len(pw._undo_stack) == _pw_undo + 1 and pw.unsaved_changes,
      (len(pw._undo_stack), pw.unsaved_changes))
check("and the settings are on before it prints", _pw_printed == [1, 3],
      _pw_printed)
pw.on_undo()
_pw_back = pw.design_canvas.document
check("undo takes the visit back",
      (_pw_back.print_quantity, _pw_back.media_settings,
       _pw_back.transform.invert) == (1, {}, False),
      (_pw_back.print_quantity, _pw_back.media_settings))

gtk_main._print_dialog = lambda *a, **k: None
try:
    pw.on_print_dialog_clicked(None)
finally:
    gtk_main._print_dialog = real_print_dialog
check("Cancel changes nothing and prints nothing",
      _pw_printed == [1, 3] and pw.design_canvas.document.print_quantity == 1)

_pw_file = [i.get_label() for i in pw.print_item.get_parent().get_children()
            if isinstance(i, Gtk.MenuItem) and i.get_label()]
check("File has Print… and Quick Print, and no Printer Settings submenu",
      '_Print\u2026' in _pw_file and '_Quick Print' in _pw_file
      and not any('Printer Settings' in t.replace('_', '') for t in _pw_file),
      _pw_file)
check("Print… is Ctrl+P, Quick Print Ctrl+Shift+P",
      pw.accelerators.get(pw.print_item) == '<Control>p'
      and pw.accelerators.get(pw.quick_print_item) == '<Control><Shift>p')
check("and the busy row withholds both while a job is out",
      pw.print_item in pw._busy._blocked
      and pw.quick_print_item in pw._busy._blocked)

# Quick Print sends at once, with no window. A fresh window, since the one
# above has its on_print_clicked stood in for.
qw = ZPLViewerWindow()
qw.design_canvas.add_frame_element()
_qw_sent, _qw_windows = [], []
def _qw_send(address, port, payload, timeout, read_reply=False, cancel=None):
    _qw_sent.append(payload.decode('utf-8'))
_qw_real_send = gtk_main.printer_io.send
gtk_main.printer_io.send = _qw_send
gtk_main._print_dialog = lambda *a, **k: _qw_windows.append(1)
try:
    qw.quick_print_item.activate()
    _qw_until = _pw_time.monotonic() + 5
    while ((not _qw_sent or qw._busy.running)
           and _pw_time.monotonic() < _qw_until):
        while Gtk.events_pending():
            Gtk.main_iteration()
        _pw_time.sleep(0.01)
finally:
    gtk_main.printer_io.send = _qw_real_send
    gtk_main._print_dialog = real_print_dialog
check("Quick Print sends the label without opening the Print window",
      len(_qw_sent) == 1 and not _qw_windows
      and '^PON' in _qw_sent[0] and '^GB' in _qw_sent[0],
      (_qw_windows, _qw_sent))
qw.destroy()
pw.destroy()

# --- File > Open Recent through the GTK window --------------------------------
# A window of its own: the suite's first was destroyed further up, and a
# destroyed one has no File menu left to open.
recent_path = Path(tempfile.mkdtemp()) / 'settings.ini'
gtk_main._config_path = lambda: recent_path
gtk_main._fallback_config_path = lambda: recent_path
recent_window = ZPLViewerWindow()
recent_errors, recent_asked = [], []
recent_window.show_error_dialog = recent_errors.append
recent_window.check_unsaved_changes = lambda: recent_asked.append(1) or True
try:
    def recent_labels():
        return [item.get_label() for item in recent_window.recent_menu.get_children()]
    recent_window._refresh_recent()
    check("an empty Open Recent is greyed out and holds nothing",
          not recent_window.recent_item.get_sensitive() and recent_labels() == [], recent_labels())

    recent_dir = tempfile.mkdtemp()
    first = os.path.join(recent_dir, 'first_label.zpl')
    second = os.path.join(recent_dir, 'R&D.zpl')
    recent_window.save_zpl_file(first, '^XA\n^FO10,10^A0N,30,30^FDrecent^FS\n^XZ')
    recent_window.load_zpl_file(first)
    check("a file opened is listed first, numbered, its underscore kept out of the mnemonic",
          recent_labels()[0] == '_1 first__label.zpl' and recent_window.recent_item.get_sensitive(),
          recent_labels())
    check("its tooltip is the full path",
          recent_window.recent_menu.get_children()[0].get_tooltip_text() == first)
    recent_window.save_zpl_file(second, '^XA\n^FO10,10^A0N,30,30^FDrecent^FS\n^XZ')
    check("saving under a new name puts that one on top",
          [l for l in recent_labels() if l][:2] == ['_1 R&D.zpl', '_2 first__label.zpl']
          and recent_labels()[-1] == '_Clear List', recent_labels())

    recent_window.recent_menu.get_children()[1].activate()
    check("choosing an entry opens that file and moves it back to the top",
          recent_window.current_filepath == first and recent_labels()[0] == '_1 first__label.zpl',
          (recent_window.current_filepath, recent_labels()))

    elsewhere = os.path.join(recent_dir, 'elsewhere.zpl')
    gtk_main.workflow.save_recent([recent_path], [elsewhere, first, second])
    file_menu = recent_window.recent_item.get_parent()
    file_menu.show(); file_menu.hide()
    check("opening File reads the list again, so another window's files appear",
          recent_labels()[0] == '_1 elsewhere.zpl', recent_labels())

    os.remove(second)
    recent_asked.clear()
    [item for item in recent_window.recent_menu.get_children()
     if (item.get_label() or '').endswith('R&D.zpl')][0].activate()
    check("a file no longer there is taken off the list, with a message that says why",
          not any('R&D' in (l or '') for l in recent_labels())
          and recent_errors and 'no longer there' in recent_errors[-1],
          (recent_labels(), recent_errors))
    check("before anyone is asked about unsaved work", recent_asked == [])

    recent_window.recent_menu.get_children()[-1].activate()
    check("Clear List empties it and greys it out",
          recent_labels() == [] and not recent_window.recent_item.get_sensitive()
          and gtk_main.workflow.load_recent([recent_path]) == [])
finally:
    gtk_main._config_path = real_config_path
    gtk_main._fallback_config_path = real_fallback_path
    recent_window.destroy()

# --- gtkui.busy.BusyBar: a worker thread's result lands back on the main loop
import time
from gtkui.busy import BusyBar

bb_btn, bb_off = Gtk.Button(label="a"), Gtk.Button(label="b")
bb_off.set_sensitive(False)
bb_msgs, bb_got = [], []
bb = BusyBar((bb_btn, bb_off), bb_msgs.append)

def bb_work(cancel):
    bb.report("halfway")
    return 42

bb.run(bb_work, lambda r, e: bb_got.append((r, e)))
check("BusyBar.run(): shows the row and makes the blocked buttons insensitive while out",
      bb.running and bb.get_visible() and not bb_btn.get_sensitive())
t0 = time.monotonic()
while not bb_got and time.monotonic() - t0 < 3:
    while Gtk.events_pending():
        Gtk.main_iteration()
    time.sleep(0.01)
check("BusyBar.run(): the result comes back on the main loop", bb_got == [(42, None)], bb_got)
check("BusyBar.report(): progress text lands on the message target", bb_msgs == ['halfway'], bb_msgs)
check("BusyBar: afterwards the row hides and each button is restored to its prior state",
      not bb.running and not bb.get_visible() and bb_btn.get_sensitive()
      and not bb_off.get_sensitive())

# --- an editor's rows, apart from the window they are shown in --------------
# The Properties panel will show the same rows the editor windows do, so each
# editor is built into a box of its own and says when any row of it changes.

from gtkui import editors as gtk_editors
from zplcore.model import EllipseElement, BarcodeElement

ff_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
ff_oval = EllipseElement(10, 10, 200, 150, 2)
ff = gtk_editors.ellipse_form(window, ff_box, ff_oval)
check("a form is built into a box of its own, with no window around it",
      ff.title == "Edit Ellipse" and ff.content is ff_box
      and ff_box.get_toplevel() is ff_box)
ff_heard = []
ff.on_change(lambda: ff_heard.append(1))
_find_all(ff_box, Gtk.SpinButton)[2].set_value(5)
check("a form reports a change to one of its inputs, once", ff_heard == [1], ff_heard)
check("...and leaves the element alone until it is applied", ff_oval.thickness == 2)
ff.apply()
check("applying the form writes its rows back", ff_oval.thickness == 5)

ff_text = document.add_text_element("words")
ff_tbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
ff_tf = gtk_editors.text_form(window, ff_tbox, ff_text)
ff_heard.clear()
ff_tf.on_change(lambda: ff_heard.append('text'))
_find_all(ff_tbox, Gtk.TextView)[0].get_buffer().set_text("other words")
# set_text() is a delete and an insert, two changes; a typed key is one
check("typing in Edit Text's text box is a change",
      ff_heard and set(ff_heard) == {'text'}, ff_heard)
ff_heard.clear()
next(b for b in _find_all(ff_tbox, Gtk.Button) if b.get_label() == "Clear").clicked()
check("clearing the font is a change, though no input shows it",
      ff_heard == ['text'], ff_heard)
document.elements.remove(ff_text)

ff_pdf = BarcodeElement(0, 0, 10, 'AB', symbology='pdf417')
ff_bbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
ff_bf = gtk_editors.barcode_form(window, ff_bbox, ff_pdf)
ff_bf.after_show()
ff_heard.clear()
ff_bf.on_change(lambda: ff_heard.append('barcode'))
_named_button(ff_bbox, 'add_position').clicked()
check("adding an ^FM position is a change", ff_heard == ['barcode'], ff_heard)
ff_heard.clear()
_named_button(ff_bbox, 'remove_position').clicked()
check("removing one is a change", ff_heard == ['barcode'], ff_heard)
# ZPL's own ranges in the editors: a 3 dot rule typed into a frame, and a
# bar 600 dots tall (2 inches at 300 dpi).
lim_frame = gtk_editors.FrameElement(10, 10, 400, 40, 2)
lim_fbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
lim_ff = gtk_editors.frame_form(window, lim_fbox, lim_frame)
lim_spins = {w.get_name(): w for w in _find_all(lim_fbox, Gtk.SpinButton)}
lim_spins['height'].set_value(3)
lim_thick = next(w for w in _find_all(lim_fbox, Gtk.SpinButton)
                 if w.get_name() not in ('width', 'height', 'rounding'))
lim_thick.set_value(2)
lim_ff.apply()
lim_bar = BarcodeElement(0, 0, 100, '1234')
lim_bbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
lim_bf = gtk_editors.barcode_form(window, lim_bbox, lim_bar)
lim_bf.after_show()
next(w for w in _find_all(lim_bbox, Gtk.SpinButton)
     if w.get_name() == 'bar_height').set_value(600)
lim_bf.apply()
check("GTK editors take ZPL's ranges: a frame typed 3 dots tall and solid, "
      "and a bar 600 dots tall",
      (lim_frame.height, lim_frame.thickness) == (3, 2)
      and lim_thick.get_adjustment().get_upper() == 2 and lim_bar.bar_height == 600,
      (lim_frame.height, lim_frame.thickness, lim_thick.get_adjustment().get_upper(),
       lim_bar.bar_height))

ff_maxi_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
ff_maxi = gtk_editors.barcode_form(
    window, ff_maxi_box, BarcodeElement(0, 0, 10, 'AB', symbology='maxicode'))
ff_heard.clear()
ff_maxi.on_change(lambda: ff_heard.append('insert'))
_named_button(ff_maxi_box, 'insert_GS').clicked()
check("an Insert button's escape is a change to the value",
      ff_heard == ['insert'], ff_heard)
check("each element finds the builder of its own rows",
      [gtk_editors.form_builder(e).__name__ for e in
       (document.add_time_element(), ff_pdf, ff_oval)]
      == ['time_form', 'barcode_form', 'ellipse_form'])
document.elements.pop()

# --- the grid: Show Grid and Grid Settings --------------------------------------
import cairo
from zplcore import grid as zpl_grid

grid_path = Path(tempfile.mkdtemp()) / 'settings.ini'
gtk_main._config_path = lambda: grid_path
gtk_main._fallback_config_path = lambda: grid_path
try:
    gw = ZPLViewerWindow()
    gc = gw.design_canvas
    check("the grid starts hidden, and Show Grid unticked",
          not gw.grid.shown and not gw.show_grid_item.get_active()
          and gc.grid is gw.grid)
    gc.set_zoom(1.0)

    def grid_pixel(x, y):
        """The canvas's colour at a pixel, as (r, g, b) out of 255."""
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, x + 1, y + 1)
        gc.on_draw(gc, cairo.Context(surface))
        surface.flush()
        stride, data = surface.get_stride(), surface.get_data()
        b, g, r = data[y * stride + 4 * x: y * stride + 4 * x + 3]
        return (r, g, b)

    WHITE = (255, 255, 255)
    check("hidden, nothing is drawn where a line would be",
          grid_pixel(20, 700) == WHITE, grid_pixel(20, 700))
    gw.show_grid_item.set_active(True)
    check("Show Grid draws a line every tenth of an inch",
          gw.grid.shown and grid_pixel(20, 700) != WHITE
          and grid_pixel(30, 700) == WHITE,
          (grid_pixel(20, 700), grid_pixel(30, 700)))
    minor, major = grid_pixel(20, 700), grid_pixel(203, 700)
    check("in pale blue, an inch line darker than a tenth",
          minor[2] > minor[0] and major[0] < minor[0], (minor, major))
    check("and is remembered at once", zpl_grid.load_grid([grid_path]).shown)
    check("Show Grid is on Ctrl+'",
          gw.accelerators.get(gw.show_grid_item) == "<Control>apostrophe")
    gw2 = ZPLViewerWindow()
    check("a new window opens with the grid shown and the item ticked",
          gw2.grid.shown and gw2.show_grid_item.get_active())
    gw2.destroy()

    def grid_named(dialog, name):
        return next(w for w in _pw_widgets(dialog) if w.get_name() == name)

    g_dialog, g_finish = gtk_main._build_grid_dialog(gw, gw.grid, 203)
    check("Grid Settings opens on the grid in force",
          (grid_named(g_dialog, 'grid_unit').get_active_id(),
           grid_named(g_dialog, 'grid_spacing').get_value()) == ('in', 0.1))
    check("with the spacing in dots beside it",
          grid_named(g_dialog, 'grid_hint').get_text() == '20.3 dots at 203 dpi',
          grid_named(g_dialog, 'grid_hint').get_text())
    grid_named(g_dialog, 'grid_unit').set_active_id('mm')
    check("switching to millimetres converts the spacing",
          grid_named(g_dialog, 'grid_spacing').get_value() == 2.54,
          grid_named(g_dialog, 'grid_spacing').get_value())
    grid_named(g_dialog, 'grid_spacing').set_value(5)
    grid_named(g_dialog, 'grid_major').set_value(2)
    chosen = g_finish(Gtk.ResponseType.OK)
    check("and OK hands back the spacing and the major lines",
          (chosen.unit, chosen.spacing, chosen.major_every) == ('mm', 5.0, 2)
          and chosen.shown, chosen)
    check("as a copy, leaving the window's grid until the window takes it",
          gw.grid.unit == 'in')
    g_dialog, g_finish = gtk_main._build_grid_dialog(gw, gw.grid, 203)
    grid_named(g_dialog, 'grid_spacing').set_value(0.5)
    check("Cancel hands back nothing", g_finish(Gtk.ResponseType.CANCEL) is None)

    real_grid_dialog = gtk_main._grid_dialog
    gtk_main._grid_dialog = lambda parent, grid, dpi: chosen
    try:
        gw.on_grid_settings_clicked(None)
    finally:
        gtk_main._grid_dialog = real_grid_dialog
    check("the window takes the chosen spacing into the grid it shares",
          gw.grid == chosen and gc.grid is gw.grid)
    check("and remembers it", zpl_grid.load_grid([grid_path]) == chosen)
    check("and draws it: at 203 dpi 5 mm is 40 dots",
          grid_pixel(40, 700) != WHITE and grid_pixel(20, 700) == WHITE)
    gw.destroy()
finally:
    gtk_main._config_path = real_config_path
    gtk_main._fallback_config_path = real_fallback_path

# --- Snap to Grid, and a drag through the canvas's own handlers ----------------
from gi.repository import Gdk

snap_path = Path(tempfile.mkdtemp()) / 'settings.ini'
gtk_main._config_path = lambda: snap_path
gtk_main._fallback_config_path = lambda: snap_path
try:
    sw = ZPLViewerWindow()
    sc = sw.design_canvas
    check("Snap to Grid starts off, and the document shares the window's grid",
          not sw.grid.snap and not sw.snap_grid_item.get_active()
          and sc.document.snap_grid is sw.grid)
    sw.snap_grid_item.set_active(True)
    check("Snap to Grid turns snapping on and is remembered at once",
          sw.grid.snap and zpl_grid.load_grid([snap_path]).snap)
    check("it is on Ctrl+Shift+', with Ctrl+Shift+\" for layouts that shift the quote",
          sw.accelerators.get(sw.snap_grid_item) == "<Control><Shift>apostrophe")
    from zplcore.model import Document as _Doc
    sc.set_document(_Doc(812, 1218, dpi=203))
    check("a new document shares the grid too", sc.document.snap_grid is sw.grid)

    class SnapEvent:
        def __init__(self, x, y, state=0, button=1):
            self.x, self.y, self.state, self.button = float(x), float(y), state, button

    class SnapKey:
        def __init__(self, keyval, kind, state=0):
            self.keyval, self.type, self.state = keyval, kind, state

    sc.set_zoom(1.0)
    sf = sc.add_frame_element()
    sf.x, sf.y = 102, 102
    heard = []
    sc.on_change_callback = lambda: heard.append('change')
    sc.on_button_press(sc, SnapEvent(112, 112))
    for k in range(1, 34):
        sc.on_motion(sc, SnapEvent(112 + k, 112 + k * 5 // 33))
    check("a drag through the canvas snaps, a dot at a time",
          (sf.x, sf.y) == (142, 102), (sf.x, sf.y))
    sc.on_motion(sc, SnapEvent(145, 117, Gdk.ModifierType.MOD1_MASK))
    check("Alt held drops the snap", (sf.x, sf.y) == (135, 107), (sf.x, sf.y))
    sc.on_key_release(sc, SnapKey(Gdk.KEY_Alt_L, Gdk.EventType.KEY_RELEASE,
                                  Gdk.ModifierType.MOD1_MASK))
    check("letting go of Alt snaps it again without the pointer moving",
          (sf.x, sf.y) == (142, 102), (sf.x, sf.y))
    sc.on_key_press(sc, SnapKey(Gdk.KEY_Shift_L, Gdk.EventType.KEY_PRESS))
    check("Shift going down holds it to the axis it has gone further along",
          (sf.x, sf.y) == (142, 102), (sf.x, sf.y))
    sc.on_motion(sc, SnapEvent(120, 140, Gdk.ModifierType.SHIFT_MASK))
    check("and Shift held through a move keeps it there",
          (sf.x, sf.y) == (102, 122), (sf.x, sf.y))
    sc.on_button_release(sc, SnapEvent(120, 140))
    check("the whole drag is one change", heard == ['change'], heard)

    # Snap to Elements, and the guides a drag shows while it lasts
    check("Snap to Elements starts off, with no shortcut",
          not sw.grid.snap_elements and not sw.snap_elements_item.get_active()
          and sw.snap_elements_item not in sw.accelerators)
    sw.snap_grid_item.set_active(False)
    sw.snap_elements_item.set_active(True)
    check("and is remembered once turned on",
          zpl_grid.load_grid([snap_path]).snap_elements)
    sc.set_document(_Doc(812, 1218, dpi=203))
    ea = sc.add_frame_element()
    ea.x, ea.y = 100, 100
    eb = sc.add_frame_element()
    eb.x, eb.y = 400, 300
    sc.last_click_element = None
    sc.on_button_press(sc, SnapEvent(500, 375))
    for k in range(1, 9):
        sc.on_motion(sc, SnapEvent(500 - 296 * k // 8, 375 - 20 * k // 8))
    check("a drag through the canvas snaps onto another element's edge",
          (eb.x, eb.y) == (100, 280), (eb.x, eb.y))
    check("with a guide for each edge and centre that line up, while it lasts",
          sc.guides == [(100.0, 100, 100.0, 430), (200.0, 100, 200.0, 430),
                        (300.0, 100, 300.0, 430)], sc.guides)
    surface =cairo.ImageSurface(cairo.FORMAT_ARGB32, 120, 300)
    sc.on_draw(sc, cairo.Context(surface))
    surface.flush()
    data, stride = surface.get_data(), surface.get_stride()
    b, g, r = data[270 * stride + 4 * 100: 270 * stride + 4 * 100 + 3]
    check("drawn in magenta, on the pixel Qt lights", (r, g, b) == (230, 0, 140),
          (r, g, b))
    sc.on_motion(sc, SnapEvent(204, 355, Gdk.ModifierType.MOD1_MASK))
    check("Alt held drops the snap and the guides",
          (eb.x, eb.y) == (104, 280) and sc.guides == [], ((eb.x, eb.y), sc.guides))
    sc.on_motion(sc, SnapEvent(204, 355))
    sc.on_button_release(sc, SnapEvent(204, 355))
    check("and the guides go when the drag ends", sc.guides == [] and eb.x == 100)
    sw.destroy()

    # View > Rulers, and the pointer readout in the status bar
    import time as _time

    def ruler_pump(seconds=0.5):
        end = _time.time() + seconds
        while _time.time() < end:
            while Gtk.events_pending():
                Gtk.main_iteration_do(False)
            _time.sleep(0.005)

    rw = ZPLViewerWindow()
    rw.resize(1000, 800)
    rw.show_all()
    ruler_pump()
    check("the rulers start hidden, on Ctrl+R",
          not rw.grid.rulers and not rw.rulers_item.get_active()
          and rw.accelerators.get(rw.rulers_item) == "<Control>r"
          and not rw.top_ruler.get_visible())
    view_before = rw.scrolled_canvas.get_allocation()
    rw.rulers_item.set_active(True)
    ruler_pump()
    view_after = rw.scrolled_canvas.get_allocation()
    check("Rulers shows them along the canvas's top and left edges",
          rw.top_ruler.get_visible() and rw.left_ruler.get_visible()
          and rw.ruler_corner.get_visible()
          and rw.top_ruler.get_allocated_height() == 20
          and rw.left_ruler.get_allocated_width() == 20)
    check("taking their room from the view, which a fit then measures",
          view_after.width == view_before.width - 20
          and view_after.height == view_before.height - 20,
          (view_before.width, view_before.height, view_after.width, view_after.height))
    check("they are remembered", zpl_grid.load_grid([snap_path]).rulers)
    rc = rw.design_canvas
    check("and the label is fitted again to the view they leave",
          rc.get_allocated_width() <= view_after.width
          and rc.get_allocated_height() <= view_after.height,
          (rc.get_allocated_width(), rc.get_allocated_height(),
           view_after.width, view_after.height))
    canvas_x, _ = rc.translate_coordinates(rw.top_ruler, 0, 0)
    _, canvas_y = rc.translate_coordinates(rw.left_ruler, 0, 0)
    check("each ruler's 0 is on the label's edge",
          rw.top_ruler.marks()[0] == (canvas_x, 0, '0')
          and rw.left_ruler.marks()[0] == (canvas_y, 0, '0'),
          (rw.top_ruler.marks()[:1], rw.left_ruler.marks()[:1], canvas_x, canvas_y))
    rc.set_zoom(2.0)
    hadjustment = rw.scrolled_canvas.get_hadjustment()
    for _ in range(40):           # until the scroll range has caught up
        ruler_pump(0.05)
        if hadjustment.get_upper() >= 1624:
            break
    check("a zoom still takes after the view has changed size under it",
          rc.get_allocated_width() == 1624, rc.get_allocated_width())
    hadjustment.set_value(300)
    ruler_pump()
    check("and stays there scrolled, the marks moving with the label",
          rw.top_ruler.origin() == -300
          and all(0 <= px <= rw.top_ruler.get_allocated_width()
                  for px, _, _ in rw.top_ruler.marks()),
          (rw.top_ruler.origin(), hadjustment.get_value()))
    rc.set_zoom(1.0)
    ruler_pump()

    check("the readout is blank with the pointer off the canvas",
          rw.pointer_label.get_text() == '')
    rc.on_motion(rc, SnapEvent(254, 162))
    check("and gives the pointer's place on the label, in the grid's unit",
          rw.pointer_label.get_text() == '1.251, 0.798 in', rw.pointer_label.get_text())
    check("which the rulers mark too",
          (rw.top_ruler.pointer, rw.left_ruler.pointer) == (254, 162))
    rw.grid.unit, rw.grid.spacing = 'mm', 1.0
    rc.on_motion(rc, SnapEvent(254, 162))
    check("in millimetres when the grid is",
          rw.pointer_label.get_text() == '31.8, 20.3 mm', rw.pointer_label.get_text())
    rc.on_leave(rc, None)
    check("and blank again once the pointer leaves",
          rw.pointer_label.get_text() == '' and rw.top_ruler.pointer is None)
    rw.rulers_item.set_active(False)
    ruler_pump()
    check("Rulers again hides them", not rw.top_ruler.get_visible())
    rw.destroy()
finally:
    gtk_main._config_path = real_config_path
    gtk_main._fallback_config_path = real_fallback_path

# --- a fit follows the view once GTK has finished laying out ------------------
# A fit resized the canvas from inside GTK's layout pass, where the resize
# was marked and never carried out: moving the Properties panel's divider
# left the canvas overhanging the view, and no zoom took after it until the
# window itself was resized.
import time as _fit_time

def fit_pump(seconds=0.5):
    end = _fit_time.time() + seconds
    while _fit_time.time() < end:
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)
        _fit_time.sleep(0.005)

fw = ZPLViewerWindow()
fw.resize(1000, 800)
fw.show_all()
fit_pump()
fc = fw.design_canvas
fw.paned.set_position(fw.paned.get_position() - 120)
fit_pump()
narrowed = fw.scrolled_canvas.get_allocation()
check("moving the panel's divider fits the label to the narrower view",
      fc.get_allocated_width() <= narrowed.width,
      (fc.get_allocated_width(), narrowed.width))
fc.set_zoom(2.0)
fit_pump()
check("and a zoom after it still takes", fc.get_allocated_width() == 1624,
      fc.get_allocated_width())
fw.destroy()

# Help > Contents, and what shows when no viewer could be started
from zplcore import helpdocs
real_launch, real_run = helpdocs.launch, Gtk.MessageDialog.run
asked, shown = [], []
try:
    class Viewer:
        """A viewer started: poll() gives its exit status, 0 once it handed on."""
        def __init__(self, code):
            self.code = code

        def poll(self):
            return self.code

    helpdocs.launch = lambda topic=helpdocs.CONTENTS: (
        asked.append(topic), helpdocs.Launch(Viewer(0), None))[1]
    Gtk.MessageDialog.run = lambda self: (shown.append(
        (self.get_title(), self.props.text, self.props.secondary_text)),
        Gtk.ResponseType.OK)[1]
    hw = ZPLViewerWindow()
    f1 = [item for item, accel in hw.accelerators.items() if accel == "F1"]
    for item in f1:
        item.activate()
    check("GTK Help > Contents opens the contents page, on F1, and shows "
          "nothing of its own when a viewer started",
          len(f1) == 1 and f1[0].get_label() == "_Contents"
          and asked == [helpdocs.CONTENTS] and shown == [], (asked, shown))
    helpdocs.launch = lambda topic=helpdocs.CONTENTS: helpdocs.Launch(
        None, "install yelp")
    hw.show_help(hw, "firmware-update")
    check("GTK: with no viewer, Help says so in a message box",
          shown == [("Help", "No help viewer", "install yelp")], shown)

    # xdg-open failing at once, with nothing after it in the list
    helpdocs.launch = lambda topic=helpdocs.CONTENTS, pages=helpdocs.PAGES, cache=None, start_after="": (
        helpdocs.Launch(None, f"nothing after {start_after}") if start_after
        else helpdocs.Launch(Viewer(3), None, topic, "xdg-open"))
    del shown[:]
    import time
    hw.show_help(hw, "firmware-update")
    until = time.monotonic() + 3
    while not shown and time.monotonic() < until:
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)
        time.sleep(0.02)
    check("GTK: a viewer that fails at once is caught, and what comes after it "
          "is shown",
          len(shown) == 1 and shown[0][2].startswith(
              "xdg-open could not show the help (exit status 3).")
          and shown[0][2].endswith("nothing after xdg-open"), shown)
    hw.destroy()
finally:
    helpdocs.launch, Gtk.MessageDialog.run = real_launch, real_run

# --- each window's Help: its own page, on a Help button and on F1 -----------
# Each window is looked at while run() would be waiting. F1 goes through the
# window's accelerators, as a key press does.
from gi.repository import Gdk
from zplcore import parser as zpl_parser
wh_asked, wh_seen = [], {}


def wh_press(key, window):
    """Press `window`'s Help button, then F1, and note what each opened,
    whether the button answered the dialog (which would end run()), and
    whether it sits at the left of its row."""
    button = next((w for w in _pw_widgets(window) if w.get_name() == "help"), None)
    responses = []
    if isinstance(window, Gtk.Dialog):
        window.connect("response", lambda _d, r: responses.append(r))
    del wh_asked[:]
    if button is not None:
        button.clicked()
    clicked = list(wh_asked)
    del wh_asked[:]
    # GTK runs an accelerator only for a widget on screen, as the window is
    # whenever F1 can be pressed in it; run() would have shown it.
    window.show()
    while Gtk.events_pending():
        Gtk.main_iteration_do(False)
    Gtk.accel_groups_activate(window, Gdk.KEY_F1, 0)
    row = button.get_parent() if button is not None else None
    at_left = (row.get_child_secondary(button) if isinstance(row, Gtk.ButtonBox)
               else row is not None and row.get_children()[0] is button)
    wh_seen[key] = (clicked, responses, list(wh_asked), at_left)


def wh_run(key, open_window, response=Gtk.ResponseType.CANCEL):
    """open_window() runs a dialog: press its Help instead of waiting, then
    answer it with `response`. Returns what open_window() returned. A
    message dialog's run() is stood in for apart: a check above left one
    of its own on that class."""
    def peek(dialog):
        wh_press(key, dialog)
        return response
    real, real_message = Gtk.Dialog.run, Gtk.MessageDialog.run
    Gtk.Dialog.run = Gtk.MessageDialog.run = peek
    try:
        return open_window()
    finally:
        Gtk.Dialog.run, Gtk.MessageDialog.run = real, real_message


wh_win = ZPLViewerWindow()
wh_win.show_help = lambda parent, topic: wh_asked.append(topic)
wh_doc = zpl_parser.parse_zpl("^XA^FO10,10^A0N,30,30^FDhi^FS^XZ")[0]
wh_run("label_settings", lambda: wh_win.on_label_settings_clicked(None))
wh_run("default_printer", lambda: wh_win.on_default_printer_clicked(None))
wh_run("local_fonts", lambda: wh_win.on_local_fonts_clicked(None))
for wh_key, wh_dialog in (
        ("grid_settings", gtk_main._build_grid_dialog(wh_win, wh_win.grid, 203)[0]),
        ("print", gtk_main._build_print_dialog(wh_win, wh_doc, _PW_PRINTER,
                                               _PW_DEFAULT)[0])):
    wh_press(wh_key, wh_dialog)
    wh_dialog.destroy()

# The printer windows, opened from a window whose printer is a port on this
# machine that nothing listens on, so what each asks on opening is refused
# at once and no real printer is reached.
import socket
wh_sock = socket.socket()
wh_sock.bind(("127.0.0.1", 0))
wh_win.printer_connection = Connection.network("127.0.0.1", wh_sock.getsockname()[1])
wh_sock.close()
wh_run("printer_fonts", lambda: wh_win.on_printer_fonts_clicked(None))
wh_run("printer_graphics", lambda: wh_win.on_printer_graphics_clicked(None))
wh_run("printer_objects", lambda: wh_win.on_printer_objects_clicked(None))
wh_run("firmware_update", lambda: wh_win.on_printer_firmware_clicked(None))
wh_run("upload_font_to", lambda: wh_win._ask_font_device(wh_win))
wh_run("store_graphic_as", lambda: wh_win._ask_device_spec(wh_win))
wh_run("store_object_as", lambda: wh_win._ask_object_name(wh_win, "NOTES", "TXT"))
wh_run("upload_template", lambda: wh_win._ask_template_name("E:", "LABEL1"))
for wh_key, wh_open, wh_attr in (
        ("printer_console", wh_win.on_printer_console_clicked, "printer_console_window"),
        ("printer_status", wh_win.on_printer_status_clicked, "printer_status_window")):
    wh_open(None)
    wh_window = getattr(wh_win, wh_attr)
    wh_press(wh_key, wh_window)
    wh_window.destroy()

# The two questions: Help leaves each running, and the answer then given is
# the one returned.
wh_answers = [
    wh_run("dpi_rescale", lambda: wh_win._ask_dpi_rescale(203, 300, False, 4.0, 6.0),
           Gtk.ResponseType.NO),
    wh_run("font_problem", lambda: wh_win._ask_font_problem(
        "The printer lacks a font.", "FOO.TTF", {"FOO.TTF": "/tmp/foo.ttf"}),
        Gtk.ResponseType.APPLY)]
check("GTK: after Help, each question still gives the answer clicked",
      wh_answers == ["keep", "upload"], wh_answers)

# The Properties panel's Help: the selected element's page, on its button
# and on F1 with the focus in the panel; F1 elsewhere is still the contents.
def wh_pump():
    while Gtk.events_pending():
        Gtk.main_iteration_do(False)


wp_win = ZPLViewerWindow()
wp_win.show_help = lambda parent, topic=helpdocs.CONTENTS: wh_asked.append(topic)
wp_win.show_all()
wh_pump()
wp_win.design_canvas.document.add_barcode_element()
wp_win.properties.request()
wh_pump()
wp_help = next(w for w in _pw_widgets(wp_win.properties) if w.get_name() == "help")
del wh_asked[:]
wp_help.clicked()
wp_clicked = list(wh_asked)
del wh_asked[:]
# The focus in one of its rows, as a click there puts it
next(w for w in _pw_widgets(wp_win.properties)
     if isinstance(w, Gtk.Entry) and w.get_mapped()).grab_focus()
wh_pump()
wp_in_panel = wp_win.properties.has_focus_within()
Gtk.accel_groups_activate(wp_win, Gdk.KEY_F1, 0)
wp_f1_panel = list(wh_asked)
del wh_asked[:]
wp_win.design_canvas.grab_focus()
wh_pump()
Gtk.accel_groups_activate(wp_win, Gdk.KEY_F1, 0)
wp_f1_canvas = list(wh_asked)
del wh_asked[:]
wp_win.design_canvas.document.clear_selection()
wp_help.clicked()
wp_none = list(wh_asked)
check("GTK: the panel's Help opens the selected element's page, and so does "
      "F1 in the panel; F1 on the canvas opens the contents; with nothing "
      "selected Help opens the panel's own page",
      wp_help.get_tooltip_text() is None and not wp_help.get_can_focus()
      and wp_clicked == ["barcode"] and wp_in_panel
      and wp_f1_panel == ["barcode"] and wp_f1_canvas == [helpdocs.CONTENTS]
      and wp_none == [helpdocs.PANEL],
      (wp_clicked, wp_in_panel, wp_f1_panel, wp_f1_canvas, wp_none))
wp_win.destroy()

for wh_key, wh_topic in sorted(helpdocs.WINDOW_HELP.items()):
    check(f"GTK {wh_key}: Help, at the left, opens {wh_topic} without ending "
          f"the window, and so does F1",
          wh_seen.get(wh_key) == ([wh_topic], [], [wh_topic], True),
          wh_seen.get(wh_key))
check("GTK: every window in the Help table was opened and tried",
      set(wh_seen) == set(helpdocs.WINDOW_HELP),
      sorted(set(helpdocs.WINDOW_HELP) ^ set(wh_seen)))
wh_win.destroy()

# --- tooltips: every one from zplcore.tips, on the field and on its label ----

from zplcore import tips as zpl_tips
from zplcore.model import Document as _TDocument


def _gtk_shown(widget, root):
    """Whether `widget` would be on screen with `root`: it and every
    container up to root are visible."""
    while widget is not None and widget is not root:
        if not widget.get_visible():
            return False
        widget = widget.get_parent()
    return True


def _gtk_field_of(label):
    """The field a label names: the widget after it in a row box, or beside
    it in a grid."""
    parent = label.get_parent()
    if isinstance(parent, Gtk.Grid):
        return parent.get_child_at(parent.child_get_property(label, 'left-attach') + 1,
                                   parent.child_get_property(label, 'top-attach'))
    if isinstance(parent, Gtk.Box):
        kids = parent.get_children()
        at = kids.index(label)
        return kids[at + 1] if at + 1 < len(kids) else None
    return None


def _gtk_label_of(widget):
    """The "Name:" label just before a field - in its row box, above it where
    an editor stacks them, or beside it in a grid - or None."""
    parent = widget.get_parent()
    before = None
    if isinstance(parent, Gtk.Grid):
        left = parent.child_get_property(widget, 'left-attach')
        if left:
            before = parent.get_child_at(left - 1,
                                         parent.child_get_property(widget, 'top-attach'))
    elif isinstance(parent, Gtk.Box):
        kids = parent.get_children()
        at = kids.index(widget)
        before = kids[at - 1] if at else None
    if isinstance(before, Gtk.Label) and before.get_text().endswith(':'):
        return before
    return None


def _gtk_tips(root, shown_only=False, expected=None):
    """{key: widget} for every widget under `root` that has a tooltip, and
    what doesn't fit the table: a tooltip on a widget it isn't the tip for,
    or a label that doesn't say what its field says - the field itself, or
    a box around it ("Pause every:" names a box of the number and its unit).
    `shown_only` passes over the rows a barcode's symbology hides, and
    `expected` gives the tip for a name the table keys some other way."""
    found, wrong = {}, []
    expected = expected or {}
    for widget in gtk_props._descendants(root):
        if shown_only and not _gtk_shown(widget, root):
            continue
        text = widget.get_tooltip_text()
        if isinstance(widget, Gtk.Label):
            if text:
                field = _gtk_field_of(widget)
                tips = set()
                if field is not None:
                    tips = {field.get_tooltip_text()} | {
                        d.get_tooltip_text() for d in gtk_props._descendants(field)}
                if text not in tips:
                    wrong.append(('label', widget.get_text()))
            continue
        if not text:
            continue
        name = widget.get_name()
        key = name[len('prop_'):] if name.startswith('prop_') else name
        if text != expected.get(key, zpl_tips.TIPS.get(key)):
            wrong.append(('widget', name, text[:40]))
            continue
        found[key] = widget
        label = _gtk_label_of(widget)
        if label is not None and label.get_tooltip_text() != text:
            wrong.append(('unlabelled', label.get_text(), name))
    return found, wrong


_tip_doc = _TDocument()
_tip_elements = {
    'text': _tip_doc.add_text_element(),
    'time': _tip_doc.add_time_element(),
    'serial': _tip_doc.add_serial_element(),
    'numbered': _tip_doc.add_numbered_element(),
    'frame': _tip_doc.add_frame_element(),
    'circle': _tip_doc.add_circle_element(),
    'ellipse': _tip_doc.add_ellipse_element(),
    'diagonal': _tip_doc.add_diagonal_element(),
    'symbol': _tip_doc.add_graphic_symbol_element(),
    'stored_graphic': _tip_doc.add_stored_graphic_element(),
}
# What each editor explains, grown section by section of the tooltip work;
# the Qt suite holds its editors to the same sets
_tip_text_rows = {'reverse_print', 'direction', 'char_gap', 'font', 'wrap',
                  'block_kind', 'block_width', 'block_height', 'max_lines',
                  'line_spacing', 'justification', 'text_justification',
                  'indent', 'font_width', 'orientation', 'element_id'}
_tip_fn_rows = {'variable', 'field_number', 'field_prompt'}
_tip_shape_rows = {'reverse_print', 'thickness', 'colour'}
_tip_expected = {
    'text': _tip_text_rows,
    'time': _tip_text_rows | {'clock_text', 'clock_format'},
    'serial': _tip_text_rows | {'serial_start', 'serial_increment',
                                'serial_leading_zero', 'serial_format'},
    'numbered': _tip_text_rows | _tip_fn_rows | {'numbered_text'},
    'frame': _tip_shape_rows | {'rounding'},
    'circle': _tip_shape_rows,
    'ellipse': _tip_shape_rows,
    'diagonal': _tip_shape_rows,
    'symbol': {'reverse_print', 'orientation', 'element_id'},
    'stored_graphic': {'command', 'device', 'name', 'extension',
                       'mag_x', 'mag_y'},
}

_tip_seen = set()
_tip_found, _tip_wrong, _tip_notes = {}, {}, {}
for _kind, _element in _tip_elements.items():
    _box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    gtk_editors.editor_form(window, _box, _element)
    _found, _wrong = _gtk_tips(_box)
    _tip_found[_kind] = set(_found)
    _tip_seen |= set(_found)
    if _wrong:
        _tip_wrong[_kind] = _wrong
    _notes = [l.get_text() for l in _find_all(_box, Gtk.Label)
              if l.get_text().startswith(("Inverts", "e.g. %m"))]
    if _notes:
        _tip_notes[_kind] = _notes
check("GTK: every tooltip in an element editor is its row's tip from "
      "zplcore.tips, on the field and on its label",
      not _tip_wrong, _tip_wrong)
check("GTK: each editor explains the rows it should",
      _tip_found == _tip_expected,
      dict((k, sorted(_tip_found[k] ^ _tip_expected[k])) for k in _tip_found
           if _tip_found[k] != _tip_expected[k]))
check("GTK: and no editor has a note under Reverse or the time format any more: "
      "they're tooltips",
      not _tip_notes, _tip_notes)

# A barcode's rows come and go with its symbology, and so do some of their
# tips: each symbology's own rows and its Check Digit say what they are for
# it. Only the rows it shows count; the Qt suite holds its editor to the same.
from zplcore import symbology as _tip_sym


def _tip_barcode(symbology):
    """What the barcode editor should explain for `symbology`: the keys,
    and the tips the table keys by symbology or label."""
    features = _tip_sym.BARCODE_FEATURES[symbology]
    keys = {'reverse_print', 'element_id'} | _tip_fn_rows
    keys |= {key for key, on in (
        ('orientation', features['orientation']),
        ('bar_height', features['height'] is not None),
        ('module_width', features['module_width'] is not None),
        ('ratio', features['ratio']), ('value_text', features['text']),
        ('check_digit', features['check_digit']), ('mode', features['mode']),
        ('positions', symbology in _tip_sym.SERIES)) if on}
    if features['control_chars']:
        keys |= {'insert'} | {f"insert_{label}"
                              for label, _code in _tip_sym.CONTROL_CHARACTERS}
    expected = dict((attribute, zpl_tips.PARAMETERS[(symbology, attribute)])
                    for attribute, _l, _c
                    in _tip_sym.BARCODE_PARAMETERS.get(symbology, ()))
    if features['check_digit']:
        expected['check_digit'] = zpl_tips.CHECK_DIGIT[features['check_digit']]
    return keys | set(expected), expected


def _tip_barcode_form(symbology):
    element = _tip_doc.add_barcode_element()
    element.symbology = symbology
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    form = gtk_editors.editor_form(window, box, element)
    # As a host shows it: every row, then the symbology's pass hides some
    box.show_all()
    form.after_show()
    return box


_tip_bc_wrong, _tip_bc_rows = {}, {}
for _symbology in _tip_sym.SYMBOLOGIES:
    _box = _tip_barcode_form(_symbology)
    _keys, _expected = _tip_barcode(_symbology)
    _found, _wrong = _gtk_tips(_box, shown_only=True, expected=_expected)
    _tip_seen |= set(_found)
    if _wrong:
        _tip_bc_wrong[_symbology] = _wrong
    if set(_found) != _keys:
        _tip_bc_rows[_symbology] = sorted(set(_found) ^ _keys)
check("GTK: every barcode symbology's shown rows carry their tips, its own "
      "rows and its Check Digit the ones for it",
      not _tip_bc_wrong and not _tip_bc_rows, (_tip_bc_wrong, _tip_bc_rows))

# One editor switched from symbology to symbology: a row two of them share
# (a Data Matrix's columns, a PDF417's) takes the new one's tip
_box = _tip_barcode_form('code128')
_bc_combo = _find_all(_box, Gtk.ComboBoxText)[0]
_bc_codes = [code for _l, code in zpl_model.BARCODE_SYMBOLOGIES]
_bc_switched = {}
for _symbology in ('datamatrix', 'pdf417', 'code39', 'upca'):
    _bc_combo.set_active(_bc_codes.index(_symbology))
    _keys, _expected = _tip_barcode(_symbology)
    _found, _wrong = _gtk_tips(_box, shown_only=True, expected=_expected)
    if _wrong or set(_found) != _keys:
        _bc_switched[_symbology] = (_wrong, sorted(set(_found) ^ _keys))
check("GTK: and switching one editor's symbology takes the tips along with it",
      not _bc_switched, _bc_switched)

tw = ZPLViewerWindow()
tw._save_settings = lambda *a: None
tdoc = tw.design_canvas.document
_t_frame = tdoc.add_frame_element()
tw.on_canvas_changed()
tw.properties.sync()
_t_found, _t_wrong = _gtk_tips(tw.properties)
check("GTK: the Properties panel's Print This Element row and its header's "
      "buttons have their tips",
      {'print_enabled', 'close_panel', 'float_panel'} <= set(_t_found)
      and not _t_wrong,
      (sorted(_t_found), _t_wrong))
_tip_seen |= set(_t_found)
tw.properties.set_floating(True)
check("GTK: floating, the float button's tip offers to dock it",
      tw.properties._float_button.get_tooltip_text() == zpl_tips.TIPS['dock_panel'],
      tw.properties._float_button.get_tooltip_text())
_tip_seen.add('dock_panel')
tw.properties.set_floating(False)
_t_text = tdoc.add_text_element()
tw.on_canvas_changed()
tdoc.select(_t_frame)
tdoc.select(_t_text, additive=True)
tw.properties.sync()
_t_found, _t_wrong = _gtk_tips(tw.properties)
_t_untipped = [w.get_name() for w in gtk_props._descendants(tw.properties)
               if w.get_name().startswith('prop_')
               and zpl_tips.tip(w.get_name()[len('prop_'):]) is None
               and w.get_tooltip_text()]
check("GTK: a selection of several: each shared row with a tip in the table "
      "has it, and the rest have none",
      {'print_enabled', 'reverse_print'} <= set(_t_found)
      and not _t_wrong and not _t_untipped,
      (sorted(_t_found), _t_wrong, _t_untipped))
_tip_seen |= set(_t_found)
# The shared rows say what the same row says in each element's own editor
_t_frame2, _t_text2 = tdoc.add_frame_element(), tdoc.add_text_element()
tw.on_canvas_changed()
_t_shared = {}
_t_codes = (tdoc.add_barcode_element(), tdoc.add_barcode_element())
tw.on_canvas_changed()
for _pair, _wanted in (((_t_frame, _t_frame2), {'thickness', 'colour', 'rounding'}),
                       ((_t_text, _t_text2), {'font_width', 'orientation'}),
                       (_t_codes, {'bar_height', 'module_width', 'orientation'})):
    tdoc.select(_pair[0])
    tdoc.select(_pair[1], additive=True)
    tw.properties.sync()
    _found, _wrong = _gtk_tips(tw.properties)
    _t_shared[type(_pair[0]).__name__] = (sorted(_wanted - set(_found)), _wrong)
    _tip_seen |= set(_found)
check("GTK: two frames', two texts' and two barcodes' shared rows carry "
      "their editors' tips",
      all(not missing and not wrong for missing, wrong in _t_shared.values()),
      _t_shared)
_t_window = {'add_symbol': tw.add_symbol_button, 'align': tw.align_button}
check("GTK: the + Symbol and Align buttons take theirs from the table",
      all(button.get_name() == key
          and button.get_tooltip_text() == zpl_tips.TIPS[key]
          for key, button in _t_window.items()),
      [(b.get_name(), b.get_tooltip_text()) for b in _t_window.values()])
_tip_seen |= set(_t_window)
_t_names = dict((w.get_name(), w) for w in gtk_props._descendants(tw))
_t_buttons = list(zpl_tips.PALETTE.values()) + ['undo', 'redo', 'zoom_out',
                                                'fit_label', 'zoom_in']
_t_untold = [key for key in _t_buttons
             if key not in _t_names
             or _t_names[key].get_tooltip_text() != zpl_tips.TIPS[key]]
check("GTK: the palette's + Time, + Serial, + Numbered, + Image and + Graphic, "
      "and undo, redo and the zoom buttons, say what they do",
      not _t_untold, _t_untold)
_tip_seen |= set(_t_buttons)

_t_dialog, _t_finish = gtk_main._build_print_dialog(
    tw, _pw_doc, _PW_PRINTER, _PW_DEFAULT)
_t_found, _t_wrong = _gtk_tips(_t_dialog)
_t_print_rows = {'pause_count', 'replicates', 'override_pause', 'cut_on_error',
                 'mirror', 'reverse', 'code_validation', 'darkness_set',
                 'darkness', 'print_speed', 'slew_speed', 'backfeed_speed',
                 'print_mode', 'prepeel', 'media_tracking', 'mark_offset',
                 'media_type', 'port', 'dpi', 'font_device', 'test_connection',
                 'use_default', 'usb_printer', 'usb_refresh'}
check("GTK: the Print window explains its rows from the table, each label too",
      set(_t_found) == _t_print_rows and not _t_wrong,
      (sorted(set(_t_found) ^ _t_print_rows), _t_wrong))
_tip_seen |= set(_t_found)
_t_dialog.destroy()

_g_dialog, _g_finish = gtk_main._build_grid_dialog(tw, tw.grid, 203)
_t_found, _t_wrong = _gtk_tips(_g_dialog)
_t_window_tips = {}
if set(_t_found) != {'grid_major'} or _t_wrong:
    _t_window_tips['Grid Settings'] = (sorted(_t_found), _t_wrong)
_tip_seen |= set(_t_found)
_g_dialog.destroy()

# The modal ones: run() is stood in for while each is looked at
_t_seen = {}


def _t_peek(dialog):
    found, wrong = _gtk_tips(dialog)
    _t_seen.update(found=set(found), wrong=wrong)
    return Gtk.ResponseType.CANCEL


from zplcore import local_port as zpl_local
_t_real_run, _t_real_ports = Gtk.Dialog.run, zpl_local.list_ports
_t_real_printer = tw.printer_connection
Gtk.Dialog.run = _t_peek
# The managers ask the printer as they open: a port nothing listens on
# answers at once, and closing drops the answer
zpl_local.list_ports = lambda: []
tw.printer_connection = Connection.network('127.0.0.1', 9)
try:
    for _title, _open, _wanted in (
            ("Label Settings", lambda: tw.on_label_settings_clicked(None),
             {'dpi', 'home_x', 'home_y'}),
            ("Default Printer", lambda: gtk_main._printer_picker_dialog(
                tw, "Default Printer", Connection.network('10.0.0.1', 9100), 203),
             {'port', 'dpi', 'font_device', 'test_connection', 'usb_printer',
              'usb_refresh'}),
            ("Store Graphic As", lambda: tw._ask_device_spec(tw), {'device'}),
            ("Upload Font To", lambda: tw._ask_font_device(tw), {'font_device'}),
            ("Store Object As", lambda: tw._ask_object_name(tw, 'X', 'DAT'),
             {'object_name'}),
            ("Graphics", lambda: tw.on_printer_graphics_clicked(None),
             {'store_graphic', 'retrieve_graphic'}),
            ("Objects", lambda: tw.on_printer_objects_clicked(None),
             {'store_object', 'retrieve_object', 'delete_object'}),
            ("Firmware", lambda: tw.on_printer_firmware_clicked(None),
             {'firmware_connection', 'firmware_refresh'}),
            ("Local Fonts", lambda: tw.on_local_fonts_clicked(None),
             {'extra_font_folder'})):
        _t_seen.clear()
        _open()
        _tip_seen |= _t_seen.get('found', set())
        if _t_seen.get('found') != _wanted or _t_seen.get('wrong'):
            _t_window_tips[_title] = (sorted(_t_seen.get('found', set()) ^ _wanted),
                                      _t_seen.get('wrong'))
finally:
    Gtk.Dialog.run, zpl_local.list_ports = _t_real_run, _t_real_ports
    tw.printer_connection = _t_real_printer
check("GTK: Label Settings, Grid Settings, Default Printer, the store windows "
      "and the printer's managers explain their rows from the table",
      not _t_window_tips, _t_window_tips)
tw.unsaved_changes = False
tw.destroy()

check("GTK: every tip in the table is shown somewhere in the GTK frontend",
      set(zpl_tips.TIPS) <= _tip_seen, sorted(set(zpl_tips.TIPS) - _tip_seen))

print("ALL GTK EDITOR CHECKS PASSED" if not fails
      else f"{len(fails)} FAILED: {fails}")
sys.exit(1 if fails else 0)
