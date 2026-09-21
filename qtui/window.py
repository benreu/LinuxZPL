#!/usr/bin/env python3
"""
The Qt frontend: a PySide2/Qt5 window over a zplcore Document.

The window owns everything that is not the document itself: the menus and their
enable rules, the undo history, the printer settings, and every command that
touches a file or the printer.
"""

import configparser
import os
import sys
from pathlib import Path

from PySide2.QtCore import QSize, Qt
from PySide2.QtGui import QCursor, QImage, QKeySequence, QPixmap
from PySide2.QtWidgets import (QAction, QApplication, QFileDialog, QLabel,
                               QMainWindow, QMenu, QScrollArea, QSizePolicy,
                               QToolBar, QToolButton, QWidget)

from zplcore import fonts as zpl_fonts
from zplcore import parser as zpl_parser
from zplcore import printer_io
from zplcore import view as zpl_view
from zplcore import workflow
from zplcore.model import (BarcodeElement, Document, FrameElement, ImageElement,
                           StoredGraphicElement, TextElement)
from zplcore.renderer import ZPLRenderer

from . import dialogs as qt_dialogs
from .busy import BusyBar
from .canvas import DesignCanvas

# The align commands, in menu order: the three horizontal, then the three
# vertical. The GTK frontend spells the same six the same way, and the
# conformance suite diffs the two menu bars against each other.
ALIGN_ITEMS = (
    ('left', "Align &Left"),
    ('center', "Centre &Horizontally"),
    ('right', "Align &Right"),
    ('top', "Align &Top"),
    ('middle', "Centre &Vertically"),
    ('bottom', "Align &Bottom"),
)

UNDO_LIMIT = 50
DEFAULT_ADDRESS = "192.168.50.21"
DEFAULT_PORT = 9100
DEFAULT_LABEL_INCHES = (4.0, 6.0)
PRINT_TIMEOUT = 10
PREVIEW_MAX_WIDTH = 300


def _config_path() -> Path:
    """The settings file, in the platform's user config directory."""
    base = os.environ.get('XDG_CONFIG_HOME') or os.path.expanduser('~/.config')
    return Path(base) / 'linuxzpl' / 'settings.ini'


def _fallback_config_path() -> Path:
    """Where settings go if the user config directory can't be written to.

    Some Linux environments (sandboxes, restricted containers) refuse writes
    under the user config directory outright. The project's own directory is
    always writable, since the interpreter had to read from it to get here.
    """
    return Path(__file__).resolve().parent.parent / 'settings.ini'


