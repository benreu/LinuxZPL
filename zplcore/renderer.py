"""
ZPL (Zebra Programming Language) Renderer

Renders ZPL commands to PIL Image objects for display.
"""

from PIL import Image, ImageDraw, ImageFont
import re
from typing import Tuple, List, Optional
from . import geometry, textraster
from .model import BarcodeElement, FieldBlock


def _parse_frame(x: int, y: int, params: str):
    """A ^GB's parameters as the FrameElement the canvas would draw."""
    from .parser import _build_element
    return _build_element({'x': x, 'y': y, 'frame': params, 'graphic': None,
                           'barcode': None, 'data': None, 'font': None,
                           'block': None, 'module_width': 2,
                           'preview': None, 'path': None}, None, None)


class ZPLRenderer:
    """Renders ZPL (Zebra Programming Language) commands to images."""
    
    # Standard label size: 4x6 inches at 203 DPI = 812x1218 pixels
    DEFAULT_WIDTH = 812
    DEFAULT_HEIGHT = 1218
    
    def __init__(self, width: int = DEFAULT_WIDTH, height: int = DEFAULT_HEIGHT):
        """
        Initialize the ZPL renderer.
        
        Args:
            width: Image width in pixels
            height: Image height in pixels
        """
        self.width = width
        self.height = height
        self.image = None
        self.draw = None
        self.current_x = 0
        self.current_y = 0
        self.current_font_size = 12
        self.current_font = None
        self.field_data = None
        self.font_cache = {}
        self.barcode_height = 0
        self.is_barcode_mode = False
        self.current_font_width = 0
        self.current_block = None
        self.barcode_orientation = ''
        self.barcode_options = ()
        self.module_width = 2
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
            module_width=max(1, getattr(self, 'module_width', 2)),
            orientation=self.barcode_orientation,
            options=self.barcode_options,
            font=(('0', self.current_font_size,
                   self.current_font_width or self.current_font_size)
                  if self.current_font_size else None))
        layout = geometry.barcode_layout(element)

        # PIL cannot rotate what has not been drawn, so the symbol is drawn
        # into its own image and turned as a whole.
        run = layout['run']
        stack = max(1, element.bar_height) + element.text_height()
        panel = Image.new('L', (max(1, run), max(1, stack)), 255)
        draw = ImageDraw.Draw(panel)

        bar_x, bar_y, bar_w, bar_h = layout['bars']
        mods = element.modules()
        mod_w = bar_w / max(1, sum(mods))
        cx = float(bar_x)
        for i, m in enumerate(mods):
            if i % 2 == 0:  # bars are at even indices
                draw.rectangle([(round(cx), bar_y),
                                (round(cx + m * mod_w), bar_y + bar_h)], fill=0)
            cx += m * mod_w

        if layout['text']:
            font = self._get_font(max(1, int(layout['font'][1])))
            try:
                width = draw.textlength(layout['text'], font=font)
            except Exception:
                width = len(layout['text']) * layout['font'][1] * 0.6
            draw.text((max(0, (run - width) / 2), layout['text_y']),
                      layout['text'], fill=0, font=font)

        if layout['angle']:
            panel = panel.rotate(-layout['angle'], expand=True)
        self.image.paste(panel, (x, y))

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
        drawn = textraster.raster_block(text, font_path, self.current_font_size,
                                        font_width, block)
        if drawn is not None:
            self.image.paste(drawn, (self.current_x, self.current_y), drawn)
            return

        # No usable font file, so there are no glyph metrics to raster with;
        # the lines still go where they belong.
        font = self._get_font(self.current_font_size)
        step = textraster.pitch(self.current_font_size, block)
        for row, line in enumerate(textraster.wrap(
                text, font_path, self.current_font_size, font_width, block)):
            self.draw.text((self.current_x + block.indent,
                            self.current_y + row * step), line, fill='black',
                           font=font)

    def _render_frame(self, params: str):
        """Draw a ^GB box through the same element the canvas draws.

        Through zplcore's parser and FrameElement rather than a second reading
        of ^GB: the preview is what a user checks a label against before
        printing it, so it has to be drawing the design rather than agreeing
        with it by coincidence. The old reading here matched digits where the
        colour is a letter, so it lost the colour and the rounding, and it drew
        an outline where a thick border fills solid.
        """
        element = _parse_frame(self.current_x, self.current_y, params)
        if element is None:
            return
        ink = 255 if element.colour == 'W' else 0
        thickness = max(1, element.thickness)
        box = [(element.x, element.y),
               (element.x + element.width, element.y + element.height)]
        radius = element.corner_radius()

        if 2 * thickness >= min(element.width, element.height):
            # ^GB fills solid once the border meets in the middle
            if radius > 0:
                self.draw.rounded_rectangle(box, radius=radius, fill=ink)
            else:
                self.draw.rectangle(box, fill=ink)
        elif radius > 0:
            self.draw.rounded_rectangle(box, radius=radius, outline=ink,
                                        width=thickness)
        else:
            self.draw.rectangle(box, outline=ink, width=thickness)

    def _render_graphic(self, params: str):
        """Render a ^GF graphic field: ^GFa,total,total,bytes_per_row,<hex>."""
        parts = params.split(',', 4)
        if len(parts) < 5:
            return
        fmt = parts[0].strip().upper()
        if fmt and fmt != 'A':
            return  # only ASCII hex (^GFA) is produced by the designer
        try:
            bytes_per_row = int(parts[3])
            raw = bytes.fromhex(parts[4].strip())
        except ValueError:
            return
        if bytes_per_row <= 0:
            return
        rows = len(raw) // bytes_per_row
        if rows <= 0:
            return

        # ZPL sets a bit for a black dot, while PIL mode '1' reads a set bit as
        # white, so the bytes are inverted before decoding. Row width is always
        # a whole number of bytes, which is exactly what mode '1' expects.
        inverted = bytes(b ^ 0xFF for b in raw[:rows * bytes_per_row])
        bitmap = Image.frombytes('1', (bytes_per_row * 8, rows), inverted)
        self.image.paste(bitmap.convert('RGB'), (self.current_x, self.current_y))

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
        
        # Parse and execute ZPL commands
        self._execute_zpl(zpl_content)
        
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
        elif command == 'FO':
            # Set field origin: ^FOx,y
            match = re.match(r'(\d+),(\d+)', params)
            if match:
                self.current_x, self.current_y = self._parse_position(match.group(1), match.group(2))
        elif command == 'FD':
            # Field data: ^FD<data>
            self.field_data = params
        elif command[0] == 'A' and command != 'A@':
            # Built-in font: ^A<font><orientation>,h,w. ^A0 is the scalable
            # font most other tools use; ^AF one of the bitmap fonts.
            match = re.match(r'([A-Z]?)(?:,(\d+))?(?:,(\d+))?', params)
            if match and match.group(2):
                self.current_font_size = int(match.group(2))
                if match.group(3):
                    self.current_font_width = int(match.group(3))
            self.current_field_font_path = None
        elif command == 'A@':
            # Downloaded font: ^A@o,h,w,device:name.TTF
            match = re.match(r'([A-Z]?),(\d+),(\d+),([^:]+):(.+)', params)
            if match:
                self.current_font_size = int(match.group(2))
                font_name = match.group(5).replace('.TTF', '').replace('.ttf', '').upper()
                self.current_field_font_path = self.font_registry.get(font_name)
        elif command == 'GB':
            self._render_frame(params)
        elif command == 'BY':
            # Module width, which sets how wide the bars are
            match = re.match(r'\s*(\d+)', params)
            if match:
                self.module_width = max(1, int(match.group(1)))
        elif command == 'FB':
            # Field block: the text that follows is wrapped into it
            self.current_block = FieldBlock.from_zpl(params)
        elif command == 'FS':
            # End field: render current field data
            if self.field_data is not None:
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
                    self.current_block = None
                else:
                    # Render as text
                    font = self._get_font(self.current_font_size)
                    try:
                        self.draw.text(
                            (self.current_x, self.current_y),
                            self.field_data,
                            fill='black',
                            font=font
                        )
                    except Exception as e:
                        # Fallback if font rendering fails
                        self.draw.text(
                            (self.current_x, self.current_y),
                            self.field_data,
                            fill='black'
                        )
                self.field_data = None
        elif command == 'GF':
            # Graphic field: ^GFa,total,total,bytes_per_row,<data>
            self._render_graphic(params)
        elif command == 'CF':
            # Change font
            pass
        elif command == 'BC':
            # Barcode: ^BCo,h,f,g,e,m - every parameter changes the label, so
            # the preview keeps them all and draws from the same element the
            # canvas would.
            parts = [p.strip() for p in params.split(',')]
            self.barcode_orientation = (parts[0][:1].upper()
                                        if parts and parts[0][:1].isalpha() else '')
            try:
                self.barcode_height = int(parts[1]) if len(parts) > 1 and parts[1] else 50
            except ValueError:
                self.barcode_height = 50
            self.barcode_options = tuple(parts[2:])
            self.is_barcode_mode = True
    
    def render_from_file(self, filepath: str) -> Image.Image:
        """
        Load ZPL from a file and render it.
        
        Args:
            filepath: Path to ZPL file
            
        Returns:
            PIL Image object
        """
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                zpl_content = f.read()
            return self.render(zpl_content)
        except Exception as e:
            raise IOError(f"Failed to read ZPL file: {e}")

