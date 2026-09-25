"""
The ZPL document: label, elements, and the ZPL they serialise to.

Nothing here imports a GUI toolkit. The rules that decide whether a label
prints the way the canvas showed it - derived text width, derived barcode
width, the 1-bit bit order, the base64 wrapping of hidden elements - all live
in this module, so they can be exercised without a display.
"""

import base64 as _b64
import copy
import io as _io
from typing import Dict, List, Optional

from PIL import (Image as PILImage, ImageDraw as PILImageDraw,
                 ImageFont as PILImageFont)

from . import code128
from . import code39
from . import ean13
from . import i2of5
from . import qr
from . import upcext
from . import fields as zpl_fields
from . import fonts as zpl_fonts
from . import graphic_store
from . import graphics
from . import transforms as zpl_transforms
from . import geometry
from . import symbology as symbologies


class DesignElement:
    """Base class for design elements.

    Every measurement on an element is in printer dots. No pixels, no points,
    no millimetres are stored anywhere: ZPL is dots, and a dot is only a
    physical size once a head resolution is chosen.
    """

    x: int
    y: int
    width: int
    height: int
    element_type: str  # 'text', 'frame', 'barcode', 'image'

    # Class attributes, so every element inherits the default without each
    # __init__ having to set it.
    print_enabled = True
    # Dots from this element's top down to the ^FT baseline it was placed by,
    # or None when it was placed by ^FO. Kept rather than normalised away so a
    # file written with ^FT is written back with ^FT, at the same y.
    typeset = None
    # ^FR: this field prints in reverse - white where the label would
    # otherwise be black, and vice versa.
    reverse_print = False
    # The groups this element is in, as a tuple of ids from the outermost
    # in, or None for none: (3, 7) is "in group 7, which is inside group 3".
    # Ids are unique across the document at every depth. A tuple rather than
    # a reference to the other members, so a snapshot's shallow copy carries
    # it and undo cannot leave a group pointing at elements that were
    # replaced. A group itself is derived: every element whose path holds
    # its id.
    group = None

    def origin_zpl(self, offset=(0, 0)) -> str:
        """The ^FO or ^FT that places this element.

        One method rather than an ^FO formatted into each element's to_zpl, so
        a label that came in typeset cannot go out typeset in some of its
        fields and not others.

        `offset` is what ^LH and ^LS added on the way in. An element holds the
        absolute dot position - so the canvas, dragging and clamping need to
        know nothing about either command - and the offset comes back out here,
        which is what lets a file carrying one be written back unchanged.
        """
        x = self.x - offset[0]
        y = self.y - offset[1]
        if self.typeset is None:
            return f"^FO{x},{y}\n"
        return f"^FT{x},{y + self.typeset}\n"

    def reverse_zpl(self) -> str:
        """^FR, if this field reverses its own print."""
        return "^FR\n" if self.reverse_print else ""

    # What a field's data is called on the subclasses that have any. ^FD and ^FN
    # are written the same way for text and for a barcode, so the rule lives
    # here rather than being spelled twice and drifting.
    data_attribute = None
    field_number = None
    field_prompt = None

    # ^SN: the printer increments this field's value each time it prints.
    # `serial_start` is kept apart from the field's own literal because ^SN
    # can appear with no ^FD at all, in which case it is the only value the
    # file gives this field.
    serial_start = None
    serial_increment = None
    serial_leading_zero = False

    # ^FC: the printer splices its real-time clock into this field's literal
    # at print time. `clock_chars` is the (a, b, c) trigger-character triple
    # ^FC names, or None to mean the file left it at the ZPL default.
    clock_format = False
    clock_chars = None

    # ^FH: the character that marks a hex escape (indicatorXX) in this
    # field's literal, or None when the file gave none. The literal itself
    # stays raw - see data_literal() - so a save writes ^FH back unchanged;
    # only display_text()/encoded_value() decode it.
    hex_indicator = None

    # ^SF (deprecated): kept only as opaque, unparsed params so a file that
    # carries one round-trips unchanged - its mask-character semantics are
    # not modelled.
    serial_field_raw = None

    def data_literal(self) -> str:
        """The literal this field prints, as the file gave it."""
        if not self.data_attribute:
            return ''
        return getattr(self, self.data_attribute, '') or ''

    def display_text(self, table=None) -> str:
        """What a canvas draws for this field.

        A numbered field with no literal of its own has nothing to draw and
        would be an invisible element on the design, so it shows its prompt or
        its number instead. The preview does not use this - it answers "what
        will print", and an unfilled ^FN prints nothing until the printer
        substitutes for it.

        ^SN and ^FC are different: the literal they carry is real content (a
        starting serial value, a clock-format string), not a stand-in, so it
        is shown with a marker rather than replaced by one.
        """
        literal = zpl_fields.decode_hex(self.data_literal(), self.hex_indicator)
        if self.serial_increment is not None:
            base = literal or self.serial_start or ''
            return zpl_fields.serial_display(base, self.serial_increment)
        if self.clock_format:
            return zpl_fields.clock_display(literal)
        if literal or self.field_number is None:
            return literal
        if table is not None:
            return table.display(self.field_number, self.field_prompt)
        return zpl_fields.placeholder(self.field_number, self.field_prompt)

    def data_zpl(self) -> str:
        """^FC/^FD/^SN/^SF/^FN as this field carries them, then the closing ^FS.

        A plain field writes ^FD exactly as it always did, which is what keeps
        every existing file byte-identical. ^FC has to precede the ^FD it
        modifies; ^SN and ^SF follow it, matching how a printer-generated
        field is conventionally written. A numbered field's ^FN/^FD pairing is
        unchanged from before - ZPL allows both together, and means by it that
        this field's data also fills every other field sharing the number.
        """
        literal = self.data_literal()
        zpl = ''
        if self.clock_format:
            chars = self.clock_chars or zpl_fields._CLOCK_DEFAULTS
            zpl += f"^FC{zpl_fields.clock_chars_zpl(chars)}"
        if self.hex_indicator:
            zpl += f"^FH{self.hex_indicator}"
        if self.field_number is None:
            zpl += f"^FD{literal}"
        else:
            name = f'"{self.field_prompt}"' if self.field_prompt is not None else ''
            data = f"^FD{literal}" if literal else ''
            zpl += f"^FN{self.field_number}{name}{data}"
        if self.serial_increment is not None:
            leading_zero = 'Y' if self.serial_leading_zero else 'N'
            zpl += f"^SN{self.serial_start},{self.serial_increment},{leading_zero}"
        if self.serial_field_raw is not None:
            zpl += f"^SF{self.serial_field_raw}"
        return f"{zpl}^FS\n"

    def contains_point(self, x: int, y: int) -> bool:
        """Check if point is within element bounds."""
        return (self.x <= x <= self.x + self.width and
                self.y <= y <= self.y + self.height)


class FieldBlock:
    """^FB - the block a piece of text is wrapped into.

    Width is in dots. Lines past `max_lines` are dropped rather than
    overflowing, which is what the printer does with them.
    """

    JUSTIFICATIONS = ('L', 'C', 'R', 'J')

    def __init__(self, width: int, max_lines: int = 1, line_spacing: int = 0,
                 justification: str = 'L', indent: int = 0):
        self.width = max(1, int(width))
        self.max_lines = max(1, int(max_lines))
        self.line_spacing = int(line_spacing)
        self.justification = (justification or 'L').upper()
        if self.justification not in self.JUSTIFICATIONS:
            self.justification = 'L'
        self.indent = int(indent)

    @classmethod
    def from_zpl(cls, params: str) -> 'FieldBlock':
        """^FB<width>,<max lines>,<line spacing>,<justification>,<indent>.

        Read here rather than in each caller: the parser and the preview
        renderer both meet ^FB, and two readings of it would eventually
        disagree about a label neither of them wrote.
        """
        parts = [p.strip() for p in params.split(',')]

        def number(index, fallback):
            try:
                return int(parts[index])
            except (IndexError, ValueError):
                return fallback

        justification = parts[3].upper() if len(parts) > 3 and parts[3] else 'L'
        return cls(number(0, 1), number(1, 1), number(2, 0),
                   justification, number(4, 0))

    def copy(self) -> 'FieldBlock':
        """An independent copy, for a snapshot that a later edit must not reach."""
        return FieldBlock(self.width, self.max_lines, self.line_spacing,
                          self.justification, self.indent)

    def to_zpl(self) -> str:
        return (f"^FB{self.width},{self.max_lines},{self.line_spacing},"
                f"{self.justification},{self.indent}")

    def __eq__(self, other):
        return isinstance(other, FieldBlock) and vars(self) == vars(other)

    def __repr__(self):
        return f"FieldBlock({self.to_zpl()[3:]})"


def _copy_element(element):
    """A copy of one element that a later edit cannot reach back through.

    Shallow, except for a text element's field block: that is an object rather
    than a scalar, and a drag resizes it in place, so sharing one between
    snapshots would rewrite every undo entry on the stack.
    """
    clone = copy.copy(element)
    block = getattr(clone, 'block', None)
    if block is not None:
        clone.block = block.copy()
    return clone


