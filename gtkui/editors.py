"""
The GTK element editors' rows, apart from the window they are shown in.

Each editor is a function that builds its rows into a box the host gives it
- an editor window's own content area, today - and returns an EditorForm:
the title, `apply()` to write the rows back onto the element, and the hooks
a host needs. The rows are the box's own children, so the window that holds
them, and anything walking it, finds them where it always did.

The Qt frontend builds the same editors in qtui/dialogs.py; the two are kept
row for row alike, and FUNCTIONAL_SPEC.md section 7 lists them.
"""

from contextlib import contextmanager
from types import SimpleNamespace

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk

from zplcore import fields as zpl_fields
from zplcore import fonts as zpl_fonts
from zplcore import geometry
from zplcore import graphic_store
from zplcore import model
from zplcore import textraster
from zplcore import tips as zpl_tips
from zplcore.model import (DIAGONAL_DIRECTIONS, FRAME_COLOURS, ORIENTATIONS,
                           STORED_GRAPHIC_COMMANDS, STORED_GRAPHIC_DEVICES,
                           TEXT_BLOCK_JUSTIFICATIONS, TEXT_BLOCKS,
                           TEXT_DIRECTIONS, TEXT_JUSTIFICATIONS,
                           BarcodeElement, CircleElement, DiagonalLineElement,
                           EllipseElement, FieldBlock, FrameElement,
                           GraphicSymbolElement, StoredGraphicElement,
                           TextBlock, TextElement, graphic_symbol_choices)


class EditorForm:
    """An element editor's rows, apart from whatever window they are shown in.

    The rows were built into `content`, a vertical box the host owns.
    `apply()` writes every row back onto the element - the host redraws and
    records the change. `after_show()` is for the host to call once the rows
    are on screen: show_all() re-shows every row an editor hid, so the rows
    that depend on another (Wrap's, a symbology's) get their visibility pass
    then. `fit()` is the host's to set, for an editor whose rows come and go
    to ask its window to take the size they now want.

    `on_change(callback)` asks to hear about every change to a row, whether
    through an input (`watch`) or a button that changes what no input shows
    - a font chosen or cleared. A form calls `changed()` for those itself.
    """

    def __init__(self, title: str, content: Gtk.Box):
        self.title = title
        self.content = content
        self.apply = lambda: None
        self.after_show = lambda: None
        self.fit = lambda: None
        self._listeners = []
        self._muted = 0

    def on_change(self, callback) -> None:
        self._listeners.append(callback)

    def changed(self, *_args) -> None:
        if self._muted:
            return
        for callback in list(self._listeners):
            callback()

    @contextmanager
    def muted(self):
        """Rows set inside this are not changes: the form putting itself back
        in step with the element, not the user editing it."""
        self._muted += 1
        try:
            yield
        finally:
            self._muted -= 1

    def watch(self, root: Gtk.Widget) -> None:
        """Report every change to an input at or under `root` as a change to
        the form. A spin button is an entry too, so it is asked first; a ^FM
        position list reports through its rows, added, edited or removed."""
        if isinstance(root, Gtk.SpinButton):
            root.connect("value-changed", self.changed)
        elif isinstance(root, Gtk.Entry):
            root.connect("changed", self.changed)
        elif isinstance(root, Gtk.ComboBox):
            root.connect("changed", self.changed)
        elif isinstance(root, Gtk.ToggleButton):
            root.connect("toggled", self.changed)
        elif isinstance(root, Gtk.TextView):
            root.get_buffer().connect("changed", self.changed)
        elif isinstance(root, Gtk.TreeView):
            rows = root.get_model()
            for signal in ("row-changed", "row-inserted", "row-deleted"):
                rows.connect(signal, self.changed)
        if isinstance(root, Gtk.Container) and not isinstance(
                root, (Gtk.ComboBox, Gtk.TreeView)):
            for child in root.get_children():
                self.watch(child)

    def finish(self, apply, after_show=None) -> 'EditorForm':
        """Take the function that writes the rows back, and the visibility
        pass, and start listening to every input the form was built with."""
        self.apply = apply
        if after_show is not None:
            self.after_show = after_show
        self.watch(self.content)
        return self


def set_tip(widget, key: str, label=None) -> None:
    """Give `widget` the tip zplcore.tips holds for `key`, and its row's
    `label` too, so hovering either explains it. GTK wraps a long tooltip
    itself. A key the table doesn't have is a mistake here, and fails."""
    set_tip_text(widget, zpl_tips.TIPS[key], label)


def set_tip_text(widget, text: str, label=None) -> None:
    """set_tip for a tip looked up some other way - a barcode row's, which
    depends on the symbology (zplcore.tips.PARAMETERS, CHECK_DIGIT)."""
    widget.set_tooltip_text(text)
    if label is not None:
        label.set_tooltip_text(text)


def choose_mapped_font(parent, title="Printer Names"):
    """The font_names.ini entry the user picks, or None.

    A plain list of name, face and file: the family chooser cannot show a
    face on its own, and these are the faces the map tells apart.
    """
    entries = zpl_fonts.mapped_fonts()
    if not entries:
        return None
    dialog = Gtk.Dialog(title=title, transient_for=parent, modal=True)
    dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
    dialog.add_button("OK", Gtk.ResponseType.OK)
    dialog.set_default_response(Gtk.ResponseType.OK)
    dialog.set_default_size(520, 360)

    store = Gtk.ListStore(str, str, str)
    for entry in entries:
        face = f"{entry.family} {entry.style}".strip()
        store.append([entry.name, face, entry.path])
    view = Gtk.TreeView(model=store)
    view.set_name("font_names_list")
    for column, heading in enumerate(("Name", "Face", "File")):
        view.append_column(Gtk.TreeViewColumn(
            heading, Gtk.CellRendererText(), text=column))
    view.connect("row-activated",
                 lambda *_a: dialog.response(Gtk.ResponseType.OK))
    view.get_selection().select_path(Gtk.TreePath(0))

    scroller = Gtk.ScrolledWindow()
    scroller.set_vexpand(True)
    scroller.add(view)
    box = dialog.get_content_area()
    box.set_border_width(8)
    box.pack_start(scroller, True, True, 0)
    dialog.show_all()

    response = dialog.run()
    model_, treeiter = view.get_selection().get_selected()
    chosen = None
    if response == Gtk.ResponseType.OK and treeiter is not None:
        chosen = entries[model_.get_path(treeiter).get_indices()[0]]
    dialog.destroy()
    return chosen


def _make_row(content, label_text, widget, label_width: int = 130,
              tip: str = None):
    """One labelled row in a dialog's content area, explained by the tip
    zplcore.tips holds for `tip`, on the field and its label, when given.

    Returns the row and its label, so a caller that needs to hide the row
    later (a barcode dialog whose fields depend on the symbology chosen) or
    relabel it does not have to reach back into `content` to find it.
    """
    row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    label = Gtk.Label(label=label_text)
    label.set_size_request(label_width, -1)
    label.set_halign(Gtk.Align.END)
    if tip is not None:
        set_tip(widget, tip, label)
    row.pack_start(label, False, False, 0)
    row.pack_start(widget, True, True, 0)
    content.pack_start(row, False, False, 0)
    return row, label


def _tip_stacked(content, widget, key: str) -> None:
    """Name and tip a field an editor stacked under its label, and the label
    above it too - the widget packed just before it."""
    widget.set_name(key)
    children = content.get_children()
    at = children.index(widget)
    label = children[at - 1] if at and isinstance(children[at - 1], Gtk.Label) else None
    set_tip(widget, key, label)


def _make_spin(value, lower, upper):
    """A whole-number spin button over a range."""
    spin = Gtk.SpinButton()
    spin.set_adjustment(Gtk.Adjustment(value=value, lower=lower,
                                       upper=upper, step_increment=1))
    spin.set_numeric(True)
    return spin


def _make_ratio_spin(value, lower, upper):
    """A one-decimal spin button, for a barcode's wide-to-narrow ratio."""
    spin = Gtk.SpinButton()
    spin.set_adjustment(Gtk.Adjustment(value=value, lower=lower,
                                       upper=upper, step_increment=0.1))
    spin.set_digits(1)
    spin.set_numeric(True)
    return spin


