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

window.on_element_double_clicked(None, text)
first = window._editors[id(text)]
check("an editor opens without blocking its caller", first.get_visible())
check("it is transient for the designer, so it floats above it",
      first.get_transient_for() is window)
check("and it is not modal, so the designer stays usable",
      not first.get_modal())

# --- one editor per element -------------------------------------------------

window.on_element_double_clicked(None, text)
check("a second double-click raises the open editor rather than opening another",
      window._editors[id(text)] is first and len(window._editors) == 1,
      len(window._editors))

window.on_element_double_clicked(None, frame)
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

window.on_element_double_clicked(None, frame)
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
    window.on_element_double_clicked(None, element)
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


def _spin_buttons(dialog):
    return [child for child in dialog.get_content_area().get_children()
            if isinstance(child, Gtk.SpinButton)]


circle = document.add_circle_element()
window.on_element_double_clicked(None, circle)
circle_dialog = window._editors[id(circle)]
diameter_spin, thickness_spin = _spin_buttons(circle_dialog)
diameter_spin.set_value(80)
thickness_spin.set_value(60)
circle_dialog.response(Gtk.ResponseType.OK)
check("OK in Edit Circle writes a round box and a thickness under the radius",
      (circle.diameter, circle.width, circle.height, circle.thickness)
      == (80, 80, 80, 40),
      (circle.diameter, circle.width, circle.height, circle.thickness))

window.on_element_double_clicked(None, circle)
circle_dialog = window._editors[id(circle)]
_spin_buttons(circle_dialog)[0].set_value(120)
circle_dialog.response(Gtk.ResponseType.CANCEL)
check("and Cancel leaves the circle alone", circle.diameter == 80, circle.diameter)
document.elements.remove(circle)

# --- Edit Diagonal Line: a thickness held to the width, and a direction -----


def _combos(dialog):
    return [child for child in dialog.get_content_area().get_children()
            if isinstance(child, Gtk.ComboBoxText)]


line = document.add_diagonal_element()
window.on_element_double_clicked(None, line)
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

window.on_element_double_clicked(None, line)
line_dialog = window._editors[id(line)]
_combos(line_dialog)[1].set_active(0)
line_dialog.response(Gtk.ResponseType.CANCEL)
check("and Cancel leaves the line alone", line.direction == 'L', line.direction)
document.elements.remove(line)

# --- Edit Ellipse: two sides, and a thickness under half the shorter --------

