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

At startup the label is the size last chosen in Label Settings — 4 × 6 inches
until one is (§13) — at the configured printer resolution (4 × 6 inches is
812 × 1218 dots at 203 dpi, 1200 × 1800 at 300).

### 3.2 Properties common to every element

`x`, `y` (top-left corner, dots), `width`, `height` (dots), `element_type`,
`print_enabled` (default true — see §6.6), and `reverse_print` (`^FR`, default
false — a checkbox in the Edit Text, Edit Frame and Edit Barcode dialogs, §7).

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

One element, five symbologies: Code 128 (`^BC`, subsets B and C), Code 39
(`^B3`), EAN-13 (`^BE`), Interleaved 2 of 5 (`^B2`) and a UPC/EAN Extension
add-on (`^BS`). QR, Data Matrix, PDF417 and the rest are still not offered -
see §18.

| Property | Default |
|---|---|
| `symbology` | `code128` - which of the five |
| `barcode_value` | `"123456789"` |
| `bar_height` | 100 dots - the bars themselves |
| `module_width` | 2 dots |
| `ratio` | 3.0 - the wide-to-narrow ratio Code 39 and Interleaved 2 of 5 draw their wide elements at; the other three are fixed-ratio and ignore it |
| `orientation` | none - `N` upright, `R` 90°, `I` 180°, `B` 270° |
| `show_text` | true - whether the value prints as an interpretation line |
| `text_above` | false for every symbology but the UPC/EAN extension, where it is true - the line goes above the bars instead of below |
| `check_digit` | false - append a check digit (Code 128's UCC/EAN one, Code 39's own Mod-43, or Interleaved 2 of 5's Mod-10); EAN-13 and the UPC/EAN extension have no such flag at all, because EAN-13's own check digit is never optional and the extension has none |
| `mode` | `N` - `A` lets Code 128 use subset C; the other four symbologies have no mode |
| `font` | none - the `^A` before the barcode command, which sets the interpretation line's font |

**`width` and `height` are the footprint, not the bars.** The element box is
the bars plus the interpretation line, transposed when the barcode is rotated:

```
run   = sum(module widths) × module_width
stack = bar_height + interpretation line height
box   = (run, stack) upright,  (stack, run) rotated
```

Because a quarter turn leaves the box axis-aligned, rotation needs nothing from
hit-testing, dragging or the resize handles - they only ever see the box.

**Derive the width from the symbol, not from the character count.** Code
128's subset C packs two digits into one symbol, so `(35 + n×11) × module_width`
is wrong by nearly half for a numeric value in mode A - and no formula at all
covers Interleaved 2 of 5's checksum digit or EAN-13 and the extension's own
fixed lengths. Summing each symbology's own encoded module widths is right for
all of them; only Code 128's happens to have a closed form as well.

Mode `A` is Code 128's automatic subset switching: move into subset C across a
run of four or more digits (or two, when the whole value is numeric and the
start code is free), and back to B for anything else. Switching for a shorter
run costs more than it saves.

The interpretation line prints the encoded value - including a check digit
when there is one, and EAN-13's own thirteenth digit or the extension's fitted
length always - in the font the `^A` selected, at that font's dot height. A
barcode whose line is switched on and whose file named no font is given one, so
what prints is stated rather than inherited from the printer's `^CF`.

**EAN-13 and the UPC/EAN extension fit the value to a fixed length rather than
validating it.** EAN-13 takes the last 12 digits (padding on the left with
zeros if there are fewer) and appends its own check digit; the extension does
the same to 2 digits if that many or fewer were given, or to 5 otherwise.
Interleaved 2 of 5 similarly gets a leading zero if, after any check digit, its
own digit count is odd - two digits share every symbol, so an odd count cannot
be interleaved at all.

Resizing a barcode is a request for a module width and a bar height, not for an
arbitrary rectangle: the drag sets those two and the box snaps back to what
they produce, so the symbol is never stretched to fill.

**Code 128's `width` has a closed form**: `(35 + len(value) × 11) × module_width`.
The constant 35 is the start, check and stop modules; each data character is
11 modules. It holds only for Code 128 subset B - see above.

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

**Title.** The titlebar names the file being edited - its basename, not its
path - and `LinuxZPL` when no file is open. It is set when the window opens and
again wherever the current file changes: New, Open, and a save that adopts a new
path. A failed save leaves it naming the file still being edited. Neither the
toolkit nor any other name for the program appears in it, in either frontend;
where the titlebar is a header bar the window's own title is set to match, so
the task switcher says the same thing.

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
- **Shift-click or Ctrl-click** adds the element under the pointer to the
  selection, or takes it out again if it is already in. A plain click on an
  element that is already selected keeps the whole selection, so a group can be
  picked up by any of its members; a click on empty canvas is what reduces a
  group back to nothing.
- **Dragging on empty canvas** draws a rubber band, and everything its rectangle
  **overlaps** is selected when the button comes up — overlapping, not
  containing, so an element running to the edge of the label can still be caught.
  Holding Shift or Ctrl adds the catch to the selection instead of replacing it.
  A band changes only the selection, never the document, so it is not undoable.
- The selection is **ordered by when each element was picked**. Its last member
  is the *primary*: the one that carries the resize handles and the one the
  z-order commands move.
- **Drag** moves the selection. A single element is clamped so it stays inside
  the label: `0 ≤ x ≤ label_width − width`, likewise for y. A group moves by one
  shared delta, clamped against the group's own bounding box — clamping each
  element separately would let the ones still inside carry on while the one
  against the edge stopped, and the group would come apart.
- **Eight resize handles** on a selection of exactly one — four corners, four
  edge midpoints — drawn as small filled squares. A group gets none: there is no
  single box to resize, and a handle on each member would offer a drag with
  nowhere to go. A handle is **8 screen pixels**, drawn and hit-tested at
  `8 / scale` dots, so it is the same size to the pointer at every zoom. A handle
  is hit if the pointer is within that of its centre, and where two are in reach
  the **nearer one wins**. That radius is wider than the drawn square on purpose,
  so zoomed out the handles of a short element overlap — and a text element is
  short by nature, its height being its font height. Answering with the first
  handle in order would then hand back one the pointer is further from, and the
  user who grabbed the bottom edge would watch the side move. While hovering one,
  the pointer changes to the matching directional resize cursor (`nw-resize`,
  `n-resize`, `ne-resize`, `w-resize`, `e-resize`, `sw-resize`, `s-resize`,
  `se-resize`).
- **A resize is measured from the press**, not from the previous motion event.
  The rules below do not store the rectangle they are given: they read a font
  width, a line count or a module width out of it and snap the box back to what
  that will print. Against the previous event that snap eats the drag — every
  motion smaller than one unit of the derived property is computed, snapped away
  and forgotten, so a slow drag moves nothing while a fast one jumps. Against the
  press the same snap is harmless, because the next event starts from the box the
  drag began with. A move is the opposite: it has nothing to snap back to and
  carries on from wherever the pointer is now.
- **A box that snaps back grows from the edge the drag left alone.** The derived
  size is rarely the dragged one, so a top or left handle anchors the opposite
  edge of the box as it was at the press. Growing from the dragged corner instead
  walks the element up or along the label, a step per motion event.
- Resizing enforces a **minimum of 20 × 20 dots** and clamps the element to the
  label bounds — origin first, then the size against the room left beyond it, so
  a box dragged larger than the label is cut down rather than pushed off the left
  edge — and then applies per-type rules:
  - frame: thickness clamped to `min(width, height) / 2`
  - text: `font_height` is set to the new height, `font_width` is solved so the
    text prints at the new width, and the box is then snapped to that printed
    width — the outline the user drags is the outline that prints. At a quarter
    turn the box is transposed, so the font height comes from the side across the
    text and the font width is solved along it, exactly as a rotated barcode
    takes its module width from its run
- **Double click** (same element, within 500 ms) opens that element's edit
  dialog. A modified click is a selection gesture and never a double click.
- **Right click** selects the element under the pointer and opens a context menu
  (§6.6). If that element is part of a group the rest of the group is kept, and
  the element becomes the primary — so the z-order commands in the menu act on
  the element that was actually pointed at.
- **Delete** removes every selected element, not only the primary.

---

## 6. Commands

### 6.1 File

| Command | Behaviour |
|---|---|
| **New** | Prompts about unsaved changes (§6.7), then a blank label of the remembered size (§13) at the printer's resolution. Clears the elements, the undo history and the current file, and resets the status to `Ready`. |
| **Open…** | Prompts about unsaved changes (§6.7), then a file chooser filtered to `*.zpl`. The chooser previews the selected `.zpl` by rendering it to an image, scaled to at most 300 px wide. Opening replaces the whole document and resets the undo history. |
| **Save** | Writes to the current path, or behaves as Save As if there is none. |
| **Save as…** | File chooser, default name `untitled.zpl`. A name typed with no extension gets `.zpl`; one that already has an extension is left alone. Confirms before overwriting an existing file, and declining returns to the chooser. Adopts the chosen path as the current file. |
| **Print** | §9. |
| **Printer Settings ▸ Set Printer for This Session…** | §9. A submenu rather than a flat item, so further printer-related actions can join it later. |
| **Quit** | Prompts about unsaved changes (§6.7). |

**The menu is in four groups**, separated in this order: start a document
(New, Open), persist it (Save, Save as), print it (Print, Printer Settings),
leave. A port that runs them together is the thing this grouping exists to
avoid.

Saving refuses an empty document ("No content to save" — a document whose ZPL
is empty or just `^XA` / `^XZ`). Save clears the modified flag; a failed save
must report the real error and leave the flag set.

### 6.2 Edit

Undo, Redo, Delete, then Bring to Front / Bring Forward / Send Backward / Send
to Back, then an **Align** submenu. Delete and the four z-order items are
disabled when nothing is selected; the raise pair is disabled when the selection
is already on top and the lower pair when it is already at the bottom.
Sensitivity is re-evaluated each time the menu opens.

The z-order commands move the **primary** element only, even while a group is
selected: what "bring forward" should mean for three elements at different
depths is a question of its own, and answering it badly is worse than leaving it.

**Align** holds six commands, in this order: Align Left, Centre Horizontally,
Align Right, Align Top, Centre Vertically, Align Bottom. Each moves one axis and
leaves the other alone, and all six are disabled when nothing is selected.

| Selection | What it lines up against |
|---|---|
| Two or more elements | The selection's own bounding box — Align Left takes every member to the leftmost x in the group, Centre Horizontally puts every member's centre on the group's centre |
| Exactly one element | The label, which is the only other thing there is to line it up with — Align Left is `x = 0`, Centre Horizontally is `(label_width − width) / 2`, Align Right is `label_width − width` |

Results are clamped into the label the way a drag is, so an element larger than
the label lands against the edge rather than at a negative coordinate. An align
that moves nothing records no undo entry. Nothing else about an element changes:
a field placed by `^FT` is written back as `^FT` at its new position, and a
rotated element aligns by its footprint, which is axis-aligned at every quarter
turn (§3.2).

The align commands have **no keyboard shortcuts**: six more window-wide bindings
would be six more keys taken away from the canvas (§14).

### 6.3 View

Zoom In, Zoom Out, then Fit Label, Fit Width, Actual Size — separated into
those two groups. §5 describes what each does to the scale.

### 6.4 Settings

| Command | Behaviour |
|---|---|
| **Label Size** | §7 |
| **Default Printer** | §7 |

### 6.5 Toolbar

`+ Text`, `+ Frame`, `+ Barcode` add an element with the defaults from §3.3.
`+ Image` opens a file chooser (JPEG/PNG) first. Each new element is placed at a
staggered offset so successive additions do not stack exactly, and becomes the
selection. `Delete` removes the selected elements.

`Align ▾` opens the same six commands the Edit menu holds, under the same enable
rules, re-evaluated as the popup opens — the popup can be reached without the
Edit menu ever having been shown. One button rather than six: the toolbar is
text-labelled, and the icon theme has no object-align icons to label six with.

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
- **Label Settings carries the label home and the three presentation flags** —
  `^LH`'s x and y because preprinted stock is a design decision, and `^PO`,
  `^PM` and `^LR` because they change the whole label. `^LS` and `^LT`
  round-trip without being exposed: one is a dead Z-130 compatibility shim and
  the other is printer calibration, and neither is a thing to design with.
- **Edit Text and Edit Barcode both carry the same three `^FN` rows** — a tick
  for "data comes from a numbered field", the number, and the field name — built
  from one shared helper per frontend so the two editors cannot offer them
  differently. A tick rather than a number meaning "none", because 0 is a field
  number ZPL allows. The box is re-measured after they are applied, since what
  the canvas draws changes with them (§8.3).

The remaining dialogs — Label Size, Default Printer, Printer Settings, the
file choosers and the prompts — are modal.

| Dialog | Fields | Range / notes |
|---|---|---|
| **Edit Text** | Text (multi-line); Font Height; Font Width; Orientation; Reverse; Font (Choose… / Clear); Wrap; Wrap Width; Max Lines; Line Spacing; Justification; Indent | Heights and widths 8–500 dots. Choose… lists installed TrueType families only; Clear reverts to the document default, shown as "Default (family)". The six wrap fields are the `^FB` block (§3.3): 10–2000 dots, 1–64 lines, −100–100 spacing, 0–2000 indent, and the justification list of §3.3. All but the checkbox are insensitive while Wrap is clear; Wrap Width starts at the width the text already prints at. Reverse is `^FR` (§3.2), a checkbox shared in name and effect across all three of these dialogs. |
| **Edit Frame** | Width; Height; Thickness; Colour; Corner Rounding; Reverse | 10–800, 10–1200, and 1 to `min(width, height) / 2` — the thickness maximum updates live as the size fields change. Colour is `^GB`'s `B`/`W`, rounding its 0–8 (§3.3). Reverse (`^FR`) flips Colour's effect a second time (§18). |
| **Edit Barcode** | Symbology; Value; Bar Height; Module Width; Ratio; Orientation; Value Text; Text Height; Check Digit; Mode; Reverse | Bar height 20–300 dots, module width 1–20, text height 6–200, ratio 2.0–3.0 in tenths. Symbology is the five choices of §3.3; the rest are that symbology's own parameters, and Ratio, Check Digit and Mode are shown only for the symbologies that have one — Check Digit's own label changes with it. Width is derived from the symbol, never entered. |
| **Edit Image** | file chooser | Replaces the source file, keeping position and size |
| **Label Size** | Presets 4×6, 5×7, 6×4, 3×5, 2×3; custom Width and Height **in inches**; DPI | 0.5–25 inches, two decimals, stepping by a tenth. DPI is the same 203 / 300 / 600 choice as Default Printer and writes the same one setting; changing it here runs §11's prompt. A live hint shows the resulting dots at the **chosen** resolution and the `^PW` / `^LL` values — changing the resolution holds the inches fixed and recomputes the dots. Shrinking clamps elements to the new bounds. The accepted size is remembered (§13). |
| **Default Printer** | Address; Port; DPI; Test Connection | Port 1–65535. DPI is a choice of 203 / 300 / 600. Test Connection opens the socket and then asks the printer its resolution, filling the DPI field in (§11). Accepting persists all three (§13). |
| **Printer Settings** | Address; Port; DPI; Test Connection; Use Default | Same fields and ranges as Default Printer, pre-filled with whichever printer is currently in effect. Use Default re-fills the fields from the persisted default printer, for comparing against or reverting to it. Accepting changes only which printer `Print` uses for the rest of this session (§9) — it never writes to settings.ini (§13). |

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
| Barcode | `^FO<x>,<y>` / `^BY<module_width>[,<ratio>]` / (`^A…` if one was set) / `^BC<orientation>,<height><options>` / `^FD<value>^FS` — or `^B3`, `^BE`, `^B2`, `^BS` for the other four symbologies, each in its own parameter order (§3.3) |
| Image | `^FO<x>,<y>` / `^FXDESIGNER_PREVIEW:<base64 JPEG>` / `^FXDESIGNER_PATH:<path>` / `^GFA,<bytes>,<bytes>,<bytes_per_row>,<hex>` / `^FS` |

Each command is on its own line. `^BY` must be emitted: without it the printer
uses its own default module width of 2, which pins the barcode's physical size
to the head resolution and makes it the one element that cannot be rescaled.
Its ratio is written too, for Code 39 and Interleaved 2 of 5, when it is not
the default 3.0.

`^PQ<quantity>[,<pause count>,<replicates>,<override pause>]` is written last,
immediately before `^XZ`, and only when at least one of its four values is not
ZPL's own default (`1,0,0,N`) — trimmed to however many of them that takes, so
a quantity-only label writes just `^PQ5` rather than `^PQ5,0,0,N`.

**Graphic encoding** (`^GFA`): one bit per dot, rows padded to whole bytes,
`bytes_per_row = ceil(width / 8)`, data as uppercase hex. **A set bit is
black** — the inverse of the usual 1-bit image convention, where 0 is black.
Padding bits at the end of a row are white (0).

**Written uncompressed, read in four encodings.** Plain hex is what this
designer writes; almost nothing else does, because a 5 KB logo is 100 KB of it.
A port must *read* all of:

| Encoding | Form |
|---|---|
| plain hex | the digits, as written above |
| `:B64:` | `:B64:<base64 of the bytes>:<crc>` |
| `:Z64:` | `:Z64:<base64 of the zlib-deflated bytes>:<crc>` |
| ASCII run-length | the shorthands below, expanded into the hex stream |

The run-length shorthands, which is why expanding them needs the row width:
`G`–`Y` repeat the next hex digit 1–19 times; `g`–`z` repeat it 20–400 times in
steps of 20; a lowercase followed by an uppercase adds the two, so `hK` is 45;
`,` fills the rest of the row with white and `!` with black; `:` repeats the row
above.

The trailing CRC is read past rather than checked — Zebra does not publish which
CRC-16 variant it is, and rejecting a valid label over a guessed initial value
would be worse than not checking. `:Z64:` carries zlib's own checksum anyway.

**Take the row count from the decoded data**, `len(bytes) / bytes_per_row`, not
from either header count: `^GFa,b,c,d` has two, and once the data is compressed
generators disagree about which is the transmitted length and which the
uncompressed total.

`^GFB` and `^GFC` are binary. A designer that reads its files as text cannot
recover those bytes, so they are **reported as unsupported** — as is a `^GFA`
whose data will not decode. Failing silently costs the label an image and says
nothing.

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
`^A@`), `^CF`, `^FB`, `^GB`, `^BC`, `^BY`, `^GFA` in every encoding of §8.1,
the stored-format family (`^DF`, `^XF`, `^FN`, `^FV`), the stored-graphic
family (`^IM`, `^XG`, `^IL`, `^IS`), the label transforms (`^LH`, `^LS`, `^LT`,
`^PO`, `^PM`, `^LR`), `^PQ`, and the four metadata keys.

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
| `^BY3` | module width 3, keeping the ratio and height the last `^BY` set |
| `^BY3,3.0,150` | and a `^BC` that gives no height of its own is 150 dots tall |

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