def _make_name_row(content, element, label_width: int = 130):
    """The ID row of an element that carries data: what a Python program
    finds it by when it fills the label in (Label.fill). Kept as an ^FX comment,
    which a printer ignores. Returns the function that applies it."""
    entry = Gtk.Entry()
    entry.set_text(element.element_id)
    entry.set_placeholder_text("optional - Python fills this element in by ID")
    entry.set_name("element_id")
    _make_row(content, "ID:", entry, label_width, tip='element_id')

    def apply_to(target):
        target.element_id = entry.get_text()

    return apply_to


def _make_field_number_rows(content, element, label_width: int = 130):
    """The ^FN controls, for a barcode.

    Text no longer uses this: a numbered text field gets its own creation
    button and its own dedicated editor - the same way ^SN and ^FC already
    got one - see numbered_form. ^FN stays
    here for a barcode, though: a recalled stored-format barcode is common
    and already tested, unlike a serialized or clock-substituted one, so it
    keeps a row rather than moving out entirely.

    A barcode either prints a literal or takes its data from a numbered field
    the printer fills in, so this is a tick rather than a number that has to
    mean "none" - 0 is a field number ZPL allows. Returns the function that
    applies it, so the two editors cannot disagree about what OK does.
    """
    check = Gtk.CheckButton(label="Data comes from a numbered field (^FN)")
    check.set_active(element.field_number is not None)
    check.set_name("variable")
    _make_row(content, "Variable:", check, label_width, tip='variable')

    number = _make_spin(element.field_number or 0, 0, zpl_fields.MAX_NUMBER)
    number.set_name("field_number")
    _make_row(content, "Field Number:", number, label_width,
              tip='field_number')

    prompt = Gtk.Entry()
    prompt.set_text(element.field_prompt or '')
    prompt.set_placeholder_text("shown on the canvas and on a printer keypad")
    prompt.set_name("field_prompt")
    _make_row(content, "Field Name:", prompt, label_width, tip='field_prompt')

    def on_toggled(button):
        number.set_sensitive(button.get_active())
        prompt.set_sensitive(button.get_active())

    on_toggled(check)
    check.connect("toggled", on_toggled)

    def apply_to(target):
        if check.get_active():
            target.field_number = int(number.get_value())
            target.field_prompt = prompt.get_text() or None
        else:
            target.field_number = None
            target.field_prompt = None

    return apply_to


def _make_combo(choices, current):
    """A combo over (label, code) choices, plus the codes to read it back."""
    combo = Gtk.ComboBoxText()
    for label_text, _code in choices:
        combo.append_text(label_text)
    codes = [code for _l, code in choices]
    combo.set_active(codes.index(current) if current in codes else 0)
    return combo, codes


