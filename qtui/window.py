#!/usr/bin/env python3
"""
The Qt frontend: a PySide2/Qt5 window over a zplcore Document.

The window owns everything that is not the document itself: the menus and their
enable rules, the undo history, the printer settings, and every command that
touches a file or the printer.
"""

import configparser
import os
import socket
import sys
from pathlib import Path

from PySide2.QtCore import QSize, Qt
from PySide2.QtGui import QImage, QKeySequence, QPixmap
from PySide2.QtWidgets import (QAction, QApplication, QFileDialog, QLabel,
                               QMainWindow, QScrollArea, QSizePolicy,
                               QToolBar, QWidget)

from zplcore import fonts as zpl_fonts
from zplcore import parser as zpl_parser
from zplcore.model import (BarcodeElement, Document, FrameElement, ImageElement,
                           TextElement)
from zplcore.renderer import ZPLRenderer

from . import dialogs as qt_dialogs
from .canvas import DesignCanvas

APP_NAME = "LinuxZPL (Qt)"
UNDO_LIMIT = 50
DEFAULT_ADDRESS = "192.168.50.21"
DEFAULT_PORT = 9100
PRINT_TIMEOUT = 10
PREVIEW_MAX_WIDTH = 300


def _config_path() -> Path:
    """The settings file, in the platform's user config directory."""
    base = os.environ.get('XDG_CONFIG_HOME') or os.path.expanduser('~/.config')
    return Path(base) / 'linuxzpl' / 'settings.ini'


