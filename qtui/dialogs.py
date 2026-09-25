"""
Every dialog the designer shows.

Each one either edits the object it is given and reports whether anything
changed, or returns a plain value. None of them touch the undo history or the
status bar - the window owns those, so one action produces one history entry
wherever it was started from.
"""

from pathlib import Path
from typing import Optional

from PIL import Image as PILImage

from PySide2.QtCore import Qt
from PySide2.QtGui import QFont, QFontMetrics, QPixmap
from PySide2.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox,
                               QDialog, QDialogButtonBox, QFileDialog,
                               QFormLayout, QFrame, QHBoxLayout, QLabel,
                               QLineEdit, QListWidget, QMessageBox,
                               QPlainTextEdit, QPushButton, QSpinBox,
                               QDoubleSpinBox, QTreeWidget, QTreeWidgetItem,
                               QVBoxLayout, QWidget)

from zplcore import (fields as zpl_fields, fonts as zpl_fonts,
                     graphic_store, printer_io, printer_objects, textraster,
                     workflow)
from zplcore.model import (BARCODE_CHECK_DIGIT, BARCODE_FEATURES,
                           BARCODE_MODES, BARCODE_ORIENTATIONS,
                           BARCODE_PARAMETERS,
                           BARCODE_SYMBOLOGIES, BARCODE_TEXT_CHOICES,
                           FRAME_COLOURS, ORIENTATIONS,
                           STORED_GRAPHIC_COMMANDS, STORED_GRAPHIC_DEVICES,
                           TEXT_JUSTIFICATIONS, Document, FieldBlock,
                           FrameElement, TextElement)

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


def _buttons(dialog, accept_text="OK"):
    box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=dialog)
    box.button(QDialogButtonBox.Ok).setText(accept_text)
    box.accepted.connect(dialog.accept)
    box.rejected.connect(dialog.reject)
    return box


def _font_device_combo(device: str) -> QComboBox:
    """Which printer memory a font goes to, offered the same way wherever it
    is chosen - Printer Settings, and the Fonts manager's own Upload.

    The same STORED_GRAPHIC_DEVICES the Store Graphic dialog offers, so a
    memory type reads identically everywhere ("E: (Flash)"), and Z: is absent
    for the same reason it is there: ~DY cannot write it and ^ID will not
    delete it.
    """
    combo = QComboBox()
    for label, code in STORED_GRAPHIC_DEVICES:
        combo.addItem(label, code)
    index = combo.findData((device or zpl_fonts.DEFAULT_FONT_DEVICE).upper())
    combo.setCurrentIndex(index if index >= 0 else 0)
    return combo


def _dpi_combo(dpi: int) -> QComboBox:
    """The resolution choice, offered the same way wherever it is edited.

    Label Settings and Printer Settings both write the one printer_dpi setting,
    so they have to offer the same list - including the fallback: a resolution
    the designer does not support can still be the one in force, and must be
    shown rather than silently replaced with a supported one.
    """
    combo = QComboBox()
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


def _show_editor(dialog, apply_edits, on_accept=None):
    """Put an element editor on screen as a non-modal child of the designer.

    The window is transient for the designer but never blocks it, so the
    fields cannot be read back off a returned value the way a modal dialog
    allowed. `finished` is the one signal that covers every way out - OK,
    Cancel, Escape and the window-manager close button alike - so the edit is
    applied there, and the caller hears about it through `on_accept`.
    """
    dialog.setAttribute(Qt.WA_DeleteOnClose)

    def on_finished(result):
        if result != QDialog.Accepted:
            return
        apply_edits()
        if on_accept:
            on_accept()

    dialog.finished.connect(on_finished)
    dialog.show()
    return dialog


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
        row.addStretch(1)
        layout.addLayout(row)

        close = QDialogButtonBox(QDialogButtonBox.Close, parent=self)
        close.rejected.connect(self.reject)
        layout.addWidget(close)

        self._rescan_btn.clicked.connect(lambda: self.refresh(rescan=True))
        self.refresh(rescan=False)

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


# --- element editing --------------------------------------------------------

def _reverse_hint(form):
    """The note under every Reverse (^FR) checkbox, worded the same way in
    both frontends so neither editor promises something the other doesn't.
    """
    hint = QLabel("Inverts whatever's already printed here (e.g. a filled "
                  "frame); prints as normal ink where there's nothing yet.")
    hint.setStyleSheet("color: gray;")
    hint.setWordWrap(True)
    form.addRow("", hint)


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

    number = QSpinBox()
    number.setObjectName("field_number")
    number.setRange(0, zpl_fields.MAX_NUMBER)
    number.setValue(element.field_number or 0)
    form.addRow("Field Number:", number)

    prompt = QLineEdit(element.field_prompt or '')
    prompt.setObjectName("field_prompt")
    prompt.setPlaceholderText("shown on the canvas and on a printer keypad")
    form.addRow("Field Name:", prompt)

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