def _text_format_rows(window, editor, content, element, show_text=None):
    """The rows every text-like editor shares: ^FP direction and gap, ^FR,
    the font, and the ^FB/^TB wrap block.

    `apply()` can change the element beyond what these rows say - a line
    break typed with Wrap clear gets a block, and clearing Wrap joins the
    lines - and then puts the rows, and through `show_text` the caller's own
    text row, back in step with it. A window applies once and closes, but the
    Properties panel applies on every change, and rows left behind would
    undo the block on the next keystroke.

    Edit Text and Edit Time Field both call this, so a clock field is
    styled the same way a plain one is. The caller adds its own text row,
    size and orientation first, sets the element's text, then calls
    `apply()`, which reads these fields back onto the element.
    `text_block_chosen()` says whether a ^TB is ticked, because the caller
    must know before it sets the text: a ^TB has no line break to write a
    typed one as. `sync()` must be called once the editor is shown, since
    its show_all() would otherwise re-show every row this hid.
    """
    def make_row(lbl_text, widget, tip=None):
        return _make_row(content, lbl_text, widget, tip=tip)[0]

    # ^FP: which way the characters run inside that turn, and how
    # far apart
    direction_combo, direction_codes = _make_combo(
        TEXT_DIRECTIONS, element.direction)
    direction_combo.set_name("direction")
    make_row("Direction:", direction_combo, tip='direction')

    gap_spin = _make_spin(element.char_gap, 0, TextElement.MAX_CHAR_GAP)
    gap_spin.set_name("char_gap")
    gap_spin.set_name("char_gap")
    make_row("Character Gap:", gap_spin, tip='char_gap')

    fr_check = Gtk.CheckButton(label="Reverse print (^FR)")
    fr_check.set_name("reverse_print")
    fr_check.set_active(element.reverse_print)
    make_row("Reverse:", fr_check, tip='reverse_print')

    # Font chooser (installed families only)
    selected_font = [element.font_path, element.font_family]

    font_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    font_lbl = Gtk.Label(label="Font:")
    font_lbl.set_size_request(130, -1)
    font_lbl.set_halign(Gtk.Align.END)
    font_row.pack_start(font_lbl, False, False, 0)

    def _font_display():
        if selected_font[1]:
            mapped = zpl_fonts.mapped_name_for_file(selected_font[0])
            if mapped:
                return f"{selected_font[1]} ({mapped})"
            return selected_font[1]
        canvas_family = window.design_canvas.font_family
        return f"Default ({canvas_family})" if canvas_family else "Default"

    font_name_lbl = Gtk.Label(label=_font_display())
    font_name_lbl.set_halign(Gtk.Align.START)
    font_name_lbl.set_ellipsize(3)  # PANGO_ELLIPSIZE_END
    font_row.pack_start(font_name_lbl, True, True, 0)

    choose_font_btn = Gtk.Button(label="Choose\u2026")
    font_row.pack_start(choose_font_btn, False, False, 0)

    # Only when font_names.ini has something to offer
    mapped_font_btn = None
    if zpl_fonts.mapped_fonts():
        mapped_font_btn = Gtk.Button(label="Printer names\u2026")
        mapped_font_btn.set_name("font_names")
        set_tip(mapped_font_btn, 'font_names')
        font_row.pack_start(mapped_font_btn, False, False, 0)

    clear_font_btn = Gtk.Button(label="Clear")
    font_row.pack_start(clear_font_btn, False, False, 0)

    # On the row, so the label, the name and both buttons all show it
    font_row.set_name("font")
    set_tip(font_row, 'font')
    content.pack_start(font_row, False, False, 0)

    def on_choose_font(btn):
        families = zpl_fonts.list_ttf_families()
        if not families:
            window.show_error_dialog("No TrueType fonts were found on this system.")
            return
        fdialog = Gtk.FontChooserDialog(title="Choose Font", parent=content.get_toplevel())
        # Size and style come from the Font Height/Width fields, and only
        # TrueType can be uploaded to the printer, so offer families only.
        fdialog.set_level(Gtk.FontChooserLevel.FAMILY)
        fdialog.set_filter_func(lambda family, face: family.get_name() in families)
        if selected_font[1]:
            fdialog.set_font(selected_font[1])
        resp = fdialog.run()
        family = None
        if resp in (Gtk.ResponseType.OK, Gtk.ResponseType.APPLY):
            desc = fdialog.get_font_desc()
            family = desc.get_family() if desc else None
        fdialog.destroy()
        if not family:
            return
        path = zpl_fonts.file_for_family(family)
        if not path:
            window.show_error_dialog(f"No TrueType file found for '{family}'.")
            return
        selected_font[0], selected_font[1] = path, family
        font_name_lbl.set_text(_font_display())
        editor.changed()

    def on_font_clear(btn):
        selected_font[0], selected_font[1] = None, None
        font_name_lbl.set_text(_font_display())
        editor.changed()

    def on_mapped_font(btn):
        entry = choose_mapped_font(content.get_toplevel())
        if entry is None:
            return
        selected_font[0], selected_font[1] = entry.path, entry.family
        font_name_lbl.set_text(_font_display())
        editor.changed()

    choose_font_btn.connect("clicked", on_choose_font)
    clear_font_btn.connect("clicked", on_font_clear)
    if mapped_font_btn is not None:
        mapped_font_btn.connect("clicked", on_mapped_font)

    # Wrapping (^FB or ^TB). Each kind's fields start from the
    # element's block when it is that kind, and otherwise from one
    # that draws the text as it is now.
    document = window.design_canvas.document
    block = (element.block if isinstance(element.block, FieldBlock)
             else element.default_block(document.font_path,
                                        document.dpi))
    text_block = (element.block
                  if isinstance(element.block, TextBlock)
                  else element.default_text_block(document.font_path,
                                                  document.dpi))

    wrap_check = Gtk.CheckButton(label="Wrap the text into a block")
    wrap_check.set_active(element.block is not None)
    wrap_check.set_name("wrap")
    make_row("Wrap:", wrap_check, tip='wrap')

    kind_combo, kind_codes = _make_combo(
        TEXT_BLOCKS,
        'TB' if isinstance(element.block, TextBlock) else 'FB')
    kind_combo.set_name("block_kind")
    kind_combo.set_name("block_kind")
    make_row("Block:", kind_combo, tip='block_kind')

    block_width_spin = _make_spin((element.block or block).width,
                                  10, 2000)
    block_width_spin.set_name("block_width")
    make_row("Wrap Width:", block_width_spin, tip='block_width')

    block_height_spin = _make_spin(text_block.height, 1, 32000)
    block_height_spin.set_name("block_height")
    block_height_spin.set_name("block_height")
    height_row = make_row("Block Height:", block_height_spin, tip='block_height')

    max_lines_spin = _make_spin(block.max_lines, 1, 64)
    max_lines_spin.set_name("max_lines")
    lines_row = make_row("Max Lines:", max_lines_spin, tip='max_lines')

    spacing_spin = _make_spin(block.line_spacing, -100, 100)
    spacing_spin.set_name("line_spacing")
    spacing_row = make_row("Line Spacing:", spacing_spin, tip='line_spacing')

    justify_combo, justify_codes = _make_combo(TEXT_JUSTIFICATIONS,
                                               block.justification)
    justify_combo.set_name("justification")
    justify_row = make_row("Justification:", justify_combo, tip='justification')

    # A ^TB's lines align by the field's own justification, which
    # has neither a centre nor a justified
    text_justify_combo, text_justify_codes = _make_combo(
        TEXT_BLOCK_JUSTIFICATIONS,
        'R' if element.justify == geometry.JUSTIFY_RIGHT
        else 'L')
    text_justify_combo.set_name("text_justification")
    text_justify_combo.set_name("text_justification")
    text_justify_row = make_row("Justification:", text_justify_combo, tip='text_justification')

    indent_spin = _make_spin(block.indent, 0, 2000)
    indent_spin.set_name("indent")
    indent_row = make_row("Indent:", indent_spin, tip='indent')

    block_fields = (kind_combo, block_width_spin, block_height_spin,
                    max_lines_spin, spacing_spin, justify_combo,
                    text_justify_combo, indent_spin)
    field_block_rows = (lines_row, spacing_row, justify_row,
                        indent_row)
    text_block_rows = (height_row, text_justify_row)

    def on_wrap_toggled(_widget):
        for field in block_fields:
            field.set_sensitive(wrap_check.get_active())
        text = kind_codes[kind_combo.get_active()] == 'TB'
        for rows, shown in ((field_block_rows, not text),
                            (text_block_rows, text)):
            for row in rows:
                row.set_visible(shown)
        # The manual does not say what ^FB does with a direction, so
        # a block keeps whichever it has and offers no other.
        direction_combo.set_sensitive(not wrap_check.get_active())

    wrap_check.connect("toggled", on_wrap_toggled)
    kind_combo.connect("changed", on_wrap_toggled)

    def text_block_chosen():
        return (wrap_check.get_active()
                and kind_codes[kind_combo.get_active()] == 'TB')

    def show_block(new_block):
        """Tick Wrap and show a field block the element was just given."""
        with editor.muted():
            wrap_check.set_active(True)
            kind_combo.set_active(kind_codes.index('FB'))
            block_width_spin.set_value(new_block.width)
            max_lines_spin.set_value(new_block.max_lines)
            spacing_spin.set_value(new_block.line_spacing)
            justify_combo.set_active(
                justify_codes.index(new_block.justification)
                if new_block.justification in justify_codes else 0)
            indent_spin.set_value(new_block.indent)
        on_wrap_toggled(wrap_check)

    def apply():
        element.direction = direction_codes[direction_combo.get_active()]
        element.char_gap = int(gap_spin.get_value())
        element.reverse_print = fr_check.get_active()

        if text_block_chosen():
            element.set_text_block(
                int(block_width_spin.get_value()),
                int(block_height_spin.get_value()),
                text_justify_codes[text_justify_combo.get_active()])
        elif wrap_check.get_active():
            # Assigned rather than mutated: the block on the element
            # may be the one an undo snapshot is holding.
            element.block = FieldBlock(
                int(block_width_spin.get_value()),
                int(max_lines_spin.get_value()),
                int(spacing_spin.get_value()),
                justify_codes[justify_combo.get_active()],
                int(indent_spin.get_value()))
        elif element.block is not None:
            # Unticked. A forced break left behind would print as the
            # two characters it is written with, so the lines are
            # joined rather than abandoned to the printer.
            element.text = textraster.join_lines(element.text)
            element.block = None
            if show_text is not None:
                with editor.muted():
                    show_text()
        elif textraster.FORCED_BREAK in element.text:
            # A break typed into an element that never had a block
            # still needs one, for the same reason. Sized to the
            # longest line, so nothing moves.
            element.block = element.default_block(
                document.font_path, document.dpi)
            show_block(element.block)

        window.design_canvas.sync_text_width(element)

        new_path, new_family = selected_font
        if new_path != element.font_path:
            if new_path:
                # The font is only recorded here; it is uploaded at print
                # time, so choosing a font never blocks on the network.
                printer_name = zpl_fonts.printer_font_name(
                    new_path, taken=window._printer_font_names(
                        exclude=element, same_file=new_path))
                window.design_canvas.set_element_font(element, new_path, new_family, printer_name)
                window.renderer.register_font(printer_name, new_path)
            else:
                element.font_path = None
                element.font_family = None
                element.printer_font_name = None
                window.design_canvas.queue_draw()

    return SimpleNamespace(text_block_chosen=text_block_chosen,
                           apply=apply,
                           sync=lambda: on_wrap_toggled(wrap_check))


def time_form(window, content, element) -> EditorForm:
    """Edit Time Field's rows, built into `content`."""
    # A clock field (^FC). It takes the same font,
    # direction and wrap rows as the text editor below, through
    # _text_format_rows, but no Data Source selector, since this
    # dialog *is* the ^FC source. The plain text editor carries no
    # field-source mechanism of its own at all any more: ^FN, ^SN and
    # ^FC each moved out to their own dialog. Unticking the clock
    # checkbox turns the element back into a plain static text field,
    # and the next double-click then falls through to the regular
    # text editor instead of here.

    editor = EditorForm("Edit Time Field", content)
    content.set_spacing(4)
    content.set_margin_start(8)
    content.set_margin_end(8)
    content.set_margin_top(8)
    content.set_margin_bottom(8)

    def make_row(label_text, widget, tip=None):
        _make_row(content, label_text, widget, tip=tip)

    text_entry = Gtk.Entry()
    text_entry.set_text(element.text)
    text_entry.set_name("clock_text")
    make_row("Format:", text_entry, tip='clock_text')

    height_spin = _make_spin(element.font_height, 8, 500)
    make_row("Font Height:", height_spin)

    width_spin = _make_spin(element.font_width, 8, 500)
    width_spin.set_name("font_width")
    make_row("Font Width:", width_spin, tip='font_width')

    orientation_combo, orientation_codes = _make_combo(
        ORIENTATIONS, element.orientation)
    orientation_combo.set_name("orientation")
    make_row("Orientation:", orientation_combo, tip='orientation')

    fmt = _text_format_rows(window, editor, content, element,
                            show_text=lambda: text_entry.set_text(element.text))

    clock_check = Gtk.CheckButton(
        label="Comes from the printer's clock (^FC)")
    clock_check.set_active(element.clock_format)
    clock_check.set_name("clock_format")
    make_row("Clock:", clock_check, tip='clock_format')

    content.show_all()

    apply_name = _make_name_row(content, element)

    def apply():
        apply_name(element)
        element.text = text_entry.get_text()
        element.font_height = int(height_spin.get_value())
        element.font_width = int(width_spin.get_value())
        element.orientation = orientation_codes[
            orientation_combo.get_active()]
        element.height = element.font_height
        fmt.apply()
        if clock_check.get_active():
            element.clock_format = True
        else:
            # Off - any custom trigger characters the field had
            # no longer mean anything once it is plain text.
            element.clock_format = False
            element.clock_chars = None
        # Last, because the box is measured from what the canvas
        # will draw, and that is the wrapped marker for as long
        # as this stays a clock field.
        window.design_canvas.document.sync_text_width(element)

    return editor.finish(apply, after_show=fmt.sync)