**`^BY` is the same kind of command for barcodes, and it is read wherever it
appears.** `^BYw,r,h` sets the module width, the wide-to-narrow ratio and the
bar height that every later barcode inherits, and it stays in effect until
another `^BY` replaces it. Each of its three parameters keeps its previous value
when omitted.

Reading it only inside an open field — below the point that needs a `^FO` —
dropped every `^BY` written at the top of a format, which is where the manual's
own examples put it and where most generators emit it. The barcode came back at
the power-up module width of 2 and **printed at half the width the file asked
for** (404 dots to 202), and a save wrote that back. Nothing was said either,
because `^BY` is a command the model holds.

The ratio is carried but not modelled for Code 128, EAN-13 and the UPC/EAN
extension: ZPL states it has no effect on fixed-ratio symbologies, and all
three are, so it changes nothing this designer draws for them. Code 39 and
Interleaved 2 of 5 are not fixed-ratio, and it does change their own wide
elements' width (§3.3). Either way it round-trips so that a file which gave
one does not lose it. `^BY`'s `h` is read but never written, because the
height always goes on the barcode command itself and there is nowhere for the
two to disagree.

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

**A symbology that cannot be drawn is dropped, not redrawn as text.** `^BQ`,
`^BX` and the rest still reach the text branch's own trap otherwise, so a QR
code sixty dots tall would arrive as nine-dot text holding its data, and save
that way. The label gaining something that was never in it is worse than
losing the barcode, which the load warning names either way. `^B3`, `^BE`,
`^B2` and `^BS` used to be dropped the same way; they are real symbologies now
(§3.3) and reach the barcode branch instead.

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
3. decoding the 1-bit `^GF` data, in any of the encodings of §8.1 (the only
   option for ZPL from other tools)

