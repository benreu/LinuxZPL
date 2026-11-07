"""
The help: Mallard pages in help/C, and starting something to show them.

The pages are read where they are, beside the program, so help works straight
from the unpacked zip with nothing installed into /usr/share/help.

No help viewer is on every Linux system, so launch() tries in turn:

1. Yelp, which ships with GNOME, Cinnamon and MATE. It is given the folder
   for the contents and a page's file for a topic; told a .page file, it
   still reads the whole folder as one document, so the page's links and
   its trail back to the contents work. A topic may name a section of its
   page, "fonts#manager" as in a Mallard xref, and Yelp is given the file
   with that fragment, which opens it scrolled to the section.
2. LinuxZPL's own viewer, made to look and work like Yelp (helpview.py,
   qtui/helpviewer.py and gtkui/helpviewer.py), in the toolkit the
   designer runs in. It is a program of its own, linuxzpl.py --show-help,
   as Yelp is, so it opens from a modal window and outlives the designer.
   While it runs, a later topic is written to its standard input instead,
   and it goes there and comes to the front: one help window, as Yelp has.
3. Neither: nothing is started, and a message for the frontend to show says
   what to install and where the pages are.

A viewer that cannot do it usually says so at once, by exiting with an error
- xdg-open does when nothing is set to open a PDF - so the frontends watch
the one started for its first WATCH_SECONDS, asking follow() every POLL_MS,
and a failure hands over to the next way down the list.

open_pdf() opens a page's PDF on its own, with the PDF viewers alone: the
built-in help viewer's Print, which prints from the PDF viewer. It is
watched and handed over the same way, down its own list.

No toolkit is imported here: both frontends call launch() and follow(), and
show a message in their own way.
"""

import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import List, NamedTuple, Optional, Set, Tuple, Union

PAGES = Path(__file__).resolve().parent.parent / "help" / "C"
MALLARD = "http://projectmallard.org/1.0/"
CONTENTS = "index"
# Tried in this order. The named PDF viewers are for a system with no
# xdg-open, or one on which xdg-open fails; their own order is arbitrary.
PDF_VIEWERS = ("xdg-open", "evince", "okular", "atril", "xreader",
               "qpdfview", "zathura", "mupdf")
BUILTIN = "linuxzpl"        # LinuxZPL's own viewer, by its name in VIEWERS
VIEWERS = ("yelp", BUILTIN)
# How a message names a viewer that failed.
VIEWER_NAMES = {"yelp": "Yelp", BUILTIN: "LinuxZPL's own help viewer"}
PROGRAM = PAGES.parent.parent / "linuxzpl.py"
WATCH_SECONDS = 5.0
POLL_MS = 250
# follow()'s answer while the viewer is still starting: ask again.
WATCHING = "watching"

# The page, and the section of it, that each window's Help button and F1
# open, by a key both frontends use for that window. Tests open every window
# in both frontends and hold its Help to this table, and check each topic
# names a page and a section that exist.
WINDOW_HELP = {
    "label_settings": "resolution#label-size",
    "grid_settings": "view#grid",
    "default_printer": "printing#default-printer",
    "print": "printing#print-window",
    "local_fonts": "fonts#which",
    "printer_fonts": "fonts#manager",
    "upload_font_to": "fonts#memory",
    "printer_graphics": "stored-graphics#store",
    "store_graphic_as": "stored-graphics#store",
    "printer_objects": "stored-graphics#objects",
    "store_object_as": "stored-graphics#objects",
    "upload_template": "templates#upload",
    "printer_console": "printer-status#console",
    "printer_status": "printer-status#status",
    "firmware_update": "firmware-update",
    "dpi_rescale": "resolution#rescale",
    "font_problem": "fonts#check",
}

# The Properties panel's own page: what it opens with nothing selected, or
# with more than one element.
PANEL = "properties-panel"


class Launch(NamedTuple):
    """What launch() did: the viewer it started, or why it started none."""
    process: Optional[subprocess.Popen]
    message: Optional[str]
    topic: str = CONTENTS
    viewer: str = ""            # the program started, by its name in VIEWERS
    pages: Path = PAGES
    pdf: Optional[Path] = None  # the PDF shown, when a PDF viewer was used
    cache: Optional[Path] = None
    printing: bool = False      # from open_pdf(), so handed over down PDF_VIEWERS
    handed: bool = False        # given to the built-in viewer already running
    frontend: str = ""          # the toolkit the built-in viewer runs in


