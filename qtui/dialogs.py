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

from zplcore import fonts as zpl_fonts, textraster
from zplcore.model import (BARCODE_CHECK_DIGIT, BARCODE_MODES,
                           BARCODE_ORIENTATIONS, BARCODE_TEXT_CHOICES,
                           TEXT_JUSTIFICATIONS, Document, FieldBlock,
                           TextElement)

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

def edit_text_dialog(parent, element: TextElement, document: Document) -> bool:
    """Edit a text element. True when something changed."""
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

    if dialog.exec_() != QDialog.Accepted:
        return False

    element.text = textraster.from_editor(text_edit.toPlainText())
    element.font_height = height_spin.value()
    element.font_width = width_spin.value()
    element.height = element.font_height

    if wrap_check.isChecked():
        # Assigned rather than mutated: the block on the element may be the one
        # an undo snapshot is holding.
        element.block = FieldBlock(block_width.value(), max_lines.value(),
                                   spacing_spin.value(),
                                   justify_combo.currentData(),
                                   indent_spin.value())
    elif element.block is not None:
        # Unticked. A forced break left behind would print as the two
        # characters it is written with, so the lines are joined rather than
        # abandoned to the printer.
        element.text = textraster.join_lines(element.text)
        element.block = None
    elif textraster.FORCED_BREAK in element.text:
        # A break typed into an element that never had a block still needs one,
        # for the same reason. Sized to the longest line, so nothing moves.
        element.block = element.default_block(document.font_path)

    if chosen['path'] != element.font_path:
        if chosen['path']:
            # The font is only recorded here; it is uploaded at print time, so
            # choosing a font never blocks on the network.
            name = zpl_fonts.printer_font_name(
                chosen['path'], taken=document.printer_font_names(exclude=element))
            document.set_element_font(element, chosen['path'], chosen['family'], name)
        else:
            element.font_path = None
            element.font_family = None
            element.printer_font_name = None
    document.sync_text_width(element)
    return True


def edit_frame_dialog(parent, element) -> bool:
    """Edit a frame. True when something changed."""
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

    layout.addWidget(_buttons(dialog))
    if dialog.exec_() != QDialog.Accepted:
        return False

    element.width = width_spin.value()
    element.height = height_spin.value()
    element.thickness = thickness_spin.value()
    return True


def edit_barcode_dialog(parent, element) -> bool:
    """Edit a barcode. True when something changed."""
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

    layout.addWidget(_buttons(dialog))
    if dialog.exec_() != QDialog.Accepted:
        return False

    element.barcode_value = value_edit.text()
    element.bar_height = height_spin.value()
    element.module_width = module_spin.value()
    element.orientation = orientation_combo.currentData()
    element.show_text, element.text_above = text_combo.currentData()
    element.check_digit = check_combo.currentData()
    element.mode = mode_combo.currentData()
    if element.show_text:
        # With the line switched on, name the font it prints in rather than
        # leaving it to whatever the printer happens to have selected.
        code = element.font[0] if element.font else element.DEFAULT_FONT[0]
        element.font = (code, font_spin.value(), font_spin.value())
    element.sync_box()
    return True


def choose_image_file(parent, title="Select Image") -> Optional[str]:
    path, _ = QFileDialog.getOpenFileName(parent, title, "", IMAGE_FILTER)
    return path or None


# --- label and printer ------------------------------------------------------

def label_size_dialog(parent, document: Document, dpi: int):
    """New label size in dots, or None. Entered in inches, stored in dots."""
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

    for text, w_in, h_in in PRESET_SIZES:
        btn = QPushButton(text)
        btn.clicked.connect(
            lambda _checked=False, w=w_in, h=h_in: (width_spin.setValue(w),
                                                    height_spin.setValue(h)))
        presets.addWidget(btn)

    hint = QLabel()
    hint.setWordWrap(True)
    layout.addWidget(hint)

    def to_dots():
        return (max(1, int(round(width_spin.value() * dpi))),
                max(1, int(round(height_spin.value() * dpi))))

    def update_hint():
        w, h = to_dots()
        hint.setText(f"{w} x {h} dots at {dpi} dpi "
                     f"(^PW{w} / ^LL{h}), saved with the file.")

    width_spin.valueChanged.connect(update_hint)
    height_spin.valueChanged.connect(update_hint)
    update_hint()

    layout.addWidget(_buttons(dialog))
    if dialog.exec_() != QDialog.Accepted:
        return None
    return to_dots()


def printer_settings_dialog(parent, address: str, port: int, dpi: int):
    """New (address, port, dpi), or None if cancelled."""
    dialog = QDialog(parent)
    dialog.setWindowTitle("Printer Settings")
    layout = QVBoxLayout(dialog)
    form = QFormLayout()
    layout.addLayout(form)

    address_edit = QLineEdit(address)
    form.addRow("Address:", address_edit)

    port_spin = QSpinBox()
    port_spin.setRange(1, 65535)
    port_spin.setValue(port)
    form.addRow("Port:", port_spin)

    dpi_combo = QComboBox()
    for d in zpl_fonts.SUPPORTED_DPI:
        dpi_combo.addItem(str(d))
    if dpi in zpl_fonts.SUPPORTED_DPI:
        dpi_combo.setCurrentIndex(list(zpl_fonts.SUPPORTED_DPI).index(dpi))
    else:
        dpi_combo.addItem(str(dpi))
        dpi_combo.setCurrentIndex(dpi_combo.count() - 1)
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
    layout.addWidget(_buttons(dialog))

    if dialog.exec_() != QDialog.Accepted:
        return None
    new_address = address_edit.text().strip()
    if not new_address:
        show_error(parent, "Printer address cannot be empty.")
        return None
    chosen = dpi_combo.currentText()
    return (new_address, port_spin.value(),
            int(chosen) if chosen.isdigit() else dpi)


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
