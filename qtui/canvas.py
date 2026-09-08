"""
The Qt design canvas: a QWidget that draws the label and edits it under the
mouse.

The canvas is a proof of what the printer will produce, not an approximation of
it, which decides most of what follows. Images are drawn as the 1-bit dithered
bitmap the printer receives, with white transparent, because a thermal head
only adds black and cannot erase - anything drawn opaque would hide something
that still prints. Text is drawn at the width it will really print. Designer
affordances - element backgrounds, outlines, handles - are translucent for the
same reason.

The geometry of editing and the text raster live in zplcore, so this file is
only the Qt half: painting, events and cursors.
"""

import time
from typing import Optional

from PySide2.QtCore import QPointF, QRectF, Qt, Signal
from PySide2.QtGui import QColor, QFont, QFontMetricsF, QImage, QPainter, QPen
from PySide2.QtWidgets import QMenu, QWidget

from zplcore import geometry, textraster
from zplcore.code128 import encode_b as _code128_modules
from zplcore.model import DesignElement, Document


def to_qimage(pil_image) -> Optional[QImage]:
    """A PIL RGBA image as a QImage.

    Copied because QImage does not take ownership of the buffer it is handed,
    and the Python bytes object would be freed as soon as this returns.
    """
    if pil_image is None:
        return None
    if pil_image.mode != 'RGBA':
        pil_image = pil_image.convert('RGBA')
    data = pil_image.tobytes('raw', 'RGBA')
    width, height = pil_image.size
    return QImage(data, width, height, 4 * width, QImage.Format_RGBA8888).copy()


