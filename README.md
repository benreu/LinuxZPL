# LinuxZPL - a ZPL Label Designer

Design ZPL (Zebra Programming Language) labels visually and print them straight
to a networked Zebra printer.

Two frontends sit on one core. **GTK3** and **PySide2/Qt5** are both
first-class: they answer to the same `FUNCTIONAL_SPEC.md`, and a conformance
suite checks on every change that they produce byte-identical ZPL. Which one
opens is only a question of which toolkit you have, or which you prefer.

## What makes the canvas trustworthy

The canvas is meant to be a proof of what the printer will produce, not an
approximation of it. Four things follow from that, and they are why this is not
just a drawing program that emits ZPL:

- **Everything is measured in printer dots.** Positions, sizes, font heights,
  frame thickness and bar widths are all integers, all dots. Nothing is stored
  in pixels, points or millimetres.
- **A thermal head only adds black; it cannot erase.** Overlapping fields
  combine, so an image placed over text does *not* hide the text - it prints
  through the image's white areas. The canvas composites the same way, and every
  designer affordance is translucent so it cannot conceal something that will
  still print.
- **Images are shown as the printer receives them** - resized, then reduced to
  1 bit with Floyd-Steinberg dithering. That same bitmap is what is saved and
  what is sent.
- **Text is drawn at the width it will print**, measured from the real font
  metrics, so `IIII` and `WWWW` do not claim the same width.

**ZPL files record no resolution.** A label of 812 x 1218 dots is 4 x 6 inches
on a 203 dpi printer and 2.7 x 4.1 on a 300 dpi one, so the designer tracks the
resolution a label was drawn for separately and offers to rescale when they
disagree.

## Features

- **Visual Designer**: drag-and-drop canvas with text, frame, barcode and image
  elements
- **Fonts**: pick any installed TrueType font per text element; upload, list and
  delete fonts on the printer, with a check before printing that the label's
  fonts are actually there
- **Network Printing**: straight over TCP to a Zebra, no printing subsystem
  involved
- **Per-element Print Toggle**: keep an element in the design and in the saved
  file, but leave it off the printed label
- **203, 300 and 600 dpi**: label size is set in inches, and a label drawn for
  one head resolution can be rescaled for another
- **Undo and redo** of every document change, 50 deep

## Requirements

- Python 3.10+
- Pillow >= 9.0.0, numpy >= 1.20.0
- fontconfig (`fc-list`, used to find installed fonts)
- **one** GUI toolkit:
  - GTK: `apt install python3-gi python3-gi-cairo gir1.2-gtk-3.0`
  - Qt: `apt install python3-pyside2.qtcore python3-pyside2.qtgui python3-pyside2.qtwidgets`

```bash
pip install -r requirements.txt        # core only
pip install -r requirements-gtk.txt    # or -qt.txt, if not using system packages
```

## Usage

```bash
./linuxzpl.py           # GTK if it is installed, otherwise Qt
./linuxzpl.py --gtk     # force the GTK frontend
./linuxzpl.py --qt      # force the Qt frontend
```

In VS Code, press **F5**; the default configuration is the flagless one.

### Quick Start

1. Add elements with the **+ Text**, **+ Frame**, **+ Barcode** and **+ Image**
   buttons
2. Drag elements to position them, or drag the handles to resize
3. Double-click an element to edit it - text, font, barcode value, image file
4. Right-click an element for **Print This Element** and the z-order actions
5. Set the printer address and resolution under **Settings -> Printer Settings**
   (**Test Connection** also asks the printer what dpi it is, and fills it in)
6. **File -> Print** to send the label

Use **Settings -> Label Size** for the label dimensions - presets of 4x6, 5x7,
6x4, 3x5 and 2x3 inches, or a custom size - and **Settings -> Printer
Fonts...** to manage the fonts stored on the printer.

`sample.zpl` is included to try the designer out.

### Keyboard shortcuts

Identical in both frontends.

| | | | |
|---|---|---|---|
| Ctrl+N | New | Ctrl+Z | Undo |
| Ctrl+O | Load | Ctrl+Shift+Z, Ctrl+Y | Redo |
| Ctrl+S | Save | Delete | Delete element |
| Ctrl+Shift+S | Save As | Ctrl+] / Ctrl+Shift+] | Bring Forward / to Front |
| Ctrl+P | Print | Ctrl+[ / Ctrl+Shift+[ | Send Backward / to Back |
| Ctrl+Q | Quit | | |

## Settings

Printer address, port and resolution are kept in
`~/.config/linuxzpl/settings.ini`, shared by both frontends. A missing or
corrupt file never blocks startup; the defaults are `192.168.50.21:9100` at
203 dpi.

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

Work that could be wrong in a way that changes what the printer produces lives
in `zplcore`; the frontends hold only what genuinely differs. See
`CONTRIBUTING.md` for how a feature lands in both, and `FUNCTIONAL_SPEC.md` for
the behaviour they implement.

## Tests

```bash
./tests/run.sh
```

`test_core.py` and `test_deviations.py` run offscreen and need no display.
`test_conformance.py` drives **both** frontends through the same scripted
editing session and diffs the ZPL after every step - it needs a display, using
`$DISPLAY` if set and otherwise `xvfb-run` (`apt install xvfb`).

## ZPL Commands Supported

Read when loading a file and written when saving:

- `^XA` / `^XZ` - Start / end format
- `^FO` - Set field origin (position)
- `^FD` / `^FS` - Field data / end field
- `^AF` - Built-in font selection
- `^A@` - Downloaded TrueType font, e.g. `^A@N,36,20,E:DEJAVUSA.TTF`
- `^GB` - Draw box
- `^BC` / `^BY` - Code 128 barcode and its module width
- `^GF` - Graphic field (images, 1-bit, where a set bit is black)
- `^PW` / `^LL` - Print width / label length
- `^FX` - Comment, used for the designer's own metadata

Four `^FX` keys carry what ZPL itself has nowhere to put, and printers ignore
them:

| Key | Holds |
|---|---|
| `^FXDESIGNER_DPI:` | the resolution the label was drawn for |
| `^FXDESIGNER_PREVIEW:` | the image at original quality, base64 JPEG |
| `^FXDESIGNER_PATH:` | where the image came from |
| `^FXDESIGNER_NOPRINT:` | an element kept in the design but not printed |

A `^FX` comment ends at the next caret rather than at the end of the line, so
any payload that could contain one is base64 encoded - otherwise a hidden
element's own `^FO` and `^FD` would resume executing and print anyway.

Sent to the printer but not rendered:

- `~DY` - Download a font to the printer
- `^HW` - List the objects stored on the printer
- `^ID` - Delete an object from the printer
- `~HI` - Ask the printer its model and head resolution

## Known limits

Recorded in `FUNCTIONAL_SPEC.md` section 18 as decisions rather than oversights:

- The canvas scales to fit the width. A label taller than the viewport scrolls;
  there is no zoom control.
- Barcodes are Code 128 subset B only, and the value is not validated against
  the subset.
- Rescaling between resolutions cannot be exact for barcodes: a module is a
  whole number of dots, so 2 becomes 3 going from 203 to 300 dpi. Positions and
  heights scale exactly.
