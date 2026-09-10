"""
The geometry of looking at a label, as opposed to editing one.

Zoom levels, the two fit rules, what a zoom about the pointer does to the
scroll position, and how big a window should open. None of it touches a
toolkit, so both frontends zoom by the same steps and round the same way - two
copies of these rules would drift a percent apart and nothing would ever raise
an error about it.

Nothing here is part of a label: no ZPL changes because of anything in this
module. It decides what you are looking at, never what will print.
"""

# The zoom levels the controls step through. Coarser at the ends, where a step
# of a few percent would not be worth the keystroke, and finer around 1.0,
# where the difference is worth seeing.
ZOOM_STEPS = (0.05, 0.1, 0.15, 0.25, 0.33, 0.5, 0.67, 0.75,
              1.0, 1.25, 1.5, 2.0, 3.0, 4.0, 6.0, 8.0)
ZOOM_MIN, ZOOM_MAX = ZOOM_STEPS[0], ZOOM_STEPS[-1]

# What the canvas shows when it has not been told otherwise
FIT_LABEL = 'label'
FIT_WIDTH = 'width'

# Opening size before the screen is taken into account, and the fraction of the
# work area it is allowed to take when the screen is smaller than that.
PREFERRED_SIZE = (1200, 900)
SCREEN_FRACTION = 0.9
MIN_WINDOW = (480, 360)


def clamp_zoom(zoom: float) -> float:
    """A zoom level inside the range the controls can reach."""
    return max(ZOOM_MIN, min(float(zoom), ZOOM_MAX))


def zoom_in(zoom: float) -> float:
    """The next step above `zoom`.

    Takes the current scale rather than an index into the steps, so zooming in
    from a fitted view - which is rarely a round number - lands on the next
    step above it instead of jumping back to wherever the last step was.
    """
    for step in ZOOM_STEPS:
        if step > zoom + 1e-9:
            return step
    return ZOOM_MAX


def zoom_out(zoom: float) -> float:
    """The next step below `zoom`."""
    for step in reversed(ZOOM_STEPS):
        if step < zoom - 1e-9:
            return step
    return ZOOM_MIN


def fit_scale(view_width, view_height, label_width, label_height) -> float:
    """Display pixels per dot that puts the whole label inside the view."""
    if view_width <= 0 or view_height <= 0:
        return 1.0
    if label_width <= 0 or label_height <= 0:
        return 1.0
    return clamp_zoom(min(view_width / label_width, view_height / label_height))


def fit_width(view_width, label_width) -> float:
    """Display pixels per dot that makes the label fill the view's width."""
    if view_width <= 0 or label_width <= 0:
        return 1.0
    return clamp_zoom(view_width / label_width)


def zoom_anchor(pointer_in_canvas, pointer_in_view, old_scale, new_scale):
    """The scroll offset that keeps the dot under the pointer under it.

    `pointer_in_canvas` is measured from the canvas widget's own origin and
    `pointer_in_view` from the visible area's, which differ both by the scroll
    offset and by any centring the container is doing when the canvas is
    smaller than the view. Taking both means neither has to be reconstructed
    from the other.
    """
    if old_scale <= 0:
        return 0.0
    return max(0.0, pointer_in_canvas * new_scale / old_scale - pointer_in_view)


def percent(scale: float) -> int:
    """A scale as the whole percentage the controls show."""
    return max(1, int(round(scale * 100)))


def place_window(work, saved=None):
    """Where and how big to open, as (x, y, width, height).

    `work` is the work area of the monitor being opened on - its position as
    well as its size, since on a stacked desktop the second monitor's origin is
    not (0, 0). With nothing saved the window is a comfortable fraction of that
    area, centred on it; a window taller than the work area gets placed wherever
    the window manager can put it, which can be almost entirely off the bottom
    edge and is indistinguishable from the application never opening.

    With a saved geometry the window comes back where it was, brought onto the
    monitor that is actually attached now - which may be smaller than the one it
    was saved on, or may not be in the same place.
    """
    wx, wy, ww, wh = work
    max_w = max(MIN_WINDOW[0], ww)
    max_h = max(MIN_WINDOW[1], wh)

    if saved is None:
        width = min(PREFERRED_SIZE[0], max(MIN_WINDOW[0], int(ww * SCREEN_FRACTION)))
        height = min(PREFERRED_SIZE[1], max(MIN_WINDOW[1], int(wh * SCREEN_FRACTION)))
        return (wx + (ww - width) // 2, wy + (wh - height) // 2, width, height)

    x, y, width, height = saved
    width = max(MIN_WINDOW[0], min(int(width), max_w))
    height = max(MIN_WINDOW[1], min(int(height), max_h))
    x = max(wx, min(int(x), wx + ww - width))
    y = max(wy, min(int(y), wy + wh - height))
    return (x, y, width, height)
