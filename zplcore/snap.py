"""
Where a drag lands: on the grid, against other elements, and held to one axis.

The canvases measure a drag from the press - a move as well as a resize - and
ask here what the total so far should become before it is applied. From the
press because a snap rounds: rounded event by event, every step of a slow drag
smaller than half a grid space would be rounded away and the element would
never leave its line, while a fast drag jumped.

Only the pointer snaps. A typed X or Y, an arrow-key nudge, Align and a paste
go exactly where they are told; none of them comes here. A box that snaps back
to what it will print - text, a barcode, a circle - has its own rules after
this, in geometry.resize_by_handle, so a dragged edge of one may come to rest
off the grid. That is the outline that prints, which is the one to show.

Snapping to elements lines a dragged box up with what is already on the label:
the left edges, centres and right edges of every element not being dragged,
and of the label itself, and the same across. It reaches only so far - a few
screen pixels, the same at every zoom - and within that reach it wins over the
grid, since lining up with a field that is there says more than a line does.
Coordinates here are doubled, so the centre of a box an odd number of dots wide
is still a whole number: a centre can then be matched to an edge within half a
dot, which is as close as whole dots can put it.
"""

import math

from . import geometry

LEFT_HANDLES = ('tl', 'ml', 'bl')
RIGHT_HANDLES = ('tr', 'mr', 'br')
TOP_HANDLES = ('tl', 'tm', 'tr')
BOTTOM_HANDLES = ('bl', 'bm', 'br')


# How far an element reaches out to catch a dragged edge, in screen pixels -
# divided by the scale, so it is the same distance to the eye at every zoom.
SNAP_DISTANCE = 6

# What a target on the label itself spans: a guide to it runs the whole label.
LABEL = 'label'


def snapping(grid) -> bool:
    """Whether `grid` is one to snap to: given, and with Snap to Grid on."""
    return grid is not None and grid.snap


def snapping_elements(grid) -> bool:
    """Whether to snap to other elements and the label: Snap to Elements on."""
    return grid is not None and grid.snap_elements


def _half_up(value) -> int:
    return int(math.floor(value + 0.5))


def targets(document, moving):
    """What a dragged edge can line up with, as (xs, ys).

    Each is a list of (coordinate, box): a coordinate in doubled dots - an
    element's left edge, centre and right edge, or top, middle and bottom -
    with the (x, y, width, height) it belongs to, or LABEL for the label's
    own edges and centre. Every element is a target except those in
    `moving`, which cannot line up with themselves.
    """
    skip = {id(el) for el in moving}
    xs, ys = [], []
    for el in document.elements:
        if id(el) in skip:
            continue
        box = (el.x, el.y, el.width, el.height)
        xs += [(2 * el.x, box), (2 * el.x + el.width, box),
               (2 * (el.x + el.width), box)]
        ys += [(2 * el.y, box), (2 * el.y + el.height, box),
               (2 * (el.y + el.height), box)]
    width, height = document.label_width, document.label_height
    xs += [(0, LABEL), (width, LABEL), (2 * width, LABEL)]
    ys += [(0, LABEL), (height, LABEL), (2 * height, LABEL)]
    return xs, ys


def _nearest(candidates, found, reach):
    """The smallest step, in doubled dots, that puts one of `candidates` on
    one of `found`, if any is within `reach`; None if none is."""
    best = None
    for c in candidates:
        for t, _ in found:
            step = t - c
            if abs(step) <= reach and (best is None or abs(step) < abs(best)):
                best = step
    return best


def _reach(scale) -> float:
    """SNAP_DISTANCE screen pixels, in doubled dots."""
    return 2 * SNAP_DISTANCE / max(1e-6, scale)


def move_delta(document, origin_box, dx, dy, grid=None, lock_axis=False,
               moving=(), scale=1.0):
    """The delta from the press a move should apply, as (dx, dy).

    `origin_box` is the moving selection's (x, y, width, height) at the
    press, and `moving` its elements. With `lock_axis` the shorter of the two
    deltas is dropped, so the move is along whichever axis the pointer has gone
    further; a locked axis stays exactly where it was pressed rather than
    snapping to anything. On a free axis, with Snap to Elements on, the box's
    nearest edge or centre within reach goes onto the nearest edge or centre of
    another element or of the label; failing that, with Snap to Grid on, its
    top-left corner goes to the nearest grid line. `scale` is display pixels
    per dot, which is what decides how far is within reach.
    """
    free_x = free_y = True
    if lock_axis:
        if abs(dx) >= abs(dy):
            dy, free_y = 0, False
        else:
            dx, free_x = 0, False
    ox, oy, width, height = origin_box
    xs = ys = ()
    if snapping_elements(grid) and (free_x or free_y):
        xs, ys = targets(document, moving)
    reach = _reach(scale)
    if free_x:
        left = ox + dx
        step = _nearest((2 * left, 2 * left + width, 2 * (left + width)), xs, reach)
        if step is not None:
            dx += _half_up(step / 2)
        elif snapping(grid):
            dx = grid.snap_value(left, document.dpi) - ox
    if free_y:
        top = oy + dy
        step = _nearest((2 * top, 2 * top + height, 2 * (top + height)), ys, reach)
        if step is not None:
            dy += _half_up(step / 2)
        elif snapping(grid):
            dy = grid.snap_value(top, document.dpi) - oy
    return dx, dy


