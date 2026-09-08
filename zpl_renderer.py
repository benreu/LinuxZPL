"""
ZPL (Zebra Programming Language) Renderer

Renders ZPL commands to PIL Image objects for display.
"""

from PIL import Image, ImageDraw, ImageFont
import re
from typing import Tuple, List, Optional
from code128 import encode_b as _code128_modules


class ZPLRenderer:
    """Renders ZPL (Zebra Programming Language) commands to images."""
    
    # Standard label size: 4x6 inches at 203 DPI = 812x1218 pixels
    DEFAULT_WIDTH = 812
    DEFAULT_HEIGHT = 1218
    DEFAULT_DPI = 203
    
    def __init__(self, width: int = DEFAULT_WIDTH, height: int = DEFAULT_HEIGHT, dpi: int = DEFAULT_DPI):
        """
        Initialize the ZPL renderer.
        
        Args:
            width: Image width in pixels
            height: Image height in pixels
            dpi: Dots per inch for font sizing
        """
        self.width = width
        self.height = height
        self.dpi = dpi
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
        self.custom_font_path: Optional[str] = None
        self.current_field_font_path: Optional[str] = None
        self.font_registry: dict = {}

    def set_font(self, font_path: str):
        self.custom_font_path = font_path
        self.font_cache.clear()

    def register_font(self, printer_font_name: str, font_path: str):
        self.font_registry[printer_font_name.upper()] = font_path
        self.font_cache.clear()

    def _get_font(self, size: int) -> ImageFont.FreeTypeFont:
        """Get or create a cached font."""
        path = self.current_field_font_path or self.custom_font_path or "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
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
        """Render a barcode visual representation using bars."""
        value_length = len(barcode_value)
        # Code 128B width formula matches BarcodeElement: (start+data+check+stop) * 2 dots/module
        barcode_width = (35 + value_length * 11) * 2

        # Draw Code 128B bars
        mods = _code128_modules(barcode_value)
        mod_w = barcode_width / sum(mods)
        cx = x
        for i, m in enumerate(mods):
            if i % 2 == 0:  # bars are at even indices
                right = round(cx + m * mod_w)
                self.draw.rectangle([(round(cx), y), (right, y + height)], fill='black')
            cx += m * mod_w

        # Draw border around barcode
        self.draw.rectangle(
            [(x - 2, y - 2), (x + barcode_width + 2, y + height + 2)],
            outline='black',
            width=1
        )
        
        # Draw barcode value text below
        font = self._get_font(8)
        text_y = y + height + 2
        try:
            self.draw.text((x, text_y), barcode_value, fill='black', font=font)
        except:
            self.draw.text((x, text_y), barcode_value, fill='black')
    
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
        elif command == 'AF':
            # Font selection: ^AFn,h,w (orientation, height, width)
            match = re.match(r'([A-Z]?)(?:,(\d+))?(?:,(\d+))?', params)
            if match and match.group(2):
                self.current_font_size = int(match.group(2))
            self.current_field_font_path = None
        elif command == 'A@':
            # Downloaded font: ^A@o,h,w,device:name.TTF
            match = re.match(r'([A-Z]?),(\d+),(\d+),([^:]+):(.+)', params)
            if match:
                self.current_font_size = int(match.group(2))
                font_name = match.group(5).replace('.TTF', '').replace('.ttf', '').upper()
                self.current_field_font_path = self.font_registry.get(font_name)
        elif command == 'GB':
            # Draw box: ^GBw,h,t,c
            match = re.match(r'(\d+),(\d+)(?:,(\d+))?(?:,(\d+))?', params)
            if match:
                width = int(match.group(1))
                height = int(match.group(2))
                thickness = int(match.group(3)) if match.group(3) else 1
                # Draw rectangle
                self.draw.rectangle(
                    [(self.current_x, self.current_y),
                     (self.current_x + width, self.current_y + height)],
                    outline='black',
                    width=thickness
                )
        elif command == 'FS':
            # End field: render current field data
            if self.field_data is not None:
                if self.is_barcode_mode:
                    # Render as barcode
                    self._render_barcode(self.field_data, self.current_x, self.current_y, self.barcode_height)
                    self.is_barcode_mode = False
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
            # Barcode: ^BCo,h,f,g,e,m
            # Format: ^BC,height
            match = re.match(r'[A-Z]?,(\d+)?', params)
            if match and match.group(1):
                self.barcode_height = int(match.group(1))
                self.is_barcode_mode = True
            elif match:
                # No height specified, use default
                self.barcode_height = 50
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

