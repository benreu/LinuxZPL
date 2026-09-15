"""
Every dialog the designer shows.

Each one either edits the object it is given and reports whether anything
changed, or returns a plain value. None of them touch the undo history or the
status bar - the window owns those, so one action produces one history entry
wherever it was started from.
"""

import socket
from pathlib import Path
from typing import Optional

from PySide2.QtCore import Qt
from PySide2.QtGui import QFont, QFontMetrics
from PySide2.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox,
                               QDialog, QDialogButtonBox, QFileDialog,
                               QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QMessageBox, QPlainTextEdit,
                               QPushButton, QSpinBox, QDoubleSpinBox,
                               QVBoxLayout, QWidget)

from zplcore import fields as zpl_fields, fonts as zpl_fonts, textraster
from zplcore.model import (BARCODE_CHECK_DIGIT, BARCODE_MODES,
                           BARCODE_ORIENTATIONS, BARCODE_TEXT_CHOICES,
                           FRAME_COLOURS, ORIENTATIONS,
                           TEXT_JUSTIFICATIONS, Document, FieldBlock,
                           FrameElement, TextElement)

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


# --- element editing --------------------------------------------------------

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

    layout.addWidget(_buttons(dialog))

    def _apply():
        element.width = width_spin.value()
        element.height = height_spin.value()
        element.thickness = thickness_spin.value()
        element.colour = colour_combo.currentData()
        element.rounding = rounding_spin.value()
        element.reverse_print = fr_check.isChecked()

    return _show_editor(dialog, _apply, on_accept)


def edit_barcode_dialog(parent, element, on_accept=None) -> QDialog:
    """Edit a barcode. `on_accept` runs once OK has changed it."""
    dialog = QDialog(parent)
    dialog.setWindowTitle("Edit Barcode")
    layout = QVBoxLayout(dialog)
    form = QFormLayout()
    layout.addLayout(form)

    value_edit = QLineEdit(element.barcode_value)
    form.addRow("Barcode Value:", value_edit)

    height_spin = QSpinBox()
    height_spin.setRange(20, 300)
    height_spin.setValue(element.bar_height)
    form.addRow("Bar Height:", height_spin)

    module_spin = QSpinBox()
    module_spin.setRange(1, 20)
    module_spin.setValue(element.module_width)
    form.addRow("Module Width:", module_spin)

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
    form.addRow("UCC Check Digit:", check_combo)

    mode_combo = QComboBox()
    for label, code in BARCODE_MODES:
        mode_combo.addItem(label, code)
    mode_combo.setCurrentIndex(
        [c for _l, c in BARCODE_MODES].index(element.mode)
        if element.mode in [c for _l, c in BARCODE_MODES] else 0)
    form.addRow("Mode:", mode_combo)

    fr_check = QCheckBox("Reverse print (^FR)")
    fr_check.setObjectName("reverse_print")
    fr_check.setChecked(element.reverse_print)
    form.addRow("Reverse:", fr_check)

    apply_field_number = _field_number_rows(form, element)

    layout.addWidget(_buttons(dialog))

    def _apply():
        element.barcode_value = value_edit.text()
        element.bar_height = height_spin.value()
        element.module_width = module_spin.value()
        element.orientation = orientation_combo.currentData()
        element.show_text, element.text_above = text_combo.currentData()
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
                        transform)


