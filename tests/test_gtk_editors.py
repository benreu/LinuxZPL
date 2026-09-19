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
    window._default_printer = ('10.0.0.9', window.printer_port, window.printer_dpi)
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
    window._default_printer = (window.printer_address, window.printer_port, window.printer_dpi)
    window._save_settings()

    real_dialog = gtk_main._printer_picker_dialog
    gtk_main._printer_picker_dialog = lambda *a, **k: ('10.0.0.5', 9200, 203)
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
    def capture_dialog(parent, title, address, port, dpi, default=None):
        seen['address'], seen['port'], seen['dpi'] = address, port, dpi
        return None  # cancel, so nothing else about window state changes
    gtk_main._printer_picker_dialog = capture_dialog
    try:
        window.on_default_printer_clicked(None)
    finally:
        gtk_main._printer_picker_dialog = real_dialog
    check("Default Printer opens pre-filled with the persisted default, not the session override",
          (seen['address'], seen['port']) == ('192.168.1.50', 9100), seen)

    # Contrast: Default Printer, given the same dialog result, does persist.
    gtk_main._printer_picker_dialog = lambda *a, **k: ('10.0.0.5', 9200, 203)
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
    window._default_printer = (window.printer_address, window.printer_port, window.printer_dpi)
    window._save_settings()

    real_dialog = gtk_main._printer_picker_dialog
    gtk_main._printer_picker_dialog = lambda *a, **k: ('10.0.0.8', 9400, 203)
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
    window._default_printer = (window.printer_address, window.printer_port, window.printer_dpi)
    window._save_settings()

    real_dialog = gtk_main._printer_picker_dialog
    gtk_main._printer_picker_dialog = lambda *a, **k: ('10.0.0.9', 9500, 203)
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
