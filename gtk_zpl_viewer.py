#!/usr/bin/env python3
"""
ZPL Viewer - Gtk Application to Display ZPL Output

A simple GTK3 application for viewing rendered ZPL (Zebra Programming Language) output.
"""

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, GdkPixbuf, GLib
import os
import base64
import configparser
import socket
from pathlib import Path
from zpl_renderer import ZPLRenderer
import zpl_fonts
from zpl_designer import DesignCanvas, TextElement, FrameElement, BarcodeElement, ImageElement
from PIL import Image
import io


DEFAULT_PRINTER_ADDRESS = '192.168.50.21'
DEFAULT_PRINTER_PORT = 9100


def _config_path() -> Path:
    """Path to the persisted settings file."""
    return Path(GLib.get_user_config_dir()) / 'linuxzpl' / 'settings.ini'


class ZPLViewerWindow(Gtk.Window):
    """Main GTK window for the ZPL Viewer application."""
    
    def __init__(self):
        super().__init__(title="ZPL Viewer")
        self.set_default_size(900, 1000)
        self.set_border_width(10)
        self.connect("delete-event", self.main_window_closed)
        
        self.renderer = ZPLRenderer()
        self.current_zpl_content = ""
        self.current_filepath = None
        self.unsaved_changes = False
        
        # Label size settings (default: 4x6 inch at 203 DPI = 812x1218 pixels)
        self.label_width = 812
        self.label_height = 1218

        # Printer connection settings (persisted in the config file)
        self.printer_address = DEFAULT_PRINTER_ADDRESS
        self.printer_port = DEFAULT_PRINTER_PORT
        self._load_settings()

        # Create main layout
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add(main_box)
        
        # Create menu bar
        menu_bar = Gtk.MenuBar()
        main_box.pack_start(menu_bar, False, False, 0)
        
        # File menu
        file_menu = Gtk.Menu()
        file_menu_item = Gtk.MenuItem(label="File")
        file_menu_item.set_submenu(file_menu)
        menu_bar.append(file_menu_item)
        
        # Load menu item
        load_item = Gtk.MenuItem(label="Load ZPL File")
        load_item.connect("activate", self.on_load_file_clicked)
        file_menu.append(load_item)
        
        # Save menu item
        save_item = Gtk.MenuItem(label="Save")
        save_item.connect("activate", self.on_save_clicked)
        file_menu.append(save_item)
        
        # Save menu item
        save_item = Gtk.MenuItem(label="Save as...")
        save_item.connect("activate", self.on_save_as_clicked)
        file_menu.append(save_item)
        
        # Print menu item
        print_item = Gtk.MenuItem(label="Print")
        print_item.connect("activate", self.on_print_clicked)
        file_menu.append(print_item)
        
        # Separator
        separator = Gtk.SeparatorMenuItem()
        file_menu.append(separator)
        
        # Quit menu item
        quit_item = Gtk.MenuItem(label="Quit")
        quit_item.connect("activate", self.close_app)
        file_menu.append(quit_item)
        
        file_menu.show_all()
        
        # Settings menu
        settings_menu = Gtk.Menu()
        settings_menu_item = Gtk.MenuItem(label="Settings")
        settings_menu_item.set_submenu(settings_menu)
        menu_bar.append(settings_menu_item)
        
        # Label settings menu item
        label_settings_item = Gtk.MenuItem(label="Label Size")
        label_settings_item.connect("activate", self.on_label_settings_clicked)
        settings_menu.append(label_settings_item)

        # Printer settings menu item
        printer_settings_item = Gtk.MenuItem(label="Printer Settings")
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
        toolbar_label = Gtk.Label(label="Designer")
        toolbar_label.set_halign(Gtk.Align.START)
        left_box.pack_start(toolbar_label, False, False, 0)
        
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

        # Delete button
        delete_btn = Gtk.Button(label="Delete")
        delete_btn.connect("clicked", self.on_delete_clicked)
        toolbar_box.pack_end(delete_btn, False, False, 0)
        
        # Design canvas
        scrolled_canvas = Gtk.ScrolledWindow()
        scrolled_canvas.set_hexpand(True)
        scrolled_canvas.set_vexpand(True)
        left_box.pack_start(scrolled_canvas, True, True, 0)
        
        self.design_canvas = DesignCanvas(on_change_callback=self.on_canvas_changed, 
                                         label_width=self.label_width, 
                                         label_height=self.label_height)
        self.design_canvas.connect("draw", self.on_canvas_draw)
        self.design_canvas.connect("element-double-clicked", self.on_element_double_clicked)
        
        # Create a viewport for the canvas
        viewport = Gtk.Viewport()
        viewport.add(self.design_canvas)
        scrolled_canvas.add(viewport)
        
        # Status bar
        self.status_bar = Gtk.Statusbar()
        main_box.pack_end(self.status_bar, False, False, 0)
        
        self.show_all()
        self.update_status("Ready")

    def main_window_closed(self, widget, event):
      if not self.close_app(widget):
        return True

    def close_app(self, widget):
      """Handle quit app from Menu or Window close button."""
      if(self.unsaved_changes):
        dialog = Gtk.MessageDialog(
          parent=self,
          flags=0,
          message_type=Gtk.MessageType.QUESTION,
          buttons=Gtk.ButtonsType.YES_NO,
          text="You have unsaved changes.\nDo you want to quit without saving?"
        )
        response = dialog.run()
        dialog.destroy()
        
        if response == Gtk.ResponseType.NO:
          return False
      Gtk.main_quit()
          
    def check_unsaved_changes(self):
      """Check for unsaved changes and prompt the user."""
      if self.unsaved_changes:
        dialog = Gtk.MessageDialog(
          parent=self,
          flags=0,
          message_type=Gtk.MessageType.QUESTION,
          buttons=Gtk.ButtonsType.YES_NO,
          text="You have unsaved changes.\nDo you want to save?"
        )
        response = dialog.run()
        dialog.destroy()
        
        if response == Gtk.ResponseType.YES:
          self.save_zpl_file(self.current_filepath, self.design_canvas.to_zpl())
          return True
        elif response == Gtk.ResponseType.NO:
          return False
        else:
          return False
      else:
        return True
    
    def on_load_file_clicked(self, widget):
        """Handle load file button click."""
        if not self.check_unsaved_changes():
          return
            
        dialog = Gtk.FileChooserDialog(
          title="Load ZPL File",
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
            return

        if not content.strip() or content == "^XA\n^XZ":
            self.show_error_dialog("No content to save")
            return
        
        # If we have a current file path, save directly
        if self.current_filepath:
          self.save_zpl_file(self.current_filepath, content)
          return
        
        # Show save dialog
        self.save_dialog() 
    
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
        
        response = dialog.run()
        if response == Gtk.ResponseType.OK:
            filepath = dialog.get_filename()
            dialog.destroy()
            self.save_zpl_file(filepath, content)
            return True
        else:
            dialog.destroy()
            return False       
  
    def save_zpl_file(self, filepath: str, content: str):
      """Save ZPL content to a file."""
      try:
        with open(filepath, 'w', encoding='utf-8') as f:
          f.write(content)
        
        self.current_filepath = filepath
        filename = os.path.basename(filepath)
        self.update_status(f"Saved: {filename}")
        self.unsaved_changes = False
      except Exception as e:
        self.show_error_dialog("Failed to save file: {e}")
        self.update_status("Save failed")
    
    def load_zpl_file(self, filepath: str):
        """Load a ZPL file and update the views."""
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                content = f.read()
            
            self.current_zpl_content = content
            self.current_filepath = filepath
            
            # Parse label size from file if present
            self._parse_label_size_from_zpl(content)
            
            # Clear and load into designer canvas
            self.design_canvas.clear()
            self.design_canvas.set_label_size(self.label_width, self.label_height)
            
            # Parse ZPL and create elements (basic parsing)
            self._parse_zpl_to_canvas(content)
            
            # Update status bar
            filename = os.path.basename(filepath)
            self.update_status(f"Loaded: {filename}")

        except Exception as e:
            self.show_error_dialog(f"Failed to load file: {e}")
            self.update_status("Error loading file")

    def _confirm_printer_fonts(self) -> bool:
        """Check the label's fonts are on the printer. False cancels printing."""
        sources = self._label_font_sources()
        if not sources:
            return True  # nothing but built-in fonts, nothing to check

        installed = zpl_fonts.query_printer_fonts(self.printer_address, self.printer_port)
        if installed is None:
            return self._ask_font_problem(
                "The printer could not be asked which fonts it has.",
                "It may be unreachable, or may not support font queries.\n"
                "Printing anyway may fall back to a substitute font.",
                uploadable={})

        missing = {n: path for n, path in sources.items() if n.upper() not in installed}
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
        """Ask what to do about missing fonts. True means go ahead and print."""
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
            for name, path in sorted(uploadable.items()):
                self.update_status(f"Uploading {zpl_fonts.printer_font_path(name)}...")
                try:
                    zpl_fonts.upload_font(self.printer_address, self.printer_port, path, name)
                except Exception as e:
                    self.show_error_dialog(f"Upload of {name} failed: {e}")
                    return False
            return True
        return response == Gtk.ResponseType.OK

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
        printer_socket.send(bytes(content, 'utf-8')) #using bytes 
        printer_socket.close () #closing connection
    
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

    def _printer_font_names(self, exclude=None):
        """Printer font names already used by the label's text elements."""
        return {el.printer_font_name for el in self.design_canvas.elements
                if el is not exclude and getattr(el, 'printer_font_name', None)}

    def _label_font_sources(self):
        """Printer font name -> local .ttf path, for fonts this label uses.

        A font loaded from a .zpl has no local file, so its value is None and it
        cannot be uploaded - only reported as missing.
        """
        sources = {}
        for el in self.design_canvas.elements:
            name = getattr(el, 'printer_font_name', None)
            if name:
                sources.setdefault(name, getattr(el, 'font_path', None))
        canvas = self.design_canvas
        if canvas.printer_font_name:
            sources.setdefault(canvas.printer_font_name, canvas.font_path)
        return sources

    def _parse_label_size_from_zpl(self, zpl_content: str):
        """Extract label size from ZPL commands if present."""
        import re
        # Look for ^PW (print width) and ^LL (label length) commands
        pw_match = re.search(r'\^PW(\d+)', zpl_content)
        ll_match = re.search(r'\^LL(\d+)', zpl_content)
        
        if pw_match:
            try:
                self.label_width = int(pw_match.group(1))
            except (ValueError, AttributeError):
                pass
        
        if ll_match:
            try:
                self.label_height = int(ll_match.group(1))
            except (ValueError, AttributeError):
                pass
    
    def _parse_zpl_to_canvas(self, zpl_content: str):
        """Parse ZPL content and populate the designer canvas with elements."""
        # Very basic ZPL parsing - this is a simplified version
        # Expand hidden elements back into real lines, preceded by a marker.
        # Doing it in place keeps z-order, since list position is z-order.
        lines = []
        for raw in zpl_content.split('\n'):
            stripped = raw.strip()
            if stripped.startswith('^FXDESIGNER_NOPRINT:'):
                blob = stripped[len('^FXDESIGNER_NOPRINT:'):]
                try:
                    body = base64.b64decode(blob).decode('utf-8')
                except Exception:
                    continue
                lines.append('^FXDESIGNER_NOPRINT')
                lines.extend(body.split('\n'))
            else:
                lines.append(raw)

        i = 0
        pending_no_print = False

        while i < len(lines):
            line = lines[i].strip()
            
            # Skip comments and empty lines
            if line.startswith(';') or not line:
                i += 1
                continue

            if line == '^FXDESIGNER_NOPRINT':
                pending_no_print = True
                i += 1
                continue
            
            if line.startswith('^FO'):
                # Position command - start of an element
                import re
                match = re.match(r'\^FO(\d+),(\d+)', line)
                if match:
                    x, y = int(match.group(1)), int(match.group(2))
                    before = len(self.design_canvas.elements)
                    
                    # Look ahead for the element type
                    i += 1
                    preview_b64 = None  # JPEG preview embedded by designer on save
                    path_hint = None    # original file path embedded by designer on save
                    while i < len(lines):
                        next_line = lines[i].strip()

                        # Designer metadata in ^FX comments — collect and keep looking
                        if next_line.startswith('^FXDESIGNER_PREVIEW:'):
                            preview_b64 = next_line[len('^FXDESIGNER_PREVIEW:'):]
                            i += 1
                            continue
                        elif next_line.startswith('^FXDESIGNER_PATH:'):
                            path_hint = next_line[len('^FXDESIGNER_PATH:'):]
                            i += 1
                            continue

                        if next_line.startswith('^AF') or next_line.startswith('^A@'):
                            # Text element. ^A@ names a font downloaded to the
                            # printer, e.g. ^A@N,53,19,E:DEJAVUSA.TTF
                            font_name = None
                            if next_line.startswith('^A@'):
                                match = re.match(
                                    r'\^A@[A-Z]?,(\d+),(\d+),[^:]*:([^.,]+)', next_line)
                                if match:
                                    font_name = match.group(3).upper()
                            else:
                                match = re.match(r'\^AF[A-Z]?,(\d+),(\d+)', next_line)
                            font_h, font_w = 36, 20
                            if match:
                                font_h = int(match.group(1))
                                font_w = int(match.group(2))

                            # Get FD (field data)
                            i += 1
                            if i < len(lines) and lines[i].strip().startswith('^FD'):
                                text = lines[i].strip()[3:-3]  # Remove ^FD and ^FS
                                self.design_canvas.add_text_element(text)
                                text_element = self.design_canvas.elements[-1]
                                text_element.x = x
                                text_element.y = y
                                text_element.font_height = font_h
                                text_element.font_width = font_w
                                text_element.height = font_h
                                text_element.printer_font_name = font_name
                                # A .zpl records only the printer name, but that
                                # name is derived from the font file, so the
                                # installed .ttf can usually be found again -
                                # without it the label would reopen in a
                                # substitute face and at the wrong width.
                                local = zpl_fonts.file_for_printer_name(font_name)
                                if local:
                                    text_element.font_path = local
                                    try:
                                        text_element.font_family = zpl_fonts.family_for_file(local)
                                    except Exception:
                                        text_element.font_family = None
                                    zpl_fonts.register_app_font(local)
                                    self.renderer.register_font(font_name, local)
                                self.design_canvas.sync_text_width(text_element)
                            break
                        elif next_line.startswith('^GB'):
                            # Frame element
                            match = re.match(r'\^GB(\d+),(\d+)(?:,(\d+))?', next_line)
                            if match:
                                w, h = int(match.group(1)), int(match.group(2))
                                t = int(match.group(3)) if match.group(3) else 2
                                box = FrameElement(x, y, w, h, t)
                                self.design_canvas.elements.append(box)
                            break
                        elif next_line.startswith('^BC'):
                            # Barcode element
                            match = re.match(r'\^BC[A-Z]?,(\d+)?', next_line)
                            h = int(match.group(1)) if match and match.group(1) else 100
                            barcode_value = "123456789"

                            # Get barcode value from next FD field
                            i += 1
                            if i < len(lines) and lines[i].strip().startswith('^FD'):
                                barcode_value = lines[i].strip()[3:-3]  # Remove ^FD and ^FS

                            barcode = BarcodeElement(x, y, height=h, barcode_value=barcode_value)
                            self.design_canvas.elements.append(barcode)
                            break
                        elif next_line.startswith('^GF'):
                            # Image element — prefer embedded JPEG preview (same quality as
                            # first import), fall back to decoding 1-bit ^GF data only when
                            # no preview exists (e.g. ZPL from an external tool).
                            gf_match = re.match(r'\^GFA,(\d+),(\d+),(\d+),(.*)', next_line)
                            if gf_match:
                                total_b = int(gf_match.group(1))
                                bpr = int(gf_match.group(3))
                                gf_h = total_b // bpr
                                gf_w = bpr * 8
                                img_el = None
                                # 1. Original file still present — highest quality
                                if path_hint and os.path.exists(path_hint):
                                    try:
                                        img_el = ImageElement(x, y, gf_w, gf_h,
                                                              image_path=path_hint)
                                    except Exception:
                                        img_el = None
                                # 2. Embedded JPEG preview — same quality as first import
                                if img_el is None and preview_b64:
                                    try:
                                        jpeg_data = base64.b64decode(preview_b64)
                                        pil_img = Image.open(
                                            io.BytesIO(jpeg_data)).convert('RGB')
                                        img_el = ImageElement(x, y, gf_w, gf_h,
                                                              _pil_image=pil_img)
                                    except Exception:
                                        img_el = None
                                # 3. Decode 1-bit ^GF data — last resort
                                if img_el is None:
                                    hex_data = gf_match.group(4).strip()
                                    if total_b > 0 and bpr > 0 and hex_data:
                                        try:
                                            import numpy as np
                                            raw = bytes.fromhex(hex_data)
                                            arr = np.frombuffer(
                                                raw, dtype=np.uint8).reshape(gf_h, bpr)
                                            unpacked = np.unpackbits(arr, axis=1)[:, :gf_w]
                                            pixel_data = (
                                                (1 - unpacked) * 255).astype(np.uint8)
                                            pil_img = Image.fromarray(
                                                pixel_data, mode='L').convert('RGB')
                                            img_el = ImageElement(x, y, gf_w, gf_h,
                                                                  _pil_image=pil_img)
                                        except Exception:
                                            pass
                                if img_el is not None:
                                    self.design_canvas.elements.append(img_el)
                            break
                        elif next_line.startswith('^FS'):
                            break

                        i += 1

                    if pending_no_print:
                        # Mark whatever this block appended, wherever it was
                        # appended from, rather than touching each branch.
                        for el in self.design_canvas.elements[before:]:
                            el.print_enabled = False
                        pending_no_print = False
            
            i += 1
        
        self.design_canvas.queue_draw()
    
    def _load_settings(self):
        """Load persisted settings from the config file."""
        parser = configparser.ConfigParser()
        try:
            parser.read(_config_path())
            self.printer_address = parser.get(
                'printer', 'address', fallback=self.printer_address)
            self.printer_port = parser.getint(
                'printer', 'port', fallback=self.printer_port)
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

        # Connection test
        result_label = Gtk.Label()
        result_label.set_halign(Gtk.Align.START)
        result_label.set_line_wrap(True)

        def on_test_clicked(btn):
            addr = address_entry.get_text().strip()
            port = int(port_spin.get_value())
            if not addr:
                result_label.set_markup(
                    "<span foreground='red'>Address is required</span>")
                return
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5)
            try:
                sock.connect((addr, port))
            except OSError as e:
                msg = GLib.markup_escape_text(str(e))
                result_label.set_markup(f"<span foreground='red'>✗ {msg}</span>")
            else:
                result_label.set_markup(
                    f"<span foreground='green'>✓ Connected to {addr}:{port}</span>")
            finally:
                sock.close()

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
            self._save_settings()
            self.update_status(f"Printer set to {new_address}:{new_port}")
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
        
        # Common label sizes (width x height in pixels at 203 DPI)
        preset_sizes = {
            "4x6": (812, 1218),
            "5x7": (1015, 1428),
            "6x4": (1218, 812),
            "3x5": (609, 1015),
            "2x3": (406, 609)
        }
        
        def on_preset_clicked(btn, w, h):
            width_spin.set_value(w)
            height_spin.set_value(h)
        
        for label_text, (w, h) in preset_sizes.items():
            btn = Gtk.Button(label=label_text)
            btn.connect("clicked", on_preset_clicked, w, h)
            presets_box.pack_start(btn, False, False, 0)
        
        # Custom sizes
        custom_label = Gtk.Label(label="Custom Size (pixels):")
        custom_label.set_halign(Gtk.Align.START)
        content.pack_start(custom_label, False, False, 0)
        
        # Width
        width_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        content.pack_start(width_box, False, False, 0)
        
        width_label = Gtk.Label(label="Width:")
        width_label.set_size_request(80, -1)
        width_box.pack_start(width_label, False, False, 0)
        
        width_spin = Gtk.SpinButton()
        width_adj = Gtk.Adjustment(value=self.label_width, lower=100, upper=5000, step_increment=10)
        width_spin.set_adjustment(width_adj)
        width_spin.set_numeric(True)
        width_box.pack_start(width_spin, True, True, 0)
        
        # Height
        height_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        content.pack_start(height_box, False, False, 0)
        
        height_label = Gtk.Label(label="Height:")
        height_label.set_size_request(80, -1)
        height_box.pack_start(height_label, False, False, 0)
        
        height_spin = Gtk.SpinButton()
        height_adj = Gtk.Adjustment(value=self.label_height, lower=100, upper=5000, step_increment=10)
        height_spin.set_adjustment(height_adj)
        height_spin.set_numeric(True)
        height_box.pack_start(height_spin, True, True, 0)
        
        # Info label
        info_label = Gtk.Label(label="Note: Label size constrains the drawing area and is saved with the file.")
        info_label.set_halign(Gtk.Align.START)
        info_label.set_line_wrap(True)
        content.pack_start(info_label, False, False, 0)
        
        content.show_all()
        
        response = dialog.run()
        if response == Gtk.ResponseType.OK:
            new_width = int(width_spin.get_value())
            new_height = int(height_spin.get_value())
            self.label_width = new_width
            self.label_height = new_height
            self.design_canvas.set_label_size(new_width, new_height)
            self.unsaved_changes = True
            self.update_status(f"Label size set to {new_width}x{new_height}")
        
        dialog.destroy()
    
    def on_canvas_changed(self):
        """Handle canvas changes (drag, resize, etc.) and mark as unsaved."""
        self.unsaved_changes = True
    
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
        self.design_canvas.remove_selected()
    
    def on_canvas_draw(self, widget, context):
        """Canvas draw event handler - re-render when canvas changes."""
        # This is called when the canvas is drawn, we can use it to trigger re-rendering
        # Actually, we'll trigger on button releases and element additions
        pass
    
    def on_element_double_clicked(self, widget, element):
        """Handle double-click on canvas element for editing."""
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
                row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
                lbl = Gtk.Label(label=lbl_text)
                lbl.set_size_request(90, -1)
                lbl.set_halign(Gtk.Align.END)
                row.pack_start(lbl, False, False, 0)
                row.pack_start(widget, True, True, 0)
                content.pack_start(row, False, False, 0)

            # Text input
            text_entry = Gtk.Entry()
            text_entry.set_text(element.text)
            make_row("Text:", text_entry)

            # Font height
            height_spin = Gtk.SpinButton()
            height_adj = Gtk.Adjustment(value=element.font_height, lower=8, upper=500, step_increment=1)
            height_spin.set_adjustment(height_adj)
            make_row("Font Height:", height_spin)

            # Font width
            width_spin = Gtk.SpinButton()
            width_adj = Gtk.Adjustment(value=element.font_width, lower=8, upper=500, step_increment=1)
            width_spin.set_adjustment(width_adj)
            make_row("Font Width:", width_spin)

            # Font chooser (installed families only)
            selected_font = [element.font_path, element.font_family]

            font_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            font_lbl = Gtk.Label(label="Font:")
            font_lbl.set_size_request(90, -1)
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

            content.show_all()

            response = dialog.run()
            if response == Gtk.ResponseType.OK:
                element.text = text_entry.get_text()
                element.font_height = int(height_spin.get_value())
                element.font_width = int(width_spin.get_value())
                element.height = element.font_height
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

            dialog.destroy()
        
        elif isinstance(element, BarcodeElement):
            # Show barcode edit dialog
            dialog = Gtk.Dialog(title="Edit Barcode", parent=self, flags=0)
            dialog.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                              Gtk.STOCK_OK, Gtk.ResponseType.OK)
            
            content = dialog.get_content_area()
            
            # Barcode value/text
            value_label = Gtk.Label(label="Barcode Value:")
            content.pack_start(value_label, False, False, 0)
            value_entry = Gtk.Entry()
            value_entry.set_text(element.barcode_value)
            content.pack_start(value_entry, False, False, 0)
            
            # Barcode height
            height_label = Gtk.Label(label="Barcode Height:")
            content.pack_start(height_label, False, False, 0)
            height_spin = Gtk.SpinButton()
            height_adj = Gtk.Adjustment(value=element.height, lower=20, upper=300, step_increment=1)
            height_spin.set_adjustment(height_adj)
            content.pack_start(height_spin, False, False, 0)
            
            content.show_all()
            
            response = dialog.run()
            if response == Gtk.ResponseType.OK:
                element.barcode_value = value_entry.get_text()
                element.width = (35 + len(element.barcode_value) * 11) * 2
                element.height = int(height_spin.get_value())
                self.design_canvas.queue_draw()
                self.on_canvas_changed()
            
            dialog.destroy()
        
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
            
            # Frame thickness
            thickness_label = Gtk.Label(label="Thickness:")
            content.pack_start(thickness_label, False, False, 0)
            thickness_spin = Gtk.SpinButton()
            thickness_adj = Gtk.Adjustment(value=element.thickness, lower=1, upper=10, step_increment=1)
            thickness_spin.set_adjustment(thickness_adj)
            content.pack_start(thickness_spin, False, False, 0)
            
            content.show_all()
            
            response = dialog.run()
            if response == Gtk.ResponseType.OK:
                element.width = int(width_spin.get_value())
                element.height = int(height_spin.get_value())
                element.thickness = int(thickness_spin.get_value())
                self.design_canvas.queue_draw()
                self.on_canvas_changed()
            
            dialog.destroy()
    
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
    Gtk.main()


if __name__ == '__main__':
    main()
