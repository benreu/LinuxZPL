#!/usr/bin/env python3
"""
ZPL Viewer - Gtk Application to Display ZPL Output

A simple GTK3 application for viewing rendered ZPL (Zebra Programming Language) output.
"""

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, Gdk, GdkPixbuf, GLib
import os
import base64
import configparser
import socket
from pathlib import Path
from zplcore import fonts as zpl_fonts
from zplcore import model
from zplcore import parser as zpl_parser
from zplcore import view as zpl_view
from zplcore import workflow
from zplcore import textraster
from zplcore.model import (FRAME_COLOURS, ORIENTATIONS, TEXT_JUSTIFICATIONS,
                           BarcodeElement, Document, FieldBlock, FrameElement,
                           ImageElement, TextElement)
from zplcore.renderer import ZPLRenderer

from .canvas import DesignCanvas
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


def _make_row(content, label_text, widget, label_width: int = 130):
    """One labelled row in a dialog's content area."""
    row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    label = Gtk.Label(label=label_text)
    label.set_size_request(label_width, -1)
    label.set_halign(Gtk.Align.END)
    row.pack_start(label, False, False, 0)
    row.pack_start(widget, True, True, 0)
    content.pack_start(row, False, False, 0)


def _make_spin(value, lower, upper):
    """A whole-number spin button over a range."""
    spin = Gtk.SpinButton()
    spin.set_adjustment(Gtk.Adjustment(value=value, lower=lower,
                                       upper=upper, step_increment=1))
    spin.set_numeric(True)
    return spin


def _make_combo(choices, current):
    """A combo over (label, code) choices, plus the codes to read it back."""
    combo = Gtk.ComboBoxText()
    for label_text, _code in choices:
        combo.append_text(label_text)
    codes = [code for _l, code in choices]
    combo.set_active(codes.index(current) if current in codes else 0)
    return combo, codes


