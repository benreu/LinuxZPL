"""
Every dialog the designer shows.

Each one either edits the object it is given and reports whether anything
changed, or returns a plain value. None of them touch the undo history or the
status bar - the window owns those, so one action produces one history entry
wherever it was started from.
"""

import html
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Optional

from PIL import Image as PILImage

from PySide2.QtCore import QSize, Qt, QTimer
from PySide2.QtGui import (QFont, QFontMetrics, QGuiApplication,
                          QKeySequence, QPixmap)
from PySide2.QtWidgets import (QAbstractItemView, QAbstractSpinBox, QApplication,
                               QCheckBox, QComboBox,
                               QDialog, QDialogButtonBox, QFileDialog,
                               QFormLayout, QFrame, QGridLayout, QGroupBox,
                               QHBoxLayout,
                               QLabel, QLineEdit, QListWidget, QMessageBox,
                               QPlainTextEdit, QProgressBar, QPushButton,
                               QRadioButton, QScrollArea, QShortcut,
                               QSpinBox, QDoubleSpinBox, QTableWidget,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout,
                               QWidget)

from zplcore import (connection as zpl_connection,
                     fields as zpl_fields, firmware as zpl_firmware,
                     fonts as zpl_fonts, geometry, graphic_store, helpdocs,
                     grid as zpl_grid, local_port as zpl_local,
                     print_settings as zpl_print, printer_io,
                     printer_objects, printer_status, textraster,
                     tips as zpl_tips, workflow)
from zplcore.connection import Connection, PortProblem
from zplcore.print_settings import AsWritten, PrintSettings
from zplcore.model import (BARCODE_CHECK_DIGIT, BARCODE_CONTROL_CHARACTERS,
                           BARCODE_FEATURES,
                           BARCODE_MODES, BARCODE_ORIENTATIONS,
                           BARCODE_PARAMETERS, BARCODE_SERIES, BarcodeSpin,
                           MAX_SERIES_ORIGINS,
                           BARCODE_SYMBOLOGIES, BARCODE_TEXT_CHOICES,
                           DIAGONAL_DIRECTIONS, FRAME_COLOURS, ORIENTATIONS,
                           STORED_GRAPHIC_COMMANDS, STORED_GRAPHIC_DEVICES,
                           TEXT_BLOCK_JUSTIFICATIONS, TEXT_BLOCKS,
                           TEXT_DIRECTIONS, TEXT_JUSTIFICATIONS,
                           BarcodeElement, CircleElement,
                           DiagonalLineElement, Document, EllipseElement,
                           FieldBlock, FrameElement, GraphicSymbolElement,
                           StoredGraphicElement, TextBlock, TextElement,
                           graphic_symbol_choices)

from .busy import BusyBar
from .canvas import to_qimage

IMAGE_FILTER = "Image files (*.jpg *.jpeg *.png *.JPG *.JPEG *.PNG);;All files (*)"
ZPL_FILTER = "ZPL files (*.zpl);;All files (*)"

PRESET_SIZES = [("4x6", 4, 6), ("5x7", 5, 7), ("6x4", 6, 4),
                ("3x5", 3, 5), ("2x3", 2, 3)]


def warn_unsupported(parent, commands):
    """Say which commands opening this file has left behind."""
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Warning)
    box.setWindowTitle("Unsupported commands")
    box.setText("This label uses ZPL the designer does not understand.")
    box.setInformativeText(
        f"{', '.join(commands)}\n\nThese are not shown on the canvas, and "
        f"saving will not preserve them.")
    box.exec_()


def notify_decoded(parent, code_page):
    """Say that this file was not UTF-8, and what a save will do with it."""
    text, detail = workflow.decoded_notice(code_page)
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Information)
    box.setWindowTitle("File encoding")
    box.setText(text)
    box.setInformativeText(detail)
    box.exec_()


def warn_control_redefined(parent, spellings):
    """Say that this file moved ZPL's control characters, and what a save does."""
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Information)
    box.setWindowTitle("Control characters redefined")
    box.setText("This label redefines ZPL's control characters.")
    box.setInformativeText(
        f"{', '.join(spellings)}\n\nIt has been read with them in force. "
        f"Saving writes the standard ^, ~ and , in their place and leaves "
        f"the redefinition out, so the saved file prints the same label but "
        f"no longer changes the printer's control characters.")
    box.exec_()


def show_error(parent, message: str):
    """Report a failure with the real underlying message, never a placeholder."""
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Critical)
    box.setWindowTitle("Error")
    box.setText("Error")
    box.setInformativeText(str(message))
    box.exec_()


def show_help(parent, topic: str = helpdocs.CONTENTS):
    """Open the help page `topic` in a viewer of its own (zplcore/helpdocs.py),
    or say why none could be started."""
    _follow_help(parent, helpdocs.launch(topic))


def _follow_help(parent, launched):
    """Say why no viewer could be started, or watch the one that was for its
    first few seconds: one that fails at once hands over to the next
    (helpdocs.follow), which is watched the same way."""
    if launched.message:
        box = QMessageBox(parent)
        box.setIcon(QMessageBox.Information)
        box.setWindowTitle("Help")
        box.setText("No help viewer")
        box.setInformativeText(launched.message)
        box.exec_()
        return
    started = time.monotonic()

    def poll():
        following = helpdocs.follow(launched, time.monotonic() - started)
        if following is helpdocs.WATCHING:
            QTimer.singleShot(helpdocs.POLL_MS, poll)
        elif following is not None:
            # Not `parent`: the window that asked may have closed since.
            _follow_help(QApplication.activeWindow(), following)

    QTimer.singleShot(helpdocs.POLL_MS, poll)


def add_help(dialog, key: str, box: Optional[QDialogButtonBox] = None):
    """Give a window its help: the page helpdocs.WINDOW_HELP names for `key`,
    on a Help button and on F1. Given `box`, the button is that box's own
    Help, which Qt's Linux layouts put at the left and which leaves the
    window open; without one, the button is returned for a row built by
    hand. Either way it is named "help"."""
    topic = helpdocs.WINDOW_HELP[key]

    def open_page():
        show_help(dialog, topic)

    if box is not None:
        button = box.addButton(QDialogButtonBox.Help)
        box.helpRequested.connect(open_page)
    else:
        button = QPushButton("&Help")
        button.setAutoDefault(False)
        button.clicked.connect(open_page)
    button.setObjectName("help")
    QShortcut(QKeySequence(Qt.Key_F1), dialog).activated.connect(open_page)
    return button


def add_question_help(box: QMessageBox, key: str):
    """A question's Help, on a button and on F1: the page WINDOW_HELP names
    for `key`. A message box closes on any of its buttons, so this one is
    cut loose from the box and only opens the page, leaving the question
    up to be answered."""
    topic = helpdocs.WINDOW_HELP[key]

    def open_page():
        show_help(box, topic)

    button = box.addButton(QMessageBox.Help)
    button.setObjectName("help")
    button.clicked.disconnect()
    button.clicked.connect(open_page)
    QShortcut(QKeySequence(Qt.Key_F1), box).activated.connect(open_page)
    return button


def _buttons(dialog, accept_text="OK"):
    box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=dialog)
    box.button(QDialogButtonBox.Ok).setText(accept_text)
    box.accepted.connect(dialog.accept)
    box.rejected.connect(dialog.reject)
    return box


def _font_device_combo(device: str) -> QComboBox:
    """Which printer memory a font goes to, offered the same way wherever it
    is chosen - the printer rows, and the Fonts manager's own Upload.

    The same STORED_GRAPHIC_DEVICES the Store Graphic dialog offers, so a
    memory type reads identically everywhere ("E: (Flash)"), and Z: is absent
    for the same reason it is there: ~DY cannot write it and ^ID will not
    delete it.
    """
    combo = QComboBox()
    combo.setObjectName("font_device")
    for label, code in STORED_GRAPHIC_DEVICES:
        combo.addItem(label, code)
    index = combo.findData((device or zpl_fonts.DEFAULT_FONT_DEVICE).upper())
    combo.setCurrentIndex(index if index >= 0 else 0)
    return combo


def _dpi_combo(dpi: int) -> QComboBox:
    """The resolution choice, offered the same way wherever it is edited.

    Label Settings and the printer rows (Default Printer, the Print window)
    all set the printer's resolution, so they have to offer the same list -
    including the fallback: a resolution the designer does not support can
    still be the one in force, and must be shown rather than silently
    replaced with a supported one.
    """
    combo = QComboBox()
    combo.setObjectName("dpi")
    for d in zpl_fonts.SUPPORTED_DPI:
        combo.addItem(str(d))
    if dpi in zpl_fonts.SUPPORTED_DPI:
        combo.setCurrentIndex(list(zpl_fonts.SUPPORTED_DPI).index(dpi))
    else:
        combo.addItem(str(dpi))
        combo.setCurrentIndex(combo.count() - 1)
    return combo


def _dpi_from(combo: QComboBox, fallback: int) -> int:
    text = combo.currentText()
    return int(text) if text.isdigit() else fallback


class _FittingScrollArea(QScrollArea):
    """A scroll area that asks for as much room as its body wants, up to the
    screen's usable area less the title bar and the button row.

    Asked at show time rather than fixed when built: an editor shows and hides
    rows as its fields change, so a size taken early is too small by then.

    The screen is looked up by where the window is, not with QWidget.screen():
    PySide2 hangs the QScreen it returns on the widget, and a window that
    later goes out in a garbage collection takes the application's screen
    with it - after which nothing can open a window at all.
    """

    def sizeHint(self):
        window = self.window()
        screen = (QApplication.screenAt(window.frameGeometry().center())
                  or QApplication.primaryScreen())
        avail = screen.availableGeometry()
        hint = self.widget().sizeHint()
        bar = self.verticalScrollBar().sizeHint().width() + 4
        return QSize(min(hint.width() + bar, avail.width() - 40),
                     min(hint.height() + bar, avail.height() - 160))


def _make_scrollable(dialog):
    """Keep a dialog inside the screen: its body scrolls once it outgrows it.

    The form moves into a scroll area; the OK/Cancel row stays outside it, so
    a small display never pushes it off the bottom. A dialog that already fits
    keeps its natural size.
    """
    layout = dialog.layout()
    buttons = dialog.findChild(QDialogButtonBox)
    if layout is None or buttons is None:
        return
    body = QWidget()
    body_layout = QVBoxLayout(body)
    body_layout.setContentsMargins(0, 0, 0, 0)
    while layout.count():
        item = layout.takeAt(0)
        if item.widget() is buttons:
            continue
        if item.layout() is not None:
            body_layout.addLayout(item.layout())
        elif item.widget() is not None:
            body_layout.addWidget(item.widget())
        else:
            body_layout.addItem(item)

    scroll = _FittingScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.NoFrame)
    scroll.setWidget(body)
    layout.addWidget(scroll)
    layout.addWidget(buttons)


def _show_editor(dialog, apply_edits, on_accept=None):
    """Put an element editor on screen as a non-modal child of the designer.

    The window is transient for the designer but never blocks it, so the
    fields cannot be read back off a returned value the way a modal dialog
    allowed. `finished` is the one signal that covers every way out - OK,
    Cancel, Escape and the window-manager close button alike - so the edit is
    applied there, and the caller hears about it through `on_accept`.
    """
    dialog.setAttribute(Qt.WA_DeleteOnClose)
    _make_scrollable(dialog)

    def on_finished(result):
        if result != QDialog.Accepted:
            return
        apply_edits()
        if on_accept:
            on_accept()

    dialog.finished.connect(on_finished)
    dialog.show()
    return dialog


class EditorForm:
    """An element editor's rows, apart from whatever window they are shown in.

    `body` is a widget holding them - `rows`, a form layout - and `apply()`
    writes every row back onto the element. The same form goes into an
    editor window with OK and Cancel (`_editor_dialog`) or anywhere else a
    widget can go.

    `on_change(callback)` asks to hear about every change to a row, whether
    it came through an input (`watch`) or a button that changes something no
    input shows - a font chosen, a position added. A form calls `changed()`
    for those itself, and `watch()` for any input it adds after it was built.
    """

    def __init__(self, title: str):
        self.title = title
        self.body = QWidget()
        self.rows = QFormLayout(self.body)
        # The window it goes into has margins of its own
        self.rows.setContentsMargins(0, 0, 0, 0)
        self.apply = lambda: None
        self._listeners = []

    def on_change(self, callback) -> None:
        self._listeners.append(callback)

    def changed(self, *_args) -> None:
        for callback in list(self._listeners):
            callback()

    def watch(self, root: QWidget) -> None:
        """Report every change to an input at or under `root` as a change to
        the form.

        A line edit inside a spin box or a combo box is that control's own,
        and reports through it, so it is passed over. textChanged rather than
        textEdited, since a row written by a button - an Insert into the
        barcode value - is a change too.
        """
        for widget in [root] + root.findChildren(QWidget):
            if isinstance(widget, (QSpinBox, QDoubleSpinBox)):
                widget.valueChanged.connect(self.changed)
            elif isinstance(widget, QComboBox):
                widget.currentIndexChanged.connect(self.changed)
            elif isinstance(widget, QCheckBox):
                widget.toggled.connect(self.changed)
            elif isinstance(widget, QPlainTextEdit):
                widget.textChanged.connect(self.changed)
            elif (isinstance(widget, QLineEdit) and not isinstance(
                    widget.parent(), (QAbstractSpinBox, QComboBox))):
                widget.textChanged.connect(self.changed)

    def finish(self, apply) -> 'EditorForm':
        """Take the function that writes the rows back, and start listening
        to every input the form was built with."""
        self.apply = apply
        self.watch(self.body)
        return self


def _editor_dialog(parent, editor: EditorForm, on_accept=None) -> QDialog:
    """A form in an editor window: non-modal, with OK and Cancel, applying on
    OK (see _show_editor)."""
    dialog = QDialog(parent)
    dialog.setWindowTitle(editor.title)
    # The rows' Python side - apply() and its closures - lives as long as
    # the window showing them.
    dialog.editor = editor
    layout = QVBoxLayout(dialog)
    layout.addWidget(editor.body)
    layout.addWidget(_buttons(dialog))
    return _show_editor(dialog, editor.apply, on_accept)


# --- fonts ------------------------------------------------------------------

class FontFamilyDialog(QDialog):
    """Pick one installed TrueType family.

    Qt's own font dialog cannot be restricted to families that have a .ttf
    file, and only TrueType can be uploaded to the printer - offering an
    OpenType or a Type 1 face would produce a label that prints in a substitute
    typeface. So the list comes from zpl_fonts, which knows the difference.
    """

    def __init__(self, parent, current_family: Optional[str] = None,
                 title: str = "Choose Font"):
        super().__init__(parent)
        self.setWindowTitle(title)
        # Opened from inside a text editor, which is itself a non-modal child
        # of the designer. Window-modal rather than application-modal, so it
        # blocks the one editor that raised it and nothing else.
        self.setWindowModality(Qt.WindowModal)
        self.resize(380, 460)
        self._families = zpl_fonts.list_ttf_families()

        layout = QVBoxLayout(self)
        self._filter = QLineEdit()
        self._filter.setPlaceholderText("Filter families…")
        layout.addWidget(self._filter)

        self._list = QListWidget()
        self._list.setSelectionMode(QAbstractItemView.SingleSelection)
        layout.addWidget(self._list, 1)

        self._preview = QLabel("The quick brown fox 0123456789")
        self._preview.setMinimumHeight(52)
        self._preview.setWordWrap(True)
        layout.addWidget(self._preview)

        layout.addWidget(_buttons(self))

        self._filter.textChanged.connect(self._repopulate)
        self._list.currentTextChanged.connect(self._update_preview)
        self._list.itemDoubleClicked.connect(lambda _i: self.accept())

        self._repopulate()
        if current_family:
            found = self._list.findItems(current_family, Qt.MatchExactly)
            if found:
                self._list.setCurrentItem(found[0])

    def _repopulate(self):
        needle = self._filter.text().strip().lower()
        current = self._list.currentItem().text() if self._list.currentItem() else None
        self._list.clear()
        for family in sorted(self._families):
            if not needle or needle in family.lower():
                self._list.addItem(family)
        if current:
            found = self._list.findItems(current, Qt.MatchExactly)
            if found:
                self._list.setCurrentItem(found[0])

    def _update_preview(self, family: str):
        path = self._families.get(family)
        if path:
            # Registered first, or Qt would fall back to a default face for a
            # family it has not been told about.
            zpl_fonts.register_app_font(path)
        font = QFont(family)
        font.setPointSize(16)
        self._preview.setFont(font)

    def selected_family(self) -> Optional[str]:
        item = self._list.currentItem()
        return item.text() if item else None


def choose_font_family(parent, current_family=None, title="Choose Font"):
    """(family, path) for a chosen TrueType family, or (None, None)."""
    families = zpl_fonts.list_ttf_families()
    if not families:
        show_error(parent, "No TrueType fonts were found on this system.")
        return None, None
    dialog = FontFamilyDialog(parent, current_family, title)
    if dialog.exec_() != QDialog.Accepted:
        return None, None
    family = dialog.selected_family()
    if not family:
        return None, None
    path = zpl_fonts.file_for_family(family)
    if not path:
        show_error(parent, f"No TrueType file found for '{family}'.")
        return None, None
    return family, path


class LocalFontsDialog(QDialog):
    """What the directory-scan font fallback sees, and whether it is in use.

    fc-list is the only thing list_ttf_families() ever calls first, and on
    most systems that is the end of it - but on a system where fontconfig is
    missing, broken, or just not set up, every font chooser would otherwise
    go quietly empty with nothing here to explain why. This is the one place
    to check either way: idle while fc-list answers normally, or the thing
    actually supplying every font in the chooser when it does not.

    Distinct from the Printer -> Fonts... dialog, which is about fonts
    stored on the physical printer, not fonts installed on this machine.
    """

    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Local Fonts")
        self.resize(480, 420)

        layout = QVBoxLayout(self)
        self._status = QLabel()
        self._status.setWordWrap(True)
        layout.addWidget(self._status)

        self._report = QPlainTextEdit()
        self._report.setReadOnly(True)
        self._report.setFont(QFont("monospace"))
        layout.addWidget(self._report, 1)

        row = QHBoxLayout()
        self._rescan_btn = QPushButton("Rescan")
        row.addWidget(self._rescan_btn)
        self._extra_btn = QPushButton("Extra Font Folder…")
        self._extra_btn.setObjectName("extra_font_folder")
        set_tip(self._extra_btn, 'extra_font_folder')
        row.addWidget(self._extra_btn)
        row.addStretch(1)
        layout.addLayout(row)

        close = QDialogButtonBox(QDialogButtonBox.Close, parent=self)
        close.rejected.connect(self.reject)
        add_help(self, "local_fonts", close)
        layout.addWidget(close)

        self._rescan_btn.clicked.connect(lambda: self.refresh(rescan=True))
        self._extra_btn.clicked.connect(self._choose_extra_folder)
        self.refresh(rescan=False)

    def _choose_extra_folder(self):
        # The window owns the choice and its persistence; this just rescans
        # so the new folder shows up in the report.
        self.parent().on_extra_font_folder()
        self.refresh(rescan=True)

    def refresh(self, rescan: bool):
        fc_list_ok, report = zpl_fonts.font_discovery_status(refresh=rescan)
        self._status.setText(
            "fc-list is working normally - the directory scan below is not "
            "being used, but shows what it would find if fc-list stopped "
            "working." if fc_list_ok else
            "fc-list is unavailable or reports no TrueType fonts on this "
            "system - LinuxZPL is using the directory scan below to find "
            "fonts instead.")

        lines = ["Font file scan", "-" * 60, ""]
        for d in report.dirs:
            lines.append(f"Scanning: {d.path}")
            lines.append("  (directory not found)" if not d.exists else
                         f"  Found {d.font_count} font file(s)")
            lines.append("")
        lines.append(f"Total: {len(report.files)} font file(s), "
                     f"{len(report.families)} usable family(ies)")
        self._report.setPlainText("\n".join(lines))


