"""
Printer -> Status... - what the printer itself reports about its own state,
parsed into named figures rather than left as the comma-separated fields the
ZPL manual documents.

This is the diagnostic counterpart to the three managers that list what a
printer is *holding* (zplcore/fonts.py, graphic_store.py, printer_objects.py).
They answer "is my font there"; this answers "why did that not print" - out of
media, paused, over temperature, no memory left, or a half-finished format
still sitting in the receive buffer.

Two facts from the ZPL manual shape everything below, and both are the reason
this module exists rather than the Console being considered enough:

**~HS answers nothing at all in five of the states worth asking about.** The
manual is explicit: the printer sends no response to ~HS when it is in MEDIA
OUT, RIBBON OUT, HEAD OPEN, REWINDER FULL or HEAD OVER-TEMPERATURE. Those are
precisely the faults a status panel exists to explain, so a panel built on ~HS
alone goes blank exactly when something is wrong. ~HQES and the Set/Get/Do
attribute zpl.system_status carry no such restriction, so the error status is
asked *first* (see query_printer_activity) and ~HS's silence is then reported
as a finding in its own right, with the flags saying which of the five it was.

**There is no printhead temperature anywhere in ZPL.** No getvar reports one;
~HD's reply is documented only as a picture of a terminal, with no field
layout to parse; ~HB gives a head *voltage* and a battery temperature, and
only on mobile printers. Temperature is available solely as the under/over
*flags* of ~HS and ~HQES, so that is what this reports. It does not
interpolate a number from a flag.

Everything here is parsed from text and returns plain data, so each parser can
be checked against the manual's own worked examples without a printer (see
tests/test_core.py). The queries follow the same None-vs-empty contract the
rest of this layer uses: None means the printer could not be asked, an empty
or partial report means it was asked and answered that way.
"""

import re
from typing import Dict, List, NamedTuple, Optional, Set, Tuple

from . import printer_io
from .fonts import DOTS_PER_MM_TO_DPI
from .graphic_store import DEVICE_NAMES

# Every device ^HW will list, the same tuple printer_objects asks - a memory
# report that left one out would understate what the printer is holding.
DEVICES = ('R', 'E', 'B', 'A', 'Z')

# ~HQ wraps its whole reply in a bare STX/ETX pair, with CR LF between lines.
# Those bytes are framing, not content, and would otherwise reach a text
# widget as control characters.
_FRAMING = '\x02\x03'


# --- what the flags mean ----------------------------------------------------
# Transcribed from the ZPL manual's Error Flags and Warning Flags tables for
# ~HQES, which zpl.system_status reports the same bits for. Each key is
# (nibble, value): group 1 of the reply is eight hex nibbles counted from the
# right, so nibble 1 is the last character, and a nibble holds the OR of its
# own flags' values. Kept as a table rather than folded into the parser so it
# can be read against the manual without reading any code.
#
# The KR403 entries are marked because that printer is a kiosk unit almost
# nobody using this designer has: naming them anyway costs nothing and means a
# flag is never reported as a bare hex digit, but the marking stops a reader
# wondering why their own printer never shows them.
ERROR_FLAGS: Dict[Tuple[int, int], str] = {
    (1, 1): "Media out",
    (1, 2): "Ribbon out",
    (1, 4): "Head open",
    (1, 8): "Cutter fault",
    (2, 1): "Printhead over temperature",
    (2, 2): "Motor over temperature",
    (2, 4): "Bad printhead element",
    (2, 8): "Printhead detection error",
    (3, 1): "Invalid firmware configuration",
    (3, 2): "Printhead thermistor open",
    (4, 1): "Paper jam during retract (KR403)",
    (4, 2): "Presenter not running (KR403)",
    (4, 4): "Paper feed error (KR403)",
    (4, 8): "Clear paper path failed (KR403)",
    (5, 1): "Paused (KR403)",
    (5, 2): "Retract function timed out (KR403)",
    (5, 4): "Black mark calibrate error (KR403)",
    (5, 8): "Black mark not found (KR403)",
}

WARNING_FLAGS: Dict[Tuple[int, int], str] = {
    (1, 1): "Needs media calibration",
    (1, 2): "Clean printhead",
    (1, 4): "Replace printhead",
    (1, 8): "Paper near end (KR403)",
    (2, 1): "Sensor 1, paper before head (KR403)",
    (2, 2): "Sensor 2, black mark (KR403)",
    (2, 4): "Sensor 3, paper after head (KR403)",
    (2, 8): "Sensor 4, loop ready (KR403)",
    (3, 1): "Sensor 5, presenter (KR403)",
    (3, 2): "Sensor 6, retract ready (KR403)",
    (3, 4): "Sensor 7, in retract (KR403)",
    (3, 8): "Sensor 8, at bin (KR403)",
}

# The five states in which the manual says ~HS is not answered at all. Named
# here, in the order the manual lists them, because the sentence explaining
# ~HS's silence has to name them and that sentence is built once (see
# silence_note) rather than in each frontend.
HOST_STATUS_SILENT_STATES = ("media is out", "the ribbon is out",
                             "the head is open", "the rewinder is full",
                             "the head is over temperature")