class TextElement(DesignElement):
    """Text element for the designer."""

    data_attribute = 'text'

    # Lines a block gets when wrapping is first switched on. The text is not
    # wrapping yet at that width, so this is only how much room it has to grow
    # into before the printer starts dropping lines.
    DEFAULT_MAX_LINES = 4

    def __init__(self, x: int = 50, y: int = 50, text: str = "Label",
                 font_height: int = 36, font_width: int = 20,
                 font_code: str = 'F', orientation: str = 'N',
                 field_number=None, field_prompt=None,
                 serial_start=None, serial_increment=None,
                 serial_leading_zero=False,
                 clock_format=False, clock_chars=None,
                 serial_field_raw=None, hex_indicator=None):
        self.x = x
        self.y = y
        self.text = text
        # ^FN: this field's data comes from the printer at print time, and
        # `text` holds only a literal the file actually gave. Keeping the
        # placeholder out of `text` is what stops a prompt being written back
        # as if it were data.
        self.field_number = field_number
        self.field_prompt = field_prompt
        # ^SN, ^FC, ^SF: the other ways a printer supplies this field's value
        # instead of the design - see DesignElement for what each one means.
        self.serial_start = serial_start
        self.serial_increment = serial_increment
        self.serial_leading_zero = serial_leading_zero
        self.clock_format = clock_format
        self.clock_chars = clock_chars
        self.serial_field_raw = serial_field_raw
        self.hex_indicator = hex_indicator
        self.font_height = font_height
        self.font_width = font_width
        self.width = len(text) * font_width
        self.height = font_height
        self.element_type = 'text'
        self.font_path: Optional[str] = None
        self.font_family: Optional[str] = None
        self.printer_font_name: Optional[str] = None
        # Built-in font designator: 'F' is what this designer has always
        # written, '0' the scalable font most other tools reach for.
        self.font_code = font_code
        # ^A's orientation, the letter before the sizes. `width` and `height`
        # are the element's footprint, transposed at a quarter turn, so the
        # shared geometry only ever sees an axis-aligned box - which is why
        # rotating text needs nothing from hit-testing or dragging.
        self.orientation = (orientation or 'N').upper()
        # ^FB, when the text is a wrapped block rather than a single line
        self.block: Optional['FieldBlock'] = None

    def _measure(self, font_path: str, text=None) -> float:
        """Advance width of the text at em = font_height, or 0 if unmeasurable.

        `text` overrides the literal, so a ^FN placeholder is measured by what
        the canvas actually draws for it. Measuring the empty literal instead
        gave a one-dot box under a visible placeholder, which could not be
        clicked on the element it belonged to.
        """
        shown = self.text if text is None else text
        try:
            font = PILImageFont.truetype(font_path, max(1, self.font_height))
            draw = PILImageDraw.Draw(PILImage.new('RGBA', (1, 1)))
            return draw.textlength(shown or " ", font=font)
        except Exception:
            return 0.0

    def printed_width(self, default_font_path: Optional[str] = None,
                      text=None) -> int:
        """Width in dots this text will actually occupy on the printer.

        ^AF selects Zebra's built-in font A, which is fixed width, so
        len(text) * font_width holds. ^A@ selects a downloaded TrueType, which
        is proportional - every glyph has its own advance - so the string has
        to be measured. Assuming fixed width there is what made "IIII" print
        far narrower and "WWWW" far wider than the designer showed.
        """
        shown = self.text if text is None else text
        font_path = self.font_path or default_font_path
        natural = self._measure(font_path, shown) if font_path else 0.0
        if natural <= 0:
            return max(1, len(shown) * self.font_width)
        # The printer scales the em square to font_width x font_height, so an
        # advance measured at font_height scales by font_width / font_height.
        return max(1, round(natural * self.font_width / max(1, self.font_height)))

    def font_width_for(self, target_width: int,
                       default_font_path: Optional[str] = None,
                       text=None) -> int:
        """The font_width that makes this text print target_width dots wide."""
        shown = self.text if text is None else text
        font_path = self.font_path or default_font_path
        natural = self._measure(font_path, shown) if font_path else 0.0
        if natural <= 0:
            # An empty literal - a ^FN placeholder has one - would divide by a
            # clamped 1 and ask for a font as wide as the whole box.
            return max(1, round(target_width / max(1, len(shown))))
        return max(1, round(target_width * max(1, self.font_height) / natural))

    def rotated(self) -> bool:
        """Whether the text runs down or up the label rather than across it."""
        return self.orientation in ('R', 'B')

    def default_block(self, default_font_path: Optional[str] = None) -> 'FieldBlock':
        """A block that wraps this text where it already ends.

        Switching wrapping on should not move anything: the width is what the
        longest line prints at now, so what the user sees first is the text
        unchanged, ready to be narrowed.
        """
        from . import textraster
        measure, _font = textraster.measurer(
            self.font_path or default_font_path, self.font_height, self.font_width)
        lines = (self.text or "").split(textraster.FORCED_BREAK)
        widest = max((measure(line) for line in lines), default=0)
        # Rounded up, not to nearest: a block a fraction of a dot narrower than
        # the line it was measured from would wrap that line immediately.
        return FieldBlock(max(1, int(widest) + 1),
                          max(self.DEFAULT_MAX_LINES, len(lines)))

    def to_zpl(self, printer_font_name: Optional[str] = None,
               offset=(0, 0)) -> str:
        """Convert to ZPL commands."""
        effective_font = self.printer_font_name or printer_font_name
        turn = self.orientation or 'N'
        zpl = self.origin_zpl(offset)
        if effective_font:
            zpl += f"^A@{turn},{self.font_height},{self.font_width},E:{effective_font}.TTF\n"
        else:
            zpl += f"^A{self.font_code}{turn},{self.font_height},{self.font_width}\n"
        if self.block is not None:
            zpl += self.block.to_zpl() + "\n"
        # ^FR immediately before the data it reverses, not right after ^FO -
        # the working convention, and the one place this differed from it.
        zpl += self.reverse_zpl()
        zpl += self.data_zpl()
        return zpl