class ZPLDesignerWindow(QMainWindow):

    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(900, 1000)

        self.printer_address = DEFAULT_ADDRESS
        self.printer_port = DEFAULT_PORT
        self.printer_dpi = zpl_fonts.DEFAULT_DPI
        self._load_settings()
        # Fonts registered before the application existed could not be handed to
        # Qt then; now there is one, so flush them.
        zpl_fonts.register_app_fonts_with_qt()

        self.current_filepath = None
        self.unsaved_changes = False
        self.renderer = ZPLRenderer()

        # A label is 4 x 6 inches at whatever resolution the printer is set to,
        # so its dot dimensions depend on that setting.
        document = Document(*self._inches_to_dots(4, 6), dpi=self.printer_dpi)
        self.canvas = DesignCanvas(document)
        self.canvas.documentChanged.connect(self.on_canvas_changed)
        self.canvas.elementDoubleClicked.connect(self.on_element_double_clicked)

        self.scroller = QScrollArea()
        self.scroller.setWidget(self.canvas)
        self.scroller.setWidgetResizable(True)
        self.scroller.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        # Always on, so the canvas width - and with it the scale - does not
        # change when the scrollbar appears, which would set off a resize loop.
        self.scroller.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.setCentralWidget(self.scroller)

        self._build_actions()
        self._build_menus()
        self._build_toolbar()
        self.statusBar().showMessage("Ready")

        self._undo_stack = []
        self._redo_stack = []
        self._current_snapshot = self.document.snapshot()
        self._update_undo_actions()

    # --- convenience ---------------------------------------------------------

    @property
    def document(self) -> Document:
        return self.canvas.document

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
        self.load_action = self._action("&Load ZPL File…", self.on_load, QKeySequence.Open)
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

        self.label_size_action = self._action("Label Size…", self.on_label_size)
        self.printer_settings_action = self._action("Printer Settings…", self.on_printer_settings)
        self.printer_fonts_action = self._action("Printer Fonts…", self.on_printer_fonts)

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
        file_menu.addSeparator()
        file_menu.addAction(self.quit_action)

        edit_menu = menubar.addMenu("&Edit")
        edit_menu.addAction(self.undo_action)
        edit_menu.addAction(self.redo_action)
        edit_menu.addSeparator()
        edit_menu.addAction(self.delete_action)
        edit_menu.addSeparator()
        for action in (self.front_action, self.forward_action,
                       self.backward_action, self.back_action):
            edit_menu.addAction(action)
        # Re-evaluated each time the menu opens, since the selection and the
        # z-order both move underneath it.
        edit_menu.aboutToShow.connect(self._update_edit_menu)

        settings_menu = menubar.addMenu("&Settings")
        settings_menu.addAction(self.label_size_action)
        settings_menu.addAction(self.printer_settings_action)
        settings_menu.addAction(self.printer_fonts_action)

    def _build_toolbar(self):
        toolbar = QToolBar("Elements", self)
        toolbar.setMovable(False)
        toolbar.setIconSize(QSize(16, 16))
        self.addToolBar(toolbar)

        toolbar.addAction(self._action("+ Text", self.on_add_text))
        toolbar.addAction(self._action("+ Frame", self.on_add_frame))
        toolbar.addAction(self._action("+ Barcode", self.on_add_barcode))
        toolbar.addAction(self._action("+ Image", self.on_add_image))
        toolbar.addSeparator()
        toolbar.addAction(self.delete_action)

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
        self.delete_action.setEnabled(doc.selected_element is not None)
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
        self.document.restore(snapshot)
        self.unsaved_changes = True
        self.canvas._sync_size()
        self.canvas.update()
        self._update_undo_actions()

    def _reset_history(self):
        """Start a fresh history, so it never spans a file load."""
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

    def on_delete(self):
        if self.document.remove_selected():
            self.canvas.commit()

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

    def on_element_double_clicked(self, element):
        """Open the edit dialog for whichever element was double-clicked."""
        changed = False
        if isinstance(element, TextElement):
            changed = qt_dialogs.edit_text_dialog(self, element, self.document)
            if changed:
                self._register_label_fonts()
        elif isinstance(element, FrameElement):
            changed = qt_dialogs.edit_frame_dialog(self, element)
        elif isinstance(element, BarcodeElement):
            changed = qt_dialogs.edit_barcode_dialog(self, element)
        elif isinstance(element, ImageElement):
            path = qt_dialogs.choose_image_file(self, "Replace Image")
            if path:
                element.image_path = path
                element.reload()
                changed = True
        if changed:
            self.canvas.commit()

    def _register_label_fonts(self):
        """Tell the preview renderer about every font this label uses."""
        for element in self.document.elements:
            name = getattr(element, 'printer_font_name', None)
            path = getattr(element, 'font_path', None)
            if name and path:
                self.renderer.register_font(name, path)

    # --- settings dialogs ----------------------------------------------------

    def on_label_size(self):
        size = qt_dialogs.label_size_dialog(self, self.document, self.printer_dpi)
        if size is None:
            return
        width, height = size
        # Shrinking clamps elements to the new bounds, which is itself part of
        # the change being recorded.
        self.document.set_label_size(width, height)
        self.canvas._sync_size()
        self.canvas.commit()
        self.update_status(f"Label size set to {width}x{height}")

    def on_printer_settings(self):
        result = qt_dialogs.printer_settings_dialog(
            self, self.printer_address, self.printer_port, self.printer_dpi)
        if result is None:
            return
        address, port, dpi = result
        old_dpi = self.printer_dpi
        self.printer_address, self.printer_port, self.printer_dpi = address, port, dpi
        self._save_settings()
        self.update_status(f"Printer set to {self.printer_address}:{self.printer_port}")
        if dpi != old_dpi:
            self._reconcile_document_dpi()

    def _reconcile_document_dpi(self):
        """Settle an open design against a printer resolution that just changed.

        Dots only mean a physical size once a resolution is fixed, so pointing
        the designer at a printer with a different head silently changes what
        the open label measures - 1200 dots is 4in at 300 dpi and 5.9in at 203.
        Re-stamping the document with the new resolution and saying nothing
        would leave the elements at the old scale, and the label would print
        oversized and run off the media. So the same choice a mismatched file
        gets on open is offered here.
        """
        document = self.document
        if document.dpi == self.printer_dpi or not document.elements:
            document.dpi = self.printer_dpi
            return

        old = document.dpi
        w_in, h_in = self._dots_to_inches(document.label_width, document.label_height)
        answer = qt_dialogs.ask_dpi_rescale(self, old, self.printer_dpi,
                                            False, w_in, h_in)
        if answer == 'rescale':
            document.rescale(self.printer_dpi / old)
            document.dpi = self.printer_dpi
            self.canvas._sync_size()
            # The design changed, so this is one undoable action.
            self.canvas.commit()
            self.update_status(f"Rescaled from {old} to {self.printer_dpi} dpi")
            return
        document.dpi = self.printer_dpi

    def on_printer_fonts(self):
        def on_uploaded(name, path):
            self.renderer.register_font(name, path)

        dialog = qt_dialogs.PrinterFontsDialog(
            self, self.printer_address, self.printer_port, on_uploaded)
        dialog.exec_()

    # --- files ---------------------------------------------------------------

    def check_unsaved_changes(self) -> bool:
        """Ask what to do with unsaved work. True means it is safe to continue."""
        if not self.unsaved_changes:
            return True
        answer = qt_dialogs.ask_unsaved_changes(self)
        if answer == 'discard':
            return True
        if answer == 'save':
            # only continue if a file was actually written; a cancelled or
            # failed save must not carry on and lose the work
            return self.save_file_or_ask_for_filename()
        return False

    def on_new(self):
        if not self.check_unsaved_changes():
            return
        document = Document(*self._inches_to_dots(4, 6), dpi=self.printer_dpi)
        self.canvas.set_document(document)
        self.current_filepath = None
        self.unsaved_changes = False
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
        dialog = QFileDialog(self, "Load ZPL File")
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
        path, _ = QFileDialog.getSaveFileName(
            self, "Save ZPL File", self.current_filepath or "untitled.zpl",
            qt_dialogs.ZPL_FILTER)
        if not path:
            return False
        return self.save_zpl_file(path, content)

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
        self.update_status(f"Saved: {os.path.basename(filepath)}")
        return True

    def load_zpl_file(self, filepath: str):
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                content = f.read()
            document, loaded_dpi = zpl_parser.parse_zpl(content, self.renderer)
            self.canvas.set_document(document)
            self.current_filepath = filepath
            rescaled = self._offer_dpi_rescale(loaded_dpi)
            self._register_label_fonts()
            self.canvas.update()
            # Both facts are worth reporting, and the load message would
            # otherwise overwrite the rescale one the instant it appeared.
            loaded = f"Loaded: {os.path.basename(filepath)}"
            self.update_status(f"{loaded} - {rescaled}" if rescaled else loaded)
            # Building elements while parsing does not count as an edit.
            self.unsaved_changes = False
            self._reset_history()
        except Exception as e:
            self.show_error(f"Failed to load file: {e}")
            self.update_status("Error loading file")

    def _offer_dpi_rescale(self, loaded_dpi):
        """If the file was drawn for another resolution, offer to rescale it.

        Returns a note for the status bar when it rescaled, else None.
        """
        document = self.document
        # A file with no ^FXDESIGNER_DPI is assumed to be 203, the resolution of
        # most ZPL in the wild. Taking the printer's resolution instead would
        # stamp that guess into the file the next time it is saved,
        # mislabelling a 203 dpi label as whatever printer happened to open it.
        assumed = not loaded_dpi or loaded_dpi <= 0
        old = zpl_fonts.DEFAULT_DPI if assumed else loaded_dpi

        if old == self.printer_dpi or not document.elements:
            document.dpi = self.printer_dpi
            return None

        w_in, h_in = self._dots_to_inches(document.label_width, document.label_height)
        answer = qt_dialogs.ask_dpi_rescale(self, old, self.printer_dpi, assumed,
                                            w_in, h_in)
        note = None
        if answer == 'rescale':
            document.rescale(self.printer_dpi / old)
            self.canvas._sync_size()
            note = f"rescaled from {old} to {self.printer_dpi} dpi"
        document.dpi = self.printer_dpi
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

    def _confirm_printer_fonts(self) -> bool:
        """Check the label's fonts are on the printer. False cancels printing."""
        sources = self.document.font_sources()
        if not sources:
            return True  # nothing but built-in fonts, nothing to check

        installed = zpl_fonts.query_printer_fonts(self.printer_address, self.printer_port)
        if installed is None:
            # Not the same as "the printer has no fonts": it could not be asked.
            return self._ask_font_problem(
                "The printer could not be asked which fonts it has.",
                "It may be unreachable, or may not support font queries.\n"
                "Printing anyway may fall back to a substitute font.",
                uploadable={})

        missing = {n: p for n, p in sources.items() if n.upper() not in installed}
        if not missing:
            return True

        uploadable = {n: p for n, p in missing.items() if p}
        lines = [f"  {zpl_fonts.printer_font_path(n)}" +
                 ("" if missing[n] else "   (source file unknown)")
                 for n in sorted(missing)]
        return self._ask_font_problem(
            "Fonts used by this label are not on the printer.",
            "\n".join(lines) + "\n\nMissing fonts print in a substitute typeface.",
            uploadable=uploadable)

    def _ask_font_problem(self, text: str, detail: str, uploadable: dict) -> bool:
        answer = qt_dialogs.ask_font_problem(self, text, detail, uploadable)
        if answer == 'upload':
            for name, path in sorted(uploadable.items()):
                self.update_status(f"Uploading {zpl_fonts.printer_font_path(name)}...")
                QApplication.processEvents()
                try:
                    zpl_fonts.upload_font(self.printer_address, self.printer_port,
                                          path, name)
                except Exception as e:
                    self.show_error(f"Upload of {name} failed: {e}")
                    return False
            return True
        return answer == 'print'

    def on_print(self):
        if not self._confirm_printer_fonts():
            self.update_status("Printing cancelled")
            return
        try:
            content = self.document.to_zpl()
        except Exception as e:
            self.show_error(f"Failed to generate ZPL: {e}")
            return

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(PRINT_TIMEOUT)
        try:
            sock.connect((self.printer_address, self.printer_port))
            sock.sendall(content.encode('utf-8'))
        except OSError as e:
            self.show_error(str(e))
            return
        finally:
            sock.close()
        self.update_status(f"Sent to {self.printer_address}:{self.printer_port}")

    # --- settings file -------------------------------------------------------

    def _load_settings(self):
        parser = configparser.ConfigParser()
        try:
            parser.read(_config_path())
            self.printer_address = parser.get(
                'printer', 'address', fallback=self.printer_address)
            self.printer_port = parser.getint(
                'printer', 'port', fallback=self.printer_port)
            dpi = parser.getint('printer', 'dpi', fallback=self.printer_dpi)
            if dpi > 0:
                self.printer_dpi = dpi
        except (configparser.Error, OSError, ValueError):
            # A missing or corrupt config must never block startup
            pass

    def _save_settings(self):
        path = _config_path()
        parser = configparser.ConfigParser()
        try:
            # Read first so unrelated sections are preserved
            parser.read(path)
            if not parser.has_section('printer'):
                parser.add_section('printer')
            parser.set('printer', 'address', self.printer_address)
            parser.set('printer', 'port', str(self.printer_port))
            parser.set('printer', 'dpi', str(self.printer_dpi))
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, 'w', encoding='utf-8') as f:
                parser.write(f)
        except (configparser.Error, OSError) as e:
            self.show_error(f"Could not save settings: {e}")

    # --- window --------------------------------------------------------------

    def closeEvent(self, event):
        if self.check_unsaved_changes():
            event.accept()
        else:
            event.ignore()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    window = ZPLDesignerWindow()
    window.show()
    return app.exec_()


if __name__ == '__main__':
    sys.exit(main())
