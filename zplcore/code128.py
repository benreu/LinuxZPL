"""Code 128 barcode encoder - returns module widths for drawing."""

# Each entry: [bar1, space1, bar2, space2, bar3, space3] widths in modules.
# Indices 0-102 are data characters (Code 128B value = ASCII - 32).
# Indices 103-105 are Start A, Start B, Start C.
_TABLE = [
    [2,1,2,2,2,2],[2,2,2,1,2,2],[2,2,2,2,2,1],[1,2,1,2,2,3],  # 0-3
    [1,2,1,3,2,2],[1,3,1,2,2,2],[1,2,2,2,1,3],[1,2,2,3,1,2],  # 4-7
    [1,3,2,2,1,2],[2,2,1,2,1,3],[2,2,1,3,1,2],[2,3,1,2,1,2],  # 8-11
    [1,1,2,2,3,2],[1,2,2,1,3,2],[1,2,2,2,3,1],[1,1,3,2,2,2],  # 12-15
    [1,2,3,1,2,2],[1,2,3,2,2,1],[2,2,3,2,1,1],[2,2,1,1,3,2],  # 16-19
    [2,2,1,2,3,1],[2,1,3,2,1,2],[2,2,3,1,1,2],[3,1,2,1,3,1],  # 20-23
    [3,1,1,2,2,2],[3,2,1,1,2,2],[3,2,1,2,2,1],[3,1,2,2,1,2],  # 24-27
    [3,2,2,1,1,2],[3,2,2,2,1,1],[2,1,2,1,2,3],[2,1,2,3,2,1],  # 28-31
    [2,3,2,1,2,1],[1,1,1,3,2,3],[1,3,1,1,2,3],[1,3,1,3,2,1],  # 32-35
    [1,1,2,3,1,3],[1,3,2,1,1,3],[1,3,2,3,1,1],[2,1,1,3,1,3],  # 36-39
    [2,3,1,1,1,3],[2,3,1,3,1,1],[1,1,2,1,3,3],[1,1,2,3,3,1],  # 40-43
    [1,3,2,1,3,1],[1,1,3,1,2,3],[1,1,3,3,2,1],[1,3,3,1,2,1],  # 44-47
    [3,1,3,1,2,1],[2,1,1,3,3,1],[2,3,1,1,3,1],[2,1,3,1,1,3],  # 48-51
    [2,1,3,3,1,1],[2,1,3,1,3,1],[3,1,1,1,2,3],[3,1,1,3,2,1],  # 52-55
    [3,3,1,1,2,1],[3,1,2,1,1,3],[3,1,2,3,1,1],[3,3,2,1,1,1],  # 56-59
    [3,1,4,1,1,1],[2,2,1,4,1,1],[4,3,1,1,1,1],[1,1,1,2,2,4],  # 60-63
    [1,1,1,4,2,2],[1,2,1,1,2,4],[1,2,1,4,2,1],[1,4,1,1,2,2],  # 64-67
    [1,4,1,2,2,1],[1,1,2,2,1,4],[1,1,2,4,1,2],[1,2,2,1,1,4],  # 68-71
    [1,2,2,4,1,1],[1,4,2,1,1,2],[1,4,2,2,1,1],[2,4,1,2,1,1],  # 72-75
    [2,2,1,1,1,4],[4,1,3,1,1,1],[2,4,1,1,1,2],[1,3,4,1,1,1],  # 76-79
    [1,1,1,2,4,2],[1,2,1,1,4,2],[1,2,1,2,4,1],[1,1,4,2,1,2],  # 80-83
    [1,2,4,1,1,2],[1,2,4,2,1,1],[4,1,1,2,1,2],[4,2,1,1,1,2],  # 84-87
    [4,2,1,2,1,1],[2,1,2,1,4,1],[2,1,4,1,2,1],[4,1,2,1,2,1],  # 88-91
    [1,1,1,1,4,3],[1,1,1,3,4,1],[1,3,1,1,4,1],[1,1,4,1,1,3],  # 92-95
    [1,1,4,3,1,1],[4,1,1,1,1,3],[4,1,1,3,1,1],[1,1,3,1,4,1],  # 96-99
    [1,1,4,1,3,1],[3,1,1,1,4,1],[4,1,1,1,3,1],                 # 100-102
    [2,1,1,4,1,2],[2,1,1,2,1,4],[2,1,1,2,3,2],                 # 103 StartA, 104 StartB, 105 StartC
]
_STOP = [2,3,3,1,1,1,2]  # 7 elements, 13 modules