def topics(pages: Path = PAGES) -> List[str]:
    """Every page's id: its file name without .page, which Mallard requires
    to match the id the page gives itself."""
    return sorted(p.stem for p in pages.glob("*.page"))


def element_topic(selection) -> str:
    """The page the Properties panel's Help opens for `selection`, a list of
    elements: the page about that kind of element when it is one element,
    and the panel's own page otherwise. A text element's page section
    follows what feeds it, as its rows in the panel do."""
    from . import model
    if len(selection) != 1:
        return PANEL
    element = selection[0]
    if isinstance(element, model.TextElement):
        if element.clock_format:
            return "text#time"
        if element.serial_increment is not None:
            return "text#serial"
        if element.field_number is not None:
            return "text#numbered"
        return "text"
    for kind, topic in ((model.FrameElement, "frame"),
                        (model.CircleElement, "circle-ellipse"),
                        (model.EllipseElement, "circle-ellipse"),
                        (model.DiagonalLineElement, "diagonal"),
                        (model.GraphicSymbolElement, "symbol"),
                        (model.BarcodeElement, "barcode"),
                        (model.ImageElement, "image"),
                        (model.StoredGraphicElement, "stored-graphics#use")):
        if isinstance(element, kind):
            return topic
    return PANEL


def split_topic(topic: str) -> Tuple[str, str]:
    """("fonts", "manager") for "fonts#manager"; ("fonts", "") for "fonts"."""
    page, _hash, section = topic.partition("#")
    return page, section


def section_ids(page: Path) -> Set[str]:
    """The ids of every section in a page, which a topic may name after #."""
    return {el.get("id") for el in ET.parse(page).getroot().iter(f"{{{MALLARD}}}section")
            if el.get("id")}


INSTALL = "Install Yelp (the yelp package), then try again."


def _where(pages: Path, pdf: Optional[Path]) -> str:
    text = f"The help pages are in {pages}"
    return text + (f"\nThis page as a PDF: {pdf}" if pdf else "")


def missing_viewer_message(pages: Path = PAGES, pdf: Optional[Path] = None,
                           other: bool = False) -> str:
    return (f"Neither Yelp nor LinuxZPL's own help viewer could be started. "
            f"{INSTALL}\n\n{_where(pages, pdf)}")


def no_pdf_viewer_message(pdf: Path, other: bool = False) -> str:
    return (f"No {'other ' if other else ''}PDF viewer was found to print the "
            f"page from. Install one (Evince, Okular, Atril or any other), "
            f"then try again.\n\nThis page as a PDF: {pdf}")


def pdf_unmade_message(error: Exception) -> str:
    return f"The page could not be made into a PDF: {error}"


def _start(argv: List[str], stdin=subprocess.DEVNULL) -> Optional[subprocess.Popen]:
    """Start a viewer apart from this process: in a session of its own, so
    it neither dies with the designer nor takes a Ctrl+C meant for it, and
    with nothing tied to the designer's terminal - but for the built-in
    viewer's standard input, a pipe the designer sends later topics down."""
    try:
        return subprocess.Popen(argv, stdin=stdin,
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL,
                                start_new_session=True)
    except OSError:
        return None


def _check(topic: str, pages: Path) -> Tuple[str, str, Path]:
    """The page and section `topic` names, and the page's file; ValueError
    for a page or a section that does not exist, a programming error."""
    page_id, section = split_topic(topic)
    page = pages / f"{page_id}.page"
    if not page.is_file():
        raise ValueError(f"no help page {page_id!r} in {pages}")
    if section and section not in section_ids(page):
        raise ValueError(f"no section {section!r} in help page {page_id!r}")
    return page_id, section, page


def open_pdf(topic: str, pages: Path = PAGES, cache: Optional[Path] = None,
             start_after: str = "") -> Launch:
    """Open the PDF of the page `topic` names, at its top, with the first
    of PDF_VIEWERS after `start_after` that is on PATH and starts, to print
    it from there. The PDFs are made into `cache` (helppdf.cache_dir() if
    None). Never waits for the viewer."""
    page_id, _section, _page = _check(topic, pages)
    names = (PDF_VIEWERS[PDF_VIEWERS.index(start_after) + 1:] if start_after
             else PDF_VIEWERS)
    try:
        from . import helppdf
        pdf = helppdf.build(pages, cache) / f"{page_id}.pdf"
    except Exception as e:  # the help must never take the program down
        return Launch(None, pdf_unmade_message(e), topic, "", pages, None, cache, True)
    for name in names:
        path = shutil.which(name)
        if not path:
            continue
        process = _start([path, str(pdf)])
        if process is not None:
            return Launch(process, None, topic, name, pages, pdf, cache, True)
    return Launch(None, no_pdf_viewer_message(pdf, bool(start_after)), topic, "",
                  pages, pdf, cache, True)


