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

# The alignments, in menu order: the three horizontal, then the three vertical
ALIGNMENTS = ('left', 'center', 'right', 'top', 'middle', 'bottom')

# How far outside its members' joint box a selected group's outline is drawn,
# in dots, so it clears the members' own selection rectangles
GROUP_OUTLINE_PAD = 2


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
    """Which handle, if any, the point grabs: the nearest one within reach.

    The hit radius is deliberately wider than the drawn square, so a handle can
    still be grabbed zoomed out. On a short element that makes neighbouring hit
    squares overlap, and a text element is short by nature - its height is its
    font height. Answering with the first handle in order then hands back one
    the pointer is further from: at a quarter scale the bottom corners of a text
    element were answering 'ml' and 'mr', so the user grabbed the bottom edge
    and the side moved. The nearest centre wins instead, a tie going to the
    earlier handle.
    """
    radius = handle_size(scale)
    nearest, shortest = None, None
    for name, (hx, hy) in handles(element).items():
        if abs(x - hx) <= radius and abs(y - hy) <= radius:
            distance = (x - hx) ** 2 + (y - hy) ** 2
            if shortest is None or distance < shortest:
                nearest, shortest = name, distance
    return nearest


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


def selection_bounds(elements):
    """The box around a group of elements, as (x, y, width, height).

    None for an empty group, so a caller cannot mistake "nothing selected" for
    a zero-sized box at the origin.
    """
    elements = [el for el in elements if el is not None]
    if not elements:
        return None
    left = min(el.x for el in elements)
    top = min(el.y for el in elements)
    right = max(el.x + el.width for el in elements)
    bottom = max(el.y + el.height for el in elements)
    return (left, top, right - left, bottom - top)


def top_group(element):
    """The id of the outermost group the element is in, or None.

    Written once, because it is the one rule everything about groups keys
    off: what a click selects, what moves together, what the z-order
    commands move. A group inside another is reached only by a direct pick
    (Document.select) or by ungrouping the outer one.
    """
    return element.group[0] if element.group else None


def units_of(elements):
    """Partition elements into the units they move as: whole top-level groups
    and loners.

    A grouped element brings every other element in the same outermost group
    along the first time one of them is met, in the order they were given, so
    a unit is a list whose order is the order of its input. An ungrouped
    element is a unit of one. Each element appears in exactly one unit, and
    the units come out in the order their first member did - which is z-order
    when given the document's elements, and pick order when given a selection.
    """
    elements = [el for el in elements if el is not None]
    units = []
    placed = set()
    for element in elements:
        if id(element) in placed:
            continue
        top = top_group(element)
        if top is None:
            unit = [element]
        else:
            unit = [el for el in elements if top_group(el) == top]
        units.append(unit)
        placed.update(id(el) for el in unit)
    return units


def group_outlines(elements, selected):
    """The boxes to draw around the selected groups, outermost first.

    One box per group, at every depth, whose members are all selected and
    number two or more: a group only some of whose members are picked has
    no outline, since a box around the picked ones would say the group is
    those, and a group of one is not a group. Each box is already padded:
    the innermost level GROUP_OUTLINE_PAD outside its members' joint box,
    each level enclosing it that much further out again, so a nested box
    always sits inside its parent's and never on top of it.
    """
    selected = [el for el in selected if el is not None]
    picked = set(id(el) for el in selected)
    # id -> (depth, members), for every id any selected element is under
    groups = {}
    for element in selected:
        for depth, gid in enumerate(element.group or ()):
            if gid not in groups:
                members = [el for el in elements if el.group and gid in el.group]
                groups[gid] = (depth, members)
    drawn = {gid: (depth, members) for gid, (depth, members) in groups.items()
             if len(members) >= 2 and all(id(el) in picked for el in members)}
    if not drawn:
        return []
    # Pad by how many drawn levels sit inside this one, so the pad is the
    # same 2 dots for a plain pair as it was before groups could nest.
    deepest = {}
    for gid, (depth, members) in drawn.items():
        inner = max(d for d, m in drawn.values()
                    if set(id(el) for el in m) <= set(id(el) for el in members))
        deepest[gid] = inner
    boxes = []
    for gid, (depth, members) in sorted(drawn.items(), key=lambda kv: kv[1][0]):
        pad = GROUP_OUTLINE_PAD * (deepest[gid] - depth + 1)
        x, y, w, h = selection_bounds(members)
        boxes.append((x - pad, y - pad, w + 2 * pad, h + 2 * pad))
    return boxes


def move_selection(document, elements, dx: int, dy: int) -> None:
    """Drag a group, keeping its shape and keeping all of it inside the label.

    The delta is clamped against the group's own box and then applied to every
    member. Clamping each element separately instead - a move_element per
    element - would let the ones still inside carry on while the one against
    the edge stopped, and the group would come apart in the user's hand.
    """
    elements = [el for el in elements if el is not None]
    if not elements:
        return
    if len(elements) == 1:
        move_element(document, elements[0], dx, dy)
        return

    x, y, width, height = selection_bounds(elements)
    dx = max(-x, min(dx, document.label_width - width - x))
    dy = max(-y, min(dy, document.label_height - height - y))
    for element in elements:
        element.x += dx
        element.y += dy


def elements_in_box(elements, x0: int, y0: int, x1: int, y1: int):
    """Every element a rubber band has caught, in the order it was given.

    Overlapping the band is enough - the band does not have to swallow an
    element whole. Containment would mean zooming out far enough to draw around
    a barcode that runs to the edge of the label before it could be picked up,
    which is the opposite of what the gesture is for.

    The corners may be given in any order, since a band is dragged in whichever
    direction the user pleases. A band of no area catches nothing, so a plain
    click on empty canvas still clears the selection.
    """
    left, right = sorted((x0, x1))
    top, bottom = sorted((y0, y1))
    return [el for el in elements
            if el.x < right and el.x + el.width > left
            and el.y < bottom and el.y + el.height > top]


