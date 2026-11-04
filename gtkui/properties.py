"""
The Properties panel: the selection's rows, edited live.

One panel, docked beside the canvas or floating in a window of its own, in
place of an editor window per element. Docked, it always shows the selection;
floating, it stays away until it is asked for (a double click, Properties,
Alt+Enter, View > Properties Panel) and then follows the selection the same
way. The Qt frontend's panel (qtui/properties.py) behaves the same, and
FUNCTIONAL_SPEC.md section 7.1 is what both answer to.

What it shows:

- one element: X, Y and Print This Element, then that element's own editor
  rows - the same form an editor window would hold (gtkui/editors.py);
- several (a group, or loose elements): X, Y and Print This Element for the
  selection as a whole, then the rows every one of them shares
  (zplcore.properties);
- nothing: a line saying so.

An edit is written to the element as it is made and the canvas redrawn, but
recorded as an undo entry only once a burst of them ends - focus leaving the
panel, Enter, a second without a change, a different selection, or any
command, click or undo before something else can act on the edited elements.
Escape throws the burst in progress away instead.
"""

from pathlib import Path

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gdk, GLib, Gtk, Pango

from zplcore import geometry
from zplcore import properties as zpl_props
from zplcore.model import ImageElement

from . import editors as gtk_editors

# How long a pause in editing ends a burst, in milliseconds
QUIET_MS = 1000

# What a row shows for a value the selected elements disagree on
MIXED_TEXT = "Mixed"

HINT = ("Text, data and symbology settings are each element's own: "
        "Ctrl-click one to edit it.")

# The width the panel opens at, docked, before one has been saved
DEFAULT_WIDTH = 400


def _same(a, b) -> bool:
    """The same elements, the same objects, in the same order."""
    return len(a) == len(b) and all(x is y for x, y in zip(a, b))


def _descendants(widget):
    """Every widget under `widget`, depth first."""
    if isinstance(widget, Gtk.Container):
        for child in widget.get_children():
            yield child
            yield from _descendants(child)


def _narrow(body) -> None:
    """Fit an editor's rows to a panel narrower than its window: a list asks
    for room for a few characters rather than its longest choice -
    Symbology's run long - and a long checkbox label wraps."""
    for widget in _descendants(body):
        if isinstance(widget, Gtk.ComboBox):
            for cell in widget.get_cells():
                if isinstance(cell, Gtk.CellRendererText):
                    cell.set_property("ellipsize", Pango.EllipsizeMode.END)
        elif isinstance(widget, Gtk.CheckButton):
            label = widget.get_child()
            if isinstance(label, Gtk.Label):
                label.set_line_wrap(True)


def _is_ancestor(ancestor, widget) -> bool:
    while widget is not None:
        if widget is ancestor:
            return True
        widget = widget.get_parent()
    return False