class FrameElement(DesignElement):
    """Frame element for the designer.

    ^GB carries a colour and a corner rounding after the thickness. Dropping
    them turned a white box black and squared off every rounded corner, without
    saying so.
    """

    # ZPL's defaults for the parameters after the thickness, in order. A frame
    # written with these is written without them, so a label this designer
    # created serialises exactly as it always did.
    DEFAULTS = ('B', 0)
    COLOURS = ('B', 'W')
    MAX_ROUNDING = 8

    def __init__(self, x: int = 100, y: int = 100, width: int = 200,
                 height: int = 150, thickness: int = 2,
                 colour: str = 'B', rounding: int = 0):
        self.x = x
        self.y = y
        self.width = width
        self.height = height
        self.thickness = thickness
        self.colour = (colour or 'B').upper()
        if self.colour not in self.COLOURS:
            self.colour = 'B'
        self.rounding = max(0, min(int(rounding or 0), self.MAX_ROUNDING))
        self.element_type = 'frame'

    def max_thickness(self) -> int:
        """Thickest useful border: at half the smaller side it fills solid."""
        return max(1, min(self.width, self.height) // 2)

    def corner_radius(self) -> float:
        """The corner radius in dots, from ^GB's 0-8 rounding index.

        8 is the most ZPL will round, which is half the shorter side - at which
        point the ends are semicircles.
        """
        if not self.rounding:
            return 0.0
        return (self.rounding / self.MAX_ROUNDING) * min(self.width, self.height) / 2

    def _options_zpl(self) -> str:
        """The colour and rounding, trimmed after the last non-default one."""
        given = [self.colour, self.rounding]
        keep = 0
        for index, value in enumerate(given):
            if value != self.DEFAULTS[index]:
                keep = index + 1
        return ''.join(f",{value}" for value in given[:keep])

    def to_zpl(self, offset=(0, 0)) -> str:
        """Convert to ZPL commands."""
        return (self.origin_zpl(offset) + self.reverse_zpl() +
                f"^GB{self.width},{self.height},{self.thickness}"
                f"{self._options_zpl()}\n^FS\n")


class BarcodeElement(DesignElement):
    """Barcode element for the designer, whichever symbology `symbology`
    names.

    Each symbology has its own encoder module (code128.py, code39.py, ...),
    matching this element's own job of holding what ^BC and its siblings
    carry by name rather than as a tail of strings, so the canvas can draw
    what each parameter will actually do. The parameters themselves differ
    per symbology - EAN-13 has no check-digit flag because its own is not
    optional, Code 39 spells its check digit before the height instead of
    after - so the shape of every command is read from one table,
    zplcore.symbology, that the parser and both editors read too.

    `bar_height` is the bars themselves. `width` and `height` are the
    element's footprint: the symbol plus the interpretation line, transposed
    when the barcode is rotated. The shared geometry only ever sees the
    footprint, which is why rotating one needs nothing from it.
    """

    # Kept as class attributes because the frontends and the tests reach for
    # them, but sourced from the one catalogue so they cannot drift from the
    # parser's idea of the same thing.
    SYMBOLOGIES = tuple(symbologies.SYMBOLOGIES)
    COMMANDS = {key: cmd[1:] for key, cmd in symbologies.COMMAND.items()}
    ORIENTATIONS = ('', 'N', 'R', 'I', 'B')
    MODES = ('N', 'U', 'A', 'D')

    # ^BY's wide-to-narrow ratio. Carried but not modelled for the
    # fixed-ratio symbologies - the manual is explicit that it "has no effect
    # on fixed-ratio bar codes" - and applied to the rest, whose own wide
    # elements are drawn at this many narrow modules, rounded to a whole one
    # the same way a module width itself always is (see `modules`).
    DEFAULT_RATIO = 3.0

    data_attribute = 'barcode_value'

    # Gap between the bars and the interpretation line, in dots
    TEXT_GAP = 2
    # The font used for the interpretation line when the line is switched on
    # and the file named none
    DEFAULT_FONT = ('0', 20, 20)

    def __init__(self, x: int = 50, y: int = 200, height: int = 100,
                 barcode_value: str = "123456789", module_width: int = 2,
                 orientation: str = '', options: tuple = (),
                 font: Optional[tuple] = None,
                 ratio: float = DEFAULT_RATIO,
                 symbology: str = 'code128',
                 params: Optional[dict] = None,
                 total_height: Optional[int] = None,
                 field_number=None, field_prompt=None,
                 serial_start=None, serial_increment=None,
                 serial_leading_zero=False,
                 clock_format=False, clock_chars=None,
                 serial_field_raw=None, hex_indicator=None):
        self.x = x
        self.y = y
        self.bar_height = height
        self.barcode_value = barcode_value
        self.field_number = field_number
        self.field_prompt = field_prompt
        self.serial_start = serial_start
        self.serial_increment = serial_increment
        self.serial_leading_zero = serial_leading_zero
        self.clock_format = clock_format
        self.clock_chars = clock_chars
        self.serial_field_raw = serial_field_raw
        self.hex_indicator = hex_indicator
        self.module_width = module_width
        self.ratio = float(ratio)
        self.orientation = orientation
        self.symbology = (symbology if symbology in symbologies.SYMBOLOGIES
                          else 'code128')
        # The ^BY height in force where this barcode was read, for the
        # symbologies whose own h is optional and whose size comes from the
        # whole symbol's height rather than one row of it. None until one is
        # needed, so a barcode that never asks carries nothing.
        self.total_height = total_height
        # The font a ^A before the barcode command selected, as
        # (code, height, width). It sets the interpretation line, so losing
        # it would change the label even though no text element uses it.
        self.font = tuple(font) if font else None
        self.element_type = 'barcode'

        # The trailing options, by name, in the canonical (show, above,
        # check, mode) order every symbology's constructor call uses
        # regardless of how its own ZPL command spells them. Each missing
        # one falls back to *that position's* default - padding with the
        # defaults as a suffix would slide them along, so ^BC,100,N would
        # read as "no line, printed above". The UPC/EAN extension is the one
        # symbology whose own default for "above" is Y, not N.
        defaults = self.DEFAULTS
        given = list(options)
        show, above, check, mode = [
            given[i] if i < len(given) and given[i] != '' else defaults[i]
            for i in range(4)]
        self.show_text = str(show).upper() != 'N'
        self.text_above = str(above).upper() == 'Y'
        self.check_digit = str(check).upper() == 'Y'
        self.mode = str(mode).upper() if str(mode).upper() in self.MODES else 'N'
        if self.symbology in symbologies.NO_TEXT:
            # A symbology with no interpretation line has no f parameter to
            # switch one on with either. Leaving show_text at its own default
            # would draw the raw field data - a QR code's `MM,A` switches and
            # all - in a line under a symbol that never has one.
            self.show_text = self.text_above = False

        # Everything past the six parameters the whole family shares, held
        # by name the same way. An omitted one takes the symbology's own
        # default rather than a blank, so nothing downstream has to know
        # which parameters a file happened to spell.
        self._read_params(params or {})

        self.sync_box()

    def _read_params(self, given: dict) -> None:
        """The symbology's own parameters, read from their ZPL spellings."""
        for name, spec in symbologies.PARAMETERS.items():
            raw = given.get(name, '')
            setattr(self, name, spec.read(raw, self.symbology))

    @property
    def DEFAULTS(self) -> tuple:
        """What an omitted f, g, e or m means for this symbology.

        A property rather than a value fixed at construction because both
        editors change `symbology` on the element in place: a barcode
        switched to the UPC/EAN extension, whose line prints above the bars
        by default, would otherwise go on being trimmed against the
        defaults of whatever it used to be.
        """
        return symbologies.flag_defaults(self.symbology)

    # --- what the printer will make of it -----------------------------------

    def _raw_value(self) -> str:
        """The field data, hex-decoded, before any symbology processing."""
        return zpl_fields.decode_hex(self.barcode_value, self.hex_indicator)

    def encoded_value(self) -> str:
        """The data the symbol carries, and the interpretation line shows."""
        value = self._raw_value()
        if self.symbology == 'ean13':
            return ean13.normalize(value)
        if self.symbology == 'upcean_extension':
            return upcext.normalize(value)
        if self.symbology == 'interleaved2of5':
            if self.check_digit:
                value += code128.ucc_check_digit(value)
            return i2of5.normalize(value)
        if self.check_digit:
            value += (code39.mod43_check_digit(value) if self.symbology == 'code39'
                      else code128.ucc_check_digit(value))
        return value

    def symbol(self) -> tuple:
        """What the printer will actually lay down, as (kind, payload).

        `('linear', modules)` is the bar and space widths of a
        one-dimensional symbol, alternating, starting with a bar.
        `('grid', rows)` is a matrix symbology, a list of rows of booleans,
        dark where True. Every drawing path goes through
        `geometry.barcode_layout`, which turns whichever kind this is into
        plain rectangles, so no canvas has to know the difference.

        An encoder that cannot produce a symbol - data too long for any
        version, or its package not installed on this machine - leaves the
        element its footprint and draws nothing, with the reason on
        `symbol_error` for the frontends to show. A printer prints no symbol
        in the same case; refusing to open the label instead would lose
        every other field on it.
        """
        self.symbol_error = None
        if self.symbology not in symbologies.MATRIX:
            return ('linear', self.modules())
        try:
            return ('grid', self._grid())
        except ImportError as exc:
            self.symbol_error = (
                f"{symbologies.SYMBOLOGIES[self.symbology]} needs a Python "
                f"package this machine does not have: {exc.name}.")
        except Exception as exc:                        # noqa: BLE001
            self.symbol_error = (
                f"{symbologies.SYMBOLOGIES[self.symbology]}: {exc}")
        return ('grid', [])

    def _grid(self) -> list:
        """The matrix symbology's own modules, as rows of booleans."""
        if self.symbology == 'qr':
            value = self._raw_value()
            return qr.encode(value, qr.error_correction(value, self.quality),
                             self.qr_mask)
        raise ValueError(f"no encoder for {self.symbology!r}")

    def modules(self) -> list:
        """The bar and space widths of the symbol, in modules.

        EAN-13 and the UPC/EAN extension always fit and checksum their own
        way (see their `normalize`), which `encoded_value` also calls for the
        interpretation line - encoding straight from the raw value here
        rather than from that result avoids re-fitting an already-fitted
        string, which would corrupt it.
        """
        if self.symbology == 'ean13':
            return ean13.encode(self._raw_value())
        if self.symbology == 'upcean_extension':
            return upcext.encode(self._raw_value())
        value = self.encoded_value()
        if self.symbology == 'code128':
            return code128.encode(value, self.mode)
        if self.symbology == 'code39':
            return self._ratio_scaled(code39.encode(value))
        return self._ratio_scaled(i2of5.encode(value))  # interleaved2of5

    def _ratio_scaled(self, mods: list) -> list:
        """Code 39 and Interleaved 2 of 5 encode a wide element as 2 - twice
        a narrow one - because neither knows this barcode's own ratio. This
        is where that 2 becomes however many narrow modules the ratio asks
        for, rounded to a whole one the way a module width itself always is.
        """
        wide = max(1, round(self.ratio))
        return [wide if m == 2 else m for m in mods]

    def symbol_size(self) -> tuple:
        """(run, stack) in dots: the symbol alone, before the line under it.

        The run is along the bars' length and the stack across them, in the
        barcode's own unrotated frame. A matrix symbology's stack is its own
        grid rather than `bar_height`, which is why this and not the height
        itself is what the footprint and every layout are measured from.
        """
        kind, payload = self.symbol()
        module = max(1, self.module_width)
        if kind == 'linear':
            run = max(1, sum(payload) * module)
            return (run, max(1, self.bar_height))
        if kind == 'grid':
            # A symbol that could not be built keeps the footprint of the
            # smallest one its symbology has, so the element stays somewhere
            # the user can select it and read why.
            rows = len(payload) or symbologies.PLACEHOLDER_GRID
            columns = len(payload[0]) if payload else symbologies.PLACEHOLDER_GRID
            return (columns * module, rows * module)
        raise ValueError(f"unknown symbol kind {kind!r}")

    def printed_width(self) -> int:
        """The symbol, end to end, in dots.

        Summed from the symbol rather than from a character count, because no
        formula covers subset C - there two digits share one symbol, and a
        numeric barcode is about two thirds the width the count would predict.
        """
        return self.symbol_size()[0]

    def text_height(self) -> int:
        """Dots the interpretation line occupies, including its gap."""
        if not self.show_text:
            return 0
        font = self.font or self.DEFAULT_FONT
        return int(font[1]) + self.TEXT_GAP

    def rotated(self) -> bool:
        """Whether the barcode is turned on its side."""
        return self.orientation.upper() in ('R', 'B')

    def sync_box(self) -> None:
        """Set the footprint from what the barcode will actually print."""
        run, stack = self.symbol_size()
        stack += self.text_height()
        self.width, self.height = (stack, run) if self.rotated() else (run, stack)

    # --- serialisation ------------------------------------------------------

    def _param_zpl(self, name: str) -> str:
        """One parameter of this barcode's own command, as ZPL spells it."""
        if name == 'o':
            return self.orientation
        if name == 'h':
            return str(self.bar_height)
        if name == 'w':
            # A matrix symbology spells its module width in its own command,
            # as a magnification, rather than deferring to ^BY.
            return str(max(1, self.module_width))
        if name == 'f':
            return 'Y' if self.show_text else 'N'
        if name == 'g':
            return 'Y' if self.text_above else 'N'
        if name == 'e':
            return 'Y' if self.check_digit else 'N'
        if name == 'm':
            return self.mode
        return symbologies.PARAMETERS[name].write(getattr(self, name))

    def _param_default(self, name: str) -> str:
        """What that parameter means when the command leaves it out."""
        if name == 'o':
            return ''
        if name in symbologies.FLAG_PARAMS:
            return self.DEFAULTS[symbologies.FLAG_PARAMS.index(name)]
        return symbologies.PARAMETERS[name].default_zpl(self.symbology)

    def _command_zpl(self) -> str:
        """The barcode command itself, trimmed after the last parameter that
        is not that position's default.

        Every command's parameters are read from the one catalogue, in the
        order that command spells them, so ^B3 - whose check digit comes
        before its height rather than after - needs no special case here or
        in the parser. A parameter the printer would otherwise resolve for
        itself is always written (see symbology.ALWAYS_WRITTEN), so a file
        this designer saves never comes back a different size on a printer
        with different defaults.
        """
        command = symbologies.COMMAND[self.symbology]
        names = symbologies.COMMAND_PARAMS[command]
        values = [self._param_zpl(name) for name in names]
        while values:
            last = names[len(values) - 1]
            if last in symbologies.ALWAYS_WRITTEN:
                break
            if values[-1] != self._param_default(last):
                break
            values.pop()
        return command + ",".join(values) + "\n"

    def _font_zpl(self) -> str:
        """The interpretation line's font, if one was chosen."""
        if not self.font:
            return ""
        code, height, width = self.font
        return f"^A{code}N,{height},{width}\n"

    def _by_zpl(self) -> str:
        """^BY, with the ratio only when it is not ZPL's default.

        Trimmed the way _options_zpl trims ^BC's tail, so a barcode this
        designer created serialises exactly as it always did and only a file
        that actually carried a ratio gets one written back.

        Written only for the symbologies that read it. A matrix code carries
        its own magnification in its own command and ignores ^BY's w
        entirely, so writing one would state a module width that is not the
        one being drawn.
        """
        if self.symbology not in symbologies.READS_BY:
            return ""
        width = max(1, self.module_width)
        if abs(self.ratio - self.DEFAULT_RATIO) < 1e-9:
            return f"^BY{width}\n"
        # One decimal place is how ZPL spells it: 2.0 to 3.0 in 0.1 increments.
        return f"^BY{width},{self.ratio:.1f}\n"

    def to_zpl(self, offset=(0, 0)) -> str:
        """Convert to ZPL commands."""
        # ^BY sets the module width. Without it the printer uses its own default
        # of 2 dots, which pins the barcode's physical size to the head
        # resolution and makes it the one element that cannot be rescaled.
        #
        # The height goes on the barcode command itself, which is why ^BY's
        # own h is read but never written: there is nowhere for it to disagree.
        preamble = (self.origin_zpl(offset) + self._by_zpl() + self._font_zpl())
        # ^FR immediately before the data it reverses, not right after ^FO -
        # the working convention, and the one place this differed from it.
        return (preamble + self._command_zpl() + self.reverse_zpl()
                + self.data_zpl())


# The choices both frontends offer for a barcode, as (label, value). Here
# rather than in either toolkit's dialog code, because a frontend offering a
# different set would produce a different label from the same design.
ORIENTATIONS = (("Normal", 'N'), ("Rotated 90°", 'R'),
                ("Upside down", 'I'), ("Rotated 270°", 'B'))
# Text and barcodes turn by the same four quarter turns and ^A and ^BC spell
# them with the same letters, so they offer one list rather than two that could
# drift apart.
BARCODE_ORIENTATIONS = ORIENTATIONS

# The interpretation line, as one choice rather than two flags
BARCODE_TEXT_CHOICES = (("Below the bars", (True, False)),
                        ("Above the bars", (True, True)),
                        ("Not printed", (False, False)))

BARCODE_MODES = (("None", 'N'),
                 ("Automatic (uses subset C for digits)", 'A'),
                 ("UCC case", 'U'),
                 ("UCC/EAN", 'D'))

BARCODE_CHECK_DIGIT = (("No", False), ("Yes", True))

# Which symbologies the editors offer, which of their own rows apply to each,
# and the extra rows a symbology's own parameters need. All three come from
# the catalogue every other reader of it uses; see zplcore/symbology.py.
BARCODE_SYMBOLOGIES = symbologies.BARCODE_SYMBOLOGIES
BARCODE_FEATURES = symbologies.BARCODE_FEATURES
BARCODE_PARAMETERS = symbologies.BARCODE_PARAMETERS

FRAME_COLOURS = (("Black", 'B'), ("White", 'W'))

# ^FB's justification, for the same reason: the wrap a user picks in one
# frontend has to be a wrap the other can pick too.
TEXT_JUSTIFICATIONS = (("Left", 'L'), ("Centred", 'C'),
                       ("Right", 'R'), ("Justified", 'J'))

# ^XG/^IM name a stored image as d:o.x - device, object name, extension - and
# both editors offer the same choices for the same reason every list above
# does.
STORED_GRAPHIC_COMMANDS = (("Recall Graphic (^XG)", 'XG'),
                           ("Image Move (^IM)", 'IM'))
STORED_GRAPHIC_DEVICES = (("R: (DRAM)", 'R'), ("E: (Flash)", 'E'),
                          ("B: (B: memory)", 'B'), ("A: (A: memory)", 'A'))


class ImageElement(DesignElement):
    """Image element for the designer, rendered from a JPG/PNG file."""

    def __init__(self, x: int = 50, y: int = 50, width: int = 200, height: int = 200,
                 image_path: str = "", _pil_image=None):
        self.x = x
        self.y = y
        self.width = width
        self.height = height
        self.image_path = image_path
        self.element_type = 'image'
        self._pil_image = _pil_image  # set when element is decoded from ZPL data
        self._source_image = None     # cached decode of the original
        self._sized_cache = None      # ((w, h), resized image)
        self._render_cache = None     # ((w, h), frontend image)

    def reload(self):
        """Drop the cached renderings after the source image changes."""
        self._source_image = None
        self._sized_cache = None
        self._render_cache = None

    def _get_source_image(self):
        """Decoded source image, cached so redraws do not re-read the file."""
        if self._source_image is None:
            try:
                if self.image_path:
                    img = PILImage.open(self.image_path)
                    img.load()
                elif self._pil_image is not None:
                    img = self._pil_image
                else:
                    return None
                if img.mode not in ('RGB', 'L'):
                    img = img.convert('RGB')
            except Exception:
                return None
            self._source_image = img
        return self._source_image

    def _get_sized_image(self):
        """Source resized to the element size - the resolution the printer gets.

        Always from the original source, never from the previous rendering: a
        re-dither of an already dithered bitmap compounds its error.
        """
        key = (self.width, self.height)
        if self._sized_cache is not None and self._sized_cache[0] == key:
            return self._sized_cache[1]
        src = self._get_source_image()
        if src is None:
            return None
        sized = src.resize((max(1, self.width), max(1, self.height)), PILImage.LANCZOS)
        self._sized_cache = (key, sized)
        return sized

    def get_print_bitmap(self):
        """The exact 1-bit bitmap the printer receives (Floyd-Steinberg dithered)."""
        sized = self._get_sized_image()
        if sized is None:
            return None
        return sized.convert('1')

    def print_rgba(self):
        """The printed bitmap as RGBA, with white transparent.

        White becomes transparent so only black dots are painted, the way the
        printer composites. An opaque image would hide elements underneath on
        screen that still print on paper. Returned as PIL so each frontend can
        wrap it in its own toolkit's image type.
        """
        bitmap = self.get_print_bitmap()
        if bitmap is None:
            return None
        grey = bitmap.convert('L')
        rgba = grey.convert('RGBA')
        rgba.putalpha(grey.point(lambda v: 0 if v else 255))
        return rgba

    def get_print_render(self, convert):
        """print_rgba() passed through a frontend's `convert`, cached by size.

        The conversion belongs to the frontend - GdkPixbuf for GTK, QImage for
        Qt - but the cache belongs here, keyed by the size the bitmap was
        dithered at, because that is what makes it stale.
        """
        key = (self.width, self.height)
        if self._render_cache is not None and self._render_cache[0] == key:
            return self._render_cache[1]
        rgba = self.print_rgba()
        if rgba is None:
            return None
        try:
            rendered = convert(rgba)
        except Exception:
            return None
        self._render_cache = (key, rendered)
        return rendered

    def peek_print_render(self):
        """Last converted image, whatever size it was, without recomputing.

        Used to keep resize drags responsive on large sources; it may be stale,
        so the caller must scale it into the element's current bounds.
        """
        return self._render_cache[1] if self._render_cache is not None else None

    def to_zpl(self, offset=(0, 0)) -> str:
        if not self.image_path and self._pil_image is None:
            return ""

        img_sized = self._get_sized_image()
        if img_sized is None:
            return ""

        # 1-bit encoding for the ZPL printer (^GF only supports 1-bit)
        img_1bit = self.get_print_bitmap()
        bytes_per_row = (self.width + 7) // 8
        total_bytes = bytes_per_row * self.height
        data = graphics.encode(img_1bit, bytes_per_row)

        # Embed full-colour JPEG preview in a ^FX comment so the designer can
        # restore the original image quality when the ZPL file is reopened.
        # Printers ignore ^FX fields entirely.
        preview_bio = _io.BytesIO()
        img_sized.convert('RGB').save(preview_bio, format='JPEG', quality=85, optimize=True)
        b64_preview = _b64.b64encode(preview_bio.getvalue()).decode('ascii')

        zpl = self.origin_zpl(offset)
        zpl += f"^FXDESIGNER_PREVIEW:{b64_preview}\n"
        if self.image_path:
            zpl += f"^FXDESIGNER_PATH:{self.image_path}\n"
        # ^FR immediately before ^GF, not right after ^FO - the comment
        # lines above carry no ink and must not sit between the two.
        zpl += self.reverse_zpl()
        zpl += f"^GFA,{total_bytes},{total_bytes},{bytes_per_row},{data}\n"
        zpl += f"^FS\n"
        return zpl


class StoredGraphicElement(DesignElement):
    """^XG (Recall Graphic) or ^IM (Image Move) - a field that places a
    graphic held in printer storage rather than one embedded in this file.

    This app has no printer to ask, but it does keep its own in-session
    memory of anything a `^IS` it has parsed this run saved - see
    zplcore/graphic_store.py. `resolve()` is looked up live, on every draw,
    rather than cached at parse time: if that memory is empty when this
    element is created, a later file's `^IS` can still fill it in without
    this element needing to be reparsed.

    Whatever `resolve()` returns, `to_zpl()` always writes back the command
    this field named, never the resolved pixels - the whole point of ^XG and
    ^IM is that the printer, not this file, owns the image data. Baking the
    resolved bitmap into a ^GF here would turn a small reference into a large
    embedded image the next time the file was saved, and would drop the
    device path a real printer still needs to look the object up by.
    """

    def __init__(self, x: int = 50, y: int = 50, width: int = 200, height: int = 200,
                 command: str = 'XG', device_spec: str = 'R:UNKNOWN.GRF',
                 mag_x: int = 1, mag_y: int = 1):
        self.x = x
        self.y = y
        self.width = width
        self.height = height
        self.command = command if command in ('IM', 'XG') else 'XG'
        self.device_spec = device_spec or 'R:UNKNOWN.GRF'
        self.mag_x = mag_x or 1
        self.mag_y = mag_y or 1
        self.element_type = 'stored_graphic'

    def resolve(self):
        """The real image this reference names, if this session has it."""
        return graphic_store.recall(self.device_spec)

    def to_zpl(self, offset=(0, 0)) -> str:
        zpl = self.origin_zpl(offset) + self.reverse_zpl()
        if self.command == 'XG':
            zpl += f"^XG{self.device_spec},{self.mag_x},{self.mag_y}\n"
        else:
            zpl += f"^IM{self.device_spec}\n"
        zpl += "^FS\n"
        return zpl


class Document:
    """The label being designed: its size, its elements, and its z-order.

    Element list order is z-order: index 0 is the bottom, the last element is
    the top. Holds no widget, so a document can be parsed, edited and
    serialised with no display attached.
    """

    def __init__(self, label_width: int = 812, label_height: int = 1218,
                 dpi: int = zpl_fonts.DEFAULT_DPI):
        self.elements: List[DesignElement] = []
        self.selection: List[DesignElement] = []
        self.label_width = label_width
        self.label_height = label_height
        self.dpi = dpi

        # A stored format (^DF) names where the printer keeps it; a recall
        # (^XF) names one to merge data into. A file can be either, and the
        # values its numbered fields carry are shared by number, so they live
        # here rather than on the elements.
        self.stored_format: Optional[str] = None
        self.recalls: List[str] = []
        self.fields = zpl_fields.FieldTable()

        # ^IL names a stored image to load at ^FO0,0, ahead of the fields
        # that overlay it - the graphic counterpart of a recall, so it lives
        # here rather than as an element for the same reason ^XF's data does.
        # ^IS instead saves everything drawn before it as a named image; a
        # format can do that more than once, so it is a list like `recalls`,
        # not a single value like `stored_format`.
        self.image_load: Optional[str] = None
        self.image_saves: List[str] = []

        # ^LH, ^LS, ^LT, ^PO, ^PM and ^LR - what the format says about the
        # label as a whole rather than about any one field on it.
        self.transform = zpl_transforms.LabelTransform()

        # ^PQ - how many copies to print, and the pause/RFID options that ride
        # along with it. Only quantity has an editor (Label Settings); the
        # rest are carried the way ^LT is - present so a save does not
        # silently drop them.
        self.print_quantity = 1
        self.print_pause_count = 0
        self.print_replicates = 0
        self.print_override_pause = False

        # ^CV - whether the printer checks each barcode's data as it prints
        # and prints INVALID - X in place of a bad one. A print-time check
        # with nothing to draw, carried the way ^LT is so a save does not
        # silently drop it. No editor, so it is not in the undo snapshot.
        self.code_validation = False

        # ^CI, ^CW and ^FL - the encoding the field data is in, and the
        # printer's font table: which letter names which downloaded font, and
        # which font supplies the glyphs another lacks. Nothing to draw, and
        # carried verbatim so a save does not silently drop them. ^CI is the
        # first one the file gave, remap pairs and all ('28', '0,21,36'), or
        # None; but see _encoding_zpl - a label holding non-ASCII is written
        # as ^CI28 whatever the file said, since what is written is UTF-8.
        # ^CW is letter -> its parameters, in file order, a re-assigned
        # letter replacing its entry; ^FL is every command in order, since
        # a link and an unlink are both actions. No editors, so none of the
        # three is in the undo snapshot.
        self.encoding: Optional[str] = None
        self.font_identifiers: Dict[str, str] = {}
        self.font_links: List[str] = []

        # Document-wide font, used by any text element that has none of its own
        self.font_path: Optional[str] = None
        self.font_family: Optional[str] = None
        self.printer_font_name: Optional[str] = None

    # --- selection -----------------------------------------------------------
    #
    # More than one element can be selected at once, so that a group of them
    # can be aligned against each other. `selection` is the truth; the singular
    # `selected_element` is the last one picked - the primary, which carries
    # the resize handles and is what the z-order commands move. Keeping the
    # singular name as a property over the list means everything that only ever
    # wants one element - the editors, the context menu, the parser - is
    # unchanged by there being more than one.
    #
    # A grouped element (see `group_selected`) is never selected on its own
    # except by a direct pick: every other way into the selection widens a
    # pick of one member to its whole outermost group, here rather than in
    # the canvases, so a click, a rubber band and a test's assignment all
    # agree on it and neither frontend can forget. A direct pick - Ctrl-click
    # - is the way to one element inside a group without ungrouping it.

    @property
    def selected_element(self) -> Optional[DesignElement]:
        return self.selection[-1] if self.selection else None

    @selected_element.setter
    def selected_element(self, element: Optional[DesignElement]):
        self.selection = self._expand([element]) if element is not None else []

    def select(self, element: Optional[DesignElement], additive: bool = False,
               direct: bool = False):
        """Pick an element, or add one to the selection and take it out again.

        A plain pick of an element already in the selection keeps the whole
        selection, so a group can be dragged by any of its members; an additive
        pick of one takes it out, which is how a member is dropped.

        Picking a grouped element picks its outermost group, with the element
        pointed at as the primary; dropping one drops its group. A direct pick
        is exactly the element and nothing else - it narrows a selected group
        down to the one member, and an additive direct pick adds or drops that
        one member alone.
        """
        if element is None:
            if not additive:
                self.clear_selection()
            return
        members = [element] if direct else self.group_members(element)
        if not additive:
            if direct or element not in self.selection:
                self.selection = members
            # The picked element becomes the primary even though the group
            # survives, so the commands that act on one element - the z-order
            # four, reached by right-clicking a member - act on the element
            # the user actually pointed at.
            self.make_primary(element)
            return
        if element in self.selection:
            for member in members:
                if member in self.selection:
                    self.selection.remove(member)
        else:
            self.selection.extend(m for m in members if m not in self.selection)
            self.make_primary(element)

    def make_primary(self, element) -> None:
        """Move a selected element to the end, making it the primary."""
        if element in self.selection and self.selection[-1] is not element:
            self.selection.remove(element)
            self.selection.append(element)

    def select_many(self, elements, direct: bool = False) -> None:
        """Select exactly these, ignoring any that are not in the document."""
        self.selection = self._expand(elements, direct)

    def extend_selection(self, elements, direct: bool = False) -> None:
        """Add these to the selection, leaving what is already in it alone.

        Adding rather than toggling, which is what an additive rubber band
        wants: a band dragged over a group to pick up one more element should
        not drop every element it passed on the way.
        """
        for element in self._expand(elements, direct):
            if element not in self.selection:
                self.selection.append(element)

    def clear_selection(self) -> None:
        self.selection = []

    def select_all(self) -> bool:
        """Select every element; returns whether that changed anything."""
        if len(self.selection) == len(self.elements):
            return False
        self.select_many(self.elements)
        return True

    def invert_selection(self) -> bool:
        """Select what is not selected, whole groups included - a member
        picked directly comes back with the rest of its group. Returns
        whether there was anything to invert."""
        if not self.elements:
            return False
        self.select_many([el for el in self.elements if el not in self.selection])
        return True

    def is_selected(self, element) -> bool:
        return element in self.selection

    def _expand(self, elements, direct: bool = False) -> List[DesignElement]:
        """These elements, each grouped one widened to its outermost group.

        In the order given, each group where its first member was, with no
        element twice; anything not in the document is left out. A direct
        expansion widens nothing - it is the same filtering, and no more.
        """
        picked = [el for el in elements if el is not None and el in self.elements]
        expanded: List[DesignElement] = []
        for element in picked:
            for member in ([element] if direct else self.group_members(element)):
                if member not in expanded:
                    expanded.append(member)
        return expanded

    # --- groups --------------------------------------------------------------
    #
    # A group is a set of elements that select, move and change depth as one.
    # It is nothing more than the same id in each member's `group` path: the
    # members stay ordinary elements in the one flat z-ordered list, so the
    # ZPL, the painting and the editors know nothing about it. Groups nest by
    # wrapping - grouping a selection that holds a group puts a new id in
    # front of every member's path, and ungrouping takes the outermost id
    # off again, so what was inside comes back out as a group of its own.

    def group_members(self, element) -> List[DesignElement]:
        """Every element in this element's outermost group, in z-order - or
        just it."""
        top = geometry.top_group(element)
        if top is None:
            return [element]
        return [el for el in self.elements if geometry.top_group(el) == top]

    def units(self, elements=None) -> List[List[DesignElement]]:
        """The document (or these elements) as the units that move together."""
        return geometry.units_of(self.elements if elements is None else elements)

    def group_outlines(self):
        """The boxes to draw around the selected groups (geometry.group_outlines)."""
        return geometry.group_outlines(self.elements, self.selection)

    def can_group(self) -> bool:
        """Two or more units are selected: something to join to something."""
        return len(self.units(self.selection)) >= 2

    def can_ungroup(self) -> bool:
        return any(el.group for el in self.selection)

    def _fresh_group_id(self) -> int:
        """One more than any id in use at any depth."""
        used = [gid for el in self.elements for gid in (el.group or ())]
        return max(used, default=0) + 1

    def group_selected(self) -> bool:
        """Wrap the selection in a new group, and make it one run in the
        z-order.

        Whole top-level groups go in, even where the selection holds only a
        directly picked member of one: a group cannot be split by grouping.
        Contiguous so that the group has one depth for the z-order commands
        to move. The run lands where the topmost member was, so the group
        stays above everything that member was above; the members keep their
        order within it, and so any group already among them keeps its run.
        """
        if not self.can_group():
            return False
        primary = self.selected_element
        chosen = self._expand(self.selection)
        members = [el for el in self.elements if el in chosen]
        top = self.elements.index(members[-1])
        for element in members:
            self.elements.remove(element)
        self.elements[top - len(members) + 1:top - len(members) + 1] = members
        fresh = self._fresh_group_id()
        for element in members:
            element.group = (fresh,) + (element.group or ())
        # The selection is now the whole new group, which it was not if a
        # member had been picked directly.
        self.selection = members
        self.make_primary(primary)
        return True

    def ungroup_selected(self) -> bool:
        """Dissolve the outermost group of every selected element; the
        selection stays.

        Of every member of that group, not only the selected ones - Ungroup
        is a command on a group, and a directly picked member names its group
        as well as any. What was nested inside comes out as a group of its
        own; another Ungroup peels that.
        """
        tops = {geometry.top_group(el) for el in self.selection if el.group}
        if not tops:
            return False
        for element in self.elements:
            if geometry.top_group(element) in tops:
                element.group = element.group[1:] or None
        return True

    def _members_of(self, gid) -> List[DesignElement]:
        return geometry.members_of(self.elements, gid)

    def _lifts(self) -> list:
        """(element, id of the group it would leave) for every selected
        element that Remove from Group would move.

        What is lifted is the selected unit - the thing the outline says is
        selected: an element picked on its own, or a whole group every
        member of which is picked - and it goes one level up, out of the
        group just outside it. Walking the path from the outside in, the
        first group wholly in the selection is that unit; none means the
        element is; one at the top means the whole top-level group is
        selected, and there is nothing to lift it out of.
        """
        picked = {id(el) for el in self.selection}
        lifts = []
        for element in self.selection:
            path = element.group or ()
            unit = len(path)
            for depth, gid in enumerate(path):
                if all(id(m) in picked for m in self._members_of(gid)):
                    unit = depth
                    break
            if unit > 0:
                lifts.append((element, path[unit - 1]))
        return lifts

    def can_remove_from_group(self) -> bool:
        return bool(self._lifts())

    def remove_from_group(self) -> bool:
        """Take the selected unit out of the group around it; the selection
        stays.

        What is lifted lands just above the last member it leaves behind, so
        every group's run stays one run and the lifted element stays on top
        of what it left, where it was. Lifts are done from the top of the
        z-order down, so several keep their order among themselves. A group
        left with one member is no group and is dissolved.
        """
        lifts = self._lifts()
        if not lifts:
            return False
        for element, gid in sorted(lifts, key=lambda lift: self.elements.index(lift[0]),
                                   reverse=True):
            element.group = tuple(g for g in element.group if g != gid) or None
            remaining = [m for m in self._members_of(gid) if m is not element]
            self.elements.remove(element)
            self.elements.insert(self.elements.index(remaining[-1]) + 1, element)
        self._dissolve_singletons({gid for _, gid in lifts})
        return True

    def _dissolve_singletons(self, ids) -> None:
        """Strip any of these group ids that only one element is left in."""
        for gid in ids:
            members = self._members_of(gid)
            if len(members) == 1:
                members[0].group = tuple(g for g in members[0].group if g != gid) or None

    def resize_target(self):
        """What the resize handles belong to: the one selected element, a
        whole selected group - the selection being exactly every member of
        some group, at whatever depth - or None for a selection that is
        neither, which gets no handles.

        From the inside out, so that every member of a nested pair picked
        directly resizes the pair and not the group around it; a plain click
        on the nest, which selects all of it, resizes the outer group.
        """
        if len(self.selection) == 1:
            return self.selection[0]
        if not self.selection:
            return None
        picked = {id(el) for el in self.selection}
        for gid in reversed(self.selection[0].group or ()):
            members = self._members_of(gid)
            if len(members) >= 2 and {id(el) for el in members} == picked:
                return geometry.GroupBox(members)
        return None

    # --- adding and removing -------------------------------------------------

    def _stagger(self, step: int) -> int:
        """Offset for a new element, so successive additions do not stack."""
        return len(self.elements) * step

    def add_text_element(self, text: str = "New Text") -> TextElement:
        offset = self._stagger(10)
        element = TextElement(50 + offset, 50 + offset, text)
        self.sync_text_width(element)
        return self._append(element)

    def add_time_element(self, text: str = "%m/%d/%y") -> TextElement:
        """A field the printer's real-time clock fills in (^FC).

        Its own creation button and element state rather than a mode of a
        plain text field - see qtui/dialogs.py's edit_time_dialog for why.
        `sync_text_width` runs after `clock_format` is set, so the box is
        measured against the wrapped marker it will actually show.
        """
        offset = self._stagger(10)
        element = TextElement(50 + offset, 50 + offset, text)
        element.clock_format = True
        self.sync_text_width(element)
        return self._append(element)

    def add_serial_element(self, text: str = "1") -> TextElement:
        """A field the printer increments or decrements each label (^SN).

        Its own creation button and element state rather than a mode of a
        plain text field - see qtui/dialogs.py's edit_serial_dialog. The
        starting value doubles as ^SN's own first parameter (`serial_start`),
        matching how `_field_source_rows.apply_to` already keeps the two in
        sync whenever a field switches into serial mode.
        """
        offset = self._stagger(10)
        element = TextElement(50 + offset, 50 + offset, text)
        element.serial_start = text
        element.serial_increment = 1
        element.serial_leading_zero = False
        self.sync_text_width(element)
        return self._append(element)

    def add_numbered_element(self, number: int = 1, prompt=None) -> TextElement:
        """A field a stored format recalls by number at print time (^FN).

        Its own creation button and element state rather than a mode of a
        plain text field - see qtui/dialogs.py's edit_numbered_dialog. No
        literal by default: a numbered field's data comes from the printer,
        and handing it one here would be inventing content the design never
        gave, the same trap a newly-created ^FN barcode used to fall into.
        """
        offset = self._stagger(10)
        element = TextElement(50 + offset, 50 + offset, '')
        element.field_number = number
        element.field_prompt = prompt
        self.sync_text_width(element)
        return self._append(element)

    def add_frame_element(self) -> FrameElement:
        offset = self._stagger(20)
        return self._append(FrameElement(100 + offset, 100 + offset))

    def add_barcode_element(self) -> BarcodeElement:
        offset = self._stagger(20)
        return self._append(BarcodeElement(50 + offset, 250 + offset))

    def add_image_element(self, image_path: str) -> ImageElement:
        offset = self._stagger(20)
        return self._append(ImageElement(50 + offset, 50 + offset, 200, 200, image_path))

    def add_stored_graphic_element(self, command: str = 'XG',
                                   device_spec: str = 'R:UNKNOWN.GRF') -> StoredGraphicElement:
        offset = self._stagger(20)
        return self._append(StoredGraphicElement(50 + offset, 50 + offset, 200, 200,
                                                  command=command, device_spec=device_spec))

    def _append(self, element: DesignElement) -> DesignElement:
        self._fit_new_element_to_bounds(element)
        self.elements.append(element)
        self.selected_element = element
        return element

    def _fit_new_element_to_bounds(self, element) -> None:
        """Bring a freshly placed element fully onto the label.

        Repositioned first and only shrunk if it is bigger than the label
        itself, so an element whose default offset overshot the label - the
        common case on a small label - is moved back onto it at full size
        rather than trimmed down to a sliver at the edge, which the
        resize-driven `_clamp_element_to_bounds` would do instead.
        """
        element.width = min(element.width, self.label_width)
        element.height = min(element.height, self.label_height)
        element.x = max(0, min(element.x, self.label_width - element.width))
        element.y = max(0, min(element.y, self.label_height - element.height))
        block = getattr(element, 'block', None)
        if block is not None:
            block.width = max(1, element.width)
            self.sync_text_width(element)

    def remove_selected(self) -> bool:
        """Delete every selected element.

        The whole selection goes, not just the primary: a user who picked three
        elements and pressed Delete meant all three.
        """
        doomed = [el for el in self.selection if el in self.elements]
        if not doomed:
            return False
        for element in doomed:
            self.elements.remove(element)
        self.clear_selection()
        # A member picked directly and deleted can leave its group with one
        # element, which is no group - and would keep Ungroup lit on what
        # looks like a loose element.
        self._dissolve_singletons({gid for el in doomed for gid in (el.group or ())})
        return True

    def clear(self):
        """Clear all elements."""
        self.elements.clear()
        self.selected_element = None

    # --- z-order -------------------------------------------------------------
    #
    # These move the unit holding the primary element: the primary alone, or
    # its whole group as one run. Not the rest of a loose multi-selection -
    # what "bring forward" should mean for three elements at different depths
    # is a question of its own, and answering it badly is worse than leaving
    # it. A group is different: it has one depth by construction.
    #
    # Rebuilding the list from its units also mends a group whose members a
    # hand-edited file left scattered, the first time its depth is changed.

    def _primary_unit(self):
        """(units, index of the one holding the primary), or (units, None)."""
        units = self.units()
        primary = self.selected_element
        for i, unit in enumerate(units):
            if primary in unit:
                return units, i
        return units, None

    def can_raise(self) -> bool:
        units, i = self._primary_unit()
        return i is not None and i < len(units) - 1

    def can_lower(self) -> bool:
        units, i = self._primary_unit()
        return i is not None and i > 0

    def _reorder_units(self, units) -> None:
        self.elements = [el for unit in units for el in unit]

    def bring_forward(self) -> bool:
        if not self.can_raise():
            return False
        units, i = self._primary_unit()
        units[i], units[i + 1] = units[i + 1], units[i]
        self._reorder_units(units)
        return True

    def send_backward(self) -> bool:
        if not self.can_lower():
            return False
        units, i = self._primary_unit()
        units[i], units[i - 1] = units[i - 1], units[i]
        self._reorder_units(units)
        return True

    def bring_to_front(self) -> bool:
        if not self.can_raise():
            return False
        units, i = self._primary_unit()
        units.append(units.pop(i))
        self._reorder_units(units)
        return True

    def send_to_back(self) -> bool:
        if not self.can_lower():
            return False
        units, i = self._primary_unit()
        units.insert(0, units.pop(i))
        self._reorder_units(units)
        return True

    def element_at(self, x: int, y: int) -> Optional[DesignElement]:
        """The topmost element containing the point, or None."""
        for element in reversed(self.elements):
            if element.contains_point(x, y):
                return element
        return None

    # --- history -------------------------------------------------------------

    def snapshot(self):
        """A restorable record of the whole design.

        A shallow copy per element is enough to be independent: everything an
        edit touches is a scalar field, bar the one exception _copy_element
        handles. ImageElement's heavy attributes are either immutable (the
        decoded source) or caches keyed by (width, height) and replaced
        wholesale, so sharing them between snapshots is safe and saves
        deep-copying decoded images and rendered bitmaps.

        The selection is recorded as indices rather than elements, since undo
        replaces every element object - a group selection has to come back as
        the group, not as a set of detached copies.
        """
        selected = [self.elements.index(el) for el in self.selection
                    if el in self.elements]
        # The field table is an object, so it is copied like a text element's
        # block: shared between snapshots, one edit would rewrite every undo
        # entry holding it.
        return (self.label_width, self.label_height,
                [_copy_element(el) for el in self.elements], selected,
                self.fields.copy(), self.transform.copy(), self.print_quantity)

    def restore(self, snap):
        """Put the design back to a snapshot taken earlier."""
        (label_width, label_height, elements, selected, table, transform,
         print_quantity) = snap
        # assigned directly rather than through set_label_size, which would
        # clamp elements that were already valid at this size
        self.label_width = label_width
        self.label_height = label_height
        # copied again on the way out, or the next edit would rewrite the
        # snapshot still sitting on the undo stack
        self.elements = [_copy_element(el) for el in elements]
        self.selection = [self.elements[i] for i in selected]
        self.fields = table.copy()
        self.transform = transform.copy()
        self.print_quantity = print_quantity

    # --- geometry ------------------------------------------------------------

    def align_selected(self, edge: str) -> bool:
        """Line the selection up on one edge, or centre it on one axis.

        Two or more elements line up against each other; one on its own lines
        up against the label. Returns whether anything actually moved, so an
        align that changes nothing records no undo entry - the same contract
        the z-order commands keep.
        """
        return geometry.align_elements(self, self.selection, edge)

    def set_label_size(self, width: int, height: int):
        """Set the label size and clamp elements to the new bounds."""
        self.label_width = width
        self.label_height = height
        self._clamp_elements_to_bounds()

    def _clamp_elements_to_bounds(self):
        """Ensure all elements stay within label bounds."""
        for element in self.elements:
            self._clamp_element_to_bounds(element)

    def _clamp_element_to_bounds(self, element) -> None:
        element.x = max(0, min(element.x, self.label_width - 1))
        element.y = max(0, min(element.y, self.label_height - 1))
        element.width = min(element.width, self.label_width - element.x)
        element.height = min(element.height, self.label_height - element.y)
        block = getattr(element, 'block', None)
        if block is not None:
            # A wrapped element's box is its block, so a box clamped to the
            # label is a narrower wrap - not a box that merely claims to be
            # narrower while the text still runs to the old width.
            block.width = max(1, element.width)
            self.sync_text_width(element)

    def rescale(self, factor: float) -> None:
        """Scale the whole design by `factor`, keeping its physical size.

        Used when a label drawn for one head resolution is opened for another:
        ZPL is in dots, so 812 dots is 4in at 203dpi but 2.7in at 300dpi.
        What each element does with the factor is geometry.scale_element's -
        the same rule a group resize applies - about the label's origin.
        """
        if factor <= 0 or factor == 1.0:
            return

        def s(v):
            return max(1, int(round(v * factor)))

        self.label_width = s(self.label_width)
        self.label_height = s(self.label_height)

        for el in self.elements:
            geometry.scale_element(self, el, 0, 0, factor, factor)
            if el.element_type == 'image':
                # the bitmap re-dithers from the source at the new size
                el.reload()

    # --- fonts ---------------------------------------------------------------

    def sync_text_width(self, element) -> None:
        """Resize a text element's box to the size it will print at."""
        if getattr(element, 'element_type', None) != 'text':
            return
        block = getattr(element, 'block', None)
        if block is not None:
            # A block is sized by ^FB, not by the string: its width is fixed
            # and its height follows however many lines the text wraps into.
            from . import textraster
            run, stack = textraster.block_size(
                self.display_text(element), element.font_path or self.font_path,
                element.font_height, element.font_width, block)
        else:
            run, stack = (element.printed_width(self.font_path,
                                                self.display_text(element)),
                          element.font_height)
        # The run is along the text, so a quarter turn swaps it with the stack.
        element.width, element.height = ((stack, run) if element.rotated()
                                         else (run, stack))

    def set_font(self, font_path: str, font_family: str, printer_font_name: str):
        """Set the document-wide font."""
        self.font_path = font_path
        self.font_family = font_family
        self.printer_font_name = printer_font_name
        zpl_fonts.register_app_font(font_path)
        for el in self.elements:
            self.sync_text_width(el)

    def set_element_font(self, element: TextElement, font_path: str,
                         font_family: str, printer_font_name: str):
        """Set one text element's own font."""
        element.font_path = font_path
        element.font_family = font_family
        element.printer_font_name = printer_font_name
        zpl_fonts.register_app_font(font_path)
        self.sync_text_width(element)

    def printer_font_names(self, exclude=None) -> set:
        """Printer font names already used by the label's text elements."""
        return {el.printer_font_name for el in self.elements
                if el is not exclude and getattr(el, 'printer_font_name', None)}

    def font_sources(self) -> dict:
        """Printer font name -> local .ttf path, for fonts this label uses.

        A font loaded from a .zpl has no local file, so its value is None and it
        cannot be uploaded - only reported as missing.
        """
        sources = {}
        for el in self.elements:
            name = getattr(el, 'printer_font_name', None)
            if name:
                sources.setdefault(name, getattr(el, 'font_path', None))
        if self.printer_font_name:
            sources.setdefault(self.printer_font_name, self.font_path)
        return sources

    # --- serialisation -------------------------------------------------------

    def _group_numbers(self) -> dict:
        """Group id -> the number it is written as: 1, 2, 3 in order of first
        appearance, walking the elements in z-order and each path from the
        outside in, so a saved file does not carry whatever ids a session's
        grouping and ungrouping left behind. A group of one is not a group
        and gets no number, at whatever depth it sits."""
        counts: dict = {}
        for element in self.elements:
            for gid in set(element.group or ()):
                counts[gid] = counts.get(gid, 0) + 1
        numbers: dict = {}
        for element in self.elements:
            for gid in element.group or ():
                if counts[gid] > 1 and gid not in numbers:
                    numbers[gid] = len(numbers) + 1
        return numbers

    @staticmethod
    def _group_marker(element, numbers: dict) -> str:
        """The ^FXDESIGNER_GROUP line for this element, or '' for none: its
        path as written numbers, outermost first, the levels that are not
        groups left out."""
        path = [numbers[gid] for gid in element.group or () if gid in numbers]
        if not path:
            return ''
        return "^FXDESIGNER_GROUP:" + ",".join(str(n) for n in path) + "\n"

    def to_zpl(self, *, explicit_flips: bool = False) -> str:
        """Generate ZPL code from the elements, with the label size settings.

        explicit_flips is passed through to the label transform (see
        LabelTransform.to_zpl) and exists for the print path, not for saving
        a file. ^CV and ^CI ride on it too, being sticky at the printer the
        same way.
        """
        # Before the fields, because ^LH is the reference point every ^FO after
        # it is measured from. Fitted to the elements first, so the offset it
        # declares is one none of them has to be written above - the commands
        # and the coordinates are then consistent by construction rather than
        # by two places agreeing.
        placed = self.transform.fitted(self._lowest_element())
        # The body first: whether it holds a byte outside ASCII decides the
        # ^CI written ahead of it.
        body = self._body_zpl(placed.field_offset())

        zpl = "^XA\n"
        # ZPL requires ^DF immediately after ^XA: everything following it is
        # stored as text rather than printed, so anything written in between
        # would be left out of the format being saved.
        if self.stored_format:
            zpl += f"^DF{self.stored_format}^FS\n"
        # ^IL belongs at the start of the format too - it names an image to
        # load at ^FO0,0, underneath the fields that follow it.
        if self.image_load:
            zpl += f"^IL{self.image_load}\n"
        # The encoding and the font table next: the manual wants ^CI "at the
        # beginning of each ZPL script", and a ^CW has to precede any ^A that
        # calls the letter it assigns.
        zpl += self._encoding_zpl(explicit=explicit_flips,
                                  unicode=not body.isascii())
        zpl += self._font_identity_zpl()
        zpl += placed.to_zpl(explicit_flips=explicit_flips)
        zpl += f"^PW{self.label_width}\n"
        zpl += f"^LL{self.label_height}\n"
        # ZPL carries no resolution, so record what the dots were drawn for.
        # Printers ignore ^FX, and the value has no caret to end the comment early.
        zpl += f"^FXDESIGNER_DPI:{self.dpi}\n"
        # Before the fields, because it is a switch over the barcodes after it.
        zpl += self._code_validation_zpl(explicit=explicit_flips)
        zpl += body
        zpl += "^XZ"
        return zpl

    def _body_zpl(self, offset) -> str:
        """Everything between the format's header and its ^XZ: the elements
        in z-order, then the recalls, the ^IS saves, ^PQ and, for a format
        with no elements, the field table."""
        zpl = ""
        groups = self._group_numbers()
        for element in self.elements:
            if self.printer_font_name and element.element_type == 'text':
                body = element.to_zpl(printer_font_name=self.printer_font_name,
                                      offset=offset)
            else:
                # By keyword: a text element's first parameter is its printer
                # font name, and a positional offset landed there instead.
                body = element.to_zpl(offset=offset)
            # The marker flags the next field the parser builds, so it goes
            # only in front of a field that will be there - an element with
            # nothing to write would hand its group to whatever came next.
            if body:
                zpl += self._group_marker(element, groups)
            if element.print_enabled:
                zpl += body
            elif body:
                # ^FX comments only until the NEXT CARET, so the body has to be
                # base64'd - inlining it raw would leave its ^FO/^FD to execute
                # and print anyway, which is the whole point of hiding it.
                blob = _b64.b64encode(body.encode('utf-8')).decode('ascii')
                zpl += f"^FXDESIGNER_NOPRINT:{blob}\n"
        # A recall call is data, not geometry: the design it fills lives on
        # the printer. Re-emitting the ^XF and the pairs is the whole of it, and
        # is what stops opening such a file and saving it from emptying it.
        for recalled in self.recalls:
            zpl += f"^XF{recalled}^FS\n"
        # ^IS saves everything drawn before it as a named image; re-emitting
        # it here, after the elements it captured, is what a save has to do
        # to keep meaning "save this design" rather than losing the request.
        for saved in self.image_saves:
            zpl += f"^IS{saved}^FS\n"
        zpl += self._print_quantity_zpl()
        if not self.elements:
            zpl += self.fields.to_zpl()
        return zpl

    def _print_quantity_zpl(self) -> str:
        """^PQ, trimmed after the last parameter still worth writing.

        q, p, r and o are positional, so anything before the last non-default
        one has to be spelled even when it is itself still the default.
        """
        given = [self.print_quantity, self.print_pause_count,
                 self.print_replicates,
                 'Y' if self.print_override_pause else 'N']
        defaults = [1, 0, 0, 'N']
        keep = 0
        for index, value in enumerate(given):
            if value != defaults[index]:
                keep = index + 1
        if keep == 0:
            return ''
        return '^PQ' + ','.join(str(v) for v in given[:keep]) + '\n'

    def _code_validation_zpl(self, *, explicit: bool) -> str:
        """^CV, sticky at the printer like ^PO/^PM/^LR: "remains active from
        format to format until turned off by another ^CV command". A save
        writes it only when on; the print path (explicit) states it either
        way, or a job that left it on would go on validating this one.
        """
        if self.code_validation:
            return '^CVY\n'
        return '^CVN\n' if explicit else ''

    def _encoding_zpl(self, *, explicit: bool, unicode: bool) -> str:
        """^CI. What a save writes and a print sends is UTF-8, so a label with
        any byte outside ASCII is declared ^CI28 whatever the file said - a
        file that opened under a single-byte ^CI held only ASCII, so any
        non-ASCII in it now is the user's own edit. An ASCII-only label goes
        on carrying the ^CI it had, national replacements and remap pairs
        included, since under them ASCII is not always ASCII (^CI6 prints [
        as Ä). Sticky at the printer like ^CV - "we recommend that a ^CI
        command is included at the beginning of each ZPL script" - so the
        print path (explicit) states ^CI28 for a label that carried none,
        while a save writes nothing and every ASCII-only file stays as it was.
        """
        if unicode:
            return '^CI28\n'
        if self.encoding is not None:
            return f'^CI{self.encoding}\n'
        return '^CI28\n' if explicit else ''

    def _font_identity_zpl(self) -> str:
        """^CW and ^FL, verbatim, one line each - the ^FL with the ^FS the
        manual's own example gives it."""
        return (''.join(f'^CW{p}\n' for p in self.font_identifiers.values())
                + ''.join(f'^FL{p}^FS\n' for p in self.font_links))

    def _lowest_element(self):
        """The smallest (x, y) any element occupies, or None if there are none."""
        if not self.elements:
            return None
        return (min(el.x for el in self.elements),
                min(el.y for el in self.elements))

    def display_text(self, element) -> str:
        """What a canvas draws for an element, its ^FN placeholder included.

        Here rather than in each frontend because a placeholder shown two
        different ways in two canvases is a divergence nothing would raise an
        error about - the reason ^FB's wrapping and ^GB's rounding live in the
        core too.
        """
        getter = getattr(element, 'display_text', None)
        if getter is None:
            return getattr(element, 'text', '') or ''
        return getter(self.fields)

    def is_empty(self) -> bool:
        """True when the ZPL carries no fields, only the format wrapper.

        Tested on the emitted ZPL rather than on len(self.elements), so an
        element that serialises to nothing - an image whose source has gone
        away - counts as no content too.
        """
        body = self.to_zpl()
        for header in ('^XA', '^XZ', f'^PW{self.label_width}',
                       f'^LL{self.label_height}', f'^FXDESIGNER_DPI:{self.dpi}'):
            body = body.replace(header, '')
        return not body.strip()