def edit_text_dialog(parent, element: TextElement, document: Document,
                     on_accept=None) -> QDialog:
    """Edit a text element. `on_accept` runs once OK has changed it."""
    dialog = QDialog(parent)
    dialog.setWindowTitle("Edit Text")
    layout = QVBoxLayout(dialog)
    form = QFormLayout()
    layout.addLayout(form)

    # Multi-line, because ZPL's forced break is two characters a user should
    # never have to spell: Enter here becomes \& on the way out.
    text_edit = QPlainTextEdit(textraster.to_editor(element.text))
    text_edit.setObjectName("text")
    text_edit.setMinimumHeight(4 * QFontMetrics(text_edit.font()).height())
    form.addRow("Text:", text_edit)

    height_spin = QSpinBox()
    height_spin.setRange(8, 500)
    height_spin.setValue(element.font_height)
    form.addRow("Font Height:", height_spin)

    width_spin = QSpinBox()
    width_spin.setRange(8, 500)
    width_spin.setValue(element.font_width)
    form.addRow("Font Width:", width_spin)

    orientation_combo = QComboBox()
    orientation_combo.setObjectName("orientation")
    for label, code in ORIENTATIONS:
        orientation_combo.addItem(label, code)
    turns = [code for _label, code in ORIENTATIONS]
    orientation_combo.setCurrentIndex(turns.index(element.orientation)
                                      if element.orientation in turns else 0)
    form.addRow("Orientation:", orientation_combo)

    fr_check = QCheckBox("Reverse print (^FR)")
    fr_check.setObjectName("reverse_print")
    fr_check.setChecked(element.reverse_print)
    form.addRow("Reverse:", fr_check)
    _reverse_hint(form)

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
    form.addRow("Font:", font_row)

    def on_choose():
        family, path = choose_font_family(dialog, chosen['family'])
        if not family:
            return
        chosen['family'], chosen['path'] = family, path
        font_label.setText(font_display())

    def on_clear():
        # Back to the document font, or the printer's built-in font A.
        chosen['family'], chosen['path'] = None, None
        font_label.setText(font_display())

    choose_btn.clicked.connect(on_choose)
    clear_btn.clicked.connect(on_clear)

    # --- wrapping (^FB) ---
    block = element.block or element.default_block(document.font_path)

    wrap_check = QCheckBox("Wrap the text into a block")
    wrap_check.setObjectName("wrap")
    wrap_check.setChecked(element.block is not None)
    form.addRow("Wrap:", wrap_check)

    block_width = QSpinBox()
    block_width.setRange(10, 2000)
    block_width.setObjectName("block_width")
    block_width.setValue(block.width)
    form.addRow("Wrap Width:", block_width)

    max_lines = QSpinBox()
    max_lines.setRange(1, 64)
    max_lines.setObjectName("max_lines")
    max_lines.setValue(block.max_lines)
    form.addRow("Max Lines:", max_lines)

    spacing_spin = QSpinBox()
    spacing_spin.setRange(-100, 100)
    spacing_spin.setObjectName("line_spacing")
    spacing_spin.setValue(block.line_spacing)
    form.addRow("Line Spacing:", spacing_spin)

    justify_combo = QComboBox()
    justify_combo.setObjectName("justification")
    for label, code in TEXT_JUSTIFICATIONS:
        justify_combo.addItem(label, code)
    codes = [code for _label, code in TEXT_JUSTIFICATIONS]
    justify_combo.setCurrentIndex(codes.index(block.justification)
                                  if block.justification in codes else 0)
    form.addRow("Justification:", justify_combo)

    indent_spin = QSpinBox()
    indent_spin.setRange(0, 2000)
    indent_spin.setObjectName("indent")
    indent_spin.setValue(block.indent)
    form.addRow("Indent:", indent_spin)

    block_fields = (block_width, max_lines, spacing_spin, justify_combo,
                    indent_spin)

    def sync_block_fields():
        for field in block_fields:
            field.setEnabled(wrap_check.isChecked())

    sync_block_fields()
    wrap_check.stateChanged.connect(sync_block_fields)

    layout.addWidget(_buttons(dialog))

    def _apply():
        element.text = textraster.from_editor(text_edit.toPlainText())
        element.font_height = height_spin.value()
        element.font_width = width_spin.value()
        element.orientation = orientation_combo.currentData()
        element.height = element.font_height
        element.reverse_print = fr_check.isChecked()

        if wrap_check.isChecked():
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
        elif textraster.FORCED_BREAK in element.text:
            # A break typed into an element that never had a block still
            # needs one, for the same reason. Sized to the longest line,
            # so nothing moves.
            element.block = element.default_block(document.font_path)

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
        document.sync_text_width(element)

    return _show_editor(dialog, _apply, on_accept)