class PropertiesPanel(Gtk.Box):
    """The selection's properties: a box the window docks beside the canvas,
    or puts in a window of its own to float."""

    def __init__(self, window):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.set_name("properties")
        self._window = window

        # What the rows were built for: the selection, as the element objects
        # themselves, and for one element the form function that built them.
        self._bound = None
        self._builder = None
        self._form = None
        # One dict per X / Y / Print row and shared row, read again on paints
        self._rows = []
        self._stale = False
        self._pending = False
        self._quiet_source = None
        self._sync_queued = False
        # The window it floats in, while it floats
        self._floating = None
        self.floating_geometry = None
        # What the View menu item is told when the panel shows or hides
        self.on_shown_changed = lambda shown: None

        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        heading = Gtk.Label(xalign=0)
        heading.set_markup("<b>Properties</b>")
        bar.pack_start(heading, True, True, 0)
        self._float_button = Gtk.Button.new_from_icon_name(
            "window-new-symbolic", Gtk.IconSize.MENU)
        self._float_button.set_relief(Gtk.ReliefStyle.NONE)
        self._float_button.set_name("float_panel")
        self._float_button.connect(
            "clicked", lambda _b: self.set_floating(not self.floating))
        bar.pack_start(self._float_button, False, False, 0)
        close = Gtk.Button.new_from_icon_name("window-close-symbolic",
                                              Gtk.IconSize.MENU)
        close.set_relief(Gtk.ReliefStyle.NONE)
        close.set_tooltip_text("Close the panel (View ▸ Properties Panel)")
        close.set_name("close_panel")
        close.connect("clicked", lambda _b: self.set_shown(False))
        bar.pack_start(close, False, False, 0)
        self.pack_start(bar, False, False, 0)
        self._update_float_button()

        self._content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        for side in ("start", "end", "top", "bottom"):
            getattr(self._content, "set_margin_" + side)(6)
        self._title = Gtk.Label(xalign=0)
        self._title.set_name("properties_title")
        self._content.pack_start(self._title, False, False, 0)
        self._area = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self._content.pack_start(self._area, False, False, 0)
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroller.add(self._content)
        self.pack_start(scroller, True, True, 0)

        # Escape and Enter that a row's own widget passed up
        self.connect("key-press-event", self._on_key_press)
        window.design_canvas.connect("draw", lambda *_a: self.queue_sync())
        window.design_canvas.connect("pressed", lambda *_a: self.finish_edit())

    # --- where it is ---------------------------------------------------------

    @property
    def floating(self) -> bool:
        return self._floating is not None

    @property
    def shown(self) -> bool:
        if self._floating is not None:
            return self._floating.get_visible()
        return self.get_visible()

    def holds(self, widget) -> bool:
        """Whether `widget` is one of the panel's own."""
        return _is_ancestor(self, widget)

    def set_shown(self, shown: bool) -> None:
        if shown == self.shown:
            return
        if not shown:
            self.finish_edit()
        if shown:
            if self._floating is not None:
                self._floating.show_all()
            else:
                self.show()
            self.queue_sync()
        elif self._floating is not None:
            self._remember_floating_geometry()
            self._floating.hide()
        else:
            self.hide()
        self.on_shown_changed(shown)

    def set_floating(self, floating: bool) -> None:
        """Pull the panel off into a window of its own, or put it back."""
        if floating == self.floating:
            return
        self.finish_edit()
        window = self._window
        if floating:
            window.panel_width = window.docked_panel_width()
            window.paned.remove(self)
            self._floating = Gtk.Window(title="Properties")
            self._floating.set_transient_for(window)
            self._floating.set_type_hint(Gdk.WindowTypeHint.UTILITY)
            self._floating.set_destroy_with_parent(True)
            if self.floating_geometry:
                x, y, width, height = self.floating_geometry
                self._floating.move(x, y)
                self._floating.resize(width, height)
            else:
                self._floating.set_default_size(DEFAULT_WIDTH, 600)
            # The window's own shortcuts, and the same rule about whose key
            # it is, wherever the panel is
            window.share_keys(self._floating)
            self._floating.connect("delete-event", self._on_floating_closed)
            self._floating.connect("focus-out-event",
                                   lambda *_a: self.finish_edit())
            self._floating.connect("set-focus", self._on_set_focus)
            self._floating.add(self)
            self._floating.show_all()
        else:
            self._remember_floating_geometry()
            self._floating.remove(self)
            self._floating.destroy()
            self._floating = None
            window.paned.pack2(self, False, False)
            self.show_all()
            window.place_panel()
        self._update_float_button()
        self.queue_sync()
        self.on_shown_changed(self.shown)

    def _remember_floating_geometry(self):
        if self._floating is not None and self._floating.get_visible():
            x, y = self._floating.get_position()
            width, height = self._floating.get_size()
            self.floating_geometry = (x, y, width, height)

    def _on_floating_closed(self, *_args):
        self.set_shown(False)
        return True         # hidden, not destroyed: it comes back as it was

    def _update_float_button(self):
        self._float_button.set_tooltip_text(
            "Dock the panel beside the canvas" if self.floating
            else "Float the panel in a window of its own")

    # --- following the selection --------------------------------------------

    def queue_sync(self):
        if not self._sync_queued:
            self._sync_queued = True
            GLib.idle_add(self._sync_idle)

    def _sync_idle(self):
        self._sync_queued = False
        self.sync()
        return False

    def sync(self):
        """Show the selection: rebuild if it is a different one, if its one
        element now needs a different form, or if the document changed
        behind the panel; otherwise just read the rows again."""
        if not self.shown:
            return
        selection = tuple(self._window.design_canvas.document.selection)
        if (self._stale or self._bound is None
                or not _same(selection, self._bound)
                or (len(selection) == 1
                    and gtk_editors.form_builder(selection[0]) is not self._builder)):
            self._rebuild(selection)
        else:
            self._refresh_rows()

    def request(self):
        """Show the panel, on the selection, with the focus in its first row."""
        self.set_shown(True)
        if self._floating is not None:
            self._floating.present()
        self.sync()
        for widget in _descendants(self._area):
            if (isinstance(widget, (Gtk.Entry, Gtk.TextView, Gtk.ComboBox,
                                    Gtk.ToggleButton))
                    and widget.get_visible() and widget.is_sensitive()
                    and widget.get_can_focus()):
                widget.grab_focus()
                break

    def document_changed(self):
        """A change the panel did not make was recorded: rebuild from the
        document. Anything the panel had pending went into that change."""
        self._pending = False
        self._stop_quiet()
        self._stale = True
        self.queue_sync()

    # --- the burst ----------------------------------------------------------

    @property
    def pending(self) -> bool:
        return self._pending

    def _stop_quiet(self):
        if self._quiet_source is not None:
            GLib.source_remove(self._quiet_source)
            self._quiet_source = None

    def _on_quiet(self):
        self._quiet_source = None
        self.finish_edit()
        return False

    def _edited(self):
        self._pending = True
        self._window.unsaved_changes = True
        self._stop_quiet()
        self._quiet_source = GLib.timeout_add(QUIET_MS, self._on_quiet)
        self._window.design_canvas.queue_draw()

    def finish_edit(self, *_args):
        """Record the edits made since the last entry as one undo entry."""
        self._stop_quiet()
        if not self._pending:
            return
        self._pending = False
        self._window.record_panel_edit()

    def revert_edit(self):
        """Throw away the edits made since the last entry."""
        self._stop_quiet()
        if not self._pending:
            return
        self._pending = False
        self._window.revert_to_last_entry()
        self._stale = True
        self.queue_sync()

    def _on_set_focus(self, _window, widget):
        """The floating window's focus moved; leaving the panel ends a burst."""
        if widget is None or not self.holds(widget):
            self.finish_edit()

    def _on_key_press(self, _widget, event):
        if event.keyval == Gdk.KEY_Escape:
            self.revert_edit()
            return True
        return False

    # --- building ------------------------------------------------------------

    def _rebuild(self, selection):
        if not _same(selection, self._bound or ()):
            # The edits so far were made to the elements now being left
            self.finish_edit()
        for child in self._area.get_children():
            child.destroy()
        self._bound = selection
        self._stale = False
        self._rows = []
        self._form = None
        self._builder = None

        if not selection:
            self._title.set_markup("<b>Nothing selected</b>")
            return

        header = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        for prop in zpl_props.header_properties(selection):
            self._add_row(header, prop, selection)
        self._area.pack_start(header, False, False, 0)

        if len(selection) == 1:
            self._build_single(selection[0])
        else:
            self._build_shared(selection)
        self._area.show_all()
        if self._form is not None:
            self._form.after_show()

    def _set_title(self, text):
        self._title.set_markup("<b>" + GLib.markup_escape_text(text) + "</b>")

    def _build_single(self, element):
        self._builder = gtk_editors.form_builder(element)
        if self._builder is not None:
            body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            self._form = self._builder(self._window, body, element)
            # An editor window's margins; the panel has its own
            for side in ("start", "end", "top", "bottom"):
                getattr(body, "set_margin_" + side)(0)
            self._form.on_change(self._form_changed)
            _narrow(body)
            title = self._form.title
            self._set_title(title[len("Edit "):] if title.startswith("Edit ") else title)
            self._area.pack_start(body, False, False, 0)
            # Enter in a line or number box ends the burst
            for widget in _descendants(body):
                if isinstance(widget, Gtk.Entry):
                    widget.connect("activate", self.finish_edit)
        elif isinstance(element, ImageElement):
            self._set_title("Image")
            self._area.pack_start(self._image_row(element), False, False, 0)
        else:
            self._set_title(type(element).__name__)

    def _build_shared(self, selection):
        doc = self._window.design_canvas.document
        units = geometry.units_of(selection)
        top = geometry.top_group(selection[0])
        whole_group = (len(units) == 1 and top is not None
                       and len(geometry.members_of(doc.elements, top)) == len(selection))
        self._set_title(f"Group of {len(selection)}" if whole_group
                        else f"{len(selection)} elements")
        shared = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        for prop in zpl_props.shared_properties(selection):
            self._add_row(shared, prop, selection)
        self._area.pack_start(shared, False, False, 0)
        hint = Gtk.Label(label=HINT, xalign=0)
        hint.set_name("properties_hint")
        hint.set_line_wrap(True)
        hint.get_style_context().add_class("dim-label")
        self._area.pack_start(hint, False, False, 0)

    def _image_row(self, element):
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        name = Gtk.Label(label=Path(element.image_path).name if element.image_path
                         else "(no file)", xalign=0)
        name.set_name("image_path")
        name.set_ellipsize(3)       # PANGO_ELLIPSIZE_END: a long name gives way
        name.set_tooltip_text(element.image_path or "")
        row.pack_start(name, True, True, 0)
        button = Gtk.Button(label="Replace Image…")
        button.set_name("replace_image")
        button.connect("clicked", lambda _b: self._replace_image(element))
        row.pack_start(button, False, False, 0)
        return row

    def _replace_image(self, element):
        self.finish_edit()
        self._window.replace_image(element)

    # --- the X / Y / Print rows and the shared rows -------------------------

    def _add_row(self, box, prop, selection):
        row = {'prop': prop, 'shown': None, 'mixed': False}
        if prop.kind == zpl_props.INT:
            widget = Gtk.SpinButton()
            widget.set_adjustment(Gtk.Adjustment(value=0, lower=0, upper=1,
                                                 step_increment=1))
            widget.connect("output", self._spin_output, row)
            row['handler'] = widget.connect(
                "value-changed", lambda w: self._row_changed(row, w.get_value_as_int()))
            widget.connect("activate", self.finish_edit)
            gtk_editors._make_row(box, prop.title(selection), widget)
        elif prop.kind == zpl_props.BOOL:
            widget = Gtk.CheckButton(label=prop.title(selection))
            row['handler'] = widget.connect(
                "toggled", lambda w: self._row_changed(row, w.get_active()))
            gtk_editors._make_row(box, "", widget)
        else:
            widget = Gtk.ComboBoxText()
            row['handler'] = widget.connect(
                "changed", lambda w: self._row_changed(row, w.get_active()))
            gtk_editors._make_row(box, prop.title(selection), widget)
        widget.set_name("prop_" + prop.key)
        row['widget'] = widget
        self._rows.append(row)
        self._show_row(row, selection)

    def _spin_output(self, spin, row):
        """A number box one below its range reads as Mixed."""
        if row['mixed'] and spin.get_value_as_int() == int(spin.get_adjustment().get_lower()):
            spin.set_text(MIXED_TEXT)
            return True
        return False

    def _show_row(self, row, selection):
        """Put the row's current value in its widget, if it has moved."""
        prop, widget = row['prop'], row['widget']
        doc = self._window.design_canvas.document
        value = prop.value(doc, selection)
        limits = prop.limits(doc, selection)
        if row['shown'] is not None and row['shown'] == (value, limits):
            return
        row['shown'] = (value, limits)
        mixed = value is zpl_props.MIXED
        row['mixed'] = mixed
        widget.handler_block(row['handler'])
        try:
            if prop.kind == zpl_props.INT:
                low, high = limits
                adjustment = widget.get_adjustment()
                if mixed:
                    # One below the range, shown as a word: picking a number
                    # writes it, and stepping back to the word writes nothing.
                    adjustment.configure(low - 1, low - 1, high, 1, 10, 0)
                else:
                    # A value already outside the range is shown as it is
                    adjustment.configure(value, min(low, value), max(high, value),
                                         1, 10, 0)
                widget.update()
            elif prop.kind == zpl_props.BOOL:
                widget.set_inconsistent(mixed)
                widget.set_active(bool(value) if not mixed else False)
            else:
                widget.remove_all()
                codes = []
                if mixed:
                    widget.append_text(MIXED_TEXT)
                    codes.append(None)
                for label, code in prop.choices:
                    widget.append_text(label)
                    codes.append(code)
                row['codes'] = codes
                widget.set_active(codes.index(value) if value in codes else 0)
        finally:
            widget.handler_unblock(row['handler'])

    def _focus(self):
        top = self.get_toplevel()
        return top.get_focus() if isinstance(top, Gtk.Window) else None

    def _refresh_rows(self):
        """Read every row again, bar the one being typed into."""
        focus = self._focus()
        for row in self._rows:
            if focus is not None and _is_ancestor(row['widget'], focus):
                continue
            self._show_row(row, self._bound)

    def _row_changed(self, row, raw):
        prop, widget = row['prop'], row['widget']
        if prop.kind == zpl_props.INT:
            if row['mixed'] and raw == int(widget.get_adjustment().get_lower()):
                return                    # back on "Mixed": nothing chosen
            value = raw
        elif prop.kind == zpl_props.BOOL:
            # Chosen now, so it is one value from here on
            widget.set_inconsistent(False)
            row['mixed'] = False
            value = raw
        else:
            value = row['codes'][raw] if 0 <= raw < len(row['codes']) else None
            if value is None:
                return
        prop.write(self._window.design_canvas.document, self._bound, value)
        self._edited()

    # --- the element's own rows ---------------------------------------------

    def _form_changed(self):
        if self._form is None or not self._bound:
            return
        self._form.apply()
        self._edited()
        if gtk_editors.form_builder(self._bound[0]) is not self._builder:
            # Unticking the clock, say, makes it a plain text field, which
            # has rows of its own. Not rebuilt here, inside the signal of a
            # row about to be thrown away.
            self.queue_sync()
