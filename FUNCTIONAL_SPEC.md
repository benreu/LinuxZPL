# LinuxZPL — Functional Specification

What the application does, described so it can be rebuilt on a different GUI
toolkit or in a different language. It covers behaviour, data and wire formats;
it does not describe the current Python/GTK implementation except where an
observable behaviour depends on it.

---

## 1. Purpose

A visual designer for Zebra thermal labels. The user places text, frames,
barcodes and images on a label-sized canvas, saves the result as a `.zpl` file,
and prints it over the network to a Zebra printer. The canvas is meant to be a
proof of what the printer will produce, not an approximation of it.

---

## 2. Concepts that must survive the port

These four ideas drive most of the behaviour below. A port that drops them will
look right and print wrong.

**Everything is measured in printer dots.** Element positions and sizes, font
heights, frame thickness, bar widths — all integers, all dots. No pixels, no
points, no millimetres are stored anywhere.

**ZPL files record no resolution.** A label of 812 × 1218 dots is 4 × 6 inches
on a 203 dpi printer and 2.7 × 4.1 inches on a 300 dpi one. The same file is
physically a different size on different hardware. The application therefore
tracks the resolution a label was drawn for separately (§11).

**A thermal printer only adds black; it cannot erase.** Overlapping fields
combine — an image placed over text does *not* hide the text, which prints
through the image's white areas. Anything a port draws as opaque on screen
misrepresents the output.

**The canvas shows the printed result, not the source material.** Images are
displayed as the 1-bit dithered bitmap the printer receives; text is displayed
at the width it will actually print. Where a designer affordance must be drawn
(element outlines, backgrounds), it is translucent so it cannot conceal
something that will still print.

---

## 3. The document

### 3.1 Label

| Property | Meaning |
|---|---|
| `label_width`, `label_height` | Label size in dots. Written as `^PW` / `^LL`. |
| `dpi` | Resolution the label is drawn for. Written as designer metadata. |
| elements | Ordered list. Index 0 is the bottom of the z-order, the last element is the top. |

At startup the label is 4 × 6 inches at the configured printer resolution
(812 × 1218 dots at 203 dpi, 1200 × 1800 at 300).

### 3.2 Properties common to every element

`x`, `y` (top-left corner, dots), `width`, `height` (dots), `element_type`, and
`print_enabled` (default true — see §6.6).

### 3.3 Element types

#### Text

