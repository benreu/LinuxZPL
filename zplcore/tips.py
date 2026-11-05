"""
What a widget means, in the words its tooltip shows.

One table both frontends read, the way they read the choice lists in
zplcore/model.py: a row explained one way in Qt and another in GTK would
leave a user who moves between them believing two different things about
the same label. A tooltip is for a row whose meaning isn't plain from its
label - Reverse print, Max Lines, a QR code's mask - not for X or Width.

Each tip is keyed by the widget's name (its Qt objectName or GTK name), or
by its zplcore.properties key where the Properties panel's shared rows have
one, so a test can walk a form and hold every tooltip in it to this table.
The wording: one to three plain sentences, the ZPL command last in
parentheses when the row's label doesn't already show it, and why a row is
greyed out when it can be.

The frontends add their own markup: Qt word-wraps only a rich-text tooltip,
so qtui wraps each tip in a paragraph; GTK wraps a plain one itself.
"""

from typing import Optional

TIPS = {
    # --- every element with ink, in its editor and in the Properties panel
    'reverse_print':
        "Prints white where it overlaps ink already on the label (a filled "
        "frame, say) and black everywhere else. Only elements behind it "
        "count: use Send to Back on the dark shape.",
    'print_enabled':
        "Untick to keep the element on the canvas, faded, but leave it out "
        "of the printed label. It's saved as a comment.",
    'orientation':
        "Turns the field clockwise a quarter at a time: Rotated 90\u00b0 "
        "reads top to bottom, Rotated 270\u00b0 bottom to top.",

    # --- every element that carries data
    'element_id':
        "A name a Python program finds this element by to fill in its data "
        "(Label.fill). Saved as a ^FX comment, which the printer ignores.",
    'variable':
        "Ticked, the printer fills the data in when the label prints, by "
        "field number (^FN), for a stored format recalled with new data. "
        "Unticked, it prints what's typed.",
    'field_number': "Every field with the same number prints the same data.",
    'field_prompt':
        "A name for the field, shown on the canvas in its place and asked "
        "for by a printer's keypad or display.",

    # --- text, in every one of its editors
    'font_width':
        "The characters' width in dots. With a scalable font, a width unlike "
        "the height stretches or narrows them.",
    'direction':
        "Which way the characters run: left to right, stacked top to bottom, "
        "or right to left (^FP). Greyed out while Wrap is ticked, because a "
        "block keeps the direction it has.",
    'char_gap': "Extra space between characters, in dots (^FP).",
    'font':
        "A TrueType font from this computer, uploaded to the printer when "
        "the label prints. Clear goes back to the label's default.",
    'wrap':
        "Breaks the text into lines to fit a width. Typing a line break in "
        "the text ticks this for you.",
    'block_kind':
        "Field block (^FB): a width and a number of lines, with "
        "justification, spacing and indent. Text block (^TB): a width and a "
        "height; whatever doesn't fit is cut off. ^TB needs printer firmware "
        "x.14 or later.",
    'block_width': "The block's width in dots. Lines break between words to fit it.",
    'block_height':
        "The block's height in dots. Lines that don't fit aren't printed (^TB).",
    'max_lines':
        "The most lines the block prints. Text past the last line prints on "
        "top of it, so allow enough (^FB).",
    'line_spacing':
        "Extra space between lines, in dots; negative pulls them together (^FB).",
    'justification':
        "How each line sits in the block. Justified widens the spaces so "
        "every line but the last fills the width (^FB).",
    'text_justification':
        "Which edge the lines line up on. A text block takes this from the "
        "field itself, so Right also anchors the field at its right edge, "
        "and it grows to the left.",
    'indent': "Indent for every line after the first, in dots: a hanging indent (^FB).",

    # --- a time field
    'clock_text':
        "What to print, with % codes the printer fills in from its clock: "
        "%m month, %d day, %y year (%Y four digits), %H:%M:%S time, %I and "
        "%p for 12-hour, %j day of the year, %U or %W week, %a or %A "
        "weekday, %b or %B month name. The canvas shows the codes.",
    'clock_format':
        "Ticked, the printer replaces the % codes with its real-time clock "
        "as it prints (^FC). Untick to print the text as typed.",

    # --- a serial field
    'serial_start':
        "The first value printed. Its last run of digits counts, up to 12 of "
        "them; anything before it stays as it is (^SN).",
    'serial_increment': "Added on each label. Negative counts down.",
    'serial_leading_zero':
        "Pads the counter with zeros to keep its width: counting down from "
        "100 gives 099, not 99.",
    'serial_format':
        "Ticked, the printer counts this on by itself for each label (^SN). "
        "Untick to print the start value every time.",

    # --- a numbered field
    'numbered_text':
        "Optional data for this field number. Every field carrying the same "
        "number prints it too.",

    # --- frames, circles, ellipses and diagonal lines
    'thickness':
        "The line's thickness in dots. Half the shorter side fills a frame, "
        "circle or ellipse solid.",
    'colour':
        "Black prints ink. White prints none over anything behind it, which "
        "is a way to clear part of a filled shape.",
    'rounding':
        "0 is square, 8 the roundest. How round depends on the frame's "
        "shorter side (^GB).",

    # --- a stored graphic
    'command':
        "Recall Graphic (^XG) places a stored graphic and can enlarge it. "
        "Image Move (^IM) places it as stored, with no enlarging.",
    'device':
        "Where on the printer the file lives. R: (DRAM) is lost when the "
        "printer is switched off; E: (Flash) keeps it; B: and A: are memory "
        "cards.",
    'name': "The name the printer stores it under, up to 8 characters.",
    'extension': "The stored file's type: GRF, or for Image Move also PNG.",
    'mag_x': "Enlarges the graphic 1 to 10 times across. Recall Graphic (^XG) only.",
    'mag_y': "Enlarges the graphic 1 to 10 times down. Recall Graphic (^XG) only.",

    # --- the main window
    'add_symbol': "Add a graphic symbol (^GS)",
    'align': "Line the selection up (Edit ▸ Align)",

    # --- the Properties panel's own header (GTK draws it; Qt's dock has a
    # title bar of its own)
    'close_panel': "Close the panel (View ▸ Properties Panel)",
    'float_panel': "Float the panel in a window of its own",
    'dock_panel': "Dock the panel beside the canvas",

    # --- the Print window
    'replicates': "Copies of each serial number before it counts on",
    'darkness_set': "Clear for the printer's setting",
}

# A barcode symbology's own rows, from symbology.BARCODE_PARAMETERS, keyed by
# (symbology, attribute): one row serves every symbology with that attribute,
# and a Data Matrix's columns are not a PDF417's.
PARAMETERS = {}

# The Check Digit row, by the label BARCODE_FEATURES gives it: Code 128's,
# Code 39's and a UPC's are three different things under one row.
CHECK_DIGIT = {}


def tip(key: str) -> Optional[str]:
    """The tip for the row named `key`, or None when it has none."""
    return TIPS.get(key)
