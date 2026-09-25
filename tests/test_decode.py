"""Read back what the preview drew, with a real barcode decoder.

Every other suite checks that a symbol is the size and shape this designer
means it to be. This one checks the only thing that actually matters on a
label: that a scanner gets the value out again. It renders through the same
preview the file chooser uses, so what is decoded is what would print.

The decoder is not a dependency of the designer - it is only ever used here.
Without it the suite says so and passes, rather than failing a machine that
simply has not installed a test tool:

    pip install zxing-cpp        (or: apt install python3-zxing-cpp)
"""

import os, sys
from pathlib import Path
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import _isolate  # a throwaway settings file, before anything else is imported

try:
    import zxingcpp
except ImportError:
    print("SKIPPED: no decoder. pip install zxing-cpp (or apt install "
          "python3-zxing-cpp) to check that what is drawn can be read back.")
    sys.exit(0)

from zplcore.renderer import ZPLRenderer

fails = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + ((" -- " + str(extra)) if extra else ""))
    if not cond:
        fails.append(name)


def decoded(zpl: str, width=600, height=600, dpi=203) -> list:
    """Everything a decoder finds in the preview of this label."""
    image = ZPLRenderer(width, height, dpi).render(zpl).convert('L')
    return [(result.format.name, result.text)
            for result in zxingcpp.read_barcodes(image)]


def reads(name: str, zpl: str, expect: str, symbology: str) -> None:
    """One label, drawn and read back."""
    found = decoded(zpl)
    check(name,
          len(found) == 1 and found[0] == (symbology, expect),
          found or "nothing decoded")


# --- ^BQ, the QR code -------------------------------------------------------

# The manual's own examples. What a scanner gets is the value behind the
# switches, never the switches themselves.
reads("^BQ carries the value behind its switches, not the switches",
      "^XA^PW600^LL600^FO20,20^BQ,2,10^FDMM,AAC-42^FS^XZ", "AC-42", 'QRCode')
reads("automatic input reads back whole, spaces and all",
      "^XA^PW600^LL600^FO20,20^BQ,2,6^FDQA,0123456789ABCD 2D code^FS^XZ",
      "0123456789ABCD 2D code", 'QRCode')
reads("manual numeric input reads back as its digits",
      "^XA^PW600^LL600^FO20,20^BQ,2,8^FDHM,N123456789012345^FS^XZ",
      "123456789012345", 'QRCode')
reads("mixed mode's segments come back as one string",
      "^XA^PW600^LL600^FO20,20^BQ,2,6"
      "^FDD03048F,LM,N0123456789,A12AABB,B0006qrcode^FS^XZ",
      "012345678912AABBqrcode", 'QRCode')
reads("a field with no switches at all is the whole value",
      "^XA^PW600^LL600^FO20,20^BQ,2,6^FDplain value^FS^XZ",
      "plain value", 'QRCode')
reads("a URL, the thing most QR codes on a label actually hold",
      "^XA^PW600^LL600^FO20,20^BQN,2,5,L^FDhttps://example.com/a/b?c=1^FS^XZ",
      "https://example.com/a/b?c=1", 'QRCode')

# Every mask and every level is a different symbol of the same value, and a
# printer draws the one the command names - so each has to be readable.
for mask in range(8):
    reads(f"mask {mask} is readable",
          f"^XA^PW600^LL600^FO20,20^BQ,2,6,M,{mask}^FDMM,AMASK{mask}^FS^XZ",
          f"MASK{mask}", 'QRCode')
for level in "LMQH":
    reads(f"error correction {level} is readable",
          f"^XA^PW600^LL600^FO20,20^BQ,2,6,{level}^FDMM,ALEVEL{level}^FS^XZ",
          f"LEVEL{level}", 'QRCode')

# A turned symbol is the same symbol: the preview rotates the drawn panel, so
# a quarter turn that lost a row or a column would show up here and nowhere
# else.
for facing in "NRIB":
    reads(f"a symbol turned {facing} still reads",
          f"^XA^PW600^LL600^FO20,20^BQ{facing},2,6^FDMM,ATURN{facing}^FS^XZ",
          f"TURN{facing}", 'QRCode')

# The magnification a file leaves out is the head's own, so the same label
# drawn for a 300 dpi printer is a bigger symbol carrying the same thing.
for dpi in (150, 203, 300, 600):
    label = (f"^XA^PW600^LL600^FXDESIGNER_DPI:{dpi}\n"
             f"^FO20,20^BQ^FDMM,ADPI{dpi}^FS^XZ")
    reads(f"an omitted magnification at {dpi} dpi still reads",
          label, f"DPI{dpi}", 'QRCode')

