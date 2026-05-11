"""
ZPL Designer - Visual element designer for creating ZPL layouts

Provides a canvas-based drag-and-drop designer for creating ZPL labels.
"""

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, Gdk, GdkPixbuf, GObject
from dataclasses import dataclass
from typing import List, Optional, Tuple
import re
import time


@dataclass
class DesignElement:
    """Base class for design elements."""
    x: int
    y: int
    width: int
    height: int
    element_type: str  # 'text', 'box', 'barcode'
    
    def contains_point(self, x: int, y: int) -> bool:
        """Check if point is within element bounds."""
        return (self.x <= x <= self.x + self.width and 
                self.y <= y <= self.y + self.height)


class TextElement(DesignElement):
    """Text element for the designer."""
    
    def __init__(self, x: int = 50, y: int = 50, text: str = "Label", 
                 font_height: int = 36, font_width: int = 20):
        self.x = x
        self.y = y
        self.text = text
        self.font_height = font_height
        self.font_width = font_width
        self.width = len(text) * font_width
        self.height = font_height
        self.element_type = 'text'
    
    def to_zpl(self) -> str:
        """Convert to ZPL commands."""
        zpl = f"^FO{self.x},{self.y}\n"
        zpl += f"^AFN,{self.font_height},{self.font_width}\n"
        zpl += f"^FD{self.text}^FS\n"
        return zpl


class BoxElement(DesignElement):
    """Box/rectangle element for the designer."""
    
    def __init__(self, x: int = 100, y: int = 100, width: int = 200, height: int = 150, thickness: int = 2):
        self.x = x
        self.y = y
        self.width = width
        self.height = height
        self.thickness = thickness
        self.element_type = 'box'
    
    def to_zpl(self) -> str:
        """Convert to ZPL commands."""
        return f"^FO{self.x},{self.y}\n^GB{self.width},{self.height},{self.thickness}\n"


class BarcodeElement(DesignElement):
    """Barcode element for the designer."""
    
    def __init__(self, x: int = 50, y: int = 200, height: int = 100, barcode_value: str = "123456789"):
        self.x = x
        self.y = y
        self.height = height
        self.width = 100  # Placeholder
        self.barcode_value = barcode_value
        self.element_type = 'barcode'
    
    def to_zpl(self) -> str:
        """Convert to ZPL commands."""
        return f"^FO{self.x},{self.y}\n^BC,{self.height}\n^FD{self.barcode_value}^FS\n"