def serial_form(window, content, element) -> EditorForm:
    """Edit Serial Field's rows, built into `content`."""
    # A serialized field (^SN). It takes the same font,
    # direction and wrap rows as the text editor below, through
    # _text_format_rows, but no Data Source selector, since this
    # dialog *is* the ^SN source. The plain text editor carries no
    # field-source mechanism of its own at all any more: ^FN, ^SN and
    # ^FC each moved out to their own dialog. Unticking the serial
    # checkbox turns the element back into a plain static text
    # field, and the next double-click then falls through to the
    # regular text editor instead of here.

    editor = EditorForm("Edit Serial Field", content)
    content.set_spacing(4)
    content.set_margin_start(8)
    content.set_margin_end(8)
    content.set_margin_top(8)
    content.set_margin_bottom(8)

    def make_row(label_text, widget, tip=None):
        _make_row(content, label_text, widget, tip=tip)

    text_entry = Gtk.Entry()
    text_entry.set_text(element.text)
    text_entry.set_name("serial_start")
    make_row("Start Value:", text_entry, tip='serial_start')

    increment_spin = _make_spin(
        element.serial_increment if element.serial_increment is not None
        else 1, -999999, 999999)
    increment_spin.set_name("serial_increment")
    make_row("Increment:", increment_spin, tip='serial_increment')

    leading_zero_check = Gtk.CheckButton(label="Add leading zeros")
    leading_zero_check.set_active(element.serial_leading_zero)
    leading_zero_check.set_name("serial_leading_zero")
    make_row("", leading_zero_check, tip='serial_leading_zero')

    height_spin = _make_spin(element.font_height, 8, 500)
    make_row("Font Height:", height_spin)

    width_spin = _make_spin(element.font_width, 8, 500)
    width_spin.set_name("font_width")
    make_row("Font Width:", width_spin, tip='font_width')

    orientation_combo, orientation_codes = _make_combo(
        ORIENTATIONS, element.orientation)
    orientation_combo.set_name("orientation")
    make_row("Orientation:", orientation_combo, tip='orientation')

    fmt = _text_format_rows(window, editor, content, element,
                            show_text=lambda: text_entry.set_text(element.text))

    serial_check = Gtk.CheckButton(
        label="Auto-increments each print (^SN)")
    serial_check.set_active(element.serial_increment is not None)
    serial_check.set_name("serial_format")
    make_row("Serial:", serial_check, tip='serial_format')

    content.show_all()

    apply_name = _make_name_row(content, element)

    def apply():
        apply_name(element)
        element.text = text_entry.get_text()
        element.font_height = int(height_spin.get_value())
        element.font_width = int(width_spin.get_value())
        element.orientation = orientation_codes[
            orientation_combo.get_active()]
        element.height = element.font_height
        fmt.apply()
        if serial_check.get_active():
            element.serial_start = element.text
            element.serial_increment = int(increment_spin.get_value())
            element.serial_leading_zero = leading_zero_check.get_active()
        else:
            # Off - the field is plain static text now, showing
            # whatever value it last had.
            element.serial_start = None
            element.serial_increment = None
            element.serial_leading_zero = False
        # Last, because the box is measured from what the canvas
        # will draw, and that is the wrapped marker for as long
        # as this stays a serial field.
        window.design_canvas.document.sync_text_width(element)

    return editor.finish(apply, after_show=fmt.sync)


def numbered_form(window, content, element) -> EditorForm:
    """Edit Numbered Field's rows, built into `content`."""
    # A numbered field (^FN). The font, direction, wrap and
    # reverse rows are the ones Edit Text has (_text_format_rows); the
    # one thing it leaves out is a Data Source selector, since this
    # dialog *is* the ^FN source. Unticking the variable checkbox
    # turns the element back into a plain static text field, and the
    # next double-click then falls through to the regular text
    # editor instead of here. ^FN is still available on a barcode,
    # through _make_field_number_rows in the barcode branch below -
    # unlike ^SN/^FC, a recalled stored-format barcode is common and
    # already tested, so it keeps a row there rather than moving out
    # entirely.

    editor = EditorForm("Edit Numbered Field", content)
    content.set_spacing(4)
    content.set_margin_start(8)
    content.set_margin_end(8)
    content.set_margin_top(8)
    content.set_margin_bottom(8)

    def make_row(label_text, widget, tip=None):
        _make_row(content, label_text, widget, tip=tip)

    text_entry = Gtk.Entry()
    text_entry.set_text(element.text)
    text_entry.set_placeholder_text(
        "optional - shared with every other field carrying the same number")
    text_entry.set_name("numbered_text")
    make_row("Text:", text_entry, tip='numbered_text')

    number_spin = _make_spin(element.field_number or 0, 0,
                             zpl_fields.MAX_NUMBER)
    number_spin.set_name("field_number")
    make_row("Field Number:", number_spin, tip='field_number')

    prompt_entry = Gtk.Entry()
    prompt_entry.set_text(element.field_prompt or '')
    prompt_entry.set_placeholder_text(
        "shown on the canvas and on a printer keypad")
    prompt_entry.set_name("field_prompt")
    make_row("Field Name:", prompt_entry, tip='field_prompt')

    height_spin = _make_spin(element.font_height, 8, 500)
    make_row("Font Height:", height_spin)

    width_spin = _make_spin(element.font_width, 8, 500)
    width_spin.set_name("font_width")
    make_row("Font Width:", width_spin, tip='font_width')

    orientation_combo, orientation_codes = _make_combo(
        ORIENTATIONS, element.orientation)
    orientation_combo.set_name("orientation")
    make_row("Orientation:", orientation_combo, tip='orientation')

    fmt = _text_format_rows(window, editor, content, element,
                            show_text=lambda: text_entry.set_text(element.text))

    variable_check = Gtk.CheckButton(
        label="Data comes from a numbered field (^FN)")
    variable_check.set_active(element.field_number is not None)
    variable_check.set_name("variable")
    make_row("Variable:", variable_check, tip='variable')

    content.show_all()

    apply_name = _make_name_row(content, element)

    def apply():
        apply_name(element)
        element.text = text_entry.get_text()
        element.font_height = int(height_spin.get_value())
        element.font_width = int(width_spin.get_value())
        element.orientation = orientation_codes[
            orientation_combo.get_active()]
        element.height = element.font_height
        fmt.apply()
        if variable_check.get_active():
            element.field_number = int(number_spin.get_value())
            element.field_prompt = prompt_entry.get_text() or None
        else:
            element.field_number = None
            element.field_prompt = None
        # Last, because the box is measured from what the canvas
        # will draw, and that is the placeholder for as long as
        # this stays a numbered field with no literal of its own.
        window.design_canvas.document.sync_text_width(element)

    return editor.finish(apply, after_show=fmt.sync)


