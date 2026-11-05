#!/usr/bin/env python3
"""
ZPL Viewer - Gtk Application to Display ZPL Output

A simple GTK3 application for viewing rendered ZPL (Zebra Programming Language) output.
"""

import cairo
import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, Gdk, GdkPixbuf, GLib
import os
import time
import base64
import configparser
from pathlib import Path
from zplcore import firmware as zpl_firmware
from zplcore import fonts as zpl_fonts
from zplcore import graphic_store
from zplcore import grid as zpl_grid
from zplcore import local_port as zpl_local
from zplcore import print_settings as zpl_print
from zplcore import printer_io
from zplcore import printer_objects
from zplcore import printer_status
from zplcore import model
from zplcore import parser as zpl_parser
from zplcore import view as zpl_view
from zplcore import workflow
from zplcore import graphic_symbols
from zplcore.model import STORED_GRAPHIC_DEVICES, Document
from zplcore.print_settings import AsWritten, PrintSettings
from zplcore.renderer import ZPLRenderer

from . import editors as gtk_editors
from .busy import BusyBar
from .canvas import DesignCanvas, palette_targets, to_pixbuf
# The row helpers every dialog here builds with, kept beside the element
# editors that use them most
from .editors import _make_combo, _make_row, _make_spin
from .properties import DEFAULT_WIDTH as PANEL_WIDTH, PropertiesPanel
from PIL import Image
import io


DEFAULT_PRINTER_ADDRESS = '192.168.50.21'
DEFAULT_PRINTER_PORT = 9100
DEFAULT_LABEL_INCHES = (4.0, 6.0)


# The align commands, in menu order: the three horizontal, then the three
# vertical. The Qt frontend spells the same six the same way, and the
# conformance suite diffs the two menu bars against each other.
ALIGN_ITEMS = (
    ('left', "Align _Left"),
    ('center', "Centre _Horizontally"),
    ('right', "Align _Right"),
    ('top', "Align _Top"),
    ('middle', "Centre _Vertically"),
    ('bottom', "Align _Bottom"),
)


def _dpi_combo(dpi: int) -> Gtk.ComboBoxText:
    """The resolution choice, offered the same way wherever it is edited.

    Label Settings and the printer rows (Default Printer, the Print window)
    all set the printer's resolution, so they have to offer the same list -
    including the fallback: a resolution the designer does not support can
    still be the one in force, and must be shown rather than silently
    replaced with a supported one.
    """
    combo = Gtk.ComboBoxText()
    for d in zpl_fonts.SUPPORTED_DPI:
        combo.append_text(str(d))
    try:
        combo.set_active(list(zpl_fonts.SUPPORTED_DPI).index(dpi))
    except ValueError:
        combo.append_text(str(dpi))
        combo.set_active(len(zpl_fonts.SUPPORTED_DPI))
    return combo


def _dpi_from(combo: Gtk.ComboBoxText, fallback: int) -> int:
    text = combo.get_active_text()
    return int(text) if text and text.isdigit() else fallback


class _PrinterRows:
    """Address, Port, DPI, Font memory and Test Connection, plus Use Default
    when there is a default to go back to. The GTK twin of
    qtui.dialogs._PrinterRows.

    Default Printer and the Print window both build their printer from these
    rows, so the two can't drift apart. `box` is the widget holding them.
    `withhold(widgets)` adds the busy row, and the widgets it names are
    withheld while a probe is out, so a half-finished one can't be accepted
    as an answer.

    Font memory belongs here rather than beside each text element. Which
    memory a printer keeps its fonts in is a property of the printer being
    deployed to, not of one field on one label.
    """

    LABEL_WIDTH = 90

    def __init__(self, address, port, dpi, font_device=None, default=None):
        self._dpi = dpi
        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)

        self.address_entry = Gtk.Entry()
        self.address_entry.set_name("address")
        self.address_entry.set_text(address)
        # Enter in the address is the dialog's default button.
        self.address_entry.set_activates_default(True)
        _make_row(self.box, "Address:", self.address_entry, self.LABEL_WIDTH)

        self.port_spin = _make_spin(port, 1, 65535)
        self.port_spin.set_name("port")
        _make_row(self.box, "Port:", self.port_spin, self.LABEL_WIDTH)

        self.dpi_combo = _dpi_combo(dpi)
        _make_row(self.box, "DPI:", self.dpi_combo, self.LABEL_WIDTH)

        # Where a font this app assigns is written and uploaded. The same
        # STORED_GRAPHIC_DEVICES the Store Graphic dialog offers, so a memory
        # type reads identically everywhere, and Z: is absent for the reason
        # it is there: ~DY cannot write it and ^ID will not delete it.
        self.font_device_combo, self._font_device_codes = _make_combo(
            STORED_GRAPHIC_DEVICES,
            (font_device or zpl_fonts.DEFAULT_FONT_DEVICE).upper())
        _make_row(self.box, "Font memory:", self.font_device_combo,
                  self.LABEL_WIDTH)

        self._test_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                                 spacing=8)
        self.test_btn = Gtk.Button(label="Test Connection")
        self.test_btn.connect("clicked", self._on_test)
        self._test_row.pack_start(self.test_btn, False, False, 0)
        if default is not None:
            # What the persisted default printer is, for comparing against or
            # going back to.
            default_btn = Gtk.Button(label="Use Default")
            default_btn.set_name("use_default")
            default_btn.connect("clicked", lambda _b: self._fill(*default))
            self._test_row.pack_start(default_btn, False, False, 0)
        self.box.pack_start(self._test_row, False, False, 0)

        self.result = Gtk.Label()
        self.result.set_halign(Gtk.Align.START)
        self.result.set_line_wrap(True)
        self.box.pack_start(self.result, False, False, 0)
        self.busy = None

    def withhold(self, widgets) -> None:
        self.busy = BusyBar((self.test_btn, *widgets),
                            lambda text: self._set_result("gray", text))
        self._test_row.pack_end(self.busy, False, False, 0)

    def abandon(self) -> None:
        if self.busy is not None:
            self.busy.abandon()

    def value(self):
        """(address, port, dpi, font_device), or None when no address is
        given."""
        address = self.address_entry.get_text().strip()
        if not address:
            return None
        return (address, int(self.port_spin.get_value()),
                _dpi_from(self.dpi_combo, self._dpi),
                self._font_device_codes[self.font_device_combo.get_active()])

    def _set_result(self, colour, text):
        self.result.set_markup(
            f"<span foreground='{colour}'>"
            f"{GLib.markup_escape_text(text)}</span>")

    def _set_dpi(self, value):
        try:
            self.dpi_combo.set_active(list(zpl_fonts.SUPPORTED_DPI).index(value))
        except ValueError:
            model = self.dpi_combo.get_model()
            for i, row in enumerate(model):
                if row[0] == str(value):
                    self.dpi_combo.set_active(i)
                    return
            self.dpi_combo.append_text(str(value))
            self.dpi_combo.set_active(len(model) - 1)

    def _fill(self, address, port, dpi, font_device):
        self.address_entry.set_text(address)
        self.port_spin.set_value(port)
        self._set_dpi(dpi)
        if font_device in self._font_device_codes:
            self.font_device_combo.set_active(
                self._font_device_codes.index(font_device))

    def _on_test(self, _btn):
        addr = self.address_entry.get_text().strip()
        prt = int(self.port_spin.get_value())
        if not addr:
            self._set_result("red", "Address is required")
            return
        self._set_result("gray", f"Connecting to {addr}:{prt}…")
        connected = f"✓ Connected to {addr}:{prt}"
        busy = self.busy

        def probe(cancel):
            # Connect and send nothing: reachability first, on its own, so
            # an unreachable printer reads as that rather than as "no dpi".
            printer_io.send(addr, prt, b'', 5, cancel=cancel)
            busy.report(f"{connected} — asking its resolution…")
            return zpl_fonts.query_printer_dpi(addr, prt, cancel=cancel)

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
                self.dpi_combo.set_active(
                    list(zpl_fonts.SUPPORTED_DPI).index(reported))
                self._set_result("green", f"{connected} — {reported} dpi")
            else:
                self._set_result("orange",
                                 f"{connected} — reports {reported} dpi, "
                                 f"which the designer does not support.")

        busy.run(probe, done)


def _printer_picker_dialog(parent, title, address, port, dpi,
                           font_device=None):
    """Default Printer's dialog: (address, port, dpi, font_device), or None
    if cancelled."""
    dialog = Gtk.Dialog(title=title, parent=parent, flags=0)
    dialog.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                       Gtk.STOCK_OK, Gtk.ResponseType.OK)
    content = dialog.get_content_area()
    content.set_spacing(4)
    for side in ("start", "end", "top", "bottom"):
        getattr(content, "set_margin_" + side)(8)
    rows = _PrinterRows(address, port, dpi, font_device)
    content.pack_start(rows.box, False, False, 0)
    rows.withhold((dialog.get_widget_for_response(Gtk.ResponseType.OK),))
    content.show_all()

    response = dialog.run()
    rows.abandon()
    result = rows.value() if response == Gtk.ResponseType.OK else None
    dialog.destroy()
    if response == Gtk.ResponseType.OK and result is None:
        parent.show_error_dialog("Printer address cannot be empty.")
    return result


# The Print window's own button, beside OK and Cancel.
PRINT_RESPONSE = 1


class _NumberRow:
    """A number box that can hold a carried value it cannot show.

    An AsWritten is shown beside the box, and is what the row gives back
    until the box is changed.
    """

    def __init__(self, spin, current, shown_instead):
        self.spin = spin
        self.note = Gtk.Label()
        self._current = current
        self._touched = False
        if isinstance(current, AsWritten):
            spin.set_value(shown_instead)
            self.note.set_text(current.label())
        else:
            if current is not None:
                spin.set_value(current)
            self.note.set_no_show_all(True)
        spin.connect("value-changed", self._on_changed)

    def _on_changed(self, _spin):
        self._touched = True
        self.note.hide()

    def value(self):
        if isinstance(self._current, AsWritten) and not self._touched:
            return self._current
        return self.spin.get_value()


def _build_print_dialog(parent, document, printer, default):
    """The Print window, built but not run: (dialog, finish).

    `finish(response)` reads the rows, destroys the window and returns what
    _print_dialog does. Apart so the rows can be filled in and read without
    a nested main loop.
    """
    settings = PrintSettings.read(document)
    dialog = Gtk.Dialog(title="Print", parent=parent, flags=0)
    dialog.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                       Gtk.STOCK_OK, Gtk.ResponseType.OK)
    print_btn = dialog.add_button("_Print", PRINT_RESPONSE)
    print_btn.set_name("print")
    # Print is what the window is opened for, so it is the one Enter presses.
    dialog.set_default_response(PRINT_RESPONSE)

    content = dialog.get_content_area()
    content.set_spacing(8)
    for side in ("start", "end", "top", "bottom"):
        getattr(content, "set_margin_" + side)(8)
    columns = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
    content.pack_start(columns, True, True, 0)
    left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    columns.pack_start(left, True, True, 0)
    columns.pack_start(right, True, True, 0)

    def group(column, title):
        frame = Gtk.Frame(label=title)
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        for side in ("start", "end", "top", "bottom"):
            getattr(inner, "set_margin_" + side)(6)
        frame.add(inner)
        column.pack_start(frame, False, False, 0)
        return inner

    def check(box, name, text, value):
        button = Gtk.CheckButton(label=text)
        button.set_name(name)
        button.set_active(value)
        box.pack_start(button, False, False, 0)
        return button

    def setup_combo(name, options, current):
        combo, values = _make_combo(zpl_print.choices(options, current),
                                    current)
        combo.set_name(name)
        return combo, values

    rows = _PrinterRows(*printer, default=default)
    group(left, "Printer (this session only)").pack_start(
        rows.box, False, False, 0)

    copies = group(left, "Copies (^PQ)")
    quantity = _make_spin(settings.quantity, *zpl_print.QUANTITY_RANGE)
    quantity.set_name("quantity")
    _make_row(copies, "Copies:", quantity, 100)
    pause = _make_spin(settings.pause_count, *zpl_print.PAUSE_RANGE)
    pause.set_name("pause_count")
    # 0 is ZPL's "no pause". The Qt window shows it as Never; a numeric
    # GtkSpinButton refuses letters, and PyGObject cannot answer its "input"
    # signal to read one back, so here the row says so beside the box.
    pause_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
    pause_row.pack_start(pause, True, True, 0)
    pause_row.pack_start(Gtk.Label(label="labels (0 = never)"), False, False, 0)
    _make_row(copies, "Pause every:", pause_row, 100)
    replicates = _make_spin(settings.replicates, *zpl_print.REPLICATE_RANGE)
    replicates.set_name("replicates")
    replicates.set_tooltip_text(
        "Copies of each serial number before it counts on")
    _make_row(copies, "Replicates:", replicates, 100)
    override = check(copies, "override_pause", "Don't pause between groups",
                     settings.override_pause)
    cut = check(copies, "cut_on_error", "Cut after a voided RFID label",
                settings.cut_on_error)

    # How the finished label is laid down, and checked, rather than where a
    # field sits on it.
    flags = group(right, "Label")
    invert = check(flags, "invert", "Print upside down (^PO)", settings.invert)
    mirror = check(flags, "mirror", "Mirror left to right (^PM)",
                   settings.mirror)
    reverse = check(flags, "reverse", "Reverse fields, white on black (^LR)",
                    settings.reverse)
    validate = check(flags, "code_validation",
                     "Check barcode data as printed (^CV)",
                     settings.code_validation)

    setup = group(right, "Printer setup sent with the label")
    # A number box has no "unset", so the darkness has a tick for it.
    darkness_set = Gtk.CheckButton()
    darkness_set.set_name("darkness_set")
    darkness_set.set_tooltip_text("Clear for the printer's setting")
    darkness_set.set_active(settings.darkness is not None)
    darkness_spin = Gtk.SpinButton()
    darkness_spin.set_name("darkness")
    low, high = zpl_print.DARKNESS_RANGE
    darkness_spin.set_adjustment(Gtk.Adjustment(
        value=0, lower=low, upper=high, step_increment=1, page_increment=10))
    darkness_spin.set_digits(zpl_print.DARKNESS_DECIMALS)
    darkness_spin.set_numeric(True)
    darkness = _NumberRow(darkness_spin, settings.darkness, 0.0)
    darkness_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
    darkness_row.pack_start(darkness_set, False, False, 0)
    darkness_row.pack_start(darkness_spin, True, True, 0)
    darkness_row.pack_start(darkness.note, False, False, 0)
    _make_row(setup, "Darkness (^MD):", darkness_row)

    print_speed, print_speeds = setup_combo(
        "print_speed", zpl_print.PRINT_SPEED_CHOICES, settings.print_speed)
    _make_row(setup, "Print speed (^PR):", print_speed)
    slew_speed, slew_speeds = setup_combo(
        "slew_speed", zpl_print.FEED_SPEED_CHOICES, settings.slew_speed)
    _make_row(setup, "Slew speed:", slew_speed)
    backfeed_speed, backfeed_speeds = setup_combo(
        "backfeed_speed", zpl_print.FEED_SPEED_CHOICES,
        settings.backfeed_speed)
    _make_row(setup, "Backfeed speed:", backfeed_speed)

    print_mode, print_modes = setup_combo(
        "print_mode", zpl_print.PRINT_MODES, settings.print_mode)
    _make_row(setup, "Print mode (^MM):", print_mode)
    prepeel = Gtk.CheckButton(label="Prepeel")
    prepeel.set_name("prepeel")
    prepeel.set_active(settings.prepeel)
    _make_row(setup, "", prepeel)

    tracking, trackings = setup_combo(
        "media_tracking", zpl_print.MEDIA_TRACKING, settings.media_tracking)
    _make_row(setup, "Media tracking (^MN):", tracking)
    offset_spin = _make_spin(0, *zpl_print.MARK_OFFSET_RANGE)
    offset_spin.set_name("mark_offset")
    offset = _NumberRow(offset_spin, settings.mark_offset, 0)
    offset_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
    offset_row.pack_start(offset_spin, True, True, 0)
    offset_row.pack_start(Gtk.Label(label="dots"), False, False, 0)
    offset_row.pack_start(offset.note, False, False, 0)
    _make_row(setup, "Black mark offset:", offset_row)

    media_type, media_types = setup_combo(
        "media_type", zpl_print.MEDIA_TYPES, settings.media_type)
    _make_row(setup, "Media type (^MT):", media_type)

    note = Gtk.Label(label="Printer's setting sends nothing. A value set "
                           "here stays in effect on the printer after this "
                           "label.")
    note.set_halign(Gtk.Align.START)
    note.set_line_wrap(True)
    note.set_max_width_chars(50)
    setup.pack_start(note, False, False, 0)

    def chosen(combo, values):
        return values[combo.get_active()]

    def follow(*_a):
        # A row that only means something with another set is greyed out
        # without it: ^PR is a chain, prepeel rides on a print mode, and the
        # offset is only for mark sensing.
        darkness_spin.set_sensitive(darkness_set.get_active())
        slew_speed.set_sensitive(chosen(print_speed, print_speeds) is not None)
        backfeed_speed.set_sensitive(
            slew_speed.get_sensitive()
            and chosen(slew_speed, slew_speeds) is not None)
        prepeel.set_sensitive(chosen(print_mode, print_modes) is not None)
        offset_spin.set_sensitive(
            chosen(tracking, trackings) == zpl_print.MARK_SENSING)

    darkness_set.connect("toggled", follow)
    for combo in (print_speed, slew_speed, print_mode, tracking):
        combo.connect("changed", follow)
    follow()

    rows.withhold((dialog.get_widget_for_response(Gtk.ResponseType.OK),
                   print_btn))
    _make_dialog_scrollable(dialog, parent)
    content.show_all()

    def finish(response):
        rows.abandon()
        if response not in (Gtk.ResponseType.OK, PRINT_RESPONSE):
            dialog.destroy()
            return None
        chosen_printer = rows.value()
        result = settings.copy()
        result.quantity = quantity.get_value_as_int()
        result.pause_count = pause.get_value_as_int()
        result.replicates = replicates.get_value_as_int()
        result.override_pause = override.get_active()
        result.cut_on_error = cut.get_active()
        result.invert = invert.get_active()
        result.mirror = mirror.get_active()
        result.reverse = reverse.get_active()
        result.code_validation = validate.get_active()
        result.darkness = darkness.value() if darkness_set.get_active() else None
        if isinstance(result.darkness, float):
            result.darkness = round(result.darkness,
                                    zpl_print.DARKNESS_DECIMALS)
        result.print_speed = chosen(print_speed, print_speeds)
        result.slew_speed = chosen(slew_speed, slew_speeds)
        result.backfeed_speed = chosen(backfeed_speed, backfeed_speeds)
        result.print_mode = chosen(print_mode, print_modes)
        result.prepeel = prepeel.get_active()
        result.media_tracking = chosen(tracking, trackings)
        offset_value = offset.value()
        result.mark_offset = (int(offset_value)
                              if isinstance(offset_value, float)
                              else offset_value)
        result.media_type = chosen(media_type, media_types)
        dialog.destroy()
        if chosen_printer is None:
            parent.show_error_dialog("Printer address cannot be empty.")
            return None
        action = 'print' if response == PRINT_RESPONSE else 'ok'
        return action, chosen_printer, result

    return dialog, finish


def _print_dialog(parent, document, printer, default):
    """The Print window. Returns (action, printer, settings), or None if
    cancelled.

    `action` is 'print' or 'ok'. OK keeps the settings without printing.
    `printer` is the (address, port, dpi, font_device) for this session, and
    the window opens on the one in effect. `default` is the persisted printer
    Use Default goes back to. `settings` is a print_settings.PrintSettings:
    everything here that is saved with the label, for the caller to apply as
    one undo entry. The rows come from print_settings, the same tables the Qt
    window draws from.
    """
    dialog, finish = _build_print_dialog(parent, document, printer, default)
    return finish(dialog.run())