# The built-in viewer started last, and its toolkit, while it may still run.
_viewer: Optional[subprocess.Popen] = None
_viewer_frontend = ""


def _frontend(frontend: Optional[str]) -> str:
    """The toolkit for the built-in viewer: the one asked for, or the one
    this process has loaded."""
    return frontend or ("qt" if "PySide2.QtWidgets" in sys.modules else "gtk")


def builtin_argv(frontend: str, topic: str) -> List[str]:
    return [sys.executable, str(PROGRAM), f"--{frontend}", "--show-help", topic]


def _hand_to_viewer(topic: str, frontend: str) -> Optional[subprocess.Popen]:
    """Give `topic` to the built-in viewer already running in `frontend`,
    down its standard input; None if there is none, or it has gone."""
    global _viewer
    if _viewer is None or _viewer_frontend != frontend or _viewer.poll() is not None:
        return None
    try:
        _viewer.stdin.write(f"{topic}\n".encode("utf-8"))
        _viewer.stdin.flush()
    except (OSError, ValueError):  # closed between the poll and the write
        _viewer = None
        return None
    return _viewer


def launch(topic: str = CONTENTS, pages: Path = PAGES,
           cache: Optional[Path] = None, start_after: str = "",
           frontend: Optional[str] = None) -> Launch:
    """Show the help page `topic`, or the contents, with the first viewer in
    VIEWERS after `start_after` that is on PATH and starts. Never waits for
    it. `topic` may name a section, "page#section", which either viewer
    opens at. The built-in viewer runs in `frontend`, "qt" or "gtk" (by
    default the toolkit loaded here), and while one runs it is given the
    topic rather than another started. `cache` is for the PDFs a viewer's
    Print makes, passed on unchanged. Asking for a page or a section that
    does not exist is a programming error."""
    global _viewer, _viewer_frontend
    _page_id, section, page = _check(topic, pages)
    frontend = _frontend(frontend)
    names = VIEWERS[VIEWERS.index(start_after) + 1:] if start_after else VIEWERS
    for name in names:
        if name == BUILTIN:
            running = _hand_to_viewer(topic, frontend)
            if running is not None:
                return Launch(running, None, topic, name, pages, None, cache,
                              handed=True, frontend=frontend)
            process = _start(builtin_argv(frontend, topic), stdin=subprocess.PIPE)
            if process is not None:
                _viewer, _viewer_frontend = process, frontend
                return Launch(process, None, topic, name, pages, None, cache,
                              frontend=frontend)
            continue
        path = shutil.which(name)
        if not path:
            continue
        target = (pages if topic == CONTENTS
                  else f"{page}#{section}" if section else page)
        process = _start([path, str(target)])
        if process is not None:
            return Launch(process, None, topic, name, pages, None, cache,
                          frontend=frontend)
    return Launch(None, missing_viewer_message(pages, None, bool(start_after)),
                  topic, "", pages, None, cache, frontend=frontend)


def follow(launched: Launch, elapsed: float) -> Union[str, None, Launch]:
    """Asked every POLL_MS, `elapsed` seconds after `launched` started its
    viewer. WATCHING while it is still running and young enough to fail;
    None once it has lasted WATCH_SECONDS or exited cleanly, as Mint's yelp
    wrapper and xdg-open do once they have handed the file over. If it
    failed, the next viewer down the list has been started, and this is
    that Launch, to be followed in turn - or nothing else could be started,
    and its message says which viewer failed and why none other was used."""
    if launched.handed:
        return None
    code = launched.process.poll()
    if code is None:
        return WATCHING if elapsed < WATCH_SECONDS else None
    if code == 0:
        return None
    if launched.printing:
        following = open_pdf(launched.topic, launched.pages, launched.cache,
                             start_after=launched.viewer)
    else:
        following = launch(launched.topic, launched.pages, launched.cache,
                           start_after=launched.viewer,
                           frontend=launched.frontend or None)
    if following.message:
        what = "the page's PDF" if launched.printing else "the help"
        name = VIEWER_NAMES.get(launched.viewer, launched.viewer)
        return following._replace(
            message=f"{name} could not show {what} "
                    f"(exit status {code}).\n\n{following.message}")
    return following