class DesignCanvas(QWidget):
    """Canvas widget for designing ZPL layouts with drag and drop."""

    elementDoubleClicked = Signal(object)
    documentChanged = Signal()

    # Cursor shown while hovering each resize handle
    HANDLE_CURSORS = {
        'tl': Qt.SizeFDiagCursor, 'tm': Qt.SizeVerCursor, 'tr': Qt.SizeBDiagCursor,
        'ml': Qt.SizeHorCursor,                           'mr': Qt.SizeHorCursor,
        'bl': Qt.SizeBDiagCursor, 'bm': Qt.SizeVerCursor, 'br': Qt.SizeFDiagCursor,
    }

    def __init__(self, document: Optional[Document] = None, parent=None):
        super().__init__(parent)
        self.document = document if document is not None else Document()
        self.setMinimumSize(600, 800)
        self.setMouseTracking(True)   # so handle cursors track a hover
        self.setFocusPolicy(Qt.StrongFocus)

        self.drag_start = None
        self._drag_changed = False               # a drag moved something
        self.active_handle: Optional[str] = None
        self.last_click_time = 0.0
        self.last_click_element = None
        self._cursor_shape = None

    # --- document ------------------------------------------------------------

    def set_document(self, document: Document):
        """Show a different document - a load, or File > New."""
        self.document = document
        self.drag_start = None
        self.active_handle = None
        self.last_click_element = None
        self._sync_size()
        self.update()

    def commit(self):
        """Redraw and report a change, so the window can record one undo entry."""
        self.update()
        self.documentChanged.emit()

    # --- coordinates ---------------------------------------------------------

    def _scale(self) -> float:
        return geometry.scale_factor(self.width(), self.document.label_width)

    def _screen_to_label(self, x: float, y: float):
        return geometry.screen_to_label(x, y, self._scale())

    def _sync_size(self):
        """Keep the widget as tall as the label is, at the current scale.

        The canvas scales to fit the width, so its height is not free: it is
        whatever that scale makes the label. The scroll area keeps its vertical
        scrollbar always on, so the usable width does not change underneath
        this and set off a resize loop.
        """
        doc = self.document
        if doc.label_width > 0:
            self.setMinimumHeight(
                int(round(self.width() * doc.label_height / doc.label_width)))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._sync_size()

    # --- painting ------------------------------------------------------------

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), Qt.white)

        scale = self._scale()
        painter.save()
        # Everything below draws in label coordinates - dots - so no drawing
        # code has to know about the display scale.
        painter.scale(scale, scale)

        doc = self.document
        pen = QPen(QColor(204, 204, 204))
        pen.setWidthF(1.0 / max(1e-6, scale))
        pen.setStyle(Qt.CustomDashLine)
        pen.setDashPattern([5, 5])
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(QRectF(0, 0, doc.label_width, doc.label_height))

        for element in doc.elements:
            selected = element is doc.selected_element
            # An element that will not print is dimmed rather than hidden: the
            # canvas shows what the file contains, and it stays selectable.
            painter.setOpacity(1.0 if element.print_enabled else 0.35)
            self._draw_element(painter, element, selected)
        painter.setOpacity(1.0)

        painter.restore()

    def _draw_element(self, painter, element: DesignElement, selected: bool):
        if element.element_type == 'text':
            self._draw_text_element(painter, element, selected)
        elif element.element_type == 'frame':
            self._draw_frame_element(painter, element, selected)
        elif element.element_type == 'barcode':
            self._draw_barcode_element(painter, element, selected)
        elif element.element_type == 'image':
            self._draw_image_element(painter, element, selected)

    def _draw_handles(self, painter, element: DesignElement):
        """The eight resize handles, as small filled squares."""
        painter.setPen(QPen(QColor(0, 0, 255), 1))
        painter.setBrush(QColor(0, 128, 255))
        half, size = geometry.HANDLE_HALF, geometry.HANDLE_SIZE
        for _, (hx, hy) in geometry.handles(element).items():
            painter.drawRect(QRectF(hx - half, hy - half, size, size))
        painter.setBrush(Qt.NoBrush)

    # --- text ----------------------------------------------------------------

    def _draw_text_element(self, painter, element, selected: bool):
        # Translucent background: a designer affordance must not hide anything
        # underneath it that will still print.
        painter.fillRect(QRectF(element.x, element.y, element.width, element.height),
                         QColor(242, 242, 255, 89))

        painter.setPen(QPen(QColor(0, 0, 255), 2) if selected
                       else QPen(QColor(128, 128, 255), 1))
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(QRectF(element.x, element.y, element.width, element.height))

        font_path = element.font_path or self.document.font_path
        raster = None
        if font_path:
            raster = to_qimage(
                textraster.raster(element.text, font_path, element.font_height))

        if raster is not None:
            # The printer scales the em square to font_width x font_height.
            # Stretching to fill element.width instead would make the text
            # always look like it fits, hiding any difference from what prints.
            h_scale = element.font_width / max(1, element.font_height)
            painter.save()
            painter.translate(element.x + textraster.MARGIN, element.y)
            painter.scale(h_scale, 1.0)
            painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
            painter.drawImage(QPointF(0, 0), raster)
            painter.restore()
        else:
            self._draw_text_fallback(painter, element, font_path)

        if selected:
            self._draw_handles(painter, element)

    def _draw_text_fallback(self, painter, element, font_path):
        """Draw with a Qt face when the font file cannot be rasterised."""
        family = element.font_family or self.document.font_family or "monospace"
        font = QFont(family)
        font.setPixelSize(max(1, element.font_height))
        painter.setFont(font)
        metrics = QFontMetricsF(font)
        measured = metrics.horizontalAdvance(element.text) or 1.0

        if font_path:
            h_scale = element.font_width / max(1, element.font_height)
        else:
            # No downloaded font means ^AF, i.e. Zebra's built-in font A, which
            # is fixed width: every character occupies font_width dots so the
            # text spans the whole box. Stretch the proportional screen face to
            # match rather than leaving a gap.
            h_scale = element.width / measured

        painter.save()
        painter.setPen(QColor(0, 0, 0))
        painter.translate(element.x + 2, element.y + element.font_height - 2)
        painter.scale(h_scale, 1.0)
        painter.drawText(QPointF(0, 0), element.text)
        painter.restore()

    # --- frame ---------------------------------------------------------------

    def _draw_frame_element(self, painter, element, selected: bool):
        # Always draw at the real thickness. Using the frame's own stroke as the
        # selection highlight would hide the thickness setting exactly when it
        # is being changed, since editing leaves the element selected.
        t = max(1, element.thickness)

        # Selection outline first, on the element bounds where the resize
        # handles are: offsetting it would leave a gap from the handles, and
        # drawing it last would eat into the frame's own edge.
        if selected:
            painter.setPen(QPen(QColor(0, 0, 255), 2))
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(QRectF(element.x, element.y, element.width, element.height))

        if 2 * t >= min(element.width, element.height):
            # ^GB fills solid once the border meets in the middle
            painter.fillRect(QRectF(element.x, element.y, element.width, element.height),
                             QColor(0, 0, 0))
        else:
            # ^GB draws its border inside the w x h box, while a stroke is
            # centred on its path, so inset by half the thickness to put the
            # outer edge on the element bounds.
            pen = QPen(QColor(0, 0, 0))
            pen.setWidthF(t)
            pen.setJoinStyle(Qt.MiterJoin)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(QRectF(element.x + t / 2, element.y + t / 2,
                                    element.width - t, element.height - t))

        if selected:
            self._draw_handles(painter, element)

    # --- barcode -------------------------------------------------------------

    def _draw_barcode_element(self, painter, element, selected: bool):
        painter.fillRect(QRectF(element.x, element.y, element.width, element.height),
                         QColor(255, 255, 255))

        mods = _code128_modules(element.barcode_value)
        total = sum(mods) or 1
        mod_w = element.width / total
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(0, 0, 0))
        cx = float(element.x)
        for i, m in enumerate(mods):
            if i % 2 == 0:  # bars are at even indices
                painter.drawRect(QRectF(cx, element.y, m * mod_w, element.height))
            cx += m * mod_w
        painter.setBrush(Qt.NoBrush)

        # Barcode value beneath the bars, at a constant on-screen size
        scale = self._scale()
        font_size = max(8.0, 14 / max(1e-6, scale))
        font = QFont("sans-serif")
        font.setPixelSize(int(round(font_size)))
        painter.setFont(font)
        painter.setPen(QColor(0, 0, 0))
        metrics = QFontMetricsF(font)
        text_x = element.x + (element.width
                              - metrics.horizontalAdvance(element.barcode_value)) / 2
        painter.drawText(QPointF(text_x, element.y + element.height + font_size),
                         element.barcode_value)

        pen = QPen(QColor(0, 179, 0) if selected else QColor(102, 102, 102))
        pen.setWidthF((2 if selected else 1) / max(1e-6, scale))
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(QRectF(element.x, element.y, element.width, element.height))

        if selected:
            self._draw_handles(painter, element)

    # --- image ---------------------------------------------------------------

    def _draw_image_element(self, painter, element, selected: bool):
        # Re-dithering a large photo costs ~100ms, so while a resize handle is
        # being dragged reuse the last bitmap stretched to the new bounds; the
        # exact one is regenerated on release.
        image = None
        if self.active_handle is not None and element is self.document.selected_element:
            image = element.peek_print_render()
        if image is None:
            image = element.get_print_render(to_qimage)

        if image is not None and not image.isNull():
            painter.save()
            # Smoothing averages the dither dots down to the canvas the way the
            # eye does looking at a real printed label.
            painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
            painter.drawImage(
                QRectF(element.x, element.y, element.width, element.height), image)
            painter.restore()
        else:
            painter.fillRect(QRectF(element.x, element.y, element.width, element.height),
                             QColor(217, 217, 217))
            painter.setPen(QColor(128, 128, 128))
            font = QFont("sans-serif")
            font.setPixelSize(14)
            painter.setFont(font)
            painter.drawText(QPointF(element.x + 5, element.y + element.height / 2),
                             "[No Image]")

        painter.setPen(QPen(QColor(0, 0, 255), 2) if selected
                       else QPen(QColor(77, 77, 77), 1))
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(QRectF(element.x, element.y, element.width, element.height))

        if selected:
            self._draw_handles(painter, element)

    # --- mouse ---------------------------------------------------------------

    def mousePressEvent(self, event):
        lx, ly = self._screen_to_label(event.x(), event.y())
        doc = self.document

        if event.button() == Qt.RightButton:
            return  # handled by contextMenuEvent
        if event.button() != Qt.LeftButton:
            return

        self.active_handle = None

        # A handle of the selected element wins over anything under the pointer
        if doc.selected_element is not None:
            handle = geometry.handle_at_point(lx, ly, doc.selected_element)
            if handle:
                self.active_handle = handle
                self.drag_start = (lx, ly)
                return

        clicked_element = doc.element_at(lx, ly)

        # Double click, tracked here rather than left to the toolkit so the
        # interval stays the 500ms the spec names, on the same element.
        now = time.time()
        if (self.last_click_element is clicked_element
                and clicked_element is not None
                and (now - self.last_click_time) < 0.5):
            self.elementDoubleClicked.emit(clicked_element)
            self.last_click_time = 0.0
            self.last_click_element = None
            return

        self.last_click_time = now
        self.last_click_element = clicked_element

        doc.selected_element = clicked_element
        if clicked_element is not None:
            self.drag_start = (lx, ly)
        self.update()

    def mouseDoubleClickEvent(self, event):
        # Qt delivers the second click of a pair as a double-click event rather
        # than a press, so it is routed back through the press handler, which
        # owns the 500ms rule.
        self.mousePressEvent(event)

    def mouseMoveEvent(self, event):
        self._update_cursor(event)

        doc = self.document
        if not self.drag_start or doc.selected_element is None:
            return

        lx, ly = self._screen_to_label(event.x(), event.y())
        dx = lx - self.drag_start[0]
        dy = ly - self.drag_start[1]

        if self.active_handle:
            geometry.resize_by_handle(doc, doc.selected_element,
                                      self.active_handle, dx, dy)
        else:
            geometry.move_element(doc, doc.selected_element, dx, dy)

        self.drag_start = (lx, ly)
        self._drag_changed = True
        self.update()

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        self.drag_start = None
        self.active_handle = None
        if self._drag_changed:
            # A drag is one change, reported once it finishes, so it is one undo
            # entry rather than one per motion event.
            self._drag_changed = False
            self.update()
            self.documentChanged.emit()

    def leaveEvent(self, event):
        if not self.active_handle:
            self._set_cursor(None)

    def _set_cursor(self, shape):
        if shape == self._cursor_shape:
            return
        self._cursor_shape = shape
        if shape is None:
            self.unsetCursor()
        else:
            self.setCursor(shape)

    def _update_cursor(self, event):
        """Show a directional resize cursor over the selected element's handles."""
        if self.active_handle:
            self._set_cursor(self.HANDLE_CURSORS.get(self.active_handle))
            return
        shape = None
        if self.document.selected_element is not None:
            lx, ly = self._screen_to_label(event.x(), event.y())
            handle = geometry.handle_at_point(lx, ly, self.document.selected_element)
            if handle:
                shape = self.HANDLE_CURSORS.get(handle)
        self._set_cursor(shape)

    # --- context menu --------------------------------------------------------

    def contextMenuEvent(self, event):
        lx, ly = self._screen_to_label(event.x(), event.y())
        doc = self.document
        element = doc.element_at(lx, ly)
        if element is None:
            return

        doc.selected_element = element
        self.update()

        menu = QMenu(self)
        print_action = menu.addAction("Print This Element")
        print_action.setCheckable(True)
        print_action.setChecked(element.print_enabled)
        menu.addSeparator()

        front = menu.addAction("Bring to Front")
        forward = menu.addAction("Bring Forward")
        backward = menu.addAction("Send Backward")
        back = menu.addAction("Send to Back")

        can_raise, can_lower = doc.can_raise(), doc.can_lower()
        front.setEnabled(can_raise)
        forward.setEnabled(can_raise)
        backward.setEnabled(can_lower)
        back.setEnabled(can_lower)

        chosen = menu.exec_(event.globalPos())
        if chosen is None:
            return
        if chosen is print_action:
            # Keeps the element in the design and in the saved file, but off the
            # printed label - how a user suppresses something that would
            # otherwise print through an image covering it.
            element.print_enabled = print_action.isChecked()
        elif chosen is front:
            doc.bring_to_front()
        elif chosen is forward:
            doc.bring_forward()
        elif chosen is backward:
            doc.send_backward()
        elif chosen is back:
            doc.send_to_back()
        self.commit()