def silence_note(faults_named: bool) -> str:
    """Why ~HS answered nothing, phrased for whichever case this is.

    The "which one" half is conditional, and deliberately so: of the five
    states that silence ~HS, **a full rewinder has no flag anywhere**. It is
    absent from the error table and from the warning table alike, so a printer
    silenced by one reports silence and all-zero flags together. Claiming the
    flags say which would, in exactly that case, send a user looking for a
    fault the printer never reported - so the claim is only made when a flag
    actually was raised.
    """
    states = (", ".join(HOST_STATUS_SILENT_STATES[:-1])
              + " or " + HOST_STATUS_SILENT_STATES[-1])
    note = ("The printer did not answer the status query. It does not answer "
            f"one when {states}, so the silence is itself a sign that one of "
            "those is the problem")
    if faults_named:
        return note + " - and the faults above say which."
    # No flag was raised, so a full rewinder is the one candidate left that
    # could produce this and report nothing: worth naming, since it is the
    # only state here with no flag of its own.
    return (note + ". No fault flag came back with it, which a full rewinder "
            "alone would do - it is the one of the five the printer has no "
            "flag for.")

# ~HS string 2's print mode field. K and S are letters, not digits, so this is
# keyed by the character as it arrives rather than by an int.
PRINT_MODES = {'0': "Rewind", '1': "Peel-off", '2': "Tear-off", '3': "Cutter",
               '4': "Applicator", '5': "Delayed cut", '6': "Linerless peel",
               '7': "Linerless rewind", '8': "Partial cutter", '9': "RFID",
               'K': "Kiosk", 'S': "Stream"}


def _flags_from_group(group: str, table: Dict[Tuple[int, int], str]) -> Set[str]:
    """The names every raised bit of one eight-nibble hex group stands for.

    Nibble 1 is the rightmost character, so the string is walked from the end.
    A bit with no entry in `table` is ignored rather than reported as a
    number: the manual leaves parts of these groups unassigned, and a printer
    raising one of those says nothing this could usefully name.
    """
    names = set()
    for position, char in enumerate(reversed(group.strip()), start=1):
        try:
            nibble = int(char, 16)
        except ValueError:
            continue
        for value in (1, 2, 4, 8):
            if nibble & value:
                name = table.get((position, value))
                if name:
                    names.add(name)
    return names


# --- parsers ----------------------------------------------------------------
# Each takes the decoded reply text and returns plain data, or None when the
# reply is not the shape the manual documents. None here means "this command
# did not answer usefully", which a query turns into an entry on
# StatusReport.unanswered - never into a fabricated figure.

class ErrorStatus(NamedTuple):
    """What zpl.system_status or ~HQES reported. `paused` is the leading flag,
    which is a state rather than a fault and so is kept apart from `errors`."""
    paused: bool
    errors: Set[str]
    warnings: Set[str]


_SYSTEM_STATUS_CSV = re.compile(
    r'(\d)\s*,\s*(\d)\s*,\s*([0-9A-Fa-f]{8})\s*,\s*([0-9A-Fa-f]{8})\s*,'
    r'\s*(\d)\s*,\s*([0-9A-Fa-f]{8})\s*,\s*([0-9A-Fa-f]{8})')
_HQES_LINE = re.compile(
    r'^\s*(ERRORS|WARNINGS)\s*:\s*(\d)\s+([0-9A-Fa-f]{8})\s+([0-9A-Fa-f]{8})',
    re.M | re.I)


def parse_error_status(text: str) -> Optional[ErrorStatus]:
    """The printer's error and warning flags, from either reply that carries
    them.

    Two formats, one parser, because the two commands report the same bits and
    which one a printer speaks is a question of its age (zpl.system_status is
    Link-OS; ~HQES is the fallback for firmware without it):

    - `zpl.system_status` answers CSV -
      `flag,error flag,error group 2,error group 1,warning flag,warning group
      2,warning group 1` - which is why it is preferred: it is unambiguous.
    - `~HQES` answers a formatted block with `ERRORS:` and `WARNINGS:` lines,
      each `<flag> <group 2> <group 1>`.

    Group 2 (nibbles 16-9) is documented as always zero and is parsed anyway,
    so a printer that does raise something there is not silently ignored.

    Returns None for a reply in neither shape - notably the bare `?` the
    printer answers for an attribute it does not have, which is exactly how an
    older printer refuses zpl.system_status and the signal to try ~HQES.
    """
    text = text.strip(_FRAMING + ' \r\n\t')
    if not text:
        return None

    csv = _SYSTEM_STATUS_CSV.search(text)
    if csv is not None:
        paused = csv.group(1) == '1'
        errors = (_flags_from_group(csv.group(3), ERROR_FLAGS)
                  | _flags_from_group(csv.group(4), ERROR_FLAGS))
        warnings = (_flags_from_group(csv.group(6), WARNING_FLAGS)
                    | _flags_from_group(csv.group(7), WARNING_FLAGS))
        return ErrorStatus(paused, errors, warnings)

    found = False
    errors: Set[str] = set()
    warnings: Set[str] = set()
    for match in _HQES_LINE.finditer(text):
        found = True
        table = (ERROR_FLAGS if match.group(1).upper() == 'ERRORS'
                 else WARNING_FLAGS)
        names = (_flags_from_group(match.group(3), table)
                 | _flags_from_group(match.group(4), table))
        (errors if table is ERROR_FLAGS else warnings).update(names)
    if not found:
        return None
    # ~HQES has no pause flag of its own - the KR403-only "Paused" error bit is
    # the nearest thing, and is reported as the error it is listed as rather
    # than promoted to this field, which only zpl.system_status fills.
    return ErrorStatus(False, errors, warnings)


