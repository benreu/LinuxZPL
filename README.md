# LinuxZPL - GTK ZPL Viewer

A GTK3-based Python application for rendering and viewing ZPL (Zebra Programming Language) output.

## Features

- **ZPL Rendering**: Parse and render ZPL commands to images
- **File Loading**: Load ZPL files via file dialog
- **Live Editing**: Edit ZPL code and refresh the preview
- **Simple UI**: Split-pane interface with text editor and preview

## Requirements

- Python 3.10+
- GTK 3.22+
- Pillow >= 9.0.0
- PyGObject >= 3.40.0

## Installation

1. Install system dependencies:
```bash
# On Ubuntu/Debian:
sudo apt-get install python3-gi python3-gi-cairo gir1.2-gtk-3.0 libgtk-3-0

# On Fedora:
sudo dnf install python3-gobject gtk3
```

2. Install Python dependencies:
```bash
pip install -r requirements.txt
```

## Usage

Run the application:
```bash
python3 gtk_zpl_viewer.py
```

Or with direct execution:
```bash
chmod +x gtk_zpl_viewer.py
./gtk_zpl_viewer.py
```

### Quick Start

1. Click "Load ZPL File" to open a ZPL file
2. View the rendered output in the preview pane on the right
3. Edit the ZPL code in the left pane and click "Refresh" to see changes

### Sample ZPL Files

A sample ZPL file (`sample.zpl`) is included to demonstrate the viewer.

## ZPL Commands Supported

Currently supported ZPL commands:

- `^XA` - Start format
- `^XZ` - End format
- `^FO` - Set field origin (position)
- `^FD` - Field data (text)
- `^FS` - End field
- `^AF` - Font selection
- `^GB` - Draw box
- `^BC` - Barcode (basic rendering)

## Application Structure

- `zpl_renderer.py` - Core ZPL rendering engine using PIL
- `gtk_zpl_viewer.py` - GTK3 GUI application
- `requirements.txt` - Python package dependencies
- `sample.zpl` - Example ZPL file for testing

## Features

### Current
- ✓ Parse and render ZPL commands
- ✓ Load ZPL files from filesystem
- ✓ Live editor with preview
- ✓ Basic font rendering
- ✓ Box drawing support

### Not Included
- ✗ Image/PNG export
- ✗ Advanced barcode rendering
- ✗ Image insertion in ZPL

## Notes

The renderer uses PIL (Pillow) to create images from ZPL commands. Default label size is 4x6 inches at 203 DPI (812x1218 pixels), which is the standard for shipping labels.

## License

Free to use and modify.