def _build_grid_dialog(parent, grid, dpi):
    """Grid Settings, built but not run: (dialog, finish).

    `finish(response)` reads the rows, destroys the window and returns a copy
    of `grid` with the spacing chosen, or None if cancelled. Only the spacing:
    the switches that use it are the View menu's. `dpi` is the label's, for
    the hint and for converting between a physical unit and dots.
    """
    dialog = Gtk.Dialog(title="Grid Settings", parent=parent, flags=0)
    dialog.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                       Gtk.STOCK_OK, Gtk.ResponseType.OK)
    dialog.set_default_response(Gtk.ResponseType.OK)
    content = dialog.get_content_area()
    content.set_spacing(8)
    for side in ("start", "end", "top", "bottom"):
        getattr(content, "set_margin_" + side)(8)

    table = Gtk.Grid(column_spacing=8, row_spacing=6)
    content.pack_start(table, False, False, 0)

    def row(number, text, widget):
        label = Gtk.Label(label=text)
        label.set_halign(Gtk.Align.START)
        table.attach(label, 0, number, 1, 1)
        widget.set_hexpand(True)
        table.attach(widget, 1, number, 1, 1)

    unit_combo = Gtk.ComboBoxText()
    unit_combo.set_name("grid_unit")
    for unit in zpl_grid.UNITS:
        unit_combo.append(unit, zpl_grid.UNIT_NAMES[unit])
    unit_combo.set_active_id(grid.unit)
    row(0, "Unit:", unit_combo)

    spacing_spin = Gtk.SpinButton()
    spacing_spin.set_name("grid_spacing")
    spacing_spin.set_numeric(True)
    spacing_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
    spacing_row.pack_start(spacing_spin, True, True, 0)
    spacing_suffix = Gtk.Label()
    spacing_row.pack_start(spacing_suffix, False, False, 0)
    row(1, "Spacing:", spacing_row)

    major_spin = Gtk.SpinButton.new_with_range(*zpl_grid.MAJOR_LIMITS, 1)
    major_spin.set_name("grid_major")
    major_spin.set_value(grid.major_every)
    major_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
    major_row.pack_start(major_spin, True, True, 0)
    major_row.pack_start(Gtk.Label(label="spaces"), False, False, 0)
    row(2, "Major line every:", major_row)

    hint = Gtk.Label()
    hint.set_name("grid_hint")
    hint.set_halign(Gtk.Align.START)
    content.pack_start(hint, False, False, 0)

    def update_hint(*_):
        hint.set_text(zpl_grid.spacing_hint(unit_combo.get_active_id(),
                                            spacing_spin.get_value(), dpi))

    def set_unit(unit, spacing):
        low, high = zpl_grid.SPACING_LIMITS[unit]
        decimals = zpl_grid.SPACING_DECIMALS[unit]
        step = 10 ** -decimals if decimals else 1
        spacing_spin.set_digits(decimals)
        spacing_spin.set_adjustment(Gtk.Adjustment(
            value=spacing, lower=low, upper=high, step_increment=step,
            page_increment=step * 10))
        spacing_spin.set_value(spacing)
        spacing_suffix.set_text(zpl_grid.UNIT_SUFFIX[unit])
        update_hint()

    shown_unit = [grid.unit]

    def on_unit_changed(_combo):
        # The grid stays the size it was: 0.1 in becomes 2.54 mm, not 0.1 mm.
        unit = unit_combo.get_active_id()
        set_unit(unit, zpl_grid.convert_spacing(spacing_spin.get_value(),
                                                shown_unit[0], unit, dpi))
        shown_unit[0] = unit

    set_unit(grid.unit, grid.spacing)
    spacing_spin.connect("value-changed", update_hint)
    unit_combo.connect("changed", on_unit_changed)
    dialog.show_all()

    def finish(response):
        # Whatever is half typed in the box counts, as it would on Enter.
        spacing_spin.update()
        major_spin.update()
        chosen = None
        if response == Gtk.ResponseType.OK:
            chosen = grid.copy()
            chosen.unit = unit_combo.get_active_id()
            chosen.spacing = zpl_grid.clamp_spacing(chosen.unit,
                                                    spacing_spin.get_value())
            chosen.major_every = major_spin.get_value_as_int()
        dialog.destroy()
        return chosen

    return dialog, finish


def _grid_dialog(parent, grid, dpi):
    """Grid Settings: a copy of `grid` with the spacing chosen, or None."""
    dialog, finish = _build_grid_dialog(parent, grid, dpi)
    return finish(dialog.run())


def _config_path() -> Path:
    """Path to the persisted settings file."""
    return Path(GLib.get_user_config_dir()) / 'linuxzpl' / 'settings.ini'


def _fallback_config_path() -> Path:
    """Where settings go if the user config directory can't be written to.

    Some Linux environments (sandboxes, restricted containers) refuse writes
    under the user config directory outright. The project's own directory is
    always writable, since the interpreter had to read from it to get here.
    """
    return Path(__file__).resolve().parent.parent / 'settings.ini'


def _make_dialog_scrollable(dialog, parent):
    """Keep a dialog inside the screen: its body scrolls once it outgrows it.

    The content area's widgets move into a scrolled window that grows with
    them up to the monitor's work area, less room for the title bar and the
    OK/Cancel row, which sit outside the scroll so they are never pushed off
    the bottom of a small display.
    """
    content = dialog.get_content_area()
    # The action area sits in a box that is one of the content box's own
    # children: leave it where it is, or OK and Cancel scroll away with the
    # form.
    action = dialog.get_action_area()
    while action.get_parent() is not content:
        action = action.get_parent()
    children = [c for c in content.get_children() if c is not action]
    body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                   spacing=content.get_spacing())
    for child in children:
        content.remove(child)
        body.pack_start(child, False, False, 0)
    for side in ("start", "end", "top", "bottom"):
        getattr(body, "set_margin_" + side)(
            getattr(content, "get_margin_" + side)())
        getattr(content, "set_margin_" + side)(0)

    display = Gdk.Display.get_default()
    window = parent.get_window() if parent is not None else None
    monitor = (display.get_monitor_at_window(window) if window
               else display.get_primary_monitor() or display.get_monitor(0))
    area = monitor.get_workarea()

    scroller = Gtk.ScrolledWindow()
    scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
    scroller.set_propagate_natural_width(True)
    scroller.set_propagate_natural_height(True)
    scroller.set_max_content_width(area.width - 40)
    scroller.set_max_content_height(area.height - 160)
    scroller.add(body)
    content.pack_start(scroller, True, True, 0)
    content.reorder_child(scroller, 0)


