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
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import _isolate  # a throwaway settings file, before any frontend is imported
from PIL import Image
from zplcore import graphic_store, transforms
FIXTURE_300 = ROOT / 'tests' / 'fixtures' / 'sample_300dpi.zpl'
# ZPL as another tool writes it: ^A0, ^FB and two commands on one line
FIXTURE_TEMPLATE = ROOT / 'tests' / 'fixtures' / 'product_barcode.zpl'
# A ^CF default font and a ^GB that names its colour and rounding
FIXTURE_DEFAULTS = ROOT / 'tests' / 'fixtures' / 'default_font.zpl'
# Fields placed by ^FT, whose y is a baseline rather than a top
FIXTURE_TYPESET = ROOT / 'tests' / 'fixtures' / 'typeset.zpl'
# ^GB rules, where the width or the height is left to default to the thickness
FIXTURE_RULES = ROOT / 'tests' / 'fixtures' / 'rules.zpl'
# ^A with its sizes left off, inherited from ^CF or from the font itself
FIXTURE_PARTIAL = ROOT / 'tests' / 'fixtures' / 'partial_font.zpl'
# The manual's own ^FW example, plus a ^CF field and a barcode that defer to
# it the same way.
FIXTURE_ORIENTED = ROOT / 'tests' / 'fixtures' / 'field_orientation.zpl'
# A logo in the encoding label software actually sends: :Z64: rather than the
# uncompressed hex this designer writes
FIXTURE_COMPRESSED = ROOT / 'tests' / 'fixtures' / 'compressed_logo.zpl'
# A stored format and the recall call that fills it in - the manual's own p51
# example, plus one whose fields name themselves and share a number.
FIXTURE_STORED = ROOT / 'tests' / 'fixtures' / 'stored_format.zpl'
FIXTURE_RECALL = ROOT / 'tests' / 'fixtures' / 'recall_format.zpl'
FIXTURE_NAMED = ROOT / 'tests' / 'fixtures' / 'named_fields.zpl'
# The graphic counterpart: ^IS saves a rendered snapshot, ^XG/^IM/^IL recall
# it - within this one process, so loading the save fixture first lets the
# recall/load fixtures resolve for real.
FIXTURE_GRAPHIC_SAVE = ROOT / 'tests' / 'fixtures' / 'stored_graphic_save.zpl'
FIXTURE_GRAPHIC_RECALL = ROOT / 'tests' / 'fixtures' / 'stored_graphic_recall.zpl'
FIXTURE_GRAPHIC_LOAD = ROOT / 'tests' / 'fixtures' / 'stored_graphic_load.zpl'
# The commands that move or flip a whole label
FIXTURE_HOME = ROOT / 'tests' / 'fixtures' / 'label_home.zpl'
FIXTURE_FLIPPED = ROOT / 'tests' / 'fixtures' / 'flipped_label.zpl'
# Fields whose ^FO names their right edge rather than their left
FIXTURE_JUSTIFIED = ROOT / 'tests' / 'fixtures' / 'justified.zpl'
# The manual's own ^CV example: a switch the printer acts on and nothing here
# draws, which both frontends still have to write back
FIXTURE_VALIDATED = ROOT / 'tests' / 'fixtures' / 'code_validation.zpl'
# The encoding and the printer's font table - ^CI28 over UTF-8 text, a ^CW
# and an ^FL - carried verbatim, and nothing here draws for them either
FIXTURE_FONT_IDENTITY = ROOT / 'tests' / 'fixtures' / 'font_identity.zpl'

# ^A@'s own d:f.x path, in the shapes the manual allows and this app does
# not write: another drive, a .FNT or .TTE, no drive at all, lower case.
FIXTURE_FONT_PATH = ROOT / 'tests' / 'fixtures' / 'font_path.zpl'

# A font every step can rely on; text width is the most divergence-prone rule,
# so the sequence exercises the measured path as well as the fixed-width one.
FONT_FAMILY = 'DejaVu Sans'


# --- comparing menu bars -----------------------------------------------------
#
# The menus are the other half of the two frontends being the same program, and
# nothing checked them: the suite diffs the ZPL, and a menu produces none. What
# follows spells both toolkits' menus the same way so that only real
# differences survive.

_MNEMONIC = re.compile(r'[&_](\w)')

# GTK renders Shift+Ctrl+S and Delete where Qt renders Ctrl+Shift+S and Del -
# a display convention on each side, not a difference in what is bound.
MODIFIER_ORDER = ('Ctrl', 'Shift', 'Alt', 'Meta')
KEY_ALIASES = {'Del': 'Delete', 'Return': 'Enter'}


# A pointer crosses a distance in steps, not in one jump, and a drag that is
# only ever delivered whole cannot catch a handler that loses the small deltas.
DRAG_STEPS = 8


def _drag_path(from_x, from_y, dx, dy):
    """The pointer positions a drag passes through, ending exactly on target."""
    return [(from_x + dx * step / DRAG_STEPS, from_y + dy * step / DRAG_STEPS)
            for step in range(1, DRAG_STEPS + 1)]


def menu_label(text):
    """A label with its mnemonic marked the same way whichever toolkit wrote it.

    `&New` and `_New` both become `[N]ew`, so the comparison ignores which
    character a toolkit uses to mark one but still catches a mnemonic that has
    moved to a different letter.
    """
    return _MNEMONIC.sub(lambda m: f'[{m.group(1)}]', text or '')