---

## 9. Printing

**File → Print**:

1. Check the label's fonts against the printer (§10.3). If the user cancels,
   stop and report "Printing cancelled".
2. Open a TCP connection to the configured address and port (10 s timeout). On
   failure, show the error and stop.
3. Send the document's ZPL as UTF-8 bytes and close the connection.

**`^PO`, `^PM` and `^LR` are always sent explicitly in step 3, whatever their
value.** A real printer keeps these three after the job that set them —
`^XA...^XZ` does not reset them — so a label that does not invert, mirror or
reverse-print still sends `^PON`, `^PMN` and `^LRN`, clearing whatever an
earlier job (from this app or elsewhere) left in effect. A saved `.zpl` file
is never sent to a printer and has no such state to correct, so Save keeps
omitting them at ZPL's own default (§8.1).

If the label carries a `^PQ`, it is sent as part of that ZPL like any other
command, and the printer prints that many copies itself — this step does not
loop the send.

Elements with `print_enabled` false are sent as `^FXDESIGNER_NOPRINT` comments
rather than as fields, so the printer ignores them.

**File → Printer Settings ▸ Set Printer for This Session…** changes the
address, port and DPI that step 2 above uses, for every `Print` from then on,
without writing them to settings.ini (§13) — the next launch starts back on
the persisted default. If the DPI changes, it runs the same rescale prompt as
Default Printer (§11), since a label's dot geometry has to stay consistent
with whichever printer will render it.

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

