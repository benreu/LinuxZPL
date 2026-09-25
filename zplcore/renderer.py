"""
ZPL (Zebra Programming Language) Renderer

Renders ZPL commands to PIL Image objects for display.
"""

from PIL import Image, ImageChops, ImageDraw, ImageFont
import re
from typing import Tuple, List, Optional
from . import fields, fonts as zpl_fonts, geometry, graphic_store, graphics, parser, textraster, transforms
from .model import BarcodeElement, FieldBlock, FrameElement, TextElement


class ZPLRenderer:
    """Renders ZPL (Zebra Programming Language) commands to images."""
    
    # Standard label size: 4x6 inches at 203 DPI = 812x1218 pixels
    DEFAULT_WIDTH = 812
    DEFAULT_HEIGHT = 1218
    
    def __init__(self, width: int = DEFAULT_WIDTH, height: int = DEFAULT_HEIGHT,
                 dpi: int = zpl_fonts.DEFAULT_DPI):
        """
        Initialize the ZPL renderer.

        Args:
            width: Image width in pixels
            height: Image height in pixels
            dpi: the head resolution to read the label at, which a matrix
                symbology's omitted magnification is measured from. A label
                that records its own (^FXDESIGNER_DPI) overrides it.
        """
        self.width = width
        self.height = height
        self.dpi = dpi
        self.default_dpi = dpi
        self.image = None
        self.draw = None
        self.current_x = 0
        self.current_y = 0
        self.current_font_size = 12
        self.current_font = None
        self.field_data = None
        self.hex_indicator = None
        self.font_cache = {}
        self.barcode_height = 0
        self.is_barcode_mode = False
        self.current_font_width = 0
        self.current_font_orientation = 'N'
        self.current_block = None
        # ^CF's font, for any field that names none of its own
        self.default_font = dict(parser.DEFAULT_FONT)
        # ^FW's orientation, for any field or barcode that names none of its
        # own
        self.default_orientation = parser.DEFAULT_ORIENTATION
        # Whether the current field was placed by ^FT, which names a baseline
        # where ^FO names a top
        self.typeset = False
        # Whether the current field is a symbology this designer cannot draw
        self.unsupported_field = False
        # ^FR: the current field prints in reverse
        self.current_reverse = False
        # A ^GB's params, held until ^FS so a ^FR that comes after it in the
        # same field is already known by the time it is drawn.
        self.pending_frame = None
        self.barcode_orientation = ''
        self.barcode_options = ()
        self.barcode_symbology = 'code128'
        self.barcode_params: dict = {}
        self.module_width = 2
        self.ratio = 3.0
        # ^BY's own height, which is what a barcode with no height of its own
        # takes - and, for the symbologies whose size is their whole symbol
        # rather than one row of it, the only height there is.
        self.barcode_default_height = None
        self.custom_font_path: Optional[str] = None
        self.current_field_font_path: Optional[str] = None
        self.font_registry: dict = {}

    def set_font(self, font_path: str):
        self.custom_font_path = font_path
        self.font_cache.clear()

    def register_font(self, printer_font_name: str, font_path: str):
        self.font_registry[printer_font_name.upper()] = font_path
        self.font_cache.clear()

    DEFAULT_FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"

    def _font_path(self) -> str:
        """The face this field will be drawn with."""
        return (self.current_field_font_path or self.custom_font_path
                or self.DEFAULT_FONT_PATH)

    def _top(self, offset: int) -> int:
        """Where the current field's content starts.

        ^FO gives the top of it outright; ^FT gives the baseline of the first
        line, or the bottom-left corner of everything that is not text, so the
        content starts `offset` dots above the y the file named.
        """
        return self.current_y - offset if self.typeset else self.current_y

    def _use_default_font(self):
        """Fall back to ^CF's font, as a printer does at every field start.

        The orientation is ^FW's: ^CF carries none, so a field with no ^A turns
        the way ^FW last said - the same value the parser gives such a field.
        """
        self.current_font_size = self.default_font['height']
        self.current_font_width = self.default_font['width']
        self.current_font_orientation = self.default_orientation
        self.current_field_font_path = None

    def _get_font(self, size: int) -> ImageFont.FreeTypeFont:
        """Get or create a cached font."""
        path = self._font_path()
        cache_key = (size, path)
        if cache_key not in self.font_cache:
            try:
                self.font_cache[cache_key] = ImageFont.truetype(path, size)
            except (IOError, OSError):
                self.font_cache[cache_key] = ImageFont.load_default()
        return self.font_cache[cache_key]
    
    def _parse_position(self, x: str, y: str) -> Tuple[int, int]:
        """Convert ZPL position values to pixels."""
        # ZPL coordinates are in dots (1/203 inch at 203 DPI)
        return (int(x), int(y))
    
    def _render_barcode(self, barcode_value: str, x: int, y: int, height: int):
        """Draw a barcode through the same element the canvas draws.

        Building one here rather than keeping a second set of rules is what
        stops the preview and the canvas disagreeing about the width of a
        subset C symbol or whether the interpretation line prints at all.
        """
        element = BarcodeElement(
            x, y, height, barcode_value,
            module_width=(getattr(self, 'barcode_magnification', None)
                          or max(1, getattr(self, 'module_width', 2))),
            orientation=self.barcode_orientation,
            options=self.barcode_options,
            symbology=getattr(self, 'barcode_symbology', 'code128'),
            params=getattr(self, 'barcode_params', None),
            total_height=self.barcode_default_height,
            ratio=getattr(self, 'ratio', 3.0),
            font=(('0', self.current_font_size,
                   self.current_font_width or self.current_font_size)
                  if self.current_font_size else None))
        layout = geometry.barcode_layout(element)

        # PIL cannot rotate what has not been drawn, so the symbol is drawn
        # into its own image and turned as a whole.
        run = layout['run']
        stack = layout['stack'] + element.text_height()
        # ^FR does not paint a background - it inverts whatever is already
        # there under its own bars and interpretation line. A panel with a 0
        # background and 255 ink is exactly the mask _invert_under() wants,
        # so the same values that used to draw a (wrong) white-on-black panel
        # now serve as that mask instead - see the paste at the end.
        bg, ink = (0, 255) if self.current_reverse else (255, 0)
        panel = Image.new('L', (max(1, run), max(1, stack)), bg)
        draw = ImageDraw.Draw(panel)

        # PIL's rectangle includes both corners, so a rect w dots wide is
        # drawn to x + w - 1. Drawing to x + w instead made every bar a dot
        # wider than it prints, which a one-dot module - a matrix symbology
        # at magnification 1 - would close up into a solid block.
        for rx, ry, rw, rh in layout['rects']:
            draw.rectangle([(rx, ry), (rx + rw - 1, ry + rh - 1)], fill=ink)

        if layout['text']:
            font = self._get_font(max(1, int(layout['font'][1])))
            try:
                width = draw.textlength(layout['text'], font=font)
            except Exception:
                width = len(layout['text']) * layout['font'][1] * 0.6
            draw.text((max(0, (run - width) / 2), layout['text_y']),
                      layout['text'], fill=ink, font=font)

        if layout['angle']:
            panel = panel.rotate(-layout['angle'], expand=True)
        pos = (x, y - element.height if self.typeset else y)
        if self.current_reverse:
            self._invert_under(panel, pos)
        else:
            self.image.paste(panel, pos)

    def _invert_under(self, mask, pos) -> None:
        """Invert the label wherever `mask` ('L', 0-255) is non-zero.

        This is what ^FR actually does on a real printer: it inverts
        whatever is already on the label under this field's own ink -
        nothing else - rather than painting a background of its own.
        Inverting blank (white) label gives black, so a field with nothing
        already printed under it prints its own ink normally; only where
        something is already black does it come out white.
        """
        if mask.width <= 0 or mask.height <= 0:
            return
        box = (pos[0], pos[1], pos[0] + mask.width, pos[1] + mask.height)
        self.image.paste(ImageChops.invert(self.image.crop(box)), pos, mask)

    def _turned(self, panel, run: int, stack: int):
        """Paste a drawn panel onto the label, turned to face the right way.

        PIL cannot rotate what has not been drawn, so text goes into its own
        image and is turned as a whole - the same way a rotated barcode is
        drawn. The footprint transposes at a quarter turn, which is what keeps
        the preview's box the same one the canvas shows.

        `panel` doubles as the ^FR mask when reversed - see _render_text and
        _render_block, which build it with the same 0-background/255-ink
        values _invert_under() expects, for the same reason _render_barcode's
        panel does.
        """
        element = TextElement(self.current_x, self.current_y,
                              orientation=self.current_font_orientation)
        element.width, element.height = run, stack
        angle = geometry.text_layout(element)['angle']
        if angle:
            panel = panel.rotate(-angle, expand=True)
        offset = textraster.baseline_offset(self._font_path(),
                                            self.current_font_size)
        pos = (self.current_x, self._top(offset))
        if self.current_reverse:
            self._invert_under(panel, pos)
        else:
            self.image.paste(panel, pos)

    def _render_text(self, text: str):
        """A plain ^FD field, in the font and the direction ^A asked for.

        Drawn to the width the same TextElement the canvas holds says it will
        print at, rather than to whatever the screen face happens to measure.
        ^A names a character width and this ignored it, so ^A0N,40,10 and
        ^A0N,40,80 drew identically - the same 178 dots, where the design said
        70 and 560.
        """
        element = TextElement(self.current_x, self.current_y, text,
                              self.current_font_size,
                              self.current_font_width or self.current_font_size,
                              orientation=self.current_font_orientation)
        element.font_path = self.current_field_font_path
        run = element.printed_width(self.custom_font_path)

        font = self._get_font(self.current_font_size)
        try:
            box = self.draw.textbbox((0, 0), text, font=font)
        except Exception:
            box = (0, 0, max(1, len(text) * self.current_font_size),
                   self.current_font_size)
        natural = max(1, box[2] - box[0])
        stack = max(1, box[3] - box[1])
        # A 0 background and 255 ink doubles as _invert_under()'s mask when
        # reversed - _turned() does the actual inverting - and is the normal
        # black-on-white panel otherwise.
        bg, ink = (0, 255) if self.current_reverse else (255, 0)
        panel = Image.new('L', (natural, stack), bg)
        ImageDraw.Draw(panel).text((-box[0], -box[1]), text, fill=ink, font=font)
        if run != natural:
            panel = panel.resize((max(1, run), stack), Image.LANCZOS)
        self._turned(panel, run, stack)

    def _render_block(self, text: str):
        """Draw text wrapped into the ^FB block, so the preview matches.

        Through the same rasteriser both canvases use, rather than a second
        arrangement of the same lines: the preview is what a user checks a
        label against before printing it, so it has to be drawing the design
        rather than agreeing with it by coincidence.
        """
        block = self.current_block
        font_path = self._font_path()
        font_width = self.current_font_width or self.current_font_size
        # A 0 background and 255 ink doubles as _invert_under()'s mask when
        # reversed - _turned() does the actual inverting - and is the normal
        # black-on-white panel otherwise.
        bg, ink = (0, (255, 255, 255, 255)) if self.current_reverse \
            else (255, (0, 0, 0, 255))
        drawn = textraster.raster_block(text, font_path, self.current_font_size,
                                        font_width, block, ink)
        if drawn is not None:
            panel = Image.new('L', drawn.size, bg)
            panel.paste(drawn.convert('L'), (0, 0), drawn)
            self._turned(panel, drawn.width, drawn.height)
            return

        # No usable font file, so there are no glyph metrics to raster with;
        # the lines still go where they belong. There is no panel here to
        # double as a mask, so one is built by hand, per line.
        font = self._get_font(self.current_font_size)
        step = textraster.pitch(self.current_font_size, block)
        for row, line in enumerate(textraster.wrap(
                text, font_path, self.current_font_size, font_width, block)):
            y = self.current_y + row * step
            if self.current_reverse:
                box = self.draw.textbbox((0, 0), line, font=font)
                w = max(1, box[2] - box[0])
                h = max(1, box[3] - box[1])
                mask = Image.new('L', (w, h), 0)
                ImageDraw.Draw(mask).text((-box[0], -box[1]), line,
                                          fill=255, font=font)
                self._invert_under(mask, (self.current_x + block.indent + box[0], y + box[1]))
            else:
                self.draw.text((self.current_x + block.indent, y), line,
                               fill='black', font=font)

    def _render_frame(self, params: str):
        """Draw a ^GB box through the same element the canvas draws.

        Through zplcore's parser and FrameElement rather than a second reading
        of ^GB: the preview is what a user checks a label against before
        printing it, so it has to be drawing the design rather than agreeing
        with it by coincidence. The old reading here matched digits where the
        colour is a letter, so it lost the colour and the rounding, and it drew
        an outline where a thick border fills solid.
        """
        element = FrameElement(self.current_x, self.current_y,
                               *parser._read_frame(params))
        element.y = self._top(element.height)
        thickness = max(1, element.thickness)
        radius = element.corner_radius()

        if self.current_reverse:
            # ^FR replaces the field's own print outright, so colour has
            # nothing left to choose between - the box is drawn into a mask,
            # local to its own top-left, and _invert_under() inverts
            # whatever the label already has under it rather than this
            # painting a flat colour of its own.
            mask = Image.new('L', (element.width, element.height), 0)
            draw, ink = ImageDraw.Draw(mask), 255
            box = [(0, 0), (element.width - 1, element.height - 1)]
        else:
            # An RGB tuple, not a bare int: PIL packs a lone int into an RGB
            # image's first channel rather than broadcasting it, which drew
            # white as red.
            draw, ink = self.draw, (255, 255, 255) if element.colour == 'W' else (0, 0, 0)
            # PIL's rectangle includes both corners, so the far edge is one
            # dot short of the width - otherwise every ^GB drew a dot wider
            # and a dot taller here than on the canvas, which is most visible
            # on a rule.
            box = [(element.x, element.y),
                   (element.x + element.width - 1, element.y + element.height - 1)]

        if 2 * thickness >= min(element.width, element.height):
            # ^GB fills solid once the border meets in the middle
            if radius > 0:
                draw.rounded_rectangle(box, radius=radius, fill=ink)
            else:
                draw.rectangle(box, fill=ink)
        elif radius > 0:
            draw.rounded_rectangle(box, radius=radius, outline=ink, width=thickness)
        else:
            draw.rectangle(box, outline=ink, width=thickness)

        if self.current_reverse:
            self._invert_under(mask, (element.x, element.y))

    def _render_graphic(self, params: str):
        """Render a ^GF graphic field, in whichever encoding it arrived in.

        Through zplcore.graphics, so the preview and the canvas cannot disagree
        about what the field holds.
        """
        decoded = graphics.decode(params)
        if decoded is None:
            return
        raw, bytes_per_row = decoded
        rows = len(raw) // bytes_per_row

        # ZPL sets a bit for a black dot, while PIL mode '1' reads a set bit as
        # white, so the bytes are inverted before decoding. Row width is always
        # a whole number of bytes, which is exactly what mode '1' expects.
        inverted = bytes(b ^ 0xFF for b in raw[:rows * bytes_per_row])
        bitmap = Image.frombytes('1', (bytes_per_row * 8, rows), inverted)
        self.image.paste(bitmap.convert('RGB'),
                         (self.current_x, self._top(rows)))

    def _render_stored_graphic(self, command: str, params: str):
        """Render a ^XG/^IM field, if this session's ^IS has the image it names.

        Unresolved is not an error: the same rule an unfilled ^FN follows -
        the printer would supply this at print time, and there is nothing to
        draw yet, so nothing is drawn.
        """
        spec, mag_x, mag_y = parser._read_stored_graphic('^' + command, params)
        image = graphic_store.recall(spec)
        if image is None:
            return
        if mag_x != 1 or mag_y != 1:
            image = image.resize((max(1, image.width * mag_x),
                                  max(1, image.height * mag_y)))
        self.image.paste(image.convert('RGB'),
                         (self.current_x, self._top(image.height)))

    def render(self, zpl_content: str) -> Image.Image:
        """
        Render ZPL content to an image.
        
        Args:
            zpl_content: ZPL command string
            
        Returns:
            PIL Image object
        """
        # Create a new image with white background
        self.image = Image.new('RGB', (self.width, self.height), color='white')
        self.draw = ImageDraw.Draw(self.image)
        self.current_field_font_path = None
        self.default_font = dict(parser.DEFAULT_FONT)
        self.default_orientation = parser.DEFAULT_ORIENTATION
        self.typeset = False
        self.unsupported_field = False
        self.current_reverse = False
        self.hex_indicator = None
        self.pending_frame = None
        # ^BY is a running default, and this renderer is a long-lived object
        # the window reuses for every preview - so one label's ^BY3 used to
        # widen the next label's barcodes, which carried no ^BY at all.
        self.is_barcode_mode = False
        self.module_width = 2
        self.ratio = 3.0
        self.barcode_default_height = None
        self.barcode_params = {}
        self.barcode_magnification = None
        self.dpi = self.default_dpi
        # ^FN's data can be declared after the field that uses it, so the table
        # is built in a pass of its own before anything is drawn.
        self.fields = parser.read_field_table(parser.tokenise(zpl_content))
        # ^LH and ^LS displace every field, so the preview has to apply them or
        # it draws the label somewhere the printer will not.
        self.origin = (0, 0)
        self.transform = transforms.LabelTransform()
        
        # Parse and execute ZPL commands
        self._execute_zpl(zpl_content)

        # ^PO and ^PM describe how the finished label is laid down, so they
        # apply to the whole image once every field is on it. ^LR is not here:
        # it is "identical to placing an ^FR command in all current and
        # subsequent fields", a per-field inversion against what is beneath,
        # and inverting the finished image would turn the white background
        # black - see FUNCTIONAL_SPEC.md section 18.
        if self.transform.invert:
            self.image = self.image.rotate(180)
        if self.transform.mirror:
            self.image = self.image.transpose(Image.FLIP_LEFT_RIGHT)
        return self.image
    
    def _execute_zpl(self, zpl_content: str):
        """Parse and execute ZPL commands."""
        # Remove whitespace and split by ^ to get individual commands
        lines = zpl_content.strip().split('\n')
        
        for line in lines:
            # Split line into commands (separated by ^)
            line = line.strip()
            if not line:
                continue
            
            # Skip comment lines
            if line.startswith(';'):
                continue
            
            # Ensure line starts with ^ for proper parsing
            if not line.startswith('^'):
                line = '^' + line
            
            # Parse commands
            commands = self._tokenize_commands(line)
            for cmd in commands:
                self._execute_command(cmd)
    
    def _tokenize_commands(self, line: str) -> List[str]:
        """Split a line into individual ZPL commands."""
        commands = []
        current_cmd = ""
        i = 0
        while i < len(line):
            if line[i] == '^' and current_cmd:
                commands.append(current_cmd)
                current_cmd = "^"
            else:
                current_cmd += line[i]
            i += 1
        if current_cmd:
            commands.append(current_cmd)
        return commands
    
    def _execute_command(self, cmd: str):
        """Execute a single ZPL command."""
        if not cmd or len(cmd) < 2:
            return
        
        command = cmd[1:3].upper()  # Get command code (2 chars after ^)
        params = cmd[3:] if len(cmd) > 3 else ""
        
        if command == 'XA':
            # Start format
            pass
        elif command == 'XZ':
            # End format
            pass
        elif command == 'PW':
            # Set print width: ^PWn (width in dots)
            try:
                self.width = int(params)
            except ValueError:
                pass
        elif command == 'LL':
            # Set label length: ^LLn (height in dots)
            try:
                self.height = int(params)
            except ValueError:
                pass
        elif command in ('LH', 'LS', 'LT', 'PO', 'PM', 'LR'):
            if command == 'LH':
                home = transforms.read_home(params)
                if home is None:        # prose in an ^FX comment, not an origin
                    return
                self.transform.home = home
            elif command == 'LS':
                shift = transforms.read_shift(params)
                if shift is None:
                    return
                self.transform.shift = shift
            elif command == 'LT':
                top = transforms.read_top(params)
                if top is None:
                    return
                self.transform.top = top
            elif command == 'PO':
                self.transform.invert = transforms.read_flag(params, 'I')
            elif command == 'PM':
                self.transform.mirror = transforms.read_flag(params)
            else:
                self.transform.reverse = transforms.read_flag(params)
            # ^LH is a running origin - it affects only the fields after it.
            self.origin = self.transform.field_offset()
        elif command in ('FO', 'FT'):
            # Field origin: ^FOx,y names the top-left, ^FTx,y the baseline.
            match = re.match(r'(-?\d+),(-?\d+)', params)
            if match:
                self.current_x, self.current_y = self._parse_position(match.group(1), match.group(2))
                self.current_x += self.origin[0]
                self.current_y += self.origin[1]
                self.typeset = (command == 'FT')
                self.unsupported_field = False
                self.current_reverse = False
                self.hex_indicator = None
                self.pending_frame = None
                self.is_barcode_mode = False
                self.barcode_params = {}
                # A field names its own font with ^A or inherits ^CF's, and a
                # printer starts every field from the latter.
                self._use_default_font()
        elif command == 'FH':
            # Field hex indicator: ^FHa marks a-XX escapes in the ^FD that
            # follows, decoded here since the preview never re-saves ZPL.
            self.hex_indicator = fields.read_hex_indicator(params)
        elif command == 'FD':
            # Field data: ^FD<data>
            self.field_data = fields.decode_hex(params, self.hex_indicator)
        elif command == 'FN':
            # A numbered field prints whatever its ^FN#^FD pair gave it, and
            # nothing at all when no pair did - the printer substitutes at print
            # time, so there is nothing to preview yet. That is the one place
            # the preview and the canvas differ on purpose: the canvas shows the
            # placeholder because it answers "what am I editing".
            read = fields.read(params)
            if read is not None:
                self.field_data = self.fields.value(read[0]) or ''
        elif command[0] == 'A':
            # ^A<font><orientation>,h,w - ^A0 is the scalable font most other
            # tools use, ^AF one of the bitmap fonts, ^A@ one downloaded to the
            # printer. Read through the parser rather than by a second pair of
            # patterns here, which is what let the preview and the model
            # disagree about how wide ^A0N,40 is.
            font = parser.read_font(command[1], params, self.default_font,
                                    self.default_orientation)
            self.current_font_orientation = font['orientation']
            self.current_font_size = font['height']
            self.current_font_width = font['width']
            self.current_field_font_path = (self.font_registry.get(font['name'])
                                            if font['name'] else None)
        elif command == 'FR':
            # Reverse print: applies to whatever this field draws, so it has
            # to be known before ^GB (drawn eagerly, below) or ^FS (which
            # draws everything else) is reached.
            self.current_reverse = True
        elif command == 'GB':
            # Held until ^FS rather than drawn here, so a ^FR that comes
            # after ^GB in the same field is still seen before it is drawn.
            self.pending_frame = params
        elif command == 'BY':
            # Module width, and the wide-to-narrow ratio Code 39 and
            # Interleaved 2 of 5 draw their wide elements at - every other
            # symbology here is fixed-ratio and ignores it, the same way
            # BarcodeElement does.
            parts = [p.strip() for p in params.split(',')]
            if parts and parts[0]:
                try:
                    self.module_width = max(1, int(parts[0]))
                except ValueError:
                    pass
            if len(parts) > 1 and parts[1]:
                try:
                    self.ratio = float(parts[1])
                except ValueError:
                    pass
            if len(parts) > 2 and parts[2]:
                try:
                    self.barcode_default_height = int(parts[2])
                except ValueError:
                    pass
        elif command == 'FB':
            # Field block: the text that follows is wrapped into it
            self.current_block = FieldBlock.from_zpl(params)
        elif command == 'FS':
            # End field: render current field data
            if self.pending_frame is not None:
                self._render_frame(self.pending_frame)
                self.pending_frame = None
            if self.unsupported_field:
                # Drawing the ^FD would put the barcode's data on the label as
                # text, which is exactly what the parser no longer does.
                self.unsupported_field = False
                self.field_data = None
                self.current_block = None
            elif self.field_data is not None:
                if self.current_block is not None and not self.is_barcode_mode:
                    self._render_block(self.field_data)
                    self.current_block = None
                elif self.is_barcode_mode:
                    # Render as barcode
                    self._render_barcode(self.field_data, self.current_x,
                                         self.current_y, self.barcode_height)
                    self.is_barcode_mode = False
                    self.barcode_orientation = ''
                    self.barcode_options = ()
                    self.barcode_symbology = 'code128'
                    self.barcode_params = {}
                    self.barcode_magnification = None
                    self.current_block = None
                else:
                    self._render_text(self.field_data)
                self.field_data = None
        elif command == 'GF':
            # Graphic field: ^GFa,total,total,bytes_per_row,<data>
            self._render_graphic(params)
        elif command in ('IM', 'XG'):
            # Recall a stored image, like ^GF but naming one this session's
            # own ^IS may have captured rather than carrying its own data.
            self._render_stored_graphic(command, params)
        elif command == 'IL':
            # Image Load: a stored image, always at ^FO0,0, underneath
            # whatever this format goes on to draw over it.
            image = graphic_store.recall(params)
            if image is not None:
                self.image.paste(image.convert('RGB'), (0, 0))
        elif command == 'IS':
            # Image Save: captured into graphic_store by the parser, which
            # runs on every open - not repeated here, so a preview render is
            # never the hidden reason a recall does or does not work. All
            # this does is honour p=N, which means "do not print this pass".
            parts = [p.strip() for p in params.split(',')]
            print_flag = parts[1].upper() if len(parts) > 1 and parts[1] else 'Y'
            if print_flag == 'N':
                self.image = Image.new('RGB', (self.width, self.height), color='white')
                self.draw = ImageDraw.Draw(self.image)
        elif command == 'FX':
            # A comment, bar the markers this designer writes in one. The
            # resolution is the one that matters here: a matrix symbology
            # whose command leaves its magnification out is drawn at the
            # manual's default for the head the label was made for, so the
            # preview has to know which head that was.
            marker = params.strip()
            if marker.startswith(parser.DPI_PARAM):
                try:
                    self.dpi = int(marker[len(parser.DPI_PARAM):])
                except ValueError:
                    pass
        elif command == 'CF':
            # ^CFf,h,w - the font every later field prints in unless it names
            # its own. Ignoring it drew a default-font field at this class's
            # own 12 dots, whatever the file asked for.
            self.default_font = parser._read_default_font(params, self.default_font)
            self._use_default_font()
        elif command == 'FW':
            # ^FWr - the orientation every later field turns to unless it names
            # its own. Only the running default changes here: an ^A already
            # read in an open field keeps the letter it resolved, and a field
            # with no ^A took the value in force at its ^FO, as it does in the
            # parser - so the preview turns exactly what the canvas turns.
            self.default_orientation = parser.read_field_orientation(
                params, self.default_orientation)
        elif '^' + command in parser.BARCODE_COMMANDS:
            # Every symbology this designer draws, read through the parser's
            # own catalogue rather than a second copy of each command's
            # parameter order - that is what stops the preview and a save
            # disagreeing about where one of them spells its check digit, its
            # error correction or its trailing flags.
            bc = parser._read_barcode(
                '^' + command, params,
                default_height=self.barcode_default_height,
                default_orientation=self.default_orientation,
                dpi=self.dpi)
            self.barcode_orientation = bc['orientation']
            self.barcode_height = bc['height']
            self.barcode_options = bc['options']
            self.barcode_symbology = bc['symbology']
            self.barcode_params = bc['params']
            self.barcode_magnification = bc['magnification']
            self.is_barcode_mode = True
        elif command[0] == 'B':
            # Code 49, Codablock, MaxiCode, MicroPDF417 and TLC39 - the
            # symbologies this designer still cannot draw. ^BY and every
            # other ^B command are matched above, so only those reach here.
            self.unsupported_field = True
    
    def render_from_file(self, filepath: str) -> Image.Image:
        """
        Load ZPL from a file and render it.
        
        Args:
            filepath: Path to ZPL file
            
        Returns:
            PIL Image object
        """
        try:
            # Read as the window reads it, so a file that is not UTF-8
            # previews in the same accents it will open with.
            zpl_content, _code_page = parser.read_file(filepath)
            # A file may have moved ^, ~ or , (see parser.canonicalise);
            # render() is otherwise only ever given the model's own ZPL.
            return self.render(parser.canonicalise(zpl_content)[0])
        except Exception as e:
            raise IOError(f"Failed to read ZPL file: {e}")

