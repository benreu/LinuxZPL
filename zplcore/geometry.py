"""
The geometry of editing: handles, hit-testing, dragging and resizing.

All of it in label coordinates - dots - and none of it aware of a toolkit, so
both frontends resize an element the same way. That matters more than it looks:
the resize rules decide whether the outline a user drags is the outline that
comes out of the printer, and two copies of these rules would eventually
disagree about it without ever raising an error.
"""

# Handles are squares this many dots across, and a click within this distance
# of a handle's centre counts as grabbing it.
HANDLE_SIZE = 8
HANDLE_HALF = HANDLE_SIZE // 2

# Smallest an element may be dragged down to
MIN_SIZE = 20

# Gap between a barcode's bars and its interpretation line, in dots
TEXT_BASELINE_GAP = 2

# Corner and edge-midpoint handles, in the order they are drawn
HANDLE_NAMES = ('tl', 'tm', 'tr', 'ml', 'mr', 'bl', 'bm', 'br')


def handles(element) -> dict:
    """Positions of the resize handles for any element type."""
    x, y, w, h = element.x, element.y, element.width, element.height
    return {
        'tl': (x, y),          'tm': (x + w // 2, y),     'tr': (x + w, y),
        'ml': (x, y + h // 2),                            'mr': (x + w, y + h // 2),
        'bl': (x, y + h),      'bm': (x + w // 2, y + h), 'br': (x + w, y + h),
    }


def handle_size(scale: float = 1.0) -> float:
    """A handle's side in dots, so it is a constant size on screen.

    The handles are drawn and hit-tested in label coordinates, which was the
    same thing as screen pixels while the canvas only ever fitted the width.
    Under zoom it is not: at a quarter scale an 8-dot handle is two pixels and
    cannot be grabbed, and at four times it is a 32-pixel blob covering the
    element it is meant to resize.
    """
    return HANDLE_SIZE / max(1e-6, scale)


def handle_at_point(x: int, y: int, element, scale: float = 1.0):
    """Which handle, if any, is within the hit radius of the point."""
    radius = handle_size(scale)
    for name, (hx, hy) in handles(element).items():
        if abs(x - hx) <= radius and abs(y - hy) <= radius:
            return name
    return None


def scale_factor(view_width: int, label_width: int) -> float:
    """Display pixels per label dot, so the label width fills the view."""
    if view_width > 0 and label_width > 0:
        return view_width / label_width
    return 1.0


def screen_to_label(x: float, y: float, scale: float):
    """A pointer position in display pixels, as label dots."""
    if scale <= 0:
        scale = 1.0
    return int(x / scale), int(y / scale)


def move_element(document, element, dx: int, dy: int) -> None:
    """Drag an element, keeping it inside the label."""
    element.x += dx
    element.y += dy
    element.x = max(0, min(element.x, document.label_width - element.width))
    element.y = max(0, min(element.y, document.label_height - element.height))


def resize_by_handle(document, element, handle: str, dx: int, dy: int) -> None:
    """Resize an element by dragging one of its handles."""
    if handle in ('tl', 'tm', 'tr'):    # Top handles - adjust y and height
        element.y += dy
        element.height -= dy
    if handle in ('bl', 'bm', 'br'):    # Bottom handles - adjust height
        element.height += dy
    if handle in ('tl', 'ml', 'bl'):    # Left handles - adjust x and width
        element.x += dx
        element.width -= dx
    if handle in ('tr', 'mr', 'br'):    # Right handles - adjust width
        element.width += dx

    element.width = max(MIN_SIZE, element.width)
    element.height = max(MIN_SIZE, element.height)

    element.x = max(0, element.x)
    element.y = max(0, element.y)
    element.x = min(element.x, document.label_width - element.width)
    element.y = min(element.y, document.label_height - element.height)
    if element.x + element.width > document.label_width:
        element.width = document.label_width - element.x
    if element.y + element.height > document.label_height:
        element.height = document.label_height - element.y

    if element.element_type == 'frame':
        element.thickness = max(1, min(element.thickness, element.max_thickness()))

    if element.element_type == 'text':
        block = getattr(element, 'block', None)
        if block is not None:
            # A wrapped element's box is its block. The side handles ask for a
            # wrap width and the top and bottom ones for a number of lines;
            # the box is then whatever the text wraps into, never a rectangle
            # the text is stretched to fill. Font size stays the dialog's
            # business - a block's height is a consequence of its wrap, so
            # scaling the font from the dragged height could not track it.
            pitch = max(1, element.font_height + block.line_spacing)
            block.width = max(MIN_SIZE, element.width)
            block.max_lines = max(1, int(round(element.height / pitch)))
            document.sync_text_width(element)
        else:
            element.font_height = element.height
            element.font_width = element.font_width_for(element.width,
                                                        document.font_path)
            # Snap the box to what will actually print, so the outline the user
            # drags is the outline that comes out of the printer.
            element.width = element.printed_width(document.font_path)

    if element.element_type == 'barcode':
        # A barcode is not free to be any size: its width is a whole number of
        # modules and its height is the bars plus the interpretation line. Take
        # the drag as a request for those two, then snap the box back to what
        # they produce, rather than stretching the symbol to fill a rectangle.
        run, stack = ((element.height, element.width) if element.rotated()
                      else (element.width, element.height))
        modules = sum(element.modules())
        element.module_width = max(1, round(run / max(1, modules)))
        element.bar_height = max(MIN_SIZE, stack - element.text_height())
        element.sync_box()


def turn(element) -> dict:
    """The rotation that places an element's own frame inside its footprint.

    `angle` and `offset` together are a rotation about the element's origin
    after a translation that brings the rotated content back onto it. Every
    quarter turn leaves the footprint axis-aligned, which is why handles,
    hit-testing, dragging and clamping never have to know about rotation.
    """
    orientation = (getattr(element, 'orientation', 'N') or 'N').upper()
    if orientation == 'R':          # 90 degrees, reading downward
        return {'angle': 90, 'offset': (element.width, 0)}
    if orientation == 'I':          # upside down
        return {'angle': 180, 'offset': (element.width, element.height)}
    if orientation == 'B':          # 270 degrees, reading upward
        return {'angle': 270, 'offset': (0, element.height)}
    return {'angle': 0, 'offset': (0, 0)}


def text_layout(element) -> dict:
    """Which way a text element faces, and where its own frame sits.

    Both frontends and the preview draw from this, so none of them can hold a
    different opinion about it - the same reason barcode_layout exists.
    """
    return turn(element)


def barcode_layout(element) -> dict:
    """Where a barcode's parts go, in its own unrotated frame.

    Both frontends and the preview renderer draw from this, so none of them
    can hold a different opinion about where the interpretation line sits or
    which way the symbol faces.

    `angle` and `offset` place that frame inside the element's footprint: a
    rotation about the element's origin, after a translation that brings the
    rotated content back onto it. The footprint stays axis-aligned at every
    quarter turn, which is why nothing else here has to know about rotation.
    """
    run = element.printed_width()
    bars = max(1, element.bar_height)
    text_h = element.text_height()

    facing = turn(element)
    angle, offset = facing['angle'], facing['offset']

    # The line goes above the bars or below them, and the bars move down to
    # make room when it is above.
    bars_y = text_h if (element.show_text and element.text_above) else 0
    text_y = 0 if (element.show_text and element.text_above) else bars + TEXT_BASELINE_GAP

    return {
        'angle': angle,
        'offset': offset,
        'run': run,
        'bars': (0, bars_y, run, bars),
        'text': element.encoded_value() if element.show_text else None,
        'text_y': text_y,
        'font': element.font or element.DEFAULT_FONT,
    }
