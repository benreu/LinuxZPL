# LinuxZPL - GTK ZPL Label Designer

A GTK3-based Python application for designing ZPL (Zebra Programming Language) labels
visually and printing them straight to a networked Zebra printer.

## Features

- **Visual Designer**: Drag-and-drop canvas with text, frame, barcode and image elements
- **WYSIWYG Preview**: The canvas shows what the printer produces - 1-bit dithered
  images, real text widths, and overlapping elements composited the way they print
- **Fonts**: Pick any installed TrueType font per text element; upload, list and delete
  fonts on the printer
- **Network Printing**: Print over TCP to a Zebra, with a check that the label's fonts
  are on the printer first
- **Per-element Print Toggle**: Keep an element in the design but leave it off the label

## Requirements

- Python 3.10+
- GTK 3.22+
- fontconfig (`fc-list`, used to find installed fonts)
- Pillow >= 9.0.0
- PyGObject >= 3.40.0
- numpy >= 1.20.0

## Installation

1. Install system dependencies:
```bash
# On Ubuntu/Debian:
sudo apt-get install python3-gi python3-gi-cairo gir1.2-gtk-3.0 libgtk-3-0 fontconfig

# On Fedora:
sudo dnf install python3-gobject gtk3 fontconfig
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

1. Add elements with the **+ Text**, **+ Frame**, **+ Barcode** and **+ Image** buttons
2. Drag elements to position them, or drag the handles to resize
3. Double-click an element to edit it - text, font, barcode value, image file
4. Right-click an element for **Print This Element** and the z-order actions
   (Bring to Front / Forward, Send Backward / to Back)
5. Set the printer address under **Settings -> Printer Settings**
6. **File -> Print** to send the label

Use **Settings -> Label Size** to change the label dimensions, and
**Settings -> Printer Fonts...** to see and manage the fonts stored on the printer.

### Sample ZPL Files

A sample ZPL file (`sample.zpl`) is included to demonstrate the designer.

## ZPL Commands Supported

Read when loading a file and written when saving:

- `^XA` / `^XZ` - Start / end format
- `^FO` - Set field origin (position)
- `^FD` / `^FS` - Field data / end field
- `^AF` - Built-in font selection
- `^A@` - Downloaded TrueType font, e.g. `^A@N,36,20,E:DEJAVUSA.TTF`
- `^GB` - Draw box
- `^BC` - Code 128 barcode
- `^GF` - Graphic field (images, 1-bit)
- `^PW` / `^LL` - Print width / label length
- `^FX` - Comment, used for the designer's own metadata

Sent to the printer but not rendered:

- `~DY` - Download a font to the printer
- `^HW` - List the objects stored on the printer
- `^ID` - Delete an object from the printer

## Application Structure

- `gtk_zpl_viewer.py` - GTK3 application, menus and dialogs
- `zpl_designer.py` - Design canvas and the element classes
- `zpl_fonts.py` - Font discovery, printer font naming, and printer font I/O
- `zpl_renderer.py` - ZPL to PIL image renderer, used for the file chooser preview
- `code128.py` - Code 128 barcode encoding
- `requirements.txt` - Python package dependencies
- `run.sh` - Launcher script
- `sample.zpl` - Example ZPL file for testing

## Features

### Current
- ✓ Visual drag-and-drop label designer
- ✓ Text, frame, Code 128 barcode and image elements
- ✓ Per-element TrueType fonts from the installed system fonts
- ✓ Printer font management (upload / list / delete) and a pre-print font check
- ✓ Network printing over TCP
- ✓ Configurable label size
- ✓ Load and save ZPL files

### Not Included
- ✗ Image/PNG export
- ✗ Barcode symbologies other than Code 128
- ✗ Multi-label batches or variable data

## Notes

The renderer uses PIL (Pillow) to create images from ZPL commands. Default label size is
4x6 inches at 203 DPI (812x1218 pixels), which is the standard for shipping labels.

**Images print as 1-bit dithered bitmaps.** A Zebra prints single black dots, so colour
and greyscale are reduced to a Floyd-Steinberg dither. The canvas shows the same dithered
result rather than the original image, so what you design is what you get.

**A thermal printer only ever adds black - it cannot erase.** Overlapping fields combine,
so an image placed over a text box does *not* hide it: the text still prints through the
image's white areas. To leave an element off the label, right-click it and untick
**Print This Element**.

**Designer metadata lives in `^FX` comments**, which printers ignore:
`^FXDESIGNER_PREVIEW` (a JPEG of the original image so quality survives a reload),
`^FXDESIGNER_PATH` (the source image path) and `^FXDESIGNER_NOPRINT` (an element that is
kept in the design but not printed). Their payloads are base64 because `^FX` only comments
up to the next `^`.

A saved `.zpl` records only the printer font *name* (`E:DEJAVUSA.TTF`), not the font file.
On reload the matching installed `.ttf` is looked up again by that name. Since the name is
truncated to 8 characters, a label saved with a bold or italic face may reopen in the
regular face of the same family; what prints is unaffected.

## License

Free to use and modify.
