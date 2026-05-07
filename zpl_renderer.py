"""
ZPL (Zebra Programming Language) Renderer

Renders ZPL commands to PIL Image objects for display.
"""

from PIL import Image, ImageDraw, ImageFont
import re
from typing import Tuple, List, Optional


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
    
    def _get_font(self, size: int) -> ImageFont.FreeTypeFont:
        """Get or create a cached font."""
        if size not in self.font_cache:
            try:
                # Try to use a default system font
                self.font_cache[size] = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", size)
            except (IOError, OSError):
                # Fallback to default font
                self.font_cache[size] = ImageFont.load_default()
        return self.font_cache[size]
    
    def _parse_position(self, x: str, y: str) -> Tuple[int, int]:
        """Convert ZPL position values to pixels."""
        # ZPL coordinates are in dots (1/203 inch at 203 DPI)
        return (int(x), int(y))
    
    def _render_barcode(self, barcode_value: str, x: int, y: int, height: int):
        """Render a barcode visual representation using bars."""
        # Create a simple barcode-like representation with vertical bars
        bar_width = 3
        bar_spacing = 1
        value_length = len(barcode_value)
        barcode_width = value_length * (bar_width + bar_spacing)
        
        # Generate pattern based on barcode value (simple hash-based pattern)
        pattern = []
        for char in barcode_value:
            # Use ASCII value to determine bar pattern
            val = ord(char) % 2
            pattern.append(val)
        
        # Draw bars
        current_x = x
        for i, bar in enumerate(pattern):
            if bar == 1:
                # Draw black bar
                self.draw.rectangle(
                    [(current_x, y), (current_x + bar_width, y + height)],
                    fill='black'
                )
            current_x += bar_width + bar_spacing
        
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
            # Format: ^AFN,36,20 means orientation=N, height=36, width=20
            match = re.match(r'([A-Z]?)(?:,(\d+))?(?:,(\d+))?', params)
            if match and match.group(2):
                self.current_font_size = int(match.group(2))
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


def render_zpl(zpl_content: str, width: int = ZPLRenderer.DEFAULT_WIDTH,
               height: int = ZPLRenderer.DEFAULT_HEIGHT) -> Image.Image:
    """
    Convenience function to render ZPL content.
    
    Args:
        zpl_content: ZPL command string
        width: Image width in pixels
        height: Image height in pixels
        
    Returns:
        PIL Image object
    """
    renderer = ZPLRenderer(width=width, height=height)
    return renderer.render(zpl_content)