def align_elements(document, elements, edge: str) -> bool:
    """Line a group up on one edge, or centre it on one axis.

    Each alignment moves one axis and leaves the other alone. What is lined up
    is each unit (see units_of): a grouped set moves as one rigid box, so
    aligning left does not stack its members at the same x and undo the very
    arrangement grouping was meant to keep. What the units are lined up
    against depends on how many there are: two or more line up against their
    joint bounding box, and a single unit - one element, or one whole group,
    which has nothing else to line up with - against the label.

    Returns whether anything actually moved, so an align that changes nothing
    records no undo entry.
    """
    units = units_of(elements)
    if not units or edge not in ALIGNMENTS:
        return False

    if len(units) > 1:
        box = selection_bounds([el for unit in units for el in unit])
    else:
        box = (0, 0, document.label_width, document.label_height)
    bx, by, bw, bh = box

    moved = False
    for unit in units:
        ux, uy, uw, uh = selection_bounds(unit)
        x, y = ux, uy
        if edge == 'left':
            x = bx
        elif edge == 'center':
            x = bx + (bw - uw) // 2
        elif edge == 'right':
            x = bx + bw - uw
        elif edge == 'top':
            y = by
        elif edge == 'middle':
            y = by + (bh - uh) // 2
        elif edge == 'bottom':
            y = by + bh - uh

        # Clamped the way a drag is, so a unit larger than the label lands
        # against the edge rather than at a negative coordinate.
        x = max(0, min(x, document.label_width - uw))
        y = max(0, min(y, document.label_height - uh))
        dx, dy = x - ux, y - uy
        if dx or dy:
            for element in unit:
                element.x += dx
                element.y += dy
            moved = True
    return moved


def resize_origin(element) -> dict:
    """The box a resize is measured from: the element as the drag started.

    A resize does not keep the rectangle it is handed. It reads a font width, a
    line count or a module width out of it and then snaps the box back to what
    that will actually print, so the outline on the canvas is the printed one.
    Measured from the previous motion event, that snap eats the drag: an event
    smaller than one unit of the derived property - and a unit of font_width is
    several dots, a line a whole pitch - is computed, snapped away and
    forgotten, so a slow drag moves nothing while a fast one jumps. Measured
    from the press the same snap is harmless, because the next event starts from
    this box again rather than from the snapped one.
    """
    return {'x': element.x, 'y': element.y,
            'width': element.width, 'height': element.height}


def _clamp_resized(document, element) -> None:
    """Hold a resized element to the minimum size and inside the label.

    The origin comes first, then the size against the room left beyond it, then
    the origin again now that the size is known. Clamping the origin against a
    size larger than the label - which is what a drag of a few thousand dots
    asks for - works out as a large negative x, and an element that starts far
    off the left edge satisfies "ends inside the label" without ever having been
    cut down. It was the drag being one event long that hid it.
    """
    element.x = max(0, element.x)
    element.y = max(0, element.y)

    element.width = max(MIN_SIZE,
                        min(element.width, document.label_width - element.x))
    element.height = max(MIN_SIZE,
                         min(element.height, document.label_height - element.y))

    element.x = min(element.x, document.label_width - element.width)
    element.y = min(element.y, document.label_height - element.height)


def resize_by_handle(document, element, handle: str, dx: int, dy: int,
                     origin: dict = None) -> None:
    """Resize an element by dragging one of its handles.

    `dx` and `dy` are measured from `origin` - the box the element had when the
    button went down, from resize_origin(). Given none they are measured from
    where the element is now, which is what a single scripted resize wants.
    """
    box = origin if origin is not None else resize_origin(element)
    x, y = box['x'], box['y']
    width, height = box['width'], box['height']

    if handle in ('tl', 'tm', 'tr'):    # Top handles - adjust y and height
        y += dy
        height -= dy
    if handle in ('bl', 'bm', 'br'):    # Bottom handles - adjust height
        height += dy
    if handle in ('tl', 'ml', 'bl'):    # Left handles - adjust x and width
        x += dx
        width -= dx
    if handle in ('tr', 'mr', 'br'):    # Right handles - adjust width
        width += dx

    element.x, element.y = x, y
    element.width, element.height = width, height
    _clamp_resized(document, element)

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
            # The run is along the text and the stack across it, so a quarter
            # turn swaps which side of the box is the font height - the same
            # transposition the barcode below makes. Reading the height as a
            # font height at every orientation set the font to the length of
            # the string as soon as a rotated element was dragged.
            run, stack = ((element.height, element.width) if element.rotated()
                          else (element.width, element.height))
            element.font_height = stack
            element.font_width = element.font_width_for(run, document.font_path)
            # Snap the box to what will actually print, so the outline the user
            # drags is the outline that comes out of the printer.
            document.sync_text_width(element)

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

    # A box that snapped back to a derived size is rarely the one that was
    # dragged, so it has to grow from somewhere: the edge opposite the handle,
    # which is the one the user left alone. Growing from the origin instead let
    # a top handle walk a wrapped block up the label a line at a time, its
    # height snapping back after every event while the y it had moved stayed.
    if handle in ('tl', 'tm', 'tr'):
        element.y = box['y'] + box['height'] - element.height
    if handle in ('tl', 'ml', 'bl'):
        element.x = box['x'] + box['width'] - element.width
    element.x = max(0, min(element.x, document.label_width - element.width))
    element.y = max(0, min(element.y, document.label_height - element.height))


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