def printer_settings_dialog(parent, address: str, port: int, dpi: int,
                            title: str = "Printer Settings", default=None):
    """New (address, port, dpi), or None if cancelled.

    `default`, when given, is the persisted (address, port, dpi) to offer via
    a "Use Default" button - for the session-only picker, which is opened
    with whatever printer is currently in effect rather than the default.
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

    test_btn = QPushButton("Test Connection")
    layout.addWidget(test_btn)
    result = QLabel()
    result.setWordWrap(True)
    layout.addWidget(result)

    def set_result(colour, text):
        result.setStyleSheet(f"color: {colour};")
        result.setText(text)
        # both steps block, so let the label paint before the next one
        from PySide2.QtWidgets import QApplication
        QApplication.processEvents()

    def on_test():
        addr = address_edit.text().strip()
        prt = port_spin.value()
        if not addr:
            set_result("red", "Address is required")
            return
        set_result("gray", f"Connecting to {addr}:{prt}…")
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        try:
            sock.connect((addr, prt))
        except OSError as e:
            set_result("red", f"✗ {e}")
            return
        finally:
            sock.close()

        connected = f"✓ Connected to {addr}:{prt}"
        set_result("gray", f"{connected} — asking its resolution…")
        reported = zpl_fonts.query_printer_dpi(addr, prt)
        if reported is None:
            # No answer must leave the manual setting alone rather than
            # substituting a guess.
            set_result("orange", f"{connected}, but it did not report its "
                                 f"resolution; set the DPI manually.")
        elif reported in zpl_fonts.SUPPORTED_DPI:
            dpi_combo.setCurrentIndex(list(zpl_fonts.SUPPORTED_DPI).index(reported))
            set_result("green", f"{connected} — {reported} dpi")
        else:
            set_result("orange", f"{connected} — reports {reported} dpi, which "
                                 f"the designer does not support.")

    test_btn.clicked.connect(on_test)

    if default is not None:
        default_btn = QPushButton("Use Default")
        layout.addWidget(default_btn)

        def on_use_default():
            def_address, def_port, def_dpi = default
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

    layout.addWidget(_buttons(dialog))

    if dialog.exec_() != QDialog.Accepted:
        return None
    new_address = address_edit.text().strip()
    if not new_address:
        show_error(parent, "Printer address cannot be empty.")
        return None
    return new_address, port_spin.value(), _dpi_from(dpi_combo, dpi)


class PrinterFontsDialog(QDialog):
    """The fonts stored on the printer, with upload, delete and refresh."""

    def __init__(self, parent, address: str, port: int, on_uploaded=None):
        super().__init__(parent)
        self.setWindowTitle("Printer Fonts")
        self.resize(380, 300)
        self._address, self._port = address, port
        self._on_uploaded = on_uploaded

        layout = QVBoxLayout(self)
        self._status = QLabel()
        self._status.setWordWrap(True)
        layout.addWidget(self._status)

        self._list = QListWidget()
        layout.addWidget(self._list, 1)

        row = QHBoxLayout()
        self._upload_btn = QPushButton("Upload…")
        self._delete_btn = QPushButton("Delete")
        self._refresh_btn = QPushButton("Refresh")
        for b in (self._upload_btn, self._delete_btn, self._refresh_btn):
            row.addWidget(b)
        row.addStretch(1)
        layout.addLayout(row)

        close = QDialogButtonBox(QDialogButtonBox.Close, parent=self)
        close.rejected.connect(self.reject)
        layout.addWidget(close)

        self._upload_btn.clicked.connect(self._on_upload)
        self._delete_btn.clicked.connect(self._on_delete)
        self._refresh_btn.clicked.connect(self.refresh)
        self.refresh()

    def refresh(self):
        self._list.clear()
        fonts = zpl_fonts.query_printer_fonts(self._address, self._port)
        if fonts is None:
            # Not the same as "no fonts": say the printer could not be asked,
            # rather than showing an empty list as if it had answered.
            self._status.setText(f"Could not reach the printer at "
                                 f"{self._address}:{self._port}.")
            self._delete_btn.setEnabled(False)
            return
        for name in sorted(fonts):
            self._list.addItem(zpl_fonts.printer_font_path(name))
        self._delete_btn.setEnabled(bool(fonts))
        self._status.setText(f"{len(fonts)} font(s) on {self._address}"
                             if fonts else "No fonts stored on the printer.")

    def _on_upload(self):
        family, path = choose_font_family(self, title="Upload Font to Printer")
        if not path:
            return
        name = zpl_fonts.printer_font_name(path)
        self._status.setText(f"Uploading {zpl_fonts.printer_font_path(name)}...")
        try:
            zpl_fonts.upload_font(self._address, self._port, path, name)
        except Exception as e:
            show_error(self, f"Font upload failed: {e}")
            return
        if self._on_uploaded:
            self._on_uploaded(name, path)
        self.refresh()

    def _on_delete(self):
        item = self._list.currentItem()
        if item is None:
            return
        shown = item.text()
        name = Path(shown).stem.split(':')[-1]
        try:
            zpl_fonts.delete_printer_font(self._address, self._port, name)
        except Exception as e:
            show_error(self, f"Could not delete {shown}: {e}")
            return
        self.refresh()


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
