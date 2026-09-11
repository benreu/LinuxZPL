#!/usr/bin/env python3
"""
The GTK element editors.

The conformance suite drives the model directly and never opens an editor, and
the other two suites are Qt-only - so without this the GTK half of the editors
has no cover at all. What it checks is the shape they were given: non-modal
children of the designer, one per element, applying on OK.

Needs a display, like the conformance suite: run it under DISPLAY, or xvfb-run.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk

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

window.destroy()

print()
print("ALL GTK EDITOR CHECKS PASSED" if not fails
      else f"{len(fails)} FAILED: {fails}")
sys.exit(1 if fails else 0)