def edit_time_dialog(parent, element: TextElement, document: Document,
                     on_accept=None) -> QDialog:
    """Edit a clock field (^FC). `on_accept` runs once OK has changed it.

    Deliberately smaller than edit_text_dialog: no wrap/block section (a
    clock stamp is one short line, not a paragraph) and no Data Source
    selector - this dialog *is* the ^FC source. edit_text_dialog carries no
    field-source mechanism of its own at all any more: ^FN, ^SN and ^FC each
    moved out to their own dialog. The one on/off control here is the
    checkbox at the bottom: unticking it turns the element back into a plain
    static text field, and the next double-click opens the regular Text
    editor instead of this one.
    """
    dialog = QDialog(parent)
    dialog.setWindowTitle("Edit Time Field")
    layout = QVBoxLayout(dialog)
    form = QFormLayout()
    layout.addLayout(form)

    text_edit = QLineEdit(element.text)
    text_edit.setObjectName("text")
    form.addRow("Format:", text_edit)

    hint = QLabel("e.g. %m/%d/%y → date, %H:%M:%S → time")
    hint.setStyleSheet("color: gray;")
    form.addRow("", hint)

    height_spin = QSpinBox()
    height_spin.setRange(8, 500)
    height_spin.setValue(element.font_height)
    form.addRow("Font Height:", height_spin)

    width_spin = QSpinBox()
    width_spin.setRange(8, 500)
    width_spin.setValue(element.font_width)
    form.addRow("Font Width:", width_spin)

    orientation_combo = QComboBox()
    orientation_combo.setObjectName("orientation")
    for label, code in ORIENTATIONS:
        orientation_combo.addItem(label, code)
    turns = [code for _label, code in ORIENTATIONS]
    orientation_combo.setCurrentIndex(turns.index(element.orientation)
                                      if element.orientation in turns else 0)
    form.addRow("Orientation:", orientation_combo)

    fr_check = QCheckBox("Reverse print (^FR)")
    fr_check.setObjectName("reverse_print")
    fr_check.setChecked(element.reverse_print)
    form.addRow("Reverse:", fr_check)
    _reverse_hint(form)

    clock_check = QCheckBox("Comes from the printer's clock (^FC)")
    clock_check.setObjectName("clock_format")
    clock_check.setChecked(element.clock_format)
    form.addRow("Clock:", clock_check)

    layout.addWidget(_buttons(dialog))

    def _apply():
        element.text = text_edit.text()
        element.font_height = height_spin.value()
        element.font_width = width_spin.value()
        element.orientation = orientation_combo.currentData()
        element.height = element.font_height
        element.reverse_print = fr_check.isChecked()
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

    return _show_editor(dialog, _apply, on_accept)


def edit_serial_dialog(parent, element: TextElement, document: Document,
                       on_accept=None) -> QDialog:
    """Edit a serialized field (^SN). `on_accept` runs once OK has changed it.

    Deliberately smaller than edit_text_dialog: no wrap/block section (a
    serial number is one short line, not a paragraph) and no Data Source
    selector - this dialog *is* the ^SN source. edit_text_dialog carries no
    field-source mechanism of its own at all any more: ^FN, ^SN and ^FC each
    moved out to their own dialog. The one on/off control here is the
    checkbox at the bottom: unticking it turns the element back into a plain
    static text field, and the next double-click opens the regular Text
    editor instead of this one.
    """
    dialog = QDialog(parent)
    dialog.setWindowTitle("Edit Serial Field")
    layout = QVBoxLayout(dialog)
    form = QFormLayout()
    layout.addLayout(form)

    text_edit = QLineEdit(element.text)
    text_edit.setObjectName("text")
    form.addRow("Start Value:", text_edit)

    increment = QSpinBox()
    increment.setObjectName("serial_increment")
    increment.setRange(-999999, 999999)
    increment.setValue(element.serial_increment
                       if element.serial_increment is not None else 1)
    form.addRow("Increment:", increment)

    leading_zero = QCheckBox("Add leading zeros")
    leading_zero.setObjectName("serial_leading_zero")
    leading_zero.setChecked(element.serial_leading_zero)
    form.addRow("", leading_zero)

    height_spin = QSpinBox()
    height_spin.setRange(8, 500)
    height_spin.setValue(element.font_height)
    form.addRow("Font Height:", height_spin)

    width_spin = QSpinBox()
    width_spin.setRange(8, 500)
    width_spin.setValue(element.font_width)
    form.addRow("Font Width:", width_spin)

    orientation_combo = QComboBox()
    orientation_combo.setObjectName("orientation")
    for label, code in ORIENTATIONS:
        orientation_combo.addItem(label, code)
    turns = [code for _label, code in ORIENTATIONS]
    orientation_combo.setCurrentIndex(turns.index(element.orientation)
                                      if element.orientation in turns else 0)
    form.addRow("Orientation:", orientation_combo)

    fr_check = QCheckBox("Reverse print (^FR)")
    fr_check.setObjectName("reverse_print")
    fr_check.setChecked(element.reverse_print)
    form.addRow("Reverse:", fr_check)
    _reverse_hint(form)

    serial_check = QCheckBox("Auto-increments each print (^SN)")
    serial_check.setObjectName("serial_format")
    serial_check.setChecked(element.serial_increment is not None)
    form.addRow("Serial:", serial_check)

    layout.addWidget(_buttons(dialog))

    def _apply():
        element.text = text_edit.text()
        element.font_height = height_spin.value()
        element.font_width = width_spin.value()
        element.orientation = orientation_combo.currentData()
        element.height = element.font_height
        element.reverse_print = fr_check.isChecked()
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

    return _show_editor(dialog, _apply, on_accept)