class HostStatus(NamedTuple):
    """~HS's three strings, named. Every flag is already a bool and every
    count an int, so no caller re-reads the manual's field letters."""
    paper_out: bool
    paused: bool
    label_length: int          # dots
    formats_in_buffer: int
    buffer_full: bool
    diagnostic_mode: bool
    partial_format: bool
    corrupt_ram: bool
    under_temperature: bool
    over_temperature: bool
    head_up: bool
    ribbon_out: bool
    thermal_transfer: bool
    print_mode: str            # as PRINT_MODES names it
    label_waiting: bool
    labels_remaining: int
    images_stored: int
    continuous_media: bool
    static_ram: bool


def _fields(line: str) -> List[str]:
    return [f.strip() for f in line.strip(_FRAMING + ' \r\n\t').split(',')]


def _flag(fields: List[str], index: int) -> bool:
    return index < len(fields) and fields[index] == '1'


def _count(fields: List[str], index: int) -> int:
    if index >= len(fields):
        return 0
    try:
        return int(fields[index])
    except ValueError:
        return 0


def parse_host_status(text: str) -> Optional[HostStatus]:
    """~HS's reply, or None if it is not the three strings it documents.

    The reply is three `<STX>...<ETX><CR><LF>` strings whose fields the manual
    names by letter: string 1 is `aaa,b,c,dddd,eee,f,g,h,iii,j,k,l`, string 2
    `mmm,n,o,p,q,r,s,t,uuuuuuuu,v,www`, string 3 `xxxx,y`. Only strings 1 and 2
    carry anything worth showing; string 3 is a password and whether static RAM
    is fitted.

    All three are required. A reply of fewer lines is a truncated one - the read
    ended on a timeout mid-answer - and returning a half-filled record from it
    would put zeros on the panel indistinguishable from real ones, so it is
    refused outright and reported as unanswered instead.

    `aaa` and `mmm` are three-digit decimals of packed bit fields (baud rate,
    parity, media type, print method). Only one bit is unpacked: `m7`, die-cut
    versus continuous media. `m0` is not, because it reports the print method
    that string 2's own `q` field already gives as a plain flag. The serial-line
    settings of `aaa` are deliberately left alone: this app only ever talks to a
    printer over TCP, so its baud rate and parity explain nothing about a label
    that printed wrong.

    `v`, format-while-printing, is documented as always 1 and so is not
    reported - a field that cannot vary is not a diagnostic.
    """
    lines = [line for line in text.replace('\x02', '\n').splitlines()
             if line.strip(_FRAMING + ' \r\n\t')]
    if len(lines) < 3:
        return None
    first, second = _fields(lines[0]), _fields(lines[1])
    third = _fields(lines[2])
    # String 1 has twelve fields and string 2 eleven; anything shorter is not
    # this reply, whatever else it may be.
    if len(first) < 12 or len(second) < 11:
        return None

    function_settings = _count(second, 0)
    mode_char = (second[5] or '0').upper()
    return HostStatus(
        paper_out=_flag(first, 1),
        paused=_flag(first, 2),
        label_length=_count(first, 3),
        formats_in_buffer=_count(first, 4),
        buffer_full=_flag(first, 5),
        diagnostic_mode=_flag(first, 6),
        partial_format=_flag(first, 7),
        corrupt_ram=_flag(first, 9),
        under_temperature=_flag(first, 10),
        over_temperature=_flag(first, 11),
        head_up=_flag(second, 2),
        ribbon_out=_flag(second, 3),
        thermal_transfer=_flag(second, 4),
        print_mode=PRINT_MODES.get(mode_char, mode_char),
        label_waiting=_flag(second, 7),
        labels_remaining=_count(second, 8),
        images_stored=_count(second, 10),
        # m7 is the high bit of the three-digit packed value, m0 the low one.
        continuous_media=bool(function_settings & 0x80),
        static_ram=_flag(third, 1),
    )


class Identification(NamedTuple):
    """~HI's reply. `dpi` is derived; `memory_kb` is as reported."""
    model: str
    firmware: str
    dpi: Optional[int]
    memory_kb: Optional[int]
    options: str


def parse_host_identification(text: str) -> Optional[Identification]:
    """~HI's `XXXXXX,V1.0.0,dpm,000KB,X` - model, firmware, head resolution in
    dots per mm, memory, and whatever options the printer recognises.

    The memory field is documented in **kilobytes** despite the manual's prose
    calling it a memory size in MB, and is reported in the unit it arrives in
    rather than converted: a figure relabelled by this app is a figure a user
    cannot check against their own configuration label.

    dots/mm is mapped through fonts.DOTS_PER_MM_TO_DPI, the same table
    fonts.query_printer_dpi already uses, so a resolution means the same thing
    wherever it is read. An unrecognised value leaves `dpi` None rather than
    guessing - the same rule that query_printer_dpi follows.
    """
    text = text.strip(_FRAMING + ' \r\n\t')
    if not text:
        return None
    fields = _fields(text.splitlines()[0])
    if len(fields) < 3:
        return None
    dpi = None
    try:
        dpi = DOTS_PER_MM_TO_DPI.get(int(fields[2]))
    except ValueError:
        pass
    memory_kb = None
    if len(fields) > 3:
        digits = re.match(r'(\d+)', fields[3])
        if digits:
            memory_kb = int(digits.group(1))
    return Identification(model=fields[0], firmware=fields[1], dpi=dpi,
                          memory_kb=memory_kb,
                          options=','.join(fields[4:]).strip())


