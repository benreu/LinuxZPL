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

# --- ^BF, MicroPDF417 -------------------------------------------------------

from zplcore import micropdf417 as _micro

# zxing-cpp reads MicroPDF417 from 3.0 on; the 2.x a distribution may
# package has no such format at all, which is a missing tool rather than
# a symbol drawn wrong.
if not hasattr(zxingcpp.BarcodeFormat, 'MicroPDF417'):
    print("SKIPPED: this zxing-cpp cannot read MicroPDF417 - pip install "
          "zxing-cpp (3.0 or later) to check ^BF.")
else:
    reads("the manual's own ^BF example, six-dot modules and eight-dot rows",
          "^XA^PW800^LL600^FO60,60^BY6^BFN,8,3^FDABCDEFGHIJKLMNOPQRSTUV^FS^XZ",
          "ABCDEFGHIJKLMNOPQRSTUV", 'MicroPDF417')
    # Every one of the 34 sizes, as full as the manual's Table 10 says it gets
    # with letters - the mode picks the rows, the columns, the error correction
    # and the address patterns each row starts on, and a reader checks all four.
    _TABLE_10_LETTERS = (6, 12, 18, 22, 30, 38, 14, 24, 36, 46, 56, 64, 72, 10,
                         18, 26, 34, 46, 66, 90, 114, 138, 162, 22, 34, 46, 58,
                         76, 106, 142, 178, 214, 250, 14)
    for _mode, _letters in enumerate(_TABLE_10_LETTERS):
        _value = ("ZEBRA TECHNOLOGIES MICROPDF " * 10)[:_letters].replace(' ', 'X')
        _rows = _micro.size(_mode)[1]
        reads(f"^BF mode {_mode}, holding its {_letters} letters, reads back",
              f"^XA^PW800^LL800^FO40,40^BY2^BFN,{max(4, 400 // _rows)},{_mode}"
              f"^FD{_value}^FS^XZ",
              _value, 'MicroPDF417')
    for facing in "NRIB":
        reads(f"a MicroPDF417 turned {facing} still reads",
              f"^XA^PW600^LL600^FO60,60^BY3^BF{facing},9,18^FDTURN{facing}^FS^XZ",
              f"TURN{facing}", 'MicroPDF417')
    for name, value in (("lower case and punctuation", "micro pdf; a.b<c>"),
                        ("the eight digits mode 0 holds, in numeric mode",
                         "12345678"),
                        ("a short run of digits among text", "LOT 4417 A"),
                        ("bytes above ASCII, in byte mode", "café résumé"),
                        ("digits first, so no text latch", "0123456789 PART")):
        _mode = next(mode for mode in range(_micro.MODES)
                     if len(_micro.data_codewords(value)) <= _micro.capacity(mode))
        reads(f"^BF compacts {name}",
              f"^XA^PW800^LL600^FO60,60^BY3^BFN,10,{_mode}^FD{value}^FS^XZ",
              value, 'MicroPDF417')
    for dpi, page in ((150, (500, 500)), (300, (900, 900)), (600, (1600, 1600))):
        found = decoded(f"^XA^FO40,40^BY{max(2, dpi // 100)}^BFN,{dpi // 25},25"
                        f"^FDRESOLUTION {dpi}^FS^XZ", *page, dpi=dpi)
        check(f"a MicroPDF417 drawn for a {dpi} dpi head reads back",
              found == [('MicroPDF417', f"RESOLUTION {dpi}")], found)

# --- ^FM, a series of symbols -----------------------------------------------

from zplcore import pdf417 as _pdf417

# A reader gives each symbol of a series back on its own; put together in
# order they are the message. zxing-cpp's Python binding does not say which
# piece a symbol is, so the order is where each was put.
_SERIES_TEXT = ("Zebra Technologies Corporation strives to be the expert "
                "supplier. ") * 9


def _series_read(zpl, width, height, formats):
    image = ZPLRenderer(width, height, 203).render(zpl).convert('L')
    return sorted(zxingcpp.read_barcodes(image, formats=formats),
                  key=lambda r: (r.position.top_left.y, r.position.top_left.x))


_pieces = _pdf417.series(_SERIES_TEXT, 6, 20, 2)
found = _series_read("^XA^FM20,20,20,300,20,580,20,860^BY2^B7N,3,2,6,20"
                     f"^FD{_SERIES_TEXT}^FS^XZ", 700, 1300,
                     zxingcpp.BarcodeFormat.PDF417)
