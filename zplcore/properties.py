"""The rows the Properties panel shows for a selection, whatever the toolkit.

Two kinds of row come from here:

- The **header**, on every selection: X and Y, the left and top of the
  selection's box in dots, and Print This Element. Setting X or Y moves the
  whole selection by the difference, through geometry.move_selection, so a
  group stays in one piece and inside the label exactly as it does when it is
  dragged.

- The **shared** properties, for a selection of several elements: those that
  mean the same thing on every element type that has them and already have a
  row in that type's editor - a thickness, a colour, an orientation, a font
  size. A property is offered only when every selected element has it, and
  writing it writes it to all of them, with whatever each type does after the
  change in its own editor (a text box re-measured, a thickness held under a
  shrunken side). Its range is the narrowest of theirs.

What only makes sense one element at a time - text, barcode data and the
symbology's own parameters, a block, a font file, ^FN, ^FM, an image's file -
stays in that element's own editor and is not here.

Both frontends draw their panels from these tables, so neither can offer a
row, a range or a side effect the other does not.
"""

from typing import Any, Callable, Optional, Sequence, Tuple

from . import geometry
from .model import (BarcodeElement, CircleElement, DiagonalLineElement,
                    EllipseElement, FrameElement, GraphicSymbolElement,
                    TextElement, BARCODE_FEATURES, FRAME_COLOURS, ORIENTATIONS)


class _Mixed:
    """The value of a property the selected elements disagree on."""

    def __repr__(self):
        return 'MIXED'


MIXED = _Mixed()

# A kind is the control a toolkit draws for the row: a number box between
# the limits, a checkbox, or a list of (label, code) choices.
INT, BOOL, CHOICE = 'int', 'bool', 'choice'


class Property:
    """One row: what it is called, which elements have it, how it is read,
    written and bounded."""

    def __init__(self, key: str, label: str, kind: str, *,
                 applies: Callable[[Any], bool] = lambda element: True,
                 get: Callable[[Any], Any],
                 put: Callable[[Any, Any, Any], None],
                 limits: Optional[Callable[[Any], Tuple[int, int]]] = None,
                 choices: Sequence[Tuple[str, Any]] = ()):
        self.key = key
        self.label = label
        self.kind = kind
        self.applies = applies
        self._get = get
        self._put = put
        self._limits = limits
        self.choices = tuple(choices)

    def __repr__(self):
        return f'Property({self.key!r})'

    def title(self, elements) -> str:
        """The row's label for this selection."""
        return self.label

    def value(self, document, elements):
        """The value every element shares, or MIXED where they differ."""
        values = [self._get(element) for element in elements]
        if not values:
            return None
        first = values[0]
        return first if all(value == first for value in values[1:]) else MIXED

    def limits(self, document, elements) -> Optional[Tuple[int, int]]:
        """The range a value may take on all of them: the larger of their
        minimums and the smaller of their maximums. None for a row with no
        range."""
        if self._limits is None:
            return None
        ranges = [self._limits(element) for element in elements]
        if not ranges:
            return None
        low = max(lower for lower, _upper in ranges)
        high = min(upper for _lower, upper in ranges)
        return low, max(low, high)

    def write(self, document, elements, value) -> None:
        """Give every element this value, and let each settle after it."""
        for element in elements:
            self._put(document, element, value)


class _Position(Property):
    """X or Y: an edge of the selection's box, rather than of each element.

    Read off the box and written by moving the whole selection by the
    difference, so writing one element's X is the same move a drag makes,
    and writing a group's moves it as one rigid box, held inside the label.
    """

    def __init__(self, key: str, label: str, axis: int):
        super().__init__(key, label, INT, get=None, put=None)
        self._axis = axis

    def value(self, document, elements):
        box = geometry.selection_bounds(elements)
        return None if box is None else box[self._axis]

    def limits(self, document, elements):
        box = geometry.selection_bounds(elements)
        if box is None:
            return None
        room = ((document.label_width - box[2]) if self._axis == 0
                else (document.label_height - box[3]))
        return 0, max(0, room)

    def write(self, document, elements, value) -> None:
        box = geometry.selection_bounds(elements)
        if box is None:
            return
        delta = int(value) - box[self._axis]
        dx, dy = (delta, 0) if self._axis == 0 else (0, delta)
        geometry.move_selection(document, elements, dx, dy)


def _set(attribute):
    """A put that sets one attribute and needs nothing after it."""
    def put(_document, element, value):
        setattr(element, attribute, value)
    return put


def _hold_thickness(element) -> None:
    """Keep a border no thicker than the shape's new size allows - what the
    editors do when a side shrinks under the thickness."""
    element.thickness = max(1, min(element.thickness, element.max_thickness()))


def _settle(document, element) -> None:
    """Re-measure an element after a change to its size or turn, as its own
    editor does on the way out."""
    if isinstance(element, TextElement):
        document.sync_text_width(element)
    elif isinstance(element, (BarcodeElement, GraphicSymbolElement)):
        element.sync_box()


def _features(element):
    return BARCODE_FEATURES[element.symbology]


# --- which elements have what ------------------------------------------------

_INKED = (TextElement, FrameElement, CircleElement, EllipseElement,
          DiagonalLineElement, GraphicSymbolElement, BarcodeElement)
_SHAPES = (FrameElement, CircleElement, EllipseElement, DiagonalLineElement)
_SIDED = (FrameElement, EllipseElement, DiagonalLineElement)


def _is(*classes):
    return lambda element: isinstance(element, classes)