### 10.5 Printer object manager

**Printer → Objects…** lists every object stored on the printer — any of
R:/E:/B:/A:/Z:, any extension — with Store, Retrieve, Delete and Refresh,
same status handling as the font and graphic managers. Where the font and
graphic managers scope their `^HW` request to `E:*.TTF` and `*.GRF`
respectively, this one is unscoped (`d:*.*`) on purpose: the point of this
manager is to surface everything the printer is holding, including objects
neither of the other two recognizes — a saved format, firmware/config
housekeeping, a printer's own WML front-panel menu, anything another tool
put there. `Z:` is queried here but not by Graphics: it is read-only
factory content (a default WML menu, an RFID recipe file), not somewhere a
user's own graphic would ever be stored.

Store and Retrieve both exist here, unlike Upload in the font and graphic
managers, because both have a genuinely generic printer command behind
them. Store sends `CISDFCRC16`, which writes an arbitrary local file to the
printer's `E:` drive verbatim — the only device it supports, so its prompt
asks only for a name and extension, never a device — with CRC and checksum
both sent as `0000`, which the command documents as skipping that field's
validation entirely, rather than risk a wrong implementation of an
under-specified checksum silently failing every upload. Retrieve sends the
`file.type` Set/Get/Do command, which hands back a named object's bytes
verbatim regardless of what kind of object it is, and offers them for
saving to a local file exactly as retrieved, no decoding attempted. Neither
`~DY` nor `~DG` — the font and graphic managers' own Upload — is generic
this way; each is locked to its one format.