check("^FM's four PDF417s each read back, and together are the message",
      len(_pieces) == 4 and [r.text for r in found] == list(_pieces)
      and ''.join(r.text for r in found) == _SERIES_TEXT,
      [len(r.text) for r in found])
found = _series_read("^XA^FM20,20,e,e,20,580,20,860^BY2^B7N,3,2,6,20"
                     f"^FD{_SERIES_TEXT}^FS^XZ", 700, 1300,
                     zxingcpp.BarcodeFormat.PDF417)
check("with the second excluded, the other three read back as theirs",
      [r.text for r in found] == [_pieces[0], _pieces[2], _pieces[3]],
      [len(r.text) for r in found])
found = _series_read("^XA^FM20,20,400,20,20,500,400,500^BY2^B7R,3,2,6,20"
                     f"^FD{_SERIES_TEXT}^FS^XZ", 900, 1000,
                     zxingcpp.BarcodeFormat.PDF417)
check("and turned, each about its own origin, they still read back",
      sorted(r.text for r in found) == sorted(_pieces),
      [len(r.text) for r in found])
if hasattr(zxingcpp.BarcodeFormat, 'MicroPDF417'):
    _micro_pieces = _micro.series(_SERIES_TEXT[:300], 22)
    found = _series_read("^XA^FM20,20,20,400,20,780^BY2^BFN,4,22"
                         f"^FD{_SERIES_TEXT[:300]}^FS^XZ", 500, 1100,
                         zxingcpp.BarcodeFormat.MicroPDF417)
    check("a MicroPDF417 series reads back piece by piece too",
          len(_micro_pieces) == 3
          and [r.text for r in found] == list(_micro_pieces),
          [len(r.text) for r in found])

# --- ^B0, Aztec Code --------------------------------------------------------

reads("^B0 carries its value, at the magnification its own command gives",
      "^XA^PW700^LL500^FO60,60^B0N,6^FDAztec test^FS^XZ",
      "Aztec test", 'Aztec')
reads("the manual's own example, turned as the manual turns it",
      "^XA^PW700^LL500^FO60,60^B0R,7,N,0,N,1,0"
      "^FD 7. This is testing label 7^FS^XZ",
      " 7. This is testing label 7", 'Aztec')
reads("^BO is the same command spelled with the letter",
      "^XA^PW700^LL500^FO60,60^BON,6^FDalias works^FS^XZ",
      "alias works", 'Aztec')
for size, why in ((102, "a compact symbol of two layers"),
                  (104, "a compact symbol of four"),
                  (203, "a full-range symbol of three"),
                  (50, "half the symbol given over to correction"),
                  (95, "almost all of it")):
    reads(f"^B0 draws {why}",
          f"^XA^PW700^LL500^FO60,60^B0N,5,N,{size}^FDSIZE {size}^FS^XZ",
          f"SIZE {size}", 'Aztec')
for facing in "NRIB":
    reads(f"an Aztec turned {facing} still reads",
          f"^XA^PW700^LL500^FO60,60^B0{facing},6^FDTURN{facing}^FS^XZ",
          f"TURN{facing}", 'Aztec')
# Each character mode, and the switches between them. A mis-encoded mode
# decodes to something else, which is the only way to catch it.
for name, value in (("upper case", "ZEBRA TECHNOLOGIES"),
                    ("lower case", "lower case only"),
                    ("mixed case, shifting", "Mixed Case Text"),
                    ("digits, in their own four-bit mode", "1234567890"),
                    ("punctuation", "a.b, c: d"),
                    ("bytes above ASCII", "caf\u00e9 r\u00e9sum\u00e9"),
                    ("a URL", "https://example.com/a?b=1")):
    reads(f"^B0 encodes {name}",
          f"^XA^PW700^LL500^FO60,60^B0N,5^FD{value}^FS^XZ", value, 'Aztec')

# --- ^BD, UPS MaxiCode -----------------------------------------------------

# zxing reads a MaxiCode only from a "pure" image - the symbol and nothing
# else, found by the extent of its dark dots - so each label holds only the
# one symbol. What it returns is compared as bytes, since its default text
# shows GS and RS as picture symbols rather than the characters themselves.
def reads_maxicode(name: str, zpl: str, expect: str, dpi=203,
                   size=(700, 700)) -> None:
    image = ZPLRenderer(size[0], size[1], dpi).render(zpl).convert('L')
    found = [result.bytes.decode('latin-1') for result in
             zxingcpp.read_barcodes(image, formats=zxingcpp.BarcodeFormat.MaxiCode)]
    check(name, found == [expect], found or "nothing decoded")


