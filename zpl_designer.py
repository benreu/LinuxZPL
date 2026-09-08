"""
ZPL Designer - Visual element designer for creating ZPL layouts

Provides a canvas-based drag-and-drop designer for creating ZPL labels.
"""

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, Gdk, GdkPixbuf, GObject
import cairo
from dataclasses import dataclass
from typing import List, Optional, Tuple
import re
import time
from code128 import encode_b as _code128_modules
import zpl_fonts
from PIL import Image as PILImage, ImageDraw as PILImageDraw, ImageFont as PILImageFont
import io as _io
import base64 as _b64


@dataclass
class DesignElement:
    """Base class for design elements."""
    x: int
    y: int
    width: int
    height: int
    element_type: str  # 'text', 'frame', 'barcode'

    # Unannotated on purpose: keeps it out of the dataclass fields, so every
    # element class inherits the default without touching their __init__.
    print_enabled = True
    
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
        self.font_path: Optional[str] = None
        self.font_family: Optional[str] = None
        self.printer_font_name: Optional[str] = None
    
    def _measure(self, font_path: str) -> float:
        """Advance width of the text at em = font_height, or 0 if unmeasurable."""
        try:
            font = PILImageFont.truetype(font_path, max(1, self.font_height))
            draw = PILImageDraw.Draw(PILImage.new('RGBA', (1, 1)))
            return draw.textlength(self.text or " ", font=font)
        except Exception:
            return 0.0

    def printed_width(self, default_font_path: Optional[str] = None) -> int:
        """Width in dots this text will actually occupy on the printer.

        ^AF selects Zebra's built-in font A, which is fixed width, so
        len(text) * font_width holds. ^A@ selects a downloaded TrueType, which
        is proportional - every glyph has its own advance - so the string has
        to be measured. Assuming fixed width there is what made "IIII" print
        far narrower and "WWWW" far wider than the designer showed.
        """
        font_path = self.font_path or default_font_path
        natural = self._measure(font_path) if font_path else 0.0
        if natural <= 0:
            return max(1, len(self.text) * self.font_width)
        # The printer scales the em square to font_width x font_height, so an
        # advance measured at font_height scales by font_width / font_height.
        return max(1, round(natural * self.font_width / max(1, self.font_height)))

    def font_width_for(self, target_width: int,
                       default_font_path: Optional[str] = None) -> int:
        """The font_width that makes this text print target_width dots wide."""
        font_path = self.font_path or default_font_path
        natural = self._measure(font_path) if font_path else 0.0
        if natural <= 0:
            return max(1, round(target_width / max(1, len(self.text))))
        return max(1, round(target_width * max(1, self.font_height) / natural))

    def to_zpl(self, printer_font_name: Optional[str] = None) -> str:
        """Convert to ZPL commands."""
        effective_font = self.printer_font_name or printer_font_name
        zpl = f"^FO{self.x},{self.y}\n"
        if effective_font:
            zpl += f"^A@N,{self.font_height},{self.font_width},E:{effective_font}.TTF\n"
        else:
            zpl += f"^AFN,{self.font_height},{self.font_width}\n"
        zpl += f"^FD{self.text}^FS\n"
        return zpl


class FrameElement(DesignElement):
    """Frame element for the designer."""
    
    def __init__(self, x: int = 100, y: int = 100, width: int = 200, height: int = 150, thickness: int = 2):
        self.x = x
        self.y = y
        self.width = width
        self.height = height
        self.thickness = thickness
        self.element_type = 'frame'
    
    def to_zpl(self) -> str:
        """Convert to ZPL commands."""
        return f"^FO{self.x},{self.y}\n^GB{self.width},{self.height},{self.thickness}\n^FS\n"


class BarcodeElement(DesignElement):
    """Barcode element for the designer."""
    
    def __init__(self, x: int = 50, y: int = 200, height: int = 100, barcode_value: str = "123456789"):
        self.x = x
        self.y = y
        self.height = height
        self.barcode_value = barcode_value
        self.width = (35 + len(barcode_value) * 11) * 2
        self.element_type = 'barcode'
    
    def to_zpl(self) -> str:
        """Convert to ZPL commands."""
        return f"^FO{self.x},{self.y}\n^BC,{self.height}\n^FD{self.barcode_value}^FS\n"


