"""
The Properties panel: the selection's rows, edited live.

One panel, docked beside the canvas or floating, in place of an editor window
per element. Docked, it always shows the selection; floating, it stays away
until it is asked for (a double click, Properties, Alt+Enter) and then follows
the selection the same way.

What it shows:

- one element: X, Y and Print This Element, then that element's own editor
  rows - the same EditorForm an editor window would hold;
- several (a group, or loose elements): X, Y and Print This Element for the
  selection as a whole, then the rows every one of them shares
  (zplcore.properties);
- nothing: a line saying so.

An edit is written to the element as it is made and the canvas redrawn, but
recorded as an undo entry only once a burst of them ends - focus leaving the
panel, Enter, a second without a change, a different selection, or any
command, click or undo before something else can act on the edited elements.
Escape throws away the burst in progress instead. Like a held arrow key, a
burst is one entry however many changes it took.
"""

from pathlib import Path

from PySide2.QtCore import Qt, QTimer
from PySide2.QtWidgets import (QAbstractSpinBox, QApplication, QCheckBox,
                               QComboBox, QDockWidget, QFormLayout, QFrame,
                               QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit,
                               QPushButton, QScrollArea, QSizePolicy, QSpinBox,
                               QVBoxLayout, QWidget)

from zplcore import geometry, helpdocs
from zplcore import properties as zpl_props
from zplcore.model import ImageElement

from . import dialogs as qt_dialogs

# How long a pause in editing ends a burst, in milliseconds
QUIET_MS = 1000

# What a row shows for a value the selected elements disagree on
MIXED_TEXT = "Mixed"

HINT = ("Text, data and symbology settings are each element's own: "
        "Ctrl-click one to edit it.")


def _narrow(rows: QFormLayout) -> None:
    """Fit the rows to a panel narrower than an editor window: a row too wide
    puts its field under its label, and a list asks for room for a few
    characters rather than for its longest choice - Symbology's run long."""
    rows.setRowWrapPolicy(QFormLayout.WrapLongRows)
    holder = rows.parentWidget()
    for combo in holder.findChildren(QComboBox) if holder is not None else ():
        combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        combo.setMinimumContentsLength(10)


def _same(a, b) -> bool:
    """The same elements, the same objects, in the same order."""
    return len(a) == len(b) and all(x is y for x, y in zip(a, b))