def parse_ram_status(text: str) -> Optional[Tuple[int, int, int]]:
    """~HM's `1024,0780,0780` - total RAM installed, maximum available to the
    user, and currently available, all in kilobytes.

    Only the third figure moves: the manual notes the first two do not change
    once the printer is powered on, and that a downloaded graphic, font or
    saved bitmap comes out of the available figure. That is why this is the one
    memory reading worth re-polling, and why the panel shows all three - a
    "free" number means nothing without the total it is free out of.
    """
    text = text.strip(_FRAMING + ' \r\n\t')
    if not text:
        return None
    fields = _fields(text.splitlines()[0])
    if len(fields) < 3:
        return None
    try:
        return (int(fields[0]), int(fields[1]), int(fields[2]))
    except ValueError:
        return None


# The footer every ^HW listing ends with. The manual's formal shape is
# `-xxxxxxx bytes free`, but real hardware adds the drive and its own name for
# the memory - `-  66119680 bytes free E: ONBOARD FLASH` - so both are matched
# and the trailing description is kept when it is there. All three of this
# app's existing ^HW parsers throw this line away; it is the only per-drive
# free-space figure ZPL offers.
_BYTES_FREE = re.compile(r'-\s*(\d+)\s+bytes\s+free\s*([^\r\n]*)', re.I)


def parse_free_space(text: str) -> Optional[Tuple[int, str]]:
    """The free bytes a ^HW listing reports, with the printer's own
    description of that memory, or None if the footer is absent."""
    match = _BYTES_FREE.search(text)
    if match is None:
        return None
    return (int(match.group(1)), match.group(2).strip())


def _object_count(text: str) -> int:
    """How many objects a ^HW listing named.

    Counted from the `*`-prefixed entry lines the command documents, rather
    than by matching object names: printer_objects already owns that regex and
    a second copy here would be a second thing to keep in step. A count is all
    this needs - the Objects manager is where the names belong.
    """
    return sum(1 for line in text.splitlines()
               if line.lstrip().startswith('*'))


_METER_LINE = re.compile(r'^\s*([A-Z][A-Z0-9 ./-]+?)\s*:\s*(\d+)\s*(\S*)\s*$',
                         re.M)


def parse_getvar(text: str) -> Optional[str]:
    """One Set/Get/Do attribute's value, or None if the printer has not got it.

    The manual's own examples show these replies wrapped in double quotes -
    `"00 days 02 hours 45 mins 30 secs"`, `"8560 INCHES, 21744 CENTIMETERS"` -
    so the quotes are stripped here rather than in each caller.

    `?` is the documented answer for a setting that *"does not exist"* or *"has
    not been configured yet"*, and is reported as None. That distinction is what
    makes an attribute cheap to try: a printer without one refuses it
    immediately, where an unsupported ~HQ sub-command is simply ignored and
    costs the full read timeout instead. It is also why a refusal must not read
    as a value - `?` shown as an odometer reading would be nonsense.
    """
    text = text.strip(_FRAMING + ' \r\n\t').strip('"').strip()
    if not text or text == '?':
        return None
    return ' '.join(text.split())


def parse_meters(text: str) -> Optional[List[Tuple[str, str]]]:
    """The `LABEL: <count> <unit>` lines of ~HQOD or ~HQPH, in the order they
    arrive, as `(label, "<count> <unit>")` pairs.

    Units are whatever the printer gives - `"` for inches, `cm`, `M` - because
    which one it uses is a printer setting (^MA's units parameter) and
    converting would report a figure the printer's own front panel disagrees
    with. Read as lines rather than to a fixed schema on purpose: ~HQOD grows
    two extra counters when Early Warning Maintenance is on, and ~HQPH's head
    life history is a numbered list whose length depends on how many times the
    head has been changed, so a parser pinned to a field count would drop
    exactly the history a worn printer has to show.
    """
    text = text.strip(_FRAMING)
    pairs = [(m.group(1).title(), f"{m.group(2)} {m.group(3)}".strip())
             for m in _METER_LINE.finditer(text)]
    return pairs or None


# --- the report -------------------------------------------------------------
# A presentation-neutral shape, the same way fonts.FontScanReport carries the
# Local Fonts... diagnostic: the frontends lay this out and colour it, and
# decide nothing about it.

# What a reading means, for a frontend to mark however its toolkit does. The
# judgment is made here, once, rather than in each frontend - two frontends
# deciding separately what counts as bad is precisely the drift that moved
# workflow.listing_status into the core.
PLAIN, OK, WARN, ERROR = '', 'ok', 'warn', 'error'