def drag_selection(document, elements, origin_box, dx, dy, grid=None,
                   lock_axis=False, scale=1.0) -> None:
    """Put the selection where a drag of (dx, dy) from the press puts it.

    Applied as the step from where the selection is now to where it should
    be, through geometry.move_selection, so the clamp to the label and the
    rule that a group moves as one box are the same as for any other move.
    A drag pushed past the edge and brought back keeps the point it grabbed
    under the pointer, rather than setting off again from the edge.
    """
    box = geometry.selection_bounds(elements)
    if box is None:
        return
    dx, dy = move_delta(document, origin_box, dx, dy, grid, lock_axis,
                        moving=elements, scale=scale)
    geometry.move_selection(document, elements,
                            origin_box[0] + dx - box[0],
                            origin_box[1] + dy - box[1])


def resize_delta(document, origin, handle, dx, dy, grid=None, moving=(),
                 scale=1.0):
    """The delta from the press a resize should apply, as (dx, dy).

    `origin` is geometry.resize_origin()'s box, and `moving` the elements
    being resized. Only the edge or edges the handle moves snap - a left
    handle's left edge, a right handle's right edge - each onto another
    element's or the label's edge or centre within reach when Snap to Elements
    is on, or failing that to the nearest grid line when Snap to Grid is on.
    An edge the handle leaves alone is not touched.
    """
    if not snapping(grid) and not snapping_elements(grid):
        return dx, dy
    dpi = document.dpi
    xs, ys = targets(document, moving) if snapping_elements(grid) else ((), ())
    reach = _reach(scale)

    def snapped(edge, delta, found):
        step = _nearest((2 * (edge + delta),), found, reach)
        if step is not None:
            return delta + _half_up(step / 2)
        if snapping(grid):
            return grid.snap_value(edge + delta, dpi) - edge
        return delta

    x, y = origin['x'], origin['y']
    right, bottom = x + origin['width'], y + origin['height']
    if handle in LEFT_HANDLES:
        dx = snapped(x, dx, xs)
    elif handle in RIGHT_HANDLES:
        dx = snapped(right, dx, xs)
    if handle in TOP_HANDLES:
        dy = snapped(y, dy, ys)
    elif handle in BOTTOM_HANDLES:
        dy = snapped(bottom, dy, ys)
    return dx, dy


def guides(document, moving, handle=None):
    """The lines that show what a dragged box has lined up with.

    Read from where the box actually ended up - after the clamp to the label,
    and after a text or a barcode snapped back to what it will print - so a
    guide never claims a match the printed size did not keep. For a move every
    edge and centre of the box is looked at; for a resize, given its `handle`,
    only the edges that handle moves. Each guide is (x0, y0, x1, y1) in dots:
    a vertical or horizontal segment on the shared coordinate, spanning the
    dragged box and every box on that line, or the whole label where the label
    is one of them. Lined up means within half a dot, which is as close as a
    centre and an edge can come in whole dots.
    """
    box = geometry.selection_bounds(moving)
    if box is None:
        return []
    bx, by, bw, bh = box
    xs, ys = targets(document, moving)
    if handle is None:
        across = (2 * bx, 2 * bx + bw, 2 * (bx + bw))
        down = (2 * by, 2 * by + bh, 2 * (by + bh))
    else:
        across = ((2 * bx,) if handle in LEFT_HANDLES
                  else (2 * (bx + bw),) if handle in RIGHT_HANDLES else ())
        down = ((2 * by,) if handle in TOP_HANDLES
                else (2 * (by + bh),) if handle in BOTTOM_HANDLES else ())

    def lines(candidates, found, start, length, label_length, vertical):
        drawn = {}
        for c in candidates:
            for t, other in found:
                if abs(t - c) > 1:
                    continue
                low, high = drawn.get(t, (start, start + length))
                if other is LABEL:
                    low, high = 0, label_length
                elif vertical:
                    low, high = min(low, other[1]), max(high, other[1] + other[3])
                else:
                    low, high = min(low, other[0]), max(high, other[0] + other[2])
                drawn[t] = (low, high)
        if vertical:
            return [(t / 2, low, t / 2, high) for t, (low, high) in sorted(drawn.items())]
        return [(low, t / 2, high, t / 2) for t, (low, high) in sorted(drawn.items())]

    return (lines(across, xs, by, bh, document.label_height, True)
            + lines(down, ys, bx, bw, document.label_width, False))