class ImageElement(DesignElement):
    """Image element for the designer, rendered from a JPG/PNG file."""

    def __init__(self, x: int = 50, y: int = 50, width: int = 200, height: int = 200,
                 image_path: str = "", _pil_image=None):
        self.x = x
        self.y = y
        self.width = width
        self.height = height
        self.image_path = image_path
        self.element_type = 'image'
        self._pil_image = _pil_image  # set when element is decoded from ZPL data
        self._source_image = None     # cached decode of the original
        self._sized_cache = None      # ((w, h), resized image)
        self._pixbuf_cache = None     # ((w, h), 1-bit GdkPixbuf)

    def reload(self):
        """Drop the cached renderings after the source image changes."""
        self._source_image = None
        self._sized_cache = None
        self._pixbuf_cache = None

    def _get_source_image(self):
        """Decoded source image, cached so redraws do not re-read the file."""
        if self._source_image is None:
            try:
                if self.image_path:
                    img = PILImage.open(self.image_path)
                    img.load()
                elif self._pil_image is not None:
                    img = self._pil_image
                else:
                    return None
                if img.mode not in ('RGB', 'L'):
                    img = img.convert('RGB')
            except Exception:
                return None
            self._source_image = img
        return self._source_image

    def _get_sized_image(self):
        """Source resized to the element size - the resolution the printer gets."""
        key = (self.width, self.height)
        if self._sized_cache is not None and self._sized_cache[0] == key:
            return self._sized_cache[1]
        src = self._get_source_image()
        if src is None:
            return None
        sized = src.resize((max(1, self.width), max(1, self.height)), PILImage.LANCZOS)
        self._sized_cache = (key, sized)
        return sized

    def get_print_bitmap(self):
        """The exact 1-bit bitmap the printer receives (Floyd-Steinberg dithered)."""
        sized = self._get_sized_image()
        if sized is None:
            return None
        return sized.convert('1')

    def get_print_pixbuf(self) -> Optional[GdkPixbuf.Pixbuf]:
        """get_print_bitmap() as a GdkPixbuf, so the canvas shows what prints."""
        key = (self.width, self.height)
        if self._pixbuf_cache is not None and self._pixbuf_cache[0] == key:
            return self._pixbuf_cache[1]
        bitmap = self.get_print_bitmap()
        if bitmap is None:
            return None
        try:
            buf = _io.BytesIO()
            # White becomes transparent so only black dots are painted, the way
            # the printer composites. An opaque image would hide elements
            # underneath on screen that still print on paper.
            rgba = bitmap.convert('L').convert('RGBA')
            alpha = bitmap.convert('L').point(lambda v: 0 if v else 255)
            rgba.putalpha(alpha)
            rgba.save(buf, format='PNG')
            buf.seek(0)
            loader = GdkPixbuf.PixbufLoader.new_with_type('png')
            loader.write(buf.read())
            loader.close()
            pixbuf = loader.get_pixbuf()
        except Exception:
            return None
        self._pixbuf_cache = (key, pixbuf)
        return pixbuf

    def peek_print_pixbuf(self) -> Optional[GdkPixbuf.Pixbuf]:
        """Last computed pixbuf, whatever size it was, without recomputing.

        Used to keep resize drags responsive on large sources; it may be stale,
        so the caller must scale it into the element's current bounds.
        """
        return self._pixbuf_cache[1] if self._pixbuf_cache is not None else None

    def to_zpl(self) -> str:
        if not self.image_path and self._pil_image is None:
            return ""
        import numpy as np, base64

        img_sized = self._get_sized_image()
        if img_sized is None:
            return ""

        # 1-bit encoding for the ZPL printer (^GF only supports 1-bit)
        img_1bit = self.get_print_bitmap()
        bytes_per_row = (self.width + 7) // 8
        total_bytes = bytes_per_row * self.height
        arr = np.array(img_1bit, dtype=np.uint8)
        padded_w = bytes_per_row * 8
        if padded_w > self.width:
            pad = np.full((self.height, padded_w - self.width), 255, dtype=np.uint8)
            arr = np.concatenate([arr, pad], axis=1)
        arr = arr.reshape(self.height, bytes_per_row, 8)
        bits = (arr == 0).astype(np.uint8)
        weights = np.array([128, 64, 32, 16, 8, 4, 2, 1], dtype=np.uint8)
        packed = (bits * weights).sum(axis=2).astype(np.uint8)
        data = packed.tobytes().hex().upper()

        # Embed full-colour JPEG preview in a ^FX comment so the designer can
        # restore the original image quality when the ZPL file is reopened.
        # Printers ignore ^FX fields entirely.
        preview_bio = _io.BytesIO()
        img_sized.convert('RGB').save(preview_bio, format='JPEG', quality=85, optimize=True)
        b64_preview = base64.b64encode(preview_bio.getvalue()).decode('ascii')

        zpl = f"^FO{self.x},{self.y}\n"
        zpl += f"^FXDESIGNER_PREVIEW:{b64_preview}\n"
        if self.image_path:
            zpl += f"^FXDESIGNER_PATH:{self.image_path}\n"
        zpl += f"^GFA,{total_bytes},{total_bytes},{bytes_per_row},{data}\n"
        zpl += f"^FS\n"
        return zpl