# --- tooltips ---------------------------------------------------------------

def tip_markup(text: str) -> str:
    """A tip as Qt shows it: as a paragraph, since Qt word-wraps only a
    rich-text tooltip and a plain one runs off the screen."""
    return "<p>" + html.escape(text, quote=False) + "</p>"


def set_tip(widget, key: str, form=None, label=None) -> None:
    """Give `widget` the tip zplcore.tips holds for `key`, and the label of
    its row in `form` too, so hovering either explains it - or `label`, for
    a row whose field is a layout around `widget`. A key the table doesn't
    have is a mistake here, and fails."""
    set_tip_text(widget, zpl_tips.TIPS[key], form, label)


def set_tip_text(widget, text: str, form=None, label=None) -> None:
    """set_tip for a tip looked up some other way - a barcode row's, which
    depends on the symbology (zplcore.tips.PARAMETERS, CHECK_DIGIT)."""
    markup = tip_markup(text)
    widget.setToolTip(markup)
    if label is None and form is not None:
        label = form.labelForField(widget)
    if label is not None:
        label.setToolTip(markup)


# --- element editing --------------------------------------------------------


def _name_row(form, element):
    """The ID row of an element that carries data: what a Python program
    finds it by when it fills the label in (Label.fill). Kept as an ^FX comment,
    which a printer ignores."""
    edit = QLineEdit(element.element_id)
    edit.setObjectName("element_id")
    edit.setPlaceholderText("optional - Python fills this element in by ID")
    form.addRow("ID:", edit)
    set_tip(edit, 'element_id', form)

    def apply_to(target):
        target.element_id = edit.text()

    return apply_to


def _field_number_rows(form, element):
    """The ^FN controls, for a barcode.

    Text no longer uses this: a numbered text field gets its own creation
    button and its own edit_numbered_dialog - see that function's docstring
    - the same way ^SN and ^FC already got edit_serial_dialog and
    edit_time_dialog. ^FN stays here for a barcode, though: a recalled
    stored-format barcode is common and already tested, unlike a serialized
    or clock-substituted one, so it keeps a row rather than moving out
    entirely.

    A barcode either prints a literal or takes its data from a numbered field
    the printer fills in, so this is a tick rather than a number that has to
    mean "none" - 0 is a field number ZPL allows.
    """
    check = QCheckBox("Data comes from a numbered field (^FN)")
    check.setObjectName("variable")
    check.setChecked(element.field_number is not None)
    form.addRow("Variable:", check)
    set_tip(check, 'variable', form)

    number = QSpinBox()
    number.setObjectName("field_number")
    number.setRange(0, zpl_fields.MAX_NUMBER)
    number.setValue(element.field_number or 0)
    form.addRow("Field Number:", number)
    set_tip(number, 'field_number', form)

    prompt = QLineEdit(element.field_prompt or '')
    prompt.setObjectName("field_prompt")
    prompt.setPlaceholderText("shown on the canvas and on a printer keypad")
    form.addRow("Field Name:", prompt)
    set_tip(prompt, 'field_prompt', form)

    def sync():
        number.setEnabled(check.isChecked())
        prompt.setEnabled(check.isChecked())

    sync()
    check.stateChanged.connect(sync)

    def apply_to(target):
        if check.isChecked():
            target.field_number = number.value()
            target.field_prompt = prompt.text() or None
        else:
            target.field_number = None
            target.field_prompt = None

    return apply_to


def _set_quietly(edit, text: str) -> None:
    """Put text in a line or text box without it counting as an edit."""
    edit.blockSignals(True)
    try:
        if isinstance(edit, QPlainTextEdit):
            edit.setPlainText(text)
        else:
            edit.setText(text)
    finally:
        edit.blockSignals(False)


def _text_format_rows(editor, form, element: TextElement,
                      document: Document, show_text=None):
    """The rows every text-like editor shares: ^FP direction and gap, ^FR,
    the font, and the ^FB/^TB wrap block.

    `apply()` can change the element beyond what these rows say - a line
    break typed with Wrap clear gets a block, and clearing Wrap joins the
    lines - and then puts the rows, and through `show_text` the caller's own
    text row, back in step with it. A window applies once and closes, but the
    Properties panel applies on every change, and rows left behind would
    undo the block on the next keystroke.

    Edit Text and Edit Time Field both call this, so a clock field is styled
    the same way a plain one is. The caller adds its own text row, size and
    orientation first, sets the element's text, then calls `apply()`, which
    reads these fields back onto the element. `text_block_chosen()` says
    whether a ^TB is ticked, because the caller must know before it sets the
    text: a ^TB has no line break to write a typed one as.
    """
    # ^FP: which way the characters run inside that turn, and how far apart
    direction_combo = QComboBox()
    direction_combo.setObjectName("direction")
    for label, code in TEXT_DIRECTIONS:
        direction_combo.addItem(label, code)
    ways = [code for _label, code in TEXT_DIRECTIONS]
    direction_combo.setCurrentIndex(ways.index(element.direction)
                                    if element.direction in ways else 0)
    form.addRow("Direction:", direction_combo)
    set_tip(direction_combo, 'direction', form)

    gap_spin = QSpinBox()
    gap_spin.setObjectName("char_gap")
    gap_spin.setRange(0, TextElement.MAX_CHAR_GAP)
    gap_spin.setValue(element.char_gap)
    form.addRow("Character Gap:", gap_spin)
    set_tip(gap_spin, 'char_gap', form)

    fr_check = QCheckBox("Reverse print (^FR)")
    fr_check.setObjectName("reverse_print")
    fr_check.setChecked(element.reverse_print)
    form.addRow("Reverse:", fr_check)
    set_tip(fr_check, 'reverse_print', form)

    chosen = {'path': element.font_path, 'family': element.font_family}

    def font_display():
        if chosen['family']:
            return chosen['family']
        return (f"Default ({document.font_family})" if document.font_family
                else "Default")

    font_row = QWidget()
    font_layout = QHBoxLayout(font_row)
    font_layout.setContentsMargins(0, 0, 0, 0)
    font_label = QLabel(font_display())
    choose_btn = QPushButton("Choose…")
    clear_btn = QPushButton("Clear")
    font_layout.addWidget(font_label, 1)
    font_layout.addWidget(choose_btn)
    font_layout.addWidget(clear_btn)
    font_row.setObjectName("font")
    form.addRow("Font:", font_row)
    set_tip(font_row, 'font', form)

    def on_choose():
        # Modal to whichever window the rows are in, not to the application
        family, path = choose_font_family(editor.body.window(), chosen['family'])
        if not family:
            return
        chosen['family'], chosen['path'] = family, path
        font_label.setText(font_display())
        editor.changed()

    def on_clear():
        # Back to the document font, or the printer's built-in font A.
        chosen['family'], chosen['path'] = None, None
        font_label.setText(font_display())
        editor.changed()

    choose_btn.clicked.connect(on_choose)
    clear_btn.clicked.connect(on_clear)

    # --- wrapping (^FB or ^TB) ---
    # Each kind's fields start from the element's block when it is that
    # kind, and otherwise from one that draws the text as it is now.
    block = (element.block if isinstance(element.block, FieldBlock)
             else element.default_block(document.font_path, document.dpi))
    text_block = (element.block if isinstance(element.block, TextBlock)
                  else element.default_text_block(document.font_path,
                                                  document.dpi))

    wrap_check = QCheckBox("Wrap the text into a block")
    wrap_check.setObjectName("wrap")
    wrap_check.setChecked(element.block is not None)
    form.addRow("Wrap:", wrap_check)
    set_tip(wrap_check, 'wrap', form)

    kind_combo = QComboBox()
    kind_combo.setObjectName("block_kind")
    for label, code in TEXT_BLOCKS:
        kind_combo.addItem(label, code)
    kind_combo.setCurrentIndex(1 if isinstance(element.block, TextBlock)
                               else 0)
    form.addRow("Block:", kind_combo)
    set_tip(kind_combo, 'block_kind', form)

    block_width = QSpinBox()
    block_width.setRange(10, 2000)
    block_width.setObjectName("block_width")
    block_width.setValue((element.block or block).width)
    form.addRow("Wrap Width:", block_width)
    set_tip(block_width, 'block_width', form)

    block_height = QSpinBox()
    block_height.setRange(1, 32000)
    block_height.setObjectName("block_height")
    block_height.setValue(text_block.height)
    form.addRow("Block Height:", block_height)
    set_tip(block_height, 'block_height', form)

    max_lines = QSpinBox()
    max_lines.setRange(1, 64)
    max_lines.setObjectName("max_lines")
    max_lines.setValue(block.max_lines)
    form.addRow("Max Lines:", max_lines)
    set_tip(max_lines, 'max_lines', form)

    spacing_spin = QSpinBox()
    spacing_spin.setRange(-100, 100)
    spacing_spin.setObjectName("line_spacing")
    spacing_spin.setValue(block.line_spacing)
    form.addRow("Line Spacing:", spacing_spin)
    set_tip(spacing_spin, 'line_spacing', form)

    justify_combo = QComboBox()
    justify_combo.setObjectName("justification")
    for label, code in TEXT_JUSTIFICATIONS:
        justify_combo.addItem(label, code)
    codes = [code for _label, code in TEXT_JUSTIFICATIONS]
    justify_combo.setCurrentIndex(codes.index(block.justification)
                                  if block.justification in codes else 0)
    form.addRow("Justification:", justify_combo)
    set_tip(justify_combo, 'justification', form)

    # A ^TB's lines align by the field's own justification, which has
    # neither a centre nor a justified
    text_justify_combo = QComboBox()
    text_justify_combo.setObjectName("text_justification")
    for label, code in TEXT_BLOCK_JUSTIFICATIONS:
        text_justify_combo.addItem(label, code)
    text_justify_combo.setCurrentIndex(
        1 if element.justify == geometry.JUSTIFY_RIGHT else 0)
    form.addRow("Justification:", text_justify_combo)
    set_tip(text_justify_combo, 'text_justification', form)

    indent_spin = QSpinBox()
    indent_spin.setRange(0, 2000)
    indent_spin.setObjectName("indent")
    indent_spin.setValue(block.indent)
    form.addRow("Indent:", indent_spin)
    set_tip(indent_spin, 'indent', form)

    field_block_only = (max_lines, spacing_spin, justify_combo, indent_spin)
    text_block_only = (block_height, text_justify_combo)
    block_fields = ((kind_combo, block_width) + field_block_only
                    + text_block_only)

    def sync_block_fields():
        for field in block_fields:
            field.setEnabled(wrap_check.isChecked())
        text = kind_combo.currentData() == 'TB'
        for fields, shown in ((field_block_only, not text),
                              (text_block_only, text)):
            for field in fields:
                field.setVisible(shown)
                form.labelForField(field).setVisible(shown)
        # The manual does not say what ^FB does with a direction, so a block
        # keeps whichever it has and offers no other.
        direction_combo.setEnabled(not wrap_check.isChecked())

    sync_block_fields()
    wrap_check.stateChanged.connect(sync_block_fields)
    kind_combo.currentIndexChanged.connect(sync_block_fields)

    def text_block_chosen():
        return wrap_check.isChecked() and kind_combo.currentData() == 'TB'

    def show_block(new_block):
        """Tick Wrap and show a field block the element was just given."""
        rows = (wrap_check, kind_combo, block_width, max_lines, spacing_spin,
                justify_combo, indent_spin)
        for row in rows:
            row.blockSignals(True)
        try:
            wrap_check.setChecked(True)
            kind_combo.setCurrentIndex(0)
            block_width.setValue(new_block.width)
            max_lines.setValue(new_block.max_lines)
            spacing_spin.setValue(new_block.line_spacing)
            justify_combo.setCurrentIndex(
                codes.index(new_block.justification)
                if new_block.justification in codes else 0)
            indent_spin.setValue(new_block.indent)
        finally:
            for row in rows:
                row.blockSignals(False)
        sync_block_fields()

    def apply():
        element.direction = direction_combo.currentData()
        element.char_gap = gap_spin.value()
        element.reverse_print = fr_check.isChecked()

        if text_block_chosen():
            element.set_text_block(block_width.value(), block_height.value(),
                                   text_justify_combo.currentData())
        elif wrap_check.isChecked():
            # Assigned rather than mutated: the block on the element may
            # be the one an undo snapshot is holding.
            element.block = FieldBlock(block_width.value(), max_lines.value(),
                                       spacing_spin.value(),
                                       justify_combo.currentData(),
                                       indent_spin.value())
        elif element.block is not None:
            # Unticked. A forced break left behind would print as the two
            # characters it is written with, so the lines are joined
            # rather than abandoned to the printer.
            element.text = textraster.join_lines(element.text)
            element.block = None
            if show_text is not None:
                show_text()
        elif textraster.FORCED_BREAK in element.text:
            # A break typed into an element that never had a block still
            # needs one, for the same reason. Sized to the longest line,
            # so nothing moves.
            element.block = element.default_block(document.font_path,
                                                  document.dpi)
            show_block(element.block)

        if chosen['path'] != element.font_path:
            if chosen['path']:
                # The font is only recorded here; it is uploaded at print
                # time, so choosing a font never blocks on the network.
                name = zpl_fonts.printer_font_name(
                    chosen['path'],
                    taken=document.printer_font_names(exclude=element))
                document.set_element_font(element, chosen['path'],
                                          chosen['family'], name)
            else:
                element.font_path = None
                element.font_family = None
                element.printer_font_name = None

    return SimpleNamespace(text_block_chosen=text_block_chosen, apply=apply)



def text_form(element: TextElement, document: Document) -> EditorForm:
    """The rows that edit a text element."""
    editor = EditorForm("Edit Text")
    form = editor.rows

    # Multi-line, because ZPL's forced break is two characters a user should
    # never have to spell: Enter here becomes \& on the way out. A ^TB breaks
    # no line at \&, so its data is shown as it is.
    text_edit = QPlainTextEdit(
        element.text if isinstance(element.block, TextBlock)
        else textraster.to_editor(element.text))
    text_edit.setObjectName("text")
    text_edit.setMinimumHeight(4 * QFontMetrics(text_edit.font()).height())
    form.addRow("Text:", text_edit)

    height_spin = QSpinBox()
    height_spin.setRange(8, 500)
    height_spin.setValue(element.font_height)
    form.addRow("Font Height:", height_spin)

    width_spin = QSpinBox()
    width_spin.setObjectName("font_width")
    width_spin.setRange(8, 500)
    width_spin.setValue(element.font_width)
    form.addRow("Font Width:", width_spin)
    set_tip(width_spin, 'font_width', form)

    orientation_combo = QComboBox()
    orientation_combo.setObjectName("orientation")
    for label, code in ORIENTATIONS:
        orientation_combo.addItem(label, code)
    turns = [code for _label, code in ORIENTATIONS]
    orientation_combo.setCurrentIndex(turns.index(element.orientation)
                                      if element.orientation in turns else 0)
    form.addRow("Orientation:", orientation_combo)
    set_tip(orientation_combo, 'orientation', form)

    fmt = _text_format_rows(
        editor, form, element, document,
        show_text=lambda: _set_quietly(text_edit, textraster.to_editor(element.text)))


    apply_name = _name_row(form, element)

    def _apply():
        apply_name(element)
        # A ^TB has no line break to write a typed one as, so it is a space
        element.text = (textraster.from_editor_unbroken
                        if fmt.text_block_chosen()
                        else textraster.from_editor)(text_edit.toPlainText())
        element.font_height = height_spin.value()
        element.font_width = width_spin.value()
        element.orientation = orientation_combo.currentData()
        element.height = element.font_height
        fmt.apply()
        document.sync_text_width(element)

    return editor.finish(_apply)


def edit_text_dialog(parent, element: TextElement, document: Document,
                     on_accept=None) -> QDialog:
    """text_form in a window of its own, with OK and Cancel.
    `on_accept` runs once OK has changed the element."""
    return _editor_dialog(parent, text_form(element, document), on_accept)


def time_form(element: TextElement, document: Document) -> EditorForm:
    """The rows that edit a clock field (^FC).

    Takes the same font, direction and wrap rows as edit_text_dialog, through
    _text_format_rows - a clock stamp is styled like any other text. It has no
    Data Source selector: this dialog *is* the ^FC source. edit_text_dialog
    carries no field-source mechanism of its own at all any more: ^FN, ^SN and
    ^FC each moved out to their own dialog. The one on/off control here is the
    checkbox at the bottom: unticking it turns the element back into a plain
    static text field, whose rows - text_form's - then take the place of
    these (form_builder).
    """
    editor = EditorForm("Edit Time Field")
    form = editor.rows

    text_edit = QLineEdit(element.text)
    text_edit.setObjectName("clock_text")
    form.addRow("Format:", text_edit)
    set_tip(text_edit, 'clock_text', form)

    height_spin = QSpinBox()
    height_spin.setRange(8, 500)
    height_spin.setValue(element.font_height)
    form.addRow("Font Height:", height_spin)

    width_spin = QSpinBox()
    width_spin.setObjectName("font_width")
    width_spin.setRange(8, 500)
    width_spin.setValue(element.font_width)
    form.addRow("Font Width:", width_spin)
    set_tip(width_spin, 'font_width', form)

    orientation_combo = QComboBox()
    orientation_combo.setObjectName("orientation")
    for label, code in ORIENTATIONS:
        orientation_combo.addItem(label, code)
    turns = [code for _label, code in ORIENTATIONS]
    orientation_combo.setCurrentIndex(turns.index(element.orientation)
                                      if element.orientation in turns else 0)
    form.addRow("Orientation:", orientation_combo)
    set_tip(orientation_combo, 'orientation', form)

    fmt = _text_format_rows(
        editor, form, element, document,
        show_text=lambda: _set_quietly(text_edit, element.text))

    clock_check = QCheckBox("Comes from the printer's clock (^FC)")
    clock_check.setObjectName("clock_format")
    clock_check.setChecked(element.clock_format)
    form.addRow("Clock:", clock_check)
    set_tip(clock_check, 'clock_format', form)

    apply_name = _name_row(form, element)

    def _apply():
        apply_name(element)
        element.text = text_edit.text()
        element.font_height = height_spin.value()
        element.font_width = width_spin.value()
        element.orientation = orientation_combo.currentData()
        element.height = element.font_height
        fmt.apply()
        if clock_check.isChecked():
            element.clock_format = True
        else:
            # Off - a field's existing custom trigger characters, if it had
            # any, no longer mean anything once it is plain static text.
            element.clock_format = False
            element.clock_chars = None
        # Last, because the box is measured from what the canvas will draw,
        # and that is the wrapped marker for as long as this stays a clock
        # field.
        document.sync_text_width(element)

    return editor.finish(_apply)


def edit_time_dialog(parent, element: TextElement, document: Document,
                     on_accept=None) -> QDialog:
    """time_form in a window of its own, with OK and Cancel.
    `on_accept` runs once OK has changed the element."""
    return _editor_dialog(parent, time_form(element, document), on_accept)