def encode_b(data: str) -> list:
    """Return list of module widths for a Code 128B barcode.

    The list alternates bar/space starting with a bar.
    """
    syms = [104]  # Start B
    for ch in data:
        v = ord(ch) - 32
        syms.append(max(0, min(v, 94)))

    # Check character: Start_B + sum(i * value_i) mod 103
    check = syms[0]
    for i, v in enumerate(syms[1:], start=1):
        check += i * v
    syms.append(check % 103)

    mods = []
    for s in syms:
        mods.extend(_TABLE[s])
    mods.extend(_STOP)
    return mods


# Symbol values that are not data
_START_B, _START_C = 104, 105
_CODE_C, _CODE_B = 99, 100


def _symbols(data: str, mode: str) -> list:
    """The Code 128 symbol values for `data`, subset switching if asked.

    Mode A is ZPL's automatic mode: the printer moves into subset C across runs
    of digits, where one symbol carries two of them, and back to B for anything
    else. It is the difference between a numeric barcode being its stated width
    and being twice it, so the designer has to make the same choice.
    """
    if mode.upper() != 'A':
        return [_START_B] + [max(0, min(ord(ch) - 32, 94)) for ch in data]

    syms = []
    i = 0
    in_c = False
    while i < len(data):
        run = _digit_run(data, i)
        # Four digits is where subset C starts paying for the switch symbol it
        # costs; at the very start the start code is free, so an all-numeric
        # value goes straight into C. Switching for a shorter run makes the
        # barcode wider, not narrower. Zebra does not publish its exact
        # threshold, so this is the conservative reading of it.
        want_c = run >= 4 or (not syms and run >= 2 and run == len(data))
        if want_c:
            pairs = run // 2
            if not syms:
                syms.append(_START_C)
            elif not in_c:
                syms.append(_CODE_C)
            in_c = True
            for p in range(pairs):
                syms.append(int(data[i + 2 * p:i + 2 * p + 2]))
            i += pairs * 2
            continue
        if not syms:
            syms.append(_START_B)
        elif in_c:
            syms.append(_CODE_B)
        in_c = False
        syms.append(max(0, min(ord(data[i]) - 32, 94)))
        i += 1

    return syms or [_START_B]


def _digit_run(data: str, start: int) -> int:
    """How many digits follow, from `start`."""
    end = start
    while end < len(data) and data[end].isdigit():
        end += 1
    return end - start


def _modules(syms: list) -> list:
    """Symbol values to the bar and space widths that draw them."""
    check = syms[0]
    for i, value in enumerate(syms[1:], start=1):
        check += i * value
    mods = []
    for value in syms + [check % 103]:
        mods.extend(_TABLE[value])
    mods.extend(_STOP)
    return mods


def encode(data: str, mode: str = 'N') -> list:
    """Module widths for a Code 128 barcode, alternating bar/space.

    The width of a barcode is the sum of these, so nothing else needs a formula
    for it - which matters because no formula covers subset C, where two digits
    share one symbol.
    """
    return _modules(_symbols(data or "", mode or 'N'))


def ucc_check_digit(data: str) -> str:
    """The UCC/EAN mod-10 check digit for a numeric string.

    Odd positions counted from the right weigh three, even ones weigh one, and
    the digit is whatever takes the total to a multiple of ten.
    """
    digits = [int(c) for c in data if c.isdigit()]
    total = sum(d * (3 if i % 2 == 0 else 1)
                for i, d in enumerate(reversed(digits)))
    return str((10 - total % 10) % 10)
