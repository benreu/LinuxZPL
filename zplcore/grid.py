"""
The grid over the canvas, and the unit it is measured in.

A view preference, like the zoom: nothing here is part of a label, and no ZPL
changes because of anything in this module. Positions stay whole dots; the
grid only decides which lines are drawn and, once snapping exists, where a drag
lands. It is kept in settings.ini, app-wide, so every label is shown with the
same grid and none is written into a file.

The spacing is physical - inches or millimetres - unless the user asks for
dots, so a grid follows a change of resolution the way the label size does. A
physical spacing is rarely a whole number of dots (1 mm is 7.99 at 203 dpi), so
line k sits at k spacings rounded to the nearest dot, never at k times a
rounded spacing: the second drifts a dot every hundred lines or so, and by the
end of a long label the grid would no longer be the one the user asked for.
Drawing and snapping both go through line_at(), so they cannot disagree about
where a line is.
"""

import configparser
import math
import os

UNITS = ('in', 'mm', 'dots')
UNIT_NAMES = {'in': 'Inches', 'mm': 'Millimetres', 'dots': 'Dots'}
UNIT_SUFFIX = {'in': 'in', 'mm': 'mm', 'dots': 'dots'}

# The spacing each unit accepts, and the decimals it is typed and stored to.
# The finest inch and millimetre spacings come out at about a dot at 203 dpi;
# spacing_dots() holds anything finer at one dot, whatever the resolution.
SPACING_LIMITS = {'in': (0.005, 2.0), 'mm': (0.1, 50.0), 'dots': (1, 400)}
SPACING_DECIMALS = {'in': 3, 'mm': 2, 'dots': 0}
MAJOR_LIMITS = (1, 100)

DEFAULT_UNIT = 'in'
# A tenth of an inch, the step a paste steps by too; what each unit falls back
# to when the file holds a spacing it cannot use.
DEFAULT_SPACING = {'in': 0.1, 'mm': 1.0, 'dots': 10}
DEFAULT_MAJOR = 10

# Lines closer together than this on screen are left out: a minor line every
# two pixels is grey mush, not a grid. The major lines go on showing until
# they too would be this close.
MIN_LINE_GAP = 5

GRID_SECTION = 'grid'
# The switches, every one off until the user turns it on
FLAGS = ('shown', 'snap', 'snap_elements', 'rulers')


def dots_per_unit(unit: str, dpi) -> float:
    """How many dots one of `unit` is at `dpi`."""
    if unit == 'in':
        return float(dpi)
    if unit == 'mm':
        return float(dpi) / 25.4
    return 1.0


def clamp_spacing(unit: str, value) -> float:
    """`value` held inside the unit's range, at the unit's precision."""
    low, high = SPACING_LIMITS[unit]
    value = round(max(low, min(float(value), high)), SPACING_DECIMALS[unit])
    return int(value) if unit == 'dots' else value


def convert_spacing(value, old_unit: str, new_unit: str, dpi) -> float:
    """A spacing in `old_unit` as near as `new_unit` can say it.

    For the dialog's unit choice: switching from inches to millimetres keeps
    the grid the size it was, as far as the new unit's precision and range
    allow, rather than reading 0.1 in as 0.1 mm.
    """
    dots = float(value) * dots_per_unit(old_unit, dpi)
    return clamp_spacing(new_unit, dots / dots_per_unit(new_unit, dpi))


def _format_number(value: float, decimals: int) -> str:
    """`value` to `decimals` places, without the trailing zeros."""
    text = f'{value:.{decimals}f}'
    return text.rstrip('0').rstrip('.') if '.' in text else text


def spacing_hint(unit: str, spacing, dpi) -> str:
    """What a spacing comes to on this label, for the dialog to show.

    In dots for a physical unit, since that is what the printer places and a
    fraction there means the lines are not evenly spaced; in inches for dots,
    since a dot spacing says nothing about size until the resolution is known.
    """
    dots = max(1.0, float(spacing) * dots_per_unit(unit, dpi))
    if unit == 'dots':
        return f'{_format_number(dots / dpi, 3)} in at {dpi} dpi'
    return f'{_format_number(dots, 2)} dots at {dpi} dpi'