_UPS = ("001840152382802[)>_1E01_1D961Z00004951_1DUPSN_1D_06X610_1D159_1D1234567"
        "_1D1/1_1D_1DY_1D634 ALPHA DR_1DPITTSBURGH_1DPA_1E_04")
# A reader hands back the sorting code - ZIP+4, country, class of service -
# spliced in after the message's own header, which is how a mode 2 or 3
# symbol is meant to be read.
_UPS_READ = ("[)>\x1e01\x1d96152382802\x1d840\x1d001\x1d1Z00004951\x1dUPSN"
             "\x1d\x06X610\x1d159\x1d1234567\x1d1/1\x1d\x1dY\x1d634 ALPHA DR"
             "\x1dPITTSBURGH\x1dPA\x1e\x04")
for dpi, page in ((203, (700, 700)), (300, (900, 900)), (600, (1500, 1500))):
    reads_maxicode(f"the manual's own UPS MaxiCode reads back at {dpi} dpi",
                   f"^XA^PW{page[0]}^LL{page[1]}^FO60,60^BD^FH^FD{_UPS}^FS^XZ",
                   _UPS_READ, dpi, page)
reads_maxicode("mode 3 carries an international postal code of letters",
               "^XA^PW700^LL700^FO60,60^BD3^FH"
               "^FD066826ABC123[)>_1E01_1D96INTL_1D_1E_04^FS^XZ",
               "[)>\x1e01\x1d96ABC123\x1d826\x1d066\x1dINTL\x1d\x1e\x04")
# Every code set, the shifts and latches between them, and a Numeric Shift.
for mode in (4, 5, 6):
    reads_maxicode(f"mode {mode} carries upper and lower case, digits and "
                   "Latin-1",
                   f"^XA^PW700^LL700^FO60,60^BD{mode}^FH"
                   "^FDCaf_E9 Stra_DFe 123456789 {x} _A9_B1_80_04^FS^XZ",
                   "Café Straße 123456789 {x} ©±\x80\x04")
reads_maxicode("a symbol that is one of a set still reads as its own message",
               "^XA^PW700^LL700^FO60,60^BD4,2,3^FDSECOND OF THREE^FS^XZ",
               "SECOND OF THREE")
# What a save writes has to be what was read: the round trip, read back.
from zplcore import parser as _parser
_saved = _parser.parse_zpl(
    f"^XA^PW700^LL700^FO60,60^BD^FH^FD{_UPS}^FS^XZ")[0].to_zpl()
reads_maxicode("and it reads the same after the designer has saved it",
               _saved, _UPS_READ)

# --- ^BR, the six of twelve that are drawn ----------------------------------

reads("^BR type 7 is a UPC-A, composite half and all left off",
      "^XA^PW900^LL400^FO60,60^BRN,7,3,2,100^FD12345678901|composite^FS^XZ",
      "0123456789012", 'EAN13')
reads("^BR type 8 is a UPC-E",
      "^XA^PW900^LL400^FO60,60^BRN,8,3,2,100^FD4210000526^FS^XZ",
      "0042100005264", 'UPCE')
reads("^BR type 9 is an EAN-13",
      "^XA^PW900^LL400^FO60,60^BRN,9,3,2,100^FD400638133393^FS^XZ",
      "4006381333931", 'EAN13')
reads("^BR type 10 is an EAN-8",
      "^XA^PW900^LL400^FO60,60^BRN,10,3,2,100^FD9638507^FS^XZ",
      "96385074", 'EAN8')
# Types 11 and 12 are GS1-128, and the FNC1 in front is the whole difference:
# a reader reports the application identifier rather than bare digits.
reads("^BR type 11 is a GS1-128, read as application identifiers",
      "^XA^PW900^LL400^FO60,60^BRN,11,3,2,100^FD0112345678901231^FS^XZ",
      "(01)12345678901231", 'Code128')
reads("^BR type 12 is the same, with a different composite it does not draw",
      "^XA^PW900^LL400^FO60,60^BRN,12,3,2,100^FD0112345678901231^FS^XZ",
      "(01)12345678901231", 'Code128')
check("and a plain ^BC of the same digits is not a GS1 symbol",
      decoded("^XA^PW900^LL400^FO60,60^BY3^BCN,100,Y,N,N,A"
              "^FD0112345678901231^FS^XZ")[0][1] == "0112345678901231",
      decoded("^XA^PW900^LL400^FO60,60^BY3^BCN,100,Y,N,N,A"
              "^FD0112345678901231^FS^XZ"))

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
