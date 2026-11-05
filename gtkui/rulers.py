"""
The rulers along the canvas's top and left edges.

They sit beside the scrolled window, not in it, so they stay put while the
label moves under them. Each finds the label's edge from where the canvas
actually is - which takes in the scroll position and the centring the
viewport does when the label is smaller than the view - and numbers its marks
from there, in the grid's unit, with the marks themselves from
zplcore.grid.ruler_ticks so the Qt rulers number the same ones.
"""

import math

import cairo
import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk  # noqa: E402

from zplcore import grid as zpl_grid  # noqa: E402

# How thick a ruler is, in pixels
RULER_SIZE = 20

OUTSIDE = (226 / 255, 226 / 255, 226 / 255)    # beyond the label's edges
INSIDE = (1.0, 1.0, 1.0)                        # along the label
MARKS = (90 / 255, 90 / 255, 90 / 255)
POINTER = (200 / 255, 30 / 255, 30 / 255)

# How far each level of mark reaches across the ruler, as a fraction of it
MARK_LENGTHS = (0.6, 0.4, 0.25)
FONT_SIZE = 9


def _line(context, x0, y0, x1, y1):
    """A one-pixel line between pixel centres, crisp without antialiasing."""
    context.move_to(x0 + 0.5, y0 + 0.5)
    context.line_to(x1 + 0.5, y1 + 0.5)


class Ruler(Gtk.DrawingArea):
    """One ruler, across the top (`horizontal`) or down the left."""

    def __init__(self, canvas, grid, horizontal: bool):
        super().__init__()
        self.canvas = canvas
        self.grid = grid
        self.horizontal = horizontal
        # Where the pointer is along this ruler, in dots, or None off the canvas
        self.pointer = None
        if horizontal:
            self.set_size_request(-1, RULER_SIZE)
            self.set_hexpand(True)
        else:
            self.set_size_request(RULER_SIZE, -1)
            self.set_vexpand(True)
        self.connect("draw", self.on_draw)

    def origin(self) -> int:
        """The label's top or left edge, in this ruler's own pixels."""
        point = self.canvas.translate_coordinates(self, 0, 0)
        if point is None:
            return 0
        return int(point[0] if self.horizontal else point[1])

    def marks(self):
        """What the ruler draws, as (pixel, level, number) along its length:
        every mark over the part of the label it shows."""
        document = self.canvas.document
        scale = self.canvas._scale()
        origin = self.origin()
        length = document.label_width if self.horizontal else document.label_height
        extent = (self.get_allocated_width() if self.horizontal
                  else self.get_allocated_height())
        low = max(0.0, -origin / scale)
        high = min(float(length), (extent - origin) / scale)
        if low > high:
            return []
        return [(origin + int(math.floor(dots * scale + 1e-9)), level, number)
                for dots, level, number in zpl_grid.ruler_ticks(
                    self.grid.unit, document.dpi, scale, low, high)]

    def set_pointer(self, dots):
        if dots != self.pointer:
            self.pointer = dots
            self.queue_draw()

    def on_draw(self, widget, context):
        width, height = self.get_allocated_width(), self.get_allocated_height()
        context.set_antialias(cairo.Antialias.NONE)
        context.set_line_width(1)
        context.set_source_rgb(*OUTSIDE)
        context.paint()
        document = self.canvas.document
        scale = self.canvas._scale()
        origin = self.origin()
        length = document.label_width if self.horizontal else document.label_height
        span = int(round(length * scale))
        size = RULER_SIZE
        context.set_source_rgb(*INSIDE)
        if self.horizontal:
            context.rectangle(origin, 0, span, size)
        else:
            context.rectangle(0, origin, size, span)
        context.fill()

        context.set_source_rgb(*MARKS)
        context.select_font_face("Sans")
        context.set_font_size(FONT_SIZE)
        numbers = []
        for pixel, level, number in self.marks():
            reach = int(round(size * MARK_LENGTHS[level]))
            if self.horizontal:
                _line(context, pixel, size - reach, pixel, size - 1)
            else:
                _line(context, size - reach, pixel, size - 1, pixel)
            if number is not None:
                numbers.append((pixel, reach, number))
        # The edge against the canvas
        if self.horizontal:
            _line(context, 0, size - 1, width, size - 1)
        else:
            _line(context, size - 1, 0, size - 1, height)
        context.stroke()

        context.set_antialias(cairo.Antialias.DEFAULT)
        for pixel, reach, number in numbers:
            context.save()
            if self.horizontal:
                context.move_to(pixel + 2, (size - reach) / 2 + FONT_SIZE / 2)
            else:
                # Turned to read up the ruler, as the left ruler of every
                # drawing program does: a number written across it would not
                # fit in its width.
                context.translate((size - reach) / 2 + FONT_SIZE / 2, pixel - 2)
                context.rotate(-math.pi / 2)
                context.move_to(0, 0)
            context.show_text(number)
            context.restore()

        if self.pointer is not None:
            context.set_antialias(cairo.Antialias.NONE)
            pixel = origin + int(math.floor(self.pointer * scale + 1e-9))
            context.set_source_rgb(*POINTER)
            if self.horizontal:
                _line(context, pixel, 0, pixel, size - 1)
            else:
                _line(context, 0, pixel, size - 1, pixel)
            context.stroke()
        return True


class RulerCorner(Gtk.DrawingArea):
    """The square where the two rulers meet, naming the unit they are in."""

    def __init__(self, grid):
        super().__init__()
        self.grid = grid
        self.set_size_request(RULER_SIZE, RULER_SIZE)
        self.connect("draw", self.on_draw)

    def on_draw(self, widget, context):
        width, height = self.get_allocated_width(), self.get_allocated_height()
        context.set_source_rgb(*OUTSIDE)
        context.paint()
        context.set_source_rgb(*MARKS)
        context.select_font_face("Sans")
        context.set_font_size(FONT_SIZE)
        text = zpl_grid.UNIT_SUFFIX[self.grid.unit]
        extents = context.text_extents(text)
        context.move_to((width - extents.width) / 2 - extents.x_bearing,
                        (height - extents.height) / 2 - extents.y_bearing)
        context.show_text(text)
        return True