Object names round-trip through this manager exactly as the printer gives
them, never forced to upper case the way the font and graphic managers
force theirs: those two only ever create upper-case 8.3 objects, but a
`CISDFCRC16`-stored object is not necessarily one (the command's own manual
examples are lower case), and `file.type` retrieval is case sensitive — a
name normalised the wrong way would silently stop matching the real object.

Delete sends `^ID`, the same extension-agnostic command the font and graphic
managers already use, and asks for confirmation first, same as Graphics —
except for a `Z:` object, where Delete is unavailable: `^ID` does not reach
that device at all and silently ignores a target there, so the control is
withheld rather than let a click report nothing and change nothing.
Deleting an object that `graphic_store`'s local cache also has pixels for
(see §18) clears that cache entry too, so a `^XG`/`^IM`/`^IL` already on the
canvas cannot go on showing pixels for an object the printer no longer has.

---

## 11. Print resolution

The printer's resolution is a persisted setting (203, 300 or 600 dpi, default
203). It is editable from **both** Printer Settings and Label Size, which offer
the same choice and write the same one setting: label size is entered in inches
but stored in dots, so the resolution is half of what the size means, and
having to leave for another dialog to change that half is how a label ends up
the wrong physical size. It can also be detected: the Test Connection button
opens the socket and then
asks the printer, mapping the reported dots per mm (6, 8, 12, 24) to dpi (152,
203, 300, 600) and filling the field in. A printer that does not answer, or
reports a resolution the application does not support, leaves the manual
setting untouched.

Label size is entered in **inches** and converted to dots with the current
resolution.

When a file is opened whose recorded `^FXDESIGNER_DPI` differs from the
printer's setting — or when the resolution is changed under an open design,
from either dialog — offer three choices:

| Choice | Result |
|---|---|
| Rescale (default) | Multiply the whole design by `printer_dpi / file_dpi`, preserving physical size |
| Keep Dots | Leave the dots alone; the label prints at a different physical size, which the prompt states in inches |
| Cancel | Leave the dots alone. It does not abandon the dialog the change came from — the resolution is still adopted and the design still stamped with it |

Rescaling multiplies positions, sizes, label dimensions, font height and width,
frame thickness and barcode module width, rounding to whole dots; text widths
are then recomputed from font metrics rather than scaled, and images re-dither
from their source at the new size.

**A file with no recorded resolution is assumed to be 203 dpi**, not the
printer's current setting. Adopting the printer's setting would stamp a guess
into the file on the next save — permanently mislabelling a 203 dpi label as
whatever printer happened to open it. When the assumption differs from the
printer, the prompt must say the resolution was assumed rather than read.

**From Label Size, the prompt governs the elements only.** The label itself
takes the size typed in that dialog, at the resolution chosen beside it, under
every answer — that size is the whole content of the dialog the user accepted,
so it wins over the one a rescale produced. The resolution is settled first, so
the prompt describes the design on the canvas rather than the one about to
replace it, and is skipped entirely when the resolution did not change.

**A rescale on load is an unsaved change.** Parsing a file is not an edit, but
a rescale is an answer the user gave, and it moves every element away from what
the file holds. Clearing the unsaved flag after it discarded that answer on
close without a word, and because the file went on recording the resolution it
was drawn for, **the same prompt returned on every subsequent open** — there
was no way to make it stop other than noticing that a save was needed. Keeping
the dots leaves the elements exactly as the file has them, so that answer
leaves nothing unsaved.

**A `^FN` field is a variable field, not an empty one.** `^FN#"a"` numbers a
field whose data the printer supplies at print time, optionally naming it with a
prompt in double quotes. In a stored format `^FN` stands where `^FD` would; in
the call that recalls one, `^FN` and `^FD` appear together to supply the data.
Field numbers are **document-scoped and shared** — ZPL's rule is that a field
carrying both `^FN` and `^FD` supplies its data to every other field with the
same number — so the values live on the document rather than on the elements.

Because that data can be declared *after* the field that uses it, the values are
collected in a pass of their own before any element is built. A recall call is
nothing but such declarations.

| Written | Opens as | Saves as |
|---|---|---|
| `^FN1^FS` | a field showing `«FN1»` | `^FN1^FS` |
| `^FN1"Ship to"^FS` | a field showing `«Ship to»` | `^FN1"Ship to"^FS` |
| `^FN1"Ship to"^FDAcme^FS` | a field showing `Acme` | `^FN1"Ship to"^FDAcme^FS` |
| `^FVtext^FS` | a field showing `text` | `^FDtext^FS` |

Requiring `^FD` before building an element discarded **every text field in a
stored format**, and a `^FN` barcode field was handed the value `123456789` — a
string that appears nowhere in the file — by a fallback meant for a barcode the
user has just created. The designer invented label content and then wrote it to
disk. An element's own text holds only a literal the file actually gave; what
the canvas draws is derived, so a prompt can never be written back as data.