class ZPLViewerWindow(Gtk.Window):
    """Main GTK window for the ZPL Viewer application."""
    
    def __init__(self):
        super().__init__(title=workflow.window_title(None))
        self.set_border_width(10)
        self.connect("delete-event", self.main_window_closed)
        
        self.renderer = ZPLRenderer()
        self.current_zpl_content = ""
        self.current_filepath = None
        self.unsaved_changes = False
        
        # Printer connection settings (persisted in the config file)
        self.printer_address = DEFAULT_PRINTER_ADDRESS
        self.printer_port = DEFAULT_PRINTER_PORT
        self.printer_dpi = zpl_fonts.DEFAULT_DPI
        # Which printer memory this app's own fonts are written to and
        # uploaded to - see Document.font_device, which every document is
        # given a copy of.
        self.printer_font_device = zpl_fonts.DEFAULT_FONT_DEVICE
        # The two non-modal printer windows - see on_printer_console_clicked
        # and on_printer_status_clicked. Both stay open while the user keeps
        # working, so both are singletons rather than stacking a copy per click.
        self.printer_console_window = None
        self.printer_status_window = None
        # The size last chosen in Label Settings, also persisted. Held in
        # inches because the resolution it converts with is itself a setting
        # that can change between sessions.
        self.label_inches = DEFAULT_LABEL_INCHES
        self.saved_geometry = None
        # Where the Properties panel was left: floating or docked, shown, its
        # docked width and its floating window's place - see _restore_panel.
        self.saved_panel = {'floating': False, 'shown': True,
                            'width': PANEL_WIDTH, 'geometry': None}
        self._load_settings()
        # The grid over the canvas and the switches that use it. Kept in its
        # own section, written the moment it changes, like Open Recent.
        self.grid = zpl_grid.load_grid(self._settings_files())
        # The persisted printer, snapshotted so the Print window, whose
        # printer is for this session only, can offer "Use Default" without
        # re-reading the settings file.
        self._default_printer = (self.printer_address, self.printer_port,
                                 self.printer_dpi, self.printer_font_device)
        self._place_on_screen()
        # Label size in dots, which depends on both settings above
        self.label_width, self.label_height = self.inches_to_dots(*self.label_inches)

        # Create main layout
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add(main_box)

        # Menu lives in the header bar rather than a separate row below it
        header = Gtk.HeaderBar()
        header.set_show_close_button(True)
        self.set_titlebar(header)
        # A custom titlebar draws its own title, so the header's is what the
        # user reads and the window's is what the task switcher reads. Both are
        # set together, from here on.
        self.header_bar = header
        self._update_title()

        menu_bar = Gtk.MenuBar()
        header.pack_start(menu_bar)

        # Keyboard shortcuts, shown in the menu and live window-wide
        accel_group = Gtk.AccelGroup()
        self.add_accel_group(accel_group)
        # Shared with the Properties panel's window while it floats
        self.accel_group = accel_group
        # What each item is bound to, kept as it is bound. PyGObject offers no
        # way to ask a menu item back: Gtk.AccelLabel.get_accel reports only
        # what set_accel put there, and the accel group's contents are not
        # enumerable. Recording at the one place bindings are made is simpler
        # than scraping them out, and it is what lets the conformance suite
        # compare this menu bar against the Qt one.
        self.accelerators = {}

        def add_accel(item, accel, visible=True):
            key, mods = Gtk.accelerator_parse(accel)
            item.add_accelerator("activate", accel_group, key, mods,
                                 Gtk.AccelFlags.VISIBLE if visible else 0)
            if visible:
                self.accelerators[item] = accel
        
        # File menu. Four groups - start a document, persist it, print it,
        # leave - matching the Qt frontend item for item. An ellipsis marks
        # every command that asks something before it acts.
        file_menu = Gtk.Menu()
        file_menu_item = Gtk.MenuItem.new_with_mnemonic("_File")
        file_menu_item.set_submenu(file_menu)
        menu_bar.append(file_menu_item)

        for label, action, accel in (
                ("_New", self.on_new_clicked, "<Control>n"),
                ("_Open\u2026", self.on_load_file_clicked, "<Control>o"),
                (None, None, None),
                ("_Save", self.on_save_clicked, "<Control>s"),
                ("Save _as\u2026", self.on_save_as_clicked, "<Control><Shift>s"),
                (None, None, None),
                ("_Print\u2026", self.on_print_dialog_clicked, "<Control>p"),
                ("_Quick Print", self.on_print_clicked, "<Control><Shift>p")):
            if label is None:
                file_menu.append(Gtk.SeparatorMenuItem())
                continue
            item = Gtk.MenuItem.new_with_mnemonic(label)
            item.connect("activate", action)
            add_accel(item, accel)
            file_menu.append(item)
            # Kept so the status bar's busy row can withhold them while a
            # print is out. Print... opens the Print window, whose own Print
            # button sends; Quick Print sends at once with the settings
            # already in force.
            if action == self.on_print_dialog_clicked:
                self.print_item = item
            if action == self.on_print_clicked:
                self.quick_print_item = item
            if action == self.on_load_file_clicked:
                # Filled in by _rebuild_recent_menu, from the settings file.
                self.recent_menu = Gtk.Menu()
                self.recent_item = Gtk.MenuItem.new_with_mnemonic("Open _Recent")
                self.recent_item.set_submenu(self.recent_menu)
                file_menu.append(self.recent_item)
        # Read again each time File opens, so a file another window has opened
        # since is offered here too.
        file_menu.connect("show", lambda _menu: self._refresh_recent())

        upload_menu = Gtk.Menu()
        upload_item = Gtk.MenuItem.new_with_mnemonic("_Upload Template to Printer")
        upload_item.set_submenu(upload_menu)
        file_menu.append(upload_item)
        self.upload_template_items = []
        for device, memory in workflow.TEMPLATE_DEVICES:
            item = Gtk.MenuItem.new_with_label(f"To {memory} ({device}:)\u2026")
            item.connect("activate", self.on_upload_template_clicked, device, memory)
            upload_menu.append(item)
            self.upload_template_items.append(item)
        upload_menu.show_all()

        file_menu.append(Gtk.SeparatorMenuItem())
        quit_item = Gtk.MenuItem.new_with_mnemonic("_Quit")
        quit_item.connect("activate", self.close_app)
        add_accel(quit_item, "<Control>q")
        file_menu.append(quit_item)

        file_menu.show_all()
        self._refresh_recent()

        # Edit menu
        edit_menu = Gtk.Menu()
        edit_menu_item = Gtk.MenuItem.new_with_mnemonic("_Edit")
        edit_menu_item.set_submenu(edit_menu)
        menu_bar.append(edit_menu_item)

        self.undo_item = Gtk.MenuItem.new_with_mnemonic("_Undo")
        self.undo_item.connect("activate", self.on_undo)
        add_accel(self.undo_item, "<Control>z")
        edit_menu.append(self.undo_item)

        self.redo_item = Gtk.MenuItem.new_with_mnemonic("_Redo")
        self.redo_item.connect("activate", self.on_redo)
        add_accel(self.redo_item, "<Control><Shift>z")
        # a second binding, unshown so the menu keeps one accelerator per item
        add_accel(self.redo_item, "<Control>y", visible=False)
        edit_menu.append(self.redo_item)

        edit_menu.append(Gtk.SeparatorMenuItem())

        # The clipboard holds ZPL (Document.copy_zpl), so what one window
        # copies another - this frontend's or the Qt one's - can paste. Like
        # Ctrl+A, these keys would be matched before any focused widget saw
        # them; while a Properties panel box has the focus, the box is offered
        # them first instead (_on_window_key_press).
        self.cut_item = Gtk.MenuItem.new_with_mnemonic("Cu_t")
        self.cut_item.connect("activate", self.on_cut_clicked)
        add_accel(self.cut_item, "<Control>x")
        edit_menu.append(self.cut_item)

        self.copy_item = Gtk.MenuItem.new_with_mnemonic("_Copy")
        self.copy_item.connect("activate", self.on_copy_clicked)
        add_accel(self.copy_item, "<Control>c")
        edit_menu.append(self.copy_item)

        self.paste_item = Gtk.MenuItem.new_with_mnemonic("_Paste")
        self.paste_item.connect("activate", self.on_paste_clicked)
        add_accel(self.paste_item, "<Control>v")
        edit_menu.append(self.paste_item)

        # No accelerator and no mnemonic: reached by a click, from this menu
        # or the canvas's right-click menu.
        self.duplicate_item = Gtk.MenuItem(label="Duplicate")
        self.duplicate_item.connect("activate", self.on_duplicate_clicked)
        edit_menu.append(self.duplicate_item)

        self.delete_item = Gtk.MenuItem.new_with_mnemonic("_Delete")
        self.delete_item.connect("activate", self.on_delete_clicked)
        add_accel(self.delete_item, "Delete")
        edit_menu.append(self.delete_item)

        edit_menu.append(Gtk.SeparatorMenuItem())

        # Ctrl+A would be matched before any focused widget saw it. The
        # Properties panel's text and number boxes keep their select-all-text
        # because the window offers them the key first (_on_window_key_press).
        self.select_all_item = Gtk.MenuItem.new_with_mnemonic("_Select All")
        self.select_all_item.connect("activate", self.on_select_all_clicked)
        add_accel(self.select_all_item, "<Control>a")
        edit_menu.append(self.select_all_item)

        self.deselect_all_item = Gtk.MenuItem.new_with_mnemonic("Dese_lect All")
        self.deselect_all_item.connect("activate", self.on_deselect_all_clicked)
        add_accel(self.deselect_all_item, "<Control><Shift>a")
        edit_menu.append(self.deselect_all_item)

        self.invert_selection_item = Gtk.MenuItem.new_with_mnemonic("_Invert Selection")
        self.invert_selection_item.connect("activate", self.on_invert_selection_clicked)
        edit_menu.append(self.invert_selection_item)

        edit_menu.append(Gtk.SeparatorMenuItem())

        self.group_item = Gtk.MenuItem.new_with_mnemonic("_Group")
        self.group_item.connect("activate", self.on_group_clicked)
        add_accel(self.group_item, "<Control>g")
        edit_menu.append(self.group_item)

        self.ungroup_item = Gtk.MenuItem.new_with_mnemonic("_Ungroup")
        self.ungroup_item.connect("activate", self.on_ungroup_clicked)
        add_accel(self.ungroup_item, "<Control><Shift>g")
        edit_menu.append(self.ungroup_item)

        # No accelerator: one more window-wide binding for a command reached
        # after a Ctrl-click, which the context menu is already under.
        self.remove_from_group_item = Gtk.MenuItem.new_with_mnemonic("Remove _from Group")
        self.remove_from_group_item.connect("activate", self.on_remove_from_group_clicked)
        edit_menu.append(self.remove_from_group_item)

        edit_menu.append(Gtk.SeparatorMenuItem())

        # Same actions as the canvas right-click menu, on the bracket
        # shortcuts drawing programs use. Page Up and Home would collide with
        # scrolling the canvas, since accelerators are matched before the
        # focused widget sees the key.
        #
        # Shift+] arrives as braceright on a US layout and would not match an
        # accelerator declared as bracketright, so the shifted keyval goes on
        # as an unshown alias.
        self.zorder_items = []
        for label, action, accel, alias in (
                ("Bring to Front", lambda _: self.design_canvas.bring_to_front(),
                 "<Control><Shift>bracketright", "<Control><Shift>braceright"),
                ("Bring Forward", lambda _: self.design_canvas.bring_forward(),
                 "<Control>bracketright", None),
                ("Send Backward", lambda _: self.design_canvas.send_backward(),
                 "<Control>bracketleft", None),
                ("Send to Back", lambda _: self.design_canvas.send_to_back(),
                 "<Control><Shift>bracketleft", "<Control><Shift>braceleft")):
            item = Gtk.MenuItem(label=label)
            item.connect("activate", action)
            add_accel(item, accel)
            if alias:
                add_accel(item, alias, visible=False)
            edit_menu.append(item)
            self.zorder_items.append(item)

        edit_menu.append(Gtk.SeparatorMenuItem())

        # Every align item in the window, across both copies of the menu, so
        # the enable rules can be applied to all of them at once, and the menus
        # holding them, whose "show" is connected once the canvas exists.
        self.align_items = []
        self._align_menus = []
        align_item = Gtk.MenuItem.new_with_mnemonic("_Align")
        align_item.set_submenu(self._build_align_menu())
        edit_menu.append(align_item)

        edit_menu.append(Gtk.SeparatorMenuItem())

        # Alt+Enter, the conventional Properties key. It opens the primary's
        # editor, as a double click on it does.
        self.properties_item = Gtk.MenuItem.new_with_mnemonic("Pr_operties")
        self.properties_item.connect("activate", self.on_properties_clicked)
        add_accel(self.properties_item, "<Alt>Return")
        edit_menu.append(self.properties_item)

        edit_menu.show_all()
        self._edit_menu = edit_menu

        # View menu
        view_menu = Gtk.Menu()
        view_menu_item = Gtk.MenuItem.new_with_mnemonic("_View")
        view_menu_item.set_submenu(view_menu)
        menu_bar.append(view_menu_item)

        # Ctrl++ needs Shift on most layouts, so the unshifted key is bound as
        # an unshown alias - the same trick the z-order items use.
        for label, action, accel, alias in (
                ("Zoom _In", self.on_zoom_in, "<Control>plus", "<Control>equal"),
                ("Zoom _Out", self.on_zoom_out, "<Control>minus", None),
                (None, None, None, None),
                ("Fit _Label", self.on_fit_label, "<Control>0", None),
                ("Fit _Width", self.on_fit_width, "<Control>9", None),
                ("_Actual Size", self.on_actual_size, "<Control>1", None)):
            if label is None:
                view_menu.append(Gtk.SeparatorMenuItem())
                continue
            item = Gtk.MenuItem.new_with_mnemonic(label)
            item.connect("activate", action)
            add_accel(item, accel)
            if alias:
                add_accel(item, alias, visible=False)
            view_menu.append(item)

        view_menu.append(Gtk.SeparatorMenuItem())
        self.show_grid_item = Gtk.CheckMenuItem.new_with_mnemonic("Show _Grid")
        self.show_grid_item.set_active(self.grid.shown)
        self.show_grid_item.connect("toggled", self.on_show_grid_toggled)
        add_accel(self.show_grid_item, "<Control>apostrophe")
        view_menu.append(self.show_grid_item)
        grid_settings_item = Gtk.MenuItem.new_with_mnemonic("Grid Se_ttings\u2026")
        grid_settings_item.connect("activate", self.on_grid_settings_clicked)
        view_menu.append(grid_settings_item)

        view_menu.append(Gtk.SeparatorMenuItem())
        self.snap_grid_item = Gtk.CheckMenuItem.new_with_mnemonic("S_nap to Grid")
        self.snap_grid_item.set_active(self.grid.snap)
        self.snap_grid_item.connect("toggled", self.on_snap_to_grid_toggled)
        # Shift+' arrives as quotedbl on a US layout, as Shift+] does as
        # braceright, so the shifted keyval goes on as an unshown alias.
        add_accel(self.snap_grid_item, "<Control><Shift>apostrophe")
        add_accel(self.snap_grid_item, "<Control><Shift>quotedbl", visible=False)
        view_menu.append(self.snap_grid_item)
        # No shortcut: every window-wide binding is a key taken from the canvas.
        self.snap_elements_item = Gtk.CheckMenuItem.new_with_mnemonic("Snap to _Elements")
        self.snap_elements_item.set_active(self.grid.snap_elements)
        self.snap_elements_item.connect("toggled", self.on_snap_to_elements_toggled)
        view_menu.append(self.snap_elements_item)

        view_menu.append(Gtk.SeparatorMenuItem())
        # Shows and hides the Properties panel, docked or floating - and is
        # kept ticked to match however it was shown or closed
        self.panel_item = Gtk.CheckMenuItem.new_with_mnemonic("Properties _Panel")
        self._panel_item_handler = self.panel_item.connect(
            "toggled", lambda item: self.properties.set_shown(item.get_active()))
        view_menu.append(self.panel_item)

        view_menu.show_all()

        # Printer menu
        printer_menu = Gtk.Menu()
        printer_menu_item = Gtk.MenuItem.new_with_mnemonic("_Printer")
        printer_menu_item.set_submenu(printer_menu)
        menu_bar.append(printer_menu_item)

        printer_graphics_item = Gtk.MenuItem(label="Graphics…")
        printer_graphics_item.connect("activate", self.on_printer_graphics_clicked)
        printer_menu.append(printer_graphics_item)

        # Printer fonts menu item
        printer_fonts_item = Gtk.MenuItem(label="Fonts…")
        printer_fonts_item.connect("activate", self.on_printer_fonts_clicked)
        printer_menu.append(printer_fonts_item)

        printer_objects_item = Gtk.MenuItem(label="Objects…")
        printer_objects_item.connect("activate", self.on_printer_objects_clicked)
        printer_menu.append(printer_objects_item)

        printer_menu.append(Gtk.SeparatorMenuItem())

        printer_console_item = Gtk.MenuItem(label="Console…")
        printer_console_item.connect("activate", self.on_printer_console_clicked)
        printer_menu.append(printer_console_item)

        printer_firmware_item = Gtk.MenuItem(label="Firmware Update…")
        printer_firmware_item.connect("activate", self.on_printer_firmware_clicked)
        printer_menu.append(printer_firmware_item)

        printer_status_item = Gtk.MenuItem(label="Status…")
        printer_status_item.connect("activate", self.on_printer_status_clicked)
        printer_menu.append(printer_status_item)

        printer_menu.show_all()

        # Undo/redo, Fit and Align sit at the right end of the header bar,
        # where Qt puts them in its menu bar's corner. pack_end fills from the
        # edge inward, so they share one box, packed in the order they read
        # left to right: undo and redo first.
        end_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        header.pack_end(end_box)
        button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        button_box.get_style_context().add_class("linked")
        end_box.pack_start(button_box, False, False, 0)

        self.undo_button = Gtk.Button()
        self.undo_button.set_image(Gtk.Image.new_from_icon_name(
            "edit-undo-symbolic", Gtk.IconSize.BUTTON))
        self.undo_button.set_tooltip_text("Undo (Ctrl+Z)")
        self.undo_button.connect("clicked", self.on_undo)
        button_box.pack_start(self.undo_button, False, False, 0)

        self.redo_button = Gtk.Button()
        self.redo_button.set_image(Gtk.Image.new_from_icon_name(
            "edit-redo-symbolic", Gtk.IconSize.BUTTON))
        self.redo_button.set_tooltip_text("Redo (Ctrl+Shift+Z)")
        self.redo_button.connect("clicked", self.on_redo)
        button_box.pack_start(self.redo_button, False, False, 0)

        # Settings menu
        settings_menu = Gtk.Menu()
        settings_menu_item = Gtk.MenuItem.new_with_mnemonic("_Settings")
        settings_menu_item.set_submenu(settings_menu)
        menu_bar.append(settings_menu_item)
        
        # Label settings menu item
        label_settings_item = Gtk.MenuItem(label="Label Size\u2026")
        label_settings_item.connect("activate", self.on_label_settings_clicked)
        settings_menu.append(label_settings_item)

        # Default printer menu item
        default_printer_item = Gtk.MenuItem(label="Default Printer\u2026")
        default_printer_item.connect("activate", self.on_default_printer_clicked)
        settings_menu.append(default_printer_item)

        # Local fonts menu item
        local_fonts_item = Gtk.MenuItem(label="Local Fonts\u2026")
        local_fonts_item.connect("activate", self.on_local_fonts_clicked)
        settings_menu.append(local_fonts_item)

        settings_menu.show_all()

        # Content box with padding
        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        content_box.set_margin_top(10)
        content_box.set_margin_bottom(10)
        content_box.set_margin_start(10)
        content_box.set_margin_end(10)
        main_box.pack_start(content_box, True, True, 0)
        
        # The element tools down the left, the designer canvas beside them
        left_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        content_box.pack_start(left_box, True, True, 0)

        # Element palette
        toolbar_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        toolbar_box.set_valign(Gtk.Align.START)
        left_box.pack_start(toolbar_box, False, False, 0)
        
        # A click adds an element at a staggered spot; dragging the button onto
        # the canvas adds it where it is let go.
        self._palette = {}
        for label, kind, handler in (
                ("+ Text", 'text', self.on_add_text_clicked),
                ("+ Time", 'time', self.on_add_time_clicked),
                ("+ Serial", 'serial', self.on_add_serial_clicked),
                ("+ Numbered", 'numbered', self.on_add_numbered_clicked),
                ("+ Frame", 'frame', self.on_add_frame_clicked),
                ("+ Circle", 'circle', self.on_add_circle_clicked),
                ("+ Ellipse", 'ellipse', self.on_add_ellipse_clicked),
                ("+ Diagonal", 'diagonal', self.on_add_diagonal_clicked),
                ("+ Barcode", 'barcode', self.on_add_barcode_clicked),
                ("+ Image", 'image', self.on_add_image_clicked),
                ("+ Graphic", 'stored_graphic', self.on_add_stored_graphic_clicked)):
            button = Gtk.Button(label=label)
            button.connect("clicked", handler)
            button.drag_source_set(Gdk.ModifierType.BUTTON1_MASK, palette_targets(),
                                   Gdk.DragAction.COPY)
            button.connect("drag-begin", self._on_palette_drag_begin, kind)
            button.connect("drag-data-get", self._on_palette_drag_data_get, kind)
            toolbar_box.pack_start(button, False, False, 0)
            self._palette[kind] = handler

        # One button opening the five ^GS symbols, rather than five buttons
        # on a toolbar that is already text-labelled end to end. Each item
        # shows the symbol it adds, drawn by the same raster the canvas uses,
        # since UL and CSA have no character of their own to label them with.
        add_symbol_btn = Gtk.MenuButton(label="+ Symbol ▾")
        add_symbol_btn.set_tooltip_text("Add a graphic symbol (^GS)")
        add_symbol_btn.set_popup(self._build_symbol_menu())
        self.add_symbol_button = add_symbol_btn
        toolbar_box.pack_start(add_symbol_btn, False, False, 0)

        # Zoom controls, after undo and redo
        zoom_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        zoom_box.get_style_context().add_class("linked")
        zoom_box.set_margin_start(10)
        end_box.pack_start(zoom_box, False, False, 0)
        for label, action, tip in (("\u2212", self.on_zoom_out, "Zoom out (Ctrl+-)"),
                                   ("Fit", self.on_fit_label, "Fit the label (Ctrl+0)"),
                                   ("+", self.on_zoom_in, "Zoom in (Ctrl++)")):
            button = Gtk.Button(label=label)
            button.set_tooltip_text(tip)
            button.connect("clicked", action)
            zoom_box.pack_start(button, False, False, 0)

        # One button opening the same six commands the Edit menu holds, rather
        # than six buttons: the header bar is text-labelled, and there are no
        # object-align icons in the icon theme to label them with. GTK menu
        # items belong to one menu, so this is a second copy of the items - the
        # handlers and the enable rules are shared, and the conformance suite
        # diffs this menu against the Qt frontend's.
        align_button = Gtk.MenuButton(label="Align \u25be")
        align_button.set_tooltip_text("Line the selection up (Edit \u25b8 Align)")
        align_button.set_popup(self._build_align_menu())
        self.align_button = align_button
        align_button.set_margin_start(10)
        end_box.pack_start(align_button, False, False, 0)
        
        # Design canvas
        scrolled_canvas = Gtk.ScrolledWindow()
        scrolled_canvas.set_hexpand(True)
        scrolled_canvas.set_vexpand(True)
        # The vertical scrollbar is always present, so the width a fit is
        # measured against does not change when it appears - which would set
        # off a resize loop.
        scrolled_canvas.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.ALWAYS)
        self._view_allocation = None      # the latest, for _apply_view_size
        self._view_pending = False
        scrolled_canvas.connect("size-allocate", self._on_view_allocated)
        self.scrolled_canvas = scrolled_canvas
        # The canvas and, docked beside it, the Properties panel
        self.paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        self.paned.pack1(scrolled_canvas, True, False)
        self.paned.connect("size-allocate", self._on_paned_allocated)
        self._panel_unplaced = True
        self.panel_width = self.saved_panel['width']
        left_box.pack_start(self.paned, True, True, 0)
        
        self.design_canvas = DesignCanvas(on_change_callback=self.on_canvas_changed, 
                                         label_width=self.label_width, 
                                         label_height=self.label_height)
        self.design_canvas.dpi = self.printer_dpi
        self.design_canvas.grid = self.grid
        self.design_canvas.document.font_device = self.printer_font_device
        # The element editors are non-modal, so more than one can be on screen
        # at once. One per element, keyed by id: an open editor holds its
        # element, so the id cannot be reused while it is registered here.
        self._editors = {}
        self.design_canvas.connect("draw", self.on_canvas_draw)
        self.design_canvas.connect("element-double-clicked", self.on_element_double_clicked)
        self.design_canvas.connect("scale-changed", self._update_zoom_readout)
        self.design_canvas.connect("zoom-at", self._zoom_at)
        self.design_canvas.connect("element-dropped", self.on_element_dropped)

        # what is selected changes while the menu is closed. Connected here
        # rather than at build time: show_all() emits "show", and the handler
        # needs the canvas.
        self._edit_menu.connect("show", self._update_edit_menu)
        self._edit_menu.connect("hide", self._release_edit_menu)
        # The toolbar's copy can be opened without the Edit menu ever having
        # been shown, so every copy re-evaluates the rules on the way up.
        for menu in self._align_menus:
            menu.connect("show", lambda _m: self._update_align_items())
            menu.connect("hide", lambda _m: self._release_align_items())
        self.design_canvas.connect("edit-requested", self._on_edit_requested)

        # The selection's properties, edited live. A burst of its edits is
        # recorded before anything else can act on what it edited: a key that
        # reaches a shortcut, a menu opening, the focus leaving it.
        self.properties = PropertiesPanel(self)
        self.paned.pack2(self.properties, False, False)
        self.properties.on_shown_changed = self._on_panel_shown
        self.connect("key-press-event", self._on_window_key_press)
        self.connect("set-focus", self._on_set_focus)
        for top in menu_bar.get_children():
            if top.get_submenu() is not None:
                top.get_submenu().connect("show", self.properties.finish_edit)
        for menu in self._align_menus:
            menu.connect("show", self.properties.finish_edit)
        self.add_symbol_button.get_popup().connect("show", self.properties.finish_edit)

        # Edit history: snapshots older than the current state, and newer ones
        self._undo_stack = []
        self._redo_stack = []
        self._current_snapshot = self.design_canvas.snapshot()
        self._update_undo_actions()
        
        # Create a viewport for the canvas
        viewport = Gtk.Viewport()
        viewport.add(self.design_canvas)
        scrolled_canvas.add(viewport)
        
        # Status bar. The zoom is its own label beside it, so a status message
        # does not wipe it away.
        status_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self.status_bar = Gtk.Statusbar()
        status_row.pack_start(self.status_bar, True, True, 0)
        self.zoom_label = Gtk.Label()
        self.zoom_label.set_margin_end(8)
        status_row.pack_end(self.zoom_label, False, False, 0)
        # Beside the zoom, for the one network call the main window makes
        # itself: Print.
        self._busy = BusyBar((self.print_item, self.quick_print_item,
                              *self.upload_template_items), self.update_status)
        self._busy.set_margin_end(8)
        status_row.pack_end(self._busy, False, False, 0)
        main_box.pack_end(status_row, False, False, 0)

        self.show_all()
        self._restore_panel()
        self._update_zoom_readout()
        self.update_status("Ready")

    # --- the Properties panel's place ------------------------------------------

    def _restore_panel(self):
        """Put the Properties panel where it was left, or docked on the right.

        Floating, it starts hidden: it is asked for when wanted (a double
        click, Properties), where docked it is part of the window.
        """
        saved = self.saved_panel
        self.properties.floating_geometry = saved['geometry']
        if saved['floating']:
            self.properties.set_floating(True)
            self.properties.set_shown(False)
        elif not saved['shown']:
            self.properties.set_shown(False)
        self._on_panel_shown(self.properties.shown)

    def _on_panel_shown(self, shown):
        """Keep View > Properties Panel ticked to match the panel."""
        self.panel_item.handler_block(self._panel_item_handler)
        self.panel_item.set_active(shown)
        self.panel_item.handler_unblock(self._panel_item_handler)

    def docked_panel_width(self):
        """How wide the docked panel is now, or was last."""
        if (not self.properties.floating and self.properties.get_visible()
                and self.paned.get_allocated_width() > 1 and not self._panel_unplaced):
            return max(1, self.paned.get_allocated_width() - self.paned.get_position())
        return self.panel_width

    def place_panel(self):
        """Give the docked panel its width, now or once the pane has one."""
        width = self.paned.get_allocated_width()
        if width > 1:
            self.paned.set_position(max(100, width - self.panel_width))
            self._panel_unplaced = False
        else:
            self._panel_unplaced = True

    def _on_paned_allocated(self, _paned, allocation):
        if self._panel_unplaced and allocation.width > 1:
            self.place_panel()

    def share_keys(self, other):
        """Give another window of this one's - the floating panel - the same
        shortcuts, and the same rule about whose key it is."""
        other.add_accel_group(self.accel_group)
        other.connect("key-press-event", self._on_window_key_press)

    def _on_window_key_press(self, window, event):
        """Whose key it is, while the focus is in the Properties panel.

        A window matches its shortcuts before the focused widget sees the
        key, which is why the designer held no text entry of its own before
        the panel: Delete would have deleted the element rather than a
        character, Ctrl+A selected the label rather than the text. So while
        the focus is in one of the panel's text or number boxes, the box is
        offered the key first, and only a key it leaves goes on to the
        shortcuts. A key that reaches one ends the panel's burst first, so
        the command is an undo entry of its own.
        """
        focus = window.get_focus()
        if (focus is not None and self.properties.holds(focus)
                and isinstance(focus, (Gtk.Editable, Gtk.TextView))):
            if window.propagate_key_event(event):
                return True
        self.properties.finish_edit()
        return False

    def _on_set_focus(self, _window, widget):
        """The focus is moving; leaving the panel ends its burst."""
        if widget is None or not self.properties.holds(widget):
            self.properties.finish_edit()

    # --- the view ------------------------------------------------------------

    def _place_on_screen(self):
        """Open at a size that fits the monitor, or where it was left.

        The monitor being worked on, not whichever one is primary: with two
        screens the window can otherwise open on the other one, and a window
        taller than the work area lands wherever the window manager can put it
        - which on a stacked desktop can be almost entirely off the bottom
        edge, indistinguishable from the application never starting.
        """
        monitor = self._monitor_under_pointer()
        if monitor is None:
            self.set_default_size(*zpl_view.PREFERRED_SIZE)
            return
        area = monitor.get_workarea()
        x, y, width, height = zpl_view.place_window(
            (area.x, area.y, area.width, area.height), self.saved_geometry)
        self.set_default_size(width, height)
        self.move(x, y)

    @staticmethod
    def _monitor_under_pointer():
        """The monitor the pointer is on, falling back to the primary one."""
        display = Gdk.Display.get_default()
        if display is None:
            return None
        try:
            _screen, px, py = display.get_default_seat().get_pointer().get_position()
            monitor = display.get_monitor_at_point(px, py)
            if monitor is not None:
                return monitor
        except Exception:
            pass
        return display.get_primary_monitor() or display.get_monitor(0)

    def _on_view_allocated(self, widget, allocation):
        """Tell the canvas how much room it has, so a fit can follow it -
        once the layout pass is over, not inside it.

        A fit resizes the canvas, and a resize asked for while GTK is still
        allocating is marked and never carried out: the canvas kept its old
        size, overhanging the view, and every zoom after it stopped at the
        stale mark and did nothing until the window itself was resized.
        Dragging the Properties panel's divider did it, and showing the rulers.
        One update per pass, with the latest size.
        """
        self._view_allocation = (allocation.width, allocation.height)
        if not self._view_pending:
            self._view_pending = True
            GLib.idle_add(self._apply_view_size)

    def _apply_view_size(self):
        self._view_pending = False
        self.design_canvas.set_view_size(*self._view_allocation)
        return False

    def _update_zoom_readout(self, *_args):
        canvas = self.design_canvas
        fitted = "" if canvas.zoom is not None else " (fit)"
        self.zoom_label.set_text(
            f"{zpl_view.percent(canvas._scale())}%{fitted}")

    def _zoom_at(self, canvas, px, py, zoom_in):
        """Step the zoom, keeping the dot that was under the pointer under it.

        The pointer's position in the visible area is measured first: the zoom
        resizes the canvas and can re-centre it, so measuring afterwards would
        be measuring the wrong thing.
        """
        # PyGObject returns the pair, or None when the two widgets share no
        # ancestor - which is every moment before the canvas is realised.
        where = canvas.translate_coordinates(self.scrolled_canvas, px, py)
        old_scale = canvas._scale()
        canvas.zoom_in() if zoom_in else canvas.zoom_out()
        if where is None:
            return
        vx, vy = where
        new_scale = canvas._scale()
        for adjustment, along, across in (
                (self.scrolled_canvas.get_hadjustment(), px, vx),
                (self.scrolled_canvas.get_vadjustment(), py, vy)):
            adjustment.set_value(
                zpl_view.zoom_anchor(along, across, old_scale, new_scale))

    def on_zoom_in(self, *_args):
        self.design_canvas.zoom_in()

    def on_zoom_out(self, *_args):
        self.design_canvas.zoom_out()

    def on_fit_label(self, *_args):
        self.design_canvas.set_fit(zpl_view.FIT_LABEL)

    def on_fit_width(self, *_args):
        self.design_canvas.set_fit(zpl_view.FIT_WIDTH)

    def on_actual_size(self, *_args):
        self.design_canvas.set_zoom(1.0)

    # --- the grid --------------------------------------------------------------

    def on_show_grid_toggled(self, item):
        self.grid.shown = item.get_active()
        self._grid_changed()

    def on_snap_to_grid_toggled(self, item):
        self.grid.snap = item.get_active()
        self._grid_changed()

    def on_snap_to_elements_toggled(self, item):
        self.grid.snap_elements = item.get_active()
        self._grid_changed()

    def on_grid_settings_clicked(self, *_args):
        chosen = _grid_dialog(self, self.grid, self.design_canvas.document.dpi)
        if chosen is None:
            return
        self.grid.update(chosen)
        self._grid_changed()

    def _grid_changed(self):
        """Show the grid as it now is, and remember it."""
        zpl_grid.save_grid(self._settings_files(), self.grid)
        self.design_canvas.queue_draw()

    def main_window_closed(self, widget, event):
      if not self.close_app(widget):
        return True

    def close_app(self, widget):
      """Handle quit app from Menu or Window close button."""
      # An edit still in its burst is an unsaved change like any other
      self.properties.finish_edit()
      if not self.check_unsaved_changes():
        return False
      # Where the window was left, so it opens there next time. Saved on the
      # way out because nothing else in the session has reason to write the
      # settings file, and a resize is not worth a write of its own.
      x, y = self.get_position()
      width, height = self.get_size()
      self.saved_geometry = (x, y, width, height)
      self._save_settings()
      # Destroy the window rather than quitting the loop. The designer is not
      # always the application: opened as one window inside another Gtk
      # program, Gtk.main_quit() here would take that whole program down.
      # main() below owns the loop when the designer really is the app.
      self.destroy()
      return True

    def check_unsaved_changes(self):
      """Ask what to do with unsaved work. True means it is safe to continue."""
      def ask():
        dialog = Gtk.MessageDialog(
          parent=self, flags=0, message_type=Gtk.MessageType.QUESTION,
          buttons=Gtk.ButtonsType.NONE, text="You have unsaved changes.")
        dialog.format_secondary_text(
          "Your changes will be lost if you do not save them.")
        dialog.add_buttons("Cancel", Gtk.ResponseType.CANCEL,
                           "Discard Changes", Gtk.ResponseType.REJECT,
                           "Save", Gtk.ResponseType.ACCEPT)
        dialog.set_default_response(Gtk.ResponseType.ACCEPT)
        response = dialog.run()
        dialog.destroy()
        if response == Gtk.ResponseType.REJECT:
          return 'discard'
        return 'save' if response == Gtk.ResponseType.ACCEPT else 'cancel'

      return workflow.unsaved_changes_gate(
        self.unsaved_changes, ask, self.save_file_or_ask_for_filename)

    def on_new_clicked(self, widget=None):
        """Start a blank label, after asking about unsaved work."""
        if not self.check_unsaved_changes():
            return
        document = Document(*self.inches_to_dots(*self.label_inches))
        document.dpi = self.printer_dpi
        document.font_device = self.printer_font_device
        self.design_canvas.set_document(document)
        self.label_width = document.label_width
        self.label_height = document.label_height
        self.current_filepath = None
        self.current_zpl_content = None
        self.unsaved_changes = False
        self._update_title()
        self._reset_history()
        self.update_status("Ready")

    def on_load_file_clicked(self, widget):
        """Handle load file button click."""
        if not self.check_unsaved_changes():
          return
            
        dialog = Gtk.FileChooserDialog(
          title="Open ZPL File",
          parent=self,
          action=Gtk.FileChooserAction.OPEN
        )
        dialog.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                          Gtk.STOCK_OPEN, Gtk.ResponseType.OK)
        
        # Add ZPL file filter
        filter_zpl = Gtk.FileFilter()
        filter_zpl.set_name("ZPL files (*.zpl)")
        filter_zpl.add_pattern("*.zpl")
        dialog.add_filter(filter_zpl)
        
        filter_all = Gtk.FileFilter()
        filter_all.set_name("All files")
        filter_all.add_pattern("*")
        dialog.add_filter(filter_all)
        

        # Preview widget for selected file
        preview_image = Gtk.Image()
        dialog.set_preview_widget(preview_image)
        dialog.set_use_preview_label(False)

        def _update_preview(widget):
            try:
                filename = widget.get_preview_filename()
            except Exception:
                filename = None

            if not filename or not filename.lower().endswith('.zpl'):
                dialog.set_preview_widget_active(False)
                return

            try:
                renderer = self._new_renderer()
                img = renderer.render_from_file(filename)
                bio = io.BytesIO()
                img.save(bio, format='PNG')
                loader = GdkPixbuf.PixbufLoader()
                loader.write(bio.getvalue())
                loader.close()
                pixbuf = loader.get_pixbuf()
                if pixbuf:
                    # Scale preview to reasonable width while keeping aspect
                    max_preview_w = 300
                    if pixbuf.get_width() > 0 and pixbuf.get_width() > max_preview_w:
                        scale = max_preview_w / pixbuf.get_width()
                        new_w = int(pixbuf.get_width() * scale)
                        new_h = int(pixbuf.get_height() * scale)
                        pix = pixbuf.scale_simple(new_w, new_h, GdkPixbuf.InterpType.BILINEAR)
                    else:
                        pix = pixbuf
                    preview_image.set_from_pixbuf(pix)
                    dialog.set_preview_widget_active(True)
                else:
                    dialog.set_preview_widget_active(False)
            except Exception:
                dialog.set_preview_widget_active(False)

        dialog.connect('update-preview', _update_preview)

        response = dialog.run()
        if response == Gtk.ResponseType.OK:
            filepath = dialog.get_filename()
            dialog.destroy()
            self.load_zpl_file(filepath)
        else:
            dialog.destroy()

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
        for child in self.recent_menu.get_children():
            self.recent_menu.remove(child)
            child.destroy()
        for number, (path, text) in enumerate(
                zip(recent, workflow.recent_labels(recent)), 1):
            # 1 to 9, then 0 for the tenth. A name's own underscores are
            # doubled, or GTK would take the first for the mnemonic.
            item = Gtk.MenuItem.new_with_mnemonic(
                f"_{number % 10} {text.replace('_', '__')}")
            item.set_tooltip_text(path)
            item.connect("activate", lambda _item, p=path: self.on_open_recent(p))
            self.recent_menu.append(item)
        if recent:
            self.recent_menu.append(Gtk.SeparatorMenuItem())
            clear_item = Gtk.MenuItem.new_with_mnemonic("_Clear List")
            clear_item.connect("activate", lambda _item: self.on_clear_recent())
            self.recent_menu.append(clear_item)
        self.recent_item.set_sensitive(bool(recent))
        self.recent_menu.show_all()

    def on_open_recent(self, filepath):
        """Open a file from File > Open Recent."""
        # Checked before asking about unsaved work: nobody should be made to
        # save or discard for the sake of a file that is not there to open.
        if not os.path.isfile(filepath):
            self._store_recent(workflow.forget_recent(
                workflow.load_recent(self._recent_candidates()), filepath))
            self.show_error_dialog(workflow.recent_gone_message(filepath))
            return
        if self.check_unsaved_changes():
            self.load_zpl_file(filepath)

    def on_clear_recent(self):
        """Empty File > Open Recent."""
        self._store_recent([])

    def on_save_clicked(self, widget):
        """Handle save button click."""
        self.save_file_or_ask_for_filename()

    def save_file_or_ask_for_filename(self):
        # Get ZPL from designer (may raise if an image element fails to encode)
        try:
            content = self.design_canvas.to_zpl()
        except Exception as e:
            self.show_error_dialog(f"Failed to generate ZPL: {e}")
            return False

        if not content.strip() or content == "^XA\n^XZ":
            self.show_error_dialog("No content to save")
            return False
        
        # If we have a current file path, save directly
        if self.current_filepath:
          return self.save_zpl_file(self.current_filepath, content)
        
        # Show save dialog
        return self.save_dialog()
    
    def on_save_as_clicked(self, widget):
        """Handle save as button click."""
        self.save_dialog()

    def save_dialog(self):
        """Show save dialog and save content to file."""
        try:
            content = self.design_canvas.to_zpl()
        except Exception as e:
            self.show_error_dialog(f"Failed to generate ZPL: {e}")
            return

        if not content.strip() or content == "^XA\n^XZ":
            self.show_error_dialog("No content to save")
            return
        
        dialog = Gtk.FileChooserDialog(
            title="Save ZPL File",
            parent=self,
            action=Gtk.FileChooserAction.SAVE
        )
        dialog.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                          Gtk.STOCK_SAVE, Gtk.ResponseType.OK)
        # GTK writes over an existing file without a word unless this is set;
        # Qt's save dialog asks on its own. A label overwritten by accident is
        # not recoverable, so both frontends have to ask.
        dialog.set_do_overwrite_confirmation(True)
        
        # Add ZPL file filter
        filter_zpl = Gtk.FileFilter()
        filter_zpl.set_name("ZPL files (*.zpl)")
        filter_zpl.add_pattern("*.zpl")
        dialog.add_filter(filter_zpl)
        
        filter_all = Gtk.FileFilter()
        filter_all.set_name("All files")
        filter_all.add_pattern("*")
        dialog.add_filter(filter_all)
        
        # Set default filename
        if self.current_filepath:
            dialog.set_filename(self.current_filepath)
        else:
            dialog.set_current_name("untitled.zpl")
        
        # A name is only checked for an overwrite once its suffix is settled,
        # so declining a replace comes back here rather than dropping the save.
        while True:
            if dialog.run() != Gtk.ResponseType.OK:
                dialog.destroy()
                return False
            filepath = workflow.confirm_save_path(
                dialog.get_filename(), self._confirm_overwrite)
            if filepath:
                dialog.destroy()
                return self.save_zpl_file(filepath, content)

    def _confirm_overwrite(self, filepath):
        """Whether to replace a file the chooser never asked about."""
        dialog = Gtk.MessageDialog(
            parent=self, flags=0, message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.NONE,
            text=f"A file named \u201c{os.path.basename(filepath)}\u201d already exists.")
        dialog.format_secondary_text("Replacing it will overwrite its contents.")
        dialog.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                           "Replace", Gtk.ResponseType.ACCEPT)
        response = dialog.run()
        dialog.destroy()
        return response == Gtk.ResponseType.ACCEPT
  
    def save_zpl_file(self, filepath: str, content: str):
      """Save ZPL content to a file."""
      try:
        with open(filepath, 'w', encoding='utf-8') as f:
          f.write(content)
        
        self.current_filepath = filepath
        self._update_title()
        filename = os.path.basename(filepath)
        self.update_status(f"Saved: {filename}")
        self.unsaved_changes = False
      except Exception as e:
        self.show_error_dialog(f"Failed to save file: {e}")
        self.update_status("Save failed")
        return False
      self._remember_recent(filepath)
      return True
    
    def load_zpl_file(self, filepath: str):
        """Load a ZPL file and update the views."""
        try:
            content, code_page = zpl_parser.read_file(filepath)

            document, loaded_dpi = zpl_parser.parse_zpl(content, self.renderer)
            document.font_device = self.printer_font_device
            self.design_canvas.set_document(document)
            self.current_zpl_content = content
            self.current_filepath = filepath
            self._update_title()
            self._remember_recent(filepath)
            self.label_width = document.label_width
            self.label_height = document.label_height

            rescaled = self._offer_dpi_rescale(loaded_dpi)
            self.label_width = self.design_canvas.label_width
            self.label_height = self.design_canvas.label_height
            self.design_canvas.sync_size()

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
                self._notify_decoded(code_page)
            workflow.warn_unsupported(content, self._warn_unsupported,
                                      self._warn_control_redefined)

        except Exception as e:
            self.show_error_dialog(f"Failed to load file: {e}")
            self.update_status("Error loading file")

    def _warn_unsupported(self, commands):
        """Say which commands opening this file has left behind."""
        dialog = Gtk.MessageDialog(
            parent=self, flags=0, message_type=Gtk.MessageType.WARNING,
            buttons=Gtk.ButtonsType.OK,
            text="This label uses ZPL the designer does not understand.")
        dialog.format_secondary_text(
            f"{', '.join(commands)}\n\nThese are not shown on the canvas, and "
            f"saving will not preserve them.")
        dialog.run()
        dialog.destroy()

    def _notify_decoded(self, code_page):
        """Say that this file was not UTF-8, and what a save will do with it."""
        text, detail = workflow.decoded_notice(code_page)
        dialog = Gtk.MessageDialog(
            parent=self, flags=0, message_type=Gtk.MessageType.INFO,
            buttons=Gtk.ButtonsType.OK, text=text)
        dialog.format_secondary_text(detail)
        dialog.run()
        dialog.destroy()

    def _warn_control_redefined(self, spellings):
        """Say that this file moved ZPL's control characters, and what a save does."""
        dialog = Gtk.MessageDialog(
            parent=self, flags=0, message_type=Gtk.MessageType.INFO,
            buttons=Gtk.ButtonsType.OK,
            text="This label redefines ZPL's control characters.")
        dialog.format_secondary_text(
            f"{', '.join(spellings)}\n\nIt has been read with them in force. "
            f"Saving writes the standard ^, ~ and , in their place and leaves "
            f"the redefinition out, so the saved file prints the same label "
            f"but no longer changes the printer's control characters.")
        dialog.run()
        dialog.destroy()

    def _ask_font_problem(self, text, detail, uploadable):
        """'upload', 'print' or 'cancel' for a label whose fonts the printer
        does not have."""
        dialog = Gtk.MessageDialog(parent=self, flags=0,
                                   message_type=Gtk.MessageType.WARNING,
                                   buttons=Gtk.ButtonsType.NONE, text=text)
        dialog.format_secondary_text(detail)
        dialog.add_button(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL)
        dialog.add_button("Print Anyway", Gtk.ResponseType.OK)
        if uploadable:
            dialog.add_button("Upload & Print", Gtk.ResponseType.APPLY)
            dialog.set_default_response(Gtk.ResponseType.APPLY)
        else:
            dialog.set_default_response(Gtk.ResponseType.CANCEL)
        response = dialog.run()
        dialog.destroy()
        if response == Gtk.ResponseType.APPLY:
            return 'upload'
        return 'print' if response == Gtk.ResponseType.OK else 'cancel'

    def _ask_template_name(self, memory, default):
        """The name to store a template under in `memory`, or None if cancelled."""
        name, problem = default, None
        while True:
            dialog = Gtk.Dialog(title="Upload Template", transient_for=self,
                                modal=True)
            dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
            dialog.add_button("Upload", Gtk.ResponseType.OK)
            dialog.set_default_response(Gtk.ResponseType.OK)
            content = dialog.get_content_area()
            content.set_spacing(8)
            content.set_border_width(12)
            if problem:
                content.pack_start(Gtk.Label(label=problem), False, False, 0)
            entry = Gtk.Entry()
            entry.set_text(name)
            entry.set_activates_default(True)
            _make_row(content, f"Name in {memory}:", entry, 150)
            dialog.show_all()
            response = dialog.run()
            name = entry.get_text().strip().upper()
            dialog.destroy()
            if response != Gtk.ResponseType.OK:
                return None
            problem = workflow.template_name_problem(name)
            if problem is None:
                return name

    def on_upload_template_clicked(self, widget, device, memory):
        """Store the design on the printer as a ^DF format, printing nothing."""
        document = self.design_canvas.document
        if document.is_empty():
            self.show_error_dialog("There is nothing to upload.")
            return
        name = self._ask_template_name(
            f"{memory} ({device}:)",
            workflow.default_template_name(document, self.current_filepath))
        if name is None:
            return
        try:
            content = workflow.build_template_upload(document, device, name)
        except Exception as e:
            self.show_error_dialog(f"Failed to generate ZPL: {e}")
            return
        address, port = self.printer_address, self.printer_port
        stored = f"{device}:{name}.ZPL"
        self.update_status(f"Uploading {stored}...")

        def work(cancel):
            printer_io.send(address, port, content.encode('utf-8'), 10,
                            cancel=cancel)

        def done(_result, error):
            if isinstance(error, printer_io.Cancelled):
                self.update_status("Upload cancelled")
            elif error is not None:
                self.show_error_dialog(str(error))
                self.update_status("Upload failed")
            else:
                self.update_status(f"Stored {stored} on {address}:{port}")

        self._busy.run(work, done)

    def on_print_clicked(self, widget):
        """Print in up to three steps, each network one off the main loop
        behind the status bar's busy row: ask the printer which fonts it has,
        prompt if any are missing (on the main loop, as a prompt must be),
        then upload whatever the user chose to and send the label.
        """
        content = self.design_canvas.to_zpl(explicit_flips=True)
        address, port = self.printer_address, self.printer_port

        def send_label(uploadable):
            self.update_status("Printing...")

            def work(cancel):
                if uploadable:
                    workflow.upload_fonts(uploadable, address, port,
                                          self._busy.report, cancel)
                    self._busy.report("Printing...")
                printer_io.send(address, port, content.encode('utf-8'), 10,
                                cancel=cancel)

            def done(_result, error):
                if isinstance(error, printer_io.Cancelled):
                    self.update_status("Printing cancelled")
                elif error is not None:
                    self.show_error_dialog(str(error))
                    self.update_status("Printing failed")
                else:
                    self.update_status(f"Sent to {address}:{port}")

            self._busy.run(work, done)

        def checked(result, error):
            if isinstance(error, printer_io.Cancelled):
                self.update_status("Printing cancelled")
                return
            if error is not None:
                self.show_error_dialog(str(error))
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
            answer = self._ask_font_problem(text, detail, uploadable)
            if answer == 'upload':
                send_label(uploadable)
            elif answer == 'print':
                send_label({})
            else:
                self.update_status("Printing cancelled")

        if not self.design_canvas.document.font_sources():
            send_label({})
            return
        self.update_status("Checking printer fonts...")
        self._busy.run(lambda cancel: workflow.missing_printer_fonts(
            self.design_canvas.document, address, port, cancel=cancel), checked)
    
    def _new_renderer(self) -> ZPLRenderer:
        """A renderer preloaded with the fonts this session knows about.

        A bare ZPLRenderer has an empty font_registry, so every ^A@ would fall
        back to the default face and custom fonts would never show in a preview.
        """
        renderer = ZPLRenderer(width=self.label_width, height=self.label_height)
        for name, path in self.renderer.font_registry.items():
            renderer.register_font(name, path)
        for el in self.design_canvas.elements:
            name = getattr(el, 'printer_font_name', None)
            path = getattr(el, 'font_path', None)
            if name and path:
                renderer.register_font(name, path)
        if self.renderer.custom_font_path:
            renderer.set_font(self.renderer.custom_font_path)
        return renderer

    def on_printer_fonts_clicked(self, widget):
        """Show the fonts stored on the printer, and add or remove them."""
        dialog = Gtk.Dialog(title="Printer Fonts", parent=self, flags=0)
        dialog.add_button(Gtk.STOCK_CLOSE, Gtk.ResponseType.CLOSE)
        dialog.set_default_size(480, 560)

        content = dialog.get_content_area()
        content.set_spacing(8)
        content.set_margin_start(8)
        content.set_margin_end(8)
        content.set_margin_top(8)
        content.set_margin_bottom(8)

        content.pack_start(Gtk.Label(label="<b>Uploaded Fonts</b>",
                                     use_markup=True, halign=Gtk.Align.START),
                           False, False, 0)
        status = Gtk.Label(halign=Gtk.Align.START)
        status.set_line_wrap(True)
        content.pack_start(status, False, False, 0)

        # Font, then the memory its drive letter names - the same second
        # column Printer Objects has, and for the same reason: a bare "B:"
        # says nothing to a reader who has not memorised the ZPL manual's
        # letter designations. Column 0 stays the full d:NAME.TTF spec.
        font_specs = []
        store = Gtk.ListStore(str, str)
        view = Gtk.TreeView(model=store)
        for title, index in (("Font", 0), ("Memory", 1)):
            column = Gtk.TreeViewColumn(title, Gtk.CellRendererText(),
                                        text=index)
            column.set_resizable(True)
            view.append_column(column)
        scroller = Gtk.ScrolledWindow()
        scroller.set_vexpand(True)
        scroller.add(view)
        content.pack_start(scroller, True, True, 0)

        preview = Gtk.Label(halign=Gtk.Align.START)
        preview.set_line_wrap(True)
        content.pack_start(preview, False, False, 0)

        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        upload_btn = Gtk.Button(label="Upload\u2026")
        delete_btn = Gtk.Button(label="Delete")
        refresh_btn = Gtk.Button(label="Refresh")
        for b in (upload_btn, delete_btn, refresh_btn):
            buttons.pack_start(b, False, False, 0)
        busy = BusyBar((upload_btn, delete_btn, refresh_btn), status.set_text)
        buttons.pack_end(busy, False, False, 0)
        content.pack_start(buttons, False, False, 0)

        content.pack_start(Gtk.Label(label="<b>Built-in Fonts</b>",
                                     use_markup=True, halign=Gtk.Align.START),
                           False, False, 0)
        resident_status = Gtk.Label(halign=Gtk.Align.START)
        resident_status.set_line_wrap(True)
        content.pack_start(resident_status, False, False, 0)

        resident_store = Gtk.ListStore(str)
        resident_view = Gtk.TreeView(model=resident_store)
        resident_view.append_column(
            Gtk.TreeViewColumn("Font", Gtk.CellRendererText(), text=0))
        resident_scroller = Gtk.ScrolledWindow()
        resident_scroller.set_vexpand(True)
        resident_scroller.add(resident_view)
        content.pack_start(resident_scroller, True, True, 0)

        def show_resident(detected):
            resident_store.clear()
            if detected:
                resident_status.set_text(
                    f"Reported by the printer at {self.printer_address}.")
            elif detected is None:
                resident_status.set_text(
                    "Could not confirm which are present - showing the "
                    "standard set. Sizes and styles are as published, not "
                    "rendered.")
            else:
                resident_status.set_text(
                    "Printer reported none of the standard set - showing "
                    "it anyway. Sizes and styles are as published, not "
                    "rendered.")
            for font in zpl_fonts.RESIDENT_FONTS:
                label = (f"{font['code']} \u2014 {font['name']} "
                        f"({font['matrix']}, {font['kind']})")
                if detected and font['code'].upper() in detected:
                    label += " \u2014 detected on this printer"
                resident_store.append([label])

        def show_preview(path):
            zpl_fonts.register_app_font(path)
            family = zpl_fonts.family_for_file(path)
            escaped = GLib.markup_escape_text("The quick brown fox 0123456789")
            preview.set_markup(
                f'<span font_desc="{GLib.markup_escape_text(family)} 16">'
                f'{escaped}</span>')

        def update_preview(selection):
            model_, treeiter = selection.get_selected()
            if treeiter is None:
                preview.set_text("")
                return
            _device, name = zpl_fonts.split_font_spec(
                model_.get_value(treeiter, 0))
            path = zpl_fonts.file_for_printer_name(name)
            if path:
                show_preview(path)
                return
            # Printers won't hand a font's bytes back once uploaded -
            # confirmed live (SGD retrieval gets no reply at all, and a
            # printer's own FTP server, where present, answers with a plain
            # 550 Permission denied for a .TTF while other stored files
            # download fine) - so there is nothing to try here, only this
            # to say.
            preview.set_markup(
                "<i>(preview unavailable \u2014 printers block "
                "downloading fonts to protect font distribution "
                "rights)</i>")

        def refresh(*_a):
            """Both lists in one trip: the stored fonts, then the resident ones."""
            store.clear()
            resident_store.clear()
            preview.set_text("")
            font_specs.clear()
            delete_btn.set_sensitive(False)
            status.set_text(f"Listing fonts on {self.printer_address}...")
            resident_status.set_text("")

            def query(cancel):
                fonts = zpl_fonts.query_printer_fonts(
                    self.printer_address, self.printer_port, cancel=cancel)
                busy.report("Asking which built-in fonts it has...")
                detected = zpl_fonts.query_resident_fonts(
                    self.printer_address, self.printer_port, cancel=cancel)
                return fonts, detected

            def done(result, error):
                if isinstance(error, printer_io.Cancelled):
                    status.set_text("Listing cancelled.")
                    return
                answer, detected = (None, None) if error is not None else result
                fonts, unreadable = (None, ()) if answer is None else answer
                if fonts is None:
                    status.set_text(f"Could not reach the printer at "
                                    f"{self.printer_address}:{self.printer_port}.")
                else:
                    font_specs[:] = sorted(fonts)
                    for spec in font_specs:
                        store.append([spec, graphic_store.device_name(spec)])
                    delete_btn.set_sensitive(bool(fonts))
                    status.set_text(workflow.listing_status(
                        self.printer_address, "font", zpl_fonts.DEVICES,
                        font_specs, unreadable))
                show_resident(detected)

            busy.run(query, done)

        view.get_selection().connect("changed", update_preview)

        def on_upload(_b):
            families = zpl_fonts.list_ttf_families()
            if not families:
                self.show_error_dialog("No TrueType fonts were found on this system.")
                return
            chooser = Gtk.FontChooserDialog(title="Upload Font to Printer", parent=dialog)
            chooser.set_level(Gtk.FontChooserLevel.FAMILY)
            chooser.set_filter_func(lambda family, face: family.get_name() in families)
            resp = chooser.run()
            desc = chooser.get_font_desc() if resp in (Gtk.ResponseType.OK, Gtk.ResponseType.APPLY) else None
            chooser.destroy()
            family = desc.get_family() if desc else None
            path = zpl_fonts.file_for_family(family) if family else None
            if not path:
                return
            device = self._ask_font_device(dialog)
            if device is None:
                return
            name = zpl_fonts.printer_font_name(path)
            shown = zpl_fonts.printer_font_path(name, device)
            status.set_text(f"Uploading {shown}...")

            def done(_result, error):
                if isinstance(error, printer_io.Cancelled):
                    status.set_text(f"Upload of {shown} cancelled.")
                    return
                if error is not None:
                    self.show_error_dialog(f"Font upload failed: {error}")
                    return
                self.renderer.register_font(name, path)
                refresh()

            busy.run(lambda cancel: zpl_fonts.upload_font(
                self.printer_address, self.printer_port, path, name, device,
                cancel=cancel), done)

        def on_delete(_b):
            model, treeiter = view.get_selection().get_selected()
            if treeiter is None:
                return
            shown = model[treeiter][0]
            # The drive the selected row is actually on, not an assumed E:.
            device, name = zpl_fonts.split_font_spec(shown)
            status.set_text(f"Deleting {shown}...")

            def done(_result, error):
                if isinstance(error, printer_io.Cancelled):
                    status.set_text(f"Deleting {shown} cancelled.")
                    return
                if error is not None:
                    self.show_error_dialog(f"Could not delete {shown}: {error}")
                    return
                refresh()

            busy.run(lambda cancel: zpl_fonts.delete_printer_font(
                self.printer_address, self.printer_port, name, device,
                cancel=cancel), done)

        upload_btn.connect("clicked", on_upload)
        delete_btn.connect("clicked", on_delete)
        refresh_btn.connect("clicked", refresh)

        content.show_all()
        refresh()
        dialog.run()
        busy.abandon()
        dialog.destroy()

    def _ask_font_device(self, parent):
        """Which memory to upload a font to. None if cancelled.

        Asked after the family, not before, so the question a user came to
        answer comes first. Opens on the Font memory printer setting, since
        that is where the designer's own ^A@ will point - picking another
        here is a manager's freedom, the same one Store Graphic has, and puts
        the font somewhere this label will not name on its own.
        """
        dialog = Gtk.Dialog(title="Upload Font To", parent=parent, flags=0)
        dialog.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                           Gtk.STOCK_OK, Gtk.ResponseType.OK)

        content = dialog.get_content_area()
        content.set_spacing(4)
        content.set_margin_start(8)
        content.set_margin_end(8)
        content.set_margin_top(8)
        content.set_margin_bottom(8)

        combo, codes = _make_combo(STORED_GRAPHIC_DEVICES,
                                   self.printer_font_device)
        _make_row(content, "Memory:", combo)

        content.show_all()
        response = dialog.run()
        device = codes[combo.get_active()] if response == Gtk.ResponseType.OK else None
        dialog.destroy()
        return device

    def _ask_device_spec(self, parent):
        """Prompt for device / name / extension. None if cancelled.

        The same three fields "Edit Stored Graphic" asks for, minus command
        and magnification - those describe an ^XG/^IM reference, not the
        object being stored.
        """
        dialog = Gtk.Dialog(title="Store Graphic As", parent=parent, flags=0)
        dialog.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                           Gtk.STOCK_OK, Gtk.ResponseType.OK)

        content = dialog.get_content_area()
        content.set_spacing(4)
        content.set_margin_start(8)
        content.set_margin_end(8)
        content.set_margin_top(8)
        content.set_margin_bottom(8)

        device_combo, device_codes = _make_combo(STORED_GRAPHIC_DEVICES, 'R')
        _make_row(content, "Device:", device_combo)

        name_entry = Gtk.Entry()
        name_entry.set_text("LOGO")
        name_entry.set_max_length(8)
        _make_row(content, "Name:", name_entry)

        ext_entry = Gtk.Entry()
        ext_entry.set_text("GRF")
        _make_row(content, "Extension:", ext_entry)

        content.show_all()
        response = dialog.run()
        spec = None
        if response == Gtk.ResponseType.OK:
            device_code = device_codes[device_combo.get_active()]
            object_name = (name_entry.get_text().strip() or 'UNKNOWN').upper()
            extension = (ext_entry.get_text().strip() or 'GRF').upper()
            spec = f"{device_code}:{object_name}.{extension}"
        dialog.destroy()
        return spec

    def _ask_object_name(self, parent, default_name: str, default_ext: str):
        """Ask for the name and extension an arbitrary local file should be
        stored under on the printer's E: drive - the only device
        printer_objects.upload_printer_object can target, so unlike
        _ask_device_spec this asks for no device. Case is left exactly as
        typed rather than forced to upper, unlike _ask_device_spec: a
        CISDFCRC16-stored object is not necessarily an upper-case 8.3 ZPL
        object (the manual's own examples include privkey.nrd, feedback.get),
        and file.type's retrieval is case sensitive - see
        zplcore.printer_objects. Returns (name, ext), or None if cancelled.
        """
        dialog = Gtk.Dialog(title="Store Object As", parent=parent, flags=0)
        dialog.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                           Gtk.STOCK_OK, Gtk.ResponseType.OK)

        content = dialog.get_content_area()
        content.set_spacing(4)
        content.set_margin_start(8)
        content.set_margin_end(8)
        content.set_margin_top(8)
        content.set_margin_bottom(8)

        name_entry = Gtk.Entry()
        name_entry.set_text(default_name)
        name_entry.set_max_length(8)
        _make_row(content, "Name (on E:):", name_entry)

        ext_entry = Gtk.Entry()
        ext_entry.set_text(default_ext)
        _make_row(content, "Extension:", ext_entry)

        content.show_all()
        response = dialog.run()
        result = None
        if response == Gtk.ResponseType.OK:
            name = name_entry.get_text().strip() or 'UNKNOWN'
            ext = ext_entry.get_text().strip() or 'DAT'
            result = (name, ext)
        dialog.destroy()
        return result

    def _confirm_delete_object(self, parent, spec: str) -> bool:
        """Whether to really delete `spec` from the printer - the same
        shape as _confirm_overwrite, for the same reason: a destructive
        action against real state needs a way back that "just don't click
        it again" cannot offer, since this one cannot be undone from here.
        Shared by the Graphics and Objects dialogs, since neither the
        wording nor the reasoning is specific to graphics.
        """
        dialog = Gtk.MessageDialog(
            parent=parent, flags=0, message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.NONE,
            text=f"Delete {spec} from the printer?")
        dialog.format_secondary_text(
            f"This removes it from {self.printer_address}:{self.printer_port} "
            "itself, not just this list. It cannot be undone from here.")
        dialog.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                           "Delete", Gtk.ResponseType.ACCEPT)
        response = dialog.run()
        dialog.destroy()
        return response == Gtk.ResponseType.ACCEPT

    def _confirm_firmware_update(self, parent, name: str, size: int,
                                 where: str) -> bool:
        """Whether to really send firmware to `where` (address:port, or a
        device path) - the printer reboots and an interrupted flash can
        leave it unusable, so it is asked every time."""
        dialog = Gtk.MessageDialog(
            parent=parent, flags=0, message_type=Gtk.MessageType.WARNING,
            buttons=Gtk.ButtonsType.NONE,
            text=f"Send {name} ({size:,} bytes) to {where}?")
        dialog.format_secondary_text(
            "The printer will flash the file and restart. Do not switch it "
            "off or cancel while it is being written: an interrupted update "
            "can leave the printer unusable.")
        dialog.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                           "Update", Gtk.ResponseType.ACCEPT)
        dialog.set_default_response(Gtk.ResponseType.CANCEL)
        response = dialog.run()
        dialog.destroy()
        return response == Gtk.ResponseType.ACCEPT

    def on_printer_firmware_clicked(self, widget):
        """Show the firmware the printer runs now and send it a new file.

        Modal, like the Fonts/Graphics/Objects managers: nothing else should
        touch the printer while its flash is being written. The upload's bar
        is the BusyBar's, switched to a percentage by report_fraction.

        The printer is the session's network one, or any USB printer this
        machine has - the way in to one in forced download mode. Refresh
        looks for USB printers again, since one that restarts into that mode
        can come back under another number.
        """
        address, port = self.printer_address, self.printer_port
        dialog = Gtk.Dialog(title="Firmware Update", parent=self, flags=0)
        dialog.set_default_size(460, 200)
        content = dialog.get_content_area()
        content.set_spacing(8)
        content.set_margin_start(8)
        content.set_margin_end(8)
        content.set_margin_top(8)
        content.set_margin_bottom(8)

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        row.pack_start(Gtk.Label(label="Connection:"), False, False, 0)
        connection = Gtk.ComboBoxText()
        row.pack_start(connection, True, True, 0)
        content.pack_start(row, False, False, 0)

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        current = Gtk.Label(label="Current firmware: checking…",
                            halign=Gtk.Align.START, selectable=True)
        row.pack_start(current, True, True, 0)
        refresh_btn = Gtk.Button(label="Refresh")
        row.pack_start(refresh_btn, False, False, 0)
        content.pack_start(row, False, False, 0)

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        file_label = Gtk.Label(label="No firmware file chosen.",
                               halign=Gtk.Align.START)
        file_label.set_line_wrap(True)
        row.pack_start(file_label, True, True, 0)
        browse_btn = Gtk.Button(label="Browse…")
        row.pack_start(browse_btn, False, False, 0)
        content.pack_start(row, False, False, 0)

        status = Gtk.Label(halign=Gtk.Align.START)
        status.set_line_wrap(True)
        content.pack_start(status, False, False, 0)

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        # Not one of the BusyBar's blocked buttons: Help is most wanted while
        # an upload is going wrong.
        help_btn = Gtk.Button(label="Help")
        row.pack_start(help_btn, False, False, 0)
        update_btn = Gtk.Button(label="Update")
        update_btn.set_sensitive(False)
        row.pack_start(update_btn, False, False, 0)
        close_btn = Gtk.Button(label="Close")
        row.pack_end(close_btn, False, False, 0)
        busy = BusyBar((connection, refresh_btn, browse_btn, update_btn,
                        close_btn), width=330)
        row.pack_end(busy, False, False, 0)
        content.pack_end(row, False, False, 0)

        chosen = {'path': None, 'size': 0}

        def list_connections():
            """Network first (id ""), then every USB printer, keeping the
            one chosen if it is still there."""
            was = connection.get_active_id()
            connection.handler_block(changed_id)
            connection.remove_all()
            connection.append("", f"Network: {address}:{port}")
            for path, described in zpl_local.list_ports():
                connection.append(path, f"USB: {path} ({described})"
                                  if described else f"USB: {path}")
            if not was or not connection.set_active_id(was):
                connection.set_active_id("")
            connection.handler_unblock(changed_id)

        def chosen_local():
            """The chosen USB port, or None for the network printer."""
            path = connection.get_active_id()
            return zpl_local.LocalPort(path) if path else None

        def ask_version(_w=None):
            current.set_text("Current firmware: checking…")
            local = chosen_local()

            def done(version, error):
                if isinstance(error, printer_io.Cancelled):
                    current.set_text("Current firmware: unknown (cancelled)")
                elif version and error is None:
                    current.set_text(f"Current firmware: {version}")
                else:
                    current.set_text("Current firmware: unavailable")
                if isinstance(error, PermissionError):
                    status.set_text(str(error))

            busy.run(lambda cancel: zpl_firmware.current_firmware(
                address, port, cancel=cancel, local=local), done)

        def on_refresh(_b):
            list_connections()
            ask_version()

        def on_browse(_b):
            chooser = Gtk.FileChooserDialog(
                title="Choose Firmware File", parent=dialog,
                action=Gtk.FileChooserAction.OPEN)
            chooser.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                                Gtk.STOCK_OPEN, Gtk.ResponseType.OK)
            response = chooser.run()
            path = chooser.get_filename()
            chooser.destroy()
            if response != Gtk.ResponseType.OK or not path:
                return
            try:
                size = os.path.getsize(path)
            except OSError as e:
                self.show_error_dialog(f"Could not read {path}: {e}")
                return
            if size == 0:
                self.show_error_dialog(f"{os.path.basename(path)} is empty.")
                return
            chosen['path'], chosen['size'] = path, size
            file_label.set_text(f"{os.path.basename(path)} ({size:,} bytes)")
            status.set_text("")
            update_btn.set_sensitive(True)

        def on_update(_b):
            path = chosen['path']
            if path is None:
                return
            name, local = os.path.basename(path), chosen_local()
            where = local.path if local else f"{address}:{port}"
            if not self._confirm_firmware_update(dialog, name, chosen['size'],
                                                 where):
                return
            status.set_text(f"Sending {name}…")

            def progress(sent, total):
                busy.report_fraction(sent / total)

            started = time.monotonic()

            def done(_result, error):
                if isinstance(error, printer_io.Cancelled):
                    status.set_text(
                        "Cancelled. The printer may have received part of "
                        "the file; check its firmware before relying on it.")
                elif error is not None:
                    status.set_text("Firmware update failed.")
                    self.show_error_dialog(f"Could not send {name}: {error}")
                else:
                    took = zpl_firmware.format_elapsed(time.monotonic() - started)
                    status.set_text(
                        f"Firmware sent in {took}. The printer will flash it and "
                        "restart; press Refresh in a minute to see the new "
                        "version.")

            busy.run(lambda cancel: zpl_firmware.upload_firmware(
                address, port, path, progress=progress, cancel=cancel,
                local=local), done)

        changed_id = connection.connect("changed", ask_version)
        list_connections()
        refresh_btn.connect("clicked", on_refresh)
        browse_btn.connect("clicked", on_browse)
        update_btn.connect("clicked", on_update)
        help_btn.connect("clicked", lambda _b: self.show_help_dialog(
            dialog, "Firmware Update", zpl_firmware.HELP))
        close_btn.connect("clicked", lambda _b: dialog.response(Gtk.ResponseType.CLOSE))
        dialog.show_all()
        ask_version()
        dialog.run()
        busy.abandon()
        dialog.destroy()

    def on_printer_graphics_clicked(self, widget):
        """View, store, retrieve and delete graphics on the real printer -
        whichever one is actually in effect this session
        (self.printer_address/self.printer_port, which a session override
        moves without touching the persisted default - see
        self._default_printer), the same target Printer Fonts already uses.

        Modelled closely on on_printer_fonts_clicked: refresh() queries the
        printer live and reports when it cannot be reached, exactly as that
        one does for ^HW. The one thing this keeps that Fonts has no need
        for is graphic_store, the in-session local cache ^XG/^IM/^IL already
        read from - Store and Retrieve mirror a successful network result
        into it (the same way upload_font's caller also registers the font
        locally), purely so an already-placed reference on the canvas
        updates without a second round trip to the printer.
        """
        dialog = Gtk.Dialog(title="Printer Graphics", parent=self, flags=0)
        dialog.add_button(Gtk.STOCK_CLOSE, Gtk.ResponseType.CLOSE)
        dialog.set_default_size(420, 340)

        content = dialog.get_content_area()
        content.set_spacing(8)
        content.set_margin_start(8)
        content.set_margin_end(8)
        content.set_margin_top(8)
        content.set_margin_bottom(8)

        status = Gtk.Label(halign=Gtk.Align.START)
        status.set_line_wrap(True)
        content.pack_start(status, False, False, 0)

        body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        content.pack_start(body, True, True, 0)

        list_store = Gtk.ListStore(str)
        tree_view = Gtk.TreeView(model=list_store)
        tree_view.append_column(
            Gtk.TreeViewColumn("Graphic", Gtk.CellRendererText(), text=0))
        scroller = Gtk.ScrolledWindow()
        scroller.set_vexpand(True)
        scroller.set_size_request(220, -1)
        scroller.add(tree_view)
        body.pack_start(scroller, True, True, 0)

        preview = Gtk.Image()
        preview.set_size_request(160, 160)
        body.pack_start(preview, False, False, 0)

        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        store_btn = Gtk.Button(label="Store…")
        retrieve_btn = Gtk.Button(label="Retrieve…")
        delete_btn = Gtk.Button(label="Delete")
        refresh_btn = Gtk.Button(label="Refresh")
        retrieve_btn.set_sensitive(False)
        delete_btn.set_sensitive(False)
        for b in (store_btn, retrieve_btn, delete_btn, refresh_btn):
            buttons.pack_start(b, False, False, 0)
        busy = BusyBar((store_btn, retrieve_btn, delete_btn, refresh_btn),
                       status.set_text)
        buttons.pack_end(busy, False, False, 0)
        content.pack_start(buttons, False, False, 0)

        entries = []
        changed_any = False

        def refresh(*_a):
            nonlocal entries
            list_store.clear()
            entries = []
            preview.clear()
            retrieve_btn.set_sensitive(False)
            delete_btn.set_sensitive(False)
            status.set_text(f"Listing graphics on {self.printer_address}...")

            def done(specs, error):
                nonlocal entries
                if isinstance(error, printer_io.Cancelled):
                    status.set_text("Listing cancelled.")
                    return
                if specs is None or error is not None:
                    status.set_text(f"Could not reach the printer at "
                                    f"{self.printer_address}:{self.printer_port}.")
                    return
                entries, unreadable = specs
                for spec in entries:
                    cached = graphic_store.recall(spec)
                    suffix = f" ({cached.width}×{cached.height})" if cached else ""
                    list_store.append([f"{spec}{suffix}"])
                status.set_text(workflow.listing_status(
                    self.printer_address, "graphic", graphic_store.DEVICES,
                    entries, unreadable))

            busy.run(lambda cancel: graphic_store.query_printer_graphics(
                self.printer_address, self.printer_port, cancel=cancel), done)

        def selected_entry():
            """The spec the list has selected, or None."""
            model, treeiter = tree_view.get_selection().get_selected()
            if treeiter is None:
                return None
            index = model.get_path(treeiter).get_indices()[0]
            return entries[index] if index < len(entries) else None

        def on_selection_changed(_selection):
            spec = selected_entry()
            retrieve_btn.set_sensitive(spec is not None)
            delete_btn.set_sensitive(spec is not None)
            if spec is None:
                preview.clear()
                return
            image = graphic_store.recall(spec)
            if image is None:
                # Not fetched this session yet - Retrieve first.
                preview.clear()
                return
            pixbuf = to_pixbuf(image.convert('RGBA'))
            if pixbuf is None:
                preview.clear()
                return
            scale = min(1.0, 150 / max(pixbuf.get_width(), pixbuf.get_height()))
            w = max(1, int(pixbuf.get_width() * scale))
            h = max(1, int(pixbuf.get_height() * scale))
            preview.set_from_pixbuf(
                pixbuf.scale_simple(w, h, GdkPixbuf.InterpType.BILINEAR))

        tree_view.get_selection().connect("changed", on_selection_changed)

        def on_store(_b):
            nonlocal changed_any
            chooser = Gtk.FileChooserDialog(
                title="Store Graphic", parent=dialog,
                action=Gtk.FileChooserAction.OPEN)
            chooser.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                                Gtk.STOCK_OPEN, Gtk.ResponseType.OK)
            filter_img = Gtk.FileFilter()
            filter_img.set_name("Image files (*.jpg, *.jpeg, *.png)")
            for pat in ("*.jpg", "*.jpeg", "*.JPG", "*.JPEG", "*.png", "*.PNG"):
                filter_img.add_pattern(pat)
            chooser.add_filter(filter_img)
            response = chooser.run()
            filepath = chooser.get_filename() if response == Gtk.ResponseType.OK else None
            chooser.destroy()
            if not filepath:
                return

            spec = self._ask_device_spec(dialog)
            if spec is None:
                return

            try:
                image = Image.open(filepath)
                image.load()
                if image.mode not in ('RGB', 'L'):
                    image = image.convert('RGB')
            except Exception as e:
                self.show_error_dialog(f"Could not open {filepath}: {e}")
                return

            status.set_text(f"Uploading {spec}...")

            def done(_result, error):
                nonlocal changed_any
                if isinstance(error, printer_io.Cancelled):
                    status.set_text(f"Upload of {spec} cancelled.")
                    return
                if error is not None:
                    self.show_error_dialog(f"Could not store {spec}: {error}")
                    refresh()
                    return
                graphic_store.store(spec, image)
                changed_any = True
                refresh()

            busy.run(lambda cancel: graphic_store.upload_graphic(
                self.printer_address, self.printer_port, spec, image,
                cancel=cancel), done)

        def on_retrieve(_b):
            """Fetch the selected graphic's real bytes from the printer,
            cache them locally so ^XG/^IM/^IL resolve, and offer to save
            them to a file too."""
            spec = selected_entry()
            if spec is None:
                return
            status.set_text(f"Retrieving {spec}...")

            def done(image, error):
                nonlocal changed_any
                if isinstance(error, printer_io.Cancelled):
                    status.set_text(f"Retrieving {spec} cancelled.")
                    return
                if error is not None:
                    self.show_error_dialog(f"Could not retrieve {spec}: {error}")
                    refresh()
                    return
                graphic_store.store(spec, image)
                changed_any = True
                chooser = Gtk.FileChooserDialog(
                    title="Save Retrieved Graphic", parent=dialog,
                    action=Gtk.FileChooserAction.SAVE)
                chooser.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                                    Gtk.STOCK_SAVE, Gtk.ResponseType.OK)
                chooser.set_do_overwrite_confirmation(True)
                _device, name, ext = graphic_store.split_device_spec(spec)
                chooser.set_current_name(f"{name}.png")
                response = chooser.run()
                filepath = chooser.get_filename() if response == Gtk.ResponseType.OK else None
                chooser.destroy()
                if filepath:
                    try:
                        if not Path(filepath).suffix:
                            filepath += '.png'
                        image.save(filepath)
                    except Exception as e:
                        self.show_error_dialog(f"Could not save {filepath}: {e}")
                refresh()

            busy.run(lambda cancel: graphic_store.retrieve_printer_graphic(
                self.printer_address, self.printer_port, spec, cancel=cancel), done)

        def on_delete(_b):
            spec = selected_entry()
            if spec is None:
                return
            if not self._confirm_delete_object(dialog, spec):
                return
            status.set_text(f"Deleting {spec}...")

            def done(_result, error):
                nonlocal changed_any
                if isinstance(error, printer_io.Cancelled):
                    status.set_text(f"Deleting {spec} cancelled.")
                    return
                if error is not None:
                    self.show_error_dialog(f"Could not delete {spec}: {error}")
                    refresh()
                    return
                graphic_store.delete(spec)
                changed_any = True
                refresh()

            busy.run(lambda cancel: graphic_store.delete_printer_graphic(
                self.printer_address, self.printer_port, spec, cancel=cancel), done)

        store_btn.connect("clicked", on_store)
        retrieve_btn.connect("clicked", on_retrieve)
        delete_btn.connect("clicked", on_delete)
        refresh_btn.connect("clicked", refresh)

        content.show_all()
        refresh()
        dialog.run()
        busy.abandon()
        dialog.destroy()
        # Storing/retrieving/deleting a graphic changes no Document state, so
        # this is a plain repaint - queue_draw(), never on_canvas_changed() -
        # so an ^XG/^IM/^IL that now resolves differently is shown without
        # marking the file dirty or pushing a bogus undo entry.
        if changed_any:
            self.design_canvas.queue_draw()

    def on_printer_objects_clicked(self, widget):
        """Every object on the real printer, across R:/E:/B:/A:/Z: and any
        extension - not just the fonts and graphics on_printer_fonts_clicked
        and on_printer_graphics_clicked already manage. Modelled on those:
        no preview, since most objects here are not images. Store and
        Retrieve both exist here because both have a genuinely generic
        printer command behind them - CISDFCRC16 and file.type - unlike
        ~DY/~DG/^HG, which are each locked to one format and stay with the
        two dialogs that already know it.

        Store always writes to E: - CISDFCRC16 gives no device choice - so
        its prompt asks only for a name and extension, never a device. Z:
        is read-only factory content ^ID cannot delete (see
        printer_objects.DEVICES), so Delete is withheld for a Z: selection
        even though it is listed and can still be Retrieved.
        """
        dialog = Gtk.Dialog(title="Printer Objects", parent=self, flags=0)
        dialog.add_button(Gtk.STOCK_CLOSE, Gtk.ResponseType.CLOSE)
        dialog.set_default_size(460, 340)

        content = dialog.get_content_area()
        content.set_spacing(8)
        content.set_margin_start(8)
        content.set_margin_end(8)
        content.set_margin_top(8)
        content.set_margin_bottom(8)

        status = Gtk.Label(halign=Gtk.Align.START)
        status.set_line_wrap(True)
        content.pack_start(status, False, False, 0)

        # Object, then the memory type its device letter names - "Z:" and
        # "E:" say nothing to a user who has not memorised the ZPL manual's
        # letter designations, so the second column says it in words (see
        # graphic_store.DEVICE_NAMES). Column 0 stays the full d:NAME.EXT
        # spec, so what the list shows still matches every status message
        # and confirmation prompt below verbatim.
        list_store = Gtk.ListStore(str, str)
        tree_view = Gtk.TreeView(model=list_store)
        for title, index in (("Object", 0), ("Memory", 1)):
            column = Gtk.TreeViewColumn(title, Gtk.CellRendererText(),
                                        text=index)
            column.set_resizable(True)
            tree_view.append_column(column)
        scroller = Gtk.ScrolledWindow()
        scroller.set_vexpand(True)
        scroller.add(tree_view)
        content.pack_start(scroller, True, True, 0)

        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        store_btn = Gtk.Button(label="Store…")
        retrieve_btn = Gtk.Button(label="Retrieve…")
        delete_btn = Gtk.Button(label="Delete")
        refresh_btn = Gtk.Button(label="Refresh")
        retrieve_btn.set_sensitive(False)
        delete_btn.set_sensitive(False)
        for b in (store_btn, retrieve_btn, delete_btn, refresh_btn):
            buttons.pack_start(b, False, False, 0)
        busy = BusyBar((store_btn, retrieve_btn, delete_btn, refresh_btn),
                       status.set_text)
        buttons.pack_end(busy, False, False, 0)
        content.pack_start(buttons, False, False, 0)

        changed_any = False

        def refresh(*_a):
            list_store.clear()
            retrieve_btn.set_sensitive(False)
            delete_btn.set_sensitive(False)
            status.set_text(f"Listing objects on {self.printer_address}...")

            def done(specs, error):
                if isinstance(error, printer_io.Cancelled):
                    status.set_text("Listing cancelled.")
                    return
                if specs is None or error is not None:
                    status.set_text(f"Could not reach the printer at "
                                    f"{self.printer_address}:{self.printer_port}.")
                    return
                entries, unreadable = specs
                for spec in entries:
                    list_store.append(
                        [spec, graphic_store.device_name(spec)])
                status.set_text(workflow.listing_status(
                    self.printer_address, "object", printer_objects.DEVICES,
                    entries, unreadable))

            busy.run(lambda cancel: printer_objects.query_printer_objects(
                self.printer_address, self.printer_port, cancel=cancel), done)

        def selected_entry():
            """The spec the list has selected, or None."""
            model, treeiter = tree_view.get_selection().get_selected()
            if treeiter is None:
                return None
            # Column 0 is the spec itself, so no parallel list of entries has
            # to be kept in step with the model just to resolve a selection.
            return model.get_value(treeiter, 0)

        def on_selection_changed(_selection):
            spec = selected_entry()
            retrieve_btn.set_sensitive(spec is not None)
            # ^ID silently ignores Z: (read-only factory content), so
            # Delete would report success and change nothing - withhold it
            # rather than let that happen.
            delete_btn.set_sensitive(spec is not None and not spec.startswith('Z:'))

        tree_view.get_selection().connect("changed", on_selection_changed)

        def on_store(_b):
            chooser = Gtk.FileChooserDialog(
                title="Store Object", parent=dialog,
                action=Gtk.FileChooserAction.OPEN)
            chooser.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                                Gtk.STOCK_OPEN, Gtk.ResponseType.OK)
            response = chooser.run()
            filepath = chooser.get_filename() if response == Gtk.ResponseType.OK else None
            chooser.destroy()
            if not filepath:
                return

            src = Path(filepath)
            result = self._ask_object_name(
                dialog, src.stem[:8] or 'UNKNOWN', src.suffix.lstrip('.') or 'DAT')
            if result is None:
                return
            name, ext = result

            try:
                data = src.read_bytes()
            except Exception as e:
                self.show_error_dialog(f"Could not read {filepath}: {e}")
                return

            status.set_text(f"Uploading E:{name}.{ext}...")

            def done(_result, error):
                if isinstance(error, printer_io.Cancelled):
                    status.set_text(f"Upload of E:{name}.{ext} cancelled.")
                    return
                if error is not None:
                    self.show_error_dialog(f"Could not store E:{name}.{ext}: {error}")
                refresh()

            busy.run(lambda cancel: printer_objects.upload_printer_object(
                self.printer_address, self.printer_port, name, ext, data,
                cancel=cancel), done)

        def on_retrieve(_b):
            spec = selected_entry()
            if spec is None:
                return
            status.set_text(f"Retrieving {spec}...")

            def done(data, error):
                if isinstance(error, printer_io.Cancelled):
                    status.set_text(f"Retrieving {spec} cancelled.")
                    return
                if isinstance(error, printer_objects.ObjectNotRetrievable):
                    self.show_error_dialog(
                        "This printer does not support retrieving stored files "
                        "(no reply to the retrieval command).")
                    refresh()
                    return
                if error is not None:
                    self.show_error_dialog(f"Could not retrieve {spec}: {error}")
                    refresh()
                    return
                chooser = Gtk.FileChooserDialog(
                    title="Save Retrieved Object", parent=dialog,
                    action=Gtk.FileChooserAction.SAVE)
                chooser.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                                    Gtk.STOCK_SAVE, Gtk.ResponseType.OK)
                chooser.set_do_overwrite_confirmation(True)
                _device, name, ext = graphic_store.split_device_spec(spec)
                chooser.set_current_name(f"{name}.{ext.lower()}")
                response = chooser.run()
                filepath = chooser.get_filename() if response == Gtk.ResponseType.OK else None
                chooser.destroy()
                if filepath:
                    try:
                        Path(filepath).write_bytes(data)
                    except Exception as e:
                        self.show_error_dialog(f"Could not save {filepath}: {e}")
                refresh()

            busy.run(lambda cancel: printer_objects.download_printer_object(
                self.printer_address, self.printer_port, spec, cancel=cancel), done)

        def on_delete(_b):
            spec = selected_entry()
            if spec is None:
                return
            if not self._confirm_delete_object(dialog, spec):
                return
            status.set_text(f"Deleting {spec}...")

            def done(_result, error):
                nonlocal changed_any
                if isinstance(error, printer_io.Cancelled):
                    status.set_text(f"Deleting {spec} cancelled.")
                    return
                if error is not None:
                    self.show_error_dialog(f"Could not delete {spec}: {error}")
                    refresh()
                    return
                if graphic_store.delete(spec):
                    changed_any = True
                refresh()

            busy.run(lambda cancel: printer_objects.delete_printer_object(
                self.printer_address, self.printer_port, spec, cancel=cancel), done)

        store_btn.connect("clicked", on_store)
        retrieve_btn.connect("clicked", on_retrieve)
        delete_btn.connect("clicked", on_delete)
        refresh_btn.connect("clicked", refresh)

        content.show_all()
        refresh()
        dialog.run()
        busy.abandon()
        dialog.destroy()
        # Same reasoning as on_printer_graphics_clicked: deleting an object
        # changes no Document state, so this is a plain repaint.
        if changed_any:
            self.design_canvas.queue_draw()

    def on_printer_console_clicked(self, widget):
        """A free-form send/reply console for whatever the type-specific
        managers (Fonts/Graphics/Objects) don't cover - one-off diagnostics
        like ~HS host status or ~HI host identification, or an SGD
        getvar/setvar not wrapped by any dialog. Text is sent to the printer
        exactly as typed, no ^XA/^XZ wrapping added, so both immediate
        commands and full formats work unchanged.

        A plain top-level Gtk.Window, not a Gtk.Dialog run modally: unlike
        the other printer managers, this one is meant to stay open while the
        user keeps working in the main window (watching status while editing
        a label, say), so it is shown with show_all() rather than blocking
        on run(). Only one is ever open at a time - a second click presents
        the existing window instead of stacking another one. on_send reads
        self.printer_address/self.printer_port fresh on every send rather
        than a value captured at open time, so a printer changed via Printer
        Settings while this window is open takes effect immediately.
        """
        if self.printer_console_window is not None:
            self.printer_console_window.present()
            return

        window = Gtk.Window(title="Printer Console")
        window.set_transient_for(self)
        window.set_destroy_with_parent(True)
        window.set_position(Gtk.WindowPosition.CENTER_ON_PARENT)
        window.set_default_size(480, 420)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        content.set_margin_start(8)
        content.set_margin_end(8)
        content.set_margin_top(8)
        content.set_margin_bottom(8)
        window.add(content)

        input_view = Gtk.TextView()
        input_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        input_scroll = Gtk.ScrolledWindow()
        input_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        input_scroll.set_shadow_type(Gtk.ShadowType.IN)
        input_scroll.set_size_request(-1, 90)
        input_scroll.add(input_view)
        content.pack_start(input_scroll, False, False, 0)

        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        send_btn = Gtk.Button(label="Send")
        close_btn = Gtk.Button(label="Close")
        buttons.pack_start(send_btn, False, False, 0)
        buttons.pack_start(close_btn, False, False, 0)
        busy = BusyBar((send_btn,))
        buttons.pack_end(busy, False, False, 0)
        content.pack_start(buttons, False, False, 0)

        log_view = Gtk.TextView()
        log_view.set_editable(False)
        log_view.set_cursor_visible(False)
        log_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        log_scroll = Gtk.ScrolledWindow()
        log_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        log_scroll.set_shadow_type(Gtk.ShadowType.IN)
        log_scroll.set_vexpand(True)
        log_scroll.add(log_view)
        content.pack_start(log_scroll, True, True, 0)

        def log(entry):
            log_buf = log_view.get_buffer()
            log_buf.insert(log_buf.get_end_iter(), entry)
            log_view.scroll_to_iter(log_buf.get_end_iter(), 0, False, 0, 0)

        def on_send(_b):
            input_buf = input_view.get_buffer()
            text = input_buf.get_text(input_buf.get_start_iter(),
                                      input_buf.get_end_iter(), False)
            if not text.strip():
                return
            address, port = self.printer_address, self.printer_port

            def done(reply, error):
                if isinstance(error, printer_io.Cancelled):
                    log(f"> {text}\n(cancelled)\n\n")
                    return
                if error is not None:
                    self.show_error_dialog(f"Could not send command: {error}")
                    return
                log(f"> {text}\n{reply or '(no reply)'}\n\n")
                input_buf.set_text("")

            busy.run(lambda cancel: printer_io.send_command(
                address, port, text, cancel=cancel), done)

        send_btn.connect("clicked", on_send)
        close_btn.connect("clicked", lambda _b: window.destroy())

        def on_destroy(_w):
            busy.abandon()
            self.printer_console_window = None

        window.connect("destroy", on_destroy)

        self.printer_console_window = window
        window.show_all()

    # How a reading's level is shown. The same three colours the Qt panel uses,
    # so a fault looks like a fault in either frontend.
    _STATUS_COLOURS = {printer_status.ERROR: '#c0392b',
                       printer_status.WARN: '#b9770e',
                       printer_status.OK: '#1e8449'}

    def _status_bar(self, reading, colour):
        """A reading the core gave a fraction to: its text, with a slim gauge
        under it.

        The text is a plain label above the bar rather than drawn on it. Set as
        the bar's own text it renders small and, once the fill reaches it, in
        poor contrast against it - and the two toolkits put it in different
        places, GTK above the trough and Qt inside it. Stacked this way the
        figure stays full size and legible in both, and the bar is left to do
        the one thing a bar is better at than a number: showing the proportion
        at a glance.

        The gauge shows what is *used* (printer_status.Reading.fraction) while
        the text says what is free - the pair a disk gauge shows, and the pair
        someone deciding whether a font will fit needs.

        Colour needs a CSS provider here, unlike Qt's stylesheet: only the
        filled part is styled, so the trough keeps the theme's own background
        and the bar stays legible in a dark one. The provider is attached to
        this widget alone rather than to the screen, so it cannot leak into any
        other progress bar in the app - the BusyBar spinner included.
        """
        stack = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        stack.set_hexpand(True)
        stack.set_margin_bottom(4)

        text = Gtk.Label(xalign=0)
        text.set_line_wrap(True)
        if colour:
            text.set_markup(f'<span foreground="{colour}" weight="bold">'
                            f'{GLib.markup_escape_text(reading.value)}</span>')
        else:
            text.set_text(reading.value)
        stack.pack_start(text, False, False, 0)

        bar = Gtk.ProgressBar()
        bar.set_fraction(reading.fraction)
        bar.set_show_text(False)
        bar.set_hexpand(True)
        if colour:
            provider = Gtk.CssProvider()
            provider.load_from_data(
                f"progressbar progress {{ background-color: {colour}; }}"
                .encode())
            bar.get_style_context().add_provider(
                provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        stack.pack_start(bar, False, False, 0)
        return stack

    def on_printer_status_clicked(self, widget):
        """What the printer says about its own state - faults, what it is
        working on, memory, and how its head is wearing.

        A plain top-level Gtk.Window and a singleton, like the console and for
        the same reason plus one of its own: this one can be left
        auto-refreshing while the user works on the label, which is the whole
        point of watching a printer. So address and port are read from self on
        every query rather than captured here, and a printer changed via Printer
        Settings takes effect on the next refresh.

        Two queries, matching the core's split: the specs once, since a model
        number and a head-wear figure do not move while anyone watches, and only
        the activity on a tick.
        """
        if self.printer_status_window is not None:
            self.printer_status_window.present()
            return

        window = Gtk.Window(title="Printer Status")
        window.set_transient_for(self)
        window.set_destroy_with_parent(True)
        window.set_position(Gtk.WindowPosition.CENTER_ON_PARENT)
        window.set_default_size(460, 620)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        for edge in ('start', 'end', 'top', 'bottom'):
            getattr(content, f'set_margin_{edge}')(8)
        window.add(content)

        status = Gtk.Label(xalign=0)
        status.set_line_wrap(True)
        content.pack_start(status, False, False, 0)

        # Scrolled, because how many rows a report has depends on how many
        # faults the printer is reporting - the height cannot be known here.
        sections_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        scroll.add(sections_box)
        content.pack_start(scroll, True, True, 0)

        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        refresh_btn = Gtk.Button(label="Refresh")
        copy_btn = Gtk.Button(label="Copy")
        copy_btn.set_sensitive(False)
        auto_check = Gtk.CheckButton(label="Auto-refresh every")
        interval = Gtk.SpinButton.new_with_range(2, 300, 1)
        interval.set_value(5)
        seconds_label = Gtk.Label(label="s")
        for widget_ in (refresh_btn, copy_btn, auto_check, interval, seconds_label):
            buttons.pack_start(widget_, False, False, 0)
        close_btn = Gtk.Button(label="Close")
        busy = BusyBar((refresh_btn,), status.set_text)
        buttons.pack_end(busy, False, False, 0)
        buttons.pack_end(close_btn, False, False, 0)
        content.pack_start(buttons, False, False, 0)

        # Held in a dict so the nested functions can rebind them: the reports
        # shown, and the GLib source id of the auto-refresh timer. That id is
        # owned by the main loop, not by this window, so it has to be removed by
        # hand on destroy - unlike Qt's QTimer, which is parented to its dialog
        # and dies with it.
        state = {'specs': None, 'activity': None, 'timer': None, 'dead': False}

        def show_reports():
            for child in sections_box.get_children():
                sections_box.remove(child)
            for report in (state['activity'], state['specs']):
                if report is None:
                    continue
                for section in report.sections:
                    frame = Gtk.Frame(label=section.title)
                    grid = Gtk.Grid(row_spacing=2, column_spacing=12)
                    for edge in ('start', 'end', 'top', 'bottom'):
                        getattr(grid, f'set_margin_{edge}')(6)
                    for row, reading in enumerate(section.readings):
                        name = Gtk.Label(label=reading.label + ":", xalign=0)
                        colour = self._STATUS_COLOURS.get(reading.level)
                        if reading.fraction is not None:
                            value = self._status_bar(reading, colour)
                        else:
                            value = Gtk.Label(xalign=0)
                            value.set_line_wrap(True)
                            if colour:
                                value.set_markup(
                                    f'<span foreground="{colour}" weight="bold">'
                                    f'{GLib.markup_escape_text(reading.value)}</span>')
                            else:
                                value.set_text(reading.value)
                        grid.attach(name, 0, row, 1, 1)
                        grid.attach(value, 1, row, 1, 1)
                    if section.note:
                        note = Gtk.Label(xalign=0)
                        note.set_line_wrap(True)
                        note.set_markup(
                            f'<i>{GLib.markup_escape_text(section.note)}</i>')
                        grid.attach(note, 0, len(section.readings), 2, 1)
                    frame.add(grid)
                    sections_box.pack_start(frame, False, False, 0)
            sections_box.show_all()

        def done(result, error, address, port):
            if state['dead']:
                return
            if isinstance(error, printer_io.Cancelled):
                status.set_text("Cancelled.")
                return
            if error is not None:
                status.set_text(f"Could not ask the printer: {error}")
                return
            specs, activity = result
            if specs is None and activity is None:
                # Nothing answered. What is already on screen came from a
                # better moment, so it is left alone rather than cleared - a
                # panel that empties itself when a printer is unplugged loses
                # the reading that would explain why.
                status.set_text(f"Could not reach the printer at "
                                f"{address}:{port}.")
                return
            state['specs'], state['activity'] = specs, activity
            show_reports()
            copy_btn.set_sensitive(True)
            unanswered = sorted({name for report in (specs, activity)
                                 if report is not None
                                 for name in report.unanswered})
            note = (" \u2014 no answer to " + ", ".join(unanswered)) if unanswered else ""
            status.set_text(f"{address}:{port}{note}")

        def refresh(*_a):
            address, port = self.printer_address, self.printer_port
            status.set_text(f"Asking {address}...")

            def work(cancel):
                specs = printer_status.query_printer_specs(address, port,
                                                           cancel=cancel)
                busy.report(f"Asking {address} what it is doing...")
                activity = printer_status.query_printer_activity(address, port,
                                                                cancel=cancel)
                return specs, activity

            busy.run(work, lambda result, error: done(result, error, address, port))

        def tick():
            """One auto-refresh. Returns False - ending the timer - once the
            window is gone, so a tick already queued when it closed cannot
            touch destroyed widgets; and skips rather than queues while a call
            is out, so a printer slower than the interval builds no backlog."""
            if state['dead']:
                return False
            if busy.running:
                return True
            address, port = self.printer_address, self.printer_port
            busy.run(
                lambda cancel: (state['specs'],
                                printer_status.query_printer_activity(
                                    address, port, cancel=cancel)),
                lambda result, error: done(result, error, address, port))
            return True

        def arm():
            disarm()
            if auto_check.get_active() and not state['dead']:
                state['timer'] = GLib.timeout_add_seconds(
                    int(interval.get_value()), tick)

        def disarm():
            if state['timer'] is not None:
                GLib.source_remove(state['timer'])
                state['timer'] = None

        def on_copy(_b):
            text = printer_status.report_text(state['specs'], state['activity'])
            Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).set_text(text, -1)
            status.set_text("Report copied to the clipboard.")

        def on_destroy(_w):
            # All three, in this order: stop the timer first so no further tick
            # can start a call, then abandon whatever is already out, then let
            # a later click open a fresh window.
            state['dead'] = True
            disarm()
            busy.abandon()
            self.printer_status_window = None

        refresh_btn.connect("clicked", refresh)
        copy_btn.connect("clicked", on_copy)
        auto_check.connect("toggled", lambda _b: arm())
        interval.connect("value-changed", lambda _b: arm())
        close_btn.connect("clicked", lambda _b: window.destroy())
        window.connect("destroy", on_destroy)

        self.printer_status_window = window
        window.show_all()
        refresh()

    def _offer_dpi_rescale(self, loaded_dpi=workflow._FROM_DOCUMENT):
        """If the file was drawn for another resolution, offer to rescale it.

        Returns a note for the status bar when it rescaled, else None.
        """
        def ask(old, printer_dpi, assumed, w_in, h_in):
            dialog = Gtk.MessageDialog(
                parent=self, flags=0, message_type=Gtk.MessageType.QUESTION,
                buttons=Gtk.ButtonsType.NONE,
                text=(f"This label does not say what resolution it was drawn "
                      f"for, so {old} dpi is assumed." if assumed
                      else f"This label was designed for {old} dpi."))
            dialog.format_secondary_text(
                f"The printer is set to {printer_dpi} dpi. Rescaling by "
                f"{printer_dpi / old:.2f} keeps its physical size; keeping the "
                f"dots as they are makes it print {w_in:.1f} x {h_in:.1f} inches.")
            dialog.add_button(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL)
            dialog.add_button("Keep Dots", Gtk.ResponseType.NO)
            dialog.add_button("Rescale", Gtk.ResponseType.YES)
            dialog.set_default_response(Gtk.ResponseType.YES)
            response = dialog.run()
            dialog.destroy()
            if response == Gtk.ResponseType.YES:
                return 'rescale'
            return 'keep' if response == Gtk.ResponseType.NO else 'cancel'

        return workflow.reconcile_dpi(self.design_canvas.document,
                                      self.printer_dpi, ask, file_dpi=loaded_dpi)

    def inches_to_dots(self, w_in: float, h_in: float):
        """Physical size -> dots at the current printer resolution."""
        return (max(1, int(round(w_in * self.printer_dpi))),
                max(1, int(round(h_in * self.printer_dpi))))

    def dots_to_inches(self, w_dots: int, h_dots: int):
        """Dots -> physical size at the current printer resolution."""
        return (w_dots / self.printer_dpi, h_dots / self.printer_dpi)

    def _printer_font_names(self, exclude=None):
        """Printer font names already used by the label's text elements."""
        return self.design_canvas.document.printer_font_names(exclude=exclude)

    def _load_settings(self):
        """Load persisted settings from the config file."""
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
            # Ignored unless it names a drive that exists: a hand-edited file
            # must not point every upload at one the printer has not got.
            font_device = parser.get(
                'printer', 'font_device',
                fallback=self.printer_font_device).strip().upper()
            if font_device in zpl_fonts.DEVICES:
                self.printer_font_device = font_device
            zpl_fonts.set_extra_font_dir(
                parser.get('fonts', 'extra_dir', fallback=''))
            # The Properties panel's place. Its own section, which the Qt
            # frontend keeps its window state in too, under a key of its own
            if parser.has_section('docks'):
                panel = self.saved_panel
                panel['floating'] = parser.getboolean('docks', 'floating',
                                                      fallback=False)
                panel['shown'] = parser.getboolean('docks', 'shown', fallback=True)
                width = parser.getint('docks', 'width', fallback=PANEL_WIDTH)
                panel['width'] = width if 100 <= width <= 3000 else PANEL_WIDTH
                geometry = parser.get('docks', 'floating_geometry', fallback='')
                parts = [int(v) for v in geometry.split(',') if v.strip()]
                panel['geometry'] = tuple(parts) if len(parts) == 4 else None
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
        """Write the current settings to the config file."""
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
                # session override (the Print window) deliberately changes
                # without this ever running. Only Default Printer and Label
                # Settings are allowed to move self._default_printer, and they
                # do so before calling this.
                parser.set('printer', 'address', self._default_printer[0])
                parser.set('printer', 'port', str(self._default_printer[1]))
                parser.set('printer', 'dpi', str(self._default_printer[2]))
                parser.set('printer', 'font_device', self._default_printer[3])
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
                    self._save_panel(parser)
                path.parent.mkdir(parents=True, exist_ok=True)
                with open(path, 'w') as f:
                    parser.write(f)
                return
            except configparser.Error as e:
                self.show_error_dialog(f"Could not save settings: {e}")
                return
            except OSError as e:
                last_error = e
        self.show_error_dialog(f"Could not save settings: {last_error}")

    def _save_panel(self, parser):
        """Where the Properties panel is, into [docks]."""
        panel = self.properties
        panel._remember_floating_geometry()
        if not parser.has_section('docks'):
            parser.add_section('docks')
        parser.set('docks', 'floating', 'yes' if panel.floating else 'no')
        if not panel.floating:
            parser.set('docks', 'shown', 'yes' if panel.shown else 'no')
        parser.set('docks', 'width', str(int(self.docked_panel_width())))
        if panel.floating_geometry:
            parser.set('docks', 'floating_geometry',
                       ','.join(str(int(v)) for v in panel.floating_geometry))

    def on_default_printer_clicked(self, widget):
        """Handle Default Printer menu item click."""
        # Opened with the persisted default, not the printer currently in
        # effect: a session override (the Print window) must never leak into
        # this dialog and get re-saved as the new default just by clicking OK.
        (default_address, default_port, default_dpi,
         default_font_device) = self._default_printer
        result = _printer_picker_dialog(
            self, "Default Printer", default_address, default_port,
            default_dpi, default_font_device)
        if result is None:
            return
        self.printer_address, self.printer_port, new_dpi, font_device = result
        old_dpi = self.printer_dpi
        self.printer_dpi = new_dpi
        self._set_font_device(font_device)
        self._default_printer = (self.printer_address, self.printer_port,
                                 self.printer_dpi, self.printer_font_device)
        self._save_settings()
        self.update_status(f"Printer set to {self.printer_address}:{self.printer_port}")
        if self.printer_dpi != old_dpi:
            # Pointing at a printer with a different head changes what the
            # open label measures - 1200 dots is 4in at 300 dpi and 5.9in
            # at 203. Re-stamping it and saying nothing would leave the
            # elements at the old scale and print the label oversized.
            note = self._offer_dpi_rescale()
            self.design_canvas.sync_size()
            if note:
                self.on_canvas_changed()
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
        document = getattr(self.design_canvas, 'document', None)
        if document is not None:
            document.font_device = device

    def on_extra_font_folder_clicked(self, widget, parent=None):
        """Pick the one extra folder the font fallback scan also covers."""
        current = zpl_fonts.EXTRA_FONT_DIR
        dialog = Gtk.FileChooserDialog(
            title="Extra Font Folder", parent=parent or self,
            action=Gtk.FileChooserAction.SELECT_FOLDER)
        dialog.add_button(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL)
        CLEAR = 1
        clear = dialog.add_button("Clear", CLEAR)
        clear.set_sensitive(bool(current))
        dialog.add_button(Gtk.STOCK_OK, Gtk.ResponseType.OK)
        if current and Path(current).expanduser().is_dir():
            dialog.set_current_folder(str(Path(current).expanduser()))
        response = dialog.run()
        folder = dialog.get_filename()
        dialog.destroy()
        if response == CLEAR:
            folder = ''
        elif response != Gtk.ResponseType.OK or not folder:
            return
        zpl_fonts.set_extra_font_dir(folder)
        self._save_settings()

    def on_local_fonts_clicked(self, widget):
        """Show what the directory-scan font fallback sees, and whether it's
        in use.

        fc-list is the only thing list_ttf_families() ever calls first, and
        on most systems that's the end of it - but on a system where
        fontconfig is missing, broken, or just not set up, every font
        chooser would otherwise go quietly empty with nothing here to
        explain why. Distinct from the Printer -> Fonts... dialog, which is
        about fonts stored on the physical printer, not fonts installed on
        this machine.
        """
        dialog = Gtk.Dialog(title="Local Fonts", parent=self, flags=0)
        dialog.add_button(Gtk.STOCK_CLOSE, Gtk.ResponseType.CLOSE)
        dialog.set_default_size(480, 420)

        content = dialog.get_content_area()
        content.set_spacing(8)
        content.set_margin_start(8)
        content.set_margin_end(8)
        content.set_margin_top(8)
        content.set_margin_bottom(8)

        status = Gtk.Label(halign=Gtk.Align.START)
        status.set_line_wrap(True)
        content.pack_start(status, False, False, 0)

        report_view = Gtk.TextView()
        report_view.set_editable(False)
        report_view.set_cursor_visible(False)
        report_view.set_monospace(True)
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroller.set_shadow_type(Gtk.ShadowType.IN)
        scroller.set_vexpand(True)
        scroller.add(report_view)
        content.pack_start(scroller, True, True, 0)

        rescan_btn = Gtk.Button(label="Rescan")
        extra_btn = Gtk.Button(label="Extra Font Folder\u2026")
        buttons = Gtk.Box(spacing=8)
        buttons.pack_start(rescan_btn, False, False, 0)
        buttons.pack_start(extra_btn, False, False, 0)
        content.pack_start(buttons, False, False, 0)

        def refresh(rescan):
            fc_list_ok, report = zpl_fonts.font_discovery_status(refresh=rescan)
            status.set_text(
                "fc-list is working normally - the directory scan below is "
                "not being used, but shows what it would find if fc-list "
                "stopped working." if fc_list_ok else
                "fc-list is unavailable or reports no TrueType fonts on "
                "this system - LinuxZPL is using the directory scan below "
                "to find fonts instead.")

            lines = ["Font file scan", "-" * 60, ""]
            for d in report.dirs:
                lines.append(f"Scanning: {d.path}")
                lines.append("  (directory not found)" if not d.exists else
                             f"  Found {d.font_count} font file(s)")
                lines.append("")
            lines.append(f"Total: {len(report.files)} font file(s), "
                         f"{len(report.families)} usable family(ies)")
            report_view.get_buffer().set_text("\n".join(lines))

        rescan_btn.connect("clicked", lambda _b: refresh(True))

        def choose_extra(_b):
            self.on_extra_font_folder_clicked(None, parent=dialog)
            refresh(True)
        extra_btn.connect("clicked", choose_extra)

        content.show_all()
        refresh(False)
        dialog.run()
        dialog.destroy()

    def on_print_dialog_clicked(self, widget):
        """Print...: the printer for this session and the label's print
        settings in one window, then the label itself, from its Print button.

        The printer is for this session only, and never reaches the persisted
        default. Everything else is saved with the label, so a visit that
        changes any of it is one undo entry. A visit that changes none of it
        records nothing and leaves the file as it was saved.
        """
        document = self.design_canvas.document
        result = _print_dialog(
            self, document,
            (self.printer_address, self.printer_port, self.printer_dpi,
             self.printer_font_device),
            self._default_printer)
        if result is None:
            return
        action, (address, port, dpi, font_device), settings = result
        moved = (address, port) != (self.printer_address, self.printer_port)
        old_dpi = self.printer_dpi
        self.printer_address, self.printer_port, self.printer_dpi = address, port, dpi
        self._set_font_device(font_device)
        # Settled before the settings go on, against the design the user was
        # looking at, as Label Settings settles it.
        note = None
        if dpi != old_dpi:
            note = self._offer_dpi_rescale()
            self.design_canvas.sync_size()
        changed = settings.apply(document)
        if note or changed:
            self.on_canvas_changed()      # one history entry for the whole visit
        if note:
            self.update_status(note[0].upper() + note[1:])
        elif moved:
            self.update_status(f"Printing to {address}:{port} for this session")
        elif changed:
            self.update_status("Print settings changed")
        if action == 'print':
            self.on_print_clicked(None)

    def on_label_settings_clicked(self, widget):
        """Handle label settings menu item click."""
        dialog = Gtk.Dialog(title="Label Settings", parent=self, flags=0)
        dialog.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                          Gtk.STOCK_OK, Gtk.ResponseType.OK)
        
        content = dialog.get_content_area()
        content.set_spacing(10)
        
        # Preset sizes
        presets_label = Gtk.Label(label="Preset Sizes:")
        presets_label.set_halign(Gtk.Align.START)
        content.pack_start(presets_label, False, False, 0)
        
        presets_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        content.pack_start(presets_box, False, False, 0)
        
        # Physical sizes; dots depend on the printer's resolution
        preset_sizes = {
            "4x6": (4, 6),
            "5x7": (5, 7),
            "6x4": (6, 4),
            "3x5": (3, 5),
            "2x3": (2, 3),
        }

        def on_preset_clicked(btn, w_in, h_in):
            width_spin.set_value(w_in)
            height_spin.set_value(h_in)

        for label_text, (w_in, h_in) in preset_sizes.items():
            btn = Gtk.Button(label=label_text)
            btn.connect("clicked", on_preset_clicked, w_in, h_in)
            presets_box.pack_start(btn, False, False, 0)
        
        # Custom sizes
        custom_label = Gtk.Label(label="Custom Size (inches):")
        custom_label.set_halign(Gtk.Align.START)
        content.pack_start(custom_label, False, False, 0)
        
        # Width
        width_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        content.pack_start(width_box, False, False, 0)
        
        width_label = Gtk.Label(label="Width:")
        width_label.set_size_request(80, -1)
        width_box.pack_start(width_label, False, False, 0)
        
        width_spin = Gtk.SpinButton()
        width_adj = Gtk.Adjustment(value=self.label_width / self.printer_dpi, lower=0.5, upper=25,
                                   step_increment=0.1)
        width_spin.set_adjustment(width_adj)
        width_spin.set_numeric(True)
        width_spin.set_digits(2)
        width_box.pack_start(width_spin, True, True, 0)
        
        # Height
        height_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        content.pack_start(height_box, False, False, 0)
        
        height_label = Gtk.Label(label="Height:")
        height_label.set_size_request(80, -1)
        height_box.pack_start(height_label, False, False, 0)
        
        height_spin = Gtk.SpinButton()
        height_adj = Gtk.Adjustment(value=self.label_height / self.printer_dpi, lower=0.5, upper=25,
                                    step_increment=0.1)
        height_spin.set_adjustment(height_adj)
        height_spin.set_numeric(True)
        height_spin.set_digits(2)
        height_box.pack_start(height_spin, True, True, 0)

        # Resolution. The inches above only mean a number of dots once this is
        # fixed, so it belongs beside them rather than a dialog away.
        dpi_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        content.pack_start(dpi_box, False, False, 0)

        dpi_label = Gtk.Label(label="DPI:")
        dpi_label.set_size_request(80, -1)
        dpi_box.pack_start(dpi_label, False, False, 0)

        dpi_combo = _dpi_combo(self.printer_dpi)
        dpi_box.pack_start(dpi_combo, True, True, 0)

        # ^LH: the origin every field is placed from. Its use is preprinted
        # stock - moving the printable area below a pre-printed header - so it
        # belongs beside the size rather than among the printer settings.
        transform = self.design_canvas.document.transform
        home_x_spin = _make_spin(transform.home[0], 0, 32000)
        _make_row(content, "Home X (dots):", home_x_spin)
        home_y_spin = _make_spin(transform.home[1], 0, 32000)
        _make_row(content, "Home Y (dots):", home_y_spin)

        # Info label
        info_label = Gtk.Label()

        def chosen_dpi():
            return _dpi_from(dpi_combo, self.printer_dpi)

        def to_dots():
            # Not self.inches_to_dots: that reads self.printer_dpi, which is
            # still the old resolution until the dialog is accepted. The
            # inches are the physical size the user asked for, so changing the
            # resolution recomputes the dots rather than the other way round.
            resolution = chosen_dpi()
            return (max(1, int(round(width_spin.get_value() * resolution))),
                    max(1, int(round(height_spin.get_value() * resolution))))

        def update_hint(*_a):
            w, h = to_dots()
            info_label.set_text(
                f"{w} x {h} dots at {chosen_dpi()} dpi "
                f"(^PW{w} / ^LL{h}), saved with the file.")

        width_spin.connect("value-changed", update_hint)
        height_spin.connect("value-changed", update_hint)
        dpi_combo.connect("changed", update_hint)
        update_hint()
        info_label.set_halign(Gtk.Align.START)
        info_label.set_line_wrap(True)
        content.pack_start(info_label, False, False, 0)
        
        content.show_all()
        
        response = dialog.run()
        if response != Gtk.ResponseType.OK:
            dialog.destroy()
            return
        new_width, new_height = to_dots()
        new_dpi = chosen_dpi()
        w_in, h_in = width_spin.get_value(), height_spin.get_value()
        # Destroyed before anything modal can be raised over it, as the printer
        # dialog does before its own rescale prompt.
        # Copied, not mutated: the document's own transform is what an undo
        # snapshot may still be holding. ^PO, ^PM and ^LR, which it also
        # holds, are the Print window's, and pass through.
        chosen_transform = transform.copy()
        chosen_transform.home = (int(home_x_spin.get_value()),
                                 int(home_y_spin.get_value()))
        dialog.destroy()
        self.apply_label_settings(new_width, new_height, new_dpi, w_in, h_in,
                                  chosen_transform)

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
        # Only the DPI slot of the default moves - address/port stay whatever
        # the persisted default already was, so a session override on those
        # (the Print window) survives a Label Settings visit untouched.
        self._default_printer = (self._default_printer[0],
                                 self._default_printer[1], dpi,
                                 self._default_printer[3])
        self.label_inches = (w_in, h_in)
        if transform is not None:
            self.design_canvas.document.transform = transform
        # Written before the prompt, as the printer dialog writes its own: the
        # prompt is modal and can be dismissed by the window manager, and the
        # choice the user already made should be on disk by then.
        self._save_settings()

        note = self._offer_dpi_rescale() if dpi != old_dpi else None

        # Shrinking clamps elements to the new bounds, which is itself part of
        # the change being recorded. Last, so the size typed in the dialog wins
        # over the one a rescale just moved - and the window's own copy of the
        # size has to follow the document's, which a rescale moved underneath it.
        self.label_width, self.label_height = width, height
        self.design_canvas.set_label_size(width, height)
        self.on_canvas_changed()      # one history entry for the whole visit
        message = f"Label size set to {width}x{height}"
        self.update_status(f"{message} - {note}" if note else message)
    
    UNDO_LIMIT = 50

    def on_canvas_changed(self):
        """A change made anywhere but the Properties panel: record it, and
        have the panel read the document again."""
        self.properties.document_changed()
        self._record_change()

    def record_panel_edit(self):
        """The Properties panel's burst of edits, as one undo entry."""
        self._record_change()

    def revert_to_last_entry(self):
        """Put the document back as the last undo entry left it - Escape in
        the Properties panel, throwing its burst of edits away.

        The selection stays what it is now, not what it was when that entry
        was made: the edits were to the elements still being looked at. A
        burst never adds or removes an element, so their places are the same
        in the restored list.
        """
        doc = self.design_canvas.document
        places = [doc.elements.index(el) for el in doc.selection
                  if el in doc.elements]
        self.design_canvas.restore(self._current_snapshot)
        doc = self.design_canvas.document
        doc.selection = [doc.elements[i] for i in places if i < len(doc.elements)]
        self.design_canvas.sync_size()
        self.design_canvas.queue_draw()

    def _record_change(self):
        """Record one undo entry and mark the document unsaved."""
        self.unsaved_changes = True
        # the snapshot standing before this change is what undo goes back to
        self._undo_stack.append(self._current_snapshot)
        del self._undo_stack[:-self.UNDO_LIMIT]
        self._redo_stack.clear()
        self._current_snapshot = self.design_canvas.snapshot()
        self._update_undo_actions()

    def on_undo(self, widget=None):
        """Step back to the state before the last change."""
        # a burst in the Properties panel, or a nudge still held down, is a
        # change of its own, recorded first
        self.properties.finish_edit()
        self.design_canvas.finish_nudge()
        if not self._undo_stack:
            return
        self._redo_stack.append(self._current_snapshot)
        self._current_snapshot = self._undo_stack.pop()
        self._apply_snapshot(self._current_snapshot)
        self.update_status("Undo")

    def on_redo(self, widget=None):
        """Step forward again after an undo."""
        # a burst in the Properties panel, or a nudge still held down, is a
        # change of its own, recorded first
        self.properties.finish_edit()
        self.design_canvas.finish_nudge()
        if not self._redo_stack:
            return
        self._undo_stack.append(self._current_snapshot)
        self._current_snapshot = self._redo_stack.pop()
        self._apply_snapshot(self._current_snapshot)
        self.update_status("Redo")

    def _apply_snapshot(self, snapshot):
        """Put the canvas back to `snapshot` and follow it with the label size."""
        self._close_element_editors()
        self.design_canvas.restore(snapshot)
        self.label_width = self.design_canvas.label_width
        self.label_height = self.design_canvas.label_height
        self.design_canvas.sync_size()
        self.unsaved_changes = True
        self._update_undo_actions()

    def _reset_history(self):
        """Start a fresh history, so it never spans a file load."""
        self._close_element_editors()
        self.properties.document_changed()
        self._undo_stack = []
        self._redo_stack = []
        self._current_snapshot = self.design_canvas.snapshot()
        self._update_undo_actions()

    def _update_undo_actions(self):
        """Enable the undo and redo controls only when they would do something."""
        can_undo = bool(self._undo_stack)
        can_redo = bool(self._redo_stack)
        for widget in (self.undo_item, self.undo_button):
            widget.set_sensitive(can_undo)
        for widget in (self.redo_item, self.redo_button):
            widget.set_sensitive(can_redo)

    def _update_align_items(self):
        """The align commands need something selected to line up."""
        selected = bool(self.design_canvas.document.selection)
        for item in self.align_items:
            item.set_sensitive(selected)

    def _release_align_items(self):
        for item in self.align_items:
            item.set_sensitive(True)

    def _release_edit_menu(self, menu):
        """Make sensitive again, once the menu has closed, everything its
        enable rules greyed out.

        The rules are for reading an open menu. An insensitive item also
        swallows its accelerator, and the selection goes on changing after
        the menu closes: a menu last opened with nothing selected would leave
        Ctrl+C dead on an element picked afterwards. Every handler already
        does nothing when there is nothing to do, which is all a shortcut
        owes the user (FUNCTIONAL_SPEC.md section 14).
        """
        for item in (self.cut_item, self.copy_item, self.paste_item,
                     self.duplicate_item, self.delete_item,
                     self.select_all_item, self.deselect_all_item,
                     self.invert_selection_item, self.group_item,
                     self.ungroup_item, self.remove_from_group_item,
                     *self.zorder_items, self.properties_item):
            item.set_sensitive(True)
        self._release_align_items()

    def _update_edit_menu(self, menu):
        """Grey out the actions that need a selected element."""
        doc = self.design_canvas.document
        for item in (self.cut_item, self.copy_item, self.duplicate_item):
            item.set_sensitive(bool(doc.selection))
        # Any text at all: whether it is ZPL is the paste's to find out, and
        # fetching a clipboard that may hold an image's worth of hex every
        # time the menu opens is not worth an earlier answer.
        self.paste_item.set_sensitive(
            Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).wait_is_text_available())
        self.delete_item.set_sensitive(doc.selected_element is not None)
        self.select_all_item.set_sensitive(len(doc.selection) < len(doc.elements))
        self.deselect_all_item.set_sensitive(bool(doc.selection))
        self.invert_selection_item.set_sensitive(bool(doc.elements))
        self.group_item.set_sensitive(doc.can_group())
        self.ungroup_item.set_sensitive(doc.can_ungroup())
        self.remove_from_group_item.set_sensitive(doc.can_remove_from_group())
        self._update_align_items()
        # From the model, as the Qt window does: a group is one depth, and
        # only the model knows where the run holding the primary ends.
        front, forward, backward, back = self.zorder_items
        for item in (front, forward):
            item.set_sensitive(doc.can_raise())
        for item in (backward, back):
            item.set_sensitive(doc.can_lower())
        self.properties_item.set_sensitive(doc.selected_element is not None)

    def _build_align_menu(self) -> Gtk.Menu:
        """One copy of the align commands, for a menu or a popup."""
        menu = Gtk.Menu()
        for edge, label in ALIGN_ITEMS:
            item = Gtk.MenuItem.new_with_mnemonic(label)
            item.connect("activate", self.on_align_clicked, edge)
            menu.append(item)
            self.align_items.append(item)
        menu.show_all()
        self._align_menus.append(menu)
        return menu

    def on_align_clicked(self, _widget, edge: str):
        self.design_canvas.align_selected(edge)

    def on_add_text_clicked(self, widget, at=None):
        """Handle add text element button click."""
        self.design_canvas.add_text_element("New Text", at=at)

    def on_add_time_clicked(self, widget, at=None):
        """Handle add time element button click."""
        self.design_canvas.add_time_element(at=at)

    def on_add_serial_clicked(self, widget, at=None):
        """Handle add serial element button click."""
        self.design_canvas.add_serial_element(at=at)

    def on_add_numbered_clicked(self, widget, at=None):
        """Handle add numbered element button click."""
        self.design_canvas.add_numbered_element(at=at)

    def on_add_frame_clicked(self, widget, at=None):
        """Handle add frame element button click."""
        self.design_canvas.add_frame_element(at=at)

    def on_add_circle_clicked(self, widget, at=None):
        """Handle add circle element button click."""
        self.design_canvas.add_circle_element(at=at)

    def on_add_ellipse_clicked(self, widget, at=None):
        """Handle add ellipse element button click."""
        self.design_canvas.add_ellipse_element(at=at)

    def on_add_diagonal_clicked(self, widget, at=None):
        """Handle add diagonal line element button click."""
        self.design_canvas.add_diagonal_element(at=at)
    
    def on_add_barcode_clicked(self, widget, at=None):
        """Handle add barcode element button click."""
        self.design_canvas.add_barcode_element(at=at)
    
    def on_add_image_clicked(self, widget, at=None):
        """Handle add image element button click."""
        dialog = Gtk.FileChooserDialog(
            title="Load Image",
            parent=self,
            action=Gtk.FileChooserAction.OPEN
        )
        dialog.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                           Gtk.STOCK_OPEN, Gtk.ResponseType.OK)

        filter_img = Gtk.FileFilter()
        filter_img.set_name("Image files (*.jpg, *.jpeg, *.png)")
        for pat in ("*.jpg", "*.jpeg", "*.JPG", "*.JPEG", "*.png", "*.PNG"):
            filter_img.add_pattern(pat)
        dialog.add_filter(filter_img)

        filter_all = Gtk.FileFilter()
        filter_all.set_name("All files")
        filter_all.add_pattern("*")
        dialog.add_filter(filter_all)

        response = dialog.run()
        if response == Gtk.ResponseType.OK:
            filepath = dialog.get_filename()
            dialog.destroy()
            self.design_canvas.add_image_element(filepath, at=at)
        else:
            dialog.destroy()

    def on_add_stored_graphic_clicked(self, widget, at=None):
        """Handle add stored graphic element button click."""
        self.design_canvas.add_stored_graphic_element(at=at)

    def _on_palette_drag_begin(self, button, context, kind):
        """Drag the element itself, as the canvas will draw it once dropped.

        Except an image, which has no file until the drop asks for one: a
        blank placeholder box says less than the button, so that drags the
        button's own picture, held where it was picked up.
        """
        if kind != 'image':
            Gtk.drag_set_icon_surface(context, self.design_canvas.drag_picture(kind))
            return
        allocation = button.get_allocation()
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32,
                                     allocation.width, allocation.height)
        button.draw(cairo.Context(surface))
        # A button has no window of its own: the pointer comes back in its
        # parent's, which the allocation is measured in too.
        _window, x, y, _mask = button.get_window().get_device_position(
            context.get_device())
        surface.set_device_offset(allocation.x - x, allocation.y - y)
        Gtk.drag_set_icon_surface(context, surface)

    def _on_palette_drag_data_get(self, _button, _context, data, _info, _time, kind):
        data.set(data.get_target(), 8, kind.encode())

    def on_element_dropped(self, _canvas, kind, x, y):
        """A palette button let go over the canvas: add its element there.

        Once the drag has finished rather than inside it, so + Image's file
        chooser is not run while the drop is still being delivered.
        """
        self.properties.finish_edit()

        def add():
            self._palette[kind](None, at=(x, y))
            return False
        GLib.idle_add(add)

    def _build_symbol_menu(self) -> Gtk.Menu:
        """The "+ Symbol" popup: one item per ^GS symbol, each with its
        picture beside its name."""
        menu = Gtk.Menu()
        for code, _shown, name in graphic_symbols.SYMBOLS:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            icon = to_pixbuf(graphic_symbols.icon(code))
            if icon is not None:
                row.pack_start(Gtk.Image.new_from_pixbuf(icon), False, False, 0)
            row.pack_start(Gtk.Label(label=name, xalign=0), True, True, 0)
            item = Gtk.MenuItem()
            item.add(row)
            item.connect("activate", self.on_add_graphic_symbol_clicked, code)
            menu.append(item)
        menu.show_all()
        return menu

    def on_add_graphic_symbol_clicked(self, _widget, code: str):
        """Handle one of the "+ Symbol" popup's items."""
        self.design_canvas.add_graphic_symbol_element(code)

    # Selection commands change the selection and never the document, so
    # they repaint and record nothing - the same as a click or a band.

    def on_select_all_clicked(self, widget):
        if self.design_canvas.document.select_all():
            self.design_canvas.queue_draw()

    def on_deselect_all_clicked(self, widget):
        document = self.design_canvas.document
        if document.selection:
            document.clear_selection()
            self.design_canvas.queue_draw()

    def on_invert_selection_clicked(self, widget):
        if self.design_canvas.document.invert_selection():
            self.design_canvas.queue_draw()

    def on_group_clicked(self, widget):
        self.design_canvas.group_selected()

    def on_ungroup_clicked(self, widget):
        self.design_canvas.ungroup_selected()

    def on_remove_from_group_clicked(self, widget):
        self.design_canvas.remove_from_group()

    def on_delete_clicked(self, widget):
        """Handle delete selected element button click."""
        # Every selected element goes, so every editor open on one has to be
        # closed - an editor must never outlive the element it is editing.
        doomed = list(self.design_canvas.document.selection)
        self.design_canvas.remove_selected()
        for element in doomed:
            self._close_editor_for(element)

    # The clipboard holds ZPL (Document.copy_zpl), so what one window copies
    # another - this frontend's or the Qt one's - can paste.

    def _set_clipboard(self, text):
        clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        clipboard.set_text(text, -1)
        # Handed to the clipboard manager, if there is one, so a copy outlives
        # this window - as Qt's does on its own.
        clipboard.store()

    def on_copy_clicked(self, widget):
        """Copy changes the clipboard and never the document: no undo entry."""
        document = self.design_canvas.document
        text = document.copy_zpl()
        if not text:
            return
        self._set_clipboard(text)
        self.update_status(f"Copied {workflow.elements_phrase(len(document.selection))}")

    def on_cut_clicked(self, widget):
        """Copy, then Delete - which is the one undo entry."""
        document = self.design_canvas.document
        count = len(document.selection)
        text = document.copy_zpl()
        if not text:
            return
        self._set_clipboard(text)
        self.on_delete_clicked(widget)
        self.update_status(f"Cut {workflow.elements_phrase(count)}")

    def on_paste_clicked(self, widget):
        text = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).wait_for_text() or ''
        try:
            message = workflow.paste_zpl(self.design_canvas.document, text,
                                         self.renderer)
        except Exception as e:
            self.show_error_dialog(f"Failed to paste: {e}")
            return
        if message is None:
            self.update_status(workflow.NOTHING_TO_PASTE)
            return
        self.design_canvas.queue_draw()
        self.on_canvas_changed()
        self.update_status(message)

    def on_duplicate_clicked(self, widget):
        count = self.design_canvas.duplicate_selected()
        if count:
            self.update_status(f"Duplicated {workflow.elements_phrase(count)}")

    def _on_edit_requested(self, _canvas, command: str):
        """A command chosen from the canvas's right-click menu."""
        {'cut': self.on_cut_clicked, 'copy': self.on_copy_clicked,
         'duplicate': self.on_duplicate_clicked,
         'delete': self.on_delete_clicked,
         'properties': self.on_properties_clicked}[command](None)

    def on_properties_clicked(self, widget):
        """Show the Properties panel on the selection - floating and hidden,
        this is how it comes back."""
        if self.design_canvas.document.selection:
            self.properties.request()

    def on_canvas_draw(self, widget, context):
        """Canvas draw event handler - re-render when canvas changes."""
        # This is called when the canvas is drawn, we can use it to trigger re-rendering
        # Actually, we'll trigger on button releases and element additions
        pass
    
    def _open_editor(self, element, dialog, on_response):
        """Show an element editor as a non-modal child of the designer.

        Transient for the designer so it floats above it and closes with it,
        but never blocking it - which rules out Gtk.Dialog.run(), whose
        recursive main loop is what made these editors modal.
        """
        dialog.set_transient_for(self)
        dialog.set_destroy_with_parent(True)
        dialog.set_modal(False)
        _make_dialog_scrollable(dialog, self)
        dialog.connect("response", on_response)
        key = id(element)
        self._editors[key] = dialog
        dialog.connect("destroy", lambda *_a: self._editors.pop(key, None))
        dialog.show_all()

    def _close_editor_for(self, element):
        """Close the editor open on one element, if there is one."""
        editor = self._editors.pop(id(element), None) if element else None
        if editor is not None:
            editor.destroy()

    def _close_element_editors(self):
        """Close every open editor.

        Undo, redo and loading a file all replace the element objects the open
        editors hold, so an editor left up would write its fields into an
        element the document no longer has - the edit would vanish with no
        error to show for it.
        """
        for editor in list(self._editors.values()):
            editor.destroy()
        self._editors.clear()

    def on_element_double_clicked(self, widget, element):
        """A double click asks for the Properties panel, which the first
        click has already pointed at the element."""
        self.properties.request()

    def replace_image(self, element):
        """Choose a new file for an image, as one undo entry - the Properties
        panel's Replace Image."""
        dialog = Gtk.FileChooserDialog(
            title="Replace Image",
            parent=self,
            action=Gtk.FileChooserAction.OPEN
        )
        dialog.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                           Gtk.STOCK_OPEN, Gtk.ResponseType.OK)

        filter_img = Gtk.FileFilter()
        filter_img.set_name("Image files (*.jpg, *.jpeg, *.png)")
        for pat in ("*.jpg", "*.jpeg", "*.JPG", "*.JPEG", "*.png", "*.PNG"):
            filter_img.add_pattern(pat)
        dialog.add_filter(filter_img)

        filter_all = Gtk.FileFilter()
        filter_all.set_name("All files")
        filter_all.add_pattern("*")
        dialog.add_filter(filter_all)

        response = dialog.run()
        if response == Gtk.ResponseType.OK:
            element.image_path = dialog.get_filename()
            dialog.destroy()
            element.reload()
            self.design_canvas.queue_draw()
            self.on_canvas_changed()
        else:
            dialog.destroy()

    def open_editor_window(self, element):
        """An element's rows in a window of their own, applying on OK - the
        editor the designer opened before the Properties panel. Nothing in
        the designer opens one any more; it is kept for driving a form whole,
        OK and all, and still one per element. Returns the window, or None
        for an element with no rows.
        """
        open_editor = self._editors.get(id(element))
        if open_editor is not None:
            open_editor.present()
            return open_editor

        builder = gtk_editors.form_builder(element)
        if builder is None:
            return None
        dialog = Gtk.Dialog(parent=self, flags=0)
        dialog.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                           Gtk.STOCK_OK, Gtk.ResponseType.OK)
        form = builder(self, dialog.get_content_area(), element)
        dialog.set_title(form.title)

        def fit():
            # A dialog GTK already grew to fit more rows does not shrink
            # back on its own just because some of them hid. Resize to the
            # size it now wants, not to 1x1: the body scrolls, so its
            # minimum is a sliver and 1x1 would stay that small.
            natural = dialog.get_preferred_size()[1]
            dialog.resize(natural.width, natural.height)

        form.fit = fit

        def on_response(_dialog, response):
            if response == Gtk.ResponseType.OK:
                form.apply()
                self.design_canvas.queue_draw()
                self.on_canvas_changed()
            _dialog.destroy()

        self._open_editor(element, dialog, on_response)
        # _open_editor's own show_all() would otherwise re-show every row the
        # form hid - so the rows that depend on another get their first
        # visibility pass only once it has run.
        form.after_show()
        return dialog

    def _update_title(self):
        """Name the file being edited in the titlebar."""
        title = workflow.window_title(self.current_filepath)
        self.set_title(title)
        self.header_bar.set_title(title)

    def update_status(self, message: str):
        """Update status bar message."""
        self.status_bar.push(self.status_bar.get_context_id("main"), message)
    
    def show_error_dialog(self, message: str):
        """Show an error dialog."""
        dialog = Gtk.MessageDialog(
            parent=self,
            flags=0,
            message_type=Gtk.MessageType.ERROR,
            buttons=Gtk.ButtonsType.OK,
            text="Error"
        )
        dialog.format_secondary_text(message)
        dialog.run()
        dialog.destroy()

    def show_help_dialog(self, parent, title: str, text: str):
        """A window's Help: `text` is plain, shared with the Qt frontend."""
        dialog = Gtk.MessageDialog(
            parent=parent, flags=0, message_type=Gtk.MessageType.INFO,
            buttons=Gtk.ButtonsType.OK, text=title)
        dialog.set_title(f"{title} Help")
        dialog.format_secondary_text(text)
        dialog.run()
        dialog.destroy()


def main(filepath=None, fields=None):
    """Main entry point for the application."""
    app = ZPLViewerWindow()
    app.connect('destroy', Gtk.main_quit)
    if filepath:
        app.load_zpl_file(filepath)
        if fields:
            workflow.apply_field_data(app.design_canvas.document, fields)
            app.design_canvas.queue_draw()
            app._reset_history()
    # Ask for the front. Started from an editor running full screen, a new
    # window can otherwise map behind it and look as though nothing happened.
    app.present()
    Gtk.main()


if __name__ == '__main__':
    main()
