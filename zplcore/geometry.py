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


def handle_at_point(x: int, y: int, element):
    """Which handle, if any, is within the hit radius of the point."""
    for name, (hx, hy) in handles(element).items():
        if abs(x - hx) <= HANDLE_SIZE and abs(y - hy) <= HANDLE_SIZE:
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
        element.font_height = element.height
        element.font_width = element.font_width_for(element.width, document.font_path)
        # Snap the box to what will actually print, so the outline the user
        # drags is the outline that comes out of the printer.
        element.width = element.printed_width(document.font_path)