**`^DF` is written immediately after `^XA`**, because ZPL stores everything
following it rather than printing it — anything emitted in between would be left
out of the format being saved. **An `^XF` recall call round-trips as data**: the
references and the `^FN`/`^FD` pairs are re-emitted, and nothing is drawn,
because the geometry it fills lives on the printer. Opening one used to empty
the file.

**`^IM`/`^XG`/`^IL`/`^IS` are the graphic counterpart of `^DF`/`^XF`.** `^IS`
saves everything a format has drawn so far as a named image; `^XG` and `^IM`
recall one inside a field, positioned by `^FO` like any other field (`^XG` also
takes a magnification factor, 1 to 10 on each axis; `^IM` is "identical to
^XG... except there are no sizing parameters", so it is always 1,1); `^IL`
recalls one at the very start of a format, always at `^FO0,0`, for the fields
after it to overlay.

Unlike `^DF`/`^XF`, resolution here is real, not only round-tripped:
`zplcore.graphic_store` keeps an in-memory registry, keyed on name and
extension (**not** on the `R:`/`E:`/`B:`/`A:` device prefix — see §18), for as
long as the process runs. Parsing a file that carries `^IS` captures a real
image into that registry; a later `^XG`/`^IM`/`^IL` — in the same file, or a
different one parsed afterwards in the same running app — resolves it back.
The lookup is live, not cached, so an element already on screen updates the
next time it is drawn once some other file's `^IS` fills the name in.

Resolution never changes what a save writes: an `^XG`/`^IM` field always writes
back the command and the name it named, never the resolved pixels — inlining
them would turn a small reference into a large embedded image, and drop the
device path a real printer still needs to look the object up by.

| Written | Opens as | Saves as |
|---|---|---|
| `^FO50,50^XGR:LOGO.GRF,2,2^FS` | a graphic field, magnified 2× if this session has `R:LOGO.GRF`, else a placeholder naming it | `^FO50,50^XGR:LOGO.GRF,2,2^FS` |
| `^FO50,50^IMR:LOGO.GRF^FS` | the same, unmagnified | `^FO50,50^IMR:LOGO.GRF^FS` |
| `^ILR:LOGO.GRF` | the document's `image_load`; drawn as a background at 0,0 if resolved | `^ILR:LOGO.GRF`, right after `^XA` |
| `^ISR:LOGO.GRF,Y^FS` | recorded on the document; captures everything drawn before it into the store | `^ISR:LOGO.GRF,Y^FS`, after the elements it captured |

A `+ Graphic` button creates an `^XG`/`^IM` reference the same way `+ Time` and
`+ Serial` create theirs; double-clicking one opens an editor for its command,
device, name, extension and magnification. `^IL` and `^IS` have no creation
dialog of their own, the same as `^DF`/`^XF` — they round-trip a file that
already carries them rather than being authored from a blank document.

**Printer → Graphics…** talks to the real printer, the same way Settings →
Printer Fonts… already does for fonts — not `graphic_store`'s local memory,
though it keeps that in step as a convenience (see below). Every request
goes to whichever printer is actually in effect this session
(`self.printer_address`/`self.printer_port`, which a session-only "Set
Printer for This Session…" override moves without touching the persisted
default) — never the persisted default itself.

- **View** queries the printer live via `^HW`, one request per device
  (`R:`/`E:`/`B:`/`A:`, since graphics — unlike fonts, always `E:` — can live
  in any of them), scoped to `*.GRF` — the canonical ZPL graphic extension,
  and the one Store writes — the same way `query_printer_fonts` is scoped to
  `E:*.TTF`. An unscoped `*.*` was tried first and rejected: a printer's own
  memory holds plenty that is not a graphic at all — fonts, firmware/config
  objects, whatever else came from the factory or another tool — and it made
  the dialog list all of it. The `.GRF` filter is applied twice, once in the
  request and again on the reply, so a printer model that ignores the
  pattern and answers with its whole directory anyway is still filtered
  correctly. Reachability is judged by the first device queried, the same
  rule `query_printer_fonts` already uses for its one query; a later device
  answering nothing is not treated as the printer going away.
- **Store…** builds a `~DG` payload — the same 1-bit, Floyd-Steinberg
  dithered, hex-encoded bitmap `^GF` fields already carry — from a PNG/JPG
  picked off disk and a chosen device/name/extension, and uploads it for
  real.
- **Retrieve…** fetches an object's real bytes back via `^HG` (Host
  Graphic), read the same request/read-reply way `^HW`'s listing already is.
  Confirmed against real hardware: the reply is not a self-contained image
  file — it is the same shape a `~DG` upload writes (name, total bytes,
  bytes per row, then the bitmap itself ASCII-hex encoded), just missing
  the device and extension a `~DG` carries. `graphics.decode_data()` reads
  that data half the same way it already does for `^GF` fields. If a reply
  does not start this way, PIL is tried on it directly as a fallback, in
  case some other firmware genuinely answers with a self-contained image;
  a reply that fits neither raises with its length and a hex preview
  rather than a bare decode error. The fetched image is then offered as a
  file to save.
- **Delete** sends `^ID...^FS`, the same shape `delete_printer_font` already
  uses, and asks for confirmation first, since — unlike everything else this
  designer does to a stored graphic — it cannot be undone from here.

Store and Retrieve also mirror a successful result into `graphic_store`'s
local, in-session cache, purely so an already-placed `^XG`/`^IM`/`^IL`
element updates on screen without a second round trip to the printer — the
same way uploading a font also registers it locally for the canvas to draw
with. An object the printer already had before this session opened is
listed by View but shows no thumbnail until Retrieved. Two real devices can
genuinely hold distinctly-named objects (a real `R:LOGO.GRF` and a real
`E:LOGO.GRF`); View's list keeps both distinct, but the *locally cached
pixels* for one can still overwrite the other's if both are Retrieved in one
session, since `graphic_store.key()` does not distinguish device (see §18).
None of this touches the Document: nothing about it is written to the saved
ZPL, and it never marks the file as having unsaved changes.

