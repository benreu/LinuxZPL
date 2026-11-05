"""
Where a drag lands: on the grid, and held to one axis.

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
"""

from . import geometry

LEFT_HANDLES = ('tl', 'ml', 'bl')
RIGHT_HANDLES = ('tr', 'mr', 'br')
TOP_HANDLES = ('tl', 'tm', 'tr')
BOTTOM_HANDLES = ('bl', 'bm', 'br')


def snapping(grid) -> bool:
    """Whether `grid` is one to snap to: given, and with Snap to Grid on."""
    return grid is not None and grid.snap


def move_delta(document, origin_box, dx, dy, grid=None, lock_axis=False):
    """The delta from the press a move should apply, as (dx, dy).

    `origin_box` is the moving selection's (x, y, width, height) at the
    press. With `lock_axis` the shorter of the two deltas is dropped, so the
    move is along whichever axis the pointer has gone further. With a grid to
    snap to, the box's top-left corner goes to the nearest line on each axis
    that is free to move; a locked axis stays exactly where it was pressed
    rather than jumping to a line of its own.
    """
    free_x = free_y = True
    if lock_axis:
        if abs(dx) >= abs(dy):
            dy, free_y = 0, False
        else:
            dx, free_x = 0, False
    if snapping(grid):
        ox, oy = origin_box[0], origin_box[1]
        if free_x:
            dx = grid.snap_value(ox + dx, document.dpi) - ox
        if free_y:
            dy = grid.snap_value(oy + dy, document.dpi) - oy
    return dx, dy


def drag_selection(document, elements, origin_box, dx, dy, grid=None,
                   lock_axis=False) -> None:
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
    dx, dy = move_delta(document, origin_box, dx, dy, grid, lock_axis)
    geometry.move_selection(document, elements,
                            origin_box[0] + dx - box[0],
                            origin_box[1] + dy - box[1])


def resize_delta(document, origin, handle, dx, dy, grid=None):
    """The delta from the press a resize should apply, as (dx, dy).

    `origin` is geometry.resize_origin()'s box. The edge or edges the handle
    moves go to the nearest grid line - a left handle's left edge, a right
    handle's right edge - and an edge it leaves alone is not touched.
    """
    if not snapping(grid):
        return dx, dy
    dpi = document.dpi
    x, y = origin['x'], origin['y']
    right, bottom = x + origin['width'], y + origin['height']
    if handle in LEFT_HANDLES:
        dx = grid.snap_value(x + dx, dpi) - x
    elif handle in RIGHT_HANDLES:
        dx = grid.snap_value(right + dx, dpi) - right
    if handle in TOP_HANDLES:
        dy = grid.snap_value(y + dy, dpi) - y
    elif handle in BOTTOM_HANDLES:
        dy = grid.snap_value(bottom + dy, dpi) - bottom
    return dx, dy
