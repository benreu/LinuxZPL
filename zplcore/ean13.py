"""EAN-13 barcode encoder - returns module widths for drawing."""

from itertools import groupby

from . import code128

# Each digit is seven modules. A and B are the left-hand digits' two parity
# patterns (odd and even); C is the right-hand pattern, used for all six of
# those regardless of parity. Shared with ean_ext.py, which draws the same
# digits' shapes for a UPC/EAN add-on.
CODES = {
    'A': ("0001101", "0011001", "0010011", "0111101", "0100011",
          "0110001", "0101111", "0111011", "0110111", "0001011"),
    'B': ("0100111", "0110011", "0011011", "0100001", "0011101",
          "0111001", "0000101", "0010001", "0001001", "0010111"),
    'C': ("1110010", "1100110", "1101100", "1000010", "1011100",
          "1001110", "1010000", "1000100", "1001000", "1110100"),
}
# Which of A/B each of the six left-hand digits uses, keyed by the leading
# (13th) digit - the parity pattern is how that digit is encoded at all,
# EAN-13 having no module width of its own to spare for it.
_LEFT_PARITY = ("AAAAAA", "AABABB", "AABBAB", "AABBBA", "ABAABB",
                "ABBAAB", "ABBBAA", "ABABAB", "ABABBA", "ABBABA")
EDGE = "101"
MIDDLE = "01010"


def fit(data: str, n: int) -> str:
    """`data`'s digits, to exactly `n` of them.

    Longer is truncated to the last n - the most recently given, and
    presumably most significant - and shorter is padded with zeros on the
    left. Both are ZPL's own words for how EAN-13 and its extensions treat
    field data that is the wrong length.
    """
    digits = ''.join(ch for ch in data if ch.isdigit())
    return digits[-n:].rjust(n, '0')


def normalize(data: str) -> str:
    """The 12-digit value EAN-13 actually carries, plus its check digit."""
    base = fit(data, 12)
    return base + code128.ucc_check_digit(base)


def bits_to_modules(bits: str) -> list:
    """A string of 0s and 1s to run-length module widths, starting with a bar.

    EAN's own guard and digit patterns often start with a space, not a bar,
    so a zero-width bar is inserted first when they do - it draws nothing,
    but keeps bars at the even indices every barcode in this designer is
    drawn by.
    """
    runs = [len(list(group)) for _, group in groupby(bits)]
    if bits[0] == '0':
        runs.insert(0, 0)
    return runs


def encode(data: str) -> list:
    """Module widths for an EAN-13 barcode, alternating bar/space.

    `data` is fit to 12 digits and given a check digit exactly as
    `normalize` does, so a caller does not have to normalize it first.
    """
    full = normalize(data)
    bits = EDGE
    parity = _LEFT_PARITY[int(full[0])]
    for code, digit in zip(parity, full[1:7]):
        bits += CODES[code][int(digit)]
    bits += MIDDLE
    for digit in full[7:13]:
        bits += CODES['C'][int(digit)]
    bits += EDGE
    return bits_to_modules(bits)