def text_form(window, content, element) -> EditorForm:
    """Edit Text's rows, built into `content`."""
    editor = EditorForm("Edit Text", content)
    content.set_spacing(4)
    content.set_margin_start(8)
    content.set_margin_end(8)
    content.set_margin_top(8)
    content.set_margin_bottom(8)

    def make_row(lbl_text, widget, tip=None):
        return _make_row(content, lbl_text, widget, tip=tip)[0]

    # Text input. Multi-line, because ZPL's forced break is two
    # characters a user should never have to spell: Enter here becomes
    # \& on the way out.
    text_view = Gtk.TextView()
    text_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
    # A ^TB breaks no line at \&, so its data is shown as it is
    text_view.get_buffer().set_text(
        element.text if isinstance(element.block, TextBlock)
        else textraster.to_editor(element.text))
    text_scroll = Gtk.ScrolledWindow()
    text_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
    text_scroll.set_shadow_type(Gtk.ShadowType.IN)
    text_scroll.set_size_request(-1, 90)
    text_scroll.add(text_view)
    make_row("Text:", text_scroll)

    height_spin = _make_spin(element.font_height, 8, 500)
    make_row("Font Height:", height_spin)

    width_spin = _make_spin(element.font_width, 8, 500)
    width_spin.set_name("font_width")
    make_row("Font Width:", width_spin, tip='font_width')

    orientation_combo, orientation_codes = _make_combo(
        ORIENTATIONS, element.orientation)
    orientation_combo.set_name("orientation")
    make_row("Orientation:", orientation_combo, tip='orientation')

    fmt = _text_format_rows(window, editor, content, element,
                            show_text=lambda: text_view.get_buffer().set_text(
                                textraster.to_editor(element.text)))

    apply_name = _make_name_row(content, element)

    def apply():
        apply_name(element)
        buffer = text_view.get_buffer()
        # A ^TB has no line break to write a typed one as, so it
        # is a space
        element.text = (textraster.from_editor_unbroken
                        if fmt.text_block_chosen()
                        else textraster.from_editor)(
            buffer.get_text(buffer.get_start_iter(),
                            buffer.get_end_iter(), False))
        element.font_height = int(height_spin.get_value())
        element.font_width = int(width_spin.get_value())
        element.orientation = orientation_codes[
            orientation_combo.get_active()]
        fmt.apply()

        window.design_canvas.document.sync_text_width(element)

    # _open_editor's own show_all() would otherwise re-show every row
    # this just hid - so the wrap-dependent ones only get their first
    # visibility pass once it has already run.
    return editor.finish(apply, after_show=fmt.sync)


