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

if fails:
    print(f"\n{len(fails)} DECODE CHECK(S) FAILED")
    for name in fails:
        print("  - " + name)
    sys.exit(1)
print("\nALL DECODE CHECKS PASSED")