def menu_accel(text):
    """An accelerator spelled the same way whichever toolkit reported it."""
    if not text:
        return ''
    mods, rest = [], text
    stripping = True
    while stripping:
        stripping = False
        for mod in MODIFIER_ORDER:
            if rest.startswith(mod + '+') and len(rest) > len(mod) + 1:
                mods.append(mod)
                rest = rest[len(mod) + 1:]
                stripping = True
    ordered = sorted(set(mods), key=MODIFIER_ORDER.index)
    return '+'.join(ordered + [KEY_ALIASES.get(rest, rest)])


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
        self.window._offer_dpi_rescale = \
            lambda loaded_dpi=workflow._FROM_DOCUMENT: workflow.reconcile_dpi(
                self.canvas.document, self.window.printer_dpi,
                lambda *a: 'rescale', file_dpi=loaded_dpi)
        # A fixture carrying a command the model cannot keep would otherwise
        # stop the run on a modal nobody is there to dismiss.
        self.window._warn_unsupported = lambda commands: None
        self.window._warn_control_redefined = lambda spellings: None

    # -- document ---------------------------------------------------------
    @property
    def elements(self):
        return self.canvas.elements

    def select(self, element):
        self.canvas.selected_element = element

    def add_text(self, text):
        return self.canvas.add_text_element(text)

    def add_time(self, text):
        return self.canvas.add_time_element(text)

    def add_serial(self, text):
        return self.canvas.add_serial_element(text)

    def add_numbered(self, number, prompt=None):
        return self.canvas.add_numbered_element(number, prompt)

    def add_frame(self):
        return self.canvas.add_frame_element()

    def add_stored_graphic(self, command='XG', device_spec='R:UNKNOWN.GRF'):
        return self.canvas.add_stored_graphic_element(command, device_spec)

    def store_graphic(self, device_spec, image):
        """What Printer -> Graphics... 'Store...' does, bypassing its
        dialogs - graphic_store is core, so both frontends do the same
        thing here."""
        graphic_store.store(device_spec, image)

    def add_barcode(self):
        return self.canvas.add_barcode_element()

    def set_font(self, element, path):
        element.font_path = path
        self.canvas.sync_text_width(element)

    def resync(self, element):
        self.canvas.sync_text_width(element)

    def set_field_number(self, element, number, prompt=None):
        element.field_number = number
        element.field_prompt = prompt
        self.canvas.sync_text_width(element)

    def resync_barcode(self, element):
        element.sync_box()

    # -- the pointer, through each frontend's own event handlers -------------
    class _Event:
        """The fields a button handler reads. GTK events cannot be built.

        Whole pixels, because that is what a pointer reports and what Qt's
        QPoint can hold: a fractional pixel here would divide back into a
        different dot from the one the Qt side lands on, and the harness would
        be reporting its own arithmetic as a disagreement.
        """
        def __init__(self, x, y, button=1, state=0):
            self.x, self.y = float(int(x)), float(int(y))
            self.button, self.state = button, state

    def fresh_gesture(self):
        """Forget the last click, so two scripted gestures are not a double one.

        A user separates gestures by seconds; a script does not, and the
        canvases time double clicks in real time.
        """
        self.canvas.last_click_time = 0
        self.canvas.last_click_element = None

    def set_zoom(self, zoom):
        self.canvas.set_zoom(zoom)

    def click(self, lx, ly):
        """Press the left button at a point in label dots."""
        scale = self.canvas._scale()
        self.canvas.on_button_press(self.canvas,
                                    self._Event(lx * scale, ly * scale))

    def shift_click(self, lx, ly):
        """Press and release with Shift held, which adds to the selection."""
        from gi.repository import Gdk
        self._modified_click(lx, ly, Gdk.ModifierType.SHIFT_MASK)

    def ctrl_click(self, lx, ly):
        """Press and release with Ctrl held: exactly the element under it."""
        from gi.repository import Gdk
        self._modified_click(lx, ly, Gdk.ModifierType.CONTROL_MASK)

    def ctrl_shift_click(self, lx, ly):
        """Ctrl and Shift: add or drop exactly the element under it."""
        from gi.repository import Gdk
        self._modified_click(lx, ly, Gdk.ModifierType.CONTROL_MASK
                             | Gdk.ModifierType.SHIFT_MASK)

    def _modified_click(self, lx, ly, state):
        scale = self.canvas._scale()
        event = self._Event(lx * scale, ly * scale, state=state)
        self.canvas.on_button_press(self.canvas, event)
        self.canvas.on_button_release(self.canvas, event)

    def band(self, from_x, from_y, to_x, to_y):
        """Drag a rubber band across the canvas, from one point to another."""
        scale = self.canvas._scale()
        self.click(from_x, from_y)
        self.canvas.on_motion(self.canvas, self._Event(to_x * scale, to_y * scale))
        self.canvas.on_button_release(self.canvas,
                                      self._Event(to_x * scale, to_y * scale))

    def selection(self):
        """Which elements are selected, as indices into the document."""
        document = self.canvas.document
        return [document.elements.index(el) for el in document.selection]

    def drag_pointer(self, from_x, from_y, dx, dy):
        """Press, move and release - the path a user's drag actually takes.

        In steps, because a pointer arrives in steps. One motion event carrying
        the whole delta is the one case a resize never had trouble with: the
        box snaps back to what it will print, so a drag delivered in small
        pieces used to lose every piece smaller than that snap.
        """
        scale = self.canvas._scale()
        self.click(from_x, from_y)
        for x, y in _drag_path(from_x, from_y, dx, dy):
            self.canvas.on_motion(self.canvas, self._Event(x * scale, y * scale))
        self.canvas.on_button_release(self.canvas,
                                      self._Event((from_x + dx) * scale,
                                                  (from_y + dy) * scale))

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

    def select_many(self, elements):
        self.canvas.document.select_many(elements)

    def align(self, edge):
        self.canvas.align_selected(edge)

    def group(self):
        """Edit > Group, through the window's handler as the menu would."""
        self.window.on_group_clicked(None)

    def ungroup(self):
        self.window.on_ungroup_clicked(None)

    def remove_from_group(self):
        self.window.on_remove_from_group_clicked(None)

    def select_all(self):
        self.window.on_select_all_clicked(None)

    def deselect_all(self):
        self.window.on_deselect_all_clicked(None)

    def invert_selection(self):
        self.window.on_invert_selection_clicked(None)

    def resize_target(self):
        return self.canvas.document.resize_target()

    def move_group(self, dx, dy):
        self.geometry.move_selection(self.canvas.document,
                                     self.canvas.document.selection, dx, dy)

    def set_label_size(self, w, h):
        self.canvas.set_label_size(w, h)

    def label_settings(self, w, h, dpi, w_in, h_in, transform=None):
        """One accepted Label Settings visit, through the frontend's handler."""
        self.window.apply_label_settings(w, h, dpi, w_in, h_in, transform)

    def label_size(self):
        return (self.canvas.label_width, self.canvas.label_height)

    def load(self, path):
        self.window.load_zpl_file(str(path))
        self.canvas = self.window.design_canvas

    def change_printer_dpi(self, dpi, answer):
        from zplcore import workflow
        self.window.printer_dpi = dpi
        workflow.reconcile_dpi(self.canvas.document, dpi, lambda *a: answer)

    def title(self):
        """The titlebar, as the user reads it and as the task switcher does."""
        return f'{self.window.header_bar.get_title()} | {self.window.get_title()}'

    def menus(self):
        """The menu bar as text: titles, items, separators and accelerators."""
        from gi.repository import Gtk

        def find_bar(widget):
            if isinstance(widget, Gtk.MenuBar):
                return widget
            if isinstance(widget, Gtk.Container):
                for kid in widget.get_children():
                    found = find_bar(kid)
                    if found is not None:
                        return found
            return None

        # The menu bar lives in the header bar, not directly under the window.
        bar = find_bar(self.window.get_titlebar()) or find_bar(self.window)
        lines = []

        def walk(menu, depth):
            """Items of a menu and of any submenu under it, indented by depth."""
            pad = '  ' * depth
            for item in menu.get_children():
                if isinstance(item, Gtk.SeparatorMenuItem):
                    lines.append(f'{pad}---')
                    continue
                accel = self.window.accelerators.get(item, '')
                if accel:
                    key, mods = Gtk.accelerator_parse(accel)
                    accel = Gtk.accelerator_get_label(key, mods)
                lines.append(f'{pad}{menu_label(item.get_label())}\t{menu_accel(accel)}')
                # A submenu is the other half of a menu bar: an Align that held
                # different commands in the two frontends would otherwise pass.
                if item.get_submenu() is not None:
                    walk(item.get_submenu(), depth + 1)

        for top in bar.get_children():
            lines.append(f'[{menu_label(top.get_label())}]')
            if top.get_submenu() is not None:
                walk(top.get_submenu(), 1)
        return '\n'.join(lines)

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
        # A fixture carrying a command the model cannot keep would otherwise
        # stop the run on a modal nobody is there to dismiss.
        dialogs.warn_unsupported = lambda *a, **k: None
        dialogs.warn_control_redefined = lambda *a, **k: None
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

    def add_time(self, text):
        return self.document.add_time_element(text)

    def add_serial(self, text):
        return self.document.add_serial_element(text)

    def add_numbered(self, number, prompt=None):
        return self.document.add_numbered_element(number, prompt)

    def add_frame(self):
        return self.document.add_frame_element()

    def add_stored_graphic(self, command='XG', device_spec='R:UNKNOWN.GRF'):
        return self.document.add_stored_graphic_element(command, device_spec)

    def store_graphic(self, device_spec, image):
        """What Printer -> Graphics... 'Store...' does, bypassing its
        dialogs - graphic_store is core, so both frontends do the same
        thing here."""
        graphic_store.store(device_spec, image)

    def add_barcode(self):
        return self.document.add_barcode_element()

    def set_font(self, element, path):
        element.font_path = path
        self.document.sync_text_width(element)

    def resync(self, element):
        self.document.sync_text_width(element)

    def set_field_number(self, element, number, prompt=None):
        element.field_number = number
        element.field_prompt = prompt
        self.document.sync_text_width(element)

    def resync_barcode(self, element):
        element.sync_box()

    # -- the pointer, through each frontend's own event handlers -------------
    @property
    def canvas(self):
        return self.window.canvas

    def _event(self, kind, lx, ly, modifiers=None):
        from PySide2.QtCore import QPoint, Qt
        from PySide2.QtGui import QMouseEvent
        scale = self.canvas._scale()
        return QMouseEvent(kind, QPoint(int(lx * scale), int(ly * scale)),
                           Qt.LeftButton, Qt.LeftButton,
                           Qt.NoModifier if modifiers is None else modifiers)

    def fresh_gesture(self):
        """Forget the last click, so two scripted gestures are not a double one.

        A user separates gestures by seconds; a script does not, and the
        canvases time double clicks in real time.
        """
        self.canvas.last_click_time = 0
        self.canvas.last_click_element = None

    def set_zoom(self, zoom):
        self.canvas.set_zoom(zoom)

    def click(self, lx, ly):
        """Press the left button at a point in label dots."""
        from PySide2.QtCore import QEvent
        self.canvas.mousePressEvent(self._event(QEvent.MouseButtonPress, lx, ly))

    def shift_click(self, lx, ly):
        """Press and release with Shift held, which adds to the selection."""
        from PySide2.QtCore import Qt
        self._modified_click(lx, ly, Qt.ShiftModifier)

    def ctrl_click(self, lx, ly):
        """Press and release with Ctrl held: exactly the element under it."""
        from PySide2.QtCore import Qt
        self._modified_click(lx, ly, Qt.ControlModifier)

    def ctrl_shift_click(self, lx, ly):
        """Ctrl and Shift: add or drop exactly the element under it."""
        from PySide2.QtCore import Qt
        self._modified_click(lx, ly, Qt.ControlModifier | Qt.ShiftModifier)

    def _modified_click(self, lx, ly, modifiers):
        from PySide2.QtCore import QEvent
        self.canvas.mousePressEvent(
            self._event(QEvent.MouseButtonPress, lx, ly, modifiers))
        self.canvas.mouseReleaseEvent(
            self._event(QEvent.MouseButtonRelease, lx, ly, modifiers))

    def band(self, from_x, from_y, to_x, to_y):
        """Drag a rubber band across the canvas, from one point to another."""
        from PySide2.QtCore import QEvent
        self.click(from_x, from_y)
        self.canvas.mouseMoveEvent(self._event(QEvent.MouseMove, to_x, to_y))
        self.canvas.mouseReleaseEvent(
            self._event(QEvent.MouseButtonRelease, to_x, to_y))

    def selection(self):
        """Which elements are selected, as indices into the document."""
        return [self.document.elements.index(el)
                for el in self.document.selection]

    def drag_pointer(self, from_x, from_y, dx, dy):
        """Press, move and release - the path a user's drag actually takes.

        In steps, for the reason the GTK driver's does.
        """
        from PySide2.QtCore import QEvent
        self.click(from_x, from_y)
        for x, y in _drag_path(from_x, from_y, dx, dy):
            self.canvas.mouseMoveEvent(self._event(QEvent.MouseMove, x, y))
        self.canvas.mouseReleaseEvent(
            self._event(QEvent.MouseButtonRelease, from_x + dx, from_y + dy))

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

    def select_many(self, elements):
        self.document.select_many(elements)

    def align(self, edge):
        self.document.align_selected(edge)

    def group(self):
        """Edit > Group, through the window's handler as the menu would."""
        self.window.on_group()

    def ungroup(self):
        self.window.on_ungroup()

    def remove_from_group(self):
        self.window.on_remove_from_group()

    def select_all(self):
        self.window.on_select_all()

    def deselect_all(self):
        self.window.on_deselect_all()

    def invert_selection(self):
        self.window.on_invert_selection()

    def resize_target(self):
        return self.document.resize_target()

    def move_group(self, dx, dy):
        self.geometry.move_selection(self.document, self.document.selection,
                                     dx, dy)

    def set_label_size(self, w, h):
        self.document.set_label_size(w, h)

    def label_settings(self, w, h, dpi, w_in, h_in, transform=None):
        """One accepted Label Settings visit, through the frontend's handler."""
        self.window.apply_label_settings(w, h, dpi, w_in, h_in, transform)

    def label_size(self):
        return (self.document.label_width, self.document.label_height)

    def load(self, path):
        self.window.unsaved_changes = False
        self.window.load_zpl_file(str(path))

    def change_printer_dpi(self, dpi, answer):
        from zplcore import workflow
        self.window.printer_dpi = dpi
        workflow.reconcile_dpi(self.document, dpi, lambda *a: answer)

    def title(self):
        """The titlebar. Qt has one title, which serves as both."""
        return f'{self.window.windowTitle()} | {self.window.windowTitle()}'

    def menus(self):
        """The menu bar as text: titles, items, separators and accelerators."""
        lines = []

        def walk(menu, depth):
            """Items of a menu and of any submenu under it, indented by depth."""
            pad = '  ' * depth
            for action in menu.actions():
                if action.isSeparator():
                    lines.append(f'{pad}---')
                    continue
                lines.append(f'{pad}{menu_label(action.text())}\t'
                             f'{menu_accel(action.shortcut().toString())}')
                # A submenu is the other half of a menu bar: an Align that held
                # different commands in the two frontends would otherwise pass.
                if action.menu() is not None:
                    walk(action.menu(), depth + 1)

        for top in self.window.menuBar().actions():
            lines.append(f'[{menu_label(top.text())}]')
            if top.menu() is not None:
                walk(top.menu(), 1)
        return '\n'.join(lines)

    def to_zpl(self):
        return self.document.to_zpl()

    def run(self, body):
        body(self)


