# LinuxZPL - a ZPL Label Designer

Design ZPL (Zebra Programming Language) labels visually and print them straight
to a networked Zebra printer.

Two frontends sit on one core. **GTK3** and **PySide2/Qt5** are both
first-class: they answer to the same `FUNCTIONAL_SPEC.md`, produce byte-identical
ZPL, and a conformance suite checks that on every change. Which one opens is
only a question of which toolkit you have, or which you prefer.

## Features

- **Visual Designer**: Drag-and-drop canvas with text, frame, barcode and image
  elements
- **WYSIWYG Preview**: The canvas shows what the printer produces - 1-bit
  dithered images, real text widths, and overlapping elements composited the way
  they print
- **Fonts**: Pick any installed TrueType font per text element; upload, list and
  delete fonts on the printer
- **Network Printing**: Print over TCP to a Zebra, with a check that the label's
  fonts are on the printer first
- **Per-element Print Toggle**: Keep an element in the design but leave it off
  the label
- **203, 300 and 600 dpi**: Label size is set in inches, and a label drawn for
  one head resolution can be rescaled for another

## Requirements

- Python 3.10+
- Pillow >= 9.0.0, numpy >= 1.20.0
- fontconfig (`fc-list`, used to find installed fonts)
- **one** GUI toolkit:
  - Qt: `apt install python3-pyside2.qtcore python3-pyside2.qtgui python3-pyside2.qtwidgets`
  - GTK: `apt install python3-gi python3-gi-cairo gir1.2-gtk-3.0`

```bash
pip install -r requirements.txt        # core only
pip install -r requirements-qt.txt     # or -gtk.txt, if not using system packages
```

## Usage

```bash
./linuxzpl.py           # whichever toolkit is installed, preferring Qt
./linuxzpl.py --gtk     # force the GTK frontend
./linuxzpl.py --qt      # force the Qt frontend
```

In VS Code, press **F5** and pick a configuration.

### Quick Start

1. Add elements with the **+ Text**, **+ Frame**, **+ Barcode** and **+ Image**
   buttons
2. Drag elements to position them, or drag the handles to resize
3. Double-click an element to edit it - text, font, barcode value, image file
4. Right-click an element for **Print This Element** and the z-order actions
5. Set the printer address and resolution under **Settings -> Printer Settings**
   (**Test Connection** also asks the printer what dpi it is, and fills it in)
6. **File -> Print** to send the label

Use **Settings -> Label Size** to change the label dimensions, and
**Settings -> Printer Fonts...** to manage the fonts stored on the printer.

## Structure

```
zplcore/    no GUI toolkit, runs headless
  model.py       elements and the Document, the ZPL written out
  parser.py      the ZPL read back in
  fonts.py       discovery, printer object naming, printer I/O
  renderer.py    ZPL to a PIL image, for file chooser previews
  geometry.py    handles, hit-testing, dragging, resizing
  textraster.py  the text raster both canvases blit
  workflow.py    the decisions that decide whether a label prints correctly
  code128.py     barcode module encoding
gtkui/      GTK3 frontend: Cairo painting, dialogs, menus
qtui/       PySide2/Qt5 frontend: QPainter painting, dialogs, menus
tests/      core checks, and the conformance suite the frontends must agree on
```

See `CONTRIBUTING.md` for how a feature lands in both frontends, and
`FUNCTIONAL_SPEC.md` for the behaviour they both implement.

## ZPL Commands Supported

Read when loading a file and written when saving:

- `^XA` / `^XZ` - Start / end format
- `^FO` - Set field origin (position)
- `^FD` / `^FS` - Field data / end field
- `^AF` - Built-in font selection
- `^A@` - Downloaded TrueType font, e.g. `^A@N,36,20,E:DEJAVUSA.TTF`
- `^GB` - Draw box
- `^BC` / `^BY` - Code 128 barcode and its module width
- `^GF` - Graphic field (images, 1-bit)
- `^PW` / `^LL` - Print width / label length
- `^FX` - Comment, used for the designer's own metadata

Sent to the printer but not rendered:

- `~DY` - Download a font to the printer
- `^HW` - List the objects stored on the printer
- `^ID` - Delete an object from the printer
- `~HI` - Ask the printer its model and head resolution
