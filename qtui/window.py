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

from PySide2.QtCore import QByteArray, QMimeData, QPoint, QSize, Qt, QTimer
from PySide2.QtGui import QCursor, QDrag, QIcon, QImage, QKeySequence, QPixmap
from PySide2.QtWidgets import (QAction, QApplication, QFileDialog, QHBoxLayout, QLabel,
                               QMainWindow, QMenu, QMessageBox, QScrollArea,
                               QToolBar, QToolButton, QWidget)

from zplcore import fonts as zpl_fonts
from zplcore import grid as zpl_grid
from zplcore import parser as zpl_parser
from zplcore import tips as zpl_tips
from zplcore import connection as zpl_connection
from zplcore import printer_io
from zplcore.connection import Connection
from zplcore import view as zpl_view
from zplcore import workflow
from zplcore import graphic_symbols
from zplcore.model import Document
from zplcore.renderer import ZPLRenderer

from . import dialogs as qt_dialogs
from .busy import BusyBar
from .canvas import PALETTE_MIME, DesignCanvas, to_qimage
from .properties import PropertiesPanel
from .rulers import RULER_SIZE, Ruler, RulerCorner

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

        # Network or USB - see zplcore.connection.
        self.printer_connection = Connection.network(DEFAULT_ADDRESS,
                                                     DEFAULT_PORT)
        self.printer_dpi = zpl_fonts.DEFAULT_DPI
        # Which printer memory this app's own fonts are written to and
        # uploaded to - see Document.font_device, which every document is
        # given a copy of.
        self.printer_font_device = zpl_fonts.DEFAULT_FONT_DEVICE
        # The two non-modal printer windows - see on_printer_console and
        # on_printer_status. Both stay open while the user keeps working, so
        # both are singletons rather than stacking a second copy per click.
        self.printer_console_dialog = None
        self.printer_status_dialog = None
        self.label_inches = DEFAULT_LABEL_INCHES
        self.saved_geometry = None
        # The dock layout - the Properties panel's side, size and whether it
        # floats - as saveState() gave it, or None for the default.
        self.saved_dock_state = None
        self._load_settings()
        # The grid over the canvas and the switches that use it. Kept in its
        # own section, written the moment it changes, like Open Recent.
        self.grid = zpl_grid.load_grid(self._settings_files())
        # The persisted printer, snapshotted so the Print window, whose printer
        # is for this session only, can offer "Use Default" without re-reading
        # the settings file.
        self._default_printer = (self.printer_connection, self.printer_dpi,
                                 self.printer_font_device)
        self._place_on_screen()
        # Fonts registered before the application existed could not be handed to
        # Qt then; now there is one, so flush them.
        zpl_fonts.register_app_fonts_with_qt()

        self.current_filepath = None
        self.unsaved_changes = False
        self._update_title()
        self.renderer = ZPLRenderer()

        # A label is the size last chosen in Label Settings, at whatever
        # resolution the printer is set to, so its dot dimensions depend on
        # both settings.
        document = Document(*self._inches_to_dots(*self.label_inches),
                            dpi=self.printer_dpi)
        document.font_device = self.printer_font_device
        self.canvas = DesignCanvas(document)
        self.canvas.grid = self.grid
        self.canvas.documentChanged.connect(self.on_canvas_changed)
        self.canvas.elementDoubleClicked.connect(self.on_element_double_clicked)
        self.canvas.editRequested.connect(self._on_edit_requested)

        self.canvas.scaleChanged.connect(self._update_zoom_readout)
        self.canvas.zoomAt.connect(self._zoom_at)
        self.canvas.elementDropped.connect(self.on_element_dropped)

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
        # The rulers, in margins the scroll area keeps outside what it scrolls.
        # Made before the viewport is watched, which places them.
        self.top_ruler = Ruler(self.canvas, self.grid, True, self.scroller)
        self.left_ruler = Ruler(self.canvas, self.grid, False, self.scroller)
        self.ruler_corner = RulerCorner(self.grid, self.scroller)
        self.scroller.viewport().installEventFilter(self)
        self.setCentralWidget(self.scroller)
        for bar in (self.scroller.horizontalScrollBar(),
                    self.scroller.verticalScrollBar()):
            bar.valueChanged.connect(self._update_rulers)
        self.canvas.scaleChanged.connect(self._update_rulers)
        self.canvas.pointerMoved.connect(self._on_pointer_moved)

        # The selection's properties, edited live, beside the canvas. Built
        # before the actions, every one of which ends its burst of edits.
        self.properties = PropertiesPanel(self)
        self.addDockWidget(Qt.RightDockWidgetArea, self.properties)

        self._build_actions()
        self._build_menus()
        self._build_toolbar()
        self._restore_docks()

        # Its own widget, so a status message does not wipe the zoom away.
        # The busy row sits beside it for the one network call the main
        # window makes itself: Print.
        self._busy = BusyBar((self.print_action, self.quick_print_action,
                              *self.upload_template_actions),
                             self.update_status, self)
        self.statusBar().addPermanentWidget(self._busy)
        # Where the pointer is on the label, in the grid's unit, beside the zoom
        self.pointer_label = QLabel()
        self.pointer_label.setContentsMargins(0, 0, 12, 0)
        self.statusBar().addPermanentWidget(self.pointer_label)
        self.zoom_label = QLabel()
        self.statusBar().addPermanentWidget(self.zoom_label)
        self.statusBar().showMessage("Ready")
        self._show_rulers()

        self._undo_stack = []
        self._redo_stack = []
        self._current_snapshot = self.document.snapshot()
        self._update_undo_actions()

    def _restore_docks(self):
        """Put the Properties panel where it was left, or docked on the right.

        Floating, it starts hidden: it is asked for when wanted (a double
        click, Properties), where docked it is part of the window.
        """
        if self.saved_dock_state:
            try:
                self.restoreState(QByteArray.fromBase64(
                    self.saved_dock_state.encode('ascii')))
            except (ValueError, UnicodeError):
                pass                # a hand-edited value: the default layout
        else:
            self.resizeDocks([self.properties], [420], Qt.Horizontal)
        if self.properties.isFloating():
            self.properties.hide()

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
        if watched is self.scroller.viewport():
            if event.type() == event.Resize:
                self._sync_view_size()
            if event.type() in (event.Resize, event.Move):
                self._place_rulers()
        elif watched in self._palette_kinds and self._palette_drag(watched, event):
            return True
        return super().eventFilter(watched, event)

    def _palette_drag(self, button, event) -> bool:
        """Drag a palette button's element once the pointer, held down on the
        button, has moved far enough to be a drag rather than a click. True
        when this event started the drag, so the button never sees it."""
        if event.type() == event.MouseButtonPress and event.button() == Qt.LeftButton:
            self._palette_press = event.pos()
            return False
        if (event.type() != event.MouseMove or self._palette_press is None
                or not event.buttons() & Qt.LeftButton):
            return False
        if ((event.pos() - self._palette_press).manhattanLength()
                < QApplication.startDragDistance()):
            return False
        hot_spot, self._palette_press = self._palette_press, None
        # The drag takes the release, so the button would be left pressed in;
        # its action never fires, which is right - a drag is not a click.
        button.setDown(False)
        mime = QMimeData()
        kind = self._palette_kinds[button]
        mime.setData(PALETTE_MIME, kind.encode())
        drag = QDrag(button)
        drag.setMimeData(mime)
        if kind == 'image':
            # No file until the drop asks for one, and a blank placeholder
            # box says less than the button does.
            drag.setPixmap(button.grab())
            drag.setHotSpot(hot_spot)
        else:
            # The element itself, as the canvas will draw it once dropped
            picture = self.canvas.drag_picture(kind)
            drag.setPixmap(picture)
            drag.setHotSpot(QPoint(picture.width() // 2, picture.height() // 2))
        drag.exec_(Qt.CopyAction)
        return True

    def on_element_dropped(self, kind, x, y):
        """A palette button let go over the canvas: add its element there.

        Once the drag has finished rather than inside it, so + Image's file
        chooser is not run while the drop is still being delivered.
        """
        self.properties.finish_edit()
        QTimer.singleShot(0, lambda: self._palette[kind](at=(x, y)))

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

    # --- the grid --------------------------------------------------------------

    def on_show_grid(self, checked):
        self.grid.shown = bool(checked)
        self._grid_changed()

    def on_snap_to_grid(self, checked):
        self.grid.snap = bool(checked)
        self._grid_changed()

    def on_snap_to_elements(self, checked):
        self.grid.snap_elements = bool(checked)
        self._grid_changed()

    def on_grid_settings(self):
        chosen = qt_dialogs.grid_settings_dialog(self, self.grid, self.document.dpi)
        if chosen is None:
            return
        self.grid.update(chosen)
        self._grid_changed()

    def on_rulers(self, checked):
        self.grid.rulers = bool(checked)
        self._show_rulers()
        self._grid_changed()

    def _show_rulers(self):
        """Give the rulers their margins, or take them away, and fit again:
        the area a fit measures has just grown or shrunk by a ruler."""
        shown = self.grid.rulers
        margin = RULER_SIZE if shown else 0
        self.scroller.setViewportMargins(margin, margin, 0, 0)
        for ruler in (self.top_ruler, self.left_ruler, self.ruler_corner):
            ruler.setVisible(shown)
        self._place_rulers()
        self._sync_view_size()

    def _place_rulers(self):
        """Lay the rulers along the viewport's top and left edges."""
        area = self.scroller.viewport().geometry()
        size = RULER_SIZE
        self.top_ruler.setGeometry(area.left(), area.top() - size, area.width(), size)
        self.left_ruler.setGeometry(area.left() - size, area.top(), size, area.height())
        self.ruler_corner.setGeometry(area.left() - size, area.top() - size, size, size)
        self._update_rulers()

    def _update_rulers(self, *_args):
        if self.grid.rulers:
            for ruler in (self.top_ruler, self.left_ruler, self.ruler_corner):
                ruler.update()

    def _on_pointer_moved(self, point):
        """Follow the pointer in the readout and on the rulers."""
        if point is None:
            self.pointer_label.setText("")
            self.top_ruler.set_pointer(None)
            self.left_ruler.set_pointer(None)
            return
        x, y = point
        self.pointer_label.setText(zpl_grid.format_point(x, y, self.grid.unit,
                                                         self.document.dpi))
        self.top_ruler.set_pointer(x)
        self.left_ruler.set_pointer(y)

    def _grid_changed(self):
        """Show the grid as it now is, and remember it."""
        zpl_grid.save_grid(self._settings_files(), self.grid)
        self.canvas.update()
        self._update_rulers()

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
        # Before the command, so the edits made in the Properties panel are
        # an undo entry of their own rather than part of whatever comes next.
        action.triggered.connect(self.properties.finish_edit)
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
        # Print... opens the Print window, whose own Print button sends;
        # Quick Print sends at once with the settings already in force.
        self.print_action = self._action("&Print…", self.on_print_dialog,
                                         "Ctrl+P")
        self.quick_print_action = self._action("&Quick Print", self.on_print,
                                               "Ctrl+Shift+P")
        self.upload_template_actions = [
            self._action(f"To {memory} ({device}:)\u2026",
                         lambda _checked=False, d=device, m=memory:
                         self.on_upload_template(d, m))
            for device, memory in workflow.TEMPLATE_DEVICES]
        self.quit_action = self._action("&Quit", self.close, "Ctrl+Q")

        self.undo_action = self._action("&Undo", self.on_undo, "Ctrl+Z")
        self.redo_action = self._action("&Redo", self.on_redo, "Ctrl+Shift+Z")
        # Ctrl+Y is the other conventional redo; a QAction carries one shortcut,
        # so the alternative gets its own hidden action.
        self.redo_alt_action = self._action("Redo", self.on_redo, "Ctrl+Y")
        self.redo_alt_action.setVisible(False)

        # Literal keys rather than QKeySequence.Cut/Copy/Paste, which on X11
        # also bind Shift+Del, Ctrl+Insert and Shift+Insert - keys the GTK
        # frontend does not take, and the two have to agree.
        self.cut_action = self._action("Cu&t", self.on_cut, "Ctrl+X")
        self.copy_action = self._action("&Copy", self.on_copy, "Ctrl+C")
        self.paste_action = self._action("&Paste", self.on_paste, "Ctrl+V")
        # No shortcut and no mnemonic: reached by a click, from this menu or
        # the canvas's right-click menu.
        self.duplicate_action = self._action("Duplicate", self.on_duplicate)
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

        # Alt+Enter, the conventional Properties key. It opens the primary's
        # editor, as a double click on it does.
        self.properties_action = self._action("Pr&operties", self.on_properties,
                                              "Alt+Return")

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
        self.rulers_action = self._action("&Rulers", self.on_rulers, "Ctrl+R",
                                          checkable=True)
        self.rulers_action.setChecked(self.grid.rulers)
        self.show_grid_action = self._action("Show &Grid", self.on_show_grid,
                                             "Ctrl+'", checkable=True)
        self.show_grid_action.setChecked(self.grid.shown)
        self.grid_settings_action = self._action("Grid Se&ttings…",
                                                 self.on_grid_settings)
        self.snap_grid_action = self._action("S&nap to Grid", self.on_snap_to_grid,
                                             "Ctrl+Shift+'", checkable=True)
        self.snap_grid_action.setChecked(self.grid.snap)
        # Ctrl+Shift+' arrives as Ctrl+" on layouts that report the shifted
        # symbol, as Bring to Front's does; the alias works the visible item.
        self._alt_snap_grid = self._action(
            "Snap to Grid", lambda _checked=False: self.snap_grid_action.trigger(),
            'Ctrl+"')
        self._alt_snap_grid.setVisible(False)
        # No shortcut: every window-wide binding is a key taken from the canvas.
        self.snap_elements_action = self._action(
            "Snap to &Elements", self.on_snap_to_elements, checkable=True)
        self.snap_elements_action.setChecked(self.grid.snap_elements)

        self.label_size_action = self._action("Label Size…", self.on_label_size)
        self.default_printer_action = self._action("Default Printer…", self.on_default_printer)
        self.local_fonts_action = self._action("Local Fonts…", self.on_local_fonts)
        self.printer_fonts_action = self._action("Fonts…", self.on_printer_fonts)
        self.printer_graphics_action = self._action("Graphics…", self.on_printer_graphics)
        self.printer_objects_action = self._action("Objects…", self.on_printer_objects)
        self.printer_console_action = self._action("Console…", self.on_printer_console)
        self.printer_firmware_action = self._action(
            "Firmware Update…", self.on_printer_firmware)
        self.printer_status_action = self._action("Status…", self.on_printer_status)
        self.help_contents_action = self._action("&Contents", self.on_help_contents, "F1")

    def _build_menus(self):
        menubar = self.menuBar()

        file_menu = menubar.addMenu("&File")
        file_menu.addAction(self.new_action)
        file_menu.addAction(self.load_action)
        # Filled in by _rebuild_recent_menu, from the settings file. Made here
        # with the window as its parent, not by addMenu("..."): PySide2 hangs
        # a menu made that way off the File menu's Python wrapper, and the
        # first walk of the menu bar through menuBar().actions() kills both
        # wrappers - the menu lives on in C++, but self.recent_menu would
        # raise from then on.
        self.recent_menu = QMenu("Open &Recent", self)
        file_menu.addMenu(self.recent_menu)
        self.recent_menu.setToolTipsVisible(True)
        # Read again each time File opens, so a file another window has opened
        # since is offered here too. File's signal rather than the submenu's:
        # a greyed-out submenu never opens, so it would never catch up.
        file_menu.aboutToShow.connect(self._refresh_recent)
        self._refresh_recent()
        file_menu.addSeparator()
        file_menu.addAction(self.save_action)
        file_menu.addAction(self.save_as_action)
        file_menu.addSeparator()
        file_menu.addAction(self.print_action)
        file_menu.addAction(self.quick_print_action)
        upload_menu = file_menu.addMenu("&Upload Template to Printer")
        for action in self.upload_template_actions:
            upload_menu.addAction(action)
        file_menu.addSeparator()
        file_menu.addAction(self.quit_action)

        edit_menu = menubar.addMenu("&Edit")
        edit_menu.addAction(self.undo_action)
        edit_menu.addAction(self.redo_action)
        edit_menu.addSeparator()
        for action in (self.cut_action, self.copy_action, self.paste_action,
                       self.duplicate_action, self.delete_action):
            edit_menu.addAction(action)
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
        edit_menu.addSeparator()
        edit_menu.addAction(self.properties_action)
        # Re-evaluated each time the menu opens, since the selection and the
        # z-order both move underneath it - and released when it closes, see
        # _release_edit_menu.
        edit_menu.aboutToShow.connect(self._update_edit_menu)
        edit_menu.aboutToHide.connect(self._release_edit_menu)
        # Paste's state is cached from the clipboard's own signal: the
        # QMimeData it hands out is freed when the clipboard changes, and a
        # stale wrapper raises if it is touched later, as the menu opens.
        self._clipboard_has_text = False
        QApplication.clipboard().dataChanged.connect(self._on_clipboard_changed)
        self._on_clipboard_changed()

        view_menu = menubar.addMenu("&View")
        view_menu.addAction(self.zoom_in_action)
        view_menu.addAction(self.zoom_out_action)
        view_menu.addSeparator()
        view_menu.addAction(self.fit_label_action)
        view_menu.addAction(self.fit_width_action)
        view_menu.addAction(self.actual_size_action)
        view_menu.addSeparator()
        view_menu.addAction(self.rulers_action)
        view_menu.addAction(self.show_grid_action)
        view_menu.addAction(self.grid_settings_action)
        view_menu.addSeparator()
        view_menu.addAction(self.snap_grid_action)
        view_menu.addAction(self.snap_elements_action)
        view_menu.addSeparator()
        # The dock's own show/hide action, so it stays ticked to match the
        # panel however it was shown or closed
        self.panel_action = self.properties.toggleViewAction()
        self.panel_action.setText("Properties &Panel")
        view_menu.addAction(self.panel_action)

        printer_menu = menubar.addMenu("&Printer")
        printer_menu.addAction(self.printer_graphics_action)
        printer_menu.addAction(self.printer_fonts_action)
        printer_menu.addAction(self.printer_objects_action)
        printer_menu.addSeparator()
        printer_menu.addAction(self.printer_console_action)
        printer_menu.addAction(self.printer_firmware_action)
        printer_menu.addAction(self.printer_status_action)

        settings_menu = menubar.addMenu("&Settings")
        settings_menu.addAction(self.label_size_action)
        settings_menu.addAction(self.default_printer_action)
        settings_menu.addAction(self.local_fonts_action)

        help_menu = menubar.addMenu("&Help")
        help_menu.addAction(self.help_contents_action)

    def _build_toolbar(self):
        toolbar = QToolBar("Elements", self)
        # So saveState() can name it alongside the Properties panel
        toolbar.setObjectName("elements")
        toolbar.setMovable(False)
        toolbar.setIconSize(QSize(16, 16))
        toolbar.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.addToolBar(Qt.LeftToolBarArea, toolbar)

        # A click adds an element at a staggered spot; dragging the button onto
        # the canvas adds it where it is let go.
        self._palette = {}         # kind -> its handler
        self._palette_kinds = {}   # toolbar button -> the kind it adds
        self._palette_press = None
        for text, kind, slot in (
                ("+ Text", 'text', self.on_add_text),
                ("+ Time", 'time', self.on_add_time),
                ("+ Serial", 'serial', self.on_add_serial),
                ("+ Numbered", 'numbered', self.on_add_numbered),
                ("+ Frame", 'frame', self.on_add_frame),
                ("+ Circle", 'circle', self.on_add_circle),
                ("+ Ellipse", 'ellipse', self.on_add_ellipse),
                ("+ Diagonal", 'diagonal', self.on_add_diagonal),
                ("+ Barcode", 'barcode', self.on_add_barcode),
                ("+ Image", 'image', self.on_add_image),
                ("+ Graphic", 'stored_graphic', self.on_add_stored_graphic)):
            action = self._action(text, slot)
            toolbar.addAction(action)
            button = toolbar.widgetForAction(action)
            # The kinds whose name alone doesn't say what they add. On the
            # action, which the button takes its tooltip from.
            key = zpl_tips.PALETTE.get(kind)
            if key is not None:
                button.setObjectName(key)
                action.setToolTip(qt_dialogs.tip_markup(zpl_tips.TIPS[key]))
            button.installEventFilter(self)
            self._palette[kind] = slot
            self._palette_kinds[button] = kind

        # One button opening the five ^GS symbols, rather than five buttons
        # on a toolbar that is already text-labelled end to end. Each action
        # shows the symbol it adds, drawn by the same raster the canvas uses,
        # since UL and CSA have no character of their own to label them with.
        symbol_button = QToolButton(self)
        symbol_button.setText("+ Symbol ▾")
        symbol_button.setObjectName("add_symbol")
        qt_dialogs.set_tip(symbol_button, 'add_symbol')
        symbol_button.setPopupMode(QToolButton.InstantPopup)
        symbol_popup = QMenu(symbol_button)
        self.symbol_actions = []
        for code, _shown, name in graphic_symbols.SYMBOLS:
            icon = QIcon(QPixmap.fromImage(
                to_qimage(graphic_symbols.icon(code))))
            action = symbol_popup.addAction(icon, name)
            action.triggered.connect(self.properties.finish_edit)
            action.triggered.connect(
                lambda _checked=False, code=code: self.on_add_graphic_symbol(code))
            self.symbol_actions.append(action)
        symbol_button.setMenu(symbol_popup)
        self.add_symbol_button = symbol_button
        toolbar.addWidget(symbol_button)

        # Undo and redo, Fit and Align fill the free end of the menu bar. Qt
        # has no slot beside the last menu, so they are its right corner.
        strip = QWidget(self)
        row = QHBoxLayout(strip)
        row.setContentsMargins(0, 0, 4, 0)
        row.setSpacing(2)

        def strip_button(action, key):
            # GTK's buttons say the same, and name the shortcut
            action.setToolTip(qt_dialogs.tip_markup(zpl_tips.TIPS[key]))
            button = QToolButton(strip)
            button.setObjectName(key)
            button.setDefaultAction(action)
            button.setAutoRaise(True)
            row.addWidget(button)

        self.undo_button_action = self._action("\u21b6 Undo", self.on_undo)
        self.redo_button_action = self._action("\u21b7 Redo", self.on_redo)
        strip_button(self.undo_button_action, 'undo')
        strip_button(self.redo_button_action, 'redo')
        row.addSpacing(10)
        strip_button(self._action("\u2212", self.on_zoom_out), 'zoom_out')
        strip_button(self._action("Fit", self.on_fit_label), 'fit_label')
        strip_button(self._action("+", self.on_zoom_in), 'zoom_in')
        row.addSpacing(10)

        # One button opening the same six commands the Edit menu holds, rather
        # than six buttons: the toolbar is text-labelled, and there are no
        # object-align icons in the icon theme to label them with. The actions
        # are the same objects, so the two menus can never disagree.
        align_button = QToolButton(strip)
        align_button.setAutoRaise(True)
        align_button.setText("Align \u25be")
        align_button.setObjectName("align")
        qt_dialogs.set_tip(align_button, 'align')
        align_button.setPopupMode(QToolButton.InstantPopup)
        align_popup = QMenu(align_button)
        for action in self.align_actions:
            align_popup.addAction(action)
        # The popup can open without the Edit menu ever having been shown, so
        # it re-evaluates the same enable rules on the way up.
        align_popup.aboutToShow.connect(self._update_edit_menu)
        align_popup.aboutToHide.connect(self._release_edit_menu)
        align_button.setMenu(align_popup)
        self.align_button = align_button
        row.addWidget(align_button)
        self.menuBar().setCornerWidget(strip, Qt.TopRightCorner)

    def _edit_menu_actions(self):
        """Every action _update_edit_menu greys out."""
        return ([self.cut_action, self.copy_action, self.paste_action,
                 self.duplicate_action, self.delete_action,
                 self.select_all_action, self.deselect_all_action,
                 self.invert_selection_action, self.group_action,
                 self.ungroup_action, self.remove_from_group_action,
                 self.front_action, self.forward_action,
                 self.backward_action, self.back_action,
                 self.properties_action]
                + self.align_actions)

    def _release_edit_menu(self):
        """Enable again, once the menu has closed, everything its enable rules
        greyed out.

        The rules are for reading an open menu. A disabled action also
        swallows its shortcut, and the selection goes on changing after the
        menu closes: a menu last opened with nothing selected would leave
        Ctrl+C dead on an element picked afterwards. Every handler already
        does nothing when there is nothing to do, which is all a shortcut
        owes the user (FUNCTIONAL_SPEC.md section 14).
        """
        for action in self._edit_menu_actions():
            action.setEnabled(True)

    def _on_clipboard_changed(self):
        """Remember whether the clipboard holds any text at all.

        Whether it is ZPL is the paste's to find out, and fetching a clipboard
        that may hold an image's worth of hex is not worth an earlier answer.
        """
        mime = QApplication.clipboard().mimeData()
        self._clipboard_has_text = mime is not None and mime.hasText()

    def _update_edit_menu(self):
        """Grey out the actions that need a selection, or a place to move to."""
        doc = self.document
        for action in (self.cut_action, self.copy_action, self.duplicate_action):
            action.setEnabled(bool(doc.selection))
        self.paste_action.setEnabled(self._clipboard_has_text)
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
        self.properties_action.setEnabled(doc.selected_element is not None)

    # --- history -------------------------------------------------------------

    def on_canvas_changed(self):
        """A change made anywhere but the Properties panel: record it, and
        have the panel read the document again."""
        self.properties.document_changed()
        self._record_change()

    def record_panel_edit(self):
        """The Properties panel's burst of edits, as one undo entry."""
        self._register_label_fonts()
        self._record_change()

    def revert_to_last_entry(self):
        """Put the document back as the last undo entry left it - Escape in
        the Properties panel, throwing its burst of edits away.

        The selection stays what it is now, not what it was when that entry
        was made: the edits were to the elements still being looked at. A
        burst never adds or removes an element, so their places are the same
        in the restored list.
        """
        doc = self.document
        places = [doc.elements.index(el) for el in doc.selection
                  if el in doc.elements]
        doc.restore(self._current_snapshot)
        doc.selection = [doc.elements[i] for i in places if i < len(doc.elements)]
        self.canvas._sync_size()
        self.canvas.update()

    def _record_change(self):
        """Record one undo entry and mark the document unsaved."""
        self.unsaved_changes = True
        # the snapshot standing before this change is what undo goes back to
        self._undo_stack.append(self._current_snapshot)
        del self._undo_stack[:-UNDO_LIMIT]
        self._redo_stack.clear()
        self._current_snapshot = self.document.snapshot()
        self._update_undo_actions()

    def on_undo(self):
        # a burst in the Properties panel, or a nudge still held down, is a
        # change of its own, recorded first
        self.properties.finish_edit()
        self.canvas.finish_nudge()
        if not self._undo_stack:
            return
        self._redo_stack.append(self._current_snapshot)
        self._current_snapshot = self._undo_stack.pop()
        self._apply_snapshot(self._current_snapshot)
        self.update_status("Undo")

    def on_redo(self):
        # a burst in the Properties panel, or a nudge still held down, is a
        # change of its own, recorded first
        self.properties.finish_edit()
        self.canvas.finish_nudge()
        if not self._redo_stack:
            return
        self._undo_stack.append(self._current_snapshot)
        self._current_snapshot = self._redo_stack.pop()
        self._apply_snapshot(self._current_snapshot)
        self.update_status("Redo")

    def _apply_snapshot(self, snapshot):
        self.document.restore(snapshot)
        self.unsaved_changes = True
        self.canvas._sync_size()
        self.canvas.update()
        self._update_undo_actions()

    def _reset_history(self):
        """Start a fresh history, so it never spans a file load."""
        self.properties.document_changed()
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

    def on_add_text(self, _checked=False, at=None):
        self.document.add_text_element("New Text", at=at)
        self.canvas.commit()

    def on_add_time(self, _checked=False, at=None):
        self.document.add_time_element(at=at)
        self.canvas.commit()

    def on_add_serial(self, _checked=False, at=None):
        self.document.add_serial_element(at=at)
        self.canvas.commit()

    def on_add_numbered(self, _checked=False, at=None):
        self.document.add_numbered_element(at=at)
        self.canvas.commit()

    def on_add_frame(self, _checked=False, at=None):
        self.document.add_frame_element(at=at)
        self.canvas.commit()

    def on_add_circle(self, _checked=False, at=None):
        self.document.add_circle_element(at=at)
        self.canvas.commit()

    def on_add_ellipse(self, _checked=False, at=None):
        self.document.add_ellipse_element(at=at)
        self.canvas.commit()

    def on_add_diagonal(self, _checked=False, at=None):
        self.document.add_diagonal_element(at=at)
        self.canvas.commit()

    def on_add_barcode(self, _checked=False, at=None):
        self.document.add_barcode_element(at=at)
        self.canvas.commit()

    def on_add_image(self, _checked=False, at=None):
        path = qt_dialogs.choose_image_file(self, "Load Image")
        if not path:
            return
        self.document.add_image_element(path, at=at)
        self.canvas.commit()

    def on_add_stored_graphic(self, _checked=False, at=None):
        self.document.add_stored_graphic_element(at=at)
        self.canvas.commit()

    def on_add_graphic_symbol(self, code: str):
        self.document.add_graphic_symbol_element(code)
        self.canvas.commit()

    def on_delete(self):
        if self.document.remove_selected():
            self.canvas.commit()

    # The clipboard holds ZPL (Document.copy_zpl), so what one window copies
    # another - this frontend's or the GTK one's - can paste.

    def on_copy(self):
        """Copy changes the clipboard and never the document: no undo entry."""
        text = self.document.copy_zpl()
        if not text:
            return
        QApplication.clipboard().setText(text)
        self.update_status(f"Copied {workflow.elements_phrase(len(self.document.selection))}")

    def on_cut(self):
        """Copy, then Delete - which is the one undo entry."""
        count = len(self.document.selection)
        text = self.document.copy_zpl()
        if not text:
            return
        QApplication.clipboard().setText(text)
        self.on_delete()
        self.update_status(f"Cut {workflow.elements_phrase(count)}")

    def on_paste(self):
        text = QApplication.clipboard().text()
        try:
            message = workflow.paste_zpl(self.document, text, self.renderer)
        except Exception as e:
            self.show_error(f"Failed to paste: {e}")
            return
        if message is None:
            self.update_status(workflow.NOTHING_TO_PASTE)
            return
        self._register_label_fonts()
        self.canvas.commit()
        self.update_status(message)

    def on_duplicate(self):
        count = self.document.duplicate_selected()
        if count:
            self.canvas.commit()
            self.update_status(f"Duplicated {workflow.elements_phrase(count)}")

    def _on_edit_requested(self, command: str):
        """A command chosen from the canvas's right-click menu."""
        {'cut': self.on_cut, 'copy': self.on_copy,
         'duplicate': self.on_duplicate, 'delete': self.on_delete,
         'properties': self.on_properties}[command]()

    def on_properties(self):
        """Show the Properties panel on the selection - floating and hidden,
        this is how it comes back."""
        if self.document.selection:
            self.properties.request()

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
        """A double click asks for the Properties panel, which the first
        click has already pointed at the element."""
        self.properties.request()

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
                             transform=None):
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
        # Only the DPI slot of the default moves - the connection stays
        # whatever the persisted default already was, so a session override
        # on it (the Print window) survives a Label Settings visit untouched.
        self._default_printer = (self._default_printer[0], dpi,
                                 self._default_printer[2])
        self.label_inches = (w_in, h_in)
        if transform is not None:
            self.document.transform = transform
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
        # effect: a session override (the Print window) must never leak into
        # this dialog and get re-saved as the new default just by clicking OK.
        default_connection, default_dpi, default_font_device = \
            self._default_printer
        result = qt_dialogs.printer_settings_dialog(
            self, default_connection, default_dpi, default_font_device,
            title="Default Printer")
        if result is None:
            return
        printer, dpi, font_device = result
        old_dpi = self.printer_dpi
        self.printer_connection, self.printer_dpi = printer, dpi
        self._set_font_device(font_device)
        self._default_printer = (printer, dpi, font_device)
        self._save_settings()
        self.update_status(f"Printer set to {printer.where()}")
        if dpi != old_dpi:
            note = self._offer_dpi_rescale()
            if note:
                self.canvas._sync_size()
                self.canvas.commit()
                self.update_status(note[0].upper() + note[1:])

    def _set_font_device(self, device: str):
        """Adopt a new Font memory setting, here and on the open document.

        The document holds its own copy (Document.font_device), so changing
        the setting has to reach it or the label would go on writing ^A@ at
        the old drive. Elements that carry a path of their own - anything
        loaded from a file - are unaffected by design, so this moves only the
        fonts this app assigned.
        """
        self.printer_font_device = device
        if self.canvas is not None and self.canvas.document is not None:
            self.canvas.document.font_device = device

    def on_local_fonts(self):
        qt_dialogs.LocalFontsDialog(self).exec_()

    def on_extra_font_folder(self):
        """Pick the one extra folder the font fallback scan also covers."""
        current = zpl_fonts.EXTRA_FONT_DIR
        box = QMessageBox(self)
        box.setWindowTitle("Extra Font Folder")
        box.setText("Folder scanned with the other font folders when the "
                    "system font list can't be read:\n"
                    + (current or "(none)"))
        choose = box.addButton("Choose…", QMessageBox.AcceptRole)
        clear = box.addButton("Clear", QMessageBox.DestructiveRole)
        clear.setEnabled(bool(current))
        box.addButton(QMessageBox.Cancel)
        box.exec_()
        if box.clickedButton() is choose:
            folder = QFileDialog.getExistingDirectory(
                self, "Extra Font Folder", current or str(Path.home()))
            if not folder:
                return
        elif box.clickedButton() is clear:
            folder = ''
        else:
            return
        zpl_fonts.set_extra_font_dir(folder)
        self._save_settings()

    def on_print_dialog(self):
        """Print...: the printer for this session and the label's print
        settings in one window, then the label itself, from its Print button.

        The printer is for this session only, and never reaches the persisted
        default. Everything else is saved with the label, so a visit that
        changes any of it is one undo entry. A visit that changes none of it
        records nothing and leaves the file as it was saved.
        """
        result = qt_dialogs.print_dialog(
            self, self.document,
            (self.printer_connection, self.printer_dpi,
             self.printer_font_device),
            self._default_printer)
        if result is None:
            return
        action, (printer, dpi, font_device), settings = result
        moved = printer.key() != self.printer_connection.key()
        old_dpi = self.printer_dpi
        self.printer_connection, self.printer_dpi = printer, dpi
        self._set_font_device(font_device)
        # Settled before the settings go on, against the design the user was
        # looking at, as Label Settings settles it.
        note = self._offer_dpi_rescale() if dpi != old_dpi else None
        changed = settings.apply(self.document)
        if note or changed:
            self.canvas.commit()      # one history entry for the whole visit
        if note:
            self.update_status(note[0].upper() + note[1:])
        elif moved:
            self.update_status(f"Printing to {printer.where()} for this session")
        elif changed:
            self.update_status("Print settings changed")
        if action == 'print':
            self.on_print()

    def on_printer_fonts(self):
        def on_uploaded(name, path):
            self.renderer.register_font(name, path)

        dialog = qt_dialogs.PrinterFontsDialog(
            self, self.printer_connection, on_uploaded,
            self.printer_font_device)
        dialog.exec_()

    def on_printer_graphics(self):
        # Storing/retrieving/deleting a graphic changes no Document state, so
        # this is a plain repaint - canvas.update(), never canvas.commit() -
        # so an ^XG/^IM/^IL that now resolves differently is shown without
        # marking the file dirty or pushing a bogus undo entry.
        dialog = qt_dialogs.PrinterGraphicsDialog(
            self, self.printer_connection,
            on_changed=lambda *_a: self._graphics_changed())
        dialog.exec_()

    def on_printer_objects(self):
        # Same reasoning as on_printer_graphics: deleting an object changes
        # no Document state, so this is a plain repaint, never a commit().
        dialog = qt_dialogs.PrinterObjectsDialog(
            self, self.printer_connection,
            on_changed=lambda *_a: self._graphics_changed())
        dialog.exec_()

    def _graphics_changed(self):
        """A graphic was stored, retrieved or deleted: re-size the stored
        graphics that name it to what the printer will print, and repaint.
        The boxes are not written to the file, so this is no change to the
        label - no undo entry, nothing unsaved."""
        self.document.sync_stored_graphics()
        self.canvas.update()
        self.properties.queue_sync()

    def on_printer_firmware(self):
        qt_dialogs.PrinterFirmwareDialog(self, self.printer_connection).exec_()

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

    def on_printer_status(self):
        # Non-modal and a singleton for the same reasons as the console, plus
        # one of its own: this one can be left auto-refreshing while the user
        # works on the label, which is the whole point of watching a printer.
        if self.printer_status_dialog is not None:
            self.printer_status_dialog.raise_()
            self.printer_status_dialog.activateWindow()
            return
        dialog = qt_dialogs.PrinterStatusDialog(self)
        dialog.destroyed.connect(lambda *_a: setattr(self, 'printer_status_dialog', None))
        self.printer_status_dialog = dialog
        dialog.show()

    def on_help_contents(self):
        # F1 with the focus in the Properties panel, docked or floating, is
        # help on what the panel shows: the shortcut is the window's, and Qt
        # keeps a window's shortcuts working while its floating dock is
        # active.
        if self.properties.has_focus_within():
            self.properties.open_help()
        else:
            qt_dialogs.show_help(self)

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
        document.font_device = self.printer_font_device
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

    # --- File > Open Recent --------------------------------------------------

    def _settings_files(self):
        """The settings files, in the order a write tries them."""
        return (_config_path(), _fallback_config_path())

    def _recent_candidates(self):
        """The settings files the list is read from and written to, in order."""
        return self._settings_files()

    def _refresh_recent(self):
        """Rebuild Open Recent from what the settings file holds now."""
        self._rebuild_recent_menu(workflow.load_recent(self._recent_candidates()))

    def _store_recent(self, recent):
        """Write the list and show it - the one place either happens."""
        workflow.save_recent(self._recent_candidates(), recent)
        self._rebuild_recent_menu(recent)

    def _remember_recent(self, filepath):
        """Put a file just opened or saved at the top of Open Recent."""
        # Re-read first, so a file another window added since is kept.
        self._store_recent(workflow.remember_recent(
            workflow.load_recent(self._recent_candidates()), filepath))

    def _rebuild_recent_menu(self, recent):
        # Taken off now, deleted once control is back in the event loop: when
        # an entry is chosen, the action still being triggered is one of them.
        for action in self.recent_menu.actions():
            self.recent_menu.removeAction(action)
            action.deleteLater()
        for number, (path, text) in enumerate(
                zip(recent, workflow.recent_labels(recent)), 1):
            # 1 to 9, then 0 for the tenth. A name's own ampersands are
            # doubled, or Qt would take the first for the mnemonic.
            action = self.recent_menu.addAction(
                f"&{number % 10} {text.replace('&', '&&')}")
            action.setToolTip(path)
            action.triggered.connect(
                lambda _checked=False, p=path: self.on_open_recent(p))
        if recent:
            self.recent_menu.addSeparator()
            self.recent_menu.addAction("&Clear List").triggered.connect(
                lambda _checked=False: self.on_clear_recent())
        self.recent_menu.menuAction().setEnabled(bool(recent))

    def on_open_recent(self, filepath):
        """Open a file from File > Open Recent."""
        # Checked before asking about unsaved work: nobody should be made to
        # save or discard for the sake of a file that is not there to open.
        if not os.path.isfile(filepath):
            self._store_recent(workflow.forget_recent(
                workflow.load_recent(self._recent_candidates()), filepath))
            self.show_error(workflow.recent_gone_message(filepath))
            return
        if self.check_unsaved_changes():
            self.load_zpl_file(filepath)

    def on_clear_recent(self):
        """Empty File > Open Recent."""
        self._store_recent([])

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
        self._remember_recent(filepath)
        return True

    def load_zpl_file(self, filepath: str):
        try:
            content, code_page = zpl_parser.read_file(filepath)
            document, loaded_dpi = zpl_parser.parse_zpl(content, self.renderer)
            document.font_device = self.printer_font_device
            self.canvas.set_document(document)
            self.current_filepath = filepath
            self._update_title()
            self._remember_recent(filepath)
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
        printer = self.printer_connection

        def send_label(uploadable):
            self.update_status("Printing...")

            def work(cancel):
                if uploadable:
                    workflow.upload_fonts(uploadable, printer,
                                          self._busy.report, cancel)
                    self._busy.report("Printing...")
                zpl_connection.send(printer, content.encode('utf-8'),
                                    PRINT_TIMEOUT, cancel=cancel)

            def done(_result, error):
                if isinstance(error, printer_io.Cancelled):
                    self.update_status("Printing cancelled")
                elif error is not None:
                    self.show_error(str(error))
                    self.update_status("Printing failed")
                else:
                    self.update_status(f"Sent to {printer.where()}")

            self._busy.run(work, done)

        def checked(result, error):
            if isinstance(error, printer_io.Cancelled):
                self.update_status("Printing cancelled")
                return
            if error is not None:
                self.show_error(str(error))
                self.update_status("Printing failed")
                return
            missing, uploadable, unreadable = (
                (None, {}, ()) if result is None else result)
            # A drive that could not be read leaves its fonts unjudged, so a
            # label whose fonts are all on such a drive still has nothing
            # missing - and still prints without a prompt, as before. The
            # prompt says which drives went unchecked when there is one.
            if result is not None and not missing:
                send_label({})
                return
            text, detail = workflow.font_problem_prompt(missing, unreadable)
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
            self.document, printer, cancel=cancel), checked)

    def on_upload_template(self, device, memory):
        """Store the design on the printer as a ^DF format, printing nothing."""
        if self.document.is_empty():
            self.show_error("There is nothing to upload.")
            return
        name = qt_dialogs.ask_template_name(
            self, f"{memory} ({device}:)",
            workflow.default_template_name(self.document, self.current_filepath))
        if name is None:
            return
        try:
            content = workflow.build_template_upload(self.document, device, name)
        except Exception as e:
            self.show_error(f"Failed to generate ZPL: {e}")
            return
        printer = self.printer_connection
        stored = f"{device}:{name}.ZPL"
        self.update_status(f"Uploading {stored}...")

        def work(cancel):
            zpl_connection.send(printer, content.encode('utf-8'),
                                PRINT_TIMEOUT, cancel=cancel)

        def done(_result, error):
            if isinstance(error, printer_io.Cancelled):
                self.update_status("Upload cancelled")
            elif error is not None:
                self.show_error(str(error))
                self.update_status("Upload failed")
            else:
                self.update_status(f"Stored {stored} on {printer.where()}")

        self._busy.run(work, done)

    # --- settings file -------------------------------------------------------

    def _load_settings(self):
        parser = configparser.ConfigParser()
        try:
            parser.read([_config_path(), _fallback_config_path()])
            self.printer_connection = Connection.read(
                parser, self.printer_connection)
            dpi = parser.getint('printer', 'dpi', fallback=self.printer_dpi)
            if dpi > 0:
                self.printer_dpi = dpi
            # Ignored unless it names a drive that exists: a hand-edited file
            # must not point every upload at one the printer has not got.
            font_device = parser.get('printer', 'font_device',
                                     fallback=self.printer_font_device).strip().upper()
            if font_device in zpl_fonts.DEVICES:
                self.printer_font_device = font_device
            zpl_fonts.set_extra_font_dir(
                parser.get('fonts', 'extra_dir', fallback=''))
            self.saved_dock_state = parser.get('docks', 'state', fallback=None)
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
                # The persisted default, not self.printer_connection/_dpi:
                # those are whatever is active for printing right now, which a
                # session override (the Print window) deliberately changes
                # without this ever running. Only Default Printer and Label
                # Settings are allowed to move self._default_printer, and they
                # do so before calling this.
                self._default_printer[0].write(parser)
                parser.set('printer', 'dpi', str(self._default_printer[1]))
                parser.set('printer', 'font_device', self._default_printer[2])
                if not parser.has_section('fonts'):
                    parser.add_section('fonts')
                parser.set('fonts', 'extra_dir', zpl_fonts.EXTRA_FONT_DIR)
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
                if hasattr(self, 'properties'):
                    # A section of its own: one more key in [window] would
                    # make it a section without the geometry its reader wants
                    if not parser.has_section('docks'):
                        parser.add_section('docks')
                    parser.set('docks', 'state',
                               bytes(self.saveState().toBase64()).decode('ascii'))
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
        # An edit still in its burst is an unsaved change like any other
        self.properties.finish_edit()
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


def main(filepath=None, fields=None):
    # The template path is ours, not Qt's, so Qt is not shown it.
    app = QApplication(sys.argv[:1])
    app.setApplicationName(workflow.APP_TITLE)
    window = ZPLDesignerWindow()
    window.show()
    if filepath:
        window.load_zpl_file(filepath)
        if fields:
            workflow.apply_field_data(window.document, fields)
            window.canvas.update()
            window._reset_history()
    # Ask for the front. Started from an editor running full screen, a new
    # window can otherwise map behind it and look as though nothing happened.
    window.raise_()
    window.activateWindow()
    return app.exec_()


if __name__ == '__main__':
    sys.exit(main())