def serial_form(element: TextElement, document: Document) -> EditorForm:
    """The rows that edit a serialized field (^SN).

    Takes the same font, direction and wrap rows as edit_text_dialog, through
    _text_format_rows - a serial number is styled like any other text. It has
    no Data Source selector: this dialog *is* the ^SN source. edit_text_dialog carries no
    field-source mechanism of its own at all any more: ^FN, ^SN and ^FC each
    moved out to their own dialog. The one on/off control here is the
    checkbox at the bottom: unticking it turns the element back into a plain
    static text field, whose rows - text_form's - then take the place of
    these (form_builder).
    """
    editor = EditorForm("Edit Serial Field")
    form = editor.rows

    text_edit = QLineEdit(element.text)
    text_edit.setObjectName("serial_start")
    form.addRow("Start Value:", text_edit)
    set_tip(text_edit, 'serial_start', form)

    increment = QSpinBox()
    increment.setObjectName("serial_increment")
    increment.setRange(-999999, 999999)
    increment.setValue(element.serial_increment
                       if element.serial_increment is not None else 1)
    form.addRow("Increment:", increment)
    set_tip(increment, 'serial_increment', form)

    leading_zero = QCheckBox("Add leading zeros")
    leading_zero.setObjectName("serial_leading_zero")
    leading_zero.setChecked(element.serial_leading_zero)
    form.addRow("", leading_zero)
    set_tip(leading_zero, 'serial_leading_zero', form)

    height_spin = QSpinBox()
    height_spin.setRange(8, 500)
    height_spin.setValue(element.font_height)
    form.addRow("Font Height:", height_spin)

    width_spin = QSpinBox()
    width_spin.setObjectName("font_width")
    width_spin.setRange(8, 500)
    width_spin.setValue(element.font_width)
    form.addRow("Font Width:", width_spin)
    set_tip(width_spin, 'font_width', form)

    orientation_combo = QComboBox()
    orientation_combo.setObjectName("orientation")
    for label, code in ORIENTATIONS:
        orientation_combo.addItem(label, code)
    turns = [code for _label, code in ORIENTATIONS]
    orientation_combo.setCurrentIndex(turns.index(element.orientation)
                                      if element.orientation in turns else 0)
    form.addRow("Orientation:", orientation_combo)
    set_tip(orientation_combo, 'orientation', form)

    fmt = _text_format_rows(
        editor, form, element, document,
        show_text=lambda: _set_quietly(text_edit, element.text))

    serial_check = QCheckBox("Auto-increments each print (^SN)")
    serial_check.setObjectName("serial_format")
    serial_check.setChecked(element.serial_increment is not None)
    form.addRow("Serial:", serial_check)
    set_tip(serial_check, 'serial_format', form)

    apply_name = _name_row(form, element)

    def _apply():
        apply_name(element)
        element.text = text_edit.text()
        element.font_height = height_spin.value()
        element.font_width = width_spin.value()
        element.orientation = orientation_combo.currentData()
        element.height = element.font_height
        fmt.apply()
        if serial_check.isChecked():
            element.serial_start = element.text
            element.serial_increment = increment.value()
            element.serial_leading_zero = leading_zero.isChecked()
        else:
            # Off - the field is plain static text now, showing whatever
            # value it last had.
            element.serial_start = None
            element.serial_increment = None
            element.serial_leading_zero = False
        # Last, because the box is measured from what the canvas will draw,
        # and that is the wrapped marker for as long as this stays a serial
        # field.
        document.sync_text_width(element)

    return editor.finish(_apply)


def edit_serial_dialog(parent, element: TextElement, document: Document,
                       on_accept=None) -> QDialog:
    """serial_form in a window of its own, with OK and Cancel.
    `on_accept` runs once OK has changed the element."""
    return _editor_dialog(parent, serial_form(element, document), on_accept)


def numbered_form(element: TextElement, document: Document) -> EditorForm:
    """The rows that edit a numbered field (^FN).

    The font, direction, wrap and reverse rows are the ones edit_text_dialog
    has (_text_format_rows); the one thing it leaves out is a Data Source
    selector, since this dialog *is* the ^FN source. The one on/off control is the checkbox at the bottom: unticking
    it turns the element back into a plain static text field, whose rows -
    text_form's - then take the place of these (form_builder).

    ^FN is still available on a barcode, through `_field_number_rows` in
    edit_barcode_dialog - unlike ^SN/^FC, a recalled stored-format barcode is
    common and already tested, so it keeps a row there rather than moving
    out entirely.
    """
    editor = EditorForm("Edit Numbered Field")
    form = editor.rows

    text_edit = QLineEdit(element.text)
    text_edit.setObjectName("numbered_text")
    text_edit.setPlaceholderText("optional - shared with every other field "
                                 "carrying the same number")
    form.addRow("Text:", text_edit)
    set_tip(text_edit, 'numbered_text', form)

    number = QSpinBox()
    number.setObjectName("field_number")
    number.setRange(0, zpl_fields.MAX_NUMBER)
    number.setValue(element.field_number or 0)
    form.addRow("Field Number:", number)
    set_tip(number, 'field_number', form)

    prompt = QLineEdit(element.field_prompt or '')
    prompt.setObjectName("field_prompt")
    prompt.setPlaceholderText("shown on the canvas and on a printer keypad")
    form.addRow("Field Name:", prompt)
    set_tip(prompt, 'field_prompt', form)

    height_spin = QSpinBox()
    height_spin.setRange(8, 500)
    height_spin.setValue(element.font_height)
    form.addRow("Font Height:", height_spin)

    width_spin = QSpinBox()
    width_spin.setObjectName("font_width")
    width_spin.setRange(8, 500)
    width_spin.setValue(element.font_width)
    form.addRow("Font Width:", width_spin)
    set_tip(width_spin, 'font_width', form)

    orientation_combo = QComboBox()
    orientation_combo.setObjectName("orientation")
    for label, code in ORIENTATIONS:
        orientation_combo.addItem(label, code)
    turns = [code for _label, code in ORIENTATIONS]
    orientation_combo.setCurrentIndex(turns.index(element.orientation)
                                      if element.orientation in turns else 0)
    form.addRow("Orientation:", orientation_combo)
    set_tip(orientation_combo, 'orientation', form)

    fmt = _text_format_rows(
        editor, form, element, document,
        show_text=lambda: _set_quietly(text_edit, element.text))

    variable_check = QCheckBox("Data comes from a numbered field (^FN)")
    variable_check.setObjectName("variable")
    variable_check.setChecked(element.field_number is not None)
    form.addRow("Variable:", variable_check)
    set_tip(variable_check, 'variable', form)

    apply_name = _name_row(form, element)

    def _apply():
        apply_name(element)
        element.text = text_edit.text()
        element.font_height = height_spin.value()
        element.font_width = width_spin.value()
        element.orientation = orientation_combo.currentData()
        element.height = element.font_height
        fmt.apply()
        if variable_check.isChecked():
            element.field_number = number.value()
            element.field_prompt = prompt.text() or None
        else:
            element.field_number = None
            element.field_prompt = None
        # Last, because the box is measured from what the canvas will draw,
        # and that is the placeholder for as long as this stays a numbered
        # field with no literal of its own.
        document.sync_text_width(element)

    return editor.finish(_apply)


def edit_numbered_dialog(parent, element: TextElement, document: Document,
                         on_accept=None) -> QDialog:
    """numbered_form in a window of its own, with OK and Cancel.
    `on_accept` runs once OK has changed the element."""
    return _editor_dialog(parent, numbered_form(element, document), on_accept)