def sequence(driver, record):
    """The scripted session. Every frontend must produce the same ZPL for it."""
    from zplcore import fonts
    font_path = fonts.file_for_family(FONT_FAMILY)

    # The menu bar first: it is a structural fact about the frontend rather
    # than anything the document does, and it is the half of "the same program"
    # that emits no ZPL and so went unchecked.
    record('the menu bar', driver.menus())
    # The titlebar is the other thing the window says on its own. It named the
    # toolkit in one frontend and an old name for the program in the other,
    # and never the label in front of the user.
    record('the titlebar with no file open', driver.title())

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

    # A matrix symbology, whose size is its own grid rather than a height the
    # command carries - so the two frontends have to agree about a footprint
    # neither of them can get from bar_height.
    barcode.symbology = 'qr'
    barcode.barcode_value = 'MM,ACONFORM-1234'
    barcode.module_width = 4
    barcode.show_text = False
    barcode.sync_box()
    record('barcode becomes a QR code')
    driver.resize(barcode, 'br', 140, 40)
    record('resize the QR code, which stays square')

    driver.select(frame)
    driver.bring_forward()
    record('bring frame forward')
    driver.send_to_back()
    record('send frame to back')

    # Selecting more than one, through each frontend's own event handlers: a
    # rubber band dragged across the label, then a click and a shift-click.
    # Neither gesture emits any ZPL, so what is recorded is the selection
    # itself - which is the whole of what the two could disagree about.
    driver.fresh_gesture()
    width, height = driver.label_size()
    driver.band(width - 1, height - 1, 0, 0)
    record('a band dragged across the label selects: ' + json.dumps(driver.selection()))
    record('and the band changed nothing in the design')

    # A band of no area over empty canvas is how a user clears the selection;
    # the click and the shift-click after it then build a group of two.
    driver.band(width - 1, height - 1, width - 1, height - 1)
    record('a click on empty canvas selects: ' + json.dumps(driver.selection()))
    driver.fresh_gesture()
    driver.click(text.x + text.width // 2, text.y + text.height // 2)
    driver.fresh_gesture()
    driver.shift_click(frame.x + frame.width // 2, frame.y + frame.height // 2)
    record('a click then a shift-click selects: ' + json.dumps(driver.selection()))

    # The selection commands, through each window's own handlers. None of
    # them touches the design, so what is recorded is the selection.
    driver.select_all()
    record('select all selects: ' + json.dumps(driver.selection()))
    driver.invert_selection()
    record('inverting that selects: ' + json.dumps(driver.selection()))
    driver.invert_selection()
    record('and inverting again selects: ' + json.dumps(driver.selection()))
    driver.deselect_all()
    record('deselect all selects: ' + json.dumps(driver.selection()))
    driver.select(barcode)
    driver.invert_selection()
    record('inverting one element selects the rest: ' + json.dumps(driver.selection()))

    # Alignment. A group lines up against its own bounding box and a lone
    # element against the label, so both rules are compared; the group drag
    # after them is the clamp that has to treat the pair as one box.
    driver.select_many([text, frame])
    for edge in ('left', 'center', 'right', 'top', 'middle', 'bottom'):
        driver.align(edge)
        record(f'the pair aligned {edge}')
    driver.move_group(-9999, -9999)
    record('the pair dragged into the corner as one box')

    # Grouping. The pair becomes one unit, and every selection gesture is
    # driven through the frontend's own handlers again, since each of them
    # has to widen a pick of one member to the pair: a click, a band touching
    # a member, a shift-click. The align with a loose element moves the pair
    # as one box, the z-order command moves the pair as one run, and the
    # ^FXDESIGNER_GROUP markers are in the ZPL every step records.
    driver.select_many([text, frame])
    driver.group()
    record('the pair grouped')
    driver.fresh_gesture()
    driver.band(width - 1, height - 1, width - 1, height - 1)
    driver.fresh_gesture()
    driver.click(frame.x + frame.width // 2, frame.y + frame.height // 2)
    record('a click on one member of the group selects: '
           + json.dumps(driver.selection()))
    driver.fresh_gesture()
    driver.band(width - 1, height - 1, width - 1, height - 1)
    driver.fresh_gesture()
    # From empty canvas just right of the frame, reaching a few dots into
    # its top corner - which the text below does not extend up to.
    driver.band(frame.x + frame.width + 40, frame.y + 10,
                frame.x + frame.width - 4, frame.y + 20)
    record('a band reaching one member of the group selects: '
           + json.dumps(driver.selection()))
    driver.fresh_gesture()
    driver.band(width - 1, height - 1, width - 1, height - 1)
    driver.fresh_gesture()
    driver.shift_click(text.x + text.width // 2, text.y + text.height // 2)
    record('a shift-click on one member of the group selects: '
           + json.dumps(driver.selection()))
    driver.select_many([text, barcode])
    record('selecting a member and a loose element selects: '
           + json.dumps(driver.selection()))
    for edge in ('right', 'bottom'):
        driver.align(edge)
        record(f'the group and a loose element aligned {edge}')
    driver.select(text)
    driver.bring_forward()
    record('bring the group forward, as one run')

    # A direct pick. Ctrl-click reaches one member of the group, through
    # each frontend's own modifier reading, and what follows it - a drag, an
    # align - acts on that member alone, while the z-order commands and
    # Ungroup still take its whole group.
    # At 1:1, as every pointer drag here is, so the two frontends quantise
    # the pointer to the same dots.
    driver.set_zoom(1.0)
    # Points inside one element only, and away from the handles a directly
    # picked element grows - a press on one of those is a resize, not a pick.
    fx, fy = frame.x + 30, frame.y + 30
    driver.fresh_gesture()
    driver.ctrl_click(fx, fy)
    record('a ctrl-click on one member selects: ' + json.dumps(driver.selection()))
    driver.fresh_gesture()
    driver.drag_pointer(fx, fy, 30, 20)
    record('and a drag after it moves that member alone')
    driver.fresh_gesture()
    driver.ctrl_shift_click(barcode.x + 20, barcode.y + 20)
    record('a ctrl-shift-click adds exactly the element under it: '
           + json.dumps(driver.selection()))
    driver.align('left')
    record('the member and the loose element aligned left, the member alone')
    driver.fresh_gesture()
    driver.ctrl_click(text.x + text.width // 2, text.y + text.height // 2)
    driver.send_to_back()
    record('send to back from a directly picked member moves its whole group')
    driver.ungroup()
    record('the pair ungrouped, from a directly picked member')

    # Nesting: the pair grouped again, then that group with the barcode. A
    # click anywhere in it selects the nest; Ungroup from inside peels the
    # outer group and leaves the pair a group of its own.
    driver.select_many([text, frame])
    driver.group()
    driver.select_many([text, barcode])
    driver.group()
    record('the pair grouped, then grouped with the barcode: a nest')
    # The frame's bottom edge is the one place the barcode and the text,
    # which sit over the rest of it by now, do not reach.
    px, py = frame.x + 10, frame.y + frame.height - 10
    driver.fresh_gesture()
    driver.click(px, py)
    record('a click on a member of the inner pair selects the whole nest: '
           + json.dumps(driver.selection()))
    driver.fresh_gesture()
    driver.ctrl_click(px, py)
    record('a ctrl-click inside the nest selects: ' + json.dumps(driver.selection()))
    driver.ungroup()
    record('ungroup from inside peels the outer group, the pair keeps its own')
    driver.fresh_gesture()
    driver.band(width - 1, height - 1, width - 1, height - 1)
    driver.fresh_gesture()
    driver.click(px, py)
    record('a click on the pair now selects just the pair: '
           + json.dumps(driver.selection()))
    driver.ungroup()
    record('the pair ungrouped again')

    # Resizing a group. The handles belong to the pair as one box; a resize
    # by any of them scales both members - the text's font, the frame's
    # thickness - and the pointer path through each canvas's handle branch
    # is what the drag step drives.
    driver.select_many([text, frame])
    driver.group()
    record('handles of the group: ' + json.dumps(
        {k: list(v) for k, v in sorted(driver.handles(driver.resize_target()).items())}))
    driver.resize(driver.resize_target(), 'br', 40, 30)
    record('the group resized by its bottom-right handle')
    driver.resize(driver.resize_target(), 'tl', -20, -10)
    record('and by its top-left handle')
    corner = driver.handles(driver.resize_target())['br']
    driver.fresh_gesture()
    driver.drag_pointer(corner[0], corner[1], 30, 20)
    record('a pointer drag on a group handle scales the group')
    driver.select_many([text, barcode])
    driver.group()
    driver.resize(driver.resize_target(), 'ml', 25, 0)
    record('a nest resized by its left handle')

    # Remove from Group lifts the directly picked member out and leaves the
    # rest grouped; then everything back to loose for the steps that follow.
    driver.fresh_gesture()
    driver.band(width - 1, height - 1, width - 1, height - 1)
    driver.fresh_gesture()
    driver.ctrl_click(frame.x + 10, frame.y + frame.height - 10)
    driver.remove_from_group()
    record('the frame removed from its group, selecting: '
           + json.dumps(driver.selection()))
    driver.select(text)
    driver.ungroup()
    driver.select(text)
    driver.ungroup()
    record('everything ungrouped again')

    driver.select(barcode)
    for edge in ('right', 'bottom', 'center', 'middle'):
        driver.align(edge)
        record(f'one element aligned {edge} on the label')

    text.print_enabled = False
    record('text marked not printing')

    driver.set_label_size(600, 900)
    record('label shrunk, elements clamped')

    driver.load(FIXTURE_300)
    record('load a 300dpi file at 203dpi, rescaled')
    record('the titlebar with that file open', driver.title())

    # The shared decisions: switching the printer's resolution under an open
    # design must offer the same choice in both frontends, not re-stamp it.
    driver.change_printer_dpi(300, answer='rescale')
    record('printer switched to 300dpi, rescaled')
    driver.change_printer_dpi(203, answer='keep')
    record('printer switched to 203dpi, dots kept')

    # Label Settings can change the resolution as well as the size, and the two
    # interact: reconciling rescales the whole design, and the size typed in the
    # dialog is then laid on top of it. The order is the divergence-prone part,
    # and nothing else in this sequence reaches it.
    driver.label_settings(900, 600, 300, 3.0, 2.0)
    record('label settings: 3x2 inches at 300dpi, design rescaled')

    # A template from another tool: the wrapped block is where the two
    # frontends would most easily disagree, since each measures and lays out
    # the text itself.
    driver.load(FIXTURE_TEMPLATE)
    record('load a template using ^A0 and ^FB')

    # Through the real button handlers, not the geometry helpers underneath:
    # every step above moves elements directly, which is how a frontend whose
    # click handler raised on every press went unnoticed.
    #
    # Pinned to a scale first. Left to fit whatever window each frontend
    # happens to have, the two run at different scales, and the dots a pointer
    # position rounds to then differ by one or two - a real divergence, but one
    # about the harness rather than about the frontends.
    driver.set_zoom(1.0)
    picked = driver.elements[0]
    driver.click(picked.x + 3, picked.y + 3)
    record('select with the pointer')
    driver.drag_pointer(picked.x + 3, picked.y + 3, 25, 15)
    record('drag with the pointer')

    # The same drag zoomed out and zoomed in. Zoom writes no ZPL of its own,
    # but every pointer position passes through screen_to_label, and a scale
    # far from 1 is where two frontends would quietly land on different dots.
    # From the middle of the element, which is a plain move at every zoom - a
    # corner is inside the handle radius at 0.25 and outside it at 4.0, so the
    # same point would be a different gesture at each.
    for zoom in (0.25, 4.0):
        driver.set_zoom(zoom)
        driver.fresh_gesture()
        driver.drag_pointer(picked.x + picked.width // 2,
                            picked.y + picked.height // 2, 20, 12)
        record(f'drag with the pointer at {zoom:g}x')
    driver.set_zoom(1.0)

    # A handle drag through the canvas's own press/motion/release path, rather
    # than straight into the geometry the way the resize steps above do. A
    # pointer delivers the distance in pieces, and a text box snaps back to the
    # width it will print at after every one of them, so this is where a resize
    # that measures from the last event instead of from the press loses the
    # whole drag. At 1:1, where both frontends agree which dot a pointer is on.
    stretched = driver.add_text('Stretch')
    driver.select(stretched)
    driver.fresh_gesture()
    corner = driver.handles(stretched)['br']
    driver.drag_pointer(corner[0], corner[1], 48, 16)
    record('stretch text by its corner handle, a pointer step at a time')

    # Every ^BC parameter, since each one changes the label and each frontend
    # has its own dialog and its own drawing code for them.
    bars = next((e for e in driver.elements if e.element_type == 'barcode'), None)
    if bars is not None:
        bars.show_text, bars.text_above = True, False
        driver.resync_barcode(bars)
        record('barcode with the value printed below')
        bars.text_above = True
        driver.resync_barcode(bars)
        record('barcode with the value printed above')
        bars.show_text = False
        driver.resync_barcode(bars)
        record('barcode with no interpretation line')
        bars.show_text, bars.mode, bars.barcode_value = True, 'A', '1234567890'
        driver.resync_barcode(bars)
        record('numeric barcode in mode A, packed into subset C')
        bars.check_digit = True
        driver.resync_barcode(bars)
        record('barcode with a UCC check digit')
        bars.orientation = 'R'
        driver.resync_barcode(bars)
        record('barcode rotated 90 degrees')

    block = next((e for e in driver.elements if e.element_type == 'text'), None)
    if block is not None:
        block.text = 'Stainless Steel Hex Head Bolt 10mm'
        driver.resync(block)
        record('a real product name wrapped into the block')

        # Every ^FB parameter, for the same reason as ^BC's: the wrap decides
        # how many lines the label needs, and each frontend measures the text
        # and lays the lines out itself.
        from zplcore.model import FieldBlock
        from zplcore import textraster
        block.block = FieldBlock(block.block.width, 4, 1, 'L', 0)
        driver.resync(block)
        record('the block left-aligned')
        block.block = FieldBlock(block.block.width, 4, 1, 'J', 0)
        driver.resync(block)
        record('the block justified')
        block.block = FieldBlock(120, 6, 1, 'C', 8)
        driver.resync(block)
        record('the block narrowed, indented and re-wrapped')
        block.text = textraster.from_editor('ACME Widget\nModel 4400')
        driver.resync(block)
        record('a forced line break typed into the block')
        driver.resize(block, 'mr', 90, 0)
        record('the wrap widened by its side handle')
        driver.resize(block, 'bm', 0, -block.font_height)
        record('a line cut by the bottom handle')

        # Wrapping switched off is what a user does last, and it must not leave
        # a forced break behind to print as the two characters it is written
        # with.
        block.text = textraster.join_lines(block.text)
        block.block = None
        driver.resync(block)
        record('wrapping switched off, the lines joined')

    # Text at each quarter turn. Rotation writes one letter into ^A, but it
    # also transposes the footprint, and a footprint is what every later drag
    # and clamp is measured against.
    turning = next((e for e in driver.elements if e.element_type == 'text'), None)
    if turning is not None:
        for facing in ('R', 'I', 'B', 'N'):
            turning.orientation = facing
            driver.resync(turning)
            record(f'text turned to {facing}')

    # ^GB's colour and corner rounding, which both frontends now draw and
    # neither used to keep.
    frame = next((e for e in driver.elements if e.element_type == 'frame'), None)
    if frame is None:
        frame = driver.add_frame()
    frame.colour, frame.rounding = 'W', 8
    record('a white frame with rounded corners')
    frame.colour, frame.rounding = 'B', 3
    record('a black frame, less rounded')

    # ^FR: a per-field flag, not a parameter of any of the other three, so it
    # gets its own step for each element type that has one in scope.
    if bars is not None:
        bars.reverse_print = True
        record('a reversed barcode')
    frame.reverse_print = True
    record('a reversed frame')
    if block is not None:
        block.reverse_print = True
        record('a reversed text field')

    # ZPL as other tools leave it: a ^CF default font rather than an ^A on
    # every field, and a ^GB carrying its colour and rounding. Last, because
    # loading replaces the document every earlier step built up.
    driver.load(FIXTURE_DEFAULTS)
    record('load a file using ^CF and a painted ^GB')

    # The rest of how other tools write a label: a field placed from its
    # baseline, a rule drawn by leaving one of ^GB's sides to default, and an
    # ^A that gives a height and lets the width follow. Each load replaces the
    # document, so these stay at the end with the one above.
    driver.load(FIXTURE_TYPESET)
    record('load a file placed by ^FT')
    driver.load(FIXTURE_RULES)
    record('load a file of ^GB rules')
    driver.load(FIXTURE_PARTIAL)
    record('load a file whose ^A leaves its sizes off')
    driver.load(FIXTURE_ORIENTED)
    record('load a file whose fields turn with ^FW')
    driver.load(FIXTURE_COMPRESSED)
    record('load a file whose logo is :Z64: compressed')

    # A stored format and its recall call. The template's ^FN fields carry no
    # data of their own, and the recall call carries nothing but data, so
    # between them they cover both halves of what ^FN means.
    driver.load(FIXTURE_STORED)
    record('load the manual\'s own ^DF stored format')
    driver.load(FIXTURE_NAMED)
    record('load a stored format whose fields name themselves')
    driver.load(FIXTURE_RECALL)
    record('load an ^XF recall call, which is data and no geometry')

    # The graphic counterpart of the pair above: ^IS saves a rendered
    # snapshot, then ^XG/^IM/^IL each recall it. Loading the save fixture
    # first, in this same process, is what lets the recall/load fixtures
    # resolve real pixels rather than only a placeholder - both frontends
    # have to agree on that resolved image, not just on the reference.
    driver.load(FIXTURE_GRAPHIC_SAVE)
    record('load a format that saves itself with ^IS')
    driver.load(FIXTURE_GRAPHIC_RECALL)
    record('load a format recalling that image with ^XG and ^IM')
    driver.load(FIXTURE_GRAPHIC_LOAD)
    record('load a format loading that image with ^IL')

    # ^LH moves every field, so a frontend that read it differently would place
    # the whole design somewhere else; the flips have to survive a save in both.
    driver.load(FIXTURE_HOME)
    record('load a format placed from a ^LH origin')
    driver.load(FIXTURE_FLIPPED)
    record('load a format that is inverted and mirrored')

    # A right justified field is placed from its right edge, so a frontend
    # reading ^FO's z differently would put the whole column somewhere else.
    driver.load(FIXTURE_JUSTIFIED)
    record('load a format justified from the right')
    driver.set_zoom(1.0)
    driver.fresh_gesture()
    _rj = driver.elements[0]
    driver.drag_pointer(_rj.x + 3, _rj.y + 3, 15, 10)
    record('drag a right justified field, which moves the ^FO it writes')

    # The editing canvas is not flipped, so a click lands on the element where
    # the canvas draws it - not where the printer will lay it down. If either
    # frontend transformed pointer input, this drag would miss and write
    # different ZPL.
    driver.set_zoom(1.0)
    driver.fresh_gesture()
    _flipped = driver.elements[0]
    driver.click(_flipped.x + 3, _flipped.y + 3)
    record('click an element on an inverted label')
    driver.fresh_gesture()
    driver.drag_pointer(_flipped.x + 3, _flipped.y + 3, 20, 10)
    record('and drag it, which a flipped canvas would send the other way')

    # ^CV is a switch the printer acts on and nothing here draws, so the only
    # thing to agree on is that both write it back.
    driver.load(FIXTURE_VALIDATED)
    record('load a format that asks the printer to validate its barcodes')

    # ^CI, ^CW and ^FL are the same kind of thing: written back at the top,
    # and the ^CI28 is what both have to agree on over the non-ASCII text.
    driver.load(FIXTURE_FONT_IDENTITY)
    record('load a format that names its encoding and font table')

    # Each ^A@ here names a path this designer would never write itself, so
    # both frontends have to carry one rather than resolve it to E:NAME.TTF.
    driver.load(FIXTURE_FONT_PATH)
    record('load a format whose fonts name their own drives')

    # Setting a home and a flip from Label Settings has to reach the file the
    # same way in both, including the ^FO each element is written back at.
    driver.load(FIXTURE_HOME)
    _moved = transforms.LabelTransform()
    _moved.home = (40, 60)
    _moved.invert = True
    driver.label_settings(406, 406, 203, 2.0, 2.0, _moved)
    record('move the label home and invert it')

    # Turning a literal field into a variable one has to reach the file the same
    # way in both frontends: a ^FN where the ^FD used to be.
    driver.load(FIXTURE_NAMED)
    _variable = driver.add_text('becomes variable')
    driver.set_field_number(_variable, 12, 'Batch')
    record('make a field variable')
    driver.set_field_number(_variable, None, None)
    record('and back to a literal')

    # A time field has its own creation button rather than being a mode of
    # the text editor's Data Source selector, so its own path -
    # add_time_element - has to write the same ^FC/^FD pair from both
    # frontends.
    driver.add_time('%m/%d/%y')
    record('add a time field')

    # Likewise for a serial field - add_serial_element, not a mode of the
    # text editor's Data Source selector.
    driver.add_serial('1')
    record('add a serial field')

    # And for a numbered field - add_numbered_element is its own creation
    # path too now, the same as ^SN and ^FC.
    driver.add_numbered(7, 'Batch')
    record('add a numbered field')

    # A stored graphic reference has its own creation path too -
    # add_stored_graphic_element - the same "+ button, its own element" shape
    # as time/serial/numbered above.
    driver.add_stored_graphic('XG', 'R:SAMPLE.GRF')
    record('add a stored graphic reference')

    # Printer -> Graphics... 'Store...' is a second way into graphic_store,
    # alongside ^IS - store_graphic bypasses its own file-chooser and
    # device/name/extension dialogs the way every other driver method
    # bypasses its editor, going straight to the graphic_store.store() call
    # both frontends make. It changes no Document state - see
    # zplcore/graphic_store.py - so the ZPL is unaffected; what has to agree
    # between frontends is that the reference just added now resolves.
    driver.store_graphic('R:SAMPLE.GRF', Image.new('RGB', (40, 30), (10, 20, 30)))
    resolved = driver.elements[-1].resolve()
    record('store a graphic and resolve the reference already pointing at it',
          text=f"resolved={resolved.size if resolved else None}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--frontend', choices=('gtk', 'qt'), required=True)
    args = ap.parse_args()

    driver = GtkDriver() if args.frontend == 'gtk' else QtDriver()
    steps = []

    def record(name, text=None):
        """Record what the design says now, or some other text to compare."""
        steps.append({'step': name,
                      'zpl': driver.to_zpl() if text is None else text})

    sequence(driver, record)
    json.dump({'frontend': driver.name, 'steps': steps}, sys.stdout)


if __name__ == '__main__':
    main()