def edit_numbered_dialog(parent, element: TextElement, document: Document,
                         on_accept=None) -> QDialog:
    """Edit a numbered field (^FN). `on_accept` runs once OK has changed it.

    Deliberately smaller than edit_text_dialog: no wrap/block section - a
    numbered field's own literal, when it has one, is short in every fixture
    this designer ships with (the manual's own stored_format.zpl example
    included) - and no Data Source selector, since this dialog *is* the ^FN
    source. The one on/off control is the checkbox at the bottom: unticking
    it turns the element back into a plain static text field, and the next
    double-click opens the regular Text editor instead of this one.

    ^FN is still available on a barcode, through `_field_number_rows` in
    edit_barcode_dialog - unlike ^SN/^FC, a recalled stored-format barcode is
    common and already tested, so it keeps a row there rather than moving
    out entirely.
    """
    dialog = QDialog(parent)
    dialog.setWindowTitle("Edit Numbered Field")
    layout = QVBoxLayout(dialog)
    form = QFormLayout()
    layout.addLayout(form)

    text_edit = QLineEdit(element.text)
    text_edit.setObjectName("text")
    text_edit.setPlaceholderText("optional - shared with every other field "
                                 "carrying the same number")
    form.addRow("Text:", text_edit)

    number = QSpinBox()
    number.setObjectName("field_number")
    number.setRange(0, zpl_fields.MAX_NUMBER)
    number.setValue(element.field_number or 0)
    form.addRow("Field Number:", number)

    prompt = QLineEdit(element.field_prompt or '')
    prompt.setObjectName("field_prompt")
    prompt.setPlaceholderText("shown on the canvas and on a printer keypad")
    form.addRow("Field Name:", prompt)

    height_spin = QSpinBox()
    height_spin.setRange(8, 500)
    height_spin.setValue(element.font_height)
    form.addRow("Font Height:", height_spin)

    width_spin = QSpinBox()
    width_spin.setRange(8, 500)
    width_spin.setValue(element.font_width)
    form.addRow("Font Width:", width_spin)

    orientation_combo = QComboBox()
    orientation_combo.setObjectName("orientation")
    for label, code in ORIENTATIONS:
        orientation_combo.addItem(label, code)
    turns = [code for _label, code in ORIENTATIONS]
    orientation_combo.setCurrentIndex(turns.index(element.orientation)
                                      if element.orientation in turns else 0)
    form.addRow("Orientation:", orientation_combo)

    fr_check = QCheckBox("Reverse print (^FR)")
    fr_check.setObjectName("reverse_print")
    fr_check.setChecked(element.reverse_print)
    form.addRow("Reverse:", fr_check)
    _reverse_hint(form)

    variable_check = QCheckBox("Data comes from a numbered field (^FN)")
    variable_check.setObjectName("variable")
    variable_check.setChecked(element.field_number is not None)
    form.addRow("Variable:", variable_check)

    layout.addWidget(_buttons(dialog))

    def _apply():
        element.text = text_edit.text()
        element.font_height = height_spin.value()
        element.font_width = width_spin.value()
        element.orientation = orientation_combo.currentData()
        element.height = element.font_height
        element.reverse_print = fr_check.isChecked()
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

    return _show_editor(dialog, _apply, on_accept)


def edit_frame_dialog(parent, element, on_accept=None) -> QDialog:
    """Edit a frame. `on_accept` runs once OK has changed it."""
    dialog = QDialog(parent)
    dialog.setWindowTitle("Edit Frame")
    layout = QVBoxLayout(dialog)
    form = QFormLayout()
    layout.addLayout(form)

    width_spin = QSpinBox()
    width_spin.setRange(10, 800)
    width_spin.setValue(element.width)
    form.addRow("Width:", width_spin)

    height_spin = QSpinBox()
    height_spin.setRange(10, 1200)
    height_spin.setValue(element.height)
    form.addRow("Height:", height_spin)

    thickness_spin = QSpinBox()
    form.addRow("Thickness:", thickness_spin)

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

    rounding_spin = QSpinBox()
    rounding_spin.setObjectName("rounding")
    rounding_spin.setRange(0, FrameElement.MAX_ROUNDING)
    rounding_spin.setValue(element.rounding)
    form.addRow("Corner Rounding:", rounding_spin)

    fr_check = QCheckBox("Reverse print (^FR)")
    fr_check.setObjectName("reverse_print")
    fr_check.setChecked(element.reverse_print)
    form.addRow("Reverse:", fr_check)
    _reverse_hint(form)

    layout.addWidget(_buttons(dialog))

    def _apply():
        element.width = width_spin.value()
        element.height = height_spin.value()
        element.thickness = thickness_spin.value()
        element.colour = colour_combo.currentData()
        element.rounding = rounding_spin.value()
        element.reverse_print = fr_check.isChecked()

    return _show_editor(dialog, _apply, on_accept)


def edit_stored_graphic_dialog(parent, element, on_accept=None) -> QDialog:
    """Edit a ^XG/^IM reference. `on_accept` runs once OK has changed it.

    ^XG/^IM name an image the printer holds, not one this file carries the
    bytes for - see zplcore/graphic_store.py. Editing this element only ever
    changes which name it recalls.
    """
    dialog = QDialog(parent)
    dialog.setWindowTitle("Edit Stored Graphic")
    layout = QVBoxLayout(dialog)
    form = QFormLayout()
    layout.addLayout(form)

    command_combo = QComboBox()
    command_combo.setObjectName("command")
    for label, code in STORED_GRAPHIC_COMMANDS:
        command_combo.addItem(label, code)
    command_codes = [code for _label, code in STORED_GRAPHIC_COMMANDS]
    command_combo.setCurrentIndex(command_codes.index(element.command)
                                  if element.command in command_codes else 0)
    form.addRow("Command:", command_combo)

    device, name, ext = graphic_store.split_device_spec(element.device_spec)
    device_combo = QComboBox()
    device_combo.setObjectName("device")
    for label, code in STORED_GRAPHIC_DEVICES:
        device_combo.addItem(label, code)
    device_codes = [code for _label, code in STORED_GRAPHIC_DEVICES]
    device_combo.setCurrentIndex(device_codes.index(device)
                                 if device in device_codes else 0)
    form.addRow("Device:", device_combo)

    name_edit = QLineEdit(name)
    name_edit.setObjectName("name")
    name_edit.setMaxLength(8)
    form.addRow("Name:", name_edit)

    ext_edit = QLineEdit(ext)
    ext_edit.setObjectName("extension")
    form.addRow("Extension:", ext_edit)

    mag_x_spin = QSpinBox()
    mag_x_spin.setRange(1, 10)
    mag_x_spin.setValue(element.mag_x)
    form.addRow("Magnification X:", mag_x_spin)

    mag_y_spin = QSpinBox()
    mag_y_spin.setRange(1, 10)
    mag_y_spin.setValue(element.mag_y)
    form.addRow("Magnification Y:", mag_y_spin)

    def sync_magnification_enabled():
        # ^IM has no magnification of its own - always 1,1.
        is_xg = command_combo.currentData() == 'XG'
        mag_x_spin.setEnabled(is_xg)
        mag_y_spin.setEnabled(is_xg)

    sync_magnification_enabled()
    command_combo.currentIndexChanged.connect(sync_magnification_enabled)

    layout.addWidget(_buttons(dialog))

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

    return _show_editor(dialog, _apply, on_accept)


