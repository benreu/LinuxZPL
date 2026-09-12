"""
The GTK design canvas: a Gtk.DrawingArea that draws the label and edits it
under the mouse.

The canvas is a proof of what the printer will produce, not an approximation of
it. Images are drawn as the 1-bit dithered bitmap the printer receives, with
white transparent, because a thermal head only adds black and cannot erase -
anything drawn opaque would hide something that still prints. Text is drawn at
the width it will really print. Designer affordances are translucent for the
same reason.

The document, the geometry of editing and the text raster live in zplcore, so
this file is only the GTK half: Cairo painting, events and cursors.
"""

import io as _io
import math
import time
from typing import List, Optional, Tuple

import cairo
import gi

gi.require_version('Gtk', '3.0')
from gi.repository import Gdk, GdkPixbuf, GObject, Gtk

from zplcore import geometry, textraster, view
from zplcore.model import (BarcodeElement, DesignElement, Document,
                           FrameElement, ImageElement, TextElement)


def to_pixbuf(pil_image) -> Optional[GdkPixbuf.Pixbuf]:
    """A PIL RGBA image as a GdkPixbuf, via PNG."""
    if pil_image is None:
        return None
    try:
        if pil_image.mode != 'RGBA':
            pil_image = pil_image.convert('RGBA')
        buf = _io.BytesIO()
        pil_image.save(buf, format='PNG')
        buf.seek(0)
        loader = GdkPixbuf.PixbufLoader.new_with_type('png')
        loader.write(buf.read())
        loader.close()
        return loader.get_pixbuf()
    except Exception:
        return None


def _rounded_path(context, x, y, width, height, radius):
    """A rectangle path, with ^GB's rounded corners when it asks for them."""
    radius = max(0.0, min(radius, min(width, height) / 2))
    if radius <= 0:
        context.rectangle(x, y, width, height)
        return
    context.new_sub_path()
    context.arc(x + width - radius, y + radius, radius, -math.pi / 2, 0)
    context.arc(x + width - radius, y + height - radius, radius, 0, math.pi / 2)
    context.arc(x + radius, y + height - radius, radius, math.pi / 2, math.pi)
    context.arc(x + radius, y + radius, radius, math.pi, 3 * math.pi / 2)
    context.close_path()