def barcode_form(window, content, element) -> EditorForm:
    """Edit Barcode's rows, built into `content`."""
    editor = EditorForm("Edit Barcode", content)
    content.set_spacing(4)
    content.set_margin_start(8)
    content.set_margin_end(8)
    content.set_margin_top(8)
    content.set_margin_bottom(8)

    def make_row(label_text, widget, tip=None):
        return _make_row(content, label_text, widget, tip=tip)

    make_spin, make_combo = _make_spin, _make_combo

    symbology_combo, symbology_codes = make_combo(
        model.BARCODE_SYMBOLOGIES, element.symbology)
    make_row("Symbology:", symbology_combo)

    value_entry = Gtk.Entry()
    value_entry.set_text(element.barcode_value)
    make_row("Barcode Value:", value_entry)

    # The control characters a MaxiCode's message is built out of,
    # which no keyboard types. Each goes in at the cursor as a ^FH
    # escape, switching ^FH on if the field had none - held here
    # until OK, like every other row, so Cancel leaves the element
    # as it was.
    pending = {'indicator': element.hex_indicator}
    control_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                          spacing=4)

    def on_insert(_button, code):
        text, position, pending['indicator'] = zpl_fields.insert_escape(
            value_entry.get_text(), value_entry.get_position(), code,
            pending['indicator'])
        value_entry.set_text(text)
        value_entry.grab_focus_without_selecting()
        value_entry.set_position(position)

    for label, code in model.BARCODE_CONTROL_CHARACTERS:
        button = Gtk.Button(label=label)
        button.set_name(f"insert_{label}")
        set_tip(button, f"insert_{label}")
        button.connect('clicked', on_insert, code)
        control_box.pack_start(button, False, False, 0)
    control_box.set_name("insert")
    control_row, _control_label = make_row("Insert:", control_box,
                                           tip='insert')

    # Wide until the symbology's own range is known
    # (on_symbology_changed), so a height below 20 - a PDF417 or
    # MicroPDF417 row - is not clamped to 20 before that range can
    # allow it, and written back on OK.
    height_spin = make_spin(element.bar_height, 1, 32000)
    height_spin.set_name("bar_height")
    height_row, _height_label = make_row("Bar Height:", height_spin,
                                         tip='bar_height')

    module_spin = make_spin(element.module_width, 1, 20)
    module_spin.set_name("module_width")
    module_row, module_label = make_row("Module Width:", module_spin,
                                        tip='module_width')

    # The rows a symbology adds for its own parameters - a QR code's
    # error correction and mask, and so on - built from the same
    # catalogue the parser and the model read, so a symbology cannot
    # arrive carrying a parameter no editor can reach.
    #
    # One row per attribute, however many symbologies have it: a Data
    # Matrix and a PDF417 both have columns and rows. The label and the
    # choices are each symbology's own, so fill_extra_rows puts the chosen
    # one's in - now, for an editor driven rather than shown, and again
    # whenever the symbology changes. A row left showing another
    # symbology's choices would write one of them back on the next edit.
    extra_rows = {}
    for _symbology_key, rows in model.BARCODE_PARAMETERS.items():
        for attribute, row_label, choices in rows:
            if attribute in extra_rows:
                continue
            if isinstance(choices, model.BarcodeSpin):
                # A number with too many values to list - a TLC39's
                # MicroPDF417 row height.
                widget = make_spin(choices.lower, choices.lower,
                                   choices.upper)
            else:
                widget = Gtk.ComboBoxText()
            widget.set_name(attribute)
            row, label = make_row(row_label + ":", widget)
            # The codes a combo reads back with, and the choices it now
            # offers, are the symbology's: filled in by fill_extra_rows
            extra_rows[attribute] = SimpleNamespace(
                widget=widget, label=label, row=row, codes=None,
                choices=None)

    def fill_extra_rows(chosen):
        """Label each row the chosen symbology has as it does, offering its
        choices, set to the element's value."""
        for attribute, row_label, choices in model.BARCODE_PARAMETERS.get(
                chosen, ()):
            extra = extra_rows[attribute]
            extra.label.set_text(row_label + ":")
            set_tip_text(extra.widget, zpl_tips.PARAMETERS[(chosen, attribute)],
                         extra.label)
            current = getattr(element, attribute, None)
            with editor.muted():
                if isinstance(choices, model.BarcodeSpin):
                    extra.widget.get_adjustment().configure(
                        current if current is not None else choices.lower,
                        choices.lower, choices.upper, 1, 10, 0)
                    extra.choices = choices
                    continue
                if extra.choices is not choices:
                    extra.widget.remove_all()
                    for choice_label, _code in choices:
                        extra.widget.append_text(choice_label)
                    extra.codes = [code for _l, code in choices]
                    extra.choices = choices
                extra.widget.set_active(extra.codes.index(current)
                                        if current in extra.codes else 0)

    fill_extra_rows(element.symbology)

    ratio_spin = _make_ratio_spin(element.ratio, 2.0, 3.0)
    ratio_spin.set_name("ratio")
    ratio_row, _ratio_label = make_row("Ratio:", ratio_spin, tip='ratio')

    orientation_combo, orientation_codes = make_combo(
        model.BARCODE_ORIENTATIONS,
        (element.orientation or 'N').upper())
    orientation_combo.set_name("orientation")
    orientation_row, _orientation_label = make_row(
        "Orientation:", orientation_combo, tip='orientation')

    text_combo, text_codes = make_combo(
        model.BARCODE_TEXT_CHOICES,
        (element.show_text, element.text_above))
    text_combo.set_name("value_text")
    text_row, _text_label = make_row("Value Text:", text_combo,
                                     tip='value_text')

    font_spin = make_spin(int((element.font or element.DEFAULT_FONT)[1]), 6, 200)
    text_height_row, _font_label = make_row("Text Height:", font_spin)

    check_combo, check_codes = make_combo(model.BARCODE_CHECK_DIGIT,
                                          element.check_digit)
    check_combo.set_name("check_digit")
    check_row, check_label = make_row("Check Digit:", check_combo)

    def tip_check_digit(chosen):
        """Three different checksums, or a choice of showing one, under one
        row: the tip goes with the label the symbology gives it."""
        name = model.BARCODE_FEATURES[chosen]['check_digit']
        if name is not None:
            set_tip_text(check_combo, zpl_tips.CHECK_DIGIT[name], check_label)

    tip_check_digit(element.symbology)

    mode_combo, mode_codes = make_combo(model.BARCODE_MODES,
                                        element.mode)
    mode_combo.set_name("mode")
    mode_row, _mode_label = make_row("Mode:", mode_combo, tip='mode')

    fr_check = Gtk.CheckButton(label="Reverse print (^FR)")
    fr_check.set_name("reverse_print")
    fr_check.set_active(element.reverse_print)
    make_row("Reverse:", fr_check, tip='reverse_print')

    # ^FM: an origin for each symbol of a series, for the two
    # symbologies it places, as label positions. No rows is a barcode
    # placed by ^FO like any other; an origin not ticked is one ^FM
    # excludes.
    positions = Gtk.ListStore(int, int, bool)
    for position in element.series_positions():
        positions.append(list(position))
    positions_view = Gtk.TreeView(model=positions)
    positions_view.set_name("positions")
    for column, title in ((0, "X"), (1, "Y")):
        spin = Gtk.CellRendererSpin(
            editable=True, digits=0,
            adjustment=Gtk.Adjustment(value=0, lower=0, upper=32000,
                                      step_increment=1))

        def on_edited(_renderer, path, text, column=column):
            try:
                positions[path][column] = max(0, min(32000, int(text)))
            except ValueError:
                pass

        spin.connect('edited', on_edited)
        positions_view.append_column(
            Gtk.TreeViewColumn(title, spin, text=column))
    toggle = Gtk.CellRendererToggle(activatable=True)

    def on_toggled(_renderer, path):
        positions[path][2] = not positions[path][2]

    toggle.connect('toggled', on_toggled)
    positions_view.append_column(
        Gtk.TreeViewColumn("Print", toggle, active=2))

    def position_rows():
        return [tuple(row) for row in positions]

    def on_add_position(_button):
        if len(positions) < model.MAX_SERIES_ORIGINS:
            x, y = element.next_series_position(position_rows())
            positions.append([x, y, True])

    def on_remove_position(_button):
        chosen = positions_view.get_selection().get_selected()[1]
        if chosen is None and len(positions):
            chosen = positions.get_iter(len(positions) - 1)
        if chosen is not None:
            positions.remove(chosen)

    positions_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                            spacing=4)
    positions_box.pack_start(positions_view, True, True, 0)
    positions_buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                                spacing=4)
    for label, name, handler in (("Add", "add_position",
                                  on_add_position),
                                 ("Remove", "remove_position",
                                  on_remove_position)):
        button = Gtk.Button(label=label)
        button.set_name(name)
        button.connect('clicked', handler)
        positions_buttons.pack_start(button, False, False, 0)
    positions_box.pack_start(positions_buttons, False, False, 0)
    positions_box.set_name("positions")
    positions_row, _positions_label = make_row("Positions (^FM):",
                                               positions_box, tip='positions')

    apply_field_number = _make_field_number_rows(content, element)
    apply_name = _make_name_row(content, element)

    def on_symbology_changed(_combo):
        # Each symbology carries a different subset of these rows -
        # Code 128's mode, a check digit only some of them have (and
        # call something different), a ratio that only matters for
        # the two not drawn at a fixed one. Showing every row for
        # every symbology would offer a Mode a Code 39 barcode has
        # no ZPL parameter for at all.
        chosen = symbology_codes[symbology_combo.get_active()]
        features = model.BARCODE_FEATURES[chosen]
        mode_row.set_visible(features['mode'])
        ratio_row.set_visible(features['ratio'])
        check_row.set_visible(features['check_digit'] is not None)
        if features['check_digit'] is not None:
            check_label.set_text(features['check_digit'] + ":")
            tip_check_digit(chosen)
        # A matrix symbology's size is its own grid, so it has no bar
        # height to offer and its module width is a magnification of
        # that grid - and no interpretation line either.
        height_row.set_visible(features['height'] is not None)
        if features['height'] is not None:
            height_spin.set_range(*features['height'])
        module_row.set_visible(features['module_width'] is not None)
        if features['module_width'] is not None:
            module_label.set_text(features['module_width'] + ":")
        text_row.set_visible(bool(features['text']))
        # A MaxiCode has no orientation to set, and a message built
        # out of characters only the Insert buttons can write.
        orientation_row.set_visible(features['orientation'])
        control_row.set_visible(features['control_chars'])
        positions_row.set_visible(chosen in model.BARCODE_SERIES)
        text_height_row.set_visible(bool(features['text']))
        wanted = [attribute for attribute, _l, _c
                  in model.BARCODE_PARAMETERS.get(chosen, ())]
        for attribute, extra in extra_rows.items():
            extra.row.set_visible(attribute in wanted)
        fill_extra_rows(chosen)
        # Rows came and went: the window around them takes their new size
        editor.fit()

    symbology_combo.connect('changed', on_symbology_changed)

    def apply():
        element.symbology = symbology_codes[symbology_combo.get_active()]
        element.barcode_value = value_entry.get_text()
        element.hex_indicator = pending['indicator']
        element.bar_height = int(height_spin.get_value())
        element.module_width = int(module_spin.get_value())
        element.ratio = ratio_spin.get_value()
        element.orientation = orientation_codes[orientation_combo.get_active()]
        element.show_text, element.text_above = text_codes[text_combo.get_active()]
        # From the catalogue rather than from whether the row is
        # on screen: an editor driven rather than clicked has no
        # visible widgets at all.
        for attribute, _l, _c in model.BARCODE_PARAMETERS.get(
                element.symbology, ()):
            extra = extra_rows[attribute]
            setattr(element, attribute,
                    int(extra.widget.get_value()) if extra.codes is None
                    else extra.codes[extra.widget.get_active()])
        apply_field_number(element)
        apply_name(element)
        element.check_digit = check_codes[check_combo.get_active()]
        element.mode = mode_codes[mode_combo.get_active()]
        element.reverse_print = fr_check.get_active()
        element.set_series_positions(
            position_rows()
            if element.symbology in model.BARCODE_SERIES else [])
        if element.show_text:
            # With the line switched on, name the font it prints in
            # rather than leaving it to whatever the printer has
            # selected.
            code = (element.font or element.DEFAULT_FONT)[0]
            size = int(font_spin.get_value())
            element.font = (code, size, size)
        element.sync_box()

    # _open_editor's own show_all() would otherwise re-show every row
    # this just hid - so the symbology-dependent ones only get their
    # first visibility pass once it has already run.
    return editor.finish(apply, after_show=lambda: on_symbology_changed(symbology_combo))


