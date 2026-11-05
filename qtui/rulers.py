"""
The rulers along the canvas's top and left edges.

They sit in margins the scroll area keeps for them, outside the area it
scrolls, so they stay put while the label moves under them. Each finds the
label's edge from where the canvas actually is on screen - which takes in the
scroll position and the centring the scroll area does when the label is
smaller than the view - and numbers its marks from there, in the grid's unit,
with the marks themselves from zplcore.grid.ruler_ticks so the GTK rulers
number the same ones.
"""

import math

from PySide2.QtCore import QPoint, QRect, Qt
from PySide2.QtGui import QColor, QPainter, QPen
from PySide2.QtWidgets import QWidget

from zplcore import grid as zpl_grid

# How thick a ruler is, in pixels
RULER_SIZE = 20

OUTSIDE = QColor(226, 226, 226)    # beyond the label's edges
INSIDE = QColor(255, 255, 255)     # along the label
MARKS = QColor(90, 90, 90)
POINTER = QColor(200, 30, 30)

# How far each level of mark reaches across the ruler, as a fraction of it
MARK_LENGTHS = (0.6, 0.4, 0.25)


class Ruler(QWidget):
    """One ruler, across the top (`horizontal`) or down the left."""

    def __init__(self, canvas, grid, horizontal: bool, parent=None):
        super().__init__(parent)
        self.canvas = canvas
        self.grid = grid
        self.horizontal = horizontal
        # Where the pointer is along this ruler, in dots, or None off the canvas
        self.pointer = None
        font = self.font()
        font.setPointSizeF(7)
        self.setFont(font)

    def origin(self) -> int:
        """The label's top or left edge, in this ruler's own pixels."""
        point = self.mapFromGlobal(self.canvas.mapToGlobal(QPoint(0, 0)))
        return point.x() if self.horizontal else point.y()

    def marks(self):
        """What the ruler draws, as (pixel, level, number) along its length:
        every mark over the part of the label it shows."""
        document = self.canvas.document
        scale = self.canvas._scale()
        origin = self.origin()
        length = document.label_width if self.horizontal else document.label_height
        extent = self.width() if self.horizontal else self.height()
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
            self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), OUTSIDE)
        document = self.canvas.document
        scale = self.canvas._scale()
        origin = self.origin()
        length = document.label_width if self.horizontal else document.label_height
        span = int(round(length * scale))
        size = RULER_SIZE
        if self.horizontal:
            painter.fillRect(QRect(origin, 0, span, size), INSIDE)
        else:
            painter.fillRect(QRect(0, origin, size, span), INSIDE)

        painter.setPen(QPen(MARKS))
        for pixel, level, number in self.marks():
            reach = int(round(size * MARK_LENGTHS[level]))
            if self.horizontal:
                painter.drawLine(pixel, size - reach, pixel, size - 1)
                if number is not None:
                    painter.drawText(QRect(pixel + 2, 0, 60, size - reach + 2),
                                     Qt.AlignLeft | Qt.AlignVCenter, number)
            else:
                painter.drawLine(size - reach, pixel, size - 1, pixel)
                if number is not None:
                    # Turned to read up the ruler, as the left ruler of every
                    # drawing program does: a number written across it would
                    # not fit in its width.
                    painter.save()
                    painter.translate(0, pixel - 2)
                    painter.rotate(-90)
                    painter.drawText(QRect(0, 0, 60, size - reach + 2),
                                     Qt.AlignLeft | Qt.AlignVCenter, number)
                    painter.restore()
        # The edge against the canvas
        if self.horizontal:
            painter.drawLine(0, size - 1, self.width(), size - 1)
        else:
            painter.drawLine(size - 1, 0, size - 1, self.height())

        if self.pointer is not None:
            pixel = origin + int(math.floor(self.pointer * scale + 1e-9))
            painter.setPen(QPen(POINTER))
            if self.horizontal:
                painter.drawLine(pixel, 0, pixel, size - 1)
            else:
                painter.drawLine(0, pixel, size - 1, pixel)


class RulerCorner(QWidget):
    """The square where the two rulers meet, naming the unit they are in."""

    def __init__(self, grid, parent=None):
        super().__init__(parent)
        self.grid = grid
        font = self.font()
        font.setPointSizeF(7)
        self.setFont(font)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), OUTSIDE)
        painter.setPen(QPen(MARKS))
        painter.drawText(self.rect(), Qt.AlignCenter,
                         zpl_grid.UNIT_SUFFIX[self.grid.unit])