class Reading(NamedTuple):
    """One labelled figure. `value` is already formatted for display, so a
    frontend never has to decide how a number is written.

    `fraction` is how full the thing being reported is, 0.0 to 1.0, for a
    reading a bar says more about than a number - so far the two memories. It
    is the proportion **in use**, not free, so the bar fills as the memory
    fills, the way a disk gauge does. Computed here rather than in each
    frontend for the same reason `level` is: two frontends dividing separately
    is two bars that can disagree. None means this reading has no bar, which is
    most of them.
    """
    label: str
    value: str
    level: str = PLAIN
    fraction: Optional[float] = None


class Section(NamedTuple):
    """A group of readings under a heading. `note` is a sentence explaining
    something the readings cannot say themselves - so far, why ~HS was
    silent."""
    title: str
    readings: List[Reading]
    note: str = ''


class StatusReport(NamedTuple):
    """`unanswered` names the commands that were sent and gave nothing, so a
    partial report is presented as partial rather than as complete. It is not
    an error in itself: ~HS is silent by design in five states (see
    silence_note), and a printer old enough to lack an SGD attribute simply
    does not answer that one."""
    sections: List[Section]
    unanswered: List[str]


def _thousands(number: int) -> str:
    return f"{number:,}"


_LEADING_DIGITS = re.compile(r'^\s*(\d+)')

# A count followed by nothing, or by the printer's own byte wording -
# `66369536`, `67108864 Bytes`, `66369536 Bytes Free`. A trailing unit
# that is *not* bytes must not match, or a figure stated in some other
# unit would be relabelled as bytes.
_BYTE_COUNT = re.compile(r'^\s*(\d+)\s*(?:bytes?\s*)?(?:free\s*)?$', re.I)


def _leading_int(value: Optional[str]) -> Optional[int]:
    """The count at the front of a printer's reply, ignoring its own wording."""
    match = _LEADING_DIGITS.match(value or '')
    return int(match.group(1)) if match else None


def _usage(free: Optional[str], total: Optional[str]) -> Optional[float]:
    """How much of a memory is in use, 0.0 to 1.0, or None if it cannot be
    worked out.

    Both figures come as the printer worded them, in whatever unit it chose, so
    only their leading counts are used - and only when they share a reply, so
    the ratio is unit-free and a printer reporting KB is not divided by one
    reporting bytes. A free figure larger than the total is clamped rather than
    trusted: it would put a bar past its own end, and a printer that reports it
    is wrong about one of the two, not about the ratio.
    """
    free_count, total_count = _leading_int(free), _leading_int(total)
    if free_count is None or not total_count:
        return None
    return max(0.0, min(1.0, (total_count - free_count) / total_count))


def _bytes(value: str) -> str:
    """A byte count with its digits grouped and its unit named once.

    The count is taken from the front and the rest of the reply discarded,
    because a real printer answers memory.flash_free with its own wording -
    `66369536 Bytes Free`, not the bare number the manual's format line implies.
    Kept verbatim, that wording collides with the sentence it gets put in and
    reads "66369536 Bytes Free free of ...". Only the number is dependable, so
    only the number is used, and the unit is said once here.

    A value with no leading count is passed through untouched rather than
    relabelled - guessing a unit onto something unrecognised is how a panel
    starts lying.
    """
    count = _BYTE_COUNT.match(value or '')
    return f"{int(count.group(1)):,} bytes" if count else value


class _Session:
    """One run of queries over one connection's worth of commands, keeping
    track of which answered.

    Encodes the reachability rule the three device queries already share (see
    printer_objects.query_printer_objects): a failure to *connect* on the very
    first command is decisive and stops the run, so a dead host costs one
    attempt rather than one timeout per command. After that, silence is a
    weaker signal - a command this printer's firmware does not implement looks
    exactly like one it chose not to answer - so it is recorded and the run
    continues. printer_io.Cancelled is not an OSError and so travels straight
    out, which is what lets a dialog's Cancel abandon a half-finished report.
    """

    def __init__(self, address, port, timeout, cancel):
        self.address, self.port = address, port
        self.timeout, self.cancel = timeout, cancel
        self.unanswered: List[str] = []
        self.answered = False
        self.dead = False

    def ask(self, name: str, payload: bytes,
            record: bool = True) -> Optional[str]:
        """Send one command and return its reply, or None.

        `record=False` is for a command that has a fallback: the caller decides
        whether the *thing wanted* went unanswered once it has tried them all,
        rather than every attempt leaving its own entry behind.
        """
        if self.dead:
            return None
        try:
            reply = printer_io.send(self.address, self.port, payload,
                                    self.timeout, read_reply=True,
                                    cancel=self.cancel)
        except OSError:
            if not self.answered and not self.unanswered:
                self.dead = True  # the connection itself failed
                return None
            if record:
                self.unanswered.append(name)
            return None
        if not reply:
            if record:
                self.unanswered.append(name)
            return None
        self.answered = True
        return reply.decode('utf-8', errors='replace')