def stored_graphic_form(window, content, element) -> EditorForm:
    """Edit Stored Graphic's rows, built into `content`."""
    # ^XG/^IM name an image the printer holds, not one this file
    # carries the bytes for - see zplcore/graphic_store.py. Editing
    # this element only ever changes which name it recalls.

    editor = EditorForm("Edit Stored Graphic", content)
    content.set_spacing(4)
    content.set_margin_start(8)
    content.set_margin_end(8)
    content.set_margin_top(8)
    content.set_margin_bottom(8)

    def make_row(label_text, widget, tip=None):
        _make_row(content, label_text, widget, tip=tip)

    command_combo, command_codes = _make_combo(
        STORED_GRAPHIC_COMMANDS, element.command)
    command_combo.set_name("command")
    make_row("Command:", command_combo, tip='command')

    device, name, ext = graphic_store.split_device_spec(element.device_spec)
    device_combo, device_codes = _make_combo(STORED_GRAPHIC_DEVICES, device)
    device_combo.set_name("device")
    make_row("Device:", device_combo, tip='device')

    name_entry = Gtk.Entry()
    name_entry.set_text(name)
    name_entry.set_max_length(8)
    name_entry.set_name("name")
    make_row("Name:", name_entry, tip='name')

    ext_entry = Gtk.Entry()
    ext_entry.set_text(ext)
    ext_entry.set_name("extension")
    make_row("Extension:", ext_entry, tip='extension')

    mag_x_spin = _make_spin(element.mag_x, 1, 10)
    mag_x_spin.set_name("mag_x")
    make_row("Magnification X:", mag_x_spin, tip='mag_x')

    mag_y_spin = _make_spin(element.mag_y, 1, 10)
    mag_y_spin.set_name("mag_y")
    make_row("Magnification Y:", mag_y_spin, tip='mag_y')

    def on_command_changed(_combo):
        # ^IM has no magnification of its own - always 1,1.
        is_xg = command_codes[command_combo.get_active()] == 'XG'
        mag_x_spin.set_sensitive(is_xg)
        mag_y_spin.set_sensitive(is_xg)

    on_command_changed(command_combo)
    command_combo.connect("changed", on_command_changed)

    content.show_all()

    def apply():
        element.command = command_codes[command_combo.get_active()]
        device_code = device_codes[device_combo.get_active()]
        object_name = (name_entry.get_text().strip() or 'UNKNOWN').upper()
        extension = (ext_entry.get_text().strip() or 'GRF').upper()
        element.device_spec = f"{device_code}:{object_name}.{extension}"
        if element.command == 'XG':
            element.mag_x = int(mag_x_spin.get_value())
            element.mag_y = int(mag_y_spin.get_value())
        else:
            element.mag_x = element.mag_y = 1

    return editor.finish(apply)