def _turns(element) -> bool:
    if isinstance(element, (TextElement, GraphicSymbolElement)):
        return True
    return isinstance(element, BarcodeElement) and _features(element)['orientation']


# --- the sides of the shapes that have two -----------------------------------
#
# Each type's editor range: ^GB's frame editor offers 10-800 by 10-1200, ^GE
# and ^GD their own commands' ranges.

def _side_limits(axis):
    def limits(element):
        if isinstance(element, FrameElement):
            return (10, 800) if axis == 'width' else (10, 1200)
        return element.MIN_SIDE, element.MAX_SIDE
    return limits


def _put_side(axis):
    def put(_document, element, value):
        setattr(element, axis, value)
        _hold_thickness(element)
    return put


def _put_diameter(_document, element, value):
    element.diameter = value
    element.sync_box()
    _hold_thickness(element)


def _put_turn(document, element, value):
    element.orientation = value
    _settle(document, element)


def _put_settled(attribute):
    """A put that sets one attribute and then re-measures the element."""
    def put(document, element, value):
        setattr(element, attribute, value)
        _settle(document, element)
    return put


class _ModuleWidth(Property):
    """Module Width, under the name the barcodes' own editor gives it when
    they agree on one - a matrix code calls it a size or a magnification."""

    def title(self, elements) -> str:
        names = {_features(element)['module_width'] for element in elements}
        return f"{names.pop()}:" if len(names) == 1 else self.label


# --- the tables ---------------------------------------------------------------

X = _Position('x', "X:", 0)
Y = _Position('y', "Y:", 1)
PRINT = Property('print_enabled', "Print This Element", BOOL,
                 get=lambda element: element.print_enabled,
                 put=_set('print_enabled'))

HEADER = (X, Y, PRINT)

SHARED = (
    Property('width', "Width:", INT, applies=_is(*_SIDED),
             get=lambda element: element.width, put=_put_side('width'),
             limits=_side_limits('width')),
    Property('height', "Height:", INT, applies=_is(*_SIDED),
             get=lambda element: element.height, put=_put_side('height'),
             limits=_side_limits('height')),
    Property('diameter', "Diameter:", INT, applies=_is(CircleElement),
             get=lambda element: element.diameter, put=_put_diameter,
             limits=lambda element: (element.MIN_DIAMETER, element.MAX_DIAMETER)),
    # Under the narrowest of the shapes' own maximums, so no shape is given a
    # border thicker than it can hold.
    Property('thickness', "Thickness:", INT, applies=_is(*_SHAPES),
             get=lambda element: element.thickness, put=_set('thickness'),
             limits=lambda element: (1, element.max_thickness())),
    Property('colour', "Colour:", CHOICE, applies=_is(*_SHAPES),
             get=lambda element: element.colour, put=_set('colour'),
             choices=FRAME_COLOURS),
    Property('rounding', "Corner Rounding:", INT, applies=_is(FrameElement),
             get=lambda element: element.rounding, put=_set('rounding'),
             limits=lambda element: (0, FrameElement.MAX_ROUNDING)),
    Property('symbol_height', "Height:", INT, applies=_is(GraphicSymbolElement),
             get=lambda element: element.font_height,
             put=_put_settled('font_height'),
             limits=lambda element: (element.MIN_SIZE, element.MAX_SIZE)),
    Property('symbol_width', "Width:", INT, applies=_is(GraphicSymbolElement),
             get=lambda element: element.font_width,
             put=_put_settled('font_width'),
             limits=lambda element: (element.MIN_SIZE, element.MAX_SIZE)),
    Property('font_height', "Font Height:", INT, applies=_is(TextElement),
             get=lambda element: element.font_height,
             put=_put_settled('font_height'),
             limits=lambda element: (8, 500)),
    Property('font_width', "Font Width:", INT, applies=_is(TextElement),
             get=lambda element: element.font_width,
             put=_put_settled('font_width'),
             limits=lambda element: (8, 500)),
    Property('bar_height', "Bar Height:", INT,
             applies=lambda element: (isinstance(element, BarcodeElement)
                                      and _features(element)['height'] is not None),
             get=lambda element: element.bar_height,
             put=_put_settled('bar_height'),
             limits=lambda element: _features(element)['height']),
    _ModuleWidth('module_width', "Module Width:", INT,
                 applies=lambda element: (
                     isinstance(element, BarcodeElement)
                     and _features(element)['module_width'] is not None),
                 get=lambda element: element.module_width,
                 put=_put_settled('module_width'),
                 limits=lambda element: (1, 20)),
    Property('orientation', "Orientation:", CHOICE, applies=_turns,
             get=lambda element: element.orientation, put=_put_turn,
             choices=ORIENTATIONS),
    Property('reverse_print', "Reverse print (^FR)", BOOL, applies=_is(*_INKED),
             get=lambda element: element.reverse_print, put=_set('reverse_print')),
)


def header_properties(elements) -> tuple:
    """X, Y and Print This Element, or nothing for an empty selection."""
    return HEADER if any(element is not None for element in elements) else ()


def shared_properties(elements) -> list:
    """The shared properties every one of these elements has, in table order."""
    elements = [element for element in elements if element is not None]
    if not elements:
        return []
    return [prop for prop in SHARED
            if all(prop.applies(element) for element in elements)]


def find(key: str) -> Property:
    """A header or shared property by its key."""
    for prop in HEADER + SHARED:
        if prop.key == key:
            return prop
    raise KeyError(key)