class DesignCanvas(Gtk.DrawingArea):
    """Canvas widget for designing ZPL layouts with drag and drop.

    Holds a zplcore Document and forwards the document operations to it, so the
    window can go on treating the canvas as the design while the behaviour
    itself is shared with the Qt frontend.
    """

    __gsignals__ = {
        'element-double-clicked': (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        'scale-changed': (GObject.SignalFlags.RUN_FIRST, None, ()),
        # (pointer x, pointer y, in) - a request rather than a notification.
        # Keeping the dot under the pointer needs its position measured before
        # the zoom moves everything, and the window owns the scrollbars, so the
        # window applies the whole thing.
        'zoom-at': (GObject.SignalFlags.RUN_FIRST, None, (float, float, bool)),
    }

    # Cursor shown while hovering each resize handle
    HANDLE_CURSORS = {
        'tl': 'nw-resize', 'tm': 'n-resize', 'tr': 'ne-resize',
        'ml': 'w-resize',                    'mr': 'e-resize',
        'bl': 'sw-resize', 'bm': 's-resize', 'br': 'se-resize',
    }

    def __init__(self, on_change_callback=None, label_width: int = 812,
                 label_height: int = 1218, document: Optional[Document] = None):
        super().__init__()

        self.document = document if document is not None else Document(
            label_width, label_height)

        # What the canvas is showing. `zoom` is display pixels per dot, or None
        # while a fit mode is deciding it; `fit` says which fit. The widget's
        # own size is a consequence of the scale, so the fit is measured
        # against the visible area the window reports, never against self -
        # measuring against self is a feedback loop.
        self.zoom = None
        self.fit = view.FIT_LABEL
        self._view_size = (600, 600)
        self.set_halign(Gtk.Align.CENTER)
        self.set_valign(Gtk.Align.CENTER)
        self.on_change_callback = on_change_callback
        self.drag_start: Optional[Tuple[int, int]] = None
        self._drag_changed = False                # a drag moved something
        self.last_click_time = 0
        self.last_click_element = None
        self.active_handle: Optional[str] = None  # which handle is being dragged
        # The box the element had when a resize started. A resize is measured
        # from here rather than from the previous motion event, because the box
        # snaps back to what the text or the symbol will print and a delta
        # smaller than that snap would be thrown away every event.
        self.resize_origin: Optional[dict] = None
        # A rubber band being dragged over empty canvas: where it started and
        # where the pointer is now, both in dots, and whether it adds to the
        # selection rather than replacing it.
        self.band_origin: Optional[Tuple[int, int]] = None
        self.band_now: Optional[Tuple[int, int]] = None
        self.band_additive = False
        self._cursor_name: Optional[str] = None   # cursor currently set
        self._cursor_cache = {}

        self.connect("draw", self.on_draw)
        self.connect("scroll-event", self.on_scroll)
        self.connect("button-press-event", self.on_button_press)
        self.connect("button-release-event", self.on_button_release)
        self.connect("motion-notify-event", self.on_motion)
        self.connect("leave-notify-event", self.on_leave)

        self.set_events(Gdk.EventMask.BUTTON_PRESS_MASK |
                        Gdk.EventMask.BUTTON_RELEASE_MASK |
                        Gdk.EventMask.POINTER_MOTION_MASK |
                        Gdk.EventMask.SCROLL_MASK |
                        Gdk.EventMask.SMOOTH_SCROLL_MASK |
                        Gdk.EventMask.LEAVE_NOTIFY_MASK)

    # --- the document, forwarded ---------------------------------------------
    #
    # The window still says design_canvas.elements and design_canvas.to_zpl();
    # these keep that working while the document itself lives in the core.

    @property
    def elements(self) -> List[DesignElement]:
        return self.document.elements

    @property
    def selected_element(self) -> Optional[DesignElement]:
        return self.document.selected_element

    @selected_element.setter
    def selected_element(self, element):
        self.document.selected_element = element

    @property
    def label_width(self) -> int:
        return self.document.label_width

    @label_width.setter
    def label_width(self, value):
        self.document.label_width = value

    @property
    def label_height(self) -> int:
        return self.document.label_height

    @label_height.setter
    def label_height(self, value):
        self.document.label_height = value

    @property
    def dpi(self) -> int:
        return self.document.dpi

    @dpi.setter
    def dpi(self, value):
        self.document.dpi = value

    @property
    def font_path(self):
        return self.document.font_path

    @property
    def font_family(self):
        return self.document.font_family

    @property
    def printer_font_name(self):
        return self.document.printer_font_name

    def set_document(self, document: Document):
        """Show a different document - a load, or File > New."""
        self.document = document
        self.drag_start = None
        self.active_handle = None
        self.resize_origin = None
        self.last_click_element = None
        self.band_origin = self.band_now = None
        self.sync_size()

    def _changed(self):
        """Redraw and report one change, so the window records one undo entry."""
        self.queue_draw()
        if self.on_change_callback:
            self.on_change_callback()

    def _added(self, element):
        self.queue_draw()
        if self.on_change_callback:
            self.on_change_callback()
        return element

    def add_text_element(self, text: str = "New Text"):
        return self._added(self.document.add_text_element(text))

    def add_frame_element(self):
        return self._added(self.document.add_frame_element())

    def add_barcode_element(self):
        return self._added(self.document.add_barcode_element())

    def add_image_element(self, image_path: str):
        return self._added(self.document.add_image_element(image_path))

    def remove_selected(self):
        if self.document.remove_selected():
            self._changed()

    def bring_forward(self):
        if self.document.bring_forward():
            self._changed()

    def send_backward(self):
        if self.document.send_backward():
            self._changed()

    def bring_to_front(self):
        if self.document.bring_to_front():
            self._changed()

    def send_to_back(self):
        if self.document.send_to_back():
            self._changed()

    def align_selected(self, edge: str):
        if self.document.align_selected(edge):
            self._changed()

    def snapshot(self):
        return self.document.snapshot()

    def restore(self, snap):
        self.document.restore(snap)
        self.queue_draw()

    def clear(self):
        self.document.clear()
        self.queue_draw()

    def rescale(self, factor: float) -> None:
        self.document.rescale(factor)
        self.queue_draw()

    def sync_text_width(self, element) -> None:
        self.document.sync_text_width(element)

    def set_font(self, font_path: str, font_family: str, printer_font_name: str):
        self.document.set_font(font_path, font_family, printer_font_name)
        self.queue_draw()

    def set_element_font(self, element, font_path: str, font_family: str,
                         printer_font_name: str):
        self.document.set_element_font(element, font_path, font_family,
                                       printer_font_name)
        self.queue_draw()

    def to_zpl(self) -> str:
        return self.document.to_zpl()

    def set_label_size(self, width: int, height: int):
        self.document.set_label_size(width, height)
        self.sync_size()

    # --- coordinates ---------------------------------------------------------

    def _scale(self) -> float:
        """Display pixels per dot: the zoom, or whichever fit is in force."""
        if self.zoom is not None:
            return self.zoom
        vw, vh = self._view_size
        if self.fit == view.FIT_WIDTH:
            return view.fit_width(vw, self.label_width)
        return view.fit_scale(vw, vh, self.label_width, self.label_height)

    def _screen_to_label(self, x: float, y: float) -> Tuple[int, int]:
        return geometry.screen_to_label(x, y, self._scale())

    def set_view_size(self, width: int, height: int):
        """The visible area the canvas is being shown in, from the window."""
        size = (max(1, int(width)), max(1, int(height)))
        if size == self._view_size:
            return
        self._view_size = size
        if self.zoom is None:      # a fit follows the area it is fitting into
            self.sync_size()

    def set_zoom(self, zoom):
        """Show the label at a fixed scale, or None to go back to fitting."""
        self.zoom = None if zoom is None else view.clamp_zoom(zoom)
        self.sync_size()

    def set_fit(self, mode: str):
        """Fit the whole label, or its width, and follow the view from now on."""
        self.fit = mode
        self.set_zoom(None)

    def zoom_in(self):
        self.set_zoom(view.zoom_in(self._scale()))

    def zoom_out(self):
        self.set_zoom(view.zoom_out(self._scale()))

    def sync_size(self):
        """Size the widget to the label at the current scale.

        The label is drawn from the widget's own origin, so the widget has to
        be exactly as big as the label is on screen: that is what gives the
        scrolled window something to scroll and what lets it centre the canvas
        when it is smaller than the view. Without it the canvas was whatever
        height the viewport happened to be and the bottom of a tall label was
        drawn outside the widget, clipped and unreachable.
        """
        self.queue_resize()
        self.queue_draw()
        self.emit('scale-changed')

    # The size is asked for rather than set, so it is re-read on every
    # allocation. A set_size_request that shrinks does not reach the viewport
    # on its own - growing raises the minimum it must honour, shrinking asks
    # nothing of it - and zooming back out would leave the canvas at the size
    # it had when it was biggest.
    def do_get_preferred_width(self):
        width = max(1, int(round(self.label_width * self._scale())))
        return width, width

    def do_get_preferred_height(self):
        height = max(1, int(round(self.label_height * self._scale())))
        return height, height

    # --- text ----------------------------------------------------------------

    def _render_text_pil(self, context, element, font_path: str,
                        ink=(0, 0, 0, 255)) -> bool:
        """Draw the element's text with the shared raster. True on success.

        Rasterised by zplcore.textraster, with the same library that measures
        the string for printed_width(), so the glyphs and the box that claims
        to contain them cannot disagree. `ink` is white instead of black for a
        ^FR field.
        """
        block = getattr(element, 'block', None)
        if block is not None:
            # A ^FB block is rasterised at its printed size, wrapped and
            # justified, so nothing further is scaled here.
            pixbuf = to_pixbuf(textraster.raster_block(
                self.document.display_text(element), font_path, element.font_height,
                element.font_width, block, ink))
            if not pixbuf:
                return False
            context.save()
            Gdk.cairo_set_source_pixbuf(context, pixbuf, 0, 0)
            context.paint()
            context.restore()
            return True

        pixbuf = to_pixbuf(
            textraster.raster(self.document.display_text(element),
                              font_path, element.font_height, ink))
        if not pixbuf:
            return False

        # The printer scales the em square to font_width x font_height.
        # Stretching to fill element.width instead would make the text always
        # look like it fits, hiding any difference from what actually prints.
        h_scale = element.font_width / max(1, element.font_height)
        context.save()
        context.translate(textraster.MARGIN, 0)
        context.scale(h_scale, 1.0)
        Gdk.cairo_set_source_pixbuf(context, pixbuf, 0, 0)
        context.paint()
        context.restore()
        return True

    def on_draw(self, widget, context):
        """Draw the canvas and elements."""
        # Draw white background
        context.set_source_rgb(1, 1, 1)
        context.paint()

        # Determine scale to map label coordinates -> display coordinates
        scale_factor = self._scale()

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
            selected = self.document.is_selected(element)
            if element.print_enabled:
                self._draw_element(context, element, selected)
            else:
                # Dim it so the canvas shows what the file contains, while
                # keeping the element selectable and draggable.
                context.push_group()
                self._draw_element(context, element, selected)
                context.pop_group_to_source()
                context.paint_with_alpha(0.35)

        self._draw_band(context, scale_factor)

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
    
    def _draw_text_element(self, context, element, selected: bool):
        """Draw a text element."""
        reverse = element.reverse_print
        if reverse:
            # ^FR: this field prints in reverse, so the box is drawn solid
            # rather than as the usual translucent editing affordance - a
            # reversed field with nothing under it would otherwise vanish.
            context.set_source_rgb(0, 0, 0)
        else:
            # Translucent background: it is a designer affordance, and must
            # not hide anything underneath that will still print.
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
        ink = (255, 255, 255, 255) if reverse else (0, 0, 0, 255)
        context.set_source_rgb(*(c / 255 for c in ink[:3]))
        font_path = element.font_path or self.font_path
        block = getattr(element, 'block', None)

        # Everything below draws the text in its own upright frame; the frame
        # is what turns. The footprint stays axis-aligned, so the outline and
        # the handles above are drawn outside the rotation.
        facing = geometry.text_layout(element)
        context.save()
        context.translate(element.x + facing['offset'][0],
                          element.y + facing['offset'][1])
        if facing['angle']:
            context.rotate(math.radians(facing['angle']))

        pil_rendered = False
        if font_path:
            pil_rendered = self._render_text_pil(context, element, font_path, ink)

        if not pil_rendered and block is not None:
            self._draw_text_block(context, element, font_path, block)
        elif not pil_rendered:
            context.select_font_face(element.font_family or self.font_family or "monospace")
            context.set_font_size(element.font_height)
            shown = self.document.display_text(element)[:20]
            extents = context.text_extents(shown)
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
            context.translate(2, element.font_height - 2)
            context.scale(horizontal_scale, 1.0)
            context.move_to(0, 0)      # draw from here, not from a stale point
            context.show_text(shown)
            context.restore()

        context.restore()

        if selected:
            self._draw_handles(context, element)

    def _draw_text_block(self, context, element, font_path, block):
        """Wrap with the Cairo toy font when the block cannot be rasterised.

        Which is the ordinary case for a new element: nothing has a font file
        until one is chosen, and without this a block would draw as a single
        truncated line, so switching wrapping on would appear to do nothing.
        The lines and where they sit still come from the shared rasteriser, so
        only the glyphs differ from what will print.
        """
        context.select_font_face(element.font_family or self.font_family or "monospace")
        context.set_font_size(element.font_height)
        measure, _font = textraster.measurer(font_path, element.font_height,
                                             element.font_width)
        step = textraster.pitch(element.font_height, block)
        marked = textraster.wrap_marked(self.document.display_text(element), font_path,
                                        element.font_height, element.font_width,
                                        block)
        for row, (line, last) in enumerate(marked):
            for piece, x in textraster.placements(line, measure, block, last):
                drawn = context.text_extents(piece).width or 1.0
                context.save()
                context.translate(x, row * step + element.font_height - 2)
                context.scale(max(1.0, measure(piece)) / drawn, 1.0)
                # show_text() draws from the current point, and the current
                # point is part of the path, which save/restore does not carry.
                # Without this every line after the first carried on from the
                # end of the one before, so a block drew as one long line off
                # the side of the label while the print wrapped.
                context.move_to(0, 0)
                context.show_text(piece)
                context.restore()

    def _draw_frame_element(self, context, element, selected: bool):
        """Draw a frame element."""
        # Always draw at the real thickness. Using the frame's own stroke as the
        # selection highlight would hide the thickness setting exactly when it
        # is being changed, since editing leaves the element selected.
        t = max(1, element.thickness)

        # Selection outline first, on the element bounds where the resize handles
        # are: offsetting it by the stroke width would leave a gap from the
        # handles, and drawing it last would eat into the frame's own edge.
        if selected:
            context.set_source_rgb(0, 0, 1)
            context.set_line_width(2)
            context.rectangle(element.x, element.y, element.width, element.height)
            context.stroke()

        # ^GB's colour: white is what the printer leaves unburnt, so it shows
        # only over something already black - drawing it black instead was the
        # one case where the canvas showed the opposite of what prints.
        # ^FR flips it again, on top of whichever colour was chosen.
        white = (getattr(element, 'colour', 'B') == 'W') != element.reverse_print
        context.set_source_rgb(1, 1, 1) if white else context.set_source_rgb(0, 0, 0)
        radius = element.corner_radius() if hasattr(element, 'corner_radius') else 0

        if 2 * t >= min(element.width, element.height):
            # ^GB fills solid once the border meets in the middle
            _rounded_path(context, element.x, element.y,
                          element.width, element.height, radius)
            context.fill()
        else:
            # ^GB draws its border inside the w x h box, while cairo centres a
            # stroke on its path, so inset by half the thickness to put the
            # outer edge on the element bounds.
            context.set_line_width(t)
            _rounded_path(context, element.x + t / 2, element.y + t / 2,
                          element.width - t, element.height - t,
                          max(0.0, radius - t / 2))
            context.stroke()

        
        if selected:
            self._draw_handles(context, element)
    
    def _draw_band(self, context, scale: float):
        """The rubber band, while one is being dragged."""
        if self.band_origin is None or self.band_now is None:
            return
        (x0, y0), (x1, y1) = self.band_origin, self.band_now
        x, y = min(x0, x1), min(y0, y1)
        width, height = abs(x1 - x0), abs(y1 - y0)
        context.set_source_rgba(0, 0.5, 1, 0.12)
        context.rectangle(x, y, width, height)
        context.fill()
        context.set_source_rgb(0, 0.5, 1)
        context.set_line_width(1 / max(1e-6, scale))
        context.set_dash([4, 4], 0)
        context.rectangle(x, y, width, height)
        context.stroke()
        context.set_dash([], 0)

    def _draw_handles(self, context, element):
        """The eight resize handles of the selected element.

        Only ever on a selection of one. A group has no single box to resize,
        and handles on each member would offer a drag with nowhere to go.
        """
        if len(self.document.selection) != 1:
            return
        scale = self._scale()
        size = geometry.handle_size(scale)
        half = size / 2
        for _name, (hx, hy) in geometry.handles(element).items():
            context.set_source_rgb(0, 0.5, 1)
            context.rectangle(hx - half, hy - half, size, size)
            context.fill()
            context.set_source_rgb(0, 0, 1)
            context.set_line_width(1 / max(1e-6, scale))
            context.rectangle(hx - half, hy - half, size, size)
            context.stroke()

    def _draw_barcode_element(self, context, element, selected: bool):
        """Draw a barcode the way it will print.

        The layout - which way it faces, where the interpretation line sits,
        what it says - comes from zplcore.geometry, so this and the Qt canvas
        cannot form different opinions about it.
        """
        layout = geometry.barcode_layout(element)
        run = layout['run']
        stack = max(1, element.bar_height) + element.text_height()

        context.save()
        dx, dy = layout['offset']
        context.translate(element.x + dx, element.y + dy)
        if layout['angle']:
            context.rotate(math.radians(layout['angle']))

        # White behind the symbol: a barcode the printer cannot read is worse
        # than one that covers something, so it is deliberately opaque.
        # ^FR swaps it for black-behind-white, same as everywhere else.
        reverse = element.reverse_print
        bg, fg = ((0, 0, 0), (1, 1, 1)) if reverse else ((1, 1, 1), (0, 0, 0))
        context.set_source_rgb(*bg)
        context.rectangle(0, 0, run, stack)
        context.fill()

        bar_x, bar_y, bar_w, bar_h = layout['bars']
        mods = element.modules()
        mod_w = bar_w / max(1, sum(mods))
        context.set_source_rgb(*fg)
        cx = float(bar_x)
        for i, m in enumerate(mods):
            if i % 2 == 0:  # bars are at even indices
                context.rectangle(cx, bar_y, m * mod_w, bar_h)
                context.fill()
            cx += m * mod_w

        if layout['text']:
            self._draw_barcode_text(context, layout, reverse)
        context.restore()

        # The selection border follows the footprint, which is axis-aligned at
        # every quarter turn, so it is drawn outside the rotation.
        scale = self._scale()
        if selected:
            context.set_source_rgb(0, 0.7, 0)
            context.set_line_width(2 / scale)
        else:
            context.set_source_rgb(0.4, 0.4, 0.4)
            context.set_line_width(1 / scale)
        context.rectangle(element.x, element.y, element.width, element.height)
        context.stroke()

        if selected:
            self._draw_handles(context, element)

    def _draw_barcode_text(self, context, layout, reverse=False):
        """The interpretation line, in dots - not at a constant screen size.

        It is what the printer puts under the bars, so it is measured and
        drawn like any other text on the label.
        """
        text, font_height = layout['text'], max(1, int(layout['font'][1]))
        font_path = self.document.font_path
        ink = (255, 255, 255, 255) if reverse else (0, 0, 0, 255)
        pixbuf = (to_pixbuf(textraster.raster(text, font_path, font_height, ink))
                  if font_path else None)
        if pixbuf:
            context.save()
            context.translate(max(0, (layout['run'] - pixbuf.get_width()) / 2),
                              layout['text_y'])
            Gdk.cairo_set_source_pixbuf(context, pixbuf, 0, 0)
            context.paint()
            context.restore()
            return

        context.set_source_rgb(*((1, 1, 1) if reverse else (0, 0, 0)))
        context.select_font_face("sans-serif", 0, 0)
        context.set_font_size(font_height)
        extents = context.text_extents(text)
        context.move_to(max(0, (layout['run'] - extents.width) / 2),
                        layout['text_y'] + font_height)
        context.show_text(text)

    def _draw_image_element(self, context, element, selected: bool):
        """Draw an image element exactly as it will print (1-bit, dithered)."""
        # Re-dithering a large photo costs ~100ms, so while a resize handle is
        # being dragged reuse the last bitmap stretched to the new bounds; the
        # exact one is regenerated on release.
        pixbuf = None
        if self.active_handle is not None and element is self.selected_element:
            pixbuf = element.peek_print_render()
        if pixbuf is None:
            pixbuf = element.get_print_render(to_pixbuf)
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
            self._draw_handles(context, element)
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
                # select() rather than an assignment, so right-clicking one
                # element of a group does not throw the rest of the group away.
                self.document.select(clicked_element)
                self.queue_draw()
                self._show_context_menu(event, clicked_element)
            return

        if event.button != 1:
            return

        # Reset active handle for new click
        self.active_handle = None

        # Shift or Ctrl adds to the selection instead of replacing it.
        additive = bool(event.state & (Gdk.ModifierType.SHIFT_MASK |
                                       Gdk.ModifierType.CONTROL_MASK))

        # Check if clicking on a resize handle of the selected element
        if len(self.document.selection) == 1:
            handle = geometry.handle_at_point(lx, ly, self.selected_element,
                                             self._scale())
            if handle:
                self.active_handle = handle
                self.resize_origin = geometry.resize_origin(self.selected_element)
                self.drag_start = (lx, ly)
                return

        # Find element at click position
        clicked_element = None
        for element in reversed(self.elements):
            if element.contains_point(lx, ly):
                clicked_element = element
                break

        # Nothing under the pointer: start a rubber band, which selects what it
        # is dragged over and clears the selection if it is dragged over nothing.
        if clicked_element is None:
            self.band_origin = self.band_now = (lx, ly)
            self.band_additive = additive
            self.last_click_element = None
            if not additive:
                self.document.clear_selection()
            self.queue_draw()
            return

        # Check for double-click (within 500ms and same element)
        current_time = time.time()
        if (not additive and
            self.last_click_element == clicked_element and 
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
        self.document.select(clicked_element, additive)
        # An additive click is a selection gesture, not the start of a drag:
        # picking up the group on the same click would move it by whatever the
        # pointer wandered before the button came back up.
        if clicked_element and not additive:
            self.drag_start = (lx, ly)
        self.queue_draw()
    
    def on_button_release(self, widget, event):
        """Handle mouse button release."""
        if event.button == 1:
            if self.band_origin is not None:
                self._finish_band()
                return
            self.drag_start = None
            self.active_handle = None
            self.resize_origin = None
            # a drag is one change, reported once it finishes, so that it is
            # one undo step rather than one per motion event
            if self._drag_changed:
                self._drag_changed = False
                if self.on_change_callback:
                    self.on_change_callback()
    
    def _finish_band(self):
        """Select what the rubber band was dragged over, and put it away."""
        (x0, y0), (x1, y1) = self.band_origin, self.band_now
        self.band_origin = self.band_now = None
        caught = geometry.elements_in_box(self.elements, x0, y0, x1, y1)
        if self.band_additive:
            self.document.extend_selection(caught)
        else:
            self.document.select_many(caught)
        # A band changes the selection, never the document, so it is not a
        # change to undo - only a redraw.
        self.queue_draw()

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
        if len(self.document.selection) == 1:
            lx, ly = self._screen_to_label(event.x, event.y)
            handle = geometry.handle_at_point(lx, ly, self.selected_element,
                                             self._scale())
            if handle:
                name = self.HANDLE_CURSORS.get(handle)
        self._set_cursor(name)

    def on_scroll(self, widget, event):
        """Ctrl+scroll zooms about the pointer; a plain scroll scrolls."""
        if not event.state & Gdk.ModifierType.CONTROL_MASK:
            return False            # let the scrolled window have it
        direction = event.direction
        if direction == Gdk.ScrollDirection.SMOOTH:
            _ok, _dx, dy = event.get_scroll_deltas()
            up = dy < 0
        elif direction in (Gdk.ScrollDirection.UP, Gdk.ScrollDirection.DOWN):
            up = direction == Gdk.ScrollDirection.UP
        else:
            return False
        self.emit('zoom-at', event.x, event.y, up)
        return True

    def on_leave(self, widget, event):
        """Restore the default cursor when the pointer leaves the canvas."""
        if not self.active_handle:
            self._set_cursor(None)

    def on_motion(self, widget, event):
        """Handle mouse motion for dragging elements or resizing."""
        self._update_cursor(event)

        if self.band_origin is not None:
            self.band_now = self._screen_to_label(event.x, event.y)
            self.queue_draw()
            return

        if not self.drag_start or not self.document.selection:
            return
        
        # Calculate movement in label coordinates
        lx, ly = self._screen_to_label(event.x, event.y)
        dx = lx - self.drag_start[0]
        dy = ly - self.drag_start[1]
        
        # If a handle is active, resize instead of move
        if self.active_handle:
            # Measured from the press, so the whole drag is still in the delta
            # after the box has snapped back to its printed size.
            geometry.resize_by_handle(self.document, self.selected_element,
                                      self.active_handle, dx, dy,
                                      origin=self.resize_origin)
        else:
            # The whole selection moves together, clamped as one box.
            geometry.move_selection(self.document, self.document.selection, dx, dy)
            # A move carries on from where the pointer is now; a resize keeps
            # its press point, which is what its delta is measured from.
            self.drag_start = (lx, ly)
        
        self._drag_changed = True
        self.queue_draw()