class PropertiesPanel(QDockWidget):
    """The selection's properties, in a dock the window owns."""

    def __init__(self, window):
        super().__init__("Properties", window)
        # So the window's saveState() can name it
        self.setObjectName("properties")
        self.setAllowedAreas(Qt.LeftDockWidgetArea | Qt.RightDockWidgetArea)
        self._window = window

        # What the rows were built for: the selection, as the element objects
        # themselves, and for one element the form function that built its
        # rows. None until the first build.
        self._bound = None
        self._builder = None
        self._form = None
        # [property, widget, what it last showed] for the X / Y / Print rows
        # and the shared rows, which are read again on every paint
        self._rows = []
        # A document change the panel did not make: rebuild on the next sync
        self._stale = False
        # Edits made since the last undo entry
        self._pending = False

        self._quiet = QTimer(self)
        self._quiet.setSingleShot(True)
        self._quiet.setInterval(QUIET_MS)
        self._quiet.timeout.connect(self.finish_edit)
        # One sync per turn of the event loop however many paints ask for one,
        # and never inside the paint or the input signal that asked.
        self._sync_timer = QTimer(self)
        self._sync_timer.setSingleShot(True)
        self._sync_timer.setInterval(0)
        self._sync_timer.timeout.connect(self.sync)

        self._content = QWidget()
        layout = QVBoxLayout(self._content)
        self._title = QLabel()
        self._title.setObjectName("properties_title")
        font = self._title.font()
        font.setBold(True)
        self._title.setFont(font)
        top = QHBoxLayout()
        top.addWidget(self._title, 1)
        # A plain button with no tooltip: Help on the selection's own page.
        # It takes no focus, so pressing it leaves an edit where it was.
        self._help = QPushButton("Help")
        self._help.setObjectName("help")
        self._help.setFocusPolicy(Qt.NoFocus)
        self._help.clicked.connect(self.open_help)
        top.addWidget(self._help)
        layout.addLayout(top)
        self._area = QVBoxLayout()
        layout.addLayout(self._area)
        layout.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(self._content)
        self.setWidget(scroll)

        QApplication.instance().focusChanged.connect(self._on_focus_changed)
        window.canvas.painted.connect(self.queue_sync)
        window.canvas.pressed.connect(self.finish_edit)

    # --- help -----------------------------------------------------------------

    def help_topic(self) -> str:
        """The help page for what the panel shows: the selected element's
        kind, or the panel itself."""
        return helpdocs.element_topic(list(self._window.document.selection))

    def has_focus_within(self) -> bool:
        focus = QApplication.focusWidget()
        return focus is not None and self.isVisible() and self.isAncestorOf(focus)

    def open_help(self):
        qt_dialogs.show_help(self, self.help_topic())

    # --- following the selection --------------------------------------------

    def queue_sync(self):
        self._sync_timer.start()

    def sync(self):
        """Show the selection: rebuild if it is a different one, if its one
        element now needs a different form, or if the document changed
        behind the panel; otherwise just read the rows again."""
        self._sync_timer.stop()
        if self.isHidden():
            return
        selection = tuple(self._window.document.selection)
        if (self._stale or self._bound is None
                or not _same(selection, self._bound)
                or (len(selection) == 1
                    and qt_dialogs.form_builder(selection[0]) is not self._builder)):
            self._rebuild(selection)
        else:
            self._refresh_rows()

    def request(self):
        """Show the panel, on the selection, with the focus in its first row."""
        self.show()
        self.raise_()
        if self.isFloating():
            self.activateWindow()
        self.sync()
        for widget in self._content.findChildren(QWidget):
            if (widget.focusPolicy() & Qt.TabFocus and widget.isEnabled()
                    and widget.isVisibleTo(self._content)
                    and not isinstance(widget.parent(), (QAbstractSpinBox, QComboBox))):
                widget.setFocus()
                break

    def document_changed(self):
        """A change the panel did not make was recorded: rebuild from the
        document. Anything the panel had pending went into that change."""
        self._pending = False
        self._quiet.stop()
        self._stale = True
        self.queue_sync()

    # --- the burst ----------------------------------------------------------

    def _edited(self):
        self._pending = True
        self._window.unsaved_changes = True
        self._quiet.start()
        self._window.canvas.update()

    def finish_edit(self):
        """Record the edits made since the last entry as one undo entry."""
        self._quiet.stop()
        if not self._pending:
            return
        self._pending = False
        self._window.record_panel_edit()

    def revert_edit(self):
        """Throw away the edits made since the last entry."""
        self._quiet.stop()
        if not self._pending:
            return
        self._pending = False
        self._window.revert_to_last_entry()
        self._stale = True
        self.queue_sync()

    @property
    def pending(self) -> bool:
        return self._pending

    def _on_focus_changed(self, old, new):
        if not self._pending or old is None:
            return
        try:
            leaving = self.isAncestorOf(old) and (
                new is None or not self.isAncestorOf(new))
        except RuntimeError:        # a row rebuilt away under the focus
            return
        if leaving:
            self.finish_edit()

    def keyPressEvent(self, event):
        # A line or number box passes Escape and Enter up to here; a text box
        # keeps Enter, which is a line break in it.
        if event.key() == Qt.Key_Escape:
            self.revert_edit()
            event.accept()
            return
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            self.finish_edit()
            event.accept()
            return
        super().keyPressEvent(event)

    def showEvent(self, event):
        super().showEvent(event)
        self.queue_sync()

    def hideEvent(self, event):
        self.finish_edit()
        super().hideEvent(event)

    # --- building ------------------------------------------------------------

    def _rebuild(self, selection):
        if not _same(selection, self._bound or ()):
            # The edits so far were made to the elements now being left
            self.finish_edit()
            focus = None
        else:
            focus = self._focus_memo()
        while self._area.count():
            item = self._area.takeAt(0)
            old = item.widget()
            if old is not None:
                # Out of the panel now, so nothing finds the old rows by name
                # before the event loop gets round to deleting them
                old.hide()
                old.setParent(None)
                old.deleteLater()
        self._bound = selection
        self._stale = False
        self._rows = []
        self._form = None
        self._builder = None

        if not selection:
            self._title.setText("Nothing selected")
            return

        header = QWidget()
        header_rows = QFormLayout(header)
        header_rows.setContentsMargins(0, 0, 0, 0)
        _narrow(header_rows)
        for prop in zpl_props.header_properties(selection):
            self._add_row(header_rows, prop, selection)
        self._area.addWidget(header)

        if len(selection) == 1:
            self._build_single(selection[0])
        else:
            self._build_shared(selection)
        if focus is not None:
            self._restore_focus(focus)

    def _build_single(self, element):
        self._builder = qt_dialogs.form_builder(element)
        if self._builder is not None:
            self._form = qt_dialogs.editor_form(element, self._window.document)
            self._form.on_change(self._form_changed)
            _narrow(self._form.rows)
            self._title.setText(self._form.title[len("Edit "):]
                                if self._form.title.startswith("Edit ")
                                else self._form.title)
            self._area.addWidget(self._form.body)
        elif isinstance(element, ImageElement):
            self._title.setText("Image")
            self._area.addWidget(self._image_row(element))
        else:
            self._title.setText(type(element).__name__)

    def _build_shared(self, selection):
        doc = self._window.document
        units = geometry.units_of(selection)
        top = geometry.top_group(selection[0])
        whole_group = (len(units) == 1 and top is not None
                       and len(geometry.members_of(doc.elements, top)) == len(selection))
        self._title.setText(f"Group of {len(selection)}" if whole_group
                            else f"{len(selection)} elements")
        shared = QWidget()
        shared_rows = QFormLayout(shared)
        shared_rows.setContentsMargins(0, 0, 0, 0)
        _narrow(shared_rows)
        for prop in zpl_props.shared_properties(selection):
            self._add_row(shared_rows, prop, selection)
        self._area.addWidget(shared)
        hint = QLabel(HINT)
        hint.setObjectName("properties_hint")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray;")
        self._area.addWidget(hint)

    def _image_row(self, element):
        box = QWidget()
        row = QHBoxLayout(box)
        row.setContentsMargins(0, 0, 0, 0)
        name = QLabel(Path(element.image_path).name if element.image_path
                      else "(no file)")
        name.setObjectName("image_path")
        # A long file name gives way to the button rather than widening the
        # panel; the whole path is on hover
        name.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        name.setToolTip(element.image_path or "")
        row.addWidget(name, 1)
        button = QPushButton("Replace Image…")
        button.setObjectName("replace_image")
        button.clicked.connect(lambda _checked=False: self._replace_image(element))
        row.addWidget(button)
        return box

    def _replace_image(self, element):
        self.finish_edit()
        path = qt_dialogs.choose_image_file(self.window(), "Replace Image")
        if path:
            element.image_path = path
            element.reload()
            # One change of its own, like any other command
            self._window.canvas.commit()

    # --- the X / Y / Print rows and the shared rows -------------------------

    def _add_row(self, layout, prop, selection):
        if prop.kind == zpl_props.INT:
            widget = QSpinBox()
            widget.valueChanged.connect(
                lambda value, prop=prop, widget=widget: self._row_changed(prop, widget, value))
            layout.addRow(prop.title(selection), widget)
        elif prop.kind == zpl_props.BOOL:
            widget = QCheckBox(prop.title(selection))
            widget.stateChanged.connect(
                lambda state, prop=prop, widget=widget: self._row_changed(prop, widget, state))
            layout.addRow(widget)
        else:
            widget = QComboBox()
            widget.currentIndexChanged.connect(
                lambda _index, prop=prop, widget=widget:
                self._row_changed(prop, widget, widget.currentData()))
            layout.addRow(prop.title(selection), widget)
        widget.setObjectName("prop_" + prop.key)
        row = [prop, widget, None]
        self._rows.append(row)
        self._show_row(row, selection)

    def _show_row(self, row, selection):
        """Put the row's current value in its widget, if it has moved."""
        prop, widget, shown = row
        doc = self._window.document
        value = prop.value(doc, selection)
        limits = prop.limits(doc, selection)
        if shown is not None and shown[0] == value and shown[1] == limits:
            return
        row[2] = (value, limits)
        mixed = value is zpl_props.MIXED
        widget.blockSignals(True)
        try:
            if prop.kind == zpl_props.INT:
                low, high = limits
                if mixed:
                    # One below the range, shown as a word: picking a number
                    # writes it, and stepping back to the word writes nothing.
                    widget.setRange(low - 1, high)
                    widget.setSpecialValueText(MIXED_TEXT)
                    widget.setValue(low - 1)
                else:
                    widget.setSpecialValueText("")
                    # A value already outside the range - a frame narrower
                    # than its editor offers, from a file - is shown as it is
                    widget.setRange(min(low, value), max(high, value))
                    widget.setValue(value)
            elif prop.kind == zpl_props.BOOL:
                widget.setTristate(mixed)
                widget.setCheckState(Qt.PartiallyChecked if mixed
                                     else Qt.Checked if value else Qt.Unchecked)
            else:
                widget.clear()
                if mixed:
                    widget.addItem(MIXED_TEXT, None)
                for label, code in prop.choices:
                    widget.addItem(label, code)
                index = widget.findData(value) if not mixed else 0
                widget.setCurrentIndex(max(0, index))
        finally:
            widget.blockSignals(False)

    def _refresh_rows(self):
        """Read every row again, bar the one being typed into."""
        focus = QApplication.focusWidget()
        for row in self._rows:
            widget = row[1]
            if focus is not None and (focus is widget or widget.isAncestorOf(focus)):
                continue
            self._show_row(row, self._bound)

    def _row_changed(self, prop, widget, raw):
        if prop.kind == zpl_props.INT:
            if widget.specialValueText() and raw == widget.minimum():
                return                    # back on "Mixed": nothing chosen
            value = raw
        elif prop.kind == zpl_props.BOOL:
            if raw == Qt.PartiallyChecked:
                return
            # Chosen now, so it is one value from here on
            widget.setTristate(False)
            value = raw == Qt.Checked
        else:
            if raw is None:
                return
            value = raw
        prop.write(self._window.document, self._bound, value)
        self._edited()

    # --- the element's own rows ---------------------------------------------

    def _form_changed(self):
        if self._form is None or not self._bound:
            return
        self._form.apply()
        self._edited()
        if qt_dialogs.form_builder(self._bound[0]) is not self._builder:
            # Unticking the clock, say, makes it a plain text field, which
            # has rows of its own. Not rebuilt here, inside the signal of a
            # row about to be thrown away.
            self.queue_sync()

    # --- keeping the focus across a rebuild ----------------------------------

    def _focus_memo(self):
        focus = QApplication.focusWidget()
        if focus is None or not self._content.isAncestorOf(focus):
            return None
        if isinstance(focus.parent(), QAbstractSpinBox):
            focus = focus.parent()
        cursor = None
        if isinstance(focus, QLineEdit):
            cursor = focus.cursorPosition()
        elif isinstance(focus, QPlainTextEdit):
            cursor = focus.textCursor().position()
        return focus.objectName(), cursor

    def _restore_focus(self, memo):
        name, cursor = memo
        if not name:
            return
        widget = self._content.findChild(QWidget, name)
        if widget is None:
            return
        widget.setFocus()
        if cursor is None:
            return
        if isinstance(widget, QLineEdit):
            widget.setCursorPosition(cursor)
        elif isinstance(widget, QPlainTextEdit):
            text_cursor = widget.textCursor()
            text_cursor.setPosition(min(cursor, len(widget.toPlainText())))
            widget.setTextCursor(text_cursor)