oval = document.add_ellipse_element()
window.on_element_double_clicked(None, oval)
oval_dialog = window._editors[id(oval)]
check("a double-click on an ellipse opens Edit Ellipse",
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

window.on_element_double_clicked(None, oval)
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

window.on_element_double_clicked(None, mark)
mark_dialog = window._editors[id(mark)]
check("a double-click on a symbol opens Edit Symbol",
      mark_dialog.get_title() == "Edit Symbol", mark_dialog.get_title())
symbol_combo, turn_combo = _find_all(mark_dialog.get_content_area(), Gtk.ComboBoxText)
mark_height, mark_width = _find_all(mark_dialog.get_content_area(), Gtk.SpinButton)
symbol_combo.set_active(1)
mark_height.set_value(60)
mark_width.set_value(30)
turn_combo.set_active(3)
_before = len(window._undo_stack)
mark_dialog.response(Gtk.ResponseType.OK)
check("OK in Edit Symbol writes the symbol, both sizes and the turn, as one "
      "undo entry",
      (mark.text, mark.font_height, mark.font_width, mark.orientation,
       mark.width, mark.height) == ('B', 60, 30, 'B', 60, 30)
      and len(window._undo_stack) == _before + 1,
      (mark.text, mark.font_height, mark.font_width, mark.orientation,
       mark.width, mark.height, len(window._undo_stack) - _before))

window.on_element_double_clicked(None, mark)
mark_dialog = window._editors[id(mark)]
_find_all(mark_dialog.get_content_area(), Gtk.ComboBoxText)[0].set_active(4)
_before = len(window._undo_stack)
mark_dialog.response(Gtk.ResponseType.CANCEL)
check("and Cancel leaves the symbol alone, recording nothing",
      mark.text == 'B' and len(window._undo_stack) == _before,
      (mark.text, len(window._undo_stack) - _before))
document.elements.remove(mark)

pair = GraphicSymbolElement(50, 50, 'AB', 5, 7000)
document.elements.append(pair)
window.on_element_double_clicked(None, pair)
pair_dialog = window._editors[id(pair)]
check("data that is not one of the five is offered first, as written",
      _find_all(pair_dialog.get_content_area(),
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
      not dark(56, 100) and dark(70, 100), (dark(56, 100), dark(70, 100)))

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
window.on_element_double_clicked(None, maxi)
maxi_dialog = window._editors[id(maxi)]
maxi_content = maxi_dialog.get_content_area()
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

window.on_element_double_clicked(None, maxi)
_cancelled = window._editors[id(maxi)]
_named_button(_cancelled.get_content_area(), 'insert_RS').clicked()
_cancelled.response(Gtk.ResponseType.CANCEL)
check("Cancel leaves the value as it was",
      maxi.barcode_value == 'A_5FB_1D_04', maxi.barcode_value)

plain = document.add_barcode_element()
window.on_element_double_clicked(None, plain)
plain_content = window._editors[id(plain)].get_content_area()
check("a Code 128 keeps its Orientation row and has no Insert",
      _row_for(plain_content, "Orientation:").get_visible()
      and not _row_for(plain_content, "Insert:").get_visible())
window._editors[id(plain)].response(Gtk.ResponseType.CANCEL)
document.elements.remove(maxi)
document.elements.remove(plain)

# --- the editors must not outlive the elements they hold --------------------
# Restoring a snapshot replaces every element object. An editor left on screen
# over one would write its fields into a copy the document no longer has, and
# the edit would vanish with nothing to show for it.

window.on_element_double_clicked(None, text)
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
window.on_element_double_clicked(None, live)
window.on_delete_clicked(None)
check("deleting an element closes the editor open on it",
      not window._editors, len(window._editors))

# --- and a group delete closes every editor it orphans ----------------------
# Delete takes the whole selection, so one editor left open over one of the
# deleted elements would be exactly the detached editor the rule above forbids.

pair = [document.add_text_element('one'), document.add_text_element('two')]
for element in pair:
    window.on_element_double_clicked(None, element)
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
window.on_element_double_clicked(None, fp_el)
fp_dialog = window._editors[id(fp_el)]
fp_combos = _find_all(fp_dialog.get_content_area(), Gtk.ComboBoxText)
fp_spins = _find_all(fp_dialog.get_content_area(), Gtk.SpinButton)
fp_wrap = [b for b in _find_all(fp_dialog.get_content_area(), Gtk.CheckButton)
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
    window._default_printer = ('10.0.0.9', window.printer_port,
                               window.printer_dpi, window.printer_font_device)
    window._save_settings()
    written = configparser.ConfigParser(); written.read(fallback_path)
    check("a settings file the user config directory won't take is written to the project fallback instead",
          written.has_section('printer') and written.get('printer', 'address') == '10.0.0.9',
          dict(written['printer']) if written.has_section('printer') else None)
    window.printer_address = gtk_main.DEFAULT_PRINTER_ADDRESS
    window._load_settings()
    check("and is read back from the fallback location",
          window.printer_address == '10.0.0.9', window.printer_address)
finally:
    gtk_main._config_path = real_config_path
    gtk_main._fallback_config_path = real_fallback_path

# --- Set Printer for This Session never touches the persisted default ------
# The DPI is held fixed across both dialogs below so neither one takes the
# rescale-prompt path, which would otherwise open a real (blocking) dialog.
session_path = Path(tempfile.mkdtemp()) / 'settings.ini'
gtk_main._config_path = lambda: session_path
gtk_main._fallback_config_path = lambda: session_path
try:
    window.printer_address, window.printer_port, window.printer_dpi = '192.168.1.50', 9100, 203
    window._default_printer = (window.printer_address, window.printer_port,
                               window.printer_dpi, window.printer_font_device)
    window._save_settings()

    real_dialog = gtk_main._printer_picker_dialog
    gtk_main._printer_picker_dialog = lambda *a, **k: ('10.0.0.5', 9200, 203, 'E')
    try:
        window.on_session_printer_clicked(None)
    finally:
        gtk_main._printer_picker_dialog = real_dialog

    check("Set Printer for This Session changes the printer in effect",
          (window.printer_address, window.printer_port) == ('10.0.0.5', 9200),
          (window.printer_address, window.printer_port))

    written = configparser.ConfigParser(); written.read(session_path)
    check("but never writes it to the settings file",
          written.get('printer', 'address', fallback=None) == '192.168.1.50',
          dict(written['printer']) if written.has_section('printer') else None)

    # Default Printer must open on the persisted default, not the session
    # override just applied above - otherwise clicking OK on an unedited
    # dialog would silently promote the override into the new default.
    seen = {}
    def capture_dialog(parent, title, address, port, dpi, font_device=None,
                       default=None):
        seen['address'], seen['port'], seen['dpi'] = address, port, dpi
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
    gtk_main._printer_picker_dialog = lambda *a, **k: ('10.0.0.5', 9200, 203, 'E')
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
    window.printer_address, window.printer_port, window.printer_dpi = '192.168.1.70', 9100, 203
    window._default_printer = (window.printer_address, window.printer_port,
                               window.printer_dpi, window.printer_font_device)
    window._save_settings()

    real_dialog = gtk_main._printer_picker_dialog
    gtk_main._printer_picker_dialog = lambda *a, **k: ('10.0.0.8', 9400, 203, 'E')
    try:
        window.on_session_printer_clicked(None)
    finally:
        gtk_main._printer_picker_dialog = real_dialog

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
    window.printer_address, window.printer_port, window.printer_dpi = '192.168.1.80', 9100, 203
    window._default_printer = (window.printer_address, window.printer_port,
                               window.printer_dpi, window.printer_font_device)
    window._save_settings()

    real_dialog = gtk_main._printer_picker_dialog
    gtk_main._printer_picker_dialog = lambda *a, **k: ('10.0.0.9', 9500, 203, 'E')
    try:
        window.on_session_printer_clicked(None)
    finally:
        gtk_main._printer_picker_dialog = real_dialog

    # Take the Keep Dots branch without a real dialog, the same trick
    # conformance_driver.py uses.
    window._offer_dpi_rescale = \
        lambda loaded_dpi=_wf._FROM_DOCUMENT: _wf.reconcile_dpi(
            window.design_canvas.document, window.printer_dpi,
            lambda *a: 'keep', file_dpi=loaded_dpi)
    window.apply_label_settings(900, 600, 300, 3.0, 2.0)

    check("Label Settings updates the persisted default's DPI",
          window._default_printer[2] == 300, window._default_printer)
    check("but leaves the persisted default's address alone",
          window._default_printer[0] == '192.168.1.80', window._default_printer)

    written = configparser.ConfigParser(); written.read(label_settings_path)
    check("the settings file reflects the new DPI but the original, non-overridden address",
          (written.get('printer', 'address', fallback=None),
           written.get('printer', 'dpi', fallback=None)) == ('192.168.1.80', '300'),
          dict(written['printer']) if written.has_section('printer') else None)
finally:
    gtk_main._config_path = real_config_path
    gtk_main._fallback_config_path = real_fallback_path

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

print("ALL GTK EDITOR CHECKS PASSED" if not fails
      else f"{len(fails)} FAILED: {fails}")
sys.exit(1 if fails else 0)