class ZPLDesignerWindow(QMainWindow):

    def __init__(self):
        super().__init__()

        self.printer_address = DEFAULT_ADDRESS
        self.printer_port = DEFAULT_PORT
        self.printer_dpi = zpl_fonts.DEFAULT_DPI
        # The one non-modal printer window - see on_printer_console.
        self.printer_console_dialog = None
        self.label_inches = DEFAULT_LABEL_INCHES
        self.saved_geometry = None
        self._load_settings()
        # The persisted printer, snapshotted so a session-only override (Print
        # To) can offer "Use Default" without re-reading the settings file.
        self._default_printer = (self.printer_address, self.printer_port, self.printer_dpi)
        self._place_on_screen()
        # Fonts registered before the application existed could not be handed to
        # Qt then; now there is one, so flush them.
        zpl_fonts.register_app_fonts_with_qt()

        self.current_filepath = None
        self.unsaved_changes = False
        self._update_title()
        self.renderer = ZPLRenderer()
        # The element editors are non-modal, so more than one can be on screen
        # at once. One per element, keyed by id: an open editor holds its
        # element, so the id cannot be reused while it is registered here.
        self._editors = {}

        # A label is the size last chosen in Label Settings, at whatever
        # resolution the printer is set to, so its dot dimensions depend on
        # both settings.
        document = Document(*self._inches_to_dots(*self.label_inches),
                            dpi=self.printer_dpi)
        self.canvas = DesignCanvas(document)
        self.canvas.documentChanged.connect(self.on_canvas_changed)
        self.canvas.elementDoubleClicked.connect(self.on_element_double_clicked)

        self.canvas.scaleChanged.connect(self._update_zoom_readout)
        self.canvas.zoomAt.connect(self._zoom_at)

        self.scroller = QScrollArea()
        self.scroller.setWidget(self.canvas)
        # The canvas sizes itself to the label at the current scale, so the
        # scroll area must not stretch it; it centres it instead, which is what
        # keeps the label drawn from the canvas's own origin and every
        # hit-test free of a pan offset.
        self.scroller.setWidgetResizable(False)
        self.scroller.setAlignment(Qt.AlignCenter)
        # Always on, so the width a fit is measured against does not change
        # when the scrollbar appears, which would set off a resize loop.
        self.scroller.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.scroller.viewport().installEventFilter(self)
        self.setCentralWidget(self.scroller)

        self._build_actions()
        self._build_menus()
        self._build_toolbar()

        # Its own widget, so a status message does not wipe the zoom away.
        # The busy row sits beside it for the one network call the main
        # window makes itself: Print.
        self._busy = BusyBar((self.print_action,), self.update_status, self)
        self.statusBar().addPermanentWidget(self._busy)
        self.zoom_label = QLabel()
        self.statusBar().addPermanentWidget(self.zoom_label)
        self.statusBar().showMessage("Ready")
        self._sync_view_size()

        self._undo_stack = []
        self._redo_stack = []
        self._current_snapshot = self.document.snapshot()
        self._update_undo_actions()

    # --- the view ------------------------------------------------------------

    def _place_on_screen(self):
        """Open at a size that fits the monitor, or where it was left.

        The screen being worked on, not whichever one is primary: with two
        monitors the window can otherwise open on the other one, and a window
        taller than the work area lands wherever the window manager can put it
        - which on a stacked desktop can be almost entirely off the bottom
        edge, indistinguishable from the application never starting.
        """
        screen = QApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen()
        if screen is None:
            width, height = zpl_view.PREFERRED_SIZE
            self.resize(width, height)
            return
        area = screen.availableGeometry()
        x, y, width, height = zpl_view.place_window(
            (area.x(), area.y(), area.width(), area.height()),
            self.saved_geometry)
        self.resize(width, height)
        self.move(x, y)

    def eventFilter(self, watched, event):
        if watched is self.scroller.viewport() and event.type() == event.Resize:
            self._sync_view_size()
        return super().eventFilter(watched, event)

    def _sync_view_size(self):
        """Tell the canvas how much room it has, so a fit can follow it."""
        viewport = self.scroller.viewport()
        self.canvas.set_view_size(viewport.width(), viewport.height())
        self._update_zoom_readout()

    def _update_zoom_readout(self):
        fitted = "" if self.canvas.zoom is not None else " (fit)"
        self.zoom_label.setText(
            f"{zpl_view.percent(self.canvas._scale())}%{fitted}")

    def _zoom_at(self, pointer, zoom_in):
        """Step the zoom, keeping the dot that was under the pointer under it.

        The pointer's position in the visible area is measured first: the zoom
        resizes the canvas and can re-centre it, so measuring afterwards would
        be measuring the wrong thing.
        """
        in_view = self.canvas.mapTo(self.scroller.viewport(), pointer)
        old_scale = self.canvas._scale()
        self.canvas.zoom_in() if zoom_in else self.canvas.zoom_out()
        new_scale = self.canvas._scale()
        for bar, along, across in (
                (self.scroller.horizontalScrollBar(), pointer.x(), in_view.x()),
                (self.scroller.verticalScrollBar(), pointer.y(), in_view.y())):
            bar.setValue(int(round(zpl_view.zoom_anchor(
                along, across, old_scale, new_scale))))

    def on_zoom_in(self):
        self.canvas.zoom_in()

    def on_zoom_out(self):
        self.canvas.zoom_out()

    def on_fit_label(self):
        self.canvas.set_fit(zpl_view.FIT_LABEL)

    def on_fit_width(self):
        self.canvas.set_fit(zpl_view.FIT_WIDTH)

    def on_actual_size(self):
        self.canvas.set_zoom(1.0)

    # --- convenience ---------------------------------------------------------

    @property
    def document(self) -> Document:
        return self.canvas.document

    def _update_title(self):
        """Name the file being edited in the titlebar."""
        self.setWindowTitle(workflow.window_title(self.current_filepath))

    def update_status(self, message: str):
        self.statusBar().showMessage(message)

    def show_error(self, message: str):
        qt_dialogs.show_error(self, message)

    def _inches_to_dots(self, w_in: float, h_in: float):
        """Physical size -> dots at the current printer resolution."""
        return (max(1, int(round(w_in * self.printer_dpi))),
                max(1, int(round(h_in * self.printer_dpi))))

    def _dots_to_inches(self, w_dots: int, h_dots: int):
        """Dots -> physical size at the current printer resolution."""
        return (w_dots / self.printer_dpi, h_dots / self.printer_dpi)

    # --- actions, menus, toolbar --------------------------------------------

    def _action(self, text, slot, shortcut=None, checkable=False):
        action = QAction(text, self)
        action.triggered.connect(slot)
        if shortcut is not None:
            action.setShortcut(QKeySequence(shortcut))
        action.setCheckable(checkable)
        # So the shortcut works wherever focus is in the window, not only while
        # its menu is open.
        action.setShortcutContext(Qt.WindowShortcut)
        self.addAction(action)
        return action

    def _build_actions(self):
        self.new_action = self._action("&New", self.on_new, QKeySequence.New)
        self.load_action = self._action("&Open…", self.on_load, QKeySequence.Open)
        self.save_action = self._action("&Save", self.on_save, QKeySequence.Save)
        self.save_as_action = self._action("Save &as…", self.on_save_as, "Ctrl+Shift+S")
        self.print_action = self._action("&Print", self.on_print, "Ctrl+P")
        self.quit_action = self._action("&Quit", self.close, "Ctrl+Q")

        self.undo_action = self._action("&Undo", self.on_undo, "Ctrl+Z")
        self.redo_action = self._action("&Redo", self.on_redo, "Ctrl+Shift+Z")
        # Ctrl+Y is the other conventional redo; a QAction carries one shortcut,
        # so the alternative gets its own hidden action.
        self.redo_alt_action = self._action("Redo", self.on_redo, "Ctrl+Y")
        self.redo_alt_action.setVisible(False)

        self.delete_action = self._action("&Delete", self.on_delete, QKeySequence.Delete)

        # Literal keys rather than QKeySequence.SelectAll / Deselect: the
        # second is bound on X11 only, and the two frontends have to agree.
        self.select_all_action = self._action("&Select All", self.on_select_all, "Ctrl+A")
        self.deselect_all_action = self._action("Dese&lect All", self.on_deselect_all,
                                                "Ctrl+Shift+A")
        self.invert_selection_action = self._action("&Invert Selection",
                                                    self.on_invert_selection)

        self.group_action = self._action("&Group", self.on_group, "Ctrl+G")
        self.ungroup_action = self._action("&Ungroup", self.on_ungroup, "Ctrl+Shift+G")
        # No shortcut: one more window-wide binding for a command reached
        # after a Ctrl-click, which the context menu is already under.
        self.remove_from_group_action = self._action("Remove &from Group",
                                                     self.on_remove_from_group)

        self.front_action = self._action("Bring to Front", self.on_bring_to_front, "Ctrl+Shift+]")
        self.forward_action = self._action("Bring Forward", self.on_bring_forward, "Ctrl+]")
        self.backward_action = self._action("Send Backward", self.on_send_backward, "Ctrl+[")
        self.back_action = self._action("Send to Back", self.on_send_to_back, "Ctrl+Shift+[")

        # Some layouts report the shifted symbol, so Ctrl+Shift+] arrives as
        # Ctrl+} and never matches a binding declared on ']'. Both are bound.
        self._alt_front = self._action("Bring to Front", self.on_bring_to_front, "Ctrl+}")
        self._alt_back = self._action("Send to Back", self.on_send_to_back, "Ctrl+{")
        for a in (self._alt_front, self._alt_back):
            a.setVisible(False)

        # No shortcuts on the align commands: six more window-wide bindings
        # would be six more chances to take a key away from the canvas.
        self.align_actions = [
            self._action(label, lambda _checked=False, edge=edge: self.on_align(edge))
            for edge, label in ALIGN_ITEMS]

        self.zoom_in_action = self._action("Zoom &In", self.on_zoom_in,
                                           QKeySequence.ZoomIn)
        # Ctrl++ needs Shift on most layouts, so the unshifted key is bound too;
        # a QAction carries one shortcut, so the alias gets its own action.
        self.zoom_in_alt_action = self._action("Zoom In", self.on_zoom_in, "Ctrl+=")
        self.zoom_in_alt_action.setVisible(False)
        self.zoom_out_action = self._action("Zoom &Out", self.on_zoom_out,
                                            QKeySequence.ZoomOut)
        self.fit_label_action = self._action("Fit &Label", self.on_fit_label, "Ctrl+0")
        self.fit_width_action = self._action("Fit &Width", self.on_fit_width, "Ctrl+9")
        self.actual_size_action = self._action("&Actual Size", self.on_actual_size,
                                               "Ctrl+1")

        self.label_size_action = self._action("Label Size…", self.on_label_size)
        self.default_printer_action = self._action("Default Printer…", self.on_default_printer)
        self.local_fonts_action = self._action("Local Fonts…", self.on_local_fonts)
        self.printer_fonts_action = self._action("Fonts…", self.on_printer_fonts)
        self.printer_graphics_action = self._action("Graphics…", self.on_printer_graphics)
        self.printer_objects_action = self._action("Objects…", self.on_printer_objects)
        self.printer_console_action = self._action("Console…", self.on_printer_console)

        self.session_printer_action = self._action(
            "&Set Printer for This Session…", self.on_session_printer)

    def _build_menus(self):
        menubar = self.menuBar()

        file_menu = menubar.addMenu("&File")
        file_menu.addAction(self.new_action)
        file_menu.addAction(self.load_action)
        file_menu.addSeparator()
        file_menu.addAction(self.save_action)
        file_menu.addAction(self.save_as_action)
        file_menu.addSeparator()
        file_menu.addAction(self.print_action)
        # A submenu rather than a flat item: this is where printer-related
        # actions beyond the one session override belong as they show up.
        printer_settings_menu = file_menu.addMenu("Prin&ter Settings")
        printer_settings_menu.addAction(self.session_printer_action)
        file_menu.addSeparator()
        file_menu.addAction(self.quit_action)

        edit_menu = menubar.addMenu("&Edit")
        edit_menu.addAction(self.undo_action)
        edit_menu.addAction(self.redo_action)
        edit_menu.addSeparator()
        edit_menu.addAction(self.delete_action)
        edit_menu.addSeparator()
        edit_menu.addAction(self.select_all_action)
        edit_menu.addAction(self.deselect_all_action)
        edit_menu.addAction(self.invert_selection_action)
        edit_menu.addSeparator()
        edit_menu.addAction(self.group_action)
        edit_menu.addAction(self.ungroup_action)
        edit_menu.addAction(self.remove_from_group_action)
        edit_menu.addSeparator()
        for action in (self.front_action, self.forward_action,
                       self.backward_action, self.back_action):
            edit_menu.addAction(action)
        edit_menu.addSeparator()
        align_menu = edit_menu.addMenu("&Align")
        for action in self.align_actions:
            align_menu.addAction(action)
        # Re-evaluated each time the menu opens, since the selection and the
        # z-order both move underneath it.
        edit_menu.aboutToShow.connect(self._update_edit_menu)

        view_menu = menubar.addMenu("&View")
        view_menu.addAction(self.zoom_in_action)
        view_menu.addAction(self.zoom_out_action)
        view_menu.addSeparator()
        view_menu.addAction(self.fit_label_action)
        view_menu.addAction(self.fit_width_action)
        view_menu.addAction(self.actual_size_action)

        printer_menu = menubar.addMenu("&Printer")
        printer_menu.addAction(self.printer_graphics_action)
        printer_menu.addAction(self.printer_fonts_action)
        printer_menu.addAction(self.printer_objects_action)
        printer_menu.addAction(self.printer_console_action)

        settings_menu = menubar.addMenu("&Settings")
        settings_menu.addAction(self.label_size_action)
        settings_menu.addAction(self.default_printer_action)
        settings_menu.addAction(self.local_fonts_action)

    def _build_toolbar(self):
        toolbar = QToolBar("Elements", self)
        toolbar.setMovable(False)
        toolbar.setIconSize(QSize(16, 16))
        self.addToolBar(toolbar)

        toolbar.addAction(self._action("+ Text", self.on_add_text))
        toolbar.addAction(self._action("+ Time", self.on_add_time))
        toolbar.addAction(self._action("+ Serial", self.on_add_serial))
        toolbar.addAction(self._action("+ Numbered", self.on_add_numbered))
        toolbar.addAction(self._action("+ Frame", self.on_add_frame))
        toolbar.addAction(self._action("+ Barcode", self.on_add_barcode))
        toolbar.addAction(self._action("+ Image", self.on_add_image))
        toolbar.addAction(self._action("+ Graphic", self.on_add_stored_graphic))
        toolbar.addSeparator()
        toolbar.addAction(self.delete_action)

        toolbar.addSeparator()
        toolbar.addAction(self._action("\u2212", self.on_zoom_out))
        toolbar.addAction(self._action("Fit", self.on_fit_label))
        toolbar.addAction(self._action("+", self.on_zoom_in))

        # One button opening the same six commands the Edit menu holds, rather
        # than six buttons: the toolbar is text-labelled, and there are no
        # object-align icons in the icon theme to label them with. The actions
        # are the same objects, so the two menus can never disagree.
        toolbar.addSeparator()
        align_button = QToolButton(self)
        align_button.setText("Align \u25be")
        align_button.setToolTip("Line the selection up (Edit \u25b8 Align)")
        align_button.setPopupMode(QToolButton.InstantPopup)
        align_popup = QMenu(align_button)
        for action in self.align_actions:
            align_popup.addAction(action)
        # The popup can open without the Edit menu ever having been shown, so
        # it re-evaluates the same enable rules on the way up.
        align_popup.aboutToShow.connect(self._update_edit_menu)
        align_button.setMenu(align_popup)
        self.align_button = align_button
        toolbar.addWidget(align_button)

        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        toolbar.addWidget(spacer)

        self.undo_button_action = self._action("↶ Undo", self.on_undo)
        self.redo_button_action = self._action("↷ Redo", self.on_redo)
        toolbar.addAction(self.undo_button_action)
        toolbar.addAction(self.redo_button_action)

    def _update_edit_menu(self):
        """Grey out the actions that need a selection, or a place to move to."""
        doc = self.document
        self.delete_action.setEnabled(bool(doc.selection))
        self.select_all_action.setEnabled(len(doc.selection) < len(doc.elements))
        self.deselect_all_action.setEnabled(bool(doc.selection))
        self.invert_selection_action.setEnabled(bool(doc.elements))
        self.group_action.setEnabled(doc.can_group())
        self.ungroup_action.setEnabled(doc.can_ungroup())
        self.remove_from_group_action.setEnabled(doc.can_remove_from_group())
        for action in self.align_actions:
            action.setEnabled(bool(doc.selection))
        for action in (self.front_action, self.forward_action):
            action.setEnabled(doc.can_raise())
        for action in (self.backward_action, self.back_action):
            action.setEnabled(doc.can_lower())

    # --- history -------------------------------------------------------------

    def on_canvas_changed(self):
        """Record one undo entry and mark the document unsaved."""
        self.unsaved_changes = True
        # the snapshot standing before this change is what undo goes back to
        self._undo_stack.append(self._current_snapshot)
        del self._undo_stack[:-UNDO_LIMIT]
        self._redo_stack.clear()
        self._current_snapshot = self.document.snapshot()
        self._update_undo_actions()

    def on_undo(self):
        if not self._undo_stack:
            return
        self._redo_stack.append(self._current_snapshot)
        self._current_snapshot = self._undo_stack.pop()
        self._apply_snapshot(self._current_snapshot)
        self.update_status("Undo")

    def on_redo(self):
        if not self._redo_stack:
            return
        self._undo_stack.append(self._current_snapshot)
        self._current_snapshot = self._redo_stack.pop()
        self._apply_snapshot(self._current_snapshot)
        self.update_status("Redo")

    def _apply_snapshot(self, snapshot):
        self._close_element_editors()
        self.document.restore(snapshot)
        self.unsaved_changes = True
        self.canvas._sync_size()
        self.canvas.update()
        self._update_undo_actions()

    def _reset_history(self):
        """Start a fresh history, so it never spans a file load."""
        self._close_element_editors()
        self._undo_stack = []
        self._redo_stack = []
        self._current_snapshot = self.document.snapshot()
        self._update_undo_actions()

    def _update_undo_actions(self):
        """Enable the undo and redo controls only when they would do something."""
        can_undo, can_redo = bool(self._undo_stack), bool(self._redo_stack)
        for action in (self.undo_action, self.undo_button_action):
            action.setEnabled(can_undo)
        for action in (self.redo_action, self.redo_button_action,
                       self.redo_alt_action):
            action.setEnabled(can_redo)

    # --- elements ------------------------------------------------------------

    def on_add_text(self):
        self.document.add_text_element("New Text")
        self.canvas.commit()

    def on_add_time(self):
        self.document.add_time_element()
        self.canvas.commit()

    def on_add_serial(self):
        self.document.add_serial_element()
        self.canvas.commit()

    def on_add_numbered(self):
        self.document.add_numbered_element()
        self.canvas.commit()

    def on_add_frame(self):
        self.document.add_frame_element()
        self.canvas.commit()

    def on_add_barcode(self):
        self.document.add_barcode_element()
        self.canvas.commit()

    def on_add_image(self):
        path = qt_dialogs.choose_image_file(self, "Load Image")
        if not path:
            return
        self.document.add_image_element(path)
        self.canvas.commit()

    def on_add_stored_graphic(self):
        self.document.add_stored_graphic_element()
        self.canvas.commit()

    def on_delete(self):
        # Every selected element goes, so every editor open on one has to be
        # closed - an editor must never outlive the element it is editing.
        doomed = list(self.document.selection)
        if self.document.remove_selected():
            for element in doomed:
                self._close_editor_for(element)
            self.canvas.commit()

    # Selection commands change the selection and never the document, so
    # they repaint and record nothing - the same as a click or a band.

    def on_select_all(self):
        if self.document.select_all():
            self.canvas.update()

    def on_deselect_all(self):
        if self.document.selection:
            self.document.clear_selection()
            self.canvas.update()

    def on_invert_selection(self):
        if self.document.invert_selection():
            self.canvas.update()

    def _reorder(self, moved: bool):
        if moved:
            self.canvas.commit()

    def on_bring_to_front(self):
        self._reorder(self.document.bring_to_front())

    def on_bring_forward(self):
        self._reorder(self.document.bring_forward())

    def on_send_backward(self):
        self._reorder(self.document.send_backward())

    def on_send_to_back(self):
        self._reorder(self.document.send_to_back())

    def on_align(self, edge: str):
        if self.document.align_selected(edge):
            self.canvas.commit()

    def on_group(self):
        if self.document.group_selected():
            self.canvas.commit()

    def on_ungroup(self):
        if self.document.ungroup_selected():
            self.canvas.commit()

    def on_remove_from_group(self):
        if self.document.remove_from_group():
            self.canvas.commit()

    def on_element_double_clicked(self, element):
        """Open the editor for whichever element was double-clicked."""
        open_editor = self._editors.get(id(element))
        if open_editor is not None:
            # Already being edited. Raising the window it is in beats opening a
            # second one onto the same element, where whichever was accepted
            # last would silently undo the other.
            open_editor.raise_()
            open_editor.activateWindow()
            return

        def committed():
            self.canvas.commit()

        def text_committed():
            self._register_label_fonts()
            self.canvas.commit()

        if isinstance(element, TextElement) and element.clock_format:
            editor = qt_dialogs.edit_time_dialog(self, element, self.document,
                                                 on_accept=text_committed)
        elif isinstance(element, TextElement) and element.serial_increment is not None:
            editor = qt_dialogs.edit_serial_dialog(self, element, self.document,
                                                   on_accept=text_committed)
        elif isinstance(element, TextElement) and element.field_number is not None:
            editor = qt_dialogs.edit_numbered_dialog(self, element, self.document,
                                                      on_accept=text_committed)
        elif isinstance(element, TextElement):
            editor = qt_dialogs.edit_text_dialog(self, element, self.document,
                                                 on_accept=text_committed)
        elif isinstance(element, FrameElement):
            editor = qt_dialogs.edit_frame_dialog(self, element,
                                                  on_accept=committed)
        elif isinstance(element, BarcodeElement):
            editor = qt_dialogs.edit_barcode_dialog(self, element,
                                                    on_accept=committed)
        elif isinstance(element, ImageElement):
            # A file chooser rather than a form of fields, so it stays modal;
            # there is nothing to leave open alongside the canvas.
            path = qt_dialogs.choose_image_file(self, "Replace Image")
            if path:
                element.image_path = path
                element.reload()
                self.canvas.commit()
            return
        elif isinstance(element, StoredGraphicElement):
            editor = qt_dialogs.edit_stored_graphic_dialog(self, element,
                                                            on_accept=committed)
        else:
            return

        self._editors[id(element)] = editor
        editor.finished.connect(lambda _r, key=id(element):
                                self._editors.pop(key, None))

    def _close_editor_for(self, element):
        """Close the editor open on one element, if there is one."""
        editor = self._editors.pop(id(element), None) if element else None
        if editor is not None:
            # close() rather than reject(): both refuse the edit, but only
            # close() honours WA_DeleteOnClose and actually frees the window.
            editor.close()

    def _close_element_editors(self):
        """Close every open editor.

        Undo, redo and loading a file all replace the element objects the open
        editors hold, so an editor left up would write its fields into an
        element the document no longer has - the edit would vanish with no
        error to show for it.
        """
        for editor in list(self._editors.values()):
            editor.close()
        self._editors.clear()

    def _register_label_fonts(self):
        """Tell the preview renderer about every font this label uses."""
        for element in self.document.elements:
            name = getattr(element, 'printer_font_name', None)
            path = getattr(element, 'font_path', None)
            if name and path:
                self.renderer.register_font(name, path)

    # --- settings dialogs ----------------------------------------------------

    def on_label_size(self):
        result = qt_dialogs.label_size_dialog(self, self.document, self.printer_dpi)
        if result is None:
            return
        self.apply_label_settings(*result)

    def apply_label_settings(self, width, height, dpi, w_in, h_in,
                             transform=None, quantity=None):
        """One accepted visit to Label Settings, whatever it changed.

        The resolution and the size can both have moved in the same visit, and
        they interact: reconciling may rescale the whole design, label included.
        So the resolution is settled first, against the design the user was
        actually looking at - the prompt quotes what keeping the dots would
        measure, and that has to describe the label on the canvas rather than
        the one about to replace it - and the size typed in the dialog is then
        laid on top of whatever it did.
        """
        old_dpi = self.printer_dpi
        self.printer_dpi = dpi
        # Only the DPI slot of the default moves - address/port stay whatever
        # the persisted default already was, so a session override on those
        # (Printer Settings) survives a Label Settings visit untouched.
        self._default_printer = (self._default_printer[0], self._default_printer[1], dpi)
        self.label_inches = (w_in, h_in)
        if transform is not None:
            self.document.transform = transform
        if quantity is not None:
            self.document.print_quantity = quantity
        # Written before the prompt, as the printer dialog writes its own: the
        # prompt is modal and can be dismissed by the window manager, and the
        # choice the user already made should be on disk by then.
        self._save_settings()

        note = self._offer_dpi_rescale() if dpi != old_dpi else None

        # Shrinking clamps elements to the new bounds, which is itself part of
        # the change being recorded. Last, so the size typed in the dialog wins
        # over the one a rescale just moved.
        self.document.set_label_size(width, height)
        self.canvas._sync_size()
        self.canvas.commit()          # one history entry for the whole visit
        message = f"Label size set to {width}x{height}"
        self.update_status(f"{message} - {note}" if note else message)

    def on_default_printer(self):
        # Opened with the persisted default, not the printer currently in
        # effect: a session override (Printer Settings) must never leak into
        # this dialog and get re-saved as the new default just by clicking OK.
        default_address, default_port, default_dpi = self._default_printer
        result = qt_dialogs.printer_settings_dialog(
            self, default_address, default_port, default_dpi,
            title="Default Printer")
        if result is None:
            return
        address, port, dpi = result
        old_dpi = self.printer_dpi
        self.printer_address, self.printer_port, self.printer_dpi = address, port, dpi
        self._default_printer = (address, port, dpi)
        self._save_settings()
        self.update_status(f"Printer set to {self.printer_address}:{self.printer_port}")
        if dpi != old_dpi:
            note = self._offer_dpi_rescale()
            if note:
                self.canvas._sync_size()
                self.canvas.commit()
                self.update_status(note[0].upper() + note[1:])

    def on_local_fonts(self):
        qt_dialogs.LocalFontsDialog(self).exec_()

    def on_session_printer(self):
        """Print To this session's printer, without touching the persisted default."""
        result = qt_dialogs.printer_settings_dialog(
            self, self.printer_address, self.printer_port, self.printer_dpi,
            default=self._default_printer)
        if result is None:
            return
        address, port, dpi = result
        old_dpi = self.printer_dpi
        self.printer_address, self.printer_port, self.printer_dpi = address, port, dpi
        self.update_status(f"Printing to {self.printer_address}:{self.printer_port} for this session")
        if dpi != old_dpi:
            note = self._offer_dpi_rescale()
            if note:
                self.canvas._sync_size()
                self.canvas.commit()
                self.update_status(note[0].upper() + note[1:])

    def on_printer_fonts(self):
        def on_uploaded(name, path):
            self.renderer.register_font(name, path)

        dialog = qt_dialogs.PrinterFontsDialog(
            self, self.printer_address, self.printer_port, on_uploaded)
        dialog.exec_()

    def on_printer_graphics(self):
        # Storing/retrieving/deleting a graphic changes no Document state, so
        # this is a plain repaint - canvas.update(), never canvas.commit() -
        # so an ^XG/^IM/^IL that now resolves differently is shown without
        # marking the file dirty or pushing a bogus undo entry.
        dialog = qt_dialogs.PrinterGraphicsDialog(
            self, self.printer_address, self.printer_port,
            on_changed=lambda *_a: self.canvas.update())
        dialog.exec_()

    def on_printer_objects(self):
        # Same reasoning as on_printer_graphics: deleting an object changes
        # no Document state, so this is a plain repaint, never a commit().
        dialog = qt_dialogs.PrinterObjectsDialog(
            self, self.printer_address, self.printer_port,
            on_changed=lambda *_a: self.canvas.update())
        dialog.exec_()

    def on_printer_console(self):
        # Non-modal and a singleton, unlike the other printer dialogs: this
        # one is meant to stay open while the user keeps working elsewhere
        # in the window, so a second click focuses it rather than stacking
        # another one.
        if self.printer_console_dialog is not None:
            self.printer_console_dialog.raise_()
            self.printer_console_dialog.activateWindow()
            return
        dialog = qt_dialogs.PrinterConsoleDialog(self)
        dialog.destroyed.connect(lambda *_a: setattr(self, 'printer_console_dialog', None))
        self.printer_console_dialog = dialog
        dialog.show()

    # --- files ---------------------------------------------------------------

    def check_unsaved_changes(self) -> bool:
        """Ask what to do with unsaved work. True means it is safe to continue."""
        return workflow.unsaved_changes_gate(
            self.unsaved_changes,
            lambda: qt_dialogs.ask_unsaved_changes(self),
            self.save_file_or_ask_for_filename)

    def on_new(self):
        if not self.check_unsaved_changes():
            return
        document = Document(*self._inches_to_dots(*self.label_inches),
                            dpi=self.printer_dpi)
        self.canvas.set_document(document)
        self.current_filepath = None
        self.unsaved_changes = False
        self._update_title()
        self._reset_history()
        self.update_status("Ready")

    def on_load(self):
        if not self.check_unsaved_changes():
            return
        path = self._zpl_open_dialog()
        if path:
            self.load_zpl_file(path)

    def _zpl_open_dialog(self):
        """A .zpl chooser that previews the selected file as a rendered label."""
        dialog = QFileDialog(self, "Open ZPL File")
        dialog.setNameFilter(qt_dialogs.ZPL_FILTER)
        dialog.setAcceptMode(QFileDialog.AcceptOpen)
        dialog.setFileMode(QFileDialog.ExistingFile)
        # The native dialog has no place to put a preview widget.
        dialog.setOption(QFileDialog.DontUseNativeDialog, True)

        preview = QLabel("No preview")
        preview.setAlignment(Qt.AlignCenter)
        preview.setMinimumWidth(PREVIEW_MAX_WIDTH)
        layout = dialog.layout()
        if layout is not None:
            layout.addWidget(preview, 0, layout.columnCount(), layout.rowCount(), 1)

        def update_preview(path):
            preview.setPixmap(QPixmap())
            if not path or not os.path.isfile(path):
                preview.setText("No preview")
                return
            try:
                image = self._new_renderer().render_from_file(path)
                image = image.convert('RGB')
                if image.width > PREVIEW_MAX_WIDTH:
                    ratio = PREVIEW_MAX_WIDTH / image.width
                    image = image.resize((PREVIEW_MAX_WIDTH,
                                          max(1, int(image.height * ratio))))
                data = image.tobytes('raw', 'RGB')
                # .copy() so the pixmap does not outlive `data`
                qimage = QImage(data, image.width, image.height,
                                3 * image.width, QImage.Format_RGB888).copy()
                preview.setPixmap(QPixmap.fromImage(qimage))
            except Exception:
                # A file that cannot be rendered is not an error here - the
                # user may still want to open it and see what parses.
                preview.setText("No preview")

        dialog.currentChanged.connect(update_preview)
        if dialog.exec_() != QFileDialog.Accepted:
            return None
        chosen = dialog.selectedFiles()
        return chosen[0] if chosen else None

    def on_save(self):
        self.save_file_or_ask_for_filename()

    def on_save_as(self):
        self.save_dialog()

    def _document_zpl(self):
        """The document's ZPL, or None when there is nothing worth saving."""
        try:
            content = self.document.to_zpl()
        except Exception as e:
            self.show_error(f"Failed to generate ZPL: {e}")
            return None
        if self.document.is_empty():
            self.show_error("No content to save")
            return None
        return content

    def save_file_or_ask_for_filename(self) -> bool:
        content = self._document_zpl()
        if content is None:
            return False
        if self.current_filepath:
            return self.save_zpl_file(self.current_filepath, content)
        return self.save_dialog()

    def save_dialog(self) -> bool:
        content = self._document_zpl()
        if content is None:
            return False
        start = self.current_filepath or "untitled.zpl"
        # A name is only checked for an overwrite once its suffix is settled,
        # so declining a replace comes back here rather than dropping the save.
        while True:
            path, _ = QFileDialog.getSaveFileName(
                self, "Save ZPL File", start, qt_dialogs.ZPL_FILTER)
            if not path:
                return False
            start = workflow.save_filename(path)
            chosen = workflow.confirm_save_path(
                path, lambda p: qt_dialogs.ask_overwrite(self, p))
            if chosen:
                return self.save_zpl_file(chosen, content)

    def save_zpl_file(self, filepath: str, content: str) -> bool:
        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(content)
        except Exception as e:
            # The flag stays set, so the work is not treated as safe.
            self.show_error(f"Failed to save file: {e}")
            self.update_status("Save failed")
            return False
        self.current_filepath = filepath
        self.unsaved_changes = False
        self._update_title()
        self.update_status(f"Saved: {os.path.basename(filepath)}")
        return True

    def load_zpl_file(self, filepath: str):
        try:
            content, code_page = zpl_parser.read_file(filepath)
            document, loaded_dpi = zpl_parser.parse_zpl(content, self.renderer)
            self.canvas.set_document(document)
            self.current_filepath = filepath
            self._update_title()
            rescaled = self._offer_dpi_rescale(loaded_dpi)
            self._register_label_fonts()
            self.canvas.update()
            # Both facts are worth reporting, and the load message would
            # otherwise overwrite the rescale one the instant it appeared.
            loaded = f"Loaded: {os.path.basename(filepath)}"
            self.update_status(f"{loaded} - {rescaled}" if rescaled else loaded)
            # Building elements while parsing does not count as an edit, but a
            # rescale does: the user was asked and answered, and the elements no
            # longer match the file on disk. Clearing the flag here threw that
            # answer away on close without a word, and the same question came
            # back on the very next open - every open, for as long as the file
            # went on recording the resolution it was drawn for.
            self.unsaved_changes = bool(rescaled)
            self._reset_history()
            if code_page:
                qt_dialogs.notify_decoded(self, code_page)
            workflow.warn_unsupported(
                content, lambda cmds: qt_dialogs.warn_unsupported(self, cmds),
                lambda found: qt_dialogs.warn_control_redefined(self, found))
        except Exception as e:
            self.show_error(f"Failed to load file: {e}")
            self.update_status("Error loading file")

    def _offer_dpi_rescale(self, loaded_dpi=workflow._FROM_DOCUMENT):
        """Settle the design against the printer's resolution.

        Called both when a file records a different resolution and when the
        printer's own resolution changes underneath an open design; the rule is
        the same either way and lives in the core.
        """
        def ask(old, printer_dpi, assumed, w_in, h_in):
            return qt_dialogs.ask_dpi_rescale(self, old, printer_dpi, assumed,
                                              w_in, h_in)

        note = workflow.reconcile_dpi(self.document, self.printer_dpi, ask,
                                      file_dpi=loaded_dpi)
        if note:
            self.canvas._sync_size()
        return note

    # --- printing ------------------------------------------------------------

    def _new_renderer(self) -> ZPLRenderer:
        """A renderer preloaded with the fonts this session knows about.

        A bare ZPLRenderer has an empty font_registry, so every ^A@ would fall
        back to the default face and custom fonts would never show in a preview.
        """
        renderer = ZPLRenderer(width=self.document.label_width,
                               height=self.document.label_height)
        for name, path in self.renderer.font_registry.items():
            renderer.register_font(name, path)
        for element in self.document.elements:
            name = getattr(element, 'printer_font_name', None)
            path = getattr(element, 'font_path', None)
            if name and path:
                renderer.register_font(name, path)
        if self.renderer.custom_font_path:
            renderer.set_font(self.renderer.custom_font_path)
        return renderer

    def on_print(self):
        """Print in up to three steps, each network one off the GUI thread
        behind the status bar's busy row: ask the printer which fonts it has,
        prompt if any are missing (on the GUI thread, as a prompt must be),
        then upload whatever the user chose to and send the label.
        """
        try:
            content = self.document.to_zpl(explicit_flips=True)
        except Exception as e:
            self.show_error(f"Failed to generate ZPL: {e}")
            return
        address, port = self.printer_address, self.printer_port

        def send_label(uploadable):
            self.update_status("Printing...")

            def work(cancel):
                if uploadable:
                    workflow.upload_fonts(uploadable, address, port,
                                          self._busy.report, cancel)
                    self._busy.report("Printing...")
                printer_io.send(address, port, content.encode('utf-8'),
                                PRINT_TIMEOUT, cancel=cancel)

            def done(_result, error):
                if isinstance(error, printer_io.Cancelled):
                    self.update_status("Printing cancelled")
                elif error is not None:
                    self.show_error(str(error))
                    self.update_status("Printing failed")
                else:
                    self.update_status(f"Sent to {address}:{port}")

            self._busy.run(work, done)

        def checked(result, error):
            if isinstance(error, printer_io.Cancelled):
                self.update_status("Printing cancelled")
                return
            if error is not None:
                self.show_error(str(error))
                self.update_status("Printing failed")
                return
            missing, uploadable = (None, {}) if result is None else result
            if result is not None and not missing:
                send_label({})
                return
            text, detail = workflow.font_problem_prompt(missing)
            answer = qt_dialogs.ask_font_problem(self, text, detail, uploadable)
            if answer == 'upload':
                send_label(uploadable)
            elif answer == 'print':
                send_label({})
            else:
                self.update_status("Printing cancelled")

        if not self.document.font_sources():
            send_label({})
            return
        self.update_status("Checking printer fonts...")
        self._busy.run(lambda cancel: workflow.missing_printer_fonts(
            self.document, address, port, cancel=cancel), checked)

    # --- settings file -------------------------------------------------------

    def _load_settings(self):
        parser = configparser.ConfigParser()
        try:
            parser.read([_config_path(), _fallback_config_path()])
            self.printer_address = parser.get(
                'printer', 'address', fallback=self.printer_address)
            self.printer_port = parser.getint(
                'printer', 'port', fallback=self.printer_port)
            dpi = parser.getint('printer', 'dpi', fallback=self.printer_dpi)
            if dpi > 0:
                self.printer_dpi = dpi
            if parser.has_section('window'):
                self.saved_geometry = tuple(
                    parser.getint('window', key) for key in ('x', 'y', 'width', 'height'))
            # Read last: getfloat raises on a malformed value rather than
            # falling back, and everything after it in this try would be lost.
            w_in = parser.getfloat('label', 'width_in',
                                   fallback=self.label_inches[0])
            h_in = parser.getfloat('label', 'height_in',
                                   fallback=self.label_inches[1])
            # The range the dialog allows. A hand-edited value outside it would
            # otherwise open the designer onto a one-dot label.
            if 0.5 <= w_in <= 25 and 0.5 <= h_in <= 25:
                self.label_inches = (w_in, h_in)
        except (configparser.Error, OSError, ValueError):
            # A missing or corrupt config must never block startup
            pass

    def _save_settings(self):
        # Two candidates: the user config directory, then the project's own
        # directory if that one refuses the write (some Linux environments
        # deny it outright). A fresh parser each time, so a stale value read
        # back from the fallback file on a later attempt can't clobber a
        # value already set for this save.
        last_error = None
        for path in (_config_path(), _fallback_config_path()):
            parser = configparser.ConfigParser()
            try:
                # Read first so unrelated sections are preserved
                parser.read(path)
                if not parser.has_section('printer'):
                    parser.add_section('printer')
                # The persisted default, not self.printer_address/_port/_dpi:
                # those are whatever is active for printing right now, which a
                # session override (Printer Settings) deliberately changes
                # without this ever running. Only Default Printer and Label
                # Settings are allowed to move self._default_printer, and they
                # do so before calling this.
                parser.set('printer', 'address', self._default_printer[0])
                parser.set('printer', 'port', str(self._default_printer[1]))
                parser.set('printer', 'dpi', str(self._default_printer[2]))
                if not parser.has_section('label'):
                    parser.add_section('label')
                # Inches, not dots: dots only mean a size once a resolution is
                # fixed, and the resolution beside them is the very thing that can
                # change between sessions. Two decimals is the dialog's own
                # precision, so the file round-trips what was typed.
                parser.set('label', 'width_in', f"{self.label_inches[0]:.2f}")
                parser.set('label', 'height_in', f"{self.label_inches[1]:.2f}")
                if self.saved_geometry is not None:
                    if not parser.has_section('window'):
                        parser.add_section('window')
                    for key, value in zip(('x', 'y', 'width', 'height'),
                                          self.saved_geometry):
                        parser.set('window', key, str(int(value)))
                path.parent.mkdir(parents=True, exist_ok=True)
                with open(path, 'w', encoding='utf-8') as f:
                    parser.write(f)
                return
            except configparser.Error as e:
                self.show_error(f"Could not save settings: {e}")
                return
            except OSError as e:
                last_error = e
        self.show_error(f"Could not save settings: {last_error}")

    # --- window --------------------------------------------------------------

    def closeEvent(self, event):
        if not self.check_unsaved_changes():
            event.ignore()
            return
        # Where the window was left, so it opens there next time. Saved on the
        # way out because nothing else in the session has reason to write the
        # settings file, and a resize is not worth a write of its own.
        frame = self.geometry()
        self.saved_geometry = (frame.x(), frame.y(), frame.width(), frame.height())
        self._save_settings()
        event.accept()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName(workflow.APP_TITLE)
    window = ZPLDesignerWindow()
    window.show()
    # Ask for the front. Started from an editor running full screen, a new
    # window can otherwise map behind it and look as though nothing happened.
    window.raise_()
    window.activateWindow()
    return app.exec_()


if __name__ == '__main__':
    sys.exit(main())