def frame_form(window, content, element) -> EditorForm:
    """Edit Frame's rows, built into `content`."""
    editor = EditorForm("Edit Frame", content)

    # Frame width
    width_label = Gtk.Label(label="Width:")
    content.pack_start(width_label, False, False, 0)
    width_spin = Gtk.SpinButton()
    width_adj = Gtk.Adjustment(value=element.width, lower=10, upper=800, step_increment=1)
    width_spin.set_adjustment(width_adj)
    content.pack_start(width_spin, False, False, 0)

    # Frame height
    height_label = Gtk.Label(label="Height:")
    content.pack_start(height_label, False, False, 0)
    height_spin = Gtk.SpinButton()
    height_adj = Gtk.Adjustment(value=element.height, lower=10, upper=1200, step_increment=1)
    height_spin.set_adjustment(height_adj)
    content.pack_start(height_spin, False, False, 0)

    # Frame thickness. ^GB has no fixed limit; the useful maximum is
    # half the smaller side, where the border meets and fills solid.
    def max_thickness():
        return max(1, min(int(width_spin.get_value()),
                          int(height_spin.get_value())) // 2)

    thickness_label = Gtk.Label(label="Thickness:")
    content.pack_start(thickness_label, False, False, 0)
    thickness_spin = Gtk.SpinButton()
    thickness_adj = Gtk.Adjustment(
        value=min(element.thickness, max_thickness()),
        lower=1, upper=max_thickness(), step_increment=1)
    thickness_spin.set_adjustment(thickness_adj)
    thickness_spin.set_numeric(True)
    content.pack_start(thickness_spin, False, False, 0)
    _tip_stacked(content, thickness_spin, 'thickness')

    def on_size_changed(_spin):
        # Shrinking the frame must not leave an illegal thickness selectable
        thickness_adj.set_upper(max_thickness())

    width_spin.connect("value-changed", on_size_changed)
    height_spin.connect("value-changed", on_size_changed)

    # ^GB's colour and corner rounding
    colour_label = Gtk.Label(label="Colour:")
    content.pack_start(colour_label, False, False, 0)
    colour_combo, colour_codes = _make_combo(FRAME_COLOURS, element.colour)
    content.pack_start(colour_combo, False, False, 0)
    _tip_stacked(content, colour_combo, 'colour')

    rounding_label = Gtk.Label(label="Corner Rounding:")
    content.pack_start(rounding_label, False, False, 0)
    rounding_spin = _make_spin(element.rounding, 0,
                               FrameElement.MAX_ROUNDING)
    content.pack_start(rounding_spin, False, False, 0)
    _tip_stacked(content, rounding_spin, 'rounding')

    fr_check = Gtk.CheckButton(label="Reverse print (^FR)")
    fr_check.set_name("reverse_print")
    set_tip(fr_check, 'reverse_print')
    fr_check.set_active(element.reverse_print)
    content.pack_start(fr_check, False, False, 0)

    content.show_all()

    def apply():
        element.width = int(width_spin.get_value())
        element.height = int(height_spin.get_value())
        element.thickness = int(thickness_spin.get_value())
        element.colour = colour_codes[colour_combo.get_active()]
        element.rounding = int(rounding_spin.get_value())
        element.reverse_print = fr_check.get_active()

    return editor.finish(apply)


def circle_form(window, content, element) -> EditorForm:
    """Edit Circle's rows, built into `content`."""


    editor = EditorForm("Edit Circle", content)

    # ^GC's own range, so a small circle from a file is not enlarged
    # by accepting the editor it was only looked at in
    content.pack_start(Gtk.Label(label="Diameter:"), False, False, 0)
    diameter_spin = _make_spin(element.diameter,
                               CircleElement.MIN_DIAMETER,
                               CircleElement.MAX_DIAMETER)
    content.pack_start(diameter_spin, False, False, 0)

    # The border meets in the middle at the radius, and fills solid.
    def max_thickness():
        return max(1, int(diameter_spin.get_value()) // 2)

    content.pack_start(Gtk.Label(label="Thickness:"), False, False, 0)
    thickness_spin = _make_spin(min(element.thickness, max_thickness()),
                                1, max_thickness())
    content.pack_start(thickness_spin, False, False, 0)
    _tip_stacked(content, thickness_spin, 'thickness')

    def on_diameter_changed(_spin):
        # Shrinking the circle must not leave an illegal thickness
        # selectable
        thickness_spin.get_adjustment().set_upper(max_thickness())

    diameter_spin.connect("value-changed", on_diameter_changed)

    # ^GC's colour: the same black or white ^GB offers
    content.pack_start(Gtk.Label(label="Colour:"), False, False, 0)
    colour_combo, colour_codes = _make_combo(FRAME_COLOURS, element.colour)
    content.pack_start(colour_combo, False, False, 0)
    _tip_stacked(content, colour_combo, 'colour')

    fr_check = Gtk.CheckButton(label="Reverse print (^FR)")
    fr_check.set_name("reverse_print")
    set_tip(fr_check, 'reverse_print')
    fr_check.set_active(element.reverse_print)
    content.pack_start(fr_check, False, False, 0)

    content.show_all()

    def apply():
        element.diameter = int(diameter_spin.get_value())
        element.sync_box()
        # An adjustment whose upper bound drops does not pull its
        # value down with it, as Qt's setRange does.
        element.thickness = min(int(thickness_spin.get_value()),
                                max_thickness())
        element.colour = colour_codes[colour_combo.get_active()]
        element.reverse_print = fr_check.get_active()

    return editor.finish(apply)


def graphic_symbol_form(window, content, element) -> EditorForm:
    """Edit Symbol's rows, built into `content`."""


    editor = EditorForm("Edit Symbol", content)
    content.set_spacing(4)
    content.set_margin_start(8)
    content.set_margin_end(8)
    content.set_margin_top(8)
    content.set_margin_bottom(8)

    def make_row(label_text, widget, tip=None):
        _make_row(content, label_text, widget, tip=tip)

    # The five, and whatever else the field already holds - see
    # graphic_symbol_choices - so OK on ^GS^FDAB leaves it AB.
    symbol_combo, symbol_codes = _make_combo(
        graphic_symbol_choices(element.text), element.text)
    make_row("Symbol:", symbol_combo)

    # ^GS's own range, so a symbol from a file is neither cut down nor
    # enlarged by accepting the editor it was only looked at in
    height_spin = _make_spin(element.font_height,
                             GraphicSymbolElement.MIN_SIZE,
                             GraphicSymbolElement.MAX_SIZE)
    make_row("Height:", height_spin)

    width_spin = _make_spin(element.font_width,
                            GraphicSymbolElement.MIN_SIZE,
                            GraphicSymbolElement.MAX_SIZE)
    make_row("Width:", width_spin)

    orientation_combo, orientation_codes = _make_combo(
        ORIENTATIONS, element.orientation)
    orientation_combo.set_name("orientation")
    make_row("Orientation:", orientation_combo, tip='orientation')

    fr_check = Gtk.CheckButton(label="Reverse print (^FR)")
    fr_check.set_name("reverse_print")
    fr_check.set_active(element.reverse_print)
    make_row("Reverse:", fr_check, tip='reverse_print')

    content.show_all()

    apply_name = _make_name_row(content, element)

    def apply():
        apply_name(element)
        element.text = symbol_codes[symbol_combo.get_active()]
        element.font_height = int(height_spin.get_value())
        element.font_width = int(width_spin.get_value())
        element.orientation = orientation_codes[
            orientation_combo.get_active()]
        element.reverse_print = fr_check.get_active()
        element.sync_box()

    return editor.finish(apply)


def ellipse_form(window, content, element) -> EditorForm:
    """Edit Ellipse's rows, built into `content`."""


    editor = EditorForm("Edit Ellipse", content)

    # ^GE's own range, so an ellipse from a file is not cut down, or a
    # small one enlarged, by accepting the editor it was only looked
    # at in
    content.pack_start(Gtk.Label(label="Width:"), False, False, 0)
    width_spin = _make_spin(element.width, EllipseElement.MIN_SIDE,
                            EllipseElement.MAX_SIDE)
    content.pack_start(width_spin, False, False, 0)

    content.pack_start(Gtk.Label(label="Height:"), False, False, 0)
    height_spin = _make_spin(element.height, EllipseElement.MIN_SIDE,
                             EllipseElement.MAX_SIDE)
    content.pack_start(height_spin, False, False, 0)

    # The border meets in the middle at half the shorter side, and
    # the ellipse fills solid.
    def max_thickness():
        return max(1, min(int(width_spin.get_value()),
                          int(height_spin.get_value())) // 2)

    content.pack_start(Gtk.Label(label="Thickness:"), False, False, 0)
    thickness_spin = _make_spin(min(element.thickness, max_thickness()),
                                1, max_thickness())
    content.pack_start(thickness_spin, False, False, 0)
    _tip_stacked(content, thickness_spin, 'thickness')

    def on_size_changed(_spin):
        # Shrinking the ellipse must not leave an illegal thickness
        # selectable
        thickness_spin.get_adjustment().set_upper(max_thickness())

    width_spin.connect("value-changed", on_size_changed)
    height_spin.connect("value-changed", on_size_changed)

    # ^GE's colour: the same black or white ^GB offers
    content.pack_start(Gtk.Label(label="Colour:"), False, False, 0)
    colour_combo, colour_codes = _make_combo(FRAME_COLOURS, element.colour)
    content.pack_start(colour_combo, False, False, 0)
    _tip_stacked(content, colour_combo, 'colour')

    fr_check = Gtk.CheckButton(label="Reverse print (^FR)")
    fr_check.set_name("reverse_print")
    set_tip(fr_check, 'reverse_print')
    fr_check.set_active(element.reverse_print)
    content.pack_start(fr_check, False, False, 0)

    content.show_all()

    def apply():
        element.width = int(width_spin.get_value())
        element.height = int(height_spin.get_value())
        # An adjustment whose upper bound drops does not pull its
        # value down with it, as Qt's setRange does.
        element.thickness = min(int(thickness_spin.get_value()),
                                max_thickness())
        element.colour = colour_codes[colour_combo.get_active()]
        element.reverse_print = fr_check.get_active()

    return editor.finish(apply)


def diagonal_form(window, content, element) -> EditorForm:
    """Edit Diagonal Line's rows, built into `content`."""


    editor = EditorForm("Edit Diagonal Line", content)

    # ^GD's own range, so a line from a file is not cut down, or a
    # short one enlarged, by accepting the editor it was only looked
    # at in
    content.pack_start(Gtk.Label(label="Width:"), False, False, 0)
    width_spin = _make_spin(element.width, DiagonalLineElement.MIN_SIDE,
                            DiagonalLineElement.MAX_SIDE)
    content.pack_start(width_spin, False, False, 0)

    content.pack_start(Gtk.Label(label="Height:"), False, False, 0)
    height_spin = _make_spin(element.height, DiagonalLineElement.MIN_SIDE,
                             DiagonalLineElement.MAX_SIDE)
    content.pack_start(height_spin, False, False, 0)

    # Each row's run meets the far side at the full width, and the
    # box fills solid.
    def max_thickness():
        return max(1, int(width_spin.get_value()))

    content.pack_start(Gtk.Label(label="Thickness:"), False, False, 0)
    thickness_spin = _make_spin(min(element.thickness, max_thickness()),
                                1, max_thickness())
    content.pack_start(thickness_spin, False, False, 0)
    _tip_stacked(content, thickness_spin, 'thickness')

    def on_width_changed(_spin):
        # Narrowing the line must not leave a thicker one selectable
        thickness_spin.get_adjustment().set_upper(max_thickness())

    width_spin.connect("value-changed", on_width_changed)

    # ^GD's colour: the same black or white ^GB offers
    content.pack_start(Gtk.Label(label="Colour:"), False, False, 0)
    colour_combo, colour_codes = _make_combo(FRAME_COLOURS, element.colour)
    content.pack_start(colour_combo, False, False, 0)
    _tip_stacked(content, colour_combo, 'colour')

    content.pack_start(Gtk.Label(label="Direction:"), False, False, 0)
    direction_combo, direction_codes = _make_combo(DIAGONAL_DIRECTIONS,
                                                   element.direction)
    content.pack_start(direction_combo, False, False, 0)

    fr_check = Gtk.CheckButton(label="Reverse print (^FR)")
    fr_check.set_name("reverse_print")
    set_tip(fr_check, 'reverse_print')
    fr_check.set_active(element.reverse_print)
    content.pack_start(fr_check, False, False, 0)

    content.show_all()

    def apply():
        element.width = int(width_spin.get_value())
        element.height = int(height_spin.get_value())
        # An adjustment whose upper bound drops does not pull its
        # value down with it, as Qt's setRange does.
        element.thickness = min(int(thickness_spin.get_value()),
                                max_thickness())
        element.colour = colour_codes[colour_combo.get_active()]
        element.direction = direction_codes[direction_combo.get_active()]
        element.reverse_print = fr_check.get_active()

    return editor.finish(apply)


def form_builder(element):
    """The function that builds this element's rows, or None for one with
    none - an image, whose one setting is which file it shows.

    A text element's rows follow what feeds it - the clock, a serial number,
    a numbered field, or nothing.
    """
    if isinstance(element, TextElement):
        if element.clock_format:
            return time_form
        if element.serial_increment is not None:
            return serial_form
        if element.field_number is not None:
            return numbered_form
        return text_form
    for kind, builder in ((BarcodeElement, barcode_form),
                          (StoredGraphicElement, stored_graphic_form),
                          (FrameElement, frame_form), (CircleElement, circle_form),
                          (GraphicSymbolElement, graphic_symbol_form),
                          (EllipseElement, ellipse_form),
                          (DiagonalLineElement, diagonal_form)):
        if isinstance(element, kind):
            return builder
    return None


def editor_form(window, content, element):
    """This element's rows, built into `content`, or None if it has none."""
    builder = form_builder(element)
    return None if builder is None else builder(window, content, element)