def edit_barcode_dialog(parent, element, on_accept=None) -> QDialog:
    """Edit a barcode. `on_accept` runs once OK has changed it."""
    dialog = QDialog(parent)
    dialog.setWindowTitle("Edit Barcode")
    layout = QVBoxLayout(dialog)
    form = QFormLayout()
    layout.addLayout(form)

    symbology_combo = QComboBox()
    for label, code in BARCODE_SYMBOLOGIES:
        symbology_combo.addItem(label, code)
    symbology_combo.setCurrentIndex(
        [c for _l, c in BARCODE_SYMBOLOGIES].index(element.symbology))
    form.addRow("Symbology:", symbology_combo)

    value_edit = QLineEdit(element.barcode_value)
    form.addRow("Barcode Value:", value_edit)

    height_spin = QSpinBox()
    height_spin.setRange(20, 300)
    height_spin.setValue(element.bar_height)
    form.addRow("Bar Height:", height_spin)
    height_label = form.labelForField(height_spin)

    module_spin = QSpinBox()
    module_spin.setRange(1, 20)
    module_spin.setValue(element.module_width)
    form.addRow("Module Width:", module_spin)
    module_label = form.labelForField(module_spin)

    # The rows a symbology adds for its own parameters - a QR code's error
    # correction and mask, and so on. Built from the same catalogue the
    # parser and the model read, so a symbology cannot arrive with a
    # parameter no editor can reach. They go here, after the value, so the
    # rows above keep the positions other code looks for them in.
    extra_rows = {}
    for symbology_key, rows in BARCODE_PARAMETERS.items():
        for attribute, label, choices in rows:
            if attribute in extra_rows:
                continue
            combo = QComboBox()
            for choice_label, value in choices:
                combo.addItem(choice_label, value)
            form.addRow(label + ":", combo)
            extra_rows[attribute] = (combo, form.labelForField(combo), choices)

    def _load_extra_rows():
        """Show each row the chosen symbology has, set to its value."""
        wanted = dict((attribute, True) for attribute, _l, _c
                      in BARCODE_PARAMETERS.get(symbology_combo.currentData(), ()))
        for attribute, (combo, label, choices) in extra_rows.items():
            shown = attribute in wanted
            combo.setVisible(shown)
            label.setVisible(shown)
            if not shown:
                continue
            current = getattr(element, attribute, None)
            values = [value for _l, value in choices]
            combo.setCurrentIndex(values.index(current) if current in values else 0)

    ratio_spin = QDoubleSpinBox()
    ratio_spin.setRange(2.0, 3.0)
    ratio_spin.setDecimals(1)
    ratio_spin.setSingleStep(0.1)
    ratio_spin.setValue(element.ratio)
    form.addRow("Ratio:", ratio_spin)

    orientation_combo = QComboBox()
    for label, code in BARCODE_ORIENTATIONS:
        orientation_combo.addItem(label, code)
    current = (element.orientation or 'N').upper()
    orientation_combo.setCurrentIndex(
        max(0, [c for _l, c in BARCODE_ORIENTATIONS].index(current)
            if current in [c for _l, c in BARCODE_ORIENTATIONS] else 0))
    form.addRow("Orientation:", orientation_combo)

    text_combo = QComboBox()
    for label, flags in BARCODE_TEXT_CHOICES:
        text_combo.addItem(label, flags)
    text_combo.setCurrentIndex(
        [flags for _l, flags in BARCODE_TEXT_CHOICES].index(
            (element.show_text, element.text_above))
        if (element.show_text, element.text_above)
        in [flags for _l, flags in BARCODE_TEXT_CHOICES] else 0)
    form.addRow("Value Text:", text_combo)

    font_spin = QSpinBox()
    font_spin.setRange(6, 200)
    font_spin.setValue(int((element.font or element.DEFAULT_FONT)[1]))
    form.addRow("Text Height:", font_spin)

    check_combo = QComboBox()
    for label, flag in BARCODE_CHECK_DIGIT:
        check_combo.addItem(label, flag)
    check_combo.setCurrentIndex(1 if element.check_digit else 0)
    form.addRow("Check Digit:", check_combo)
    check_label = form.labelForField(check_combo)

    mode_combo = QComboBox()
    for label, code in BARCODE_MODES:
        mode_combo.addItem(label, code)
    mode_combo.setCurrentIndex(
        [c for _l, c in BARCODE_MODES].index(element.mode)
        if element.mode in [c for _l, c in BARCODE_MODES] else 0)
    form.addRow("Mode:", mode_combo)
    mode_label = form.labelForField(mode_combo)
    ratio_label = form.labelForField(ratio_spin)

    fr_check = QCheckBox("Reverse print (^FR)")
    fr_check.setObjectName("reverse_print")
    fr_check.setChecked(element.reverse_print)
    form.addRow("Reverse:", fr_check)
    _reverse_hint(form)

    apply_field_number = _field_number_rows(form, element)

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
        _load_extra_rows()

    symbology_combo.currentIndexChanged.connect(_update_visible_rows)
    _update_visible_rows()

    layout.addWidget(_buttons(dialog))

    def _apply():
        element.symbology = symbology_combo.currentData()
        element.barcode_value = value_edit.text()
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
            combo = extra_rows[attribute][0]
            setattr(element, attribute, combo.currentData())
        element.check_digit = check_combo.currentData()
        element.mode = mode_combo.currentData()
        element.reverse_print = fr_check.isChecked()
        apply_field_number(element)
        if element.show_text:
            # With the line switched on, name the font it prints in rather than
            # leaving it to whatever the printer happens to have selected.
            code = element.font[0] if element.font else element.DEFAULT_FONT[0]
            element.font = (code, font_spin.value(), font_spin.value())
        element.sync_box()

    return _show_editor(dialog, _apply, on_accept)


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
    for label, code in STORED_GRAPHIC_DEVICES:
        device_combo.addItem(label, code)
    form.addRow("Device:", device_combo)

    name_edit = QLineEdit("LOGO")
    name_edit.setMaxLength(8)
    form.addRow("Name:", name_edit)

    ext_edit = QLineEdit("GRF")
    form.addRow("Extension:", ext_edit)

    layout.addWidget(_buttons(dialog))

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

    layout.addWidget(_buttons(dialog))
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
    name_edit.setMaxLength(8)
    form.addRow("Name (on E:):", name_edit)

    ext_edit = QLineEdit(default_ext)
    form.addRow("Extension:", ext_edit)

    layout.addWidget(_buttons(dialog))

    if dialog.exec_() != QDialog.Accepted:
        return None
    name = name_edit.text().strip() or 'UNKNOWN'
    ext = ext_edit.text().strip() or 'DAT'
    return name, ext