def frame_form(element) -> EditorForm:
    """The rows that edit a frame."""
    editor = EditorForm("Edit Frame")
    form = editor.rows

    width_spin = QSpinBox()
    width_spin.setRange(10, 800)
    width_spin.setValue(element.width)
    form.addRow("Width:", width_spin)

    height_spin = QSpinBox()
    height_spin.setRange(10, 1200)
    height_spin.setValue(element.height)
    form.addRow("Height:", height_spin)

    thickness_spin = QSpinBox()
    thickness_spin.setObjectName("thickness")
    form.addRow("Thickness:", thickness_spin)
    set_tip(thickness_spin, 'thickness', form)

    def max_thickness():
        # ^GB has no fixed limit; the useful maximum is half the smaller side,
        # where the border meets in the middle and the frame fills solid.
        return max(1, min(width_spin.value(), height_spin.value()) // 2)

    def sync_thickness_range():
        # Shrinking the frame must not leave an illegal thickness selectable.
        thickness_spin.setRange(1, max_thickness())

    sync_thickness_range()
    thickness_spin.setValue(min(element.thickness, max_thickness()))
    width_spin.valueChanged.connect(sync_thickness_range)
    height_spin.valueChanged.connect(sync_thickness_range)

    # ^GB's colour and corner rounding
    colour_combo = QComboBox()
    colour_combo.setObjectName("colour")
    for label, code in FRAME_COLOURS:
        colour_combo.addItem(label, code)
    codes = [code for _label, code in FRAME_COLOURS]
    colour_combo.setCurrentIndex(codes.index(element.colour)
                                 if element.colour in codes else 0)
    form.addRow("Colour:", colour_combo)
    set_tip(colour_combo, 'colour', form)

    rounding_spin = QSpinBox()
    rounding_spin.setObjectName("rounding")
    rounding_spin.setRange(0, FrameElement.MAX_ROUNDING)
    rounding_spin.setValue(element.rounding)
    form.addRow("Corner Rounding:", rounding_spin)
    set_tip(rounding_spin, 'rounding', form)

    fr_check = QCheckBox("Reverse print (^FR)")
    fr_check.setObjectName("reverse_print")
    fr_check.setChecked(element.reverse_print)
    form.addRow("Reverse:", fr_check)
    set_tip(fr_check, 'reverse_print', form)

    def _apply():
        element.width = width_spin.value()
        element.height = height_spin.value()
        element.thickness = thickness_spin.value()
        element.colour = colour_combo.currentData()
        element.rounding = rounding_spin.value()
        element.reverse_print = fr_check.isChecked()

    return editor.finish(_apply)


def edit_frame_dialog(parent, element, on_accept=None) -> QDialog:
    """frame_form in a window of its own, with OK and Cancel.
    `on_accept` runs once OK has changed the element."""
    return _editor_dialog(parent, frame_form(element), on_accept)


def circle_form(element) -> EditorForm:
    """The rows that edit a ^GC circle."""
    editor = EditorForm("Edit Circle")
    form = editor.rows

    # ^GC's own range, so a small circle from a file is not enlarged by
    # accepting the editor it was only looked at in
    diameter_spin = QSpinBox()
    diameter_spin.setObjectName("diameter")
    diameter_spin.setRange(CircleElement.MIN_DIAMETER, CircleElement.MAX_DIAMETER)
    diameter_spin.setValue(element.diameter)
    form.addRow("Diameter:", diameter_spin)

    thickness_spin = QSpinBox()
    thickness_spin.setObjectName("thickness")
    form.addRow("Thickness:", thickness_spin)
    set_tip(thickness_spin, 'thickness', form)

    def max_thickness():
        # The border meets in the middle at the radius, and fills solid.
        return max(1, diameter_spin.value() // 2)

    def sync_thickness_range():
        # Shrinking the circle must not leave an illegal thickness selectable.
        thickness_spin.setRange(1, max_thickness())

    sync_thickness_range()
    thickness_spin.setValue(min(element.thickness, max_thickness()))
    diameter_spin.valueChanged.connect(sync_thickness_range)

    # ^GC's colour: the same black or white ^GB offers
    colour_combo = QComboBox()
    colour_combo.setObjectName("colour")
    for label, code in FRAME_COLOURS:
        colour_combo.addItem(label, code)
    codes = [code for _label, code in FRAME_COLOURS]
    colour_combo.setCurrentIndex(codes.index(element.colour)
                                 if element.colour in codes else 0)
    form.addRow("Colour:", colour_combo)
    set_tip(colour_combo, 'colour', form)

    fr_check = QCheckBox("Reverse print (^FR)")
    fr_check.setObjectName("reverse_print")
    fr_check.setChecked(element.reverse_print)
    form.addRow("Reverse:", fr_check)
    set_tip(fr_check, 'reverse_print', form)

    def _apply():
        element.diameter = diameter_spin.value()
        element.sync_box()
        element.thickness = thickness_spin.value()
        element.colour = colour_combo.currentData()
        element.reverse_print = fr_check.isChecked()

    return editor.finish(_apply)


def edit_circle_dialog(parent, element, on_accept=None) -> QDialog:
    """circle_form in a window of its own, with OK and Cancel.
    `on_accept` runs once OK has changed the element."""
    return _editor_dialog(parent, circle_form(element), on_accept)


def ellipse_form(element) -> EditorForm:
    """The rows that edit a ^GE ellipse."""
    editor = EditorForm("Edit Ellipse")
    form = editor.rows

    # ^GE's own range, so an ellipse from a file is not cut down, or a small
    # one enlarged, by accepting the editor it was only looked at in
    width_spin = QSpinBox()
    width_spin.setObjectName("width")
    width_spin.setRange(EllipseElement.MIN_SIDE, EllipseElement.MAX_SIDE)
    width_spin.setValue(element.width)
    form.addRow("Width:", width_spin)

    height_spin = QSpinBox()
    height_spin.setObjectName("height")
    height_spin.setRange(EllipseElement.MIN_SIDE, EllipseElement.MAX_SIDE)
    height_spin.setValue(element.height)
    form.addRow("Height:", height_spin)

    thickness_spin = QSpinBox()
    thickness_spin.setObjectName("thickness")
    form.addRow("Thickness:", thickness_spin)
    set_tip(thickness_spin, 'thickness', form)

    def max_thickness():
        # The border meets in the middle at half the shorter side, and the
        # ellipse fills solid.
        return max(1, min(width_spin.value(), height_spin.value()) // 2)

    def sync_thickness_range():
        # Shrinking the ellipse must not leave an illegal thickness selectable.
        thickness_spin.setRange(1, max_thickness())

    sync_thickness_range()
    thickness_spin.setValue(min(element.thickness, max_thickness()))
    width_spin.valueChanged.connect(sync_thickness_range)
    height_spin.valueChanged.connect(sync_thickness_range)

    # ^GE's colour: the same black or white ^GB offers
    colour_combo = QComboBox()
    colour_combo.setObjectName("colour")
    for label, code in FRAME_COLOURS:
        colour_combo.addItem(label, code)
    codes = [code for _label, code in FRAME_COLOURS]
    colour_combo.setCurrentIndex(codes.index(element.colour)
                                 if element.colour in codes else 0)
    form.addRow("Colour:", colour_combo)
    set_tip(colour_combo, 'colour', form)

    fr_check = QCheckBox("Reverse print (^FR)")
    fr_check.setObjectName("reverse_print")
    fr_check.setChecked(element.reverse_print)
    form.addRow("Reverse:", fr_check)
    set_tip(fr_check, 'reverse_print', form)

    def _apply():
        element.width = width_spin.value()
        element.height = height_spin.value()
        element.thickness = thickness_spin.value()
        element.colour = colour_combo.currentData()
        element.reverse_print = fr_check.isChecked()

    return editor.finish(_apply)


def edit_ellipse_dialog(parent, element, on_accept=None) -> QDialog:
    """ellipse_form in a window of its own, with OK and Cancel.
    `on_accept` runs once OK has changed the element."""
    return _editor_dialog(parent, ellipse_form(element), on_accept)


def diagonal_form(element) -> EditorForm:
    """The rows that edit a ^GD diagonal line."""
    editor = EditorForm("Edit Diagonal Line")
    form = editor.rows

    # ^GD's own range, so a line from a file is not cut down, or a short one
    # enlarged, by accepting the editor it was only looked at in
    width_spin = QSpinBox()
    width_spin.setObjectName("width")
    width_spin.setRange(DiagonalLineElement.MIN_SIDE, DiagonalLineElement.MAX_SIDE)
    width_spin.setValue(element.width)
    form.addRow("Width:", width_spin)

    height_spin = QSpinBox()
    height_spin.setObjectName("height")
    height_spin.setRange(DiagonalLineElement.MIN_SIDE, DiagonalLineElement.MAX_SIDE)
    height_spin.setValue(element.height)
    form.addRow("Height:", height_spin)

    thickness_spin = QSpinBox()
    thickness_spin.setObjectName("thickness")
    form.addRow("Thickness:", thickness_spin)
    set_tip(thickness_spin, 'thickness', form)

    def sync_thickness_range():
        # Each row's run meets the far side at the full width, and the box
        # fills solid; narrowing it must not leave a thicker one selectable.
        thickness_spin.setRange(1, max(1, width_spin.value()))

    sync_thickness_range()
    thickness_spin.setValue(min(element.thickness, element.max_thickness()))
    width_spin.valueChanged.connect(sync_thickness_range)

    # ^GD's colour: the same black or white ^GB offers
    colour_combo = QComboBox()
    colour_combo.setObjectName("colour")
    for label, code in FRAME_COLOURS:
        colour_combo.addItem(label, code)
    codes = [code for _label, code in FRAME_COLOURS]
    colour_combo.setCurrentIndex(codes.index(element.colour)
                                 if element.colour in codes else 0)
    form.addRow("Colour:", colour_combo)
    set_tip(colour_combo, 'colour', form)

    direction_combo = QComboBox()
    direction_combo.setObjectName("direction")
    for label, code in DIAGONAL_DIRECTIONS:
        direction_combo.addItem(label, code)
    directions = [code for _label, code in DIAGONAL_DIRECTIONS]
    direction_combo.setCurrentIndex(directions.index(element.direction)
                                    if element.direction in directions else 0)
    form.addRow("Direction:", direction_combo)

    fr_check = QCheckBox("Reverse print (^FR)")
    fr_check.setObjectName("reverse_print")
    fr_check.setChecked(element.reverse_print)
    form.addRow("Reverse:", fr_check)
    set_tip(fr_check, 'reverse_print', form)

    def _apply():
        element.width = width_spin.value()
        element.height = height_spin.value()
        element.thickness = thickness_spin.value()
        element.colour = colour_combo.currentData()
        element.direction = direction_combo.currentData()
        element.reverse_print = fr_check.isChecked()

    return editor.finish(_apply)


def edit_diagonal_dialog(parent, element, on_accept=None) -> QDialog:
    """diagonal_form in a window of its own, with OK and Cancel.
    `on_accept` runs once OK has changed the element."""
    return _editor_dialog(parent, diagonal_form(element), on_accept)


def graphic_symbol_form(element) -> EditorForm:
    """The rows that edit a ^GS symbol."""
    editor = EditorForm("Edit Symbol")
    form = editor.rows

    # The five, and whatever else the field already holds - see
    # graphic_symbol_choices - so OK on ^GS^FDAB leaves it AB.
    choices = graphic_symbol_choices(element.text)
    symbol_combo = QComboBox()
    symbol_combo.setObjectName("symbol")
    for label, code in choices:
        symbol_combo.addItem(label, code)
    codes = [code for _label, code in choices]
    symbol_combo.setCurrentIndex(codes.index(element.text)
                                 if element.text in codes else 0)
    form.addRow("Symbol:", symbol_combo)

    # ^GS's own range, so a symbol from a file is neither cut down nor
    # enlarged by accepting the editor it was only looked at in
    height_spin = QSpinBox()
    height_spin.setObjectName("font_height")
    height_spin.setRange(GraphicSymbolElement.MIN_SIZE, GraphicSymbolElement.MAX_SIZE)
    height_spin.setValue(element.font_height)
    form.addRow("Height:", height_spin)

    width_spin = QSpinBox()
    width_spin.setObjectName("font_width")
    width_spin.setRange(GraphicSymbolElement.MIN_SIZE, GraphicSymbolElement.MAX_SIZE)
    width_spin.setValue(element.font_width)
    form.addRow("Width:", width_spin)

    orientation_combo = QComboBox()
    orientation_combo.setObjectName("orientation")
    for label, code in ORIENTATIONS:
        orientation_combo.addItem(label, code)
    turns = [code for _label, code in ORIENTATIONS]
    orientation_combo.setCurrentIndex(turns.index(element.orientation)
                                      if element.orientation in turns else 0)
    form.addRow("Orientation:", orientation_combo)
    set_tip(orientation_combo, 'orientation', form)

    fr_check = QCheckBox("Reverse print (^FR)")
    fr_check.setObjectName("reverse_print")
    fr_check.setChecked(element.reverse_print)
    form.addRow("Reverse:", fr_check)
    set_tip(fr_check, 'reverse_print', form)

    apply_name = _name_row(form, element)

    def _apply():
        apply_name(element)
        element.text = symbol_combo.currentData()
        element.font_height = height_spin.value()
        element.font_width = width_spin.value()
        element.orientation = orientation_combo.currentData()
        element.reverse_print = fr_check.isChecked()
        element.sync_box()

    return editor.finish(_apply)


def edit_graphic_symbol_dialog(parent, element, on_accept=None) -> QDialog:
    """graphic_symbol_form in a window of its own, with OK and Cancel.
    `on_accept` runs once OK has changed the element."""
    return _editor_dialog(parent, graphic_symbol_form(element), on_accept)


def stored_graphic_form(element) -> EditorForm:
    """The rows that edit a ^XG/^IM reference.

    ^XG/^IM name an image the printer holds, not one this file carries the
    bytes for - see zplcore/graphic_store.py. Editing this element only ever
    changes which name it recalls.
    """
    editor = EditorForm("Edit Stored Graphic")
    form = editor.rows

    command_combo = QComboBox()
    command_combo.setObjectName("command")
    for label, code in STORED_GRAPHIC_COMMANDS:
        command_combo.addItem(label, code)
    command_codes = [code for _label, code in STORED_GRAPHIC_COMMANDS]
    command_combo.setCurrentIndex(command_codes.index(element.command)
                                  if element.command in command_codes else 0)
    form.addRow("Command:", command_combo)
    set_tip(command_combo, 'command', form)

    device, name, ext = graphic_store.split_device_spec(element.device_spec)
    device_combo = QComboBox()
    device_combo.setObjectName("device")
    for label, code in STORED_GRAPHIC_DEVICES:
        device_combo.addItem(label, code)
    device_codes = [code for _label, code in STORED_GRAPHIC_DEVICES]
    device_combo.setCurrentIndex(device_codes.index(device)
                                 if device in device_codes else 0)
    form.addRow("Device:", device_combo)
    set_tip(device_combo, 'device', form)

    name_edit = QLineEdit(name)
    name_edit.setObjectName("name")
    name_edit.setMaxLength(8)
    form.addRow("Name:", name_edit)
    set_tip(name_edit, 'name', form)

    ext_edit = QLineEdit(ext)
    ext_edit.setObjectName("extension")
    form.addRow("Extension:", ext_edit)
    set_tip(ext_edit, 'extension', form)

    mag_x_spin = QSpinBox()
    mag_x_spin.setObjectName("mag_x")
    mag_x_spin.setRange(1, 10)
    mag_x_spin.setValue(element.mag_x)
    form.addRow("Magnification X:", mag_x_spin)
    set_tip(mag_x_spin, 'mag_x', form)

    mag_y_spin = QSpinBox()
    mag_y_spin.setObjectName("mag_y")
    mag_y_spin.setRange(1, 10)
    mag_y_spin.setValue(element.mag_y)
    form.addRow("Magnification Y:", mag_y_spin)
    set_tip(mag_y_spin, 'mag_y', form)

    def sync_magnification_enabled():
        # ^IM has no magnification of its own - always 1,1.
        is_xg = command_combo.currentData() == 'XG'
        mag_x_spin.setEnabled(is_xg)
        mag_y_spin.setEnabled(is_xg)

    sync_magnification_enabled()
    command_combo.currentIndexChanged.connect(sync_magnification_enabled)

    def _apply():
        element.command = command_combo.currentData()
        object_name = (name_edit.text().strip() or 'UNKNOWN').upper()
        extension = (ext_edit.text().strip() or 'GRF').upper()
        element.device_spec = f"{device_combo.currentData()}:{object_name}.{extension}"
        if element.command == 'XG':
            element.mag_x = mag_x_spin.value()
            element.mag_y = mag_y_spin.value()
        else:
            element.mag_x = element.mag_y = 1

    return editor.finish(_apply)


def edit_stored_graphic_dialog(parent, element, on_accept=None) -> QDialog:
    """stored_graphic_form in a window of its own, with OK and Cancel.
    `on_accept` runs once OK has changed the element."""
    return _editor_dialog(parent, stored_graphic_form(element), on_accept)


def barcode_form(element) -> EditorForm:
    """The rows that edit a barcode."""
    editor = EditorForm("Edit Barcode")
    form = editor.rows

    symbology_combo = QComboBox()
    symbology_combo.setObjectName("symbology")
    for label, code in BARCODE_SYMBOLOGIES:
        symbology_combo.addItem(label, code)
    symbology_combo.setCurrentIndex(
        [c for _l, c in BARCODE_SYMBOLOGIES].index(element.symbology))
    form.addRow("Symbology:", symbology_combo)

    value_edit = QLineEdit(element.barcode_value)
    value_edit.setObjectName("value")
    form.addRow("Barcode Value:", value_edit)

    # The control characters a MaxiCode's message is built out of, which no
    # keyboard types. Each goes in at the cursor as a ^FH escape, switching
    # ^FH on if the field had none - held here until OK, like every other
    # row, so Cancel leaves the element as it was.
    pending = {'indicator': element.hex_indicator}
    control_row = QWidget()
    control_layout = QHBoxLayout(control_row)
    control_layout.setContentsMargins(0, 0, 0, 0)

    def _insert(code):
        text, position, pending['indicator'] = zpl_fields.insert_escape(
            value_edit.text(), value_edit.cursorPosition(), code,
            pending['indicator'])
        value_edit.setText(text)
        value_edit.setCursorPosition(position)
        value_edit.setFocus()

    for label, code in BARCODE_CONTROL_CHARACTERS:
        button = QPushButton(label)
        button.setObjectName(f"insert_{label}")
        set_tip(button, f"insert_{label}")
        button.setAutoDefault(False)
        button.clicked.connect(lambda _checked=False, code=code: _insert(code))
        control_layout.addWidget(button)
    control_layout.addStretch(1)
    control_row.setObjectName("insert")
    form.addRow("Insert:", control_row)
    set_tip(control_row, 'insert', form)
    control_label = form.labelForField(control_row)

    height_spin = QSpinBox()
    # Wide until the symbology's own range is known (_update_visible_rows),
    # so a height below 20 - a PDF417 or MicroPDF417 row - is not clamped to
    # 20 before that range can allow it, and written back on OK.
    height_spin.setRange(1, 32000)
    height_spin.setValue(element.bar_height)
    height_spin.setObjectName("bar_height")
    form.addRow("Bar Height:", height_spin)
    set_tip(height_spin, 'bar_height', form)
    height_label = form.labelForField(height_spin)

    module_spin = QSpinBox()
    module_spin.setRange(1, 20)
    module_spin.setValue(element.module_width)
    module_spin.setObjectName("module_width")
    form.addRow("Module Width:", module_spin)
    set_tip(module_spin, 'module_width', form)
    module_label = form.labelForField(module_spin)

    # The rows a symbology adds for its own parameters - a QR code's error
    # correction and mask, and so on. Built from the same catalogue the
    # parser and the model read, so a symbology cannot arrive with a
    # parameter no editor can reach. They go here, after the value, so the
    # rows above keep the positions other code looks for them in.
    #
    # One row per attribute, however many symbologies have it: a Data
    # Matrix and a PDF417 both have columns and rows. The label and the
    # choices are each symbology's own, so _load_extra_rows puts the chosen
    # one's in; a row left showing another symbology's choices would write
    # one of them back on the next edit.
    extra_rows = {}
    for symbology_key, rows in BARCODE_PARAMETERS.items():
        for attribute, label, choices in rows:
            if attribute in extra_rows:
                continue
            widget = (QSpinBox() if isinstance(choices, BarcodeSpin)
                      else QComboBox())
            widget.setObjectName(attribute)
            form.addRow(label + ":", widget)
            # [widget, its label, the choices it now offers]
            extra_rows[attribute] = [widget, form.labelForField(widget), None]

    def _extra_value(attribute):
        widget = extra_rows[attribute][0]
        return (widget.value() if isinstance(widget, QSpinBox)
                else widget.currentData())

    def _load_extra_rows():
        """Show each row the chosen symbology has, labelled and offering
        that symbology's choices, set to the element's value."""
        own = dict((attribute, (label, choices)) for attribute, label, choices
                   in BARCODE_PARAMETERS.get(symbology_combo.currentData(), ()))
        for attribute, row in extra_rows.items():
            widget, label = row[0], row[1]
            shown = attribute in own
            widget.setVisible(shown)
            label.setVisible(shown)
            if not shown:
                continue
            text, choices = own[attribute]
            label.setText(text + ":")
            set_tip_text(widget, zpl_tips.PARAMETERS[
                (symbology_combo.currentData(), attribute)], form)
            current = getattr(element, attribute, None)
            # Filling a row in is not an edit of it
            widget.blockSignals(True)
            try:
                if isinstance(widget, QSpinBox):
                    widget.setRange(choices.lower, choices.upper)
                    widget.setValue(current if current is not None
                                    else choices.lower)
                else:
                    if row[2] is not choices:
                        widget.clear()
                        for choice_label, value in choices:
                            widget.addItem(choice_label, value)
                    values = [value for _l, value in choices]
                    widget.setCurrentIndex(values.index(current)
                                           if current in values else 0)
            finally:
                widget.blockSignals(False)
            row[2] = choices

    ratio_spin = QDoubleSpinBox()
    ratio_spin.setRange(2.0, 3.0)
    ratio_spin.setDecimals(1)
    ratio_spin.setSingleStep(0.1)
    ratio_spin.setValue(element.ratio)
    ratio_spin.setObjectName("ratio")
    form.addRow("Ratio:", ratio_spin)
    set_tip(ratio_spin, 'ratio', form)

    orientation_combo = QComboBox()
    orientation_combo.setObjectName("orientation")
    for label, code in BARCODE_ORIENTATIONS:
        orientation_combo.addItem(label, code)
    current = (element.orientation or 'N').upper()
    orientation_combo.setCurrentIndex(
        max(0, [c for _l, c in BARCODE_ORIENTATIONS].index(current)
            if current in [c for _l, c in BARCODE_ORIENTATIONS] else 0))
    form.addRow("Orientation:", orientation_combo)
    set_tip(orientation_combo, 'orientation', form)

    text_combo = QComboBox()
    for label, flags in BARCODE_TEXT_CHOICES:
        text_combo.addItem(label, flags)
    text_combo.setCurrentIndex(
        [flags for _l, flags in BARCODE_TEXT_CHOICES].index(
            (element.show_text, element.text_above))
        if (element.show_text, element.text_above)
        in [flags for _l, flags in BARCODE_TEXT_CHOICES] else 0)
    text_combo.setObjectName("value_text")
    form.addRow("Value Text:", text_combo)
    set_tip(text_combo, 'value_text', form)

    font_spin = QSpinBox()
    font_spin.setRange(6, 200)
    font_spin.setValue(int((element.font or element.DEFAULT_FONT)[1]))
    form.addRow("Text Height:", font_spin)

    check_combo = QComboBox()
    for label, flag in BARCODE_CHECK_DIGIT:
        check_combo.addItem(label, flag)
    check_combo.setCurrentIndex(1 if element.check_digit else 0)
    check_combo.setObjectName("check_digit")
    form.addRow("Check Digit:", check_combo)
    check_label = form.labelForField(check_combo)

    mode_combo = QComboBox()
    for label, code in BARCODE_MODES:
        mode_combo.addItem(label, code)
    mode_combo.setCurrentIndex(
        [c for _l, c in BARCODE_MODES].index(element.mode)
        if element.mode in [c for _l, c in BARCODE_MODES] else 0)
    mode_combo.setObjectName("mode")
    form.addRow("Mode:", mode_combo)
    set_tip(mode_combo, 'mode', form)
    mode_label = form.labelForField(mode_combo)
    ratio_label = form.labelForField(ratio_spin)

    fr_check = QCheckBox("Reverse print (^FR)")
    fr_check.setObjectName("reverse_print")
    fr_check.setChecked(element.reverse_print)
    form.addRow("Reverse:", fr_check)
    set_tip(fr_check, 'reverse_print', form)

    # ^FM: an origin for each symbol of a series, for the two symbologies it
    # places, as label positions. No rows is a barcode placed by ^FO like any
    # other; an origin not ticked is one ^FM excludes.
    positions = QTableWidget(0, 3)
    positions.setObjectName("positions")
    positions.setHorizontalHeaderLabels(["X", "Y", "Print"])
    positions.verticalHeader().setVisible(False)
    positions.setSelectionBehavior(QAbstractItemView.SelectRows)

    def _add_position(x, y, prints):
        row = positions.rowCount()
        positions.insertRow(row)
        for column, value in ((0, x), (1, y)):
            spin = QSpinBox()
            spin.setRange(0, 32000)
            spin.setValue(value)
            positions.setCellWidget(row, column, spin)
        check = QCheckBox()
        check.setChecked(prints)
        positions.setCellWidget(row, 2, check)

    def _position_rows():
        return [(positions.cellWidget(row, 0).value(),
                 positions.cellWidget(row, 1).value(),
                 positions.cellWidget(row, 2).isChecked())
                for row in range(positions.rowCount())]

    for x, y, prints in element.series_positions():
        _add_position(x, y, prints)

    def _on_add_position():
        if positions.rowCount() < MAX_SERIES_ORIGINS:
            _add_position(*element.next_series_position(_position_rows()), True)
            row = positions.rowCount() - 1
            for column in range(positions.columnCount()):
                editor.watch(positions.cellWidget(row, column))
            editor.changed()

    def _on_remove_position():
        row = positions.currentRow()
        positions.removeRow(row if row >= 0 else positions.rowCount() - 1)
        editor.changed()

    positions_box = QWidget()
    positions_layout = QVBoxLayout(positions_box)
    positions_layout.setContentsMargins(0, 0, 0, 0)
    positions_layout.addWidget(positions)
    positions_buttons = QHBoxLayout()
    for label, name, slot in (("Add", "add_position", _on_add_position),
                              ("Remove", "remove_position",
                               _on_remove_position)):
        button = QPushButton(label)
        button.setObjectName(name)
        button.setAutoDefault(False)
        button.clicked.connect(slot)
        positions_buttons.addWidget(button)
    positions_buttons.addStretch(1)
    positions_layout.addLayout(positions_buttons)
    positions_box.setObjectName("positions")
    form.addRow("Positions (^FM):", positions_box)
    set_tip(positions_box, 'positions', form)
    positions_label = form.labelForField(positions_box)

    apply_field_number = _field_number_rows(form, element)
    apply_name = _name_row(form, element)

    def _update_visible_rows():
        # Each symbology carries a different subset of these rows - Code
        # 128's mode, a check digit only some of them have (and call
        # something different), a ratio that only matters for the two not
        # drawn at a fixed one. Showing every row for every symbology would
        # offer a Mode a Code 39 barcode has no ZPL parameter for at all.
        features = BARCODE_FEATURES[symbology_combo.currentData()]
        for widget in (mode_combo, mode_label):
            widget.setVisible(features['mode'])
        for widget in (ratio_spin, ratio_label):
            widget.setVisible(features['ratio'])
        check_visible = features['check_digit'] is not None
        for widget in (check_combo, check_label):
            widget.setVisible(check_visible)
        if check_visible:
            check_label.setText(features['check_digit'] + ":")
            # Three different checksums, or a choice of showing one, under
            # one row: the tip goes with the label
            set_tip_text(check_combo,
                         zpl_tips.CHECK_DIGIT[features['check_digit']], form)
        # A matrix symbology's size is its own grid, so it has no bar height
        # to offer and its module width is a magnification of that grid.
        for widget in (height_spin, height_label):
            widget.setVisible(features['height'] is not None)
        if features['height'] is not None:
            height_spin.setRange(*features['height'])
        for widget in (module_spin, module_label):
            widget.setVisible(features['module_width'] is not None)
        if features['module_width'] is not None:
            module_label.setText(features['module_width'] + ":")
        # ...and no interpretation line, so neither the line nor its font.
        for widget in (text_combo, form.labelForField(text_combo),
                       font_spin, form.labelForField(font_spin)):
            widget.setVisible(bool(features['text']))
        # A MaxiCode has no orientation to set, and a message built out of
        # characters only the Insert buttons can write.
        for widget in (orientation_combo,
                       form.labelForField(orientation_combo)):
            widget.setVisible(features['orientation'])
        for widget in (control_row, control_label):
            widget.setVisible(features['control_chars'])
        for widget in (positions_box, positions_label):
            widget.setVisible(symbology_combo.currentData() in BARCODE_SERIES)
        _load_extra_rows()

    symbology_combo.currentIndexChanged.connect(_update_visible_rows)
    _update_visible_rows()

    def _apply():
        element.symbology = symbology_combo.currentData()
        element.barcode_value = value_edit.text()
        element.hex_indicator = pending['indicator']
        element.bar_height = height_spin.value()
        element.module_width = module_spin.value()
        element.ratio = ratio_spin.value()
        element.orientation = orientation_combo.currentData()
        element.show_text, element.text_above = text_combo.currentData()
        # Which parameters to write comes from the catalogue, not from
        # whether the row is on screen: a dialog that was never shown has no
        # visible widgets at all, so asking the widget silently dropped every
        # change when the editor was driven rather than clicked.
        for attribute, _label, _choices in BARCODE_PARAMETERS.get(
                element.symbology, ()):
            setattr(element, attribute, _extra_value(attribute))
        element.check_digit = check_combo.currentData()
        element.mode = mode_combo.currentData()
        element.reverse_print = fr_check.isChecked()
        apply_field_number(element)
        apply_name(element)
        element.set_series_positions(_position_rows()
                                     if element.symbology in BARCODE_SERIES
                                     else [])
        if element.show_text:
            # With the line switched on, name the font it prints in rather than
            # leaving it to whatever the printer happens to have selected.
            code = element.font[0] if element.font else element.DEFAULT_FONT[0]
            element.font = (code, font_spin.value(), font_spin.value())
        element.sync_box()

    return editor.finish(_apply)


def edit_barcode_dialog(parent, element, on_accept=None) -> QDialog:
    """barcode_form in a window of its own, with OK and Cancel.
    `on_accept` runs once OK has changed the element."""
    return _editor_dialog(parent, barcode_form(element), on_accept)


def form_builder(element):
    """The form function that edits this element, or None for one with no
    form - an image, whose one setting is which file it shows.

    A text element's form follows what feeds it - the clock, a serial number,
    a numbered field, or nothing - so unticking the clock in its form changes
    which form it needs.
    """
    if isinstance(element, TextElement):
        if element.clock_format:
            return time_form
        if element.serial_increment is not None:
            return serial_form
        if element.field_number is not None:
            return numbered_form
        return text_form
    for kind, builder in ((FrameElement, frame_form), (CircleElement, circle_form),
                          (EllipseElement, ellipse_form),
                          (DiagonalLineElement, diagonal_form),
                          (GraphicSymbolElement, graphic_symbol_form),
                          (StoredGraphicElement, stored_graphic_form),
                          (BarcodeElement, barcode_form)):
        if isinstance(element, kind):
            return builder
    return None


def editor_form(element, document: Document) -> Optional[EditorForm]:
    """The form that edits this element, built, or None if it has none."""
    builder = form_builder(element)
    if builder is None:
        return None
    if builder in (text_form, time_form, serial_form, numbered_form):
        return builder(element, document)
    return builder(element)


def choose_image_file(parent, title="Select Image") -> Optional[str]:
    path, _ = QFileDialog.getOpenFileName(parent, title, "", IMAGE_FILTER)
    return path or None


def store_graphic_dialog(parent) -> Optional[str]:
    """Ask for device / name / extension for a graphic about to be stored.

    The same three fields `edit_stored_graphic_dialog` asks for, minus
    command and magnification - those describe an ^XG/^IM reference, not the
    object being stored. Modal, unlike the element editors: there is no live
    element on the canvas for a non-modal dialog to stay in sync with here.
    Returns the `d:o.x` spec, or None if cancelled.
    """
    dialog = QDialog(parent)
    dialog.setWindowTitle("Store Graphic As")
    layout = QVBoxLayout(dialog)
    form = QFormLayout()
    layout.addLayout(form)

    device_combo = QComboBox()
    device_combo.setObjectName("device")
    for label, code in STORED_GRAPHIC_DEVICES:
        device_combo.addItem(label, code)
    form.addRow("Device:", device_combo)
    set_tip(device_combo, 'device', form)

    name_edit = QLineEdit("LOGO")
    name_edit.setMaxLength(8)
    form.addRow("Name:", name_edit)

    ext_edit = QLineEdit("GRF")
    form.addRow("Extension:", ext_edit)

    buttons = _buttons(dialog)
    add_help(dialog, "store_graphic_as", buttons)
    layout.addWidget(buttons)

    if dialog.exec_() != QDialog.Accepted:
        return None
    object_name = (name_edit.text().strip() or 'UNKNOWN').upper()
    extension = (ext_edit.text().strip() or 'GRF').upper()
    return f"{device_combo.currentData()}:{object_name}.{extension}"


def font_device_dialog(parent, default_device: str) -> Optional[str]:
    """Which memory to upload a font to. None if cancelled.

    Asked after the family, not before, so the question a user came to answer
    comes first. Opens on the Font memory printer setting, since that is
    where the designer's own ^A@ will point - picking another here is a
    manager's freedom, the same one Store Graphic has, and puts the font
    somewhere this label will not name on its own.
    """
    dialog = QDialog(parent)
    dialog.setWindowTitle("Upload Font To")
    layout = QVBoxLayout(dialog)
    form = QFormLayout()
    layout.addLayout(form)

    combo = _font_device_combo(default_device)
    form.addRow("Memory:", combo)
    set_tip(combo, 'font_device', form)

    buttons = _buttons(dialog)
    add_help(dialog, "upload_font_to", buttons)
    layout.addWidget(buttons)
    if dialog.exec_() != QDialog.Accepted:
        return None
    return combo.currentData()


def store_object_dialog(parent, default_name: str, default_ext: str):
    """Ask for the name and extension an arbitrary local file should be
    stored under on the printer's E: drive - the only device
    printer_objects.upload_printer_object can target, so unlike
    store_graphic_dialog this asks for no device. Case is left exactly as
    typed rather than forced to upper, unlike store_graphic_dialog: a
    CISDFCRC16-stored object is not necessarily an upper-case 8.3 ZPL
    object (the manual's own examples include privkey.nrd, feedback.get),
    and file.type's retrieval is case sensitive - see zplcore.printer_objects.
    Returns (name, ext), or None if cancelled.
    """
    dialog = QDialog(parent)
    dialog.setWindowTitle("Store Object As")
    layout = QVBoxLayout(dialog)
    form = QFormLayout()
    layout.addLayout(form)

    name_edit = QLineEdit(default_name)
    name_edit.setObjectName("object_name")
    name_edit.setMaxLength(8)
    form.addRow("Name (on E:):", name_edit)
    set_tip(name_edit, 'object_name', form)

    ext_edit = QLineEdit(default_ext)
    form.addRow("Extension:", ext_edit)

    buttons = _buttons(dialog)
    add_help(dialog, "store_object_as", buttons)
    layout.addWidget(buttons)

    if dialog.exec_() != QDialog.Accepted:
        return None
    name = name_edit.text().strip() or 'UNKNOWN'
    ext = ext_edit.text().strip() or 'DAT'
    return name, ext


# --- label and printer ------------------------------------------------------

def label_size_dialog(parent, document: Document, dpi: int):
    """New (width, height, dpi, width_in, height_in, transform), or None.

    The transform is the document's with the home typed here; ^PO, ^PM and
    ^LR, which it also holds, are the Print window's, and pass through.

    The size is entered in inches and stored in dots, so the resolution belongs
    beside it: the dots are a consequence of both, and having to leave for
    another dialog to change one of the two halves is how a label ends up the
    wrong physical size. The inches come back as well as the dots - they are
    what gets remembered for the next new label, and dividing the dots back out
    would not give the two decimals that were typed.
    """
    dialog = QDialog(parent)
    dialog.setWindowTitle("Label Settings")
    layout = QVBoxLayout(dialog)

    layout.addWidget(QLabel("Preset Sizes:"))
    presets = QHBoxLayout()
    layout.addLayout(presets)

    layout.addWidget(QLabel("Custom Size (inches):"))
    form = QFormLayout()
    layout.addLayout(form)

    width_spin = QDoubleSpinBox()
    width_spin.setRange(0.5, 25.0)
    width_spin.setDecimals(2)
    width_spin.setSingleStep(0.1)
    width_spin.setValue(document.label_width / dpi)
    form.addRow("Width:", width_spin)

    height_spin = QDoubleSpinBox()
    height_spin.setRange(0.5, 25.0)
    height_spin.setDecimals(2)
    height_spin.setSingleStep(0.1)
    height_spin.setValue(document.label_height / dpi)
    form.addRow("Height:", height_spin)

    dpi_combo = _dpi_combo(dpi)
    form.addRow("DPI:", dpi_combo)
    set_tip(dpi_combo, 'dpi', form)

    # ^LH: the origin every field is placed from. Its use is preprinted stock -
    # moving the printable area below a pre-printed header - so it belongs
    # beside the size rather than among the printer settings.
    home_x = QSpinBox()
    home_x.setObjectName("home_x")
    home_x.setRange(0, 32000)
    home_x.setValue(document.transform.home[0])
    form.addRow("Home X (dots):", home_x)
    set_tip(home_x, 'home_x', form)

    home_y = QSpinBox()
    home_y.setObjectName("home_y")
    home_y.setRange(0, 32000)
    home_y.setValue(document.transform.home[1])
    form.addRow("Home Y (dots):", home_y)
    set_tip(home_y, 'home_y', form)

    for text, w_in, h_in in PRESET_SIZES:
        btn = QPushButton(text)
        btn.clicked.connect(
            lambda _checked=False, w=w_in, h=h_in: (width_spin.setValue(w),
                                                    height_spin.setValue(h)))
        presets.addWidget(btn)

    hint = QLabel()
    hint.setWordWrap(True)
    layout.addWidget(hint)

    def chosen_dpi():
        return _dpi_from(dpi_combo, dpi)

    def to_dots():
        # The inches are the physical size the user asked for, so changing the
        # resolution recomputes the dots rather than the other way round.
        resolution = chosen_dpi()
        return (max(1, int(round(width_spin.value() * resolution))),
                max(1, int(round(height_spin.value() * resolution))))

    def update_hint():
        w, h = to_dots()
        hint.setText(f"{w} x {h} dots at {chosen_dpi()} dpi "
                     f"(^PW{w} / ^LL{h}), saved with the file.")

    width_spin.valueChanged.connect(update_hint)
    height_spin.valueChanged.connect(update_hint)
    dpi_combo.currentIndexChanged.connect(update_hint)
    update_hint()

    buttons = _buttons(dialog)
    add_help(dialog, "label_settings", buttons)
    layout.addWidget(buttons)
    if dialog.exec_() != QDialog.Accepted:
        return None
    # Copied, not mutated: the document's own transform is what an undo
    # snapshot may still be holding.
    transform = document.transform.copy()
    transform.home = (home_x.value(), home_y.value())
    return to_dots() + (chosen_dpi(), width_spin.value(), height_spin.value(),
                        transform)


def grid_settings_dialog(parent, grid, dpi: int):
    """A copy of `grid` with the spacing chosen here, or None.

    Only the spacing: the switches that use it are the View menu's. `dpi` is
    the label's, for the hint and for converting between a physical unit and
    dots when the unit is changed.
    """
    dialog = QDialog(parent)
    dialog.setWindowTitle("Grid Settings")
    layout = QVBoxLayout(dialog)
    form = QFormLayout()
    layout.addLayout(form)

    unit_combo = QComboBox()
    unit_combo.setObjectName("grid_unit")
    for unit in zpl_grid.UNITS:
        unit_combo.addItem(zpl_grid.UNIT_NAMES[unit], unit)
    unit_combo.setCurrentIndex(zpl_grid.UNITS.index(grid.unit))
    form.addRow("Unit:", unit_combo)

    spacing_spin = QDoubleSpinBox()
    spacing_spin.setObjectName("grid_spacing")
    form.addRow("Spacing:", spacing_spin)

    major_spin = QSpinBox()
    major_spin.setObjectName("grid_major")
    major_spin.setRange(*zpl_grid.MAJOR_LIMITS)
    major_spin.setValue(grid.major_every)
    major_spin.setSuffix(" spaces")
    form.addRow("Major line every:", major_spin)
    set_tip(major_spin, 'grid_major', form)

    hint = QLabel()
    layout.addWidget(hint)

    def set_unit(unit, spacing):
        low, high = zpl_grid.SPACING_LIMITS[unit]
        decimals = zpl_grid.SPACING_DECIMALS[unit]
        spacing_spin.blockSignals(True)
        spacing_spin.setDecimals(decimals)
        spacing_spin.setRange(low, high)
        spacing_spin.setSingleStep(10 ** -decimals if decimals else 1)
        spacing_spin.setSuffix(" " + zpl_grid.UNIT_SUFFIX[unit])
        spacing_spin.setValue(spacing)
        spacing_spin.blockSignals(False)
        update_hint()

    def update_hint():
        hint.setText(zpl_grid.spacing_hint(unit_combo.currentData(),
                                           spacing_spin.value(), dpi))

    shown_unit = [grid.unit]

    def on_unit_changed(_index):
        # The grid stays the size it was: 0.1 in becomes 2.54 mm, not 0.1 mm.
        unit = unit_combo.currentData()
        set_unit(unit, zpl_grid.convert_spacing(spacing_spin.value(),
                                                shown_unit[0], unit, dpi))
        shown_unit[0] = unit

    set_unit(grid.unit, grid.spacing)
    unit_combo.currentIndexChanged.connect(on_unit_changed)
    spacing_spin.valueChanged.connect(update_hint)

    buttons = _buttons(dialog)
    add_help(dialog, "grid_settings", buttons)
    layout.addWidget(buttons)
    if dialog.exec_() != QDialog.Accepted:
        return None
    chosen = grid.copy()
    chosen.unit = unit_combo.currentData()
    chosen.spacing = zpl_grid.clamp_spacing(chosen.unit, spacing_spin.value())
    chosen.major_every = major_spin.value()
    return chosen


class _PrinterRows:
    """Connection (Network or USB), then Address and Port or the USB Printer
    list, DPI, Font memory and Test Connection, plus Use Default when there
    is a default to go back to.

    Default Printer and the Print window both build their printer from these
    rows, so the two can't drift apart. `box` is the widget holding them.
    `withhold(buttons)` adds the busy row, and the buttons it names are
    withheld while a probe is out, so a half-finished one can't be accepted as
    an answer.

    Only the rows for the chosen connection are shown, but both halves are
    kept: the address typed stays while USB is chosen, and the other way
    round, so switching back offers what was there.

    Font memory belongs here rather than beside each text element. Which
    memory a printer keeps its fonts in is a property of the printer being
    deployed to, not of one field on one label.
    """

    def __init__(self, printer: Connection, dpi: int,
                 font_device: str = None, default=None):
        self._dpi = dpi
        # Carried so whatever the rows do not show survives a visit.
        self._printer = printer
        self.box = QWidget()
        layout = QVBoxLayout(self.box)
        layout.setContentsMargins(0, 0, 0, 0)
        # A grid rather than a form: a QFormLayout keeps the spacing of a row
        # it hides, and Network and USB each hide the other's rows.
        grid = QGridLayout()
        grid.setColumnStretch(1, 1)
        layout.addLayout(grid)
        self._labels = {}

        def add_row(text, field, *widgets):
            label = QLabel(text)
            label.setBuddy(widgets[0] if widgets else field)
            row = grid.rowCount()
            grid.addWidget(label, row, 0)
            if isinstance(field, QWidget):
                grid.addWidget(field, row, 1)
            else:
                grid.addLayout(field, row, 1)
            self._labels[id(field)] = label
            return label

        kinds = QHBoxLayout()
        self.network_radio = QRadioButton("Network")
        self.network_radio.setObjectName("connection_network")
        self.usb_radio = QRadioButton("USB")
        self.usb_radio.setObjectName("connection_usb")
        kinds.addWidget(self.network_radio)
        kinds.addWidget(self.usb_radio)
        kinds.addStretch(1)
        add_row("Connection:", kinds, self.network_radio)

        self.address_edit = QLineEdit(printer.address)
        self.address_edit.setObjectName("address")
        add_row("Address:", self.address_edit)

        self.port_spin = QSpinBox()
        self.port_spin.setObjectName("port")
        self.port_spin.setRange(1, 65535)
        self.port_spin.setValue(printer.port)
        set_tip(self.port_spin, 'port',
                label=add_row("Port:", self.port_spin))

        self._usb_row = QHBoxLayout()
        self.usb_combo = QComboBox()
        self.usb_combo.setObjectName("usb_printer")
        self._usb_row.addWidget(self.usb_combo, 1)
        self.usb_refresh_btn = QPushButton("Refresh")
        self.usb_refresh_btn.setObjectName("usb_refresh")
        self._usb_row.addWidget(self.usb_refresh_btn)
        set_tip(self.usb_combo, 'usb_printer',
                label=add_row("Printer:", self._usb_row, self.usb_combo))
        set_tip(self.usb_refresh_btn, 'usb_refresh')
        self._usb_entries = []
        self._list_usb(printer)

        self.dpi_combo = _dpi_combo(dpi)
        set_tip(self.dpi_combo, 'dpi', label=add_row("DPI:", self.dpi_combo))

        self.font_device_combo = _font_device_combo(font_device)
        set_tip(self.font_device_combo, 'font_device',
                label=add_row("Font memory:", self.font_device_combo))

        self._test_row = QHBoxLayout()
        self.test_btn = QPushButton("Test Connection")
        self.test_btn.setObjectName("test_connection")
        set_tip(self.test_btn, 'test_connection')
        self._test_row.addWidget(self.test_btn)
        if default is not None:
            # What the persisted default printer is, for comparing against or
            # going back to.
            default_btn = QPushButton("Use Default")
            default_btn.setObjectName("use_default")
            set_tip(default_btn, 'use_default')
            default_btn.clicked.connect(lambda: self._fill(*default))
            self._test_row.addWidget(default_btn)
        self._test_row.addStretch(1)
        layout.addLayout(self._test_row)
        self.result = QLabel()
        self.result.setObjectName("test_result")
        self.result.setWordWrap(True)
        layout.addWidget(self.result)
        self.busy = None
        self.test_btn.clicked.connect(self._on_test)
        self.usb_refresh_btn.clicked.connect(
            lambda: self._list_usb(self._chosen_usb() or self._printer))
        (self.usb_radio if printer.is_usb else self.network_radio).setChecked(True)
        self.network_radio.toggled.connect(self._show_kind)
        self._show_kind()

    def withhold(self, buttons) -> None:
        self.busy = BusyBar((self.test_btn, *buttons),
                            lambda text: self._set_result("gray", text),
                            self.box)
        self._test_row.addWidget(self.busy)

    def abandon(self) -> None:
        if self.busy is not None:
            self.busy.abandon()

    def value(self, parent):
        """(Connection, dpi, font_device), or None when there is no printer
        to give - no address, or no USB printer - which is reported."""
        printer, problem = self._chosen()
        if printer is None:
            show_error(parent, problem)
            return None
        return (printer, _dpi_from(self.dpi_combo, self._dpi),
                self.font_device_combo.currentData())

    def _chosen(self):
        """(Connection, None) for the printer the rows describe, or (None,
        what is missing)."""
        address = self.address_edit.text().strip()
        port = self.port_spin.value()
        if self.usb_radio.isChecked():
            usb = self._chosen_usb()
            if usb is None:
                return None, zpl_connection.NO_USB_CHOSEN
            carried = (self._printer.as_network(address, port) if address
                       else self._printer)
            return carried.as_usb(usb.usb_serial, usb.usb_name), None
        if not address:
            return None, zpl_connection.NO_ADDRESS
        return self._printer.as_network(address, port), None

    def _chosen_usb(self) -> Optional[Connection]:
        index = self.usb_combo.currentIndex()
        if 0 <= index < len(self._usb_entries):
            return self._usb_entries[index]
        return None

    def _list_usb(self, keep: Connection) -> None:
        """Fill the USB Printer list afresh, on `keep` if it is there."""
        entries, index = zpl_connection.usb_choices(keep)
        self.usb_combo.clear()
        for label, _printer in entries:
            self.usb_combo.addItem(label)
        self._usb_entries = [printer for _label, printer in entries]
        self.usb_combo.setCurrentIndex(index)

    def _show_kind(self, _checked=None) -> None:
        network = self.network_radio.isChecked()
        for field, widgets, shown in (
                (self.address_edit, (self.address_edit,), network),
                (self.port_spin, (self.port_spin,), network),
                (self._usb_row, (self.usb_combo, self.usb_refresh_btn),
                 not network)):
            for widget in widgets:
                widget.setVisible(shown)
            self._labels[id(field)].setVisible(shown)

    def _set_result(self, colour, text):
        self.result.setStyleSheet(f"color: {colour};")
        self.result.setText(text)

    def _fill(self, printer, dpi, font_device):
        index = self.font_device_combo.findData(font_device)
        if index >= 0:
            self.font_device_combo.setCurrentIndex(index)
        self._printer = printer
        self.address_edit.setText(printer.address)
        self.port_spin.setValue(printer.port)
        self._list_usb(printer)
        (self.usb_radio if printer.is_usb else self.network_radio).setChecked(True)
        if dpi in zpl_fonts.SUPPORTED_DPI:
            self.dpi_combo.setCurrentIndex(
                list(zpl_fonts.SUPPORTED_DPI).index(dpi))
        else:
            index = self.dpi_combo.findText(str(dpi))
            if index < 0:
                self.dpi_combo.addItem(str(dpi))
                index = self.dpi_combo.count() - 1
            self.dpi_combo.setCurrentIndex(index)

    def _on_test(self):
        printer, problem = self._chosen()
        if printer is None:
            self._set_result("red", problem)
            return
        self._set_result("gray", f"Connecting to {printer.where()}…")
        connected = f"✓ Connected to {printer.where()}"
        busy = self.busy

        def probe(cancel):
            # Connect and send nothing: reachability first, on its own, so
            # an unreachable printer reads as that rather than as "no dpi".
            # For a USB printer that is finding its node and opening it.
            zpl_connection.send(printer, b'', 5, cancel=cancel)
            busy.report(f"{connected} — asking its resolution…")
            return zpl_fonts.query_printer_dpi(printer, cancel=cancel)

        def done(reported, error):
            if isinstance(error, printer_io.Cancelled):
                self._set_result("gray", "Test cancelled.")
            elif error is not None:
                self._set_result("red", f"✗ {error}")
            elif reported is None:
                # No answer must leave the manual setting alone rather than
                # substituting a guess.
                self._set_result("orange",
                                 f"{connected}, but it did not report its "
                                 f"resolution; set the DPI manually.")
            elif reported in zpl_fonts.SUPPORTED_DPI:
                self.dpi_combo.setCurrentIndex(
                    list(zpl_fonts.SUPPORTED_DPI).index(reported))
                self._set_result("green", f"{connected} — {reported} dpi")
            else:
                self._set_result("orange",
                                 f"{connected} — reports {reported} dpi, "
                                 f"which the designer does not support.")

        busy.run(probe, done)


def printer_settings_dialog(parent, printer: Connection, dpi: int,
                            font_device: str = None,
                            title: str = "Default Printer"):
    """New (Connection, dpi, font_device), or None if cancelled."""
    dialog = QDialog(parent)
    dialog.setWindowTitle(title)
    layout = QVBoxLayout(dialog)
    rows = _PrinterRows(printer, dpi, font_device)
    layout.addWidget(rows.box)
    buttons = _buttons(dialog)
    rows.withhold((buttons.button(QDialogButtonBox.Ok),))
    add_help(dialog, "default_printer", buttons)
    layout.addWidget(buttons)

    accepted = dialog.exec_() == QDialog.Accepted
    rows.abandon()
    if not accepted:
        return None
    return rows.value(parent)


def _value_combo(name: str, options) -> QComboBox:
    """A choice row over (label, value) pairs. The values stay on the Python
    side, `combo.value()`, rather than going through Qt's item data, and the
    row starts on the first one."""
    combo = QComboBox()
    combo.setObjectName(name)
    values = []
    for label, value in options:
        combo.addItem(label)
        values.append(value)
    combo.value = lambda: values[combo.currentIndex()]
    combo.select = lambda value: combo.setCurrentIndex(
        values.index(value) if value in values else 0)
    return combo


def _setup_combo(name: str, options, current) -> QComboBox:
    """A set-up row: the printer's setting, then the carried value when no
    option is it, then the options."""
    combo = _value_combo(name, zpl_print.choices(options, current))
    combo.select(current)
    return combo


def _count_spin(name: str, limits, value: int) -> QSpinBox:
    spin = QSpinBox()
    spin.setObjectName(name)
    spin.setRange(*limits)
    spin.setValue(value)
    spin.setGroupSeparatorShown(True)
    return spin


class _NumberRow:
    """A number box that can hold a carried value it cannot show.

    An AsWritten is shown beside the box, and is what the row gives back
    until the box is changed.
    """

    def __init__(self, spin, current, shown_instead):
        self.spin = spin
        self.note = QLabel()
        self._current = current
        self._touched = False
        if isinstance(current, AsWritten):
            spin.setValue(shown_instead)
            self.note.setText(current.label())
        else:
            if current is not None:
                spin.setValue(current)
            self.note.hide()
        spin.valueChanged.connect(self._on_changed)

    def _on_changed(self, _value):
        self._touched = True
        self.note.hide()

    def value(self):
        if isinstance(self._current, AsWritten) and not self._touched:
            return self._current
        return self.spin.value()


def print_dialog(parent, document: Document, printer, default):
    """The Print window. Returns (action, printer, settings), or None if
    cancelled.

    `action` is 'print' or 'ok'. OK keeps the settings without printing.
    `printer` is the (Connection, dpi, font_device) for this session, and
    the window opens on the one in effect. `default` is the persisted printer
    Use Default goes back to. `settings` is a print_settings.PrintSettings:
    everything here that is saved with the label, for the caller to apply as
    one undo entry. The rows come from print_settings, the same tables the
    GTK window draws from.
    """
    settings = PrintSettings.read(document)
    dialog = QDialog(parent)
    dialog.setWindowTitle("Print")
    layout = QVBoxLayout(dialog)
    columns = QHBoxLayout()
    layout.addLayout(columns)
    left, right = QVBoxLayout(), QVBoxLayout()
    columns.addLayout(left)
    columns.addLayout(right)

    printer_box = QGroupBox("Printer (this session only)")
    rows = _PrinterRows(*printer, default=default)
    QVBoxLayout(printer_box).addWidget(rows.box)
    left.addWidget(printer_box)

    copies_box = QGroupBox("Copies (^PQ)")
    copies = QFormLayout(copies_box)
    quantity = _count_spin("quantity", zpl_print.QUANTITY_RANGE,
                           settings.quantity)
    copies.addRow("Copies:", quantity)
    pause = _count_spin("pause_count", zpl_print.PAUSE_RANGE,
                        settings.pause_count)
    pause.setSpecialValueText("Never")
    pause.setSuffix(" labels")
    copies.addRow("Pause every:", pause)
    set_tip(pause, 'pause_count', copies)
    replicates = _count_spin("replicates", zpl_print.REPLICATE_RANGE,
                             settings.replicates)
    copies.addRow("Replicates:", replicates)
    set_tip(replicates, 'replicates', copies)
    override = QCheckBox("Don't pause between groups")
    override.setObjectName("override_pause")
    override.setChecked(settings.override_pause)
    set_tip(override, 'override_pause')
    copies.addRow(override)
    cut = QCheckBox("Cut after a voided RFID label")
    cut.setObjectName("cut_on_error")
    cut.setChecked(settings.cut_on_error)
    set_tip(cut, 'cut_on_error')
    copies.addRow(cut)
    left.addWidget(copies_box)
    left.addStretch(1)

    # How the finished label is laid down, and checked, rather than where a
    # field sits on it.
    label_box = QGroupBox("Label")
    flags = QVBoxLayout(label_box)

    def flag(name, text, value):
        check = QCheckBox(text)
        check.setObjectName(name)
        check.setChecked(value)
        flags.addWidget(check)
        return check

    invert = flag("invert", "Print upside down (^PO)", settings.invert)
    mirror = flag("mirror", "Mirror left to right (^PM)", settings.mirror)
    reverse = flag("reverse", "Reverse fields, white on black (^LR)",
                   settings.reverse)
    validate = flag("code_validation", "Check barcode data as printed (^CV)",
                    settings.code_validation)
    # Print upside down says all there is to say
    for check, key in ((mirror, 'mirror'), (reverse, 'reverse'),
                       (validate, 'code_validation')):
        set_tip(check, key)
    right.addWidget(label_box)

    setup_box = QGroupBox("Printer setup sent with the label")
    setup = QFormLayout(setup_box)

    # A number box has no "unset", so the darkness has a tick for it.
    darkness_set = QCheckBox()
    darkness_set.setObjectName("darkness_set")
    set_tip(darkness_set, 'darkness_set')
    darkness_set.setChecked(settings.darkness is not None)
    darkness_spin = QDoubleSpinBox()
    darkness_spin.setObjectName("darkness")
    darkness_spin.setRange(*zpl_print.DARKNESS_RANGE)
    darkness_spin.setDecimals(zpl_print.DARKNESS_DECIMALS)
    darkness_spin.setSingleStep(1.0)
    darkness = _NumberRow(darkness_spin, settings.darkness, 0.0)
    darkness_row = QHBoxLayout()
    darkness_row.addWidget(darkness_set)
    darkness_row.addWidget(darkness_spin, 1)
    darkness_row.addWidget(darkness.note)
    setup.addRow("Darkness (^MD):", darkness_row)
    set_tip(darkness_spin, 'darkness', label=setup.labelForField(darkness_row))

    print_speed = _setup_combo("print_speed", zpl_print.PRINT_SPEED_CHOICES,
                               settings.print_speed)
    setup.addRow("Print speed (^PR):", print_speed)
    set_tip(print_speed, 'print_speed', setup)
    slew_speed = _setup_combo("slew_speed", zpl_print.FEED_SPEED_CHOICES,
                              settings.slew_speed)
    setup.addRow("Slew speed:", slew_speed)
    set_tip(slew_speed, 'slew_speed', setup)
    backfeed_speed = _setup_combo("backfeed_speed",
                                  zpl_print.FEED_SPEED_CHOICES,
                                  settings.backfeed_speed)
    setup.addRow("Backfeed speed:", backfeed_speed)
    set_tip(backfeed_speed, 'backfeed_speed', setup)

    print_mode = _setup_combo("print_mode", zpl_print.PRINT_MODES,
                              settings.print_mode)
    setup.addRow("Print mode (^MM):", print_mode)
    set_tip(print_mode, 'print_mode', setup)
    prepeel = QCheckBox("Prepeel")
    prepeel.setObjectName("prepeel")
    prepeel.setChecked(settings.prepeel)
    setup.addRow("", prepeel)
    set_tip(prepeel, 'prepeel', setup)

    tracking = _setup_combo("media_tracking", zpl_print.MEDIA_TRACKING,
                            settings.media_tracking)
    setup.addRow("Media tracking (^MN):", tracking)
    set_tip(tracking, 'media_tracking', setup)
    offset_spin = QSpinBox()
    offset_spin.setObjectName("mark_offset")
    offset_spin.setRange(*zpl_print.MARK_OFFSET_RANGE)
    offset_spin.setSuffix(" dots")
    offset = _NumberRow(offset_spin, settings.mark_offset, 0)
    offset_row = QHBoxLayout()
    offset_row.addWidget(offset_spin, 1)
    offset_row.addWidget(offset.note)
    setup.addRow("Black mark offset:", offset_row)
    set_tip(offset_spin, 'mark_offset', label=setup.labelForField(offset_row))

    media_type = _setup_combo("media_type", zpl_print.MEDIA_TYPES,
                              settings.media_type)
    setup.addRow("Media type (^MT):", media_type)
    set_tip(media_type, 'media_type', setup)

    note = QLabel("Printer's setting sends nothing. A value set here stays "
                  "in effect on the printer after this label.")
    note.setWordWrap(True)
    setup.addRow(note)
    right.addWidget(setup_box)
    right.addStretch(1)

    def follow():
        # A row that only means something with another set is greyed out
        # without it: ^PR is a chain, prepeel rides on a print mode, and the
        # offset is only for mark sensing.
        darkness_spin.setEnabled(darkness_set.isChecked())
        slew_speed.setEnabled(print_speed.value() is not None)
        backfeed_speed.setEnabled(slew_speed.isEnabled()
                                  and slew_speed.value() is not None)
        prepeel.setEnabled(print_mode.value() is not None)
        offset_spin.setEnabled(tracking.value() == zpl_print.MARK_SENSING)

    darkness_set.toggled.connect(follow)
    for combo in (print_speed, slew_speed, print_mode, tracking):
        combo.currentIndexChanged.connect(follow)
    follow()

    buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel,
                               parent=dialog)
    print_btn = buttons.addButton("Print", QDialogButtonBox.AcceptRole)
    print_btn.setObjectName("print")
    ok_btn = buttons.button(QDialogButtonBox.Ok)
    # Print is what the window is opened for, so it is the one Enter presses.
    ok_btn.setAutoDefault(False)
    print_btn.setDefault(True)
    chosen = {}
    buttons.clicked.connect(lambda button: chosen.update(
        action='print' if button is print_btn else 'ok'))
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    rows.withhold((ok_btn, print_btn))
    add_help(dialog, "print", buttons)
    layout.addWidget(buttons)
    _make_scrollable(dialog)

    accepted = dialog.exec_() == QDialog.Accepted
    rows.abandon()
    if not accepted:
        return None
    chosen_printer = rows.value(parent)
    if chosen_printer is None:
        return None

    result = settings.copy()
    result.quantity = quantity.value()
    result.pause_count = pause.value()
    result.replicates = replicates.value()
    result.override_pause = override.isChecked()
    result.cut_on_error = cut.isChecked()
    result.invert = invert.isChecked()
    result.mirror = mirror.isChecked()
    result.reverse = reverse.isChecked()
    result.code_validation = validate.isChecked()
    result.darkness = darkness.value() if darkness_set.isChecked() else None
    if isinstance(result.darkness, float):
        result.darkness = round(result.darkness, zpl_print.DARKNESS_DECIMALS)
    result.print_speed = print_speed.value()
    result.slew_speed = slew_speed.value()
    result.backfeed_speed = backfeed_speed.value()
    result.print_mode = print_mode.value()
    result.prepeel = prepeel.isChecked()
    result.media_tracking = tracking.value()
    result.mark_offset = offset.value()
    result.media_type = media_type.value()
    return chosen.get('action', 'ok'), chosen_printer, result


class PrinterFontsDialog(QDialog):
    """The fonts stored on the printer, with upload, delete and refresh, plus
    a read-only reference list of the printer's built-in resident fonts."""

    def __init__(self, parent, printer: Connection, on_uploaded=None,
                 font_device: str = zpl_fonts.DEFAULT_FONT_DEVICE):
        super().__init__(parent)
        self.setWindowTitle("Printer Fonts")
        self.resize(480, 520)
        self._printer = printer
        self._on_uploaded = on_uploaded
        # Where Upload... offers to put a font first: the Font memory printer
        # setting. Only a default - this is a manager, so it can put one on
        # any drive, the same way Printer Graphics can.
        self._font_device = font_device or zpl_fonts.DEFAULT_FONT_DEVICE
        self._font_specs = []

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("<b>Uploaded Fonts</b>"))
        self._status = QLabel()
        self._status.setWordWrap(True)
        layout.addWidget(self._status)

        # Font, then the memory its drive letter names - the same second
        # column Printer Objects has, and for the same reason: a bare "B:"
        # says nothing to a reader who has not memorised the ZPL manual's
        # letter designations. Column 0 stays the full d:NAME.TTF spec.
        self._list = QTreeWidget()
        self._list.setHeaderLabels(["Font", "Memory"])
        self._list.setRootIsDecorated(False)
        self._list.setUniformRowHeights(True)
        layout.addWidget(self._list, 1)

        self._preview = QLabel()
        self._preview.setMinimumHeight(52)
        self._preview.setWordWrap(True)
        layout.addWidget(self._preview)

        row = QHBoxLayout()
        self._upload_btn = QPushButton("Upload…")
        self._delete_btn = QPushButton("Delete")
        self._refresh_btn = QPushButton("Refresh")
        buttons = (self._upload_btn, self._delete_btn, self._refresh_btn)
        for b in buttons:
            row.addWidget(b)
        row.addStretch(1)
        self._busy = BusyBar(buttons, self._status.setText, self)
        row.addWidget(self._busy)
        layout.addLayout(row)

        layout.addWidget(QLabel("<b>Built-in Fonts</b>"))
        self._resident_status = QLabel()
        self._resident_status.setWordWrap(True)
        layout.addWidget(self._resident_status)
        self._resident_list = QListWidget()
        layout.addWidget(self._resident_list, 1)

        close = QDialogButtonBox(QDialogButtonBox.Close, parent=self)
        close.rejected.connect(self.reject)
        add_help(self, "printer_fonts", close)
        layout.addWidget(close)

        self._upload_btn.clicked.connect(self._on_upload)
        self._delete_btn.clicked.connect(self._on_delete)
        self._refresh_btn.clicked.connect(self.refresh)
        self._list.currentItemChanged.connect(self._update_preview)
        self.refresh()

    def reject(self):
        self._busy.abandon()
        super().reject()

    def refresh(self):
        """Both lists in one trip: the stored fonts, then the resident ones."""
        self._list.clear()
        self._preview.clear()
        self._resident_list.clear()
        self._font_specs = []
        self._delete_btn.setEnabled(False)
        where = self._printer.where(with_port=False)
        self._status.setText(f"Listing fonts on {where}...")
        self._resident_status.clear()

        def query(cancel):
            fonts = zpl_fonts.query_printer_fonts(self._printer, cancel=cancel)
            self._busy.report("Asking which built-in fonts it has...")
            detected = zpl_fonts.query_resident_fonts(self._printer,
                                                      cancel=cancel)
            return fonts, detected

        def done(result, error):
            if isinstance(error, printer_io.Cancelled):
                self._status.setText("Listing cancelled.")
                return
            answer, detected = (None, None) if error is not None else result
            fonts, unreadable = (None, ()) if answer is None else answer
            if isinstance(error, PortProblem):
                # A USB port with a fix the user needs to hear about.
                self._status.setText(str(error))
            elif fonts is None:
                # Not the same as "no fonts": say the printer could not be
                # asked, rather than showing an empty list as if it had answered.
                self._status.setText(f"Could not reach the printer at "
                                     f"{self._printer.where()}.")
            else:
                self._font_specs = sorted(fonts)
                for spec in self._font_specs:
                    QTreeWidgetItem(self._list,
                                    [spec, graphic_store.device_name(spec)])
                self._delete_btn.setEnabled(bool(fonts))
                self._status.setText(workflow.listing_status(
                    where, "font", zpl_fonts.DEVICES,
                    self._font_specs, unreadable))
            self._show_resident(detected)

        self._busy.run(query, done)

    def _show_resident(self, detected):
        self._resident_list.clear()
        if detected:
            self._resident_status.setText(
                f"Reported by the printer at "
                f"{self._printer.where(with_port=False)}.")
        elif detected is None:
            self._resident_status.setText(
                "Could not confirm which are present - showing the standard "
                "set. Sizes and styles are as published, not rendered.")
        else:
            self._resident_status.setText(
                "Printer reported none of the standard set - showing it "
                "anyway. Sizes and styles are as published, not rendered.")
        for font in zpl_fonts.RESIDENT_FONTS:
            label = f"{font['code']} — {font['name']} ({font['matrix']}, {font['kind']})"
            if detected and font['code'].upper() in detected:
                label += " — detected on this printer"
            self._resident_list.addItem(label)

    def _update_preview(self, current=None, _previous=None):
        item = current if current is not None else self._list.currentItem()
        if item is None:
            self._preview.clear()
            return
        _device, name = zpl_fonts.split_font_spec(item.text(0))
        path = zpl_fonts.file_for_printer_name(name)
        if path:
            self._show_preview(path)
            return
        # Printers won't hand a font's bytes back once uploaded - confirmed
        # live (SGD retrieval gets no reply at all, and a printer's own FTP
        # server, where present, answers with a plain 550 Permission denied
        # for a .TTF while other stored files download fine) - so there is
        # nothing to try here, only this to say.
        self._preview.setText(
            "(preview unavailable — printers block downloading fonts to "
            "protect font distribution rights)")
        self._preview.setFont(QFont())

    def _show_preview(self, path: str):
        zpl_fonts.register_app_font(path)
        font = QFont(zpl_fonts.family_for_file(path))
        font.setPointSize(16)
        self._preview.setText("The quick brown fox 0123456789")
        self._preview.setFont(font)

    def _on_upload(self):
        family, path = choose_font_family(self, title="Upload Font to Printer")
        if not path:
            return
        device = font_device_dialog(self, self._font_device)
        if device is None:
            return
        name = zpl_fonts.printer_font_name(path)
        shown = zpl_fonts.printer_font_path(name, device)
        self._status.setText(f"Uploading {shown}...")

        def done(_result, error):
            if isinstance(error, printer_io.Cancelled):
                self._status.setText(f"Upload of {shown} cancelled.")
                return
            if error is not None:
                show_error(self, f"Font upload failed: {error}")
                return
            if self._on_uploaded:
                self._on_uploaded(name, path)
            self.refresh()

        self._busy.run(lambda cancel: zpl_fonts.upload_font(
            self._printer, path, name, device, cancel=cancel), done)

    def _on_delete(self):
        item = self._list.currentItem()
        if item is None:
            return
        shown = item.text(0)
        # The drive the selected row is actually on, not an assumed E:.
        device, name = zpl_fonts.split_font_spec(shown)
        self._status.setText(f"Deleting {shown}...")

        def done(_result, error):
            if isinstance(error, printer_io.Cancelled):
                self._status.setText(f"Deleting {shown} cancelled.")
                return
            if error is not None:
                show_error(self, f"Could not delete {shown}: {error}")
                return
            self.refresh()

        self._busy.run(lambda cancel: zpl_fonts.delete_printer_font(
            self._printer, name, device, cancel=cancel), done)


class PrinterGraphicsDialog(QDialog):
    """View, store, retrieve and delete graphics on the real printer -
    whichever one is actually in effect this session (the caller passes
    self.printer_connection, which a session override moves without
    touching the persisted default), the same target PrinterFontsDialog
    already uses.

    Modelled closely on PrinterFontsDialog: refresh() queries the printer
    live and reports when it cannot be reached, exactly as that one does for
    ^HW. The one thing this keeps that Fonts has no need for is
    graphic_store's in-session local cache, which ^XG/^IM/^IL already read
    from - Store and Retrieve mirror a successful network result into it
    (the same way upload_font's caller also registers the font locally),
    purely so an already-placed reference on the canvas updates without a
    second round trip to the printer.
    """

    def __init__(self, parent, printer: Connection, on_changed=None):
        super().__init__(parent)
        self.setWindowTitle("Printer Graphics")
        self.resize(460, 340)
        self._printer = printer
        self._on_changed = on_changed
        self._entries = []

        layout = QVBoxLayout(self)
        self._status = QLabel()
        self._status.setWordWrap(True)
        layout.addWidget(self._status)

        body = QHBoxLayout()
        layout.addLayout(body, 1)

        self._list = QListWidget()
        body.addWidget(self._list, 1)

        self._preview = QLabel()
        self._preview.setFixedSize(160, 160)
        self._preview.setAlignment(Qt.AlignCenter)
        self._preview.setFrameShape(QFrame.Box)
        body.addWidget(self._preview)

        row = QHBoxLayout()
        self._store_btn = QPushButton("Store…")
        self._retrieve_btn = QPushButton("Retrieve…")
        self._delete_btn = QPushButton("Delete")
        self._refresh_btn = QPushButton("Refresh")
        for button, key in ((self._store_btn, 'store_graphic'),
                            (self._retrieve_btn, 'retrieve_graphic')):
            button.setObjectName(key)
            set_tip(button, key)
        self._retrieve_btn.setEnabled(False)
        self._delete_btn.setEnabled(False)
        buttons = (self._store_btn, self._retrieve_btn, self._delete_btn,
                   self._refresh_btn)
        for b in buttons:
            row.addWidget(b)
        row.addStretch(1)
        self._busy = BusyBar(buttons, self._status.setText, self)
        row.addWidget(self._busy)
        layout.addLayout(row)

        close = QDialogButtonBox(QDialogButtonBox.Close, parent=self)
        close.rejected.connect(self.reject)
        add_help(self, "printer_graphics", close)
        layout.addWidget(close)

        self._store_btn.clicked.connect(self._on_store)
        self._retrieve_btn.clicked.connect(self._on_retrieve)
        self._delete_btn.clicked.connect(self._on_delete)
        self._refresh_btn.clicked.connect(self.refresh)
        self._list.currentRowChanged.connect(self._on_selection_changed)
        self.refresh()

    def reject(self):
        self._busy.abandon()
        super().reject()

    def refresh(self):
        self._list.clear()
        self._entries = []
        self._preview.clear()
        self._retrieve_btn.setEnabled(False)
        self._delete_btn.setEnabled(False)
        where = self._printer.where(with_port=False)
        self._status.setText(f"Listing graphics on {where}...")

        def done(specs, error):
            if isinstance(error, printer_io.Cancelled):
                self._status.setText("Listing cancelled.")
                return
            if isinstance(error, PortProblem):
                self._status.setText(str(error))
                return
            if specs is None or error is not None:
                self._status.setText(f"Could not reach the printer at "
                                     f"{self._printer.where()}.")
                return
            self._entries, unreadable = specs
            for spec in self._entries:
                cached = graphic_store.recall(spec)
                suffix = f" ({cached.width}×{cached.height})" if cached else ""
                self._list.addItem(f"{spec}{suffix}")
            self._status.setText(workflow.listing_status(
                where, "graphic", graphic_store.DEVICES,
                self._entries, unreadable))

        self._busy.run(lambda cancel: graphic_store.query_printer_graphics(
            self._printer, cancel=cancel), done)

    def _selected_entry(self):
        """The spec the list has selected, or None."""
        row = self._list.currentRow()
        return self._entries[row] if 0 <= row < len(self._entries) else None

    def _on_selection_changed(self, row: int):
        spec = self._selected_entry()
        self._retrieve_btn.setEnabled(spec is not None)
        self._delete_btn.setEnabled(spec is not None)
        if spec is None:
            self._preview.clear()
            return
        image = graphic_store.recall(spec)
        if image is None:
            # Not fetched this session yet - Retrieve first.
            self._preview.clear()
            return
        qimage = to_qimage(image)
        if qimage is None or qimage.isNull():
            self._preview.clear()
            return
        pixmap = QPixmap.fromImage(qimage).scaled(
            self._preview.width(), self._preview.height(),
            Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self._preview.setPixmap(pixmap)

    def _on_store(self):
        path = choose_image_file(self, "Store Graphic")
        if not path:
            return
        spec = store_graphic_dialog(self)
        if spec is None:
            return
        try:
            image = PILImage.open(path)
            image.load()
            if image.mode not in ('RGB', 'L'):
                image = image.convert('RGB')
        except Exception as e:
            show_error(self, f"Could not open {path}: {e}")
            return

        self._status.setText(f"Uploading {spec}...")

        def done(_result, error):
            if isinstance(error, printer_io.Cancelled):
                self._status.setText(f"Upload of {spec} cancelled.")
                return
            if error is not None:
                show_error(self, f"Could not store {spec}: {error}")
                self.refresh()
                return
            graphic_store.store(spec, image)
            if self._on_changed:
                self._on_changed(spec, image)
            self.refresh()

        self._busy.run(lambda cancel: graphic_store.upload_graphic(
            self._printer, spec, image, cancel=cancel), done)

    def _on_retrieve(self):
        """Fetch the selected graphic's real bytes from the printer, cache
        them locally so ^XG/^IM/^IL resolve, and offer to save them to a
        file too."""
        spec = self._selected_entry()
        if spec is None:
            return
        self._status.setText(f"Retrieving {spec}...")

        def done(image, error):
            if isinstance(error, printer_io.Cancelled):
                self._status.setText(f"Retrieving {spec} cancelled.")
                return
            if error is not None:
                show_error(self, f"Could not retrieve {spec}: {error}")
                self.refresh()
                return
            graphic_store.store(spec, image)
            if self._on_changed:
                self._on_changed(spec, image)
            _device, name, _ext = graphic_store.split_device_spec(spec)
            path, _ = QFileDialog.getSaveFileName(
                self, "Save Retrieved Graphic", f"{name}.png", IMAGE_FILTER)
            if path:
                try:
                    if not Path(path).suffix:
                        path += '.png'
                    image.save(path)
                except Exception as e:
                    show_error(self, f"Could not save {path}: {e}")
            self.refresh()

        self._busy.run(lambda cancel: graphic_store.retrieve_printer_graphic(
            self._printer, spec, cancel=cancel), done)

    def _on_delete(self):
        spec = self._selected_entry()
        if spec is None:
            return
        if not ask_delete_object(self, spec, self._printer):
            return
        self._status.setText(f"Deleting {spec}...")

        def done(_result, error):
            if isinstance(error, printer_io.Cancelled):
                self._status.setText(f"Deleting {spec} cancelled.")
                return
            if error is not None:
                show_error(self, f"Could not delete {spec}: {error}")
                self.refresh()
                return
            graphic_store.delete(spec)
            if self._on_changed:
                self._on_changed(spec, None)
            self.refresh()

        self._busy.run(lambda cancel: graphic_store.delete_printer_graphic(
            self._printer, spec, cancel=cancel), done)


class PrinterObjectsDialog(QDialog):
    """Every object stored on the real printer at `address`:`port`, across
    R:/E:/B:/A:/Z: and any extension - not just the fonts and graphics
    PrinterFontsDialog and PrinterGraphicsDialog already manage. Modelled on
    PrinterFontsDialog: no preview pane, since most objects here are not
    images. Store and Retrieve both exist here because both have a genuinely
    generic printer command behind them - CISDFCRC16 and file.type - unlike
    ~DY/~DG/^HG, which are each locked to one format and stay with the two
    dialogs that already know it.

    Store always writes to E: - CISDFCRC16 gives no device choice - so its
    prompt asks only for a name and extension, never a device. Z: is
    read-only factory content ^ID cannot delete (see
    printer_objects.DEVICES), so Delete is withheld for a Z: selection even
    though it is listed and can still be Retrieved.
    """

    def __init__(self, parent, printer: Connection, on_changed=None):
        super().__init__(parent)
        self.setWindowTitle("Printer Objects")
        self.resize(460, 340)
        self._printer = printer
        self._on_changed = on_changed

        layout = QVBoxLayout(self)
        self._status = QLabel()
        self._status.setWordWrap(True)
        layout.addWidget(self._status)

        # Object, then the memory type its device letter names - "Z:" and
        # "E:" say nothing to a user who has not memorised the ZPL manual's
        # letter designations, so the second column says it in words (see
        # graphic_store.DEVICE_NAMES). Column 0 stays the full d:NAME.EXT
        # spec, so what the list shows still matches every status message and
        # confirmation prompt below verbatim. A QTreeWidget, not the flat
        # QListWidget the font and graphic managers use, purely because this
        # one has columns to head.
        self._list = QTreeWidget()
        self._list.setHeaderLabels(["Object", "Memory"])
        self._list.setRootIsDecorated(False)
        self._list.setUniformRowHeights(True)
        layout.addWidget(self._list, 1)

        row = QHBoxLayout()
        self._store_btn = QPushButton("Store…")
        self._retrieve_btn = QPushButton("Retrieve…")
        self._delete_btn = QPushButton("Delete")
        self._refresh_btn = QPushButton("Refresh")
        for button, key in ((self._store_btn, 'store_object'),
                            (self._retrieve_btn, 'retrieve_object'),
                            (self._delete_btn, 'delete_object')):
            button.setObjectName(key)
            set_tip(button, key)
        self._retrieve_btn.setEnabled(False)
        self._delete_btn.setEnabled(False)
        buttons = (self._store_btn, self._retrieve_btn, self._delete_btn,
                   self._refresh_btn)
        for b in buttons:
            row.addWidget(b)
        row.addStretch(1)
        self._busy = BusyBar(buttons, self._status.setText, self)
        row.addWidget(self._busy)
        layout.addLayout(row)

        close = QDialogButtonBox(QDialogButtonBox.Close, parent=self)
        close.rejected.connect(self.reject)
        add_help(self, "printer_objects", close)
        layout.addWidget(close)

        self._store_btn.clicked.connect(self._on_store)
        self._retrieve_btn.clicked.connect(self._on_retrieve)
        self._delete_btn.clicked.connect(self._on_delete)
        self._refresh_btn.clicked.connect(self.refresh)
        self._list.currentItemChanged.connect(self._on_selection_changed)
        self.refresh()

    def reject(self):
        self._busy.abandon()
        super().reject()

    def refresh(self):
        self._list.clear()
        self._retrieve_btn.setEnabled(False)
        self._delete_btn.setEnabled(False)
        where = self._printer.where(with_port=False)
        self._status.setText(f"Listing objects on {where}...")

        def done(specs, error):
            if isinstance(error, printer_io.Cancelled):
                self._status.setText("Listing cancelled.")
                return
            if isinstance(error, PortProblem):
                self._status.setText(str(error))
                return
            if specs is None or error is not None:
                self._status.setText(f"Could not reach the printer at "
                                     f"{self._printer.where()}.")
                return
            entries, unreadable = specs
            for spec in entries:
                QTreeWidgetItem(self._list,
                                [spec, graphic_store.device_name(spec)])
            self._status.setText(workflow.listing_status(
                where, "object", printer_objects.DEVICES,
                entries, unreadable))

        self._busy.run(lambda cancel: printer_objects.query_printer_objects(
            self._printer, cancel=cancel), done)

    def _selected_spec(self) -> Optional[str]:
        item = self._list.currentItem()
        return item.text(0) if item is not None else None

    def _on_selection_changed(self, _current, _previous):
        spec = self._selected_spec()
        self._retrieve_btn.setEnabled(spec is not None)
        # ^ID silently ignores Z: (read-only factory content), so Delete
        # would report success and change nothing - withhold it rather than
        # let that happen.
        self._delete_btn.setEnabled(spec is not None and not spec.startswith('Z:'))

    def _on_store(self):
        path, _ = QFileDialog.getOpenFileName(self, "Store Object")
        if not path:
            return
        default_name = Path(path).stem[:8] or 'UNKNOWN'
        default_ext = Path(path).suffix.lstrip('.') or 'DAT'
        result = store_object_dialog(self, default_name, default_ext)
        if result is None:
            return
        name, ext = result

        try:
            data = Path(path).read_bytes()
        except Exception as e:
            show_error(self, f"Could not read {path}: {e}")
            return

        self._status.setText(f"Uploading E:{name}.{ext}...")

        def done(_result, error):
            if isinstance(error, printer_io.Cancelled):
                self._status.setText(f"Upload of E:{name}.{ext} cancelled.")
                return
            if error is not None:
                show_error(self, f"Could not store E:{name}.{ext}: {error}")
            self.refresh()

        self._busy.run(lambda cancel: printer_objects.upload_printer_object(
            self._printer, name, ext, data, cancel=cancel), done)

    def _on_retrieve(self):
        spec = self._selected_spec()
        if spec is None:
            return
        self._status.setText(f"Retrieving {spec}...")

        def done(data, error):
            if isinstance(error, printer_io.Cancelled):
                self._status.setText(f"Retrieving {spec} cancelled.")
                return
            if isinstance(error, printer_objects.ObjectNotRetrievable):
                show_error(self, "This printer does not support retrieving "
                                 "stored files (no reply to the retrieval "
                                 "command).")
                self.refresh()
                return
            if error is not None:
                show_error(self, f"Could not retrieve {spec}: {error}")
                self.refresh()
                return
            _device, name, ext = graphic_store.split_device_spec(spec)
            path, _ = QFileDialog.getSaveFileName(
                self, "Save Retrieved Object", f"{name}.{ext.lower()}")
            if path:
                try:
                    Path(path).write_bytes(data)
                except Exception as e:
                    show_error(self, f"Could not save {path}: {e}")
            self.refresh()

        self._busy.run(lambda cancel: printer_objects.download_printer_object(
            self._printer, spec, cancel=cancel), done)

    def _on_delete(self):
        spec = self._selected_spec()
        if spec is None:
            return
        if not ask_delete_object(self, spec, self._printer):
            return
        self._status.setText(f"Deleting {spec}...")

        def done(_result, error):
            if isinstance(error, printer_io.Cancelled):
                self._status.setText(f"Deleting {spec} cancelled.")
                return
            if error is not None:
                show_error(self, f"Could not delete {spec}: {error}")
                self.refresh()
                return
            if graphic_store.delete(spec) and self._on_changed:
                self._on_changed(spec, None)
            self.refresh()

        self._busy.run(lambda cancel: printer_objects.delete_printer_object(
            self._printer, spec, cancel=cancel), done)


class PrinterConsoleDialog(QDialog):
    """A free-form send/reply console for whatever the type-specific
    managers (Fonts/Graphics/Objects) don't cover - one-off diagnostics like
    ~HS host status or ~HI host identification, or an SGD getvar/setvar not
    wrapped by any dialog. Text is sent to the printer exactly as typed, no
    ^XA/^XZ wrapping added, so both immediate commands and full formats work
    unchanged.

    Shown non-modally (see on_printer_console in qtui/window.py) so the main
    window stays usable while this stays open. That means it can outlive a
    printer change made elsewhere, so address/port are never copied at
    construction - _on_send reads them from `parent` fresh on every send.
    WA_DeleteOnClose ensures Close (or the window's own close button) really
    tears the dialog down rather than leaving a hidden, stale instance.
    """

    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Printer Console")
        self.resize(480, 420)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self._parent = parent

        layout = QVBoxLayout(self)
        self._input = QPlainTextEdit()
        self._input.setMinimumHeight(4 * QFontMetrics(self._input.font()).height())
        layout.addWidget(self._input)

        row = QHBoxLayout()
        self._send_btn = QPushButton("Send")
        row.addWidget(self._send_btn)
        row.addStretch(1)
        self._busy = BusyBar((self._send_btn,), parent=self)
        row.addWidget(self._busy)
        layout.addLayout(row)

        self._log = QPlainTextEdit(readOnly=True)
        layout.addWidget(self._log, 1)

        close = QDialogButtonBox(QDialogButtonBox.Close, parent=self)
        close.rejected.connect(self.reject)
        add_help(self, "printer_console", close)
        layout.addWidget(close)

        self._send_btn.clicked.connect(self._on_send)

    def reject(self):
        self._busy.abandon()
        super().reject()

    def _on_send(self):
        text = self._input.toPlainText()
        if not text.strip():
            return
        printer = self._parent.printer_connection

        def done(reply, error):
            if isinstance(error, printer_io.Cancelled):
                self._log.appendPlainText(f"> {text}\n(cancelled)\n")
                return
            if error is not None:
                show_error(self, f"Could not send command: {error}")
                return
            self._log.appendPlainText(f"> {text}\n{reply or '(no reply)'}\n")
            self._input.clear()

        self._busy.run(lambda cancel: zpl_connection.send_command(
            printer, text, cancel=cancel), done)


class PrinterFirmwareDialog(QDialog):
    """Show the firmware the printer runs now and send it a new file.

    Modal, like the Fonts/Graphics/Objects managers: nothing else should
    touch the printer while its flash is being written. The upload's bar is
    the BusyBar's, switched to a percentage by report_fraction.

    The printer is the session's network one, or any USB printer this
    machine has - the way in to one in forced download mode. It opens on the
    session's printer: the USB one, when that is what is in use and it is
    plugged in. Refresh looks for USB printers again, since one that
    restarts into that mode can come back under another number.
    """

    def __init__(self, parent, printer: Connection):
        super().__init__(parent)
        self.setWindowTitle("Firmware Update")
        self.resize(460, 200)
        self._network = Connection.network(printer.address, printer.port)
        self._opening_path = zpl_connection.node_of(printer)
        self._path = None
        self._size = 0

        layout = QVBoxLayout(self)
        row = QHBoxLayout()
        connection_label = QLabel("Connection:")
        row.addWidget(connection_label)
        self._connection = QComboBox()
        self._connection.setObjectName("firmware_connection")
        connection_label.setBuddy(self._connection)
        set_tip(self._connection, 'firmware_connection', label=connection_label)
        row.addWidget(self._connection, 1)
        layout.addLayout(row)
        self._list_connections()

        row = QHBoxLayout()
        self._current = QLabel("Current firmware: checking\u2026")
        self._current.setTextInteractionFlags(Qt.TextSelectableByMouse)
        row.addWidget(self._current, 1)
        self._refresh_btn = QPushButton("Refresh")
        self._refresh_btn.setObjectName("firmware_refresh")
        set_tip(self._refresh_btn, 'firmware_refresh')
        row.addWidget(self._refresh_btn)
        layout.addLayout(row)

        row = QHBoxLayout()
        self._file = QLabel("No firmware file chosen.")
        self._file.setWordWrap(True)
        row.addWidget(self._file, 1)
        self._browse_btn = QPushButton("Browse\u2026")
        row.addWidget(self._browse_btn)
        layout.addLayout(row)

        self._status = QLabel()
        self._status.setWordWrap(True)
        layout.addWidget(self._status)
        layout.addStretch(1)

        row = QHBoxLayout()
        # Not one of the BusyBar's blocked buttons: Help is most wanted while
        # an upload is going wrong.
        self._help_btn = add_help(self, "firmware_update")
        row.addWidget(self._help_btn)
        self._update_btn = QPushButton("Update")
        self._update_btn.setEnabled(False)
        row.addWidget(self._update_btn)
        row.addStretch(1)
        self._close_btn = QPushButton("Close")
        self._busy = BusyBar((self._connection, self._refresh_btn,
                              self._browse_btn, self._update_btn,
                              self._close_btn),
                             parent=self, width=330)
        row.addWidget(self._busy)
        row.addWidget(self._close_btn)
        layout.addLayout(row)

        self._refresh_btn.clicked.connect(self._on_refresh)
        self._connection.currentIndexChanged.connect(self._ask_version)
        self._browse_btn.clicked.connect(self._on_browse)
        self._update_btn.clicked.connect(self._on_update)
        self._close_btn.clicked.connect(self.reject)
        QTimer.singleShot(0, self._ask_version)

    def reject(self):
        self._busy.abandon()
        super().reject()

    def _list_connections(self):
        """Network first, then every USB printer, keeping the one chosen if
        it is still there."""
        chosen = (self._connection.currentData() if self._connection.count()
                  else self._opening_path)
        self._connection.blockSignals(True)
        self._connection.clear()
        self._connection.addItem(f"Network: {self._network.where()}", None)
        for path, described in zpl_local.list_ports():
            self._connection.addItem(
                f"USB: {path} ({described})" if described else f"USB: {path}", path)
            if path == chosen:
                self._connection.setCurrentIndex(self._connection.count() - 1)
        self._connection.blockSignals(False)

    def _local(self) -> Optional[zpl_local.LocalPort]:
        """The chosen USB port, or None for the network printer."""
        path = self._connection.currentData()
        return zpl_local.LocalPort(path) if path else None

    def _where(self) -> str:
        local = self._local()
        return local.path if local else self._network.where()

    def _on_refresh(self):
        self._list_connections()
        self._ask_version()

    def _ask_version(self, _index=None):
        self._current.setText("Current firmware: checking\u2026")
        local = self._local()

        def done(version, error):
            if isinstance(error, printer_io.Cancelled):
                self._current.setText("Current firmware: unknown (cancelled)")
                return
            self._current.setText(
                f"Current firmware: {version}" if version and error is None
                else "Current firmware: unavailable")
            if isinstance(error, PortProblem):
                self._status.setText(str(error))

        self._busy.run(lambda cancel: zpl_firmware.current_firmware(
            local or self._network, cancel=cancel), done)

    def _on_browse(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose Firmware File")
        if not path:
            return
        try:
            size = Path(path).stat().st_size
        except OSError as e:
            show_error(self, f"Could not read {path}: {e}")
            return
        if size == 0:
            show_error(self, f"{Path(path).name} is empty.")
            return
        self._path, self._size = path, size
        self._file.setText(f"{Path(path).name} ({size:,} bytes)")
        self._status.clear()
        self._update_btn.setEnabled(True)

    def _on_update(self):
        if self._path is None or not ask_firmware_update(
                self, Path(self._path).name, self._size, self._where()):
            return
        path, name, local = self._path, Path(self._path).name, self._local()
        self._status.setText(f"Sending {name}\u2026")

        def progress(sent, total):
            self._busy.report_fraction(sent / total)

        started = time.monotonic()

        def done(_result, error):
            if isinstance(error, printer_io.Cancelled):
                self._status.setText(
                    "Cancelled. The printer may have received part of the "
                    "file; check its firmware before relying on it.")
                return
            if error is not None:
                self._status.setText("Firmware update failed.")
                show_error(self, f"Could not send {name}: {error}")
                return
            took = zpl_firmware.format_elapsed(time.monotonic() - started)
            self._status.setText(
                f"Firmware sent in {took}. The printer will flash it and restart; "
                "press Refresh in a minute to see the new version.")

        self._busy.run(lambda cancel: zpl_firmware.upload_firmware(
            local or self._network, path, progress=progress,
            cancel=cancel), done)


class PrinterStatusDialog(QDialog):
    """What the printer says about its own state - faults, what it is working
    on, memory, and how its head is wearing.

    Non-modal and a singleton, like PrinterConsoleDialog and for the same
    reason plus one more: auto-refresh is only useful if the user can keep
    working while it polls. That means it outlives a printer change made
    elsewhere, so address and port are never captured at construction - every
    query reads them from `parent` afresh, the way the console's _on_send does.

    Two queries, not one, matching the core's split (zplcore/printer_status.py):
    the specs are asked once, since a model number and a head-wear figure do not
    move while anyone watches, and only the activity is re-asked on a tick.

    Layout is deliberately the same shape as the GTK window's - one group box
    per section, label and value per row - so the two frontends read
    identically. Neither decides what is bad: the core marks each reading and
    this only colours it (see printer_status.PLAIN/OK/WARN/ERROR).
    """

    # How a reading's level is shown. Chosen to survive a dark theme, which a
    # palette colour would not: these are set on the value label only, never on
    # the whole row, so a marked figure is legible beside an unmarked one.
    _COLOURS = {printer_status.ERROR: '#c0392b', printer_status.WARN: '#b9770e',
                printer_status.OK: '#1e8449'}

    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Printer Status")
        self.resize(460, 620)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self._parent = parent
        self._specs = None
        self._activity = None

        layout = QVBoxLayout(self)
        self._status = QLabel()
        self._status.setWordWrap(True)
        layout.addWidget(self._status)

        # The sections go in a scroll area: how many rows a report has depends
        # on how many faults the printer is reporting, so the height cannot be
        # known here.
        self._sections = QVBoxLayout()
        self._sections.setContentsMargins(0, 0, 0, 0)
        holder = QWidget()
        holder.setLayout(self._sections)
        scroll = QScrollArea()
        scroll.setWidget(holder)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        layout.addWidget(scroll, 1)

        row = QHBoxLayout()
        self._refresh_btn = QPushButton("Refresh")
        self._copy_btn = QPushButton("Copy")
        self._copy_btn.setEnabled(False)
        row.addWidget(self._refresh_btn)
        row.addWidget(self._copy_btn)
        self._auto = QCheckBox("Auto-refresh every")
        row.addWidget(self._auto)
        self._interval = QSpinBox()
        self._interval.setRange(2, 300)
        self._interval.setValue(5)
        self._interval.setSuffix(" s")
        row.addWidget(self._interval)
        row.addStretch(1)
        self._busy = BusyBar((self._refresh_btn,), self._status.setText, self)
        row.addWidget(self._busy)
        layout.addLayout(row)

        close = QDialogButtonBox(QDialogButtonBox.Close, parent=self)
        close.rejected.connect(self.reject)
        add_help(self, "printer_status", close)
        layout.addWidget(close)

        # Parented to the dialog, so it is destroyed with it - unlike GTK's
        # GLib source, which the main loop owns and which has to be removed by
        # hand (see on_printer_status_clicked in gtkui/window.py).
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

        self._refresh_btn.clicked.connect(self.refresh)
        self._copy_btn.clicked.connect(self._on_copy)
        self._auto.toggled.connect(self._on_auto_toggled)
        self._interval.valueChanged.connect(self._on_interval_changed)
        self.refresh()

    def reject(self):
        # stop() before abandon(): reject() runs before deletion, and a timeout
        # already queued would otherwise start one more query into a dialog
        # that is being torn down.
        self._timer.stop()
        self._busy.abandon()
        super().reject()

    # --- refreshing ---------------------------------------------------------

    def _printer(self):
        """The printer in effect now, not the one in effect when this opened."""
        return self._parent.printer_connection

    def refresh(self):
        """Ask everything - the specs as well as the activity."""
        printer = self._printer()
        self._status.setText(f"Asking {printer.where(with_port=False)}...")

        def work(cancel):
            specs = printer_status.query_printer_specs(printer, cancel=cancel)
            self._busy.report(f"Asking {printer.where(with_port=False)} "
                              f"what it is doing...")
            activity = printer_status.query_printer_activity(printer,
                                                             cancel=cancel)
            return specs, activity

        self._busy.run(work, lambda result, error:
                       self._done(result, error, printer))

    def _tick(self):
        """One auto-refresh. Only the activity, and only when nothing else is
        already out - a tick is skipped rather than queued, so a printer slower
        than the interval cannot build a backlog of requests."""
        if self._busy.running:
            return
        printer = self._printer()
        self._busy.run(
            lambda cancel: (self._specs,
                            printer_status.query_printer_activity(
                                printer, cancel=cancel)),
            lambda result, error: self._done(result, error, printer))

    def _done(self, result, error, printer):
        if isinstance(error, printer_io.Cancelled):
            self._status.setText("Cancelled.")
            return
        if error is not None:
            self._status.setText(f"Could not ask the printer: {error}")
            return
        specs, activity = result
        if specs is None and activity is None:
            # Nothing answered at all. The sections already on screen are from
            # an earlier, better moment, so they are left alone rather than
            # cleared - a panel that empties itself when a printer is unplugged
            # loses the reading that would explain why.
            self._status.setText(f"Could not reach the printer at "
                                 f"{printer.where()}.")
            return
        self._specs, self._activity = specs, activity
        self._show()
        self._copy_btn.setEnabled(True)
        unanswered = sorted({name for report in (specs, activity)
                             if report is not None
                             for name in report.unanswered})
        note = (" \u2014 no answer to " + ", ".join(unanswered)) if unanswered else ""
        self._status.setText(f"{printer.where()}{note}")

    def _show(self):
        """Rebuild the section list from the two reports held."""
        while self._sections.count():
            item = self._sections.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        for report in (self._activity, self._specs):
            if report is None:
                continue
            for section in report.sections:
                self._sections.addWidget(self._section_box(section))
        self._sections.addStretch(1)

    def _section_box(self, section):
        box = QGroupBox(section.title)
        form = QFormLayout(box)
        form.setLabelAlignment(Qt.AlignLeft)
        for reading in section.readings:
            colour = self._COLOURS.get(reading.level)
            if reading.fraction is not None:
                form.addRow(QLabel(reading.label + ":"),
                            self._bar(reading, colour))
                continue
            value = QLabel(reading.value)
            value.setWordWrap(True)
            if colour:
                value.setStyleSheet(f"color: {colour}; font-weight: bold;")
            form.addRow(QLabel(reading.label + ":"), value)
        if section.note:
            note = QLabel(section.note)
            note.setWordWrap(True)
            note.setStyleSheet("font-style: italic;")
            form.addRow(note)
        return box

    def _bar(self, reading, colour):
        """A reading the core gave a fraction to: its text, with a slim gauge
        under it.

        The text is a plain label above the bar rather than drawn on it. Set as
        the bar's own format it renders small and, once the fill reaches it, in
        poor contrast against it - and the two toolkits put it in different
        places, Qt inside the trough and GTK above it. Stacked this way the
        figure stays full size and legible in both, and the bar is left to do
        the one thing a bar is better at than a number: showing the proportion
        at a glance.

        The gauge shows what is *used* (printer_status.Reading.fraction) while
        the text says what is free - the pair a disk gauge shows, and the pair
        someone deciding whether a font will fit needs. Thousandths rather than
        percent, so a nearly empty Flash does not round to a bar that reads as
        empty when it is not.
        """
        holder = QWidget()
        stack = QVBoxLayout(holder)
        stack.setContentsMargins(0, 0, 0, 4)
        stack.setSpacing(2)

        text = QLabel(reading.value)
        text.setWordWrap(True)
        if colour:
            text.setStyleSheet(f"color: {colour}; font-weight: bold;")
        stack.addWidget(text)

        bar = QProgressBar()
        bar.setRange(0, 1000)
        bar.setValue(round(reading.fraction * 1000))
        bar.setTextVisible(False)
        bar.setFixedHeight(8)
        if colour:
            # Only the filled part is coloured; the groove keeps the theme's
            # own background so the bar stays legible in a dark one.
            bar.setStyleSheet(
                f"QProgressBar::chunk {{ background-color: {colour}; }}")
        stack.addWidget(bar)
        return holder

    # --- the other controls --------------------------------------------------

    def _on_auto_toggled(self, on):
        if on:
            self._timer.start(self._interval.value() * 1000)
        else:
            self._timer.stop()

    def _on_interval_changed(self, seconds):
        if self._timer.isActive():
            self._timer.start(seconds * 1000)

    def _on_copy(self):
        """The whole report as text, rendered by the core so this and the GTK
        window put identical text on the clipboard."""
        text = printer_status.report_text(self._specs, self._activity)
        QGuiApplication.clipboard().setText(text)
        self._status.setText("Report copied to the clipboard.")


# --- prompts ----------------------------------------------------------------

def ask_template_name(parent, memory: str, default: str) -> Optional[str]:
    """The name to store a template under in `memory`, or None if cancelled.
    A name the printer would not take is asked for again, saying why."""
    name, problem = default, None
    while True:
        dialog = QDialog(parent)
        dialog.setWindowTitle("Upload Template")
        layout = QVBoxLayout(dialog)
        if problem:
            layout.addWidget(QLabel(problem))
        form = QFormLayout()
        layout.addLayout(form)
        edit = QLineEdit(name)
        edit.setObjectName("template_name")
        form.addRow(f"Name in {memory}:", edit)
        buttons = _buttons(dialog, "Upload")
        add_help(dialog, "upload_template", buttons)
        layout.addWidget(buttons)
        accepted = dialog.exec_() == QDialog.Accepted
        name = edit.text().strip().upper()
        dialog.deleteLater()
        if not accepted:
            return None
        problem = workflow.template_name_problem(name)
        if problem is None:
            return name


def ask_overwrite(parent, filepath) -> bool:
    """Whether to replace a file the chooser never asked about."""
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Question)
    box.setWindowTitle("Replace File")
    box.setText(f"A file named \u201c{Path(filepath).name}\u201d already exists.")
    box.setInformativeText("Replacing it will overwrite its contents.")
    replace = box.addButton("Replace", QMessageBox.AcceptRole)
    cancel = box.addButton("Cancel", QMessageBox.RejectRole)
    box.setDefaultButton(cancel)
    box.setEscapeButton(cancel)
    box.exec_()
    return box.clickedButton() is replace


def ask_delete_object(parent, spec: str, printer: Connection) -> bool:
    """Whether to really delete `spec` from the printer.

    A destructive action against real state needs a way back that "just
    don't click it again" cannot offer, since this one cannot be undone
    from here - the same reasoning ask_overwrite already has, just for a
    printer object instead of a local file. Shared by PrinterGraphicsDialog
    and PrinterObjectsDialog, since neither the wording nor the reasoning
    is specific to graphics.
    """
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Question)
    box.setWindowTitle("Delete Object")
    box.setText(f"Delete {spec} from the printer?")
    box.setInformativeText(
        f"This removes it from {printer.where()} itself, not just this "
        "list. It cannot be undone from here.")
    delete = box.addButton("Delete", QMessageBox.DestructiveRole)
    cancel = box.addButton("Cancel", QMessageBox.RejectRole)
    box.setDefaultButton(cancel)
    box.setEscapeButton(cancel)
    box.exec_()
    return box.clickedButton() is delete


def ask_firmware_update(parent, name: str, size: int, where: str) -> bool:
    """Whether to really send firmware to `where` (address:port, or a device
    path) - the printer reboots and an interrupted flash can leave it
    unusable, so it is asked every time."""
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Warning)
    box.setWindowTitle("Update Firmware")
    box.setText(f"Send {name} ({size:,} bytes) to {where}?")
    box.setInformativeText(
        "The printer will flash the file and restart. Do not switch it off "
        "or cancel while it is being written: an interrupted update can "
        "leave the printer unusable.")
    update = box.addButton("Update", QMessageBox.AcceptRole)
    cancel = box.addButton("Cancel", QMessageBox.RejectRole)
    box.setDefaultButton(cancel)
    box.setEscapeButton(cancel)
    box.exec_()
    return box.clickedButton() is update


