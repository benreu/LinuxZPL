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
    window.printer_address = '10.0.0.9'
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

print("ALL GTK EDITOR CHECKS PASSED" if not fails
      else f"{len(fails)} FAILED: {fails}")
sys.exit(1 if fails else 0)