def _dpi_combo(dpi: int) -> Gtk.ComboBoxText:
    """The resolution choice, offered the same way wherever it is edited.

    Label Settings and Printer Settings both write the one printer_dpi setting,
    so they have to offer the same list - including the fallback: a resolution
    the designer does not support can still be the one in force, and must be
    shown rather than silently replaced with a supported one.
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


def _config_path() -> Path:
    """Path to the persisted settings file."""
    return Path(GLib.get_user_config_dir()) / 'linuxzpl' / 'settings.ini'


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
        # The size last chosen in Label Settings, also persisted. Held in
        # inches because the resolution it converts with is itself a setting
        # that can change between sessions.
        self.label_inches = DEFAULT_LABEL_INCHES
        self.saved_geometry = None
        self._load_settings()
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
                ("_Print", self.on_print_clicked, "<Control>p"),
                (None, None, None),
                ("_Quit", self.close_app, "<Control>q")):
            if label is None:
                file_menu.append(Gtk.SeparatorMenuItem())
                continue
            item = Gtk.MenuItem.new_with_mnemonic(label)
            item.connect("activate", action)
            add_accel(item, accel)
            file_menu.append(item)

        file_menu.show_all()

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

        self.delete_item = Gtk.MenuItem.new_with_mnemonic("_Delete")
        self.delete_item.connect("activate", self.on_delete_clicked)
        add_accel(self.delete_item, "Delete")
        edit_menu.append(self.delete_item)

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

        view_menu.show_all()

        # Undo/redo buttons at the far end of the header bar. pack_end fills
        # right to left, so redo goes in first to read undo then redo.
        button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        button_box.get_style_context().add_class("linked")
        header.pack_end(button_box)

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

        # Printer settings menu item
        printer_settings_item = Gtk.MenuItem(label="Printer Settings\u2026")
        printer_settings_item.connect("activate", self.on_printer_settings_clicked)
        settings_menu.append(printer_settings_item)

        # Printer fonts menu item
        printer_fonts_item = Gtk.MenuItem(label="Printer Fonts\u2026")
        printer_fonts_item.connect("activate", self.on_printer_fonts_clicked)
        settings_menu.append(printer_fonts_item)

        settings_menu.show_all()
        
        # Content box with padding
        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        content_box.set_margin_top(10)
        content_box.set_margin_bottom(10)
        content_box.set_margin_start(10)
        content_box.set_margin_end(10)
        main_box.pack_start(content_box, True, True, 0)
        
        # Left side: Designer canvas
        left_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        content_box.pack_start(left_box, True, True, 0)
        
        # Designer toolbar
        toolbar_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        left_box.pack_start(toolbar_box, False, False, 0)
        
        # Add text button
        add_text_btn = Gtk.Button(label="+ Text")
        add_text_btn.connect("clicked", self.on_add_text_clicked)
        toolbar_box.pack_start(add_text_btn, False, False, 0)
        
        # Add frame button
        add_frame_btn = Gtk.Button(label="+ Frame")
        add_frame_btn.connect("clicked", self.on_add_frame_clicked)
        toolbar_box.pack_start(add_frame_btn, False, False, 0)
        
        # Add barcode button
        add_barcode_btn = Gtk.Button(label="+ Barcode")
        add_barcode_btn.connect("clicked", self.on_add_barcode_clicked)
        toolbar_box.pack_start(add_barcode_btn, False, False, 0)

        # Add image button
        add_image_btn = Gtk.Button(label="+ Image")
        add_image_btn.connect("clicked", self.on_add_image_clicked)
        toolbar_box.pack_start(add_image_btn, False, False, 0)

        # Zoom controls
        zoom_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        zoom_box.get_style_context().add_class("linked")
        toolbar_box.pack_start(zoom_box, False, False, 10)
        for label, action, tip in (("\u2212", self.on_zoom_out, "Zoom out (Ctrl+-)"),
                                   ("Fit", self.on_fit_label, "Fit the label (Ctrl+0)"),
                                   ("+", self.on_zoom_in, "Zoom in (Ctrl++)")):
            button = Gtk.Button(label=label)
            button.set_tooltip_text(tip)
            button.connect("clicked", action)
            zoom_box.pack_start(button, False, False, 0)

        # One button opening the same six commands the Edit menu holds, rather
        # than six buttons: the toolbar is text-labelled, and there are no
        # object-align icons in the icon theme to label them with. GTK menu
        # items belong to one menu, so this is a second copy of the items - the
        # handlers and the enable rules are shared, and the conformance suite
        # diffs this menu against the Qt frontend's.
        align_button = Gtk.MenuButton(label="Align \u25be")
        align_button.set_tooltip_text("Line the selection up (Edit \u25b8 Align)")
        align_button.set_popup(self._build_align_menu())
        self.align_button = align_button
        toolbar_box.pack_start(align_button, False, False, 0)

        # Delete button
        delete_btn = Gtk.Button(label="Delete")
        delete_btn.connect("clicked", self.on_delete_clicked)
        toolbar_box.pack_end(delete_btn, False, False, 0)
        
        # Design canvas
        scrolled_canvas = Gtk.ScrolledWindow()
        scrolled_canvas.set_hexpand(True)
        scrolled_canvas.set_vexpand(True)
        # The vertical scrollbar is always present, so the width a fit is
        # measured against does not change when it appears - which would set
        # off a resize loop.
        scrolled_canvas.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.ALWAYS)
        scrolled_canvas.connect("size-allocate", self._on_view_allocated)
        self.scrolled_canvas = scrolled_canvas
        left_box.pack_start(scrolled_canvas, True, True, 0)
        
        self.design_canvas = DesignCanvas(on_change_callback=self.on_canvas_changed, 
                                         label_width=self.label_width, 
                                         label_height=self.label_height)
        self.design_canvas.dpi = self.printer_dpi
        # The element editors are non-modal, so more than one can be on screen
        # at once. One per element, keyed by id: an open editor holds its
        # element, so the id cannot be reused while it is registered here.
        self._editors = {}
        self.design_canvas.connect("draw", self.on_canvas_draw)
        self.design_canvas.connect("element-double-clicked", self.on_element_double_clicked)
        self.design_canvas.connect("scale-changed", self._update_zoom_readout)
        self.design_canvas.connect("zoom-at", self._zoom_at)

        # what is selected changes while the menu is closed. Connected here
        # rather than at build time: show_all() emits "show", and the handler
        # needs the canvas.
        self._edit_menu.connect("show", self._update_edit_menu)
        # The toolbar's copy can be opened without the Edit menu ever having
        # been shown, so every copy re-evaluates the rules on the way up.
        for menu in self._align_menus:
            menu.connect("show", lambda _m: self._update_align_items())

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
        main_box.pack_end(status_row, False, False, 0)

        self.show_all()
        self._update_zoom_readout()
        self.update_status("Ready")

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
        """Tell the canvas how much room it has, so a fit can follow it."""
        self.design_canvas.set_view_size(allocation.width, allocation.height)

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

    def main_window_closed(self, widget, event):
      if not self.close_app(widget):
        return True

    def close_app(self, widget):
      """Handle quit app from Menu or Window close button."""
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
        return True
      except Exception as e:
        self.show_error_dialog(f"Failed to save file: {e}")
        self.update_status("Save failed")
        return False
    
    def load_zpl_file(self, filepath: str):
        """Load a ZPL file and update the views."""
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                content = f.read()

            document, loaded_dpi = zpl_parser.parse_zpl(content, self.renderer)
            self.design_canvas.set_document(document)
            self.current_zpl_content = content
            self.current_filepath = filepath
            self._update_title()
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
            # Building elements while parsing does not count as an edit.
            self.unsaved_changes = False
            self._reset_history()
            workflow.warn_unsupported(content, self._warn_unsupported)

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

    def _confirm_printer_fonts(self) -> bool:
        """Check the label's fonts are on the printer. False cancels printing."""
        def ask(text, detail, uploadable):
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

        def progress(message):
            self.update_status(message)
            while Gtk.events_pending():
                Gtk.main_iteration()

        proceed, error = workflow.confirm_printer_fonts(
            self.design_canvas.document, self.printer_address,
            self.printer_port, ask, progress)
        if error:
            self.show_error_dialog(error)
        return proceed

    def on_print_clicked(self, widget):
        """Handle print button click."""
        if not self._confirm_printer_fonts():
            self.update_status("Printing cancelled")
            return
        printer_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        printer_socket.settimeout(10)
        try:
            printer_socket.connect((self.printer_address, self.printer_port))
        except OSError as e:
            self.show_error_dialog(str(e))
            return
        content = self.design_canvas.to_zpl()
        try:
            # sendall, not send: a label with an image runs to tens of
            # kilobytes, and send() may write only part of it.
            printer_socket.sendall(content.encode('utf-8'))
        except OSError as e:
            self.show_error_dialog(str(e))
            return
        finally:
            printer_socket.close()
        self.update_status(f"Sent to {self.printer_address}:{self.printer_port}")
    
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
        dialog.set_default_size(360, 280)

        content = dialog.get_content_area()
        content.set_spacing(8)
        content.set_margin_start(8)
        content.set_margin_end(8)
        content.set_margin_top(8)
        content.set_margin_bottom(8)

        status = Gtk.Label(halign=Gtk.Align.START)
        status.set_line_wrap(True)
        content.pack_start(status, False, False, 0)

        store = Gtk.ListStore(str)
        view = Gtk.TreeView(model=store)
        view.append_column(Gtk.TreeViewColumn("Font", Gtk.CellRendererText(), text=0))
        scroller = Gtk.ScrolledWindow()
        scroller.set_vexpand(True)
        scroller.add(view)
        content.pack_start(scroller, True, True, 0)

        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        upload_btn = Gtk.Button(label="Upload\u2026")
        delete_btn = Gtk.Button(label="Delete")
        refresh_btn = Gtk.Button(label="Refresh")
        for b in (upload_btn, delete_btn, refresh_btn):
            buttons.pack_start(b, False, False, 0)
        content.pack_start(buttons, False, False, 0)

        def refresh(*_a):
            store.clear()
            fonts = zpl_fonts.query_printer_fonts(self.printer_address, self.printer_port)
            if fonts is None:
                status.set_text(f"Could not reach the printer at "
                                f"{self.printer_address}:{self.printer_port}.")
                delete_btn.set_sensitive(False)
                return
            for name in sorted(fonts):
                store.append([zpl_fonts.printer_font_path(name)])
            delete_btn.set_sensitive(bool(fonts))
            status.set_text(f"{len(fonts)} font(s) on {self.printer_address}"
                            if fonts else "No fonts stored on the printer.")

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
            name = zpl_fonts.printer_font_name(path)
            status.set_text(f"Uploading {zpl_fonts.printer_font_path(name)}...")
            try:
                zpl_fonts.upload_font(self.printer_address, self.printer_port, path, name)
            except Exception as e:
                self.show_error_dialog(f"Font upload failed: {e}")
                return
            self.renderer.register_font(name, path)
            refresh()

        def on_delete(_b):
            model, treeiter = view.get_selection().get_selected()
            if treeiter is None:
                return
            shown = model[treeiter][0]
            name = Path(shown).stem.split(':')[-1]
            try:
                zpl_fonts.delete_printer_font(self.printer_address, self.printer_port, name)
            except Exception as e:
                self.show_error_dialog(f"Could not delete {shown}: {e}")
                return
            refresh()

        upload_btn.connect("clicked", on_upload)
        delete_btn.connect("clicked", on_delete)
        refresh_btn.connect("clicked", refresh)

        content.show_all()
        refresh()
        dialog.run()
        dialog.destroy()

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
            parser.read(_config_path())
            self.printer_address = parser.get(
                'printer', 'address', fallback=self.printer_address)
            self.printer_port = parser.getint(
                'printer', 'port', fallback=self.printer_port)
            dpi = parser.getint('printer', 'dpi', fallback=self.printer_dpi)
            if dpi > 0:
                self.printer_dpi = dpi
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
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, 'w') as f:
                parser.write(f)
        except (configparser.Error, OSError) as e:
            self.show_error_dialog(f"Could not save settings: {e}")

    def on_printer_settings_clicked(self, widget):
        """Handle printer settings menu item click."""
        dialog = Gtk.Dialog(title="Printer Settings", parent=self, flags=0)
        dialog.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                           Gtk.STOCK_OK, Gtk.ResponseType.OK)

        content = dialog.get_content_area()
        content.set_spacing(4)
        content.set_margin_start(8)
        content.set_margin_end(8)
        content.set_margin_top(8)
        content.set_margin_bottom(8)

        def make_row(lbl_text, widget):
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            lbl = Gtk.Label(label=lbl_text)
            lbl.set_size_request(90, -1)
            lbl.set_halign(Gtk.Align.END)
            row.pack_start(lbl, False, False, 0)
            row.pack_start(widget, True, True, 0)
            content.pack_start(row, False, False, 0)

        # Printer address
        address_entry = Gtk.Entry()
        address_entry.set_text(self.printer_address)
        make_row("Address:", address_entry)

        # Printer port
        port_spin = Gtk.SpinButton()
        port_adj = Gtk.Adjustment(value=self.printer_port, lower=1,
                                  upper=65535, step_increment=1)
        port_spin.set_adjustment(port_adj)
        port_spin.set_numeric(True)
        make_row("Port:", port_spin)

        # Printer resolution
        dpi_combo = _dpi_combo(self.printer_dpi)
        make_row("DPI:", dpi_combo)

        # Connection test, which also asks the printer its resolution
        result_label = Gtk.Label()
        result_label.set_halign(Gtk.Align.START)
        result_label.set_line_wrap(True)

        def set_result(colour, text):
            result_label.set_markup(
                f"<span foreground='{colour}'>"
                f"{GLib.markup_escape_text(text)}</span>")
            # both steps block, so let the label paint before the next one
            while Gtk.events_pending():
                Gtk.main_iteration()

        def on_test_clicked(btn):
            addr = address_entry.get_text().strip()
            port = int(port_spin.get_value())
            if not addr:
                set_result("red", "Address is required")
                return
            set_result("gray", f"Connecting to {addr}:{port}\u2026")
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5)
            try:
                sock.connect((addr, port))
            except OSError as e:
                set_result("red", f"\u2717 {e}")
                return
            finally:
                sock.close()

            connected = f"\u2713 Connected to {addr}:{port}"
            set_result("gray", f"{connected} \u2014 asking its resolution\u2026")
            dpi = zpl_fonts.query_printer_dpi(addr, port)
            if dpi is None:
                set_result("orange", f"{connected}, but it did not report its "
                                     f"resolution; set the DPI manually.")
            elif dpi in zpl_fonts.SUPPORTED_DPI:
                dpi_combo.set_active(list(zpl_fonts.SUPPORTED_DPI).index(dpi))
                set_result("green", f"{connected} \u2014 {dpi} dpi")
            else:
                set_result("orange", f"{connected} \u2014 reports {dpi} dpi, which "
                                     f"the designer does not support.")

        test_btn = Gtk.Button(label="Test Connection")
        test_btn.connect("clicked", on_test_clicked)
        content.pack_start(test_btn, False, False, 0)
        content.pack_start(result_label, False, False, 0)

        content.show_all()

        response = dialog.run()
        if response == Gtk.ResponseType.OK:
            new_address = address_entry.get_text().strip()
            new_port = int(port_spin.get_value())
            if not new_address:
                dialog.destroy()
                self.show_error_dialog("Printer address cannot be empty.")
                return
            self.printer_address = new_address
            self.printer_port = new_port
            old_dpi = self.printer_dpi
            self.printer_dpi = _dpi_from(dpi_combo, self.printer_dpi)
            self._save_settings()
            self.update_status(f"Printer set to {new_address}:{new_port}")
            if self.printer_dpi != old_dpi:
                # Pointing at a printer with a different head changes what the
                # open label measures - 1200 dots is 4in at 300 dpi and 5.9in
                # at 203. Re-stamping it and saying nothing would leave the
                # elements at the old scale and print the label oversized.
                dialog.destroy()
                note = self._offer_dpi_rescale()
                self.design_canvas.sync_size()
                if note:
                    self.on_canvas_changed()
                    self.update_status(note[0].upper() + note[1:])
                return
        dialog.destroy()

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
        dialog.destroy()
        self.apply_label_settings(new_width, new_height, new_dpi, w_in, h_in)

    def apply_label_settings(self, width, height, dpi, w_in, h_in):
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
        self.label_inches = (w_in, h_in)
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
        """Handle canvas changes (drag, resize, etc.) and mark as unsaved."""
        self.unsaved_changes = True
        # the snapshot standing before this change is what undo goes back to
        self._undo_stack.append(self._current_snapshot)
        del self._undo_stack[:-self.UNDO_LIMIT]
        self._redo_stack.clear()
        self._current_snapshot = self.design_canvas.snapshot()
        self._update_undo_actions()

    def on_undo(self, widget=None):
        """Step back to the state before the last change."""
        if not self._undo_stack:
            return
        self._redo_stack.append(self._current_snapshot)
        self._current_snapshot = self._undo_stack.pop()
        self._apply_snapshot(self._current_snapshot)
        self.update_status("Undo")

    def on_redo(self, widget=None):
        """Step forward again after an undo."""
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

    def _update_edit_menu(self, menu):
        """Grey out the actions that need a selected element."""
        element = self.design_canvas.selected_element
        self.delete_item.set_sensitive(element is not None)
        self._update_align_items()
        elements = self.design_canvas.elements
        idx = elements.index(element) if element in elements else None
        front, forward, backward, back = self.zorder_items
        for item in (front, forward):
            item.set_sensitive(idx is not None and idx < len(elements) - 1)
        for item in (backward, back):
            item.set_sensitive(idx is not None and idx > 0)
    
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

    def on_add_text_clicked(self, widget):
        """Handle add text element button click."""
        self.design_canvas.add_text_element("New Text")
    
    def on_add_frame_clicked(self, widget):
        """Handle add frame element button click."""
        self.design_canvas.add_frame_element()
    
    def on_add_barcode_clicked(self, widget):
        """Handle add barcode element button click."""
        self.design_canvas.add_barcode_element()
    
    def on_add_image_clicked(self, widget):
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
            self.design_canvas.add_image_element(filepath)
        else:
            dialog.destroy()

    def on_delete_clicked(self, widget):
        """Handle delete selected element button click."""
        # Every selected element goes, so every editor open on one has to be
        # closed - an editor must never outlive the element it is editing.
        doomed = list(self.design_canvas.document.selection)
        self.design_canvas.remove_selected()
        for element in doomed:
            self._close_editor_for(element)
    
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
        """Handle double-click on canvas element for editing."""
        open_editor = self._editors.get(id(element))
        if open_editor is not None:
            # Already being edited. Raising the window it is in beats opening a
            # second one onto the same element, where whichever was accepted
            # last would silently undo the other.
            open_editor.present()
            return

        if isinstance(element, TextElement):
            # Show text edit dialog
            dialog = Gtk.Dialog(title="Edit Text", parent=self, flags=0)
            dialog.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                              Gtk.STOCK_OK, Gtk.ResponseType.OK)

            content = dialog.get_content_area()
            content.set_spacing(4)
            content.set_margin_start(8)
            content.set_margin_end(8)
            content.set_margin_top(8)
            content.set_margin_bottom(8)

            def make_row(lbl_text, widget):
                _make_row(content, lbl_text, widget)

            # Text input. Multi-line, because ZPL's forced break is two
            # characters a user should never have to spell: Enter here becomes
            # \& on the way out.
            text_view = Gtk.TextView()
            text_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
            text_view.get_buffer().set_text(textraster.to_editor(element.text))
            text_scroll = Gtk.ScrolledWindow()
            text_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
            text_scroll.set_shadow_type(Gtk.ShadowType.IN)
            text_scroll.set_size_request(-1, 90)
            text_scroll.add(text_view)
            make_row("Text:", text_scroll)

            height_spin = _make_spin(element.font_height, 8, 500)
            make_row("Font Height:", height_spin)

            width_spin = _make_spin(element.font_width, 8, 500)
            make_row("Font Width:", width_spin)

            orientation_combo, orientation_codes = _make_combo(
                ORIENTATIONS, element.orientation)
            make_row("Orientation:", orientation_combo)

            # Font chooser (installed families only)
            selected_font = [element.font_path, element.font_family]

            font_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            font_lbl = Gtk.Label(label="Font:")
            font_lbl.set_size_request(130, -1)
            font_lbl.set_halign(Gtk.Align.END)
            font_row.pack_start(font_lbl, False, False, 0)

            def _font_display():
                if selected_font[1]:
                    return selected_font[1]
                canvas_family = self.design_canvas.font_family
                return f"Default ({canvas_family})" if canvas_family else "Default"

            font_name_lbl = Gtk.Label(label=_font_display())
            font_name_lbl.set_halign(Gtk.Align.START)
            font_name_lbl.set_ellipsize(3)  # PANGO_ELLIPSIZE_END
            font_row.pack_start(font_name_lbl, True, True, 0)

            choose_font_btn = Gtk.Button(label="Choose\u2026")
            font_row.pack_start(choose_font_btn, False, False, 0)

            clear_font_btn = Gtk.Button(label="Clear")
            font_row.pack_start(clear_font_btn, False, False, 0)

            content.pack_start(font_row, False, False, 0)

            def on_choose_font(btn):
                families = zpl_fonts.list_ttf_families()
                if not families:
                    self.show_error_dialog("No TrueType fonts were found on this system.")
                    return
                fdialog = Gtk.FontChooserDialog(title="Choose Font", parent=dialog)
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
                    self.show_error_dialog(f"No TrueType file found for '{family}'.")
                    return
                selected_font[0], selected_font[1] = path, family
                font_name_lbl.set_text(_font_display())

            def on_font_clear(btn):
                selected_font[0], selected_font[1] = None, None
                font_name_lbl.set_text(_font_display())

            choose_font_btn.connect("clicked", on_choose_font)
            clear_font_btn.connect("clicked", on_font_clear)

            # Wrapping (^FB)
            document = self.design_canvas.document
            block = element.block or element.default_block(document.font_path)

            wrap_check = Gtk.CheckButton(label="Wrap the text into a block")
            wrap_check.set_active(element.block is not None)
            make_row("Wrap:", wrap_check)

            block_width_spin = _make_spin(block.width, 10, 2000)
            make_row("Wrap Width:", block_width_spin)

            max_lines_spin = _make_spin(block.max_lines, 1, 64)
            make_row("Max Lines:", max_lines_spin)

            spacing_spin = _make_spin(block.line_spacing, -100, 100)
            make_row("Line Spacing:", spacing_spin)

            justify_combo, justify_codes = _make_combo(TEXT_JUSTIFICATIONS,
                                                       block.justification)
            make_row("Justification:", justify_combo)

            indent_spin = _make_spin(block.indent, 0, 2000)
            make_row("Indent:", indent_spin)

            block_fields = (block_width_spin, max_lines_spin, spacing_spin,
                            justify_combo, indent_spin)

            def on_wrap_toggled(btn):
                for field in block_fields:
                    field.set_sensitive(btn.get_active())

            on_wrap_toggled(wrap_check)
            wrap_check.connect("toggled", on_wrap_toggled)

            content.show_all()

            def on_response(_dialog, response):
                if response == Gtk.ResponseType.OK:
                    buffer = text_view.get_buffer()
                    element.text = textraster.from_editor(buffer.get_text(
                        buffer.get_start_iter(), buffer.get_end_iter(), False))
                    element.font_height = int(height_spin.get_value())
                    element.font_width = int(width_spin.get_value())
                    element.orientation = orientation_codes[
                        orientation_combo.get_active()]

                    if wrap_check.get_active():
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
                    elif textraster.FORCED_BREAK in element.text:
                        # A break typed into an element that never had a block
                        # still needs one, for the same reason. Sized to the
                        # longest line, so nothing moves.
                        element.block = element.default_block(document.font_path)

                    self.design_canvas.sync_text_width(element)

                    new_path, new_family = selected_font
                    if new_path != element.font_path:
                        if new_path:
                            # The font is only recorded here; it is uploaded at print
                            # time, so choosing a font never blocks on the network.
                            printer_name = zpl_fonts.printer_font_name(
                                new_path, taken=self._printer_font_names(exclude=element))
                            self.design_canvas.set_element_font(element, new_path, new_family, printer_name)
                            self.renderer.register_font(printer_name, new_path)
                        else:
                            element.font_path = None
                            element.font_family = None
                            element.printer_font_name = None
                            self.design_canvas.queue_draw()

                    self.on_canvas_changed()

                _dialog.destroy()

            self._open_editor(element, dialog, on_response)
        
        elif isinstance(element, BarcodeElement):
            # Show barcode edit dialog
            dialog = Gtk.Dialog(title="Edit Barcode", parent=self, flags=0)
            dialog.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                              Gtk.STOCK_OK, Gtk.ResponseType.OK)

            content = dialog.get_content_area()
            content.set_spacing(4)
            content.set_margin_start(8)
            content.set_margin_end(8)
            content.set_margin_top(8)
            content.set_margin_bottom(8)

            def make_row(label_text, widget):
                _make_row(content, label_text, widget)

            make_spin, make_combo = _make_spin, _make_combo

            value_entry = Gtk.Entry()
            value_entry.set_text(element.barcode_value)
            make_row("Barcode Value:", value_entry)

            height_spin = make_spin(element.bar_height, 20, 300)
            make_row("Bar Height:", height_spin)

            module_spin = make_spin(element.module_width, 1, 20)
            make_row("Module Width:", module_spin)

            orientation_combo, orientation_codes = make_combo(
                model.BARCODE_ORIENTATIONS,
                (element.orientation or 'N').upper())
            make_row("Orientation:", orientation_combo)

            text_combo, text_codes = make_combo(
                model.BARCODE_TEXT_CHOICES,
                (element.show_text, element.text_above))
            make_row("Value Text:", text_combo)

            font_spin = make_spin(int((element.font or element.DEFAULT_FONT)[1]), 6, 200)
            make_row("Text Height:", font_spin)

            check_combo, check_codes = make_combo(model.BARCODE_CHECK_DIGIT,
                                                  element.check_digit)
            make_row("UCC Check Digit:", check_combo)

            mode_combo, mode_codes = make_combo(model.BARCODE_MODES,
                                                element.mode)
            make_row("Mode:", mode_combo)

            content.show_all()

            def on_response(_dialog, response):
                if response == Gtk.ResponseType.OK:
                    element.barcode_value = value_entry.get_text()
                    element.bar_height = int(height_spin.get_value())
                    element.module_width = int(module_spin.get_value())
                    element.orientation = orientation_codes[orientation_combo.get_active()]
                    element.show_text, element.text_above = text_codes[text_combo.get_active()]
                    element.check_digit = check_codes[check_combo.get_active()]
                    element.mode = mode_codes[mode_combo.get_active()]
                    if element.show_text:
                        # With the line switched on, name the font it prints in
                        # rather than leaving it to whatever the printer has
                        # selected.
                        code = (element.font or element.DEFAULT_FONT)[0]
                        size = int(font_spin.get_value())
                        element.font = (code, size, size)
                    element.sync_box()
                    self.design_canvas.queue_draw()
                    self.on_canvas_changed()

                _dialog.destroy()

            self._open_editor(element, dialog, on_response)
        
        elif isinstance(element, ImageElement):
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

        elif isinstance(element, FrameElement):
            # Show Frame edit dialog
            dialog = Gtk.Dialog(title="Edit Frame", parent=self, flags=0)
            dialog.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                              Gtk.STOCK_OK, Gtk.ResponseType.OK)
            
            content = dialog.get_content_area()
            
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

            rounding_label = Gtk.Label(label="Corner Rounding:")
            content.pack_start(rounding_label, False, False, 0)
            rounding_spin = _make_spin(element.rounding, 0,
                                       FrameElement.MAX_ROUNDING)
            content.pack_start(rounding_spin, False, False, 0)

            content.show_all()
            
            def on_response(_dialog, response):
                if response == Gtk.ResponseType.OK:
                    element.width = int(width_spin.get_value())
                    element.height = int(height_spin.get_value())
                    element.thickness = int(thickness_spin.get_value())
                    element.colour = colour_codes[colour_combo.get_active()]
                    element.rounding = int(rounding_spin.get_value())
                    self.design_canvas.queue_draw()
                    self.on_canvas_changed()

                _dialog.destroy()

            self._open_editor(element, dialog, on_response)
    
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


def main():
    """Main entry point for the application."""
    app = ZPLViewerWindow()
    app.connect('destroy', Gtk.main_quit)
    # Ask for the front. Started from an editor running full screen, a new
    # window can otherwise map behind it and look as though nothing happened.
    app.present()
    Gtk.main()


if __name__ == '__main__':
    main()