def _error_status(session: _Session) -> Optional[ErrorStatus]:
    """The error and warning flags, from whichever command this printer
    answers.

    zpl.system_status is asked first because it answers CSV rather than a
    formatted block, and ~HQES second because firmware without the Link-OS
    attribute is exactly what the fallback is for. Neither result is remembered
    between calls: which of the two a printer speaks would have to be cached
    per address to be safe, and a cache keyed on the wrong printer reports the
    wrong thing after a session printer change - a saved round trip is not
    worth that. An older printer therefore pays one extra, promptly refused,
    request per refresh.

    The two are recorded as one thing on `unanswered`, under the name of what
    was wanted rather than of either command: a printer that refuses the
    attribute but answers ~HQES has told us everything we asked for, and
    naming the refused half would report a fault where there is none. Only
    both failing means the flags could not be had.
    """
    reply = session.ask('the error status', b'! U1 getvar "zpl.system_status"\r\n',
                        record=False)
    status = parse_error_status(reply) if reply else None
    if status is not None:
        return status
    reply = session.ask('the error status', b'~HQES', record=False)
    status = parse_error_status(reply) if reply else None
    if status is None:
        session.unanswered.append('the error status')
    return status


def _fault_section(status: Optional[ErrorStatus], host_silent: bool) -> Section:
    """The faults, and - when ~HS said nothing - why that silence is itself
    informative rather than a failure to report."""
    readings: List[Reading] = []
    if status is None:
        readings.append(Reading("Faults", "could not be read", WARN))
    else:
        for name in sorted(status.errors):
            readings.append(Reading(name, "yes", ERROR))
        for name in sorted(status.warnings):
            readings.append(Reading(name, "yes", WARN))
        # Pause is a work state, and the Work section reports it from ~HS's
        # own field - so it is only shown here when ~HS said nothing and that
        # section cannot. Otherwise the same fact appears twice, which reads as
        # a bug rather than as corroboration. This is also the one thing the
        # CSV carries that the ~HQES block does not, so when ~HS is silent it
        # is the only way pause is known at all.
        if status.paused and host_silent:
            readings.append(Reading("Paused", "yes", WARN))
        if not readings:
            readings.append(Reading("Faults", "none reported", OK))
    # The note's wording turns on whether a flag was raised, not on how many
    # readings there are: a "none reported" row is not a named fault.
    named = status is not None and bool(status.errors or status.warnings)
    note = silence_note(named) if host_silent else ''
    return Section("Faults", readings, note)


def _work_section(host: Optional[HostStatus]) -> Section:
    """What the printer is doing - the nearest thing a Zebra has to the "is it
    busy or is it stuck" question, since it reports no processor activity of
    any kind."""
    if host is None:
        return Section("Work", [Reading("Work state", "could not be read", WARN)])
    flags = [
        ("Paused", host.paused, WARN),
        ("Receive buffer full", host.buffer_full, ERROR),
        ("Partial format in progress", host.partial_format, WARN),
        # A printer left in communications diagnostics mode (~JD) prints a hex
        # dump of everything it receives instead of labels, which looks like a
        # ruined label rather than like a mode. Worth surfacing loudly.
        ("Communications diagnostic mode", host.diagnostic_mode, WARN),
        ("Label waiting at peeler", host.label_waiting, PLAIN),
        ("Head open", host.head_up, ERROR),
        ("Media out", host.paper_out, ERROR),
        ("Ribbon out", host.ribbon_out, ERROR),
        ("Over temperature", host.over_temperature, ERROR),
        ("Under temperature", host.under_temperature, ERROR),
        ("Configuration data lost", host.corrupt_ram, ERROR),
    ]
    readings = [Reading(label, "yes", level) for label, raised, level in flags
                if raised]
    readings.append(Reading("Formats queued in receive buffer",
                            _thousands(host.formats_in_buffer)))
    readings.append(Reading("Labels remaining in batch",
                            _thousands(host.labels_remaining)))
    readings.append(Reading("Images held in memory",
                            _thousands(host.images_stored)))
    readings.append(Reading("Label length",
                            f"{_thousands(host.label_length)} dots"))
    readings.append(Reading("Print mode", host.print_mode))
    readings.append(Reading("Media", "continuous" if host.continuous_media
                            else "die-cut"))
    readings.append(Reading("Print method", "thermal transfer"
                            if host.thermal_transfer else "direct thermal"))
    return Section("Work", readings)


def _flash_readings(session: _Session) -> List[Reading]:
    """Flash, read as Set/Get/Do attributes.

    Asked this way rather than by reading the free-space footer off a ^HW
    listing per drive, even though that footer is the only per-drive figure ZPL
    has and this app already has three parsers that walk past it. The reason is
    what an absent drive costs: ^HW must be sent per device, and a drive that is
    not fitted answers nothing at all, so probing R:/E:/B:/A:/Z: on a printer
    with two of them spends three full read timeouts to learn that - every
    refresh. An attribute the printer has not got is refused with '?' at once
    (see parse_getvar). ^HW is kept as a fallback and only for E:, for firmware
    without the attributes: one drive, the one that matters.
    """
    readings: List[Reading] = []
    for size_attr, free_attr, name in _MEMORIES:
        size = parse_getvar(session.ask(
            size_attr, f'! U1 getvar "{size_attr}"\r\n'.encode('ascii'),
            record=False) or '')
        free = parse_getvar(session.ask(
            free_attr, f'! U1 getvar "{free_attr}"\r\n'.encode('ascii'),
            record=False) or '')
        if free is None and size is None:
            continue
        value = (f"{_bytes(free)} free of {_bytes(size)}" if free and size
                 else f"{_bytes(free)} free" if free
                 else f"{_bytes(size)} fitted")
        readings.append(Reading(name, value, _memory_level(free, size),
                                _usage(free, size)))

    if not readings:
        reply = session.ask('^HWE:', b'^XA^HWE:*.*^XZ', record=False)
        free = parse_free_space(reply) if reply else None
        if free is not None:
            described = free[1] or f"E: {DEVICE_NAMES['E']}"
            # No bar: ^HW's footer gives what is free but never the total it is
            # free out of, and a bar drawn against a guessed total would be an
            # invented figure rather than a reported one.
            readings.append(Reading(
                described if described.upper().startswith('E:')
                else f"E: {described}",
                f"{_thousands(free[0])} bytes free, "
                f"{_thousands(_object_count(reply))} object(s)"))
    return readings