# --- label and printer ------------------------------------------------------

def label_size_dialog(parent, document: Document, dpi: int):
    """New (width, height, dpi, width_in, height_in, transform), or None.

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

    quantity_spin = QSpinBox()
    quantity_spin.setObjectName("quantity")
    quantity_spin.setRange(1, 99999999)
    quantity_spin.setValue(document.print_quantity)
    form.addRow("Copies (^PQ):", quantity_spin)

    # ^LH: the origin every field is placed from. Its use is preprinted stock -
    # moving the printable area below a pre-printed header - so it belongs
    # beside the size rather than among the printer settings.
    home_x = QSpinBox()
    home_x.setObjectName("home_x")
    home_x.setRange(0, 32000)
    home_x.setValue(document.transform.home[0])
    form.addRow("Home X (dots):", home_x)

    home_y = QSpinBox()
    home_y.setObjectName("home_y")
    home_y.setRange(0, 32000)
    home_y.setValue(document.transform.home[1])
    form.addRow("Home Y (dots):", home_y)

    # How the finished label is laid down, rather than where a field sits on it
    invert_check = QCheckBox("Print upside down (^PO)")
    invert_check.setObjectName("invert")
    invert_check.setChecked(document.transform.invert)
    form.addRow("Orientation:", invert_check)

    mirror_check = QCheckBox("Mirror left to right (^PM)")
    mirror_check.setObjectName("mirror")
    mirror_check.setChecked(document.transform.mirror)
    form.addRow("Mirror:", mirror_check)

    reverse_check = QCheckBox("Reverse fields, white on black (^LR)")
    reverse_check.setObjectName("reverse")
    reverse_check.setChecked(document.transform.reverse)
    form.addRow("Reverse:", reverse_check)

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

    layout.addWidget(_buttons(dialog))
    if dialog.exec_() != QDialog.Accepted:
        return None
    # Copied, not mutated: the document's own transform is what an undo
    # snapshot may still be holding.
    transform = document.transform.copy()
    transform.home = (home_x.value(), home_y.value())
    transform.invert = invert_check.isChecked()
    transform.mirror = mirror_check.isChecked()
    transform.reverse = reverse_check.isChecked()
    return to_dots() + (chosen_dpi(), width_spin.value(), height_spin.value(),
                        transform, quantity_spin.value())


def printer_settings_dialog(parent, address: str, port: int, dpi: int,
                            font_device: str = None,
                            title: str = "Printer Settings", default=None):
    """New (address, port, dpi, font_device), or None if cancelled.

    `default`, when given, is the persisted (address, port, dpi, font_device)
    to offer via a "Use Default" button - for the session-only picker, which
    is opened with whatever printer is currently in effect rather than the
    default.

    Font memory belongs here rather than beside each text element: which
    memory a printer keeps its fonts in is a property of the printer being
    deployed to, not of one field on one label.
    """
    dialog = QDialog(parent)
    dialog.setWindowTitle(title)
    layout = QVBoxLayout(dialog)
    form = QFormLayout()
    layout.addLayout(form)

    address_edit = QLineEdit(address)
    form.addRow("Address:", address_edit)

    port_spin = QSpinBox()
    port_spin.setRange(1, 65535)
    port_spin.setValue(port)
    form.addRow("Port:", port_spin)

    dpi_combo = _dpi_combo(dpi)
    form.addRow("DPI:", dpi_combo)

    font_device_combo = _font_device_combo(font_device)
    form.addRow("Font memory:", font_device_combo)

    test_row = QHBoxLayout()
    test_btn = QPushButton("Test Connection")
    test_row.addWidget(test_btn)
    test_row.addStretch(1)
    layout.addLayout(test_row)
    result = QLabel()
    result.setWordWrap(True)
    layout.addWidget(result)

    def set_result(colour, text):
        result.setStyleSheet(f"color: {colour};")
        result.setText(text)

    buttons = _buttons(dialog)
    # OK is withheld while a probe is out so a half-finished one can't be
    # accepted as an answer.
    busy = BusyBar((test_btn, buttons.button(QDialogButtonBox.Ok)),
                   lambda text: set_result("gray", text), dialog)
    test_row.addWidget(busy)

    def on_test():
        addr = address_edit.text().strip()
        prt = port_spin.value()
        if not addr:
            set_result("red", "Address is required")
            return
        set_result("gray", f"Connecting to {addr}:{prt}…")
        connected = f"✓ Connected to {addr}:{prt}"

        def probe(cancel):
            # Connect and send nothing: reachability first, on its own, so
            # an unreachable printer reads as that rather than as "no dpi".
            printer_io.send(addr, prt, b'', 5, cancel=cancel)
            busy.report(f"{connected} — asking its resolution…")
            return zpl_fonts.query_printer_dpi(addr, prt, cancel=cancel)

        def done(reported, error):
            if isinstance(error, printer_io.Cancelled):
                set_result("gray", "Test cancelled.")
            elif error is not None:
                set_result("red", f"✗ {error}")
            elif reported is None:
                # No answer must leave the manual setting alone rather than
                # substituting a guess.
                set_result("orange", f"{connected}, but it did not report its "
                                     f"resolution; set the DPI manually.")
            elif reported in zpl_fonts.SUPPORTED_DPI:
                dpi_combo.setCurrentIndex(list(zpl_fonts.SUPPORTED_DPI).index(reported))
                set_result("green", f"{connected} — {reported} dpi")
            else:
                set_result("orange", f"{connected} — reports {reported} dpi, "
                                     f"which the designer does not support.")

        busy.run(probe, done)

    test_btn.clicked.connect(on_test)

    if default is not None:
        default_btn = QPushButton("Use Default")
        layout.addWidget(default_btn)

        def on_use_default():
            def_address, def_port, def_dpi, def_font_device = default
            idx = font_device_combo.findData(def_font_device)
            if idx >= 0:
                font_device_combo.setCurrentIndex(idx)
            address_edit.setText(def_address)
            port_spin.setValue(def_port)
            if def_dpi in zpl_fonts.SUPPORTED_DPI:
                dpi_combo.setCurrentIndex(list(zpl_fonts.SUPPORTED_DPI).index(def_dpi))
            else:
                idx = dpi_combo.findText(str(def_dpi))
                if idx < 0:
                    dpi_combo.addItem(str(def_dpi))
                    idx = dpi_combo.count() - 1
                dpi_combo.setCurrentIndex(idx)

        default_btn.clicked.connect(on_use_default)

    layout.addWidget(buttons)

    accepted = dialog.exec_() == QDialog.Accepted
    busy.abandon()
    if not accepted:
        return None
    new_address = address_edit.text().strip()
    if not new_address:
        show_error(parent, "Printer address cannot be empty.")
        return None
    return (new_address, port_spin.value(), _dpi_from(dpi_combo, dpi),
            font_device_combo.currentData())


class PrinterFontsDialog(QDialog):
    """The fonts stored on the printer, with upload, delete and refresh, plus
    a read-only reference list of the printer's built-in resident fonts."""

    def __init__(self, parent, address: str, port: int, on_uploaded=None,
                 font_device: str = zpl_fonts.DEFAULT_FONT_DEVICE):
        super().__init__(parent)
        self.setWindowTitle("Printer Fonts")
        self.resize(480, 520)
        self._address, self._port = address, port
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
        self._status.setText(f"Listing fonts on {self._address}...")
        self._resident_status.clear()

        def query(cancel):
            fonts = zpl_fonts.query_printer_fonts(self._address, self._port,
                                                  cancel=cancel)
            self._busy.report("Asking which built-in fonts it has...")
            detected = zpl_fonts.query_resident_fonts(self._address, self._port,
                                                      cancel=cancel)
            return fonts, detected

        def done(result, error):
            if isinstance(error, printer_io.Cancelled):
                self._status.setText("Listing cancelled.")
                return
            fonts, detected = (None, None) if error is not None else result
            if fonts is None:
                # Not the same as "no fonts": say the printer could not be
                # asked, rather than showing an empty list as if it had answered.
                self._status.setText(f"Could not reach the printer at "
                                     f"{self._address}:{self._port}.")
            else:
                self._font_specs = sorted(fonts)
                for spec in self._font_specs:
                    QTreeWidgetItem(self._list,
                                    [spec, graphic_store.device_name(spec)])
                self._delete_btn.setEnabled(bool(fonts))
                self._status.setText(f"{len(fonts)} font(s) on {self._address}"
                                     if fonts else "No fonts stored on the printer.")
            self._show_resident(detected)

        self._busy.run(query, done)

    def _show_resident(self, detected):
        self._resident_list.clear()
        if detected:
            self._resident_status.setText(
                f"Reported by the printer at {self._address}.")
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
            self._address, self._port, path, name, device, cancel=cancel), done)

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
            self._address, self._port, name, device, cancel=cancel), done)