class DesignCanvas(Gtk.DrawingArea):
    """Canvas widget for designing ZPL layouts with drag and drop."""
    
    __gsignals__ = {
        'element-double-clicked': (GObject.SignalFlags.RUN_FIRST, None, (object,))
    }
    
    HANDLE_SIZE = 8
    HANDLE_HALF = HANDLE_SIZE // 2

    # Cursor shown while hovering each resize handle
    HANDLE_CURSORS = {
        'tl': 'nw-resize', 'tm': 'n-resize', 'tr': 'ne-resize',
        'ml': 'w-resize',                    'mr': 'e-resize',
        'bl': 'sw-resize', 'bm': 's-resize', 'br': 'se-resize',
    }
    
    def __init__(self, on_change_callback=None, label_width: int = 812, label_height: int = 1218):
        super().__init__()
        self.set_size_request(600, 800)
        
        self.elements: List[DesignElement] = []
        self.selected_element: Optional[DesignElement] = None
        self.drag_start: Optional[Tuple[int, int]] = None
        self.on_change_callback = on_change_callback
        self.last_click_time = 0
        self.last_click_element = None
        self.active_handle: Optional[str] = None  # Track which handle is being dragged
        self._cursor_name: Optional[str] = None   # cursor currently set on the window
        self._cursor_cache = {}
        
        # Label size constraints (in pixels, default 4x6 inch at 203 DPI)
        self.label_width = label_width
        self.label_height = label_height

        self.font_path: Optional[str] = None
        self.font_family: Optional[str] = None
        self.printer_font_name: Optional[str] = None

        # Set up event handlers
        self.connect("draw", self.on_draw)
        self.connect("button-press-event", self.on_button_press)
        self.connect("button-release-event", self.on_button_release)
        self.connect("motion-notify-event", self.on_motion)
        self.connect("leave-notify-event", self.on_leave)
        
        # Enable mouse events
        self.set_events(Gdk.EventMask.BUTTON_PRESS_MASK | 
                       Gdk.EventMask.BUTTON_RELEASE_MASK | 
                       Gdk.EventMask.POINTER_MOTION_MASK |
                       Gdk.EventMask.LEAVE_NOTIFY_MASK)
    
    def add_text_element(self, text: str = "New Text"):
        """Add a text element to the canvas."""
        element = TextElement(50 + len(self.elements) * 10, 50 + len(self.elements) * 10, text)
        self.elements.append(element)
        self.selected_element = element
        self.queue_draw()
        if self.on_change_callback:
            self.on_change_callback()
        return element
    
    def add_frame_element(self):
        """Add a frame element to the canvas."""
        element = FrameElement(100 + len(self.elements) * 20, 100 + len(self.elements) * 20)
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

    def add_image_element(self, image_path: str):
        """Add an image element loaded from a file."""
        offset = len(self.elements) * 20
        element = ImageElement(50 + offset, 50 + offset, 200, 200, image_path)
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

    def bring_forward(self):
        """Move selected element one step forward (toward top)."""
        if not self.selected_element:
            return
        idx = self.elements.index(self.selected_element)
        if idx < len(self.elements) - 1:
            self.elements[idx], self.elements[idx + 1] = self.elements[idx + 1], self.elements[idx]
            self.queue_draw()
            if self.on_change_callback:
                self.on_change_callback()

    def send_backward(self):
        """Move selected element one step backward (toward bottom)."""
        if not self.selected_element:
            return
        idx = self.elements.index(self.selected_element)
        if idx > 0:
            self.elements[idx], self.elements[idx - 1] = self.elements[idx - 1], self.elements[idx]
            self.queue_draw()
            if self.on_change_callback:
                self.on_change_callback()

    def bring_to_front(self):
        """Move selected element to the top."""
        if not self.selected_element:
            return
        self.elements.remove(self.selected_element)
        self.elements.append(self.selected_element)
        self.queue_draw()
        if self.on_change_callback:
            self.on_change_callback()

    def send_to_back(self):
        """Move selected element to the bottom."""
        if not self.selected_element:
            return
        self.elements.remove(self.selected_element)
        self.elements.insert(0, self.selected_element)
        self.queue_draw()
        if self.on_change_callback:
            self.on_change_callback()
    
    def clear(self):
        """Clear all elements from the canvas."""
        self.elements.clear()
        self.selected_element = None
        self.queue_draw()
    
    def sync_text_width(self, element) -> None:
        """Resize a text element's box to the width it will print at."""
        if getattr(element, 'element_type', None) == 'text':
            element.width = element.printed_width(self.font_path)
            element.height = element.font_height

    def set_font(self, font_path: str, font_family: str, printer_font_name: str):
        self.font_path = font_path
        self.font_family = font_family
        self.printer_font_name = printer_font_name
        zpl_fonts.register_app_font(font_path)
        for el in self.elements:
            self.sync_text_width(el)
        self.queue_draw()

    def set_element_font(self, element: 'TextElement', font_path: str, font_family: str, printer_font_name: str):
        element.font_path = font_path
        element.font_family = font_family
        element.printer_font_name = printer_font_name
        zpl_fonts.register_app_font(font_path)
        self.sync_text_width(element)
        self.queue_draw()

    def to_zpl(self) -> str:
        """Generate ZPL code from canvas elements with label size settings."""
        zpl = "^XA\n"
        zpl += f"^PW{self.label_width}\n"
        zpl += f"^LL{self.label_height}\n"
        for element in self.elements:
            if self.printer_font_name and element.element_type == 'text':
                body = element.to_zpl(printer_font_name=self.printer_font_name)
            else:
                body = element.to_zpl()
            if element.print_enabled:
                zpl += body
            elif body:
                # ^FX comments only until the NEXT CARET, so the body has to be
                # base64'd - inlining it raw would leave its ^FO/^FD to execute
                # and print anyway, which is the whole bug being fixed here.
                blob = _b64.b64encode(body.encode('utf-8')).decode('ascii')
                zpl += f"^FXDESIGNER_NOPRINT:{blob}\n"
        zpl += "^XZ"
        return zpl
    
    def set_label_size(self, width: int, height: int):
        """Set the label size and update constraints."""
        self.label_width = width
        self.label_height = height
        # Clamp existing elements to new bounds
        self._clamp_elements_to_bounds()
        self.queue_draw()
    
    def get_label_size(self) -> tuple:
        """Get current label size as (width, height)."""
        return (self.label_width, self.label_height)
    
    def _clamp_elements_to_bounds(self):
        """Ensure all elements stay within label bounds."""
        for element in self.elements:
            # Clamp position
            element.x = max(0, min(element.x, self.label_width - 1))
            element.y = max(0, min(element.y, self.label_height - 1))
            # Clamp size
            element.width = min(element.width, self.label_width - element.x)
            element.height = min(element.height, self.label_height - element.y)
    
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

        # Clamp to label bounds
        element.x = max(0, element.x)
        element.y = max(0, element.y)
        element.x = min(element.x, self.label_width - element.width)
        element.y = min(element.y, self.label_height - element.height)
        # Ensure element doesn't exceed label bounds
        if element.x + element.width > self.label_width:
            element.width = self.label_width - element.x
        if element.y + element.height > self.label_height:
            element.height = self.label_height - element.y

        # Sync font dimensions for text elements
        if element.element_type == 'text':
            element.font_height = element.height
            element.font_width = element.font_width_for(element.width, self.font_path)
            # Snap the box to what will actually print, so the outline the user
            # drags is the outline that comes out of the printer.
            element.width = element.printed_width(self.font_path)
    
    def _get_scale_factor(self) -> float:
        allocation = self.get_allocation()
        if allocation.width > 0 and self.label_width > 0:
            return allocation.width / self.label_width
        return 1.0

    def _screen_to_label(self, x: float, y: float) -> Tuple[int, int]:
        s = self._get_scale_factor()
        return int(x / s), int(y / s)

    def on_draw(self, widget, context):
        """Draw the canvas and elements."""
        # Draw white background
        context.set_source_rgb(1, 1, 1)
        context.paint()

        # Determine scale to map label coordinates -> display coordinates
        scale_factor = self._get_scale_factor()

        # Apply uniform scaling so all drawing uses label-space coordinates
        context.save()
        context.scale(scale_factor, scale_factor)

        # Draw label boundary (light gray dashed line) in label coordinates
        context.set_source_rgb(0.8, 0.8, 0.8)
        context.set_line_width(1.0 / max(1e-6, scale_factor))
        context.set_dash([5, 5], 0)
        context.rectangle(0, 0, self.label_width, self.label_height)
        context.stroke()
        context.set_dash([], 0)

        # Draw elements in label coordinates (context is scaled)
        for element in self.elements:
            selected = element == self.selected_element
            if element.print_enabled:
                self._draw_element(context, element, selected)
            else:
                # Dim it so the canvas shows what the file contains, while
                # keeping the element selectable and draggable.
                context.push_group()
                self._draw_element(context, element, selected)
                context.pop_group_to_source()
                context.paint_with_alpha(0.35)
            
        context.restore()
    
    def _draw_element(self, context, element: DesignElement, selected: bool):
        """Draw a single element."""
        if element.element_type == 'text':
            self._draw_text_element(context, element, selected)
        elif element.element_type == 'frame':
            self._draw_frame_element(context, element, selected)
        elif element.element_type == 'barcode':
            self._draw_barcode_element(context, element, selected)
        elif element.element_type == 'image':
            self._draw_image_element(context, element, selected)
    
    def _render_text_pil(self, context, element, font_path: str) -> bool:
        """Render element text using PIL and blit onto the Cairo context. Returns True on success."""
        try:
            pil_font = PILImageFont.truetype(font_path, element.font_height)
        except Exception:
            return False

        text = element.text[:20] or " "
        tmp = PILImage.new('RGBA', (1, 1))
        bbox = PILImageDraw.Draw(tmp).textbbox((0, 0), text, font=pil_font)
        text_w = max(1, bbox[2] - bbox[0])
        text_h = max(1, bbox[3] - bbox[1])

        img = PILImage.new('RGBA', (text_w + 4, text_h + 4), (0, 0, 0, 0))
        PILImageDraw.Draw(img).text((2 - bbox[0], 2 - bbox[1]), text, fill=(0, 0, 0, 255), font=pil_font)

        # Convert PIL → GdkPixbuf via PNG
        buf = _io.BytesIO()
        img.save(buf, format='PNG')
        buf.seek(0)
        loader = GdkPixbuf.PixbufLoader.new_with_type('png')
        loader.write(buf.read())
        loader.close()
        pixbuf = loader.get_pixbuf()
        if not pixbuf:
            return False

        # The printer scales the em square to font_width x font_height. Stretching
        # to fill element.width instead would make the text always look like it
        # fits, hiding any difference from what actually prints.
        h_scale = element.font_width / max(1, element.font_height)
        context.save()
        context.translate(element.x + 2, element.y)
        context.scale(h_scale, 1.0)
        Gdk.cairo_set_source_pixbuf(context, pixbuf, 0, 0)
        context.paint()
        context.restore()
        return True

    def _draw_text_element(self, context, element, selected: bool):
        """Draw a text element."""
        # Draw text background (translucent: it is a designer affordance, and
        # must not hide anything underneath that will still print)
        context.set_source_rgba(0.95, 0.95, 1, 0.35)
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
        
        # Draw text using PIL when a custom font is set, otherwise Cairo toy font
        context.set_source_rgb(0, 0, 0)
        font_path = element.font_path or self.font_path
        pil_rendered = False
        if font_path:
            pil_rendered = self._render_text_pil(context, element, font_path)

        if not pil_rendered:
            context.select_font_face(element.font_family or self.font_family or "monospace")
            context.set_font_size(element.font_height)
            extents = context.text_extents(element.text[:20])
            if font_path:
                horizontal_scale = element.font_width / max(1, element.font_height)
            else:
                # No downloaded font means ^AF, i.e. Zebra's built-in font A,
                # which is fixed width: every character occupies font_width dots
                # so the text spans the whole box. Stretch the proportional
                # screen face to match rather than leaving a gap.
                measured = extents.width if extents.width > 0 else 1.0
                horizontal_scale = element.width / measured
            context.save()
            context.translate(element.x + 2, element.y + element.font_height - 2)
            context.scale(horizontal_scale, 1.0)
            context.show_text(element.text[:20])
            context.restore()
        
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
    
    def _draw_frame_element(self, context, element, selected: bool):
        """Draw a frame element."""
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
        # White background
        context.set_source_rgb(1, 1, 1)
        context.rectangle(element.x, element.y, element.width, element.height)
        context.fill()

        # Draw Code 128B bars
        mods = _code128_modules(element.barcode_value)
        mod_w = element.width / sum(mods)
        context.set_source_rgb(0, 0, 0)
        cx = element.x
        for i, m in enumerate(mods):
            if i % 2 == 0:  # bars are at even indices
                context.rectangle(cx, element.y, m * mod_w, element.height)
                context.fill()
            cx += m * mod_w

        # Barcode value text below the bars
        scale = self._get_scale_factor()
        font_size = max(8, 14 / scale)
        context.set_source_rgb(0, 0, 0)
        context.select_font_face("sans-serif", 0, 0)
        context.set_font_size(font_size)
        text_y = element.y + element.height + font_size
        extents = context.text_extents(element.barcode_value)
        text_x = element.x + (element.width - extents.width) / 2
        context.move_to(text_x, text_y)
        context.show_text(element.barcode_value)

        # Selection border
        if selected:
            context.set_source_rgb(0, 0.7, 0)
            context.set_line_width(2 / scale)
        else:
            context.set_source_rgb(0.4, 0.4, 0.4)
            context.set_line_width(1 / scale)
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
    
    def _draw_image_element(self, context, element, selected: bool):
        """Draw an image element exactly as it will print (1-bit, dithered)."""
        # Re-dithering a large photo costs ~100ms, so while a resize handle is
        # being dragged reuse the last bitmap stretched to the new bounds; the
        # exact one is regenerated on release.
        pixbuf = None
        if self.active_handle is not None and element is self.selected_element:
            pixbuf = element.peek_print_pixbuf()
        if pixbuf is None:
            pixbuf = element.get_print_pixbuf()
        if pixbuf:
            # The pixbuf is at label resolution, the same coordinate space this
            # context is scaled into, so normally this scale is 1:1. GOOD
            # filtering averages the dither dots down to the canvas the way the
            # eye does looking at a real printed label.
            context.save()
            context.translate(element.x, element.y)
            context.scale(element.width / pixbuf.get_width(),
                          element.height / pixbuf.get_height())
            Gdk.cairo_set_source_pixbuf(context, pixbuf, 0, 0)
            context.get_source().set_filter(cairo.Filter.GOOD)
            context.paint()
            context.restore()
        else:
            context.set_source_rgb(0.85, 0.85, 0.85)
            context.rectangle(element.x, element.y, element.width, element.height)
            context.fill()
            context.set_source_rgb(0.5, 0.5, 0.5)
            context.select_font_face("sans")
            context.set_font_size(14)
            context.move_to(element.x + 5, element.y + element.height / 2)
            context.show_text("[No Image]")

        if selected:
            context.set_source_rgb(0, 0, 1)
            context.set_line_width(2)
        else:
            context.set_source_rgb(0.3, 0.3, 0.3)
            context.set_line_width(1)
        context.rectangle(element.x, element.y, element.width, element.height)
        context.stroke()

        if selected:
            handles = self._get_handles(element)
            for _, (hx, hy) in handles.items():
                context.set_source_rgb(0, 0.5, 1)
                context.rectangle(hx - self.HANDLE_HALF, hy - self.HANDLE_HALF,
                                   self.HANDLE_SIZE, self.HANDLE_SIZE)
                context.fill()
                context.set_source_rgb(0, 0, 1)
                context.set_line_width(1)
                context.rectangle(hx - self.HANDLE_HALF, hy - self.HANDLE_HALF,
                                   self.HANDLE_SIZE, self.HANDLE_SIZE)
                context.stroke()

    def _show_context_menu(self, event, element):
        """Show right-click context menu for element reordering."""
        menu = Gtk.Menu()

        item_print = Gtk.CheckMenuItem(label="Print This Element")
        item_print.set_active(element.print_enabled)

        def on_toggle_print(item):
            element.print_enabled = item.get_active()
            self.queue_draw()
            if self.on_change_callback:
                self.on_change_callback()

        item_print.connect("toggled", on_toggle_print)
        menu.append(item_print)
        menu.append(Gtk.SeparatorMenuItem())

        item_front = Gtk.MenuItem(label="Bring to Front")
        item_front.connect("activate", lambda _: self.bring_to_front())
        menu.append(item_front)

        item_forward = Gtk.MenuItem(label="Bring Forward")
        item_forward.connect("activate", lambda _: self.bring_forward())
        menu.append(item_forward)

        item_backward = Gtk.MenuItem(label="Send Backward")
        item_backward.connect("activate", lambda _: self.send_backward())
        menu.append(item_backward)

        item_back = Gtk.MenuItem(label="Send to Back")
        item_back.connect("activate", lambda _: self.send_to_back())
        menu.append(item_back)

        idx = self.elements.index(element)
        item_front.set_sensitive(idx < len(self.elements) - 1)
        item_forward.set_sensitive(idx < len(self.elements) - 1)
        item_backward.set_sensitive(idx > 0)
        item_back.set_sensitive(idx > 0)

        menu.show_all()
        menu.popup_at_pointer(event)

    def on_button_press(self, widget, event):
        """Handle mouse button press for element selection and double-click detection."""
        lx, ly = self._screen_to_label(event.x, event.y)

        if event.button == 3:
            clicked_element = None
            for element in reversed(self.elements):
                if element.contains_point(lx, ly):
                    clicked_element = element
                    break
            if clicked_element:
                self.selected_element = clicked_element
                self.queue_draw()
                self._show_context_menu(event, clicked_element)
            return

        if event.button != 1:
            return

        # Reset active handle for new click
        self.active_handle = None


        # Check if clicking on a resize handle of the selected element
        if self.selected_element:
            handle = self._get_handle_at_point(lx, ly, self.selected_element)
            if handle:
                self.active_handle = handle
                self.drag_start = (lx, ly)
                return

        # Find element at click position
        clicked_element = None
        for element in reversed(self.elements):
            if element.contains_point(lx, ly):
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
            self.drag_start = (lx, ly)
        self.queue_draw()
    
    def on_button_release(self, widget, event):
        """Handle mouse button release."""
        if event.button == 1:
            self.drag_start = None
            self.active_handle = None
    
    def _set_cursor(self, name: Optional[str]):
        """Set the window cursor by CSS name, or None for the default."""
        if name == self._cursor_name:
            return
        self._cursor_name = name
        window = self.get_window()
        if window is None:
            return
        if name is not None and name not in self._cursor_cache:
            self._cursor_cache[name] = Gdk.Cursor.new_from_name(self.get_display(), name)
        window.set_cursor(self._cursor_cache.get(name) if name else None)

    def _update_cursor(self, event):
        """Show a directional resize cursor over the selected element's handles."""
        if self.active_handle:
            self._set_cursor(self.HANDLE_CURSORS.get(self.active_handle))
            return
        name = None
        if self.selected_element:
            lx, ly = self._screen_to_label(event.x, event.y)
            handle = self._get_handle_at_point(lx, ly, self.selected_element)
            if handle:
                name = self.HANDLE_CURSORS.get(handle)
        self._set_cursor(name)

    def on_leave(self, widget, event):
        """Restore the default cursor when the pointer leaves the canvas."""
        if not self.active_handle:
            self._set_cursor(None)

    def on_motion(self, widget, event):
        """Handle mouse motion for dragging elements or resizing."""
        self._update_cursor(event)

        if not self.drag_start or not self.selected_element:
            return
        
        # Calculate movement in label coordinates
        lx, ly = self._screen_to_label(event.x, event.y)
        dx = lx - self.drag_start[0]
        dy = ly - self.drag_start[1]
        
        # If a handle is active, resize instead of move
        if self.active_handle:
            self._resize_element_by_handle(self.selected_element, self.active_handle, dx, dy)
        else:
            # Update element position (regular drag)
            self.selected_element.x += dx
            self.selected_element.y += dy
            
            # Clamp to label bounds
            self.selected_element.x = max(0, self.selected_element.x)
            self.selected_element.y = max(0, self.selected_element.y)
            # Ensure element stays within label boundaries
            self.selected_element.x = min(self.selected_element.x, self.label_width - self.selected_element.width)
            self.selected_element.y = min(self.selected_element.y, self.label_height - self.selected_element.height)
        
        # Update drag start for next movement (always update)
        self.drag_start = (lx, ly)
        
        self.queue_draw()
        if self.on_change_callback:
            self.on_change_callback()