def _memory_level(free, total) -> str:
    """Marked once less than a tenth is left - the point at which the next font
    or graphic is the one that will not fit."""
    fraction = _usage(free, total)
    return WARN if fraction is not None and fraction > 0.9 else PLAIN


def _memory_section(session: _Session,
                    ram: Optional[Tuple[int, int, int]]) -> Section:
    """Both memories together, each as a bar: RAM from ~HM, Flash from its own
    attributes.

    One section rather than two, and both re-asked on every refresh, because
    both are the same question - how much room is left - and both move for the
    same reason. The manual notes that a downloaded graphic, font or saved
    bitmap comes out of RAM; Flash is where a font this app uploads actually
    lands (fonts.DEFAULT_FONT_DEVICE is E:), so it is the one that drops when
    someone uses Printer -> Fonts. Watching either fill is the point, so
    neither belongs in the half of the report that is asked once.

    RAM is measured against what the manual calls the maximum available to the
    user, not against what is installed: firmware holds some of the latter back
    permanently, so a bar drawn against it would never fill and would read as
    healthier than the printer is. What is installed is still shown, as a plain
    row, since it is the figure on the printer's own configuration label.
    """
    readings: List[Reading] = []
    if ram is not None:
        total, maximum, free = ram
        readings.append(Reading(
            "RAM", f"{_thousands(free)} KB free of {_thousands(maximum)} KB",
            _memory_level(str(free), str(maximum)),
            _usage(str(free), str(maximum))))
    readings.extend(_flash_readings(session))
    if ram is not None:
        readings.append(Reading("RAM installed", f"{_thousands(ram[0])} KB"))
    if not readings:
        readings.append(Reading("Memory", "could not be read", WARN))
    return Section("Memory", readings)


def query_printer_activity(address: str, port: int, timeout: float = 5,
                           cancel=None) -> Optional[StatusReport]:
    """What the printer is doing and what is wrong with it, right now - the
    half of the report worth asking again.

    Three commands, in this order for a reason: **the error flags are asked
    before ~HS**, because ~HS is documented as answering nothing at all in five
    states (media out, ribbon out, head open, rewinder full, head over
    temperature) and those are the states most worth reporting. Asking this way
    round also means the report is built in one forward pass, with no need to
    hold ~HS's silence back and reinterpret it once the flags arrive. Asked the other
    way round, a printer with its head open would produce a blank panel and no
    explanation. Asked this way, the flags name the fault and ~HS's silence
    corroborates it (see silence_note).

    Returns None only if the printer could not be asked at all - a connection
    that failed outright, or a run in which nothing answered. A command that
    answered nothing on its own is named in `unanswered` and the rest of the
    report still stands.
    """
    session = _Session(address, port, timeout, cancel)
    status = _error_status(session)
    host_reply = session.ask('~HS', b'~HS')
    host = parse_host_status(host_reply) if host_reply else None
    ram_reply = session.ask('~HM', b'~HM')
    ram = parse_ram_status(ram_reply) if ram_reply else None

    if not session.answered:
        return None
    sections = [_fault_section(status, host_silent=host_reply is None),
                _work_section(host), _memory_section(session, ram)]
    return StatusReport(sections, session.unanswered)


# Flash only, deliberately, though memory.ram_size and memory.ram_free exist
# beside these: RAM is already reported from ~HM, in kilobytes the manual
# states, and these attributes document no unit at all. Two RAM figures in two
# unagreeing units is worse than one - so RAM is read where its unit is known,
# and this reads the memory ~HM cannot see. Flash is the one that fills up
# because of something done here: it is where a font goes by default
# (fonts.DEFAULT_FONT_DEVICE is E:) and where Objects stores what it writes.
_MEMORIES = (('memory.flash_size', 'memory.flash_free', "Flash"),)


def _printer_section(session: _Session) -> Section:
    """Which printer this is - model, firmware, resolution, and how long it has
    been up.

    The serial number is deliberately absent. ~HQSN would give one, but ~HQ is
    supported only on the Xi4/RXi4, ZM/RZ, S4M and G-Series - not on the ZT, ZD
    or ZQ ranges - so on a current printer it is not answered at all and costs a
    full read timeout to discover that. There is no clean Set/Get/Do equivalent
    (the device.serial_numbers.* attributes report board dates, not the unit's
    serial), and ~HI already names the model, so the figure is skipped rather
    than paid for.
    """
    readings: List[Reading] = []
    reply = session.ask('~HI', b'~HI')
    ident = parse_host_identification(reply) if reply else None
    if ident is not None:
        readings.append(Reading("Model", ident.model))
        readings.append(Reading("Firmware", ident.firmware))
        readings.append(Reading("Head resolution",
                                f"{ident.dpi} dpi" if ident.dpi
                                else "not recognised",
                                PLAIN if ident.dpi else WARN))
        if ident.memory_kb is not None:
            # Reported in KB because ~HI reports KB, whatever its prose says.
            readings.append(Reading("Memory fitted",
                                    f"{_thousands(ident.memory_kb)} KB"))
        if ident.options:
            readings.append(Reading("Options", ident.options))

    uptime = parse_getvar(session.ask(
        'device.uptime', b'! U1 getvar "device.uptime"\r\n', record=False) or '')
    if uptime:
        readings.append(Reading("Up for", uptime))

    if not readings:
        readings.append(Reading("Printer", "could not be read", WARN))
    return Section("Printer", readings)


