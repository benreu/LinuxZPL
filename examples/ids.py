"""Fill a label's elements by ID from Python.

    python3 examples/ids.py                     # uses the template below
    python3 examples/ids.py my_label.zpl        # uses a file saved from the designer

An element's ID is an ^FX comment, `^FXid:customer`, which a printer ignores.
In the designer, double-click a text, barcode or symbol and fill in its ID row.
Writes one .zpl and one .png per record into a folder it prints at the end.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
from zplcore import Label

# The ID goes inside the field, between ^FO and ^FS. The text after ^FD is only
# a placeholder: it is what the label shows until Python replaces it.
TEMPLATE = """^XA
^PW600
^LL260
^FO30,30^FXid:customer^A0N,50,50^FDCustomer^FS
^FO30,100^FXid:sku^BCN,70,Y,N^FD000000^FS
^FO30,220^A0N,24,24^FDThis text has no ID and is never changed^FS
^XZ"""

RECORDS = [
    {"customer": "ACME Corp", "sku": "100234"},
    {"customer": "Globex", "sku": "100235"},
]


def main():
    # Label.load(path) reads a file; Label.from_zpl(text) takes the ZPL itself.
    label = Label.load(sys.argv[1]) if len(sys.argv) > 1 else Label.from_zpl(TEMPLATE)
    print("IDs in the template:", label.ids)

    out = tempfile.mkdtemp(prefix="linuxzpl_ids_")
    for number, record in enumerate(RECORDS, 1):
        # One Label serves every record: filling never changes the template.
        for ident, value in record.items():
            if ident in label.ids:
                label[ident] = value            # same as label.fill(customer=...)
        # Several at once, with keywords: label.fill(customer="ACME", sku="1")

        base = os.path.join(out, f"label_{number}")
        label.save(base + ".zpl")
        label.save_image(base + ".png")
        print(f"record {number}: {record}")

    # A misspelt ID is an error, not a silent no-op, and says what IDs exist.
    try:
        label["custmer"] = "typo"
    except KeyError as e:
        print("misspelt ID refused:", e)

    # IDs are strings; ints are ^FN field numbers, so the two never collide.
    # label[1] = "x"  would fill the element with ^FN1 instead.

    print("written to", out)


if __name__ == "__main__":
    main()
