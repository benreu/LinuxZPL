#!/usr/bin/env python3
"""
Run one scripted editing session against one frontend and print the ZPL it
produced at every step, as JSON.

Each frontend runs in its own process: GTK and Qt both want to be the one
toolkit in charge, and keeping them apart means a crash in one cannot be
mistaken for a disagreement between them. tests/test_conformance.py runs this
twice and diffs the results.

The sequence deliberately goes through each frontend's OWN code path - the GTK
canvas's element classes and resize math, the Qt canvas's core-backed ones - so
this measures agreement rather than assuming it.
"""

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
FIXTURE_300 = ROOT / 'tests' / 'fixtures' / 'sample_300dpi.zpl'

# A font every step can rely on; text width is the most divergence-prone rule,
# so the sequence exercises the measured path as well as the fixed-width one.
FONT_FAMILY = 'DejaVu Sans'


class GtkDriver:
    """The GTK frontend, through its own canvas and parser."""

    name = 'gtk'

    def __init__(self):
        import gi
        gi.require_version('Gtk', '3.0')
        from gtkui import window as gtk_window
        from zplcore import geometry, workflow
        self.geometry = geometry
        self.window = gtk_window.ZPLViewerWindow()
        self.window._save_settings = lambda *a: None
        self.window.printer_dpi = 203
        self.canvas = self.window.design_canvas
        self.canvas.dpi = 203
        # Take the Rescale branch without a dialog, matching what the Qt side
        # is told to do.
        self.window._offer_dpi_rescale = lambda loaded_dpi=None: workflow.reconcile_dpi(
            self.canvas.document, self.window.printer_dpi,
            lambda *a: 'rescale', file_dpi=loaded_dpi)

    # -- document ---------------------------------------------------------
    @property
    def elements(self):
        return self.canvas.elements

    def select(self, element):
        self.canvas.selected_element = element

    def add_text(self, text):
        return self.canvas.add_text_element(text)

    def add_frame(self):
        return self.canvas.add_frame_element()

    def add_barcode(self):
        return self.canvas.add_barcode_element()

    def set_font(self, element, path):
        element.font_path = path
        self.canvas.sync_text_width(element)

    def resize(self, element, handle, dx, dy):
        self.geometry.resize_by_handle(self.canvas.document, element, handle, dx, dy)

    def move(self, element, dx, dy):
        self.geometry.move_element(self.canvas.document, element, dx, dy)

    def handles(self, element):
        return self.geometry.handles(element)

    def bring_forward(self):
        self.canvas.bring_forward()

    def send_to_back(self):
        self.canvas.send_to_back()

    def set_label_size(self, w, h):
        self.canvas.set_label_size(w, h)

    def load(self, path):
        self.window.load_zpl_file(str(path))
        self.canvas = self.window.design_canvas

    def to_zpl(self):
        return self.canvas.to_zpl()

    def run(self, body):
        body(self)


class QtDriver:
    """The Qt frontend, through its canvas and zplcore."""

    name = 'qt'

    def __init__(self):
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
        from PySide2.QtWidgets import QApplication
        self.app = QApplication.instance() or QApplication([])
        from qtui import dialogs, window
        from zplcore import geometry
        self.geometry = geometry
        dialogs.ask_dpi_rescale = lambda *a, **k: 'rescale'
        self.window = window.ZPLDesignerWindow()
        self.window._save_settings = lambda *a: None
        self.window.printer_dpi = 203
        self.window.document.dpi = 203

    @property
    def document(self):
        return self.window.document

    @property
    def elements(self):
        return self.document.elements

    def select(self, element):
        self.document.selected_element = element

    def add_text(self, text):
        return self.document.add_text_element(text)

    def add_frame(self):
        return self.document.add_frame_element()

    def add_barcode(self):
        return self.document.add_barcode_element()

    def set_font(self, element, path):
        element.font_path = path
        self.document.sync_text_width(element)

    def resize(self, element, handle, dx, dy):
        self.geometry.resize_by_handle(self.document, element, handle, dx, dy)

    def move(self, element, dx, dy):
        self.geometry.move_element(self.document, element, dx, dy)

    def handles(self, element):
        return self.geometry.handles(element)

    def bring_forward(self):
        self.document.bring_forward()

    def send_to_back(self):
        self.document.send_to_back()

    def set_label_size(self, w, h):
        self.document.set_label_size(w, h)

    def load(self, path):
        self.window.unsaved_changes = False
        self.window.load_zpl_file(str(path))

    def to_zpl(self):
        return self.document.to_zpl()

    def run(self, body):
        body(self)


def sequence(driver, record):
    """The scripted session. Every frontend must produce the same ZPL for it."""
    from zplcore import fonts
    font_path = fonts.file_for_family(FONT_FAMILY)

    driver.set_label_size(812, 1218)
    record('empty 4x6 label')

    text = driver.add_text('Conformance')
    record('add text')

    if font_path:
        driver.set_font(text, font_path)
        record('text gets a measured TrueType width')

    frame = driver.add_frame()
    record('add frame')

    barcode = driver.add_barcode()
    record('add barcode')

    # drag, in label dots
    driver.select(text)
    driver.move(text, 40, 25)
    record('drag text')

    # drag hard against the edge, to exercise the clamp
    driver.move(text, -9999, -9999)
    record('drag text past the top left corner')

    # resize by every handle, so none of the eight can drift on its own
    driver.select(frame)
    for handle in ('br', 'tl', 'mr', 'bm', 'tr', 'bl', 'ml', 'tm'):
        driver.resize(frame, handle, 17, 11)
        record(f'resize frame by {handle}')

    # a thickness that should clamp to half the shorter side
    frame.thickness = 500
    driver.resize(frame, 'br', 0, 0)
    record('frame thickness clamped')

    # text resize, where font height and width are solved and the box snaps
    driver.select(text)
    driver.resize(text, 'br', 120, 18)
    record('resize text')

    # handle positions themselves
    record('handles: ' + json.dumps(
        {k: list(v) for k, v in sorted(driver.handles(frame).items())}))

    driver.select(barcode)
    barcode.barcode_value = 'CONFORM-1234'
    barcode.width = barcode.printed_width()
    record('barcode value changed')

    driver.select(frame)
    driver.bring_forward()
    record('bring frame forward')
    driver.send_to_back()
    record('send frame to back')

    text.print_enabled = False
    record('text marked not printing')

    driver.set_label_size(600, 900)
    record('label shrunk, elements clamped')

    driver.load(FIXTURE_300)
    record('load a 300dpi file at 203dpi, rescaled')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--frontend', choices=('gtk', 'qt'), required=True)
    args = ap.parse_args()

    driver = GtkDriver() if args.frontend == 'gtk' else QtDriver()
    steps = []

    def record(name):
        steps.append({'step': name, 'zpl': driver.to_zpl()})

    sequence(driver, record)
    json.dump({'frontend': driver.name, 'steps': steps}, sys.stdout)


if __name__ == '__main__':
    main()