# The odometer attributes, with the label each is shown under. Set/Get/Do is
# asked before ~HQOD because the two command families cover almost disjoint
# hardware - ~HQ is the Xi4/ZM/S4M/G-Series generation, these attributes the
# ZT/ZD/ZQ/QLn one - so neither can be treated as the fallback for the other on
# grounds of age alone. What breaks the tie is the cost of being wrong: an
# attribute a printer has not got is refused with '?' at once, while an ~HQ
# sub-command it does not support is *ignored*, which cannot be told from a slow
# printer until the read times out. So the cheap-to-refuse form is tried first,
# and ~HQOD/~HQPH are sent only if none of these answered.
_ODOMETERS = (
    ('odometer.total_print_length', "Printed over its life"),
    ('odometer.headclean', "Since the head was last cleaned"),
    ('odometer.headnew', "Since the head was replaced"),
)


def _wear_section(session: _Session) -> Section:
    """How far this printer has printed and how its head is wearing.

    The two head figures are the panel's most useful non-fault reading: they are
    what the Clean Printhead and Replace Printhead warnings are counted against,
    so they say how close a printer is to earning one.

    Distances are shown in the units the printer sends. The attributes answer
    both at once (*"8560 INCHES, 21744 CENTIMETERS"*), and ~HQOD answers in
    whichever single unit ^MA was set to - either way the figure is passed
    through rather than converted, so it matches what the printer's own front
    panel shows.
    """
    readings: List[Reading] = []
    for attribute, label in _ODOMETERS:
        value = parse_getvar(session.ask(
            attribute, f'! U1 getvar "{attribute}"\r\n'.encode('ascii'),
            record=False) or '')
        if value:
            readings.append(Reading(label, value))

    if not readings:
        # No attribute answered, so this is the older generation: ~HQ is the
        # family it does speak. Read as labelled lines rather than to a schema,
        # because ~HQOD grows two counters when Early Warning Maintenance is on
        # and ~HQPH's history lengthens with every head change (see
        # parse_meters).
        for name, payload in (('~HQOD', b'~HQOD'), ('~HQPH', b'~HQPH')):
            reply = session.ask(name, payload, record=False)
            for label, value in (parse_meters(reply) if reply else None) or ():
                readings.append(Reading(label, value))

    if not readings:
        readings.append(Reading("Wear", "could not be read", WARN))
    return Section("Wear", readings)


def query_printer_specs(address: str, port: int, timeout: float = 5,
                        cancel=None) -> Optional[StatusReport]:
    """What this printer *is*, rather than what it is doing - model, firmware,
    resolution, serial, uptime, free space per memory, and head wear.

    Separate from query_printer_activity because none of it moves while a user
    watches: the manual states outright that ~HM's installed and maximum RAM do
    not change after power-on, and a model number and serial never do. Splitting
    them is what lets an auto-refresh re-ask only the three commands whose
    answers can actually differ, instead of a dozen every few seconds.

    Same None contract as every other query in this layer: None means the
    printer could not be asked.
    """
    session = _Session(address, port, timeout, cancel)
    sections = [_printer_section(session), _wear_section(session)]
    if not session.answered:
        return None
    return StatusReport(sections, session.unanswered)


def report_text(*reports: Optional[StatusReport]) -> str:
    """The whole report as plain text, for the clipboard.

    Built here rather than in either frontend so the two put identical text on
    the clipboard - the same reason workflow.listing_status builds its sentence
    here. Takes several reports because the panel shows two (specs and
    activity) and a user copying it wants both, in the order given. A None
    report - one whose printer could not be asked - contributes a line saying
    so rather than being silently absent.
    """
    lines: List[str] = []
    unanswered: List[str] = []
    for report in reports:
        if report is None:
            lines.append("(the printer could not be asked)")
            continue
        for section in report.sections:
            lines.append("")
            lines.append(section.title)
            lines.append("-" * len(section.title))
            width = max((len(r.label) for r in section.readings), default=0)
            for reading in section.readings:
                mark = {ERROR: "  <-- error", WARN: "  <-- warning"}.get(
                    reading.level, "")
                lines.append(f"{reading.label.ljust(width)}  "
                             f"{reading.value}{mark}")
            if section.note:
                lines.append("")
                lines.append(section.note)
        unanswered.extend(report.unanswered)
    if unanswered:
        lines.append("")
        lines.append("Not answered: " + ", ".join(dict.fromkeys(unanswered)))
    return "\n".join(lines).strip() + "\n"