def ask_unsaved_changes(parent) -> str:
    """'save', 'discard' or 'cancel'. Escape and closing both mean cancel."""
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Question)
    box.setWindowTitle("Unsaved Changes")
    box.setText("The label has unsaved changes.")
    box.setInformativeText("Save them before continuing?")
    save = box.addButton("Save", QMessageBox.AcceptRole)
    discard = box.addButton("Discard Changes", QMessageBox.DestructiveRole)
    cancel = box.addButton("Cancel", QMessageBox.RejectRole)
    box.setDefaultButton(save)
    box.setEscapeButton(cancel)
    box.exec_()
    clicked = box.clickedButton()
    if clicked is save:
        return 'save'
    if clicked is discard:
        return 'discard'
    return 'cancel'


def ask_dpi_rescale(parent, old_dpi: int, printer_dpi: int, assumed: bool,
                    w_in: float, h_in: float) -> str:
    """'rescale', 'keep' or 'cancel'."""
    factor = printer_dpi / old_dpi
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Question)
    box.setWindowTitle("Label Resolution")
    box.setText(f"This label does not say what resolution it was drawn for, "
                f"so {old_dpi} dpi is assumed." if assumed
                else f"This label was designed for {old_dpi} dpi.")
    box.setInformativeText(
        f"The printer is set to {printer_dpi} dpi. Rescaling by {factor:.2f} "
        f"keeps its physical size; keeping the dots as they are makes it print "
        f"{w_in:.1f} x {h_in:.1f} inches.")
    rescale = box.addButton("Rescale", QMessageBox.AcceptRole)
    keep = box.addButton("Keep Dots", QMessageBox.RejectRole)
    cancel = box.addButton("Cancel", QMessageBox.RejectRole)
    box.setDefaultButton(rescale)
    box.setEscapeButton(cancel)
    add_question_help(box, "dpi_rescale")
    box.exec_()
    clicked = box.clickedButton()
    if clicked is rescale:
        return 'rescale'
    if clicked is keep:
        return 'keep'
    return 'cancel'


def ask_font_problem(parent, text: str, detail: str, uploadable: dict) -> str:
    """'upload', 'print' or 'cancel' for a label whose fonts may be missing."""
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Warning)
    box.setWindowTitle("Printer Fonts")
    box.setText(text)
    box.setInformativeText(detail)
    cancel = box.addButton("Cancel", QMessageBox.RejectRole)
    print_anyway = box.addButton("Print Anyway", QMessageBox.AcceptRole)
    upload = box.addButton("Upload & Print", QMessageBox.AcceptRole) if uploadable else None
    # Uploading is the fix; printing anyway is a fallback, so the default is
    # whichever of those is actually available.
    box.setDefaultButton(upload if upload is not None else cancel)
    box.setEscapeButton(cancel)
    add_question_help(box, "font_problem")
    box.exec_()
    clicked = box.clickedButton()
    if upload is not None and clicked is upload:
        return 'upload'
    if clicked is print_anyway:
        return 'print'
    return 'cancel'