# --- the symbologies that already drew --------------------------------------
# Not new, but never read back by a decoder before - a bar a dot too wide or
# a guard pattern a module short is exactly the kind of thing that passes
# every size check and fails at the scanner.

# Placed at 60 dots in rather than 20: a decoder wants a quiet zone either
# side - ten modules for ITF - and so does a real scanner, which is why no
# label puts a barcode hard against its own edge.
reads("^BC Code 128 reads back",
      "^XA^PW600^LL600^FO60,60^BY3^BCN,100^FDABC-12345^FS^XZ",
      "ABC-12345", 'Code128')
reads("^BC in mode A, which packs digit pairs into subset C",
      "^XA^PW600^LL600^FO60,60^BY3^BCN,100,Y,N,N,A^FD1234567890^FS^XZ",
      "1234567890", 'Code128')
reads("^B3 Code 39 reads back, between its own start and stop",
      "^XA^PW600^LL600^FO60,60^BY3^B3N,N,100^FDCODE-39^FS^XZ",
      "CODE-39", 'Code39')
reads("^BE EAN-13 reads back, with the check digit it added itself",
      "^XA^PW600^LL600^FO60,60^BY3^BEN,100^FD400638133393^FS^XZ",
      "4006381333931", 'EAN13')
reads("^B2 Interleaved 2 of 5 reads back",
      "^XA^PW600^LL600^FO60,60^BY3^B2N,100^FD123456^FS^XZ",
      "123456", 'ITF')

# --- the symbologies added with this one ------------------------------------
# A UPC-A is an EAN-13 with a leading zero, bar for bar, so a decoder is free
# to call it either - and reports the thirteen digits. A UPC-E is reported
# expanded, as the UPC-A it stands for, which is the real check on the
# zero-suppression table: a wrong rule gives a well-formed symbol for the
# wrong product code.
reads("^BU UPC-A reads back, with the check digit it added itself",
      "^XA^PW600^LL600^FO60,60^BY3^BUN,100^FD03600029145^FS^XZ",
      "0036000291452", 'EAN13')
reads("^B9 UPC-E expands to the UPC-A number it was given",
      "^XA^PW600^LL600^FO60,60^BY3^B9N,100^FD4210000526^FS^XZ",
      "0042100005264", 'UPCE')
reads("...and a different suppression rule expands to its own number",
      "^XA^PW600^LL600^FO60,60^BY3^B9N,100^FD1230000045^FS^XZ",
      "0012300000451", 'UPCE')
reads("^B8 EAN-8 reads back",
      "^XA^PW600^LL600^FO60,60^BY3^B8N,100^FD9638507^FS^XZ",
      "96385074", 'EAN8')
reads("^BA Code 93 reads back, its two check characters checking out",
      "^XA^PW600^LL600^FO60,60^BY3^BAN,100^FDTEST93^FS^XZ",
      "TEST93", 'Code93')
reads("^BA Code 93 carries the full alphanumeric set",
      "^XA^PW600^LL600^FO60,60^BY2^BAN,100^FDABC-123. $/+%^FS^XZ",
      "ABC-123. $/+%", 'Code93')
reads("^BK Codabar reads back between the start and stop it was given",
      "^XA^PW600^LL600^FO60,60^BY3^BKN,N,100,Y,N,A,A^FD123456^FS^XZ",
      "A123456A", 'Codabar')
reads("...and a different start and stop pair comes back as that pair",
      "^XA^PW600^LL600^FO60,60^BY3^BKN,N,100,Y,N,B,C^FD12-34^FS^XZ",
      "B12-34C", 'Codabar')
reads("^BL LOGMARS reads back as the Code 39 it is, check digit and all",
      "^XA^PW600^LL600^FO60,60^BY3^BLN,100^FD12AB^FS^XZ",
      "12ABO", 'Code39')

# --- ^BX, Data Matrix -------------------------------------------------------

reads("^BX carries its value, at the module size its own command gives",
      "^XA^PW600^LL600^FO60,60^BXN,8,200^FDHELLO^FS^XZ", "HELLO", 'DataMatrix')
reads("the manual's own example, which fills several data regions",
      "^XA^PW600^LL600^FO60,60^BXN,6,200"
      "^FDZEBRA TECHNOLOGIES CORPORATION 333 CORPORATE WOODS PARKWAY^FS^XZ",
      "ZEBRA TECHNOLOGIES CORPORATION 333 CORPORATE WOODS PARKWAY", 'DataMatrix')