| Property | Default |
|---|---|
| `text` | `"New Text"` |
| `font_height` | 36 dots |
| `font_width` | 20 dots |
| `font_path`, `font_family`, `printer_font_name` | none (uses the document font, or the printer's built-in font) |
| `font_code` | `F` - the built-in font designator, written as `^A<code>`. `0` is the scalable font most other tools use |
| `orientation` | `N` - `^A`'s orientation letter: `N`, `R` (90°), `I` (180°), `B` (270°) |
| `block` | none - a field block (`^FB`), when the text wraps rather than running on one line |

`height` always equals `font_height` **unless the element has a block**. **`width` is derived, never set
directly**, and must be recomputed whenever the text, the font or either font
dimension changes:

- **With a TrueType font selected**: measure the advance width of the whole
  string with that font at em size = `font_height`, then multiply by
  `font_width / font_height` (the printer scales the em square to
  `font_width × font_height`). Round to an integer, minimum 1.
- **Without one** (Zebra's built-in font A, which is fixed-width):
  `len(text) × font_width`.

Getting this wrong is the single most visible defect a port can have: assuming
fixed width for a proportional font makes `IIII` print far narrower and `WWWW`
far wider than the canvas showed.

**A field block replaces both derivations.** `^FB` gives a width in dots, a
maximum number of lines, extra spacing between them, a justification
(left / centre / right / justified) and a hanging indent. Text in a block:

- breaks first at `\&`, a forced line break inside the field data
- then wraps greedily to the block width, measured with the same metrics as
  above, so the wrap and the box that holds it cannot disagree
- **drops** lines past the maximum rather than overflowing, as the printer does
- takes its `width` from the block and its `height` from
  `lines × (font_height + line spacing)`

A word too long for the block is left on its own line rather than split.

**A quarter turn transposes the footprint.** `width` and `height` are the box
the label occupies, so at `R` and `B` they are the run and the stack swapped -
exactly as a rotated barcode's are. The box stays axis-aligned at every quarter
turn, which is why hit-testing, dragging, the resize handles and the clamping
need to know nothing about rotation: only the drawing turns, about the
element's origin. A wrapped block turns with its text, its width still measured
along the text.

**Justified (`J`) is the one that cannot be expressed as a starting x.** Every
line but the one that ends its paragraph is laid out word by word, with the
slack between the block width and the words shared equally among the gaps, so
the line meets both edges. A line that ends a paragraph - including whichever
line survives when the maximum truncates the rest - is left aligned, because it
is short from the text running out rather than from the next word not fitting.

**Editing a block is editing the text, not spelling `\&`.** The value field is
a multi-line box: a line break typed into it is written to the file as `\&`,
and `\&` read from a file appears in the box as a line break. Two rules keep
the two in step, because `\&` outside a `^FB` prints as the two characters it is
written with rather than breaking:

- typing a break into an element with no block gives it one, wide enough for
  its longest line, so nothing moves on the canvas
- switching wrapping off joins the lines back into one with spaces, rather than
  leaving a break behind for the printer to print

**The resize handles ask a block for a wrap, not a rectangle.** The side handles
set the block width and the text re-flows; the top and bottom handles set the
maximum number of lines, so dragging the bottom edge up cuts lines the printer
would then drop and dragging it down reveals them. The box is then whatever the
text wraps into - it is never stretched to fill the dragged rectangle, and the
font size is the dialog's business alone.

#### Frame

| Property | Default |
|---|---|
| `width`, `height` | 200 × 150 dots |
| `thickness` | 2 dots |
| `colour` | `B` — `^GB`'s fourth parameter, `B` or `W` |
| `rounding` | 0 — `^GB`'s fifth, 0 to 8 |

**White is not the absence of a frame.** A thermal head only adds black, so a
`W` frame prints nothing on bare stock and shows only over something already
black. Drawing it black instead is the one case where the canvas would show the
opposite of what prints.

**Rounding** is an index, not a radius: 8 is the most ZPL will round, which is
half the shorter side, so the radius is `rounding / 8 × min(width, height) / 2`.
A border insets its own radius by half the thickness, as it insets its path.

Thickness is a border drawn inward from the element bounds. Its useful maximum
is `min(width, height) / 2`, at which point the border meets in the middle and
the frame is a solid filled rectangle. Clamp to that maximum, minimum 1.

#### Barcode

Code 128, subsets B and C.

| Property | Default |
|---|---|
| `barcode_value` | `"123456789"` |
| `bar_height` | 100 dots - the bars themselves, `^BC`'s own height |
| `module_width` | 2 dots |
| `orientation` | none - `N` upright, `R` 90°, `I` 180°, `B` 270° |
| `show_text` | true - whether the value prints as an interpretation line |
| `text_above` | false - the line goes above the bars instead of below |
| `check_digit` | false - append a UCC/EAN mod-10 digit |
| `mode` | `N` - `A` lets the symbol use subset C |
| `font` | none - the `^A` before the `^BC`, which sets the interpretation line's font |

**`width` and `height` are the footprint, not the bars.** The element box is
the bars plus the interpretation line, transposed when the barcode is rotated:

```
run   = sum(module widths) × module_width
stack = bar_height + interpretation line height
box   = (run, stack) upright,  (stack, run) rotated
```

Because a quarter turn leaves the box axis-aligned, rotation needs nothing from
hit-testing, dragging or the resize handles - they only ever see the box.

**Derive the width from the symbol, not from the character count.** Subset C
packs two digits into one symbol, so `(35 + n×11) × module_width` is wrong by
nearly half for a numeric value in mode A. Summing the encoded module widths is
right for both subsets.

Mode `A` is the printer's automatic subset switching: move into subset C across
a run of four or more digits (or two, when the whole value is numeric and the
start code is free), and back to B for anything else. Switching for a shorter
run costs more than it saves.

The interpretation line prints the encoded value - including the check digit
when there is one - in the font the `^A` selected, at that font's dot height. A
barcode whose line is switched on and whose file named no font is given one, so
what prints is stated rather than inherited from the printer's `^CF`.

Resizing a barcode is a request for a module width and a bar height, not for an
arbitrary rectangle: the drag sets those two and the box snaps back to what
they produce, so the symbol is never stretched to fill.

**`width` is derived**: `(35 + len(value) × 11) × module_width`. The constant 35
is the start, check and stop modules; each data character is 11 modules.

#### Image

| Property | Default |
|---|---|
| `image_path` | the file the user chose |
| `width`, `height` | 200 × 200 dots |

Loaded from JPEG or PNG. The image is resized to the element's dot dimensions
(high-quality/Lanczos resampling) and then converted to 1-bit with
Floyd–Steinberg dithering. That dithered bitmap is what is displayed, what is
saved, and what is printed. Resizing the element re-dithers from the original
source at the new size — never from the previous bitmap.

---

## 4. Main window

```
┌─────────────────────────────────────────────────────────┐
│ File Edit View Settings           Title        ↶  ↷   ✕ │  header bar
├─────────────────────────────────────────────────────────┤
│ [+ Text] [+ Frame] [+ Barcode] [+ Image] [− Fit +] [Del]│  toolbar
├─────────────────────────────────────────────────────────┤
│                                                         │
│                    design canvas                        │  scrollable
│                                                         │
├─────────────────────────────────────────────────────────┤
│ Ready                                            75% ▏  │  status bar
└─────────────────────────────────────────────────────────┘
```

The menu bar sits in the header bar; undo and redo are icon buttons at the
opposite end, disabled when their history stack is empty. The canvas is
scrollable, with the vertical scrollbar always present so the width a fit is
measured against cannot change when it appears.

**Opening size.** The window opens onto the monitor the pointer is on — not
whichever is primary — at a comfortable fraction of that monitor's *work area*,
never larger than it, capped at 1200 × 900, and centred. A window taller than
the work area is placed wherever the window manager can put it, which on a
stacked multi-monitor desktop can be almost entirely off the bottom edge and is
indistinguishable from the application never starting. Nothing may impose a
minimum that stops the window being shrunk: the canvas is inside a scroll area
and must never dictate the window's size.

**Remembered.** Position and size are written to the settings file on close and
restored next time, clamped back onto the monitor that is attached then — which
may be smaller, or elsewhere, than the one they were saved on.

The zoom percentage is its own widget in the status bar, so a status message
does not wipe it away; `(fit)` marks a scale being decided by a fit rather than
pinned.

---

## 5. Canvas behaviour

### Display

- The canvas draws in label coordinates, uniformly scaled. All hit-testing
  converts pointer position back to label coordinates before comparing against
  element geometry.
- **The scale is a zoom level, or a fit.** A fit is measured against the
  *visible area*, never against the canvas itself: the canvas's own size is a
  consequence of the scale, so measuring against it is a feedback loop.
  - **Fit Label** — the smaller of the two ratios, so the whole label is
    visible. This is the default, for a new document and after a load.
  - **Fit Width** — the label fills the width and a taller label scrolls.
  - **A pinned zoom** — one of a fixed ladder of steps from 5% to 800%. Zoom In
    and Zoom Out move to the next step above or below *the current scale*, so
    zooming in from a fitted 62% lands on 67% rather than jumping back to
    wherever the last step was.
- **The canvas widget is exactly the label at the current scale**, and the label
  is drawn from its origin. That is what gives the scroll area something to
  scroll, what lets the container centre a canvas smaller than the view, and
  what keeps every hit-test free of a pan offset. A canvas that is not resized
  with the scale clips the part of the label that falls outside it, with no
  scrollbar to reach it.
- **Zooming about the pointer**: Ctrl with the wheel steps the zoom and moves
  the scroll offsets so the dot that was under the pointer is still under it.
  A plain wheel scrolls.
- White background; the label boundary is a light grey dashed rectangle.
- Elements are drawn in list order, bottom first.
- An element with `print_enabled` false is drawn at 35% opacity — visible and
  fully editable, but marked as not printing.
- Text and frame elements are drawn with a translucent background and a thin
  outline (blue when selected, lighter otherwise) so nothing beneath them is
  hidden.
- Images are drawn as the dithered bitmap with **white treated as
  transparent**, so elements underneath remain visible, exactly as they will
  still print.

### Selection and manipulation

- **Left click** selects the topmost element containing the point, or clears the
  selection.
- **Drag** moves the selected element. Position is clamped so the element stays
  inside the label: `0 ≤ x ≤ label_width − width`, likewise for y.
- **Eight resize handles** on the selected element — four corners, four edge
  midpoints — drawn as small filled squares. A handle is **8 screen pixels**,
  drawn and hit-tested at `8 / scale` dots, so it is the same size to the
  pointer at every zoom. A handle is hit if the pointer is within that of its
  centre. While hovering one, the pointer changes to the
  matching directional resize cursor (`nw-resize`, `n-resize`, `ne-resize`,
  `w-resize`, `e-resize`, `sw-resize`, `s-resize`, `se-resize`).
- Resizing enforces a **minimum of 20 × 20 dots**, clamps the element to the
  label bounds, and then applies per-type rules:
  - frame: thickness clamped to `min(width, height) / 2`
  - text: `font_height` is set to the new height, `font_width` is solved so the
    text prints at the new width, and the box is then snapped to that printed
    width — the outline the user drags is the outline that prints
- **Double click** (same element, within 500 ms) opens that element's edit
  dialog.
- **Right click** selects the element under the pointer and opens a context menu
  (§6.6).

---

## 6. Commands

### 6.1 File

| Command | Behaviour |
|---|---|
| **New** | Prompts about unsaved changes (§6.7), then a blank 4 × 6 label at the printer's resolution. Clears the elements, the undo history and the current file, and resets the status to `Ready`. |
| **Open…** | Prompts about unsaved changes (§6.7), then a file chooser filtered to `*.zpl`. The chooser previews the selected `.zpl` by rendering it to an image, scaled to at most 300 px wide. Opening replaces the whole document and resets the undo history. |
| **Save** | Writes to the current path, or behaves as Save As if there is none. |
| **Save as…** | File chooser, default name `untitled.zpl`. Adopts the chosen path as the current file. |
| **Print** | §9. |
| **Quit** | Prompts about unsaved changes (§6.7). |

**The menu is in four groups**, separated in this order: start a document
(New, Open), persist it (Save, Save as), print it, leave. A port that runs them
together is the thing this grouping exists to avoid.

Saving refuses an empty document ("No content to save" — a document whose ZPL
is empty or just `^XA` / `^XZ`). Save clears the modified flag; a failed save
must report the real error and leave the flag set.

### 6.2 Edit

Undo, Redo, Delete, then Bring to Front / Bring Forward / Send Backward / Send
to Back. Delete and the four z-order items are disabled when nothing is
selected; the raise pair is disabled when the selection is already on top and
the lower pair when it is already at the bottom. Sensitivity is re-evaluated
each time the menu opens.

### 6.3 View

Zoom In, Zoom Out, then Fit Label, Fit Width, Actual Size — separated into
those two groups. §5 describes what each does to the scale.

### 6.4 Settings

| Command | Behaviour |
|---|---|
| **Label Size** | §7 |
| **Printer Settings** | §7 |
| **Printer Fonts…** | §10.4 |

### 6.5 Toolbar

`+ Text`, `+ Frame`, `+ Barcode` add an element with the defaults from §3.3.
`+ Image` opens a file chooser (JPEG/PNG) first. Each new element is placed at a
staggered offset so successive additions do not stack exactly, and becomes the
selection. `Delete` removes the selected element.

### 6.6 Element context menu (right click)

- **Print This Element** — a checkbox, default on. Unticking keeps the element
  in the design and in the saved file but leaves it off the printed label. This
  is how a user suppresses an element that would otherwise print through an
  image covering it.
- **Bring to Front / Bring Forward / Send Backward / Send to Back**, disabled at
  the ends of the z-order.

### 6.7 The unsaved-changes prompt

Shown when loading a file or quitting with unsaved changes. Three choices:

| Choice | Result |
|---|---|
| Save (default) | Save, then continue **only if a file was actually written** — a cancelled or failed save aborts the operation |
| Discard Changes | Continue, losing the changes |
| Cancel (also Escape / closing the dialog) | Abort; the document is untouched |

Loading a file must clear the modified flag, including any flag set as a side
effect of building elements while parsing.

---

## 7. Dialogs

The three element editors that are forms of fields — Edit Text, Edit Frame and
Edit Barcode — are **non-modal child windows** of the designer. Each is
transient for the designer, so it floats above it, follows it and closes with
it rather than taking a window of its own, but it never blocks it: the canvas,
the menus and the toolbar stay live while one is open. Edit Image is a file
chooser rather than a form, and stays modal.

- **One editor per element.** Two different elements may each have one open at
  once; double-clicking an element that already has one raises that window
  instead of opening a second onto the same element.
- **The edit applies on OK**, never as it is typed. Cancel, Escape and the
  window's close button all leave the document untouched, and OK records one
  history entry (§12).
- Because the designer stays live, an element can be moved or resized on the
  canvas while its editor is open. The editor's fields still hold the values it
  was opened with, so accepting it afterwards writes those back — the Frame
  editor's Width and Height will undo a resize made behind it.
- The font chooser opened from Edit Text is modal to that editor alone, not to
  the application.

The remaining dialogs — Label Size, Printer Settings, the file choosers and the
prompts — are modal.

| Dialog | Fields | Range / notes |
|---|---|---|
| **Edit Text** | Text (multi-line); Font Height; Font Width; Orientation; Font (Choose… / Clear); Wrap; Wrap Width; Max Lines; Line Spacing; Justification; Indent | Heights and widths 8–500 dots. Choose… lists installed TrueType families only; Clear reverts to the document default, shown as "Default (family)". The six wrap fields are the `^FB` block (§3.3): 10–2000 dots, 1–64 lines, −100–100 spacing, 0–2000 indent, and the justification list of §3.3. All but the checkbox are insensitive while Wrap is clear; Wrap Width starts at the width the text already prints at. |
| **Edit Frame** | Width; Height; Thickness; Colour; Corner Rounding | 10–800, 10–1200, and 1 to `min(width, height) / 2` — the thickness maximum updates live as the size fields change. Colour is `^GB`'s `B`/`W`, rounding its 0–8 (§3.3). |
| **Edit Barcode** | Value; Bar Height; Module Width; Orientation; Value Text; Text Height; UCC Check Digit; Mode | Bar height 20–300 dots, module width 1–20, text height 6–200. The remaining four are `^BC`'s own parameters (§3.3); width is derived from the symbol, never entered. |
| **Edit Image** | file chooser | Replaces the source file, keeping position and size |
| **Label Size** | Presets 4×6, 5×7, 6×4, 3×5, 2×3; custom Width and Height **in inches** | 0.5–25 inches, two decimals, stepping by a tenth. A live hint shows the resulting dots at the current resolution and the `^PW` / `^LL` values. Shrinking clamps elements to the new bounds. |
| **Printer Settings** | Address; Port; DPI; Test Connection | Port 1–65535. DPI is a choice of 203 / 300 / 600. Test Connection opens the socket and then asks the printer its resolution, filling the DPI field in (§11). |

---

## 8. File format

### 8.1 What is written

```
^XA
^PW<label_width>
^LL<label_height>
^FXDESIGNER_DPI:<dpi>
  ... one block per element, in z-order ...
^XZ
```

| Element | Block |
|---|---|
| Text, built-in font | `^FO<x>,<y>` / `^A<font_code><orientation>,<font_height>,<font_width>` / `^FD<text>^FS` |
| Text in a block | as above, with `^FB<width>,<lines>,<spacing>,<justification>,<indent>` between the font and the data |
| Text, downloaded font | `^FO<x>,<y>` / `^A@<orientation>,<font_height>,<font_width>,E:<NAME>.TTF` / `^FD<text>^FS` |
| Frame | `^FO<x>,<y>` / `^GB<width>,<height>,<thickness>[,<colour>[,<rounding>]]` / `^FS` — the colour and rounding are written only when they are not `B` and `0` |
| Barcode | `^FO<x>,<y>` / `^BY<module_width>` / (`^A…` if one was set) / `^BC<orientation>,<height><options>` / `^FD<value>^FS` |
| Image | `^FO<x>,<y>` / `^FXDESIGNER_PREVIEW:<base64 JPEG>` / `^FXDESIGNER_PATH:<path>` / `^GFA,<bytes>,<bytes>,<bytes_per_row>,<hex>` / `^FS` |

Each command is on its own line. `^BY` must be emitted: without it the printer
uses its own default module width of 2, which pins the barcode's physical size
to the head resolution and makes it the one element that cannot be rescaled.

**Graphic encoding** (`^GFA`): one bit per dot, rows padded to whole bytes,
`bytes_per_row = ceil(width / 8)`, data as uppercase hex. **A set bit is
black** — the inverse of the usual 1-bit image convention, where 0 is black.
Padding bits at the end of a row are white (0).

### 8.2 Designer metadata

Four `^FX` comment keys, which printers ignore:

| Key | Payload | Purpose |
|---|---|---|
| `^FXDESIGNER_DPI:` | integer | The resolution the label was drawn for (§11) |
| `^FXDESIGNER_PREVIEW:` | base64 JPEG | The image at original quality, so a reopened file need not be rebuilt from the 1-bit data |
| `^FXDESIGNER_PATH:` | plain filesystem path | Where the image came from |
| `^FXDESIGNER_NOPRINT:` | base64 of a whole element block | An element kept in the design but not printed |

**`^FX` comments end at the next caret, not at the end of the line.** Any
payload that could contain a caret must therefore be base64 encoded — otherwise
a "hidden" element's own `^FO` / `^FD` would resume executing and print anyway.
This applies to the preview and no-print payloads. The DPI value and the image
path are caret-free and are stored as-is.

### 8.3 What is read

`^PW`, `^LL`, `^FO`, `^FT`, `^A` in every form (`^A0`, `^AF`, any bitmap font,
`^A@`), `^CF`, `^FB`, `^GB`, `^BC`, `^BY`, `^GFA`, and the four metadata keys.

**Every parameter of a command is optional, and an omitted one is not an
absent one.** A pattern that requires all of them either replaces what was
given or drops the field, and does both silently, since the command itself is
one the model holds:

| Written | Means |
|---|---|
| `^A0N,40` | height 40, and a scalable font with no width is proportional - which this model spells as a width equal to the height |
| `^AFN,18` | height 18, width from `^CF`, because a bitmap font is not proportional |
| `^A0N` | both sizes from `^CF` |
| `^GB300` | a 300 x 1 rule: `w` and `h` both default to the thickness |
| `^GB300,0,4` | a 300 x 4 rule: `w` and `h` are also **clamped up** to the thickness, so neither can be thinner than the border drawing it |

`^A0N,40` came back as `^A0N,36,20`, losing the height it did give, while the
preview - which required nothing - drew it at 40. `^GB300` and `^GB,,4` were
dropped outright, and a rule survived only as a box with a zero side, which
the canvas then drew as nothing at all.

**`^CF` is the default font, and a field without an `^A` is not a field without
a font.** `^CF<f>,<h>,<w>` sets the font every later field prints in unless it
names its own, and each of its three parameters keeps its previous value when
omitted. With no `^CF` anywhere the default is ZPL's own: font `A` at 9 × 5
dots. Requiring an explicit `^A` before building a text element does not
degrade such a field — it **discards** it, and the element is gone from the
canvas, the preview and the next save.

The default applies to text only. A barcode that named no font of its own must
go on naming none, or a file that had no `^A` before its `^BC` grows one.

**`^FT` places a field from its baseline, and `^FO` from its top.** `^FT`
opens a field exactly as `^FO` does; ignoring it does not misplace such a field
but drops it, so a label written by a tool that typesets its text opens
completely empty. Its `y` is the baseline of the first line for text, and the
bottom-left corner of everything else.

The gap between that point and the element's top is **kept on the element**, and
a save writes the `^FT` back. Normalising it to an `^FO` would be simpler, but
where a baseline sits inside a character cell is measured from the font file and
is only an estimate of what the printer will do - and normalising bakes that
estimate into the file every time such a label is opened and saved.

**A symbology that cannot be drawn is dropped, not redrawn as text.** `^B3`,
`^BQ`, `^BX` and the rest reached the text branch, so a Code 39 sixty dots tall
arrived as nine-dot text holding the barcode's data, and saved that way. The
label gaining something that was never in it is worse than losing the barcode,
which the load warning names either way.

**Read the source as commands, not as lines.** A ZPL command is a caret (or
tilde) plus exactly two characters, and its parameters run to the next caret -
wherever the newlines happen to fall. Real ZPL routinely puts several commands
on one line (`^FO45,50^BY3`), and a port that scans line by line will silently
lose every command that does not start one. Two characters is also what makes
the font family fall out for free: `^A0`, `^AF` and `^A@` are one command whose
second character is the font.

A `^FO` opens a field and `^FS` closes it; everything between is gathered, and
the element type is decided once the whole field has been read rather than at
the first command that looks decisive - otherwise a `^FB` sitting between the
font and the data loses the element.

Parsing is deliberately tolerant: an unrecognised command is skipped rather
than treated as an error, and missing parameters fall back to the defaults in
§3.3. Because a save rebuilds the file from the model, anything skipped is
gone once the user saves, so on load the application lists the print-affecting
commands it could not model.

Before parsing, `^FXDESIGNER_NOPRINT` payloads are decoded and expanded back
into the line stream in place, preceded by a marker, so hidden elements keep
their z-order position.

**Restoring an image**, in order of preference:

1. the original file, if `^FXDESIGNER_PATH` still exists on disk
2. the embedded JPEG preview
3. decoding the 1-bit `^GFA` data (the only option for ZPL from other tools)

---

## 9. Printing

**File → Print**:

1. Check the label's fonts against the printer (§10.3). If the user cancels,
   stop and report "Printing cancelled".
2. Open a TCP connection to the configured address and port (10 s timeout). On
   failure, show the error and stop.
3. Send the document's ZPL as UTF-8 bytes and close the connection.

Elements with `print_enabled` false are sent as `^FXDESIGNER_NOPRINT` comments
rather than as fields, so the printer ignores them.

### Printer wire formats

| Purpose | Payload | Reply |
|---|---|---|
| Print | the ZPL document | none |
| List fonts | `^XA^HWE:*.TTF^XZ` | object names, parsed as `<name>.TTF` (up to 8 chars of `A-Z 0-9 _ -`) |
| Upload font | `~DYE:<NAME>,A,TT,<size>,<size>,` followed by the raw font file bytes | none |
| Delete font | `^XA^ID E:<NAME>.TTF^FS^XZ` (no space) | none |
| Query resolution | `~HI` | model, firmware and head resolution in dots per mm |

Reads use a 5 s timeout to first data, then a short 0.5 s timeout between
chunks, since a printer that has started answering sends the rest promptly.

**A printer that does not answer must be distinguishable from a printer that
answers "nothing".** An empty font list means the printer has no fonts; no
reply at all means it could not be asked, and the two lead to different
prompts. The same applies to the resolution query, where no answer must leave
the user's manual setting alone rather than substituting a guess.

---

## 10. Fonts

### 10.1 Which fonts can be used

Only installed **TrueType** (`.ttf`) families. OpenType, Type 1 and TrueType
collections are excluded deliberately: they cannot be uploaded to the printer,
and offering them would produce labels that print in a substitute face. Where a
family ships several faces, prefer the one styled regular / book / roman /
normal.

Font lookup by family name must be exact. Do not fall back to the platform's
"closest match" service, which always returns something and would silently
substitute a different font for a name that is not installed.

### 10.2 Printer object names

A font stored on the printer is `E:<NAME>.TTF` where `<NAME>` is derived from
the font's filename: uppercased, non-ASCII dropped, every character outside
`[A-Z0-9_-]` removed, truncated to **8 characters**. Empty results become
`FONT`. Any character outside that set would corrupt the `~DY` header and every
`^A@` reference — a space in a family like "Catrina Demo" is the common case.

Truncation makes collisions easy (`DejaVuSans` and `DejaVuSans-Bold` both give
`DEJAVUSA`), so a name already in use within the same label gets a numeric
suffix instead of overwriting.

A saved `.zpl` records only the object name, never the font file. On load, the
name is mapped back to an installed `.ttf` by deriving each candidate's object
name and comparing. Because truncation is lossy, several faces can match;
prefer the family's canonical face, then the shortest filename, so the base
face wins over Bold/Italic. A label saved with a bold face may therefore reopen
in the regular face of the same family — what prints is unaffected, since the
printer only has the one object.

### 10.3 The pre-print check

Fonts are recorded when chosen and uploaded only at print time, so picking a
font never blocks on the network.

Before printing, collect the object names the label uses. If none (built-in
fonts only), print. Otherwise ask the printer what it has and compare:

| Situation | Prompt | Buttons |
|---|---|---|
| Printer did not answer | "The printer could not be asked which fonts it has." | Cancel (default), Print Anyway |
| Fonts missing, source files known | Lists the missing `E:NAME.TTF` objects | Cancel, Print Anyway, **Upload & Print** (default) |
| Fonts missing, source unknown (loaded from a `.zpl`) | Same, each marked "(source file unknown)" | Cancel, Print Anyway |
| Nothing missing | — | prints |

"Upload & Print" uploads each font it can, then prints; a failed upload aborts.

### 10.4 Printer font manager

Lists the font objects on the printer, with Upload… (choose an installed
family), Delete (the selected object) and Refresh. When the printer is
unreachable it says so and disables Delete rather than showing an empty list as
if the printer had no fonts.

---

## 11. Print resolution

The printer's resolution is a persisted setting (203, 300 or 600 dpi, default
203), and can be detected: the Test Connection button opens the socket and then
asks the printer, mapping the reported dots per mm (6, 8, 12, 24) to dpi (152,
203, 300, 600) and filling the field in. A printer that does not answer, or
reports a resolution the application does not support, leaves the manual
setting untouched.

Label size is entered in **inches** and converted to dots with the current
resolution.

When a file is opened whose recorded `^FXDESIGNER_DPI` differs from the
printer's setting, offer three choices:

| Choice | Result |
|---|---|
| Rescale (default) | Multiply the whole design by `printer_dpi / file_dpi`, preserving physical size |
| Keep Dots | Leave the dots alone; the label prints at a different physical size, which the prompt states in inches |
| Cancel | Leave the dots alone |

Rescaling multiplies positions, sizes, label dimensions, font height and width,
frame thickness and barcode module width, rounding to whole dots; text widths
are then recomputed from font metrics rather than scaled, and images re-dither
from their source at the new size.

**A file with no recorded resolution is assumed to be 203 dpi**, not the
printer's current setting. Adopting the printer's setting would stamp a guess
into the file on the next save — permanently mislabelling a 203 dpi label as
whatever printer happened to open it. When the assumption differs from the
printer, the prompt must say the resolution was assumed rather than read.

**Barcodes cannot rescale exactly.** Module width is a whole number of dots, so
a module of 2 becomes 3 rather than 2.96 going from 203 to 300 dpi — a width
error of up to half a dot per module. Positions and heights scale exactly.

---

## 12. Undo and redo

A single linear history of document snapshots. A snapshot holds the label size,
every element with all its properties, and which element is selected.

- **One entry per user action.** A drag or a resize is one entry, recorded when
  the mouse is released — not one per motion event.
- Every document change is undoable: adding, deleting, moving, resizing,
  reordering, editing an element through its dialog, toggling Print This
  Element, and changing the label size (including the element clamping that a
  smaller label causes).
- Performing a new action after undoing discards the redo branch.
- History is capped at 50 entries, oldest discarded.
- Loading a file clears the history — undo never crosses a file boundary.
- Undo and redo both mark the document modified.
- Undo and redo controls are disabled when their stack is empty.
- **Undo, redo and loading a file close any open element editor** (§7).
  Restoring a snapshot replaces every element object, so an editor left open
  would hold one the document no longer has; accepting it would write the edit
  into that detached copy, where it would be lost with no error to show for it.
  Deleting an element closes the editor open on it for the same reason.

Changes that are *not* part of the document — printer address, port, resolution
— are not undoable.

---

## 13. Persisted settings

An INI file at the platform's user config directory, `linuxzpl/settings.ini`:

```ini
[printer]
address = 192.168.50.21
port = 9100
dpi = 203
```

A missing or corrupt file must never block startup; fall back to the defaults
above.

---

## 14. Keyboard shortcuts

| Shortcut | Action |
|---|---|
| Ctrl+O | Open |
| Ctrl+S | Save |
| Ctrl+Shift+S | Save As |
| Ctrl+P | Print |
| Ctrl+Q | Quit |
| Ctrl+Z | Undo |
| Ctrl+Shift+Z, Ctrl+Y | Redo |
| Delete | Delete selected element |
| Ctrl+] | Bring Forward |
| Ctrl+Shift+] | Bring to Front |
| Ctrl+[ | Send Backward |
| Ctrl+Shift+[ | Send to Back |
| Ctrl++, Ctrl+= | Zoom In |
| Ctrl+- | Zoom Out |
| Ctrl+0 | Fit Label |
| Ctrl+9 | Fit Width |
| Ctrl+1 | Actual Size (1:1) |

Three notes for a port:

- Shortcuts are global to the window, not only active while a menu is open —
  which is why Page Up / Home were avoided for the z-order actions: they would
  be taken away from scrolling the canvas.
- A shortcut is subject to the same enable/disable rules as its menu item. Ctrl+Y
  does nothing when there is nothing to redo, and Delete does nothing with no
  selection; neither is an error.
- On toolkits that report the *shifted* key symbol, `Ctrl+Shift+]` arrives as
  `}` and will not match a binding declared on `]`; both forms may need
  registering. `Ctrl++` has the same problem from the other side — it needs
  Shift on most layouts — so `Ctrl+=` is registered alongside it.

---

## 15. Status and errors

A status bar reports the last significant action: `Ready`, `Loaded: <file>`,
`Saved: <file>`, `Save failed`, `Error loading file`, `Undo`, `Redo`,
`Printing cancelled`, `Rescaled from <old> to <new> dpi`,
`Label size set to <w>x<h>`, `Printer set to <address>:<port>`, and progress
while uploading a font.

Failures are reported in a modal error dialog with the actual underlying
message — never a swallowed exception or a placeholder.

---

## 16. Reference: defaults and limits

| | Value |
|---|---|
| Default label | 4 × 6 inches at the configured dpi |
| Default printer | `192.168.50.21:9100`, 203 dpi |
| Supported resolutions | 203, 300, 600 dpi |
| Text | 36 dot height, 20 dot width, `"New Text"` |
| Text dialog limits | font height and width 8–500 dots |
| Frame | 200 × 150 dots, 2 dot thickness |
| Frame dialog limits | width 10–800, height 10–1200, thickness 1 to `min(w,h)/2` |
| Barcode | Code 128, `"123456789"`, 100 dot bar height, module width 2, value printed below |
| Barcode dialog limits | bar height 20–300 dots, module width 1–20, interpretation line height 6–200 |
| Image | 200 × 200 dots, JPEG/PNG source |
| Minimum element size when resizing | 20 × 20 dots |
| Resize handle size and hit radius | 8 **screen pixels** — `8 / scale` dots, so it neither shrinks out of reach when zoomed out nor covers the element when zoomed in |
| Zoom range | 5% to 800%, along a fixed ladder of steps |
| Window opening size | the monitor's work area × 0.9, capped at 1200 × 900, minimum 480 × 360 |
| Double-click interval | 500 ms |
| Undo depth | 50 |
| Printer font object name | 8 characters, `[A-Z0-9_-]`, stored on `E:` |
| Print timeout | 10 s |
| Query timeout | 5 s, then 0.5 s between chunks |
| Font upload timeout | 30 s |

---

## 17. Platform services a port must supply

| Service | Used for | Notes |
|---|---|---|
| Font enumeration | Listing installed TrueType families with their file paths | Must give exact family → file mapping, not fuzzy matching |
| Font metrics | Measuring string advance width at a given em size | Required for correct text width; a port without it cannot honour §3.3 |
| Font rasterising | Drawing text on the canvas in the chosen face | |
| Image decoding and resampling | JPEG/PNG loading, high-quality resize | |
| Dithering | Floyd–Steinberg to 1-bit | Must match what is sent to the printer, or the canvas stops being a proof |
| JPEG encoding | The embedded preview | |
| Vector drawing | Canvas rendering with a uniform scale transform, translucency and alpha compositing | |
| TCP sockets | All printer communication | Plain sockets; no printing subsystem is involved |
| Registering a font at runtime | So a chosen font can be drawn before it is installed anywhere | Optional; without it the canvas may fall back to a default face |

---

## 18. Known deviations

Behaviours in the current implementation that a port should treat as decisions
rather than requirements:

- **Canvas text is truncated to the first 20 characters for display**, while
  the element box and the printed output use the whole string. A longer text
  element therefore shows less on screen than it prints. Text in a block is
  drawn whole, wrapped, whether or not a font file is available.
- **Code 128 only.** No other symbology is offered, and the value is not
  validated against the subset.
- **Modes `U` and `D` are carried but not simulated.** Only `A` changes the
  symbol; UCC case mode and UCC/EAN mode round-trip and can be chosen, but the
  canvas draws them as `N`. The same goes for `>` FNC1 escapes in `^FD`.
- **The exact subset-switching threshold is inferred.** Zebra does not publish
  where mode A moves into subset C; the rule above is the conservative reading,
  and a printer would settle it.
- **The interpretation line's leading is assumed** to be the font height plus
  two dots. ZPL does not document its own spacing.
- **`^FB`'s indent is applied to every line**, where ZPL hangs it on the second
  and later ones. The parameter round-trips; only where it lands differs.
