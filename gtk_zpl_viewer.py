#!/usr/bin/env python3
"""
ZPL Viewer - Gtk Application to Display ZPL Output

A simple GTK3 application for viewing rendered ZPL (Zebra Programming Language) output.
"""

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, GdkPixbuf, Gdk
import os
from pathlib import Path
from zpl_renderer import ZPLRenderer
from zpl_designer import DesignCanvas, TextElement, BoxElement, BarcodeElement
from PIL import Image
import io


class ZPLViewerWindow(Gtk.Window):
    """Main GTK window for the ZPL Viewer application."""
    unsaved_changes = False
    
    def __init__(self):
        super().__init__(title="ZPL Viewer")
        self.set_default_size(900, 1000)
        self.set_border_width(10)
        self.connect("delete-event", self.main_window_closed)
        
        self.renderer = ZPLRenderer()
        self.current_zpl_content = ""
        self.current_filepath = None
        
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
        
        # Separator
        separator = Gtk.SeparatorMenuItem()
        file_menu.append(separator)
        
        # Quit menu item
        quit_item = Gtk.MenuItem(label="Quit")
        quit_item.connect("activate", self.close_app)
        file_menu.append(quit_item)
        
        file_menu.show_all()

        # Renderer menu
        renderer_menu = Gtk.Menu()
        renderer_menu_item = Gtk.MenuItem(label="Renderer")
        renderer_menu_item.set_submenu(renderer_menu)
        menu_bar.append(renderer_menu_item)
        
        # Refresh menu item
        refresh_item = Gtk.MenuItem(label="Refresh")
        refresh_item.connect("activate", self.on_refresh_clicked)
        renderer_menu.append(refresh_item)

        renderer_menu.show_all()
        
        # Content box with padding
        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        content_box.set_margin_top(10)
        content_box.set_margin_bottom(10)
        content_box.set_margin_start(10)
        content_box.set_margin_end(10)
        main_box.pack_start(content_box, True, True, 0)
        
        # Create paned view with text editor and preview
        paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        content_box.pack_start(paned, True, True, 0)
        
        # Left side: Designer canvas
        left_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        paned.add1(left_box)
        
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
        
        # Add box button
        add_box_btn = Gtk.Button(label="+ Box")
        add_box_btn.connect("clicked", self.on_add_box_clicked)
        toolbar_box.pack_start(add_box_btn, False, False, 0)
        
        # Add barcode button
        add_barcode_btn = Gtk.Button(label="+ Barcode")
        add_barcode_btn.connect("clicked", self.on_add_barcode_clicked)
        toolbar_box.pack_start(add_barcode_btn, False, False, 0)
        
        # Delete button
        delete_btn = Gtk.Button(label="Delete")
        delete_btn.connect("clicked", self.on_delete_clicked)
        toolbar_box.pack_end(delete_btn, False, False, 0)
        
        # Design canvas
        scrolled_canvas = Gtk.ScrolledWindow()
        scrolled_canvas.set_hexpand(True)
        scrolled_canvas.set_vexpand(True)
        left_box.pack_start(scrolled_canvas, True, True, 0)
        
        self.design_canvas = DesignCanvas(on_change_callback=self.render_zpl)
        self.design_canvas.connect("draw", self.on_canvas_draw)
        self.design_canvas.connect("element-double-clicked", self.on_element_double_clicked)
        
        # Create a viewport for the canvas
        viewport = Gtk.Viewport()
        viewport.add(self.design_canvas)
        scrolled_canvas.add(viewport)
        
        # Right side: Preview
        right_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        paned.add2(right_box)
        
        right_label = Gtk.Label(label="Preview")
        right_label.set_halign(Gtk.Align.START)
        right_box.pack_start(right_label, False, False, 0)
        
        # Image view with scroll
        scrolled_image = Gtk.ScrolledWindow()
        scrolled_image.set_hexpand(True)
        scrolled_image.set_vexpand(True)
        right_box.pack_start(scrolled_image, True, True, 0)
        
        self.image_view = Gtk.Image()
        scrolled_image.add(self.image_view)
        
        # Status bar
        self.status_bar = Gtk.Statusbar()
        main_box.pack_end(self.status_bar, False, False, 0)
        
        # Set paned position
        paned.set_position(400)
        
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
          if self.save_file_or_ask_for_filename() == False:
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
          return True
      return False
    
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
        # Get ZPL from designer
        content = self.design_canvas.to_zpl()
        
        if not content.strip() or content == "^XA\n^XZ":
            self.show_error_dialog("No content to save")
            return
        
        # If we have a current file path, save directly
        if self.current_filepath:
          self.save_zpl_file(self.current_filepath, content)
          return
        
        # Show save dialog
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
            
            # Clear and load into designer canvas
            self.design_canvas.clear()
            # Parse ZPL and create elements (basic parsing)
            self._parse_zpl_to_canvas(content)
            
            # Update status bar
            filename = os.path.basename(filepath)
            self.update_status("Loaded: {filename}")
            
            # Render and display
            self.render_zpl()
        except Exception as e:
            self.show_error_dialog("Failed to load file: {e}")
            self.update_status("Error loading file")
    
    def _parse_zpl_to_canvas(self, zpl_content: str):
        """Parse ZPL content and populate the designer canvas with elements."""
        # Very basic ZPL parsing - this is a simplified version
        lines = zpl_content.split('\n')
        i = 0
        
        while i < len(lines):
            line = lines[i].strip()
            
            if line.startswith('^FO'):
                # Position command - start of an element
                import re
                match = re.match(r'\^FO(\d+),(\d+)', line)
                if match:
                    x, y = int(match.group(1)), int(match.group(2))
                    
                    # Look ahead for the element type
                    i += 1
                    while i < len(lines):
                        next_line = lines[i].strip()
                        
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
                                self.design_canvas.elements[-1].x = x
                                self.design_canvas.elements[-1].y = y
                            break
                        elif next_line.startswith('^GB'):
                            # Box element
                            match = re.match(r'\^GB(\d+),(\d+)(?:,(\d+))?', next_line)
                            if match:
                                w, h = int(match.group(1)), int(match.group(2))
                                t = int(match.group(3)) if match.group(3) else 2
                                box = BoxElement(x, y, w, h, t)
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
                        elif next_line.startswith('^FS'):
                            break
                        
                        i += 1
            
            i += 1
        
        self.design_canvas.queue_draw()
    
    def on_refresh_clicked(self, widget):
        """Handle refresh button click."""
        self.render_zpl()
    
    def render_zpl(self):
        """Render the ZPL content from the design canvas."""
        try:
            # Get ZPL from designer
            content = self.design_canvas.to_zpl()
            
            if not content.strip() or content == "^XA\n^XZ":
                self.image_view.clear()
                self.update_status("No elements to render")
                return
            
            # Render ZPL
            pil_image = self.renderer.render(content)
            
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
        self.unsaved_changes = True
        self.render_zpl()
    
    def on_add_box_clicked(self, widget):
        """Handle add box element button click."""
        self.design_canvas.add_box_element()
        self.unsaved_changes = True
        self.render_zpl()
    
    def on_add_barcode_clicked(self, widget):
        """Handle add barcode element button click."""
        self.design_canvas.add_barcode_element()
        self.unsaved_changes = True
        self.render_zpl()
    
    def on_delete_clicked(self, widget):
        """Handle delete selected element button click."""
        self.design_canvas.remove_selected()
        self.unsaved_changes = True
        self.render_zpl()
    
    def on_canvas_draw(self, widget, context):
        """Canvas draw event handler - re-render when canvas changes."""
        # This is called when the canvas is drawn, we can use it to trigger re-rendering
        # Actually, we'll trigger on button releases and element additions
        pass
    
    def on_element_double_clicked(self, widget, element):
        """Handle double-click on canvas element for editing."""
        from zpl_designer import TextElement, BarcodeElement, BoxElement
        
        if isinstance(element, TextElement):
            # Show text edit dialog
            dialog = Gtk.Dialog(title="Edit Text", parent=self, flags=0)
            dialog.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                              Gtk.STOCK_OK, Gtk.ResponseType.OK)
            
            content = dialog.get_content_area()
            
            # Text input
            label = Gtk.Label(label="Text:")
            content.pack_start(label, False, False, 0)
            text_entry = Gtk.Entry()
            text_entry.set_text(element.text)
            content.pack_start(text_entry, False, False, 0)
            
            # Font height
            height_label = Gtk.Label(label="Font Height:")
            content.pack_start(height_label, False, False, 0)
            height_spin = Gtk.SpinButton()
            height_adj = Gtk.Adjustment(value=element.font_height, lower=8, upper=100, step_increment=1)
            height_spin.set_adjustment(height_adj)
            content.pack_start(height_spin, False, False, 0)
            
            # Font width
            width_label = Gtk.Label(label="Font Width:")
            content.pack_start(width_label, False, False, 0)
            width_spin = Gtk.SpinButton()
            width_adj = Gtk.Adjustment(value=element.font_width, lower=8, upper=100, step_increment=1)
            width_spin.set_adjustment(width_adj)
            content.pack_start(width_spin, False, False, 0)
            
            content.show_all()
            
            response = dialog.run()
            if response == Gtk.ResponseType.OK:
                element.text = text_entry.get_text()
                element.font_height = int(height_spin.get_value())
                element.font_width = int(width_spin.get_value())
                element.width = len(element.text) * element.font_width
                element.height = element.font_height
                self.unsaved_changes = True
                self.design_canvas.queue_draw()
                self.render_zpl()
            
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
                element.height = int(height_spin.get_value())
                self.unsaved_changes = True
                self.design_canvas.queue_draw()
                self.render_zpl()
            
            dialog.destroy()
        
        elif isinstance(element, BoxElement):
            # Show box edit dialog
            dialog = Gtk.Dialog(title="Edit Box", parent=self, flags=0)
            dialog.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                              Gtk.STOCK_OK, Gtk.ResponseType.OK)
            
            content = dialog.get_content_area()
            
            # Box width
            width_label = Gtk.Label(label="Width:")
            content.pack_start(width_label, False, False, 0)
            width_spin = Gtk.SpinButton()
            width_adj = Gtk.Adjustment(value=element.width, lower=10, upper=800, step_increment=1)
            width_spin.set_adjustment(width_adj)
            content.pack_start(width_spin, False, False, 0)
            
            # Box height
            height_label = Gtk.Label(label="Height:")
            content.pack_start(height_label, False, False, 0)
            height_spin = Gtk.SpinButton()
            height_adj = Gtk.Adjustment(value=element.height, lower=10, upper=1200, step_increment=1)
            height_spin.set_adjustment(height_adj)
            content.pack_start(height_spin, False, False, 0)
            
            # Box thickness
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
                self.unsaved_changes = True
                self.design_canvas.queue_draw()
                self.render_zpl()
            
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
