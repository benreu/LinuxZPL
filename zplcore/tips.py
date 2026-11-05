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
