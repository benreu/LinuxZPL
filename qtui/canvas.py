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

from PySide2.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide2.QtGui import QColor, QFont, QFontMetricsF, QImage, QPainter, QPen
from PySide2.QtWidgets import QMenu, QWidget

from zplcore import geometry, textraster, view
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
    scaleChanged = Signal()
    # (pointer position in canvas pixels, zooming in) - a request rather than
    # a notification. Keeping the dot under the pointer needs its position
    # measured before the zoom moves everything, and the window owns the
    # scrollbars, so the window applies the whole thing.
    zoomAt = Signal(object, bool)

    # Cursor shown while hovering each resize handle
    HANDLE_CURSORS = {
        'tl': Qt.SizeFDiagCursor, 'tm': Qt.SizeVerCursor, 'tr': Qt.SizeBDiagCursor,
        'ml': Qt.SizeHorCursor,                           'mr': Qt.SizeHorCursor,
        'bl': Qt.SizeBDiagCursor, 'bm': Qt.SizeVerCursor, 'br': Qt.SizeFDiagCursor,
    }

    def __init__(self, document: Optional[Document] = None, parent=None):
        super().__init__(parent)
        self.document = document if document is not None else Document()

        # What the canvas is showing. `zoom` is display pixels per dot, or None
        # while a fit mode is deciding it; `fit` says which fit. The widget's
        # own size is a consequence of the scale, so the fit is measured
        # against the visible area the window reports, never against self -
        # measuring against self is a feedback loop.
        self.zoom = None
        self.fit = view.FIT_LABEL
        self._view_size = (600, 600)

        self.setMouseTracking(True)   # so handle cursors track a hover
        self.setFocusPolicy(Qt.StrongFocus)

        self.drag_start = None
        self._drag_changed = False               # a drag moved something
        self.active_handle: Optional[str] = None
        # The box the element had when a resize started. A resize is measured
        # from here rather than from the previous motion event, because the box
        # snaps back to what the text or the symbol will print and a delta
        # smaller than that snap would be thrown away every event.
        self.resize_origin: Optional[dict] = None
        # A rubber band being dragged over empty canvas: where it started and
        # where the pointer is now, both in dots, and whether it adds to the
        # selection rather than replacing it.
        self.band_origin = None
        self.band_now = None
        self.band_additive = False
        self.last_click_time = 0.0
        self.last_click_element = None
        self._cursor_shape = None

    # --- document ------------------------------------------------------------

    def set_document(self, document: Document):
        """Show a different document - a load, or File > New."""
        self.document = document
        self.drag_start = None
        self.active_handle = None
        self.resize_origin = None
        self.last_click_element = None
        self.band_origin = self.band_now = None
        self._sync_size()
        self.update()

    def commit(self):
        """Redraw and report a change, so the window can record one undo entry."""
        self.update()
        self.documentChanged.emit()

    # --- coordinates ---------------------------------------------------------

    def _scale(self) -> float:
        """Display pixels per dot: the zoom, or whichever fit is in force."""
        if self.zoom is not None:
            return self.zoom
        doc = self.document
        vw, vh = self._view_size
        if self.fit == view.FIT_WIDTH:
            return view.fit_width(vw, doc.label_width)
        return view.fit_scale(vw, vh, doc.label_width, doc.label_height)

    def _screen_to_label(self, x: float, y: float):
        return geometry.screen_to_label(x, y, self._scale())

    def set_view_size(self, width: int, height: int):
        """The visible area the canvas is being shown in, from the window."""
        size = (max(1, int(width)), max(1, int(height)))
        if size == self._view_size:
            return
        self._view_size = size
        if self.zoom is None:      # a fit follows the area it is fitting into
            self._sync_size()

    def set_zoom(self, zoom):
        """Show the label at a fixed scale, or None to go back to fitting."""
        self.zoom = None if zoom is None else view.clamp_zoom(zoom)
        self._sync_size()

    def set_fit(self, mode: str):
        """Fit the whole label, or its width, and follow the view from now on."""
        self.fit = mode
        self.set_zoom(None)

    def zoom_in(self):
        self.set_zoom(view.zoom_in(self._scale()))

    def zoom_out(self):
        self.set_zoom(view.zoom_out(self._scale()))

    def _sync_size(self):
        """Size the widget to the label at the current scale.

        The label is drawn from the widget's own origin, so the widget has to
        be exactly as big as the label is on screen: that is what gives the
        scroll area something to scroll and what lets the container centre the
        canvas when it is smaller than the view. Nothing here reads self.width().
        """
        doc = self.document
        scale = self._scale()
        self.setFixedSize(QSize(max(1, int(round(doc.label_width * scale))),
                                max(1, int(round(doc.label_height * scale)))))
        self.update()
        self.scaleChanged.emit()

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
            selected = doc.is_selected(element)
            # An element that will not print is dimmed rather than hidden: the
            # canvas shows what the file contains, and it stays selectable.
            painter.setOpacity(1.0 if element.print_enabled else 0.35)
            self._draw_element(painter, element, selected)
        painter.setOpacity(1.0)

        self._draw_band(painter, scale)

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

    def _draw_band(self, painter, scale: float):
        """The rubber band, while one is being dragged."""
        if self.band_origin is None or self.band_now is None:
            return
        (x0, y0), (x1, y1) = self.band_origin, self.band_now
        pen = QPen(QColor(0, 128, 255))
        pen.setWidthF(1.0 / max(1e-6, scale))
        pen.setStyle(Qt.CustomDashLine)
        pen.setDashPattern([4, 4])
        painter.setPen(pen)
        painter.setBrush(QColor(0, 128, 255, 30))
        painter.drawRect(QRectF(min(x0, x1), min(y0, y1),
                                abs(x1 - x0), abs(y1 - y0)))
        painter.setBrush(Qt.NoBrush)

    def _draw_handles(self, painter, element: DesignElement):
        """The eight resize handles, as small filled squares.

        Only ever on a selection of one. A group has no single box to resize,
        and handles on each member would offer a drag with nowhere to go.
        """
        if len(self.document.selection) != 1:
            return
        painter.setPen(QPen(QColor(0, 0, 255), 1))
        painter.setBrush(QColor(0, 128, 255))
        size = geometry.handle_size(self._scale())
        half = size / 2
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
        block = getattr(element, 'block', None)

        # Everything below draws the text in its own upright frame; the frame
        # is what turns. The footprint stays axis-aligned, so the outline and
        # the handles are drawn outside the rotation.
        facing = geometry.text_layout(element)
        painter.save()
        painter.translate(element.x + facing['offset'][0],
                          element.y + facing['offset'][1])
        if facing['angle']:
            painter.rotate(facing['angle'])

        raster = None
        if block is not None:
            # A ^FB block is rasterised at its printed size, wrapped and
            # justified, so nothing further is scaled here.
            wrapped = to_qimage(textraster.raster_block(
                self.document.display_text(element), font_path, element.font_height,
                element.font_width, block)) if font_path else None
            if wrapped is not None:
                painter.drawImage(QPointF(0, 0), wrapped)
            else:
                self._draw_text_block(painter, element, font_path, block)
            painter.restore()
            if selected:
                self._draw_handles(painter, element)
            return
        if font_path:
            raster = to_qimage(
                textraster.raster(self.document.display_text(element),
                                  font_path, element.font_height))

        if raster is not None:
            # The printer scales the em square to font_width x font_height.
            # Stretching to fill element.width instead would make the text
            # always look like it fits, hiding any difference from what prints.
            h_scale = element.font_width / max(1, element.font_height)
            painter.save()
            painter.translate(textraster.MARGIN, 0)
            painter.scale(h_scale, 1.0)
            painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
            painter.drawImage(QPointF(0, 0), raster)
            painter.restore()
        else:
            self._draw_text_fallback(painter, element, font_path)

        painter.restore()

        if selected:
            self._draw_handles(painter, element)

    def _draw_text_block(self, painter, element, font_path, block):
        """Wrap with a Qt face when the block cannot be rasterised.

        Which is the ordinary case for a new element: nothing has a font file
        until one is chosen, and without this a block would draw as a single
        unwrapped line, so switching wrapping on would appear to do nothing.
        The lines and where they sit still come from the shared rasteriser, so
        only the glyphs differ from what will print.
        """
        family = element.font_family or self.document.font_family or "monospace"
        font = QFont(family)
        font.setPixelSize(max(1, element.font_height))
        painter.setFont(font)
        painter.setPen(QColor(0, 0, 0))
        metrics = QFontMetricsF(font)
        measure, _font = textraster.measurer(font_path, element.font_height,
                                             element.font_width)
        step = textraster.pitch(element.font_height, block)
        marked = textraster.wrap_marked(self.document.display_text(element), font_path,
                                        element.font_height, element.font_width,
                                        block)
        for row, (line, last) in enumerate(marked):
            for piece, x in textraster.placements(line, measure, block, last):
                drawn = metrics.horizontalAdvance(piece) or 1.0
                painter.save()
                painter.translate(x, row * step + element.font_height - 2)
                painter.scale(max(1.0, measure(piece)) / drawn, 1.0)
                painter.drawText(QPointF(0, 0), piece)
                painter.restore()

    def _draw_text_fallback(self, painter, element, font_path):
        """Draw with a Qt face when the font file cannot be rasterised."""
        family = element.font_family or self.document.font_family or "monospace"
        font = QFont(family)
        font.setPixelSize(max(1, element.font_height))
        painter.setFont(font)
        metrics = QFontMetricsF(font)
        shown = self.document.display_text(element)
        measured = metrics.horizontalAdvance(shown) or 1.0

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
        painter.translate(2, element.font_height - 2)
        painter.scale(h_scale, 1.0)
        painter.drawText(QPointF(0, 0), shown)
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

        # ^GB's colour: white is what the printer leaves unburnt, so it shows
        # only over something already black - drawing it black instead was the
        # one case where the canvas showed the opposite of what prints.
        ink = QColor(255, 255, 255) if getattr(element, 'colour', 'B') == 'W' \
            else QColor(0, 0, 0)
        radius = element.corner_radius() if hasattr(element, 'corner_radius') else 0

        if 2 * t >= min(element.width, element.height):
            # ^GB fills solid once the border meets in the middle
            box = QRectF(element.x, element.y, element.width, element.height)
            painter.setPen(Qt.NoPen)
            painter.setBrush(ink)
            if radius > 0:
                painter.drawRoundedRect(box, radius, radius)
            else:
                painter.fillRect(box, ink)
            painter.setBrush(Qt.NoBrush)
        else:
            # ^GB draws its border inside the w x h box, while a stroke is
            # centred on its path, so inset by half the thickness to put the
            # outer edge on the element bounds.
            pen = QPen(ink)
            pen.setWidthF(t)
            pen.setJoinStyle(Qt.MiterJoin)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            box = QRectF(element.x + t / 2, element.y + t / 2,
                         element.width - t, element.height - t)
            inner = max(0.0, radius - t / 2)
            if inner > 0:
                painter.drawRoundedRect(box, inner, inner)
            else:
                painter.drawRect(box)

        if selected:
            self._draw_handles(painter, element)

    # --- barcode -------------------------------------------------------------

    def _draw_barcode_element(self, painter, element, selected: bool):
        """Draw a barcode the way it will print.

        The layout - which way it faces, where the interpretation line sits,
        what it says - comes from zplcore.geometry, so this and the GTK canvas
        cannot form different opinions about it.
        """
        layout = geometry.barcode_layout(element)
        run = layout['run']
        stack = max(1, element.bar_height) + element.text_height()

        painter.save()
        dx, dy = layout['offset']
        painter.translate(element.x + dx, element.y + dy)
        if layout['angle']:
            painter.rotate(layout['angle'])

        # White behind the symbol: a barcode the printer cannot read is worse
        # than one that covers something, so it is deliberately opaque.
        painter.fillRect(QRectF(0, 0, run, stack), QColor(255, 255, 255))

        bar_x, bar_y, bar_w, bar_h = layout['bars']
        mods = element.modules()
        mod_w = bar_w / max(1, sum(mods))
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(0, 0, 0))
        cx = float(bar_x)
        for i, m in enumerate(mods):
            if i % 2 == 0:  # bars are at even indices
                painter.drawRect(QRectF(cx, bar_y, m * mod_w, bar_h))
            cx += m * mod_w
        painter.setBrush(Qt.NoBrush)

        if layout['text']:
            self._draw_barcode_text(painter, layout)
        painter.restore()

        # The selection border follows the footprint, which is axis-aligned at
        # every quarter turn, so it is drawn outside the rotation.
        scale = self._scale()
        pen = QPen(QColor(0, 179, 0) if selected else QColor(102, 102, 102))
        pen.setWidthF((2 if selected else 1) / max(1e-6, scale))
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(QRectF(element.x, element.y, element.width, element.height))

        if selected:
            self._draw_handles(painter, element)

    def _draw_barcode_text(self, painter, layout):
        """The interpretation line, in dots - not at a constant screen size.

        It is what the printer puts under the bars, so it is measured and
        drawn like any other text on the label.
        """
        text, font_height = layout['text'], max(1, int(layout['font'][1]))
        font_path = self.document.font_path
        raster = (to_qimage(textraster.raster(text, font_path, font_height))
                  if font_path else None)
        if raster is not None:
            painter.drawImage(
                QPointF(max(0, (layout['run'] - raster.width()) / 2),
                        layout['text_y']), raster)
            return

        font = QFont("sans-serif")
        font.setPixelSize(font_height)
        painter.setFont(font)
        painter.setPen(QColor(0, 0, 0))
        metrics = QFontMetricsF(font)
        painter.drawText(
            QPointF(max(0, (layout['run'] - metrics.horizontalAdvance(text)) / 2),
                    layout['text_y'] + font_height), text)

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
        # Shift or Ctrl adds to the selection instead of replacing it.
        additive = bool(event.modifiers() & (Qt.ShiftModifier | Qt.ControlModifier))

        # A handle of the selected element wins over anything under the pointer
        if len(doc.selection) == 1:
            handle = geometry.handle_at_point(lx, ly, doc.selected_element,
                                             self._scale())
            if handle:
                self.active_handle = handle
                self.resize_origin = geometry.resize_origin(doc.selected_element)
                self.drag_start = (lx, ly)
                return

        clicked_element = doc.element_at(lx, ly)

        # Nothing under the pointer: start a rubber band, which selects what it
        # is dragged over and clears the selection if it is dragged over nothing.
        if clicked_element is None:
            self.band_origin = self.band_now = (lx, ly)
            self.band_additive = additive
            self.last_click_element = None
            if not additive:
                doc.clear_selection()
            self.update()
            return

        # Double click, tracked here rather than left to the toolkit so the
        # interval stays the 500ms the spec names, on the same element.
        now = time.time()
        if (not additive
                and self.last_click_element is clicked_element
                and clicked_element is not None
                and (now - self.last_click_time) < 0.5):
            self.elementDoubleClicked.emit(clicked_element)
            self.last_click_time = 0.0
            self.last_click_element = None
            return

        self.last_click_time = now
        self.last_click_element = clicked_element

        doc.select(clicked_element, additive)
        # An additive click is a selection gesture, not the start of a drag:
        # picking up the group on the same click would move it by whatever the
        # pointer wandered before the button came back up.
        if clicked_element is not None and not additive:
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
        if self.band_origin is not None:
            self.band_now = self._screen_to_label(event.x(), event.y())
            self.update()
            return

        if not self.drag_start or not doc.selection:
            return

        lx, ly = self._screen_to_label(event.x(), event.y())
        dx = lx - self.drag_start[0]
        dy = ly - self.drag_start[1]

        if self.active_handle:
            # Measured from the press, so the whole drag is still in the delta
            # after the box has snapped back to its printed size.
            geometry.resize_by_handle(doc, doc.selected_element,
                                      self.active_handle, dx, dy,
                                      origin=self.resize_origin)
        else:
            # The whole selection moves together, clamped as one box.
            geometry.move_selection(doc, doc.selection, dx, dy)
            # A move carries on from where the pointer is now; a resize keeps
            # its press point, which is what its delta is measured from.
            self.drag_start = (lx, ly)

        self._drag_changed = True
        self.update()

    def wheelEvent(self, event):
        """Ctrl+wheel zooms about the pointer; a plain wheel scrolls."""
        if not event.modifiers() & Qt.ControlModifier:
            event.ignore()          # let the scroll area have it
            return
        step = event.angleDelta().y()
        if not step:
            return
        self.zoomAt.emit(event.pos(), step > 0)
        event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        if self.band_origin is not None:
            self._finish_band()
            return
        self.drag_start = None
        self.active_handle = None
        self.resize_origin = None
        if self._drag_changed:
            # A drag is one change, reported once it finishes, so it is one undo
            # entry rather than one per motion event.
            self._drag_changed = False
            self.update()
            self.documentChanged.emit()

    def _finish_band(self):
        """Select what the rubber band was dragged over, and put it away."""
        doc = self.document
        (x0, y0), (x1, y1) = self.band_origin, self.band_now
        self.band_origin = self.band_now = None
        caught = geometry.elements_in_box(doc.elements, x0, y0, x1, y1)
        if self.band_additive:
            doc.extend_selection(caught)
        else:
            doc.select_many(caught)
        # A band changes the selection, never the document, so it is not a
        # change to undo - only a redraw.
        self.update()

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
        if len(self.document.selection) == 1:
            lx, ly = self._screen_to_label(event.x(), event.y())
            handle = geometry.handle_at_point(lx, ly,
                                             self.document.selected_element,
                                             self._scale())
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

        # select() rather than an assignment, so right-clicking one element of
        # a group does not throw the rest of the group away.
        doc.select(element)
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
