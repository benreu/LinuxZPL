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

    # --- a barcode
    'bar_height':
        "The bars' height in dots. For PDF417 and MicroPDF417, the height of "
        "each row.",
    'module_width':
        "The size of the barcode's smallest part in dots: a narrow bar, or "
        "one square of a matrix code. Bigger scans more easily (^BY).",
    'ratio':
        "How many times wider the wide bars are than the narrow ones, 2.0 to "
        "3.0. Only barcodes with two bar widths have it (^BY).",
    'value_text':
        "Where the readable line of the value prints: under the bars, over "
        "them, or not at all.",
    'mode':
        "How the data becomes Code 128. None uses the subset switches you "
        "write (>: >; >5). Automatic chooses them, packing four or more "
        "digits in pairs. UCC case takes 19 digits for a shipping case code. "
        "UCC/EAN reads GS1 application identifiers in parentheses, which "
        "print but aren't encoded.",
    'insert':
        "Control characters a UPS MaxiCode message is built from, which a "
        "keyboard can't type. Each goes in at the cursor as a ^FH escape.",
    'insert_GS':
        "Group separator, between the fields of a message. Written _1D (^FH).",
    'insert_RS':
        "Record separator, between the formats of a message. Written _1E (^FH).",
    'insert_EOT':
        "End of transmission, which ends the message. Written _04 (^FH).",
    'positions':
        "Data too long for one symbol is split across several, which a "
        "scanner joins back up; each row places one of them. Untick Print to "
        "leave that symbol off (^FM).",

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
PARAMETERS = {
    ('qr', 'quality'):
        "How much damage the symbol survives: L about 7 %, M 15 %, Q 25 %, "
        "H 30 %. More makes a bigger symbol.",
    ('qr', 'qr_model'):
        "Model 2 is the standard, and what the manual recommends. Model 1 is "
        "the original, for old readers only.",
    ('qr', 'qr_mask'):
        "A pattern laid over the modules so that no area confuses a reader. "
        "Every mask scans; change it only if a reader struggles. The "
        "printer's own default is 7.",
    ('datamatrix', 'aspect'):
        "Square, or rectangular to fit a narrow space. Rectangular needs "
        "printer firmware V60.16.5Z or later.",
    ('datamatrix', 'quality_dm'):
        "ECC 200 is the Data Matrix every modern reader expects, and what "
        "the manual recommends. ECC 000 to 140 are older types, for a closed "
        "system that reads only its own labels.",
    ('datamatrix', 'columns'):
        "Makes the symbol at least this many columns, so a row of them come "
        "out alike. Data too big for that size prints no symbol at all. Fit "
        "the data picks the smallest that holds it.",
    ('datamatrix', 'rows'):
        "Makes the symbol at least this many rows, so a row of them come out "
        "alike. Data too big for that size prints no symbol at all. Fit the "
        "data picks the smallest that holds it.",
    ('pdf417', 'security'):
        "How many error-correction codewords to add. 0 only detects damage; "
        "each level up repairs more and makes the symbol bigger.",
    ('pdf417', 'columns'):
        "Data columns across the symbol, 1 to 30, which set its width. Fit "
        "the aspect lets the printer choose.",
    ('pdf417', 'rows'):
        "Rows down the symbol, 3 to 90, which set its height. Fit the data "
        "uses as many as the data needs.",
    ('pdf417', 'truncate'):
        "Leaves off the right-hand row indicators and stop pattern: "
        "narrower, but less tolerant of damage.",
    ('micropdf417', 'micro_mode'):
        "The symbol's columns and rows. Each size holds a fixed amount of "
        "data and error correction (^BF).",
    ('tlc39', 'micro_width'):
        "The module width of the small MicroPDF417 over the Code 39, in dots.",
    ('tlc39', 'micro_height'):
        "The row height of the small MicroPDF417 over the Code 39, in dots.",
    ('aztec', 'aztec_size'):
        "A percentage is the least error correction to add, and the printer "
        "sizes the symbol to fit. A layer count fixes the size instead: "
        "Compact up to 4 layers, Full range up to 32.",
    ('codabar', 'start_char'):
        "The start character, A to D. Any pair scans; some systems use the "
        "letters to tell kinds of label apart.",
    ('codabar', 'stop_char'):
        "The stop character, A to D. Any pair scans; some systems use the "
        "letters to tell kinds of label apart.",
    ('code11', 'code11_check'):
        "Code 11 normally carries two check digits; one is enough for 10 "
        "digits or fewer.",
    ('databar', 'databar_type'):
        "Which GS1 DataBar to print. Omnidirectional and Stacked "
        "Omnidirectional read from any angle; Truncated, Stacked and Limited "
        "are smaller; Expanded holds GS1 application identifiers.",
    ('databar', 'separator'):
        "The height of the separator between a stacked or composite "
        "symbol's parts, 1 or 2.",
    ('postal', 'postal_type'):
        "Postnet and PLANET are the older US mail barcodes; the USPS "
        "Intelligent Mail barcode replaced them.",
    ('maxicode', 'maxi_mode'):
        "2 and 3 are UPS shipping labels, with a US numeric or an "
        "international alphanumeric postal code; 4 is general data; 5 has "
        "more error correction; 6 programs a reader.",
    ('maxicode', 'symbol_number'):
        "Data too long for one MaxiCode can be split over up to 8: which one "
        "of them this is.",
    ('maxicode', 'symbol_count'):
        "How many MaxiCodes the data is split over, up to 8.",
    ('msi', 'msi_check'):
        "Which check digits the printer adds. They must match what the "
        "scanner expects.",
    ('msi', 'msi_show_check'): "Whether the readable line shows the check digits.",
}

# The Check Digit row, by the label BARCODE_FEATURES gives it: Code 128's,
# Code 39's and a UPC's are three different things under one row.
CHECK_DIGIT = {
    "UCC Check Digit":
        "Adds a mod 10 check digit to the data, as UCC/EAN shipping labels "
        "need. The symbol's own mod 103 check character is always there.",
    "Mod-43 Check Digit":
        "Adds a mod 43 check digit to the data. Use it only if the scanner "
        "or the standard you're labelling for expects one.",
    "Mod-10 Check Digit":
        "Adds a mod 10 check digit to the data. Use it only if the scanner "
        "or the standard you're labelling for expects one.",
    "Print Check Digit":
        "The symbol always carries its check digit; this is whether the "
        "readable line shows it.",
    "Print Check Characters":
        "The printer always adds Code 93's two check characters; this is "
        "whether the readable line shows them.",
    "Print Check Digits":
        "The printer always adds Plessey's check digits; this is whether the "
        "readable line shows them.",
}


def tip(key: str) -> Optional[str]:
    """The tip for the row named `key`, or None when it has none."""
    return TIPS.get(key)