**`^LH` and `^LS` are folded into coordinates; `^LT` is not.** An element
holds the **absolute** dot position it will print at, so the canvas, dragging,
clamping and alignment need to know nothing about either command. A save
subtracts the offset again, so a file carrying one comes back exactly as it went
in.

| Written | Element holds | Saves as |
|---|---|---|
| `^LH100,100` + `^FO50,50` | 150, 150 | `^LH100,100` + `^FO50,50` |
| `^LS30` + `^FO50,50` | 20, 50 | `^LS30` + `^FO50,50` |
| `^LT10` + `^FO50,50` | 50, 50 | `^LT10` + `^FO50,50` |

`^LS` subtracts where `^LH` adds — it *"shifts all field positions to the
left"*. **`^LT` is carried but never applied**: it is media registration, ±120
dot rows of fine-tuning for print creeping up or down the roll, which modern
printers set at the printer. It says nothing about where a field sits within the
label, so folding it in would move the design on screen to describe a printer
adjustment. It is read and written back so a save cannot delete it.

`^LH` *"affects only fields that come after it"*, so it is read as a running
origin, the way `^CF` and `^BY` are. The document keeps the first one to write
back, fitted so that no element has to be written at a negative `^FO` — `^FO`'s
range starts at 0, and a format that moves its home part-way through leaves the
fields before it behind the new origin. Reducing the home costs nothing: printed
position is `home + ^FO`, so re-splitting the same absolute coordinate a
different way lands in exactly the same place.

**A parameter that is not a position is not an origin.** `^FX` comments run only
to the next caret, so prose naming a command becomes that command — a comment
mentioning `^LH` arrives as `^LH` carrying words. Reading that as `(0, 0)` let it
take the place of the format's real home, so the transform readers reject
anything that is not wholly a number.

**`^PO`, `^PM` and `^LR` describe how the finished label is laid down** and
round-trip unchanged. They are editable from Label Settings (§7), because they
apply to the whole label rather than to any field on it.

**Barcodes cannot rescale exactly.** Module width is a whole number of dots, so
a module of 2 becomes 3 rather than 2.96 going from 203 to 300 dpi — a width
error of up to half a dot per module. Positions and heights scale exactly.

---

## 12. Undo and redo

A single linear history of document snapshots. A snapshot holds the label size,
every element with all its properties, and which elements are selected — as
indices, since restoring replaces every element object and a group selection has
to come back as the group.

- **One entry per user action.** A drag or a resize is one entry, recorded when
  the mouse is released — not one per motion event.
- Every document change is undoable: adding, deleting, moving, resizing,
  reordering, aligning, editing an element through its dialog, toggling Print
  This Element, and changing the label size (including the element clamping that
  a smaller label causes).
- Changing the **selection** is not a document change and is not undoable: a
  click, a shift-click and a rubber band record no entry. An align that moves
  nothing records none either.
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
— are not undoable. This holds for a resolution changed through Label Size too:
one visit to that dialog is one history entry, and undoing it restores the label
size and the element positions a rescale moved, but not the resolution the
document is stamped with.

---

## 13. Persisted settings

An INI file at the platform's user config directory, `linuxzpl/settings.ini`.
Some Linux environments refuse writes there outright; when that happens the
file is written to the project's own directory instead, as `settings.ini`,
and read back from there the same way on the next start.

```ini
[printer]
address = 192.168.50.21
port = 9100
dpi = 203

[label]
width_in = 4.00
height_in = 6.00

[window]
x = 401
y = 952
width = 1201
height = 844
```

| Section | Written | Used by |
|---|---|---|
| `[printer]` | when Default Printer or Label Size is accepted | printing, font queries, every inch↔dot conversion |
| `[label]` | when Label Size is accepted | the label at startup and on File > New |
| `[window]` | on quit | where the window opens (§2) |

**File → Printer Settings ▸ Set Printer for This Session…** (§9) deliberately
never writes `[printer]` — only Default Printer and Label Size do.

Writing re-reads the file first, so a section another version wrote survives.

**The label size is stored in inches, not dots.** Dots only mean a physical size
once a resolution is fixed, and the resolution beside them is exactly what can
change between sessions — a size remembered as 812 × 1218 dots would silently
become a 2.7 × 4.1 inch label the day the printer became a 300 dpi one. Two
decimals is the dialog's own precision, so the file round-trips what was typed.

**Opening a file does not change the remembered size.** Opening someone else's
2 × 3 label must not redefine what File > New gives you from then on; only
accepting the Label Size dialog does.

A missing or corrupt file must never block startup; fall back to the defaults
above. A label size outside the range the dialog allows (0.5–25 inches) is
treated as corrupt and falls back too.

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
| Default label | the size last chosen in Label Settings, 4 × 6 inches until one is, at the configured dpi |
| Default printer | `192.168.50.21:9100`, 203 dpi |
| Supported resolutions | 203, 300, 600 dpi |
| Text | 36 dot height, 20 dot width, `"New Text"` |
| Text dialog limits | font height and width 8–500 dots |
| Frame | 200 × 150 dots, 2 dot thickness |
| Frame dialog limits | width 10–800, height 10–1200, thickness 1 to `min(w,h)/2` |
| Barcode | Code 128, `"123456789"`, 100 dot bar height, module width 2, value printed below |
| Barcode dialog limits | bar height 20–300 dots, module width 1–20, interpretation line height 6–200, ratio 2.0–3.0 |
| Image | 200 × 200 dots, JPEG/PNG source |
| Minimum element size when resizing | 20 × 20 dots |
| Resize handle size and hit radius | 8 **screen pixels** — `8 / scale` dots, so it neither shrinks out of reach when zoomed out nor covers the element when zoomed in; where the radius puts two handles in reach, the nearer wins |
| Resize drag origin | the box the element had at the press — a resize snaps to a derived size, so a delta measured from the previous motion event would be lost |
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
- **Five symbologies: Code 128, Code 39, EAN-13, Interleaved 2 of 5 and the
  UPC/EAN extension.** QR, Data Matrix, PDF417 and the rest are still not
  offered, and no symbology's value is validated against its own character
  set or length - EAN-13 and the extension fit whatever they are given rather
  than rejecting it (§3.3), and Code 39 draws an out-of-set character as a
  blank rather than refusing the barcode.