class Grid:
    """The grid's spacing and the switches that use it.

    One instance per window, shared by reference with whatever draws or snaps,
    so a change made through the menu or the dialog is seen everywhere at once.
    """

    def __init__(self, unit=DEFAULT_UNIT, spacing=None, major_every=DEFAULT_MAJOR,
                 shown=False, snap=False, snap_elements=False, rulers=False):
        self.unit = unit if unit in UNITS else DEFAULT_UNIT
        self.spacing = clamp_spacing(self.unit, DEFAULT_SPACING[self.unit]
                                     if spacing is None else spacing)
        self.major_every = max(MAJOR_LIMITS[0], min(int(major_every), MAJOR_LIMITS[1]))
        self.shown = bool(shown)
        self.snap = bool(snap)
        self.snap_elements = bool(snap_elements)
        self.rulers = bool(rulers)

    def copy(self):
        return Grid(self.unit, self.spacing, self.major_every, self.shown,
                    self.snap, self.snap_elements, self.rulers)

    def update(self, other) -> None:
        """Take every setting from `other`, keeping this instance - the one
        everything holds a reference to."""
        self.__dict__.update(other.copy().__dict__)

    def __eq__(self, other):
        return isinstance(other, Grid) and self.__dict__ == other.__dict__

    def __repr__(self):
        flags = ', '.join(f for f in FLAGS if getattr(self, f))
        return (f'Grid({self.spacing} {self.unit}, major every {self.major_every}'
                f'{", " + flags if flags else ""})')

    def spacing_dots(self, dpi) -> float:
        """The spacing in dots at `dpi`, never under one."""
        return max(1.0, self.spacing * dots_per_unit(self.unit, dpi))

    def line_at(self, k: int, dpi) -> int:
        """Where line k is, in dots from the label's top or left edge."""
        return int(math.floor(k * self.spacing_dots(dpi) + 0.5))

    def nearest_line(self, value, dpi) -> int:
        """The number of the line nearest `value`, a tie going to the higher."""
        k = int(math.floor(value / self.spacing_dots(dpi)))
        below, above = self.line_at(k, dpi), self.line_at(k + 1, dpi)
        return k if value - below < above - value else k + 1

    def snap_value(self, value, dpi) -> int:
        """The grid line nearest `value`, a tie going to the higher one."""
        return self.line_at(self.nearest_line(value, dpi), dpi)

    def lines(self, low, high, scale, dpi) -> list:
        """The lines to draw across one axis, as (dots, is_major).

        Every line from `low` to `high` inclusive, at `scale` display pixels per
        dot, less the ones that would be too close together to read: the minor
        lines go first, and the major ones once they too would be under
        MIN_LINE_GAP pixels apart. Snapping uses every line, drawn or not.
        """
        step = self.spacing_dots(dpi)
        gap = step * scale
        if gap * self.major_every < MIN_LINE_GAP:
            return []
        minors = gap >= MIN_LINE_GAP
        found = []
        for k in range(max(0, int(math.floor(low / step))),
                       int(math.floor(high / step)) + 2):
            position = self.line_at(k, dpi)
            if position < low or position > high:
                continue
            major = k % self.major_every == 0
            if major or minors:
                found.append((position, major))
        return found


def load_grid(candidates) -> Grid:
    """The grid the settings file holds; the defaults where it holds none.

    `candidates` are the settings files in the order a write tries them, and
    the first with a [grid] section is read, for the reason load_recent gives.
    Each value stands alone: one that cannot be read falls back to its default
    without taking the rest with it, and a spacing outside its unit's range is
    taken as damaged rather than clamped, since clamping would quietly turn a
    typo into a grid nobody asked for.
    """
    for path in candidates:
        parser = configparser.ConfigParser(interpolation=None)
        try:
            if not parser.read(path, encoding='utf-8'):
                continue
        except (configparser.Error, UnicodeDecodeError):
            continue
        if not parser.has_section(GRID_SECTION):
            continue
        section = parser[GRID_SECTION]
        grid = Grid()
        unit = section.get('unit', DEFAULT_UNIT).strip()
        if unit in UNITS:
            grid.unit = unit
            grid.spacing = clamp_spacing(unit, DEFAULT_SPACING[unit])
            try:
                spacing = float(section.get('spacing', ''))
                low, high = SPACING_LIMITS[unit]
                if low <= spacing <= high:
                    grid.spacing = clamp_spacing(unit, spacing)
            except ValueError:
                pass
        try:
            major = int(section.get('major_every', ''))
            if MAJOR_LIMITS[0] <= major <= MAJOR_LIMITS[1]:
                grid.major_every = major
        except ValueError:
            pass
        for flag in FLAGS:
            try:
                setattr(grid, flag, section.getboolean(flag, fallback=False))
            except ValueError:
                pass
        return grid
    return Grid()


def save_grid(candidates, grid) -> bool:
    """Write the [grid] section, leaving every other one as it was.

    Tried against each candidate in turn, as save_recent does. Nothing is
    reported on failure: the grid has already changed on screen, and one that
    could not be remembered costs only setting it again next time.
    """
    for path in candidates:
        parser = configparser.ConfigParser()
        try:
            parser.read(path, encoding='utf-8')
            parser.remove_section(GRID_SECTION)
            parser.add_section(GRID_SECTION)
            parser.set(GRID_SECTION, 'unit', grid.unit)
            parser.set(GRID_SECTION, 'spacing', _format_number(
                grid.spacing, SPACING_DECIMALS[grid.unit]))
            parser.set(GRID_SECTION, 'major_every', str(grid.major_every))
            for flag in FLAGS:
                parser.set(GRID_SECTION, flag, 'yes' if getattr(grid, flag) else 'no')
            os.makedirs(os.path.dirname(os.fspath(path)), exist_ok=True)
            with open(path, 'w', encoding='utf-8') as f:
                parser.write(f)
            return True
        except (configparser.Error, OSError, UnicodeDecodeError):
            continue
    return False
