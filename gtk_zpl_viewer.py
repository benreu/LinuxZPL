#!/usr/bin/env python3
"""
ZPL Viewer - Gtk Application to Display ZPL Output

A simple GTK3 application for viewing rendered ZPL (Zebra Programming Language) output.
"""

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, GdkPixbuf, Gdk
import os
import base64
from pathlib import Path
from zpl_renderer import ZPLRenderer
from zpl_designer import DesignCanvas, TextElement, FrameElement, BarcodeElement, ImageElement
from PIL import Image
import io


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
        self.printer_font_name = None
        self.font_path = None
        
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

        # Upload font menu item
        upload_font_item = Gtk.MenuItem(label="Upload Font to Printer")
        upload_font_item.connect("activate", self.on_upload_font_clicked)
        settings_menu.append(upload_font_item)

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
                renderer = ZPLRenderer(width=self.label_width, height=self.label_height)
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

    def on_print_clicked(self, widget):
        """Handle print button click."""
        import socket
        printer_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            printer_socket.connect(('192.168.50.21', 9100)) #connect to printer IP and port
        except OSError as e:
            self.show_error_dialog(str(e))
            return
        content = self.design_canvas.to_zpl()
        printer_socket.send(bytes(content, 'utf-8')) #using bytes 
        printer_socket.close () #closing connection
    
    def on_upload_font_clicked(self, widget):
        dialog = Gtk.FileChooserDialog(
            title="Select Font File", parent=self,
            action=Gtk.FileChooserAction.OPEN,
            buttons=(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                     Gtk.STOCK_OPEN, Gtk.ResponseType.OK))
        ffilter = Gtk.FileFilter()
        ffilter.set_name("TrueType Fonts (*.ttf)")
        ffilter.add_pattern("*.ttf")
        ffilter.add_pattern("*.TTF")
        dialog.add_filter(ffilter)
        response = dialog.run()
        font_path = dialog.get_filename() if response == Gtk.ResponseType.OK else None
        dialog.destroy()
        if not font_path:
            return
        self._apply_font(font_path)

    def _apply_font(self, font_path: str):
        from PIL import ImageFont
        from pathlib import Path
        try:
            pil_font = ImageFont.truetype(font_path, 12)
            font_family = pil_font.getname()[0]
        except Exception as e:
            self.show_error_dialog(f"Could not read font: {e}")
            return
        printer_font_name = Path(font_path).stem[:8].upper()
        try:
            self._upload_font_to_printer(font_path, printer_font_name)
        except Exception as e:
            self.show_error_dialog(f"Font upload failed: {e}")
            return
        self.font_path = font_path
        self.printer_font_name = printer_font_name
        self.design_canvas.set_font(font_path, font_family, printer_font_name)
        self.renderer.set_font(font_path)
        self.update_status(f"Font '{font_family}' uploaded as E:{printer_font_name}.TTF")

    def _upload_font_to_printer(self, font_path: str, font_name: str):
        import socket
        with open(font_path, 'rb') as f:
            font_data = f.read()
        data_len = len(font_data)
        header = f"~DYE:{font_name},A,TT,{data_len},{data_len},".encode('ascii')
        payload = header + font_data
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(10)
        sock.connect(('192.168.50.21', 9100))
        sock.sendall(payload)
        sock.close()

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
        lines = zpl_content.split('\n')
        i = 0
        
        while i < len(lines):
            line = lines[i].strip()
            
            # Skip comments and empty lines
            if line.startswith(';') or not line:
                i += 1
                continue
            
            if line.startswith('^FO'):
                # Position command - start of an element
                import re
                match = re.match(r'\^FO(\d+),(\d+)', line)
                if match:
                    x, y = int(match.group(1)), int(match.group(2))
                    
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

                        if next_line.startswith('^AF'):
                            # Text element
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
                                text_element.width = len(text) * font_w
                                text_element.height = font_h
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
            
            i += 1
        
        self.design_canvas.queue_draw()
    
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
    
    def render_zpl(self):
        """Render the ZPL content from the design canvas."""
        try:
            # Get ZPL from designer
            content = self.design_canvas.to_zpl()
            
            # Check if there are any actual elements (beyond just XA and XZ)
            if not self.design_canvas.elements:
                self.image_view.clear()
                self.update_status("No elements to render")
                return
            
            # Create renderer with current label size
            renderer = ZPLRenderer(width=self.label_width, height=self.label_height)
            
            # Render ZPL
            pil_image = renderer.render(content)
            
            # Convert PIL image to GdkPixbuf
            pixbuf = self.pil_to_pixbuf(pil_image)
            
            # Scale to fit display (max 600px width)
            if pixbuf.get_width() > 600:
                scale = 600 / pixbuf.get_width()
                new_width = int(pixbuf.get_width() * scale)
                new_height = int(pixbuf.get_height() * scale)
                pixbuf = pixbuf.scale_simple(new_width, new_height, GdkPixbuf.InterpType.BILINEAR)
            
            # Display image
            self.image_view.set_from_pixbuf(pixbuf)
            self.update_status("Rendered successfully")
            
        except Exception as e:
            self.show_error_dialog("Failed to render ZPL: {e}")
            self.update_status("Render failed")
    
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

            # Font file picker
            selected_font_path = [element.font_path]

            font_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            font_lbl = Gtk.Label(label="Font:")
            font_lbl.set_size_request(90, -1)
            font_lbl.set_halign(Gtk.Align.END)
            font_row.pack_start(font_lbl, False, False, 0)

            def _font_display(path):
                if path:
                    from pathlib import Path as _Path
                    return _Path(path).name
                canvas_family = self.design_canvas.font_family
                return f"Default ({canvas_family})" if canvas_family else "Default"

            font_name_lbl = Gtk.Label(label=_font_display(element.font_path))
            font_name_lbl.set_halign(Gtk.Align.START)
            font_name_lbl.set_ellipsize(3)  # PANGO_ELLIPSIZE_END
            font_row.pack_start(font_name_lbl, True, True, 0)

            font_browse_btn = Gtk.Button(label="Browse…")
            font_row.pack_start(font_browse_btn, False, False, 0)

            clear_font_btn = Gtk.Button(label="Clear")
            font_row.pack_start(clear_font_btn, False, False, 0)

            content.pack_start(font_row, False, False, 0)

            def on_font_browse(btn):
                fdialog = Gtk.FileChooserDialog(
                    title="Select Font File", parent=dialog,
                    action=Gtk.FileChooserAction.OPEN,
                    buttons=(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                             Gtk.STOCK_OPEN, Gtk.ResponseType.OK))
                ff = Gtk.FileFilter()
                ff.set_name("TrueType Fonts (*.ttf)")
                ff.add_pattern("*.ttf")
                ff.add_pattern("*.TTF")
                fdialog.add_filter(ff)
                if selected_font_path[0]:
                    fdialog.set_filename(selected_font_path[0])
                resp = fdialog.run()
                path = fdialog.get_filename() if resp == Gtk.ResponseType.OK else None
                fdialog.destroy()
                if path:
                    selected_font_path[0] = path
                    font_name_lbl.set_text(_font_display(path))

            def on_font_clear(btn):
                selected_font_path[0] = None
                font_name_lbl.set_text(_font_display(None))

            font_browse_btn.connect("clicked", on_font_browse)
            clear_font_btn.connect("clicked", on_font_clear)

            content.show_all()

            response = dialog.run()
            if response == Gtk.ResponseType.OK:
                element.text = text_entry.get_text()
                element.font_height = int(height_spin.get_value())
                element.font_width = int(width_spin.get_value())
                element.width = len(element.text) * element.font_width
                element.height = element.font_height

                new_path = selected_font_path[0]
                if new_path != element.font_path:
                    if new_path:
                        from PIL import ImageFont as _IF
                        from pathlib import Path as _Path
                        try:
                            family = _IF.truetype(new_path, 12).getname()[0]
                            printer_name = _Path(new_path).stem[:8].upper()
                            try:
                                self._upload_font_to_printer(new_path, printer_name)
                            except Exception as e:
                                self.show_error_dialog(f"Font upload failed: {e}")
                            self.design_canvas.set_element_font(element, new_path, family, printer_name)
                            self.renderer.register_font(printer_name, new_path)
                        except Exception as e:
                            self.show_error_dialog(f"Could not load font: {e}")
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
                element._load_pixbuf()
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
    
    @staticmethod
    def pil_to_pixbuf(pil_image: Image.Image) -> GdkPixbuf.Pixbuf:
        """Convert PIL Image to GdkPixbuf."""
        # Convert PIL image to PNG bytes
        png_data = io.BytesIO()
        pil_image.save(png_data, format='PNG')
        png_data.seek(0)
        
        # Load into GdkPixbuf
        loader = GdkPixbuf.PixbufLoader.new_with_type('png')
        loader.write(png_data.read())
        loader.close()
        return loader.get_pixbuf()
    
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