class DesignCanvas(Gtk.DrawingArea):
    """Canvas widget for designing ZPL layouts with drag and drop."""
    
    __gsignals__ = {
        'element-double-clicked': (GObject.SignalFlags.RUN_FIRST, None, (object,))
    }
    
    HANDLE_SIZE = 8
    HANDLE_HALF = HANDLE_SIZE // 2
    
    def __init__(self, on_change_callback=None):
        super().__init__()
        self.set_size_request(600, 800)
        
        self.elements: List[DesignElement] = []
        self.selected_element: Optional[DesignElement] = None
        self.drag_start: Optional[Tuple[int, int]] = None
        self.on_change_callback = on_change_callback
        self.last_click_time = 0
        self.last_click_element = None
        self.active_handle: Optional[str] = None  # Track which handle is being dragged
        
        
        # Set up event handlers
        self.connect("draw", self.on_draw)
        self.connect("button-press-event", self.on_button_press)
        self.connect("button-release-event", self.on_button_release)
        self.connect("motion-notify-event", self.on_motion)
        
        # Enable mouse events
        self.set_events(Gdk.EventMask.BUTTON_PRESS_MASK | 
                       Gdk.EventMask.BUTTON_RELEASE_MASK | 
                       Gdk.EventMask.POINTER_MOTION_MASK)
    
    def add_text_element(self, text: str = "New Text"):
        """Add a text element to the canvas."""
        element = TextElement(50 + len(self.elements) * 10, 50 + len(self.elements) * 10, text)
        self.elements.append(element)
        self.selected_element = element
        self.queue_draw()
        if self.on_change_callback:
            self.on_change_callback()
        return element
    
    def add_box_element(self):
        """Add a box element to the canvas."""
        element = BoxElement(100 + len(self.elements) * 20, 100 + len(self.elements) * 20)
        self.elements.append(element)
        self.selected_element = element
        self.queue_draw()
        if self.on_change_callback:
            self.on_change_callback()
        return element
    
    def add_barcode_element(self):
        """Add a barcode element to the canvas."""
        element = BarcodeElement(50 + len(self.elements) * 20, 250 + len(self.elements) * 20)
        self.elements.append(element)
        self.selected_element = element
        self.queue_draw()
        if self.on_change_callback:
            self.on_change_callback()
        return element
    
    def remove_selected(self):
        """Remove the selected element."""
        if self.selected_element and self.selected_element in self.elements:
            self.elements.remove(self.selected_element)
            self.selected_element = None
            self.queue_draw()
            if self.on_change_callback:
                self.on_change_callback()
    
    def clear(self):
        """Clear all elements from the canvas."""
        self.elements.clear()
        self.selected_element = None
        self.queue_draw()
    
    def to_zpl(self) -> str:
        """Generate ZPL code from canvas elements."""
        zpl = "^XA\n"
        for element in self.elements:
            zpl += element.to_zpl()
        zpl += "^XZ"
        return zpl
    
    def _get_handles(self, element: DesignElement) -> dict:
        """Get the positions of resize handles for any element type."""
        x, y = element.x, element.y
        w, h = element.width, element.height
        
        return {
            'tl': (x, y),                          # top-left
            'tm': (x + w // 2, y),                # top-middle
            'tr': (x + w, y),                      # top-right
            'ml': (x, y + h // 2),                # middle-left
            'mr': (x + w, y + h // 2),            # middle-right
            'bl': (x, y + h),                      # bottom-left
            'bm': (x + w // 2, y + h),            # bottom-middle
            'br': (x + w, y + h),                 # bottom-right
        }
    
    def _get_handle_at_point(self, x: int, y: int, element: DesignElement) -> Optional[str]:
        """Check if a resize handle is at the given point."""
        handles = self._get_handles(element)
        for handle_name, (hx, hy) in handles.items():
            if (abs(x - hx) <= self.HANDLE_SIZE and 
                abs(y - hy) <= self.HANDLE_SIZE):
                return handle_name
        return None
    
    def _resize_element_by_handle(self, element: DesignElement, handle: str, 
                                   dx: int, dy: int):
        """Resize an element based on which handle is being dragged."""
        if handle in ('tl', 'tm', 'tr'):  # Top handles - adjust y and height
            element.y += dy
            element.height -= dy
        
        if handle in ('bl', 'bm', 'br'):  # Bottom handles - adjust height
            element.height += dy
        
        if handle in ('tl', 'ml', 'bl'):  # Left handles - adjust x and width
            element.x += dx
            element.width -= dx
        
        if handle in ('tr', 'mr', 'br'):  # Right handles - adjust width
            element.width += dx
        
        # Ensure minimum size
        element.width = max(20, element.width)
        element.height = max(20, element.height)
    
    def on_draw(self, widget, context):
        """Draw the canvas and elements."""
        # Draw white background
        context.set_source_rgb(1, 1, 1)
        context.paint()
        
        # Draw elements
        for element in self.elements:
            selected = element == self.selected_element
            self._draw_element(context, element, selected)
    
    
    def _draw_element(self, context, element: DesignElement, selected: bool):
        """Draw a single element."""
        if element.element_type == 'text':
            self._draw_text_element(context, element, selected)
        elif element.element_type == 'box':
            self._draw_box_element(context, element, selected)
        elif element.element_type == 'barcode':
            self._draw_barcode_element(context, element, selected)
    
    def _draw_text_element(self, context, element, selected: bool):
        """Draw a text element."""
        # Draw text background
        context.set_source_rgb(0.95, 0.95, 1)
        context.rectangle(element.x, element.y, element.width, element.height)
        context.fill()
        
        # Draw border
        if selected:
            context.set_source_rgb(0, 0, 1)
            context.set_line_width(2)
        else:
            context.set_source_rgb(0.5, 0.5, 1)
            context.set_line_width(1)
        context.rectangle(element.x, element.y, element.width, element.height)
        context.stroke()
        
        # Draw text
        context.set_source_rgb(0, 0, 0)
        context.select_font_face("monospace")
        context.set_font_size(12)
        context.move_to(element.x + 2, element.y + 15)
        context.show_text(element.text[:20])
        
        # Draw resize handles if selected
        if selected:
            handles = self._get_handles(element)
            for handle_name, (hx, hy) in handles.items():
                # Draw handle as a small square
                context.set_source_rgb(0, 0.5, 1)
                context.rectangle(hx - self.HANDLE_HALF, hy - self.HANDLE_HALF, 
                                self.HANDLE_SIZE, self.HANDLE_SIZE)
                context.fill()
                
                # Draw handle border
                context.set_source_rgb(0, 0, 1)
                context.set_line_width(1)
                context.rectangle(hx - self.HANDLE_HALF, hy - self.HANDLE_HALF, 
                                self.HANDLE_SIZE, self.HANDLE_SIZE)
                context.stroke()
    
    def _draw_box_element(self, context, element, selected: bool):
        """Draw a box element."""
        if selected:
            context.set_source_rgb(0, 1, 0)
            context.set_line_width(3)
        else:
            context.set_source_rgb(0, 0, 0)
            context.set_line_width(element.thickness)
        
        context.rectangle(element.x, element.y, element.width, element.height)
        context.stroke()
        
        # Draw resize handles if selected
        if selected:
            handles = self._get_handles(element)
            for handle_name, (hx, hy) in handles.items():
                # Draw handle as a small square
                context.set_source_rgb(0, 0.5, 1)
                context.rectangle(hx - self.HANDLE_HALF, hy - self.HANDLE_HALF, 
                                self.HANDLE_SIZE, self.HANDLE_SIZE)
                context.fill()
                
                # Draw handle border
                context.set_source_rgb(0, 0, 1)
                context.set_line_width(1)
                context.rectangle(hx - self.HANDLE_HALF, hy - self.HANDLE_HALF, 
                                self.HANDLE_SIZE, self.HANDLE_SIZE)
                context.stroke()
    
    def _draw_barcode_element(self, context, element, selected: bool):
        """Draw a barcode element."""
        context.set_source_rgb(0.95, 1, 0.95)
        context.rectangle(element.x, element.y, element.width, element.height)
        context.fill()
        
        if selected:
            context.set_source_rgb(0, 1, 0)
            context.set_line_width(2)
        else:
            context.set_source_rgb(0.5, 1, 0.5)
            context.set_line_width(1)
        context.rectangle(element.x, element.y, element.width, element.height)
        context.stroke()
        
        # Draw barcode icon
        context.set_source_rgb(0, 0, 0)
        context.select_font_face("monospace")
        context.set_font_size(10)
        context.move_to(element.x + 5, element.y + 15)
        context.show_text("||||| CODE128")
        
        # Draw resize handles if selected
        if selected:
            handles = self._get_handles(element)
            for handle_name, (hx, hy) in handles.items():
                # Draw handle as a small square
                context.set_source_rgb(0, 0.5, 1)
                context.rectangle(hx - self.HANDLE_HALF, hy - self.HANDLE_HALF, 
                                self.HANDLE_SIZE, self.HANDLE_SIZE)
                context.fill()
                
                # Draw handle border
                context.set_source_rgb(0, 0, 1)
                context.set_line_width(1)
                context.rectangle(hx - self.HANDLE_HALF, hy - self.HANDLE_HALF, 
                                self.HANDLE_SIZE, self.HANDLE_SIZE)
                context.stroke()
    
    def on_button_press(self, widget, event):
        """Handle mouse button press for element selection and double-click detection."""
        if event.button != 1:
            return
        
        # Reset active handle for new click
        self.active_handle = None
        
        # Check if clicking on a resize handle of the selected element
        if self.selected_element:
            handle = self._get_handle_at_point(int(event.x), int(event.y), 
                                              self.selected_element)
            if handle:
                self.active_handle = handle
                self.drag_start = (int(event.x), int(event.y))
                return
        
        # Find element at click position
        clicked_element = None
        for element in reversed(self.elements):
            if element.contains_point(int(event.x), int(event.y)):
                clicked_element = element
                break
        
        # Check for double-click (within 500ms and same element)
        current_time = time.time()
        if (self.last_click_element == clicked_element and 
            clicked_element is not None and 
            (current_time - self.last_click_time) < 0.5):
            # Double-click detected
            self.emit('element-double-clicked', clicked_element)
            self.last_click_time = 0
            self.last_click_element = None
            return
        
        # Update click tracking
        self.last_click_time = current_time
        self.last_click_element = clicked_element
        
        # Single click selection
        self.selected_element = clicked_element
        if clicked_element:
            self.drag_start = (int(event.x), int(event.y))
        self.queue_draw()
    
    def on_button_release(self, widget, event):
        """Handle mouse button release."""
        if event.button == 1:
            self.drag_start = None
            self.active_handle = None
    
    def on_motion(self, widget, event):
        """Handle mouse motion for dragging elements or resizing."""
        if not self.drag_start or not self.selected_element:
            return
        
        # Calculate movement
        dx = int(event.x) - self.drag_start[0]
        dy = int(event.y) - self.drag_start[1]
        
        # If a handle is active, resize instead of move
        if self.active_handle:
            self._resize_element_by_handle(self.selected_element, self.active_handle, dx, dy)
        else:
            # Update element position (regular drag)
            self.selected_element.x += dx
            self.selected_element.y += dy
            
            # Clamp to canvas bounds
            self.selected_element.x = max(0, self.selected_element.x)
            self.selected_element.y = max(0, self.selected_element.y)
        
        # Update drag start for next movement (always update)
        self.drag_start = (int(event.x), int(event.y))
        
        self.queue_draw()
        if self.on_change_callback:
            self.on_change_callback()