class PrinterGraphicsDialog(QDialog):
    """View, store, retrieve and delete graphics on the real printer at
    `address`:`port` - whichever one is actually in effect this session
    (the caller passes self.printer_address/self.printer_port, which a
    session override moves without touching the persisted default), the
    same target PrinterFontsDialog already uses.

    Modelled closely on PrinterFontsDialog: refresh() queries the printer
    live and reports when it cannot be reached, exactly as that one does for
    ^HW. The one thing this keeps that Fonts has no need for is
    graphic_store's in-session local cache, which ^XG/^IM/^IL already read
    from - Store and Retrieve mirror a successful network result into it
    (the same way upload_font's caller also registers the font locally),
    purely so an already-placed reference on the canvas updates without a
    second round trip to the printer.
    """

    def __init__(self, parent, address: str, port: int, on_changed=None):
        super().__init__(parent)
        self.setWindowTitle("Printer Graphics")
        self.resize(460, 340)
        self._address, self._port = address, port
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
        self._status.setText(f"Listing graphics on {self._address}...")

        def done(specs, error):
            if isinstance(error, printer_io.Cancelled):
                self._status.setText("Listing cancelled.")
                return
            if specs is None or error is not None:
                self._status.setText(f"Could not reach the printer at "
                                     f"{self._address}:{self._port}.")
                return
            self._entries = specs
            for spec in self._entries:
                cached = graphic_store.recall(spec)
                suffix = f" ({cached.width}×{cached.height})" if cached else ""
                self._list.addItem(f"{spec}{suffix}")
            self._status.setText(
                f"{len(self._entries)} graphic(s) on {self._address}"
                if self._entries else f"No graphics on {self._address}.")

        self._busy.run(lambda cancel: graphic_store.query_printer_graphics(
            self._address, self._port, cancel=cancel), done)

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
            self._address, self._port, spec, image, cancel=cancel), done)

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
            self._address, self._port, spec, cancel=cancel), done)

    def _on_delete(self):
        spec = self._selected_entry()
        if spec is None:
            return
        if not ask_delete_object(self, spec, self._address, self._port):
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
            self._address, self._port, spec, cancel=cancel), done)


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

    def __init__(self, parent, address: str, port: int, on_changed=None):
        super().__init__(parent)
        self.setWindowTitle("Printer Objects")
        self.resize(460, 340)
        self._address, self._port = address, port
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
        self._status.setText(f"Listing objects on {self._address}...")

        def done(specs, error):
            if isinstance(error, printer_io.Cancelled):
                self._status.setText("Listing cancelled.")
                return
            if specs is None or error is not None:
                self._status.setText(f"Could not reach the printer at "
                                     f"{self._address}:{self._port}.")
                return
            for spec in specs:
                QTreeWidgetItem(self._list,
                                [spec, graphic_store.device_name(spec)])
            self._status.setText(f"{len(specs)} object(s) on {self._address}"
                                 if specs else "No objects on the printer.")

        self._busy.run(lambda cancel: printer_objects.query_printer_objects(
            self._address, self._port, cancel=cancel), done)

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
            self._address, self._port, name, ext, data, cancel=cancel), done)

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
            self._address, self._port, spec, cancel=cancel), done)

    def _on_delete(self):
        spec = self._selected_spec()
        if spec is None:
            return
        if not ask_delete_object(self, spec, self._address, self._port):
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
            self._address, self._port, spec, cancel=cancel), done)


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
        layout.addWidget(close)

        self._send_btn.clicked.connect(self._on_send)

    def reject(self):
        self._busy.abandon()
        super().reject()

    def _on_send(self):
        text = self._input.toPlainText()
        if not text.strip():
            return
        address, port = self._parent.printer_address, self._parent.printer_port

        def done(reply, error):
            if isinstance(error, printer_io.Cancelled):
                self._log.appendPlainText(f"> {text}\n(cancelled)\n")
                return
            if error is not None:
                show_error(self, f"Could not send command: {error}")
                return
            self._log.appendPlainText(f"> {text}\n{reply or '(no reply)'}\n")
            self._input.clear()

        self._busy.run(lambda cancel: printer_io.send_command(
            address, port, text, cancel=cancel), done)


# --- prompts ----------------------------------------------------------------

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


def ask_delete_object(parent, spec: str, address: str, port: int) -> bool:
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
        f"This removes it from {address}:{port} itself, not just this "
        "list. It cannot be undone from here.")
    delete = box.addButton("Delete", QMessageBox.DestructiveRole)
    cancel = box.addButton("Cancel", QMessageBox.RejectRole)
    box.setDefaultButton(cancel)
    box.setEscapeButton(cancel)
    box.exec_()
    return box.clickedButton() is delete


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
    box.exec_()
    clicked = box.clickedButton()
    if upload is not None and clicked is upload:
        return 'upload'
    if clicked is print_anyway:
        return 'print'
    return 'cancel'