reads("a rectangular symbol reads the same as a square one",
      "^XA^PW600^LL600^FO60,60^BXN,6,200,,,,,2^FDZEBRA TECH^FS^XZ",
      "ZEBRA TECH", 'DataMatrix')
reads("a symbol forced up to a larger size still reads",
      "^XA^PW600^LL600^FO60,60^BXN,8,200,20,20^FDFORCED^FS^XZ",
      "FORCED", 'DataMatrix')
reads("...and one sized from ^BY's height rather than its own parameter",
      "^XA^PW600^LL600^FO60,60^BY3,3,200^BXN,,200"
      "^FDZEBRA TECHNOLOGIES CORPORATION^FS^XZ",
      "ZEBRA TECHNOLOGIES CORPORATION", 'DataMatrix')
for facing in "NRIB":
    reads(f"a Data Matrix turned {facing} still reads",
          f"^XA^PW600^LL600^FO60,60^BX{facing},8,200^FDTURN{facing}^FS^XZ",
          f"TURN{facing}", 'DataMatrix')
# Each encodation scheme, picked by what the data is made of: digits go to
# ASCII in pairs, upper case to C40, lower case to Text, and anything with a
# byte above 127 to Base 256. A scheme chosen but mis-encoded decodes to
# something else, or to nothing, which is the whole point of reading it back.
for name, value in (("digit pairs, in ASCII", "12345678901234567890"),
                    ("upper case, in C40", "ZEBRA TECHNOLOGIES CORP"),
                    ("lower case, in Text", "lower case only here"),
                    ("mixed case and punctuation",
                     "https://example.com/track/AC-42"),
                    ("a single character", "A")):
    reads(f"^BX encodes {name}",
          f"^XA^PW600^LL600^FO60,60^BXN,6,200^FD{value}^FS^XZ",
          value, 'DataMatrix')

# --- ^B7, PDF417 ------------------------------------------------------------

reads("^B7 carries its value at the columns and security asked for",
      "^XA^PW800^LL600^FO60,60^BY2^B7N,5,5^FDPDF417 test^FS^XZ",
      "PDF417 test", 'PDF417')
reads("the manual's own example, a paragraph of text",
      "^XA^PW800^LL600^FO60,60^BY2^B7N,3,5,5"
      "^FDZebra Technologies Corporation strives to be the expert supplier^FS^XZ",
      "Zebra Technologies Corporation strives to be the expert supplier", 'PDF417')
reads("a truncated symbol still reads, being narrower by an indicator and a stop",
      "^XA^PW800^LL600^FO60,60^BY2^B7N,4,0,,,Y^FDTRUNCATED^FS^XZ",
      "TRUNCATED", 'PDF417')
reads("...and one sized from ^BY's height rather than its own parameter",
      "^XA^PW800^LL600^FO60,60^BY3,3,200^B7N^FDZebra Technologies^FS^XZ",
      "Zebra Technologies", 'PDF417')
for facing in "NRIB":
    reads(f"a PDF417 turned {facing} still reads",
          f"^XA^PW800^LL600^FO60,60^BY2^B7{facing},4,3^FDTURN{facing}^FS^XZ",
          f"TURN{facing}", 'PDF417')
# Each compaction mode, picked by what the run is made of. A mode chosen but
# mis-encoded decodes to something else - which is how a full stop sitting at
# the wrong value in the upper submode was caught, having turned "a.b" into
# "aAk".
for name, value in (("upper-case text", "ZEBRA TECHNOLOGIES"),
                    ("lower case, through its own submode", "lower case only"),
                    ("mixed case, shifting between them", "Mixed Case Text"),
                    ("punctuation, through the punct submode", "a.b;c<d>e@f"),
                    ("a long run of digits, in numeric mode",
                     "1234567890123456789012345678901234567890"),
                    ("bytes above ASCII, in byte mode", "caf\u00e9 r\u00e9sum\u00e9"),
                    ("text and digits together", "PART 12345678901234 REV A")):
    reads(f"^B7 compacts {name}",
          f"^XA^PW800^LL600^FO60,60^BY2^B7N,4,3^FD{value}^FS^XZ",
          value, 'PDF417')

# ^BI, ^BJ, ^B1, ^BM and ^BP have no decoder here - zxing reads none of
# Industrial or Standard 2 of 5, Code 11, MSI or Plessey. Each was checked
# module for module against BWIPP, the reference implementation, while it was
# written; see FUNCTIONAL_SPEC.md section 18.

if fails:
    print(f"\n{len(fails)} DECODE CHECK(S) FAILED")
    for name in fails:
        print("  - " + name)
    sys.exit(1)
print("\nALL DECODE CHECKS PASSED")