- **Code 39's Full ASCII Mode is not simulated.** The `+$`/`-$` escapes a
  scanner configured for it would read specially are drawn as the literal `+`,
  `$` and `-` characters they are - Code 39 itself has no such mode; it is a
  convention some scanners apply to the decoded text, one this designer has
  no way to know a given printer's scanner follows.
- **Modes `U` and `D` are carried but not simulated.** Only `A` changes the
  symbol; UCC case mode and UCC/EAN mode round-trip and can be chosen, but the
  canvas draws them as `N`. The same goes for `>` FNC1 escapes in `^FD`. Both
  are Code 128 only - the other four symbologies have no mode at all.
- **The exact subset-switching threshold is inferred.** Zebra does not publish
  where mode A moves into subset C; the rule above is the conservative reading,
  and a printer would settle it.
- **The interpretation line's leading is assumed** to be the font height plus
  two dots. ZPL does not document its own spacing.
- **`^FB`'s indent is applied to every line**, where ZPL hangs it on the second
  and later ones. The parameter round-trips; only where it lands differs.
- **The preview applies `^PO` and `^PM`; the editing canvas does not.** The
  preview answers "what will print", so it turns the finished label end for end
  and mirrors it. The canvas answers "what am I editing", and editing through a
  mirror is hostile: every pointer event would have to be inverse-transformed,
  and dragging right on an inverted label would move the element left. Pointer
  input is therefore untouched by either flag.
- **`^LR` round-trips but is not simulated.** ZPL defines it as *"identical to
  placing an `^FR` command in all current and subsequent fields"* — a per-field
  inversion against whatever is beneath — not a whole-image invert. Inverting
  the finished image would turn the white background black, which is not what a
  printer does, so nothing is drawn for it in either the canvas or the preview.
- **`^PQ`'s pause count, RFID replicates and override-pause flag round-trip but
  have no editor and are not otherwise acted on.** Only quantity, the common
  case, is exposed in Label Settings; a file from another tool that sets the
  other three keeps them through a save, the same treatment `^LT` gets.
- **The canvas shows a `^FN` placeholder; the preview does not.** The canvas
  answers "what am I editing", so an unfilled variable field draws its prompt or
  its number rather than becoming invisible. The preview answers "what will
  print", and an unfilled `^FN` prints nothing until the printer substitutes for
  it, so it draws no ink. This is the one place the two deliberately disagree.
- **A `^BC` with no height and no `^BY` to inherit one from is drawn 100 dots
  tall.** ZPL's power-up default is 10, which a printer would honour and which
  would make such a barcode a hairline on the canvas. A `^BY` that does give a
  height is always obeyed; this is the fallback when nothing in the file has
  said anything at all.
- **`^FR` is approximated as an ink/background swap on the field's own
  footprint, not a true sample-and-invert of whatever is already on the label
  underneath it.** Both canvases and the preview draw the field's background
  solid and its ink in the opposite colour, which reproduces the common case —
  a field reversed against a solid `^GB` box already there — without any new
  compositing machinery. The cost: a field reversed with nothing solid beneath
  it shows as a filled box, where a real printer would show nothing at all.
- **`^XG`/`^IM`/`^IL` resolve a stored graphic in `graphic_store` (the local,
  in-session cache) by name and extension only — the `R:`/`E:`/`B:`/`A:`
  device prefix is preserved for round-tripping but does not distinguish
  objects there.** A real printer has separate storage areas and can hold
  `R:LOGO.GRF` and `E:LOGO.GRF` as two different images at once; **Printer →
  Graphics…**'s View correctly keeps the two distinct, since it lists what
  the real printer reports, device and all. But Retrieving both into the
  local cache in the same session *can* now produce the collision this used
  to be impossible to reach: the second Retrieve's pixels overwrite the
  first's under the shared, device-blind key — see `zplcore/graphic_store.py`.
  Nothing about `^IS`/`^XG`/`^IM`/`^IL` themselves changed to cause this;
  it is Printer → Graphics… bridging session-local memory to a real
  multi-device printer for the first time.
- **A stored graphic resolves only for as long as the app keeps running, and
  only if this same run has already parsed the `^IS` that saved it, or
  Stored/Retrieved it via Printer → Graphics….** There is no on-disk
  persistence, no merging of data across separately opened files — the same
  limit `^DF`/`^XF` already has. Opening a file with `^XG`/`^IM`/`^IL` cold
  shows a placeholder naming what it is waiting for rather than failing or
  inventing an image; opening **Printer → Graphics…** and using Retrieve
  resolves it for real, straight from the printer, without needing a
  matching `^IS` to have been parsed at all.
- **`^DG` (Download Graphic), `^HG` (Host Graphic) and `^ID` (Object Delete)
  are not parsed from ZPL *files*.** A file that relies on any of them to
  seed, fetch or remove a stored object still opens with the graphic it names
  unresolved or unremoved — this app never sees the command, only the
  `^XG`/`^IM`/`^IL` that assumed it had already run. **Printer → Graphics…**
  performs the real exchanges instead — `~DG` for Store, `^HG` for Retrieve,
  `^ID` for Delete — directly against the printer, just never triggered by
  opening a file.
