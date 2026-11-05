"""
The help: Mallard pages in help/C, and starting something to show them.

The pages are read where they are, beside the program, so help works straight
from the unpacked zip with nothing installed into /usr/share/help.

No help viewer is on every Linux system, so launch() tries in turn:

1. Yelp, which ships with GNOME, Cinnamon and MATE. It is given the folder
   for the contents and a page's file for a topic; told a .page file, it
   still reads the whole folder as one document, so the page's links and
   its trail back to the contents work.
2. A PDF of the page (helppdf.py), opened with xdg-open, so with whatever
   the desktop opens PDFs in, or else the first of the usual PDF viewers on
   PATH. Nearly every desktop has one.
3. Neither: nothing is started, and a message for the frontend to show says
   what to install and where the pages are.

A viewer that cannot do it usually says so at once, by exiting with an error
- xdg-open does when nothing is set to open a PDF - so the frontends watch
the one started for its first WATCH_SECONDS, asking follow() every POLL_MS,
and a failure hands over to the next way down the list.

No toolkit is imported here: both frontends call launch() and follow(), and
show a message in their own way.
"""

import shutil
import subprocess
from pathlib import Path
from typing import List, NamedTuple, Optional, Union

PAGES = Path(__file__).resolve().parent.parent / "help" / "C"
MALLARD = "http://projectmallard.org/1.0/"
CONTENTS = "index"
# Tried in this order. The named PDF viewers are for a system with no
# xdg-open, or one on which xdg-open fails; their own order is arbitrary.
VIEWERS = ("yelp", "xdg-open", "evince", "okular", "atril", "xreader",
           "qpdfview", "zathura", "mupdf")
WATCH_SECONDS = 5.0
POLL_MS = 250
# follow()'s answer while the viewer is still starting: ask again.
WATCHING = "watching"


class Launch(NamedTuple):
    """What launch() did: the viewer it started, or why it started none."""
    process: Optional[subprocess.Popen]
    message: Optional[str]
    topic: str = CONTENTS
    viewer: str = ""            # the program started, by its name in VIEWERS
    pages: Path = PAGES
    pdf: Optional[Path] = None  # the PDF shown, when a PDF viewer was used
    cache: Optional[Path] = None


def topics(pages: Path = PAGES) -> List[str]:
    """Every page's id: its file name without .page, which Mallard requires
    to match the id the page gives itself."""
    return sorted(p.stem for p in pages.glob("*.page"))


INSTALL = "Install Yelp (the yelp package) or any PDF viewer, then try again."


def _where(pages: Path, pdf: Optional[Path]) -> str:
    text = f"The help pages are in {pages}"
    return text + (f"\nThis page as a PDF: {pdf}" if pdf else "")


def missing_viewer_message(pages: Path = PAGES, pdf: Optional[Path] = None,
                           other: bool = False) -> str:
    return (f"No {'other ' if other else ''}help viewer was found. {INSTALL}"
            f"\n\n{_where(pages, pdf)}")


def unmade_message(pages: Path, error: Exception) -> str:
    return (f"No Yelp was found, and the help could not be made into PDF: "
            f"{error}\n\nInstall Yelp (the yelp package), then try again.\n\n"
            f"{_where(pages, None)}")


def _start(argv: List[str]) -> Optional[subprocess.Popen]:
    """Start a viewer apart from this process: in a session of its own, so
    it neither dies with the designer nor takes a Ctrl+C meant for it, and
    with nothing tied to the designer's terminal."""
    try:
        return subprocess.Popen(argv, stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL,
                                start_new_session=True)
    except OSError:
        return None


def launch(topic: str = CONTENTS, pages: Path = PAGES,
           cache: Optional[Path] = None, start_after: str = "") -> Launch:
    """Show the help page `topic`, or the contents, with the first viewer in
    VIEWERS after `start_after` that is on PATH and starts. Never waits for
    it. The PDFs are made, into `cache` (helppdf.cache_dir() if None), only
    when it comes to a PDF viewer. Asking for a page that does not exist is
    a programming error."""
    page = pages / f"{topic}.page"
    if not page.is_file():
        raise ValueError(f"no help page {topic!r} in {pages}")
    names = VIEWERS[VIEWERS.index(start_after) + 1:] if start_after else VIEWERS
    pdf = None
    for name in names:
        path = shutil.which(name)
        if not path:
            continue
        if name == "yelp":
            target = pages if topic == CONTENTS else page
        else:
            if pdf is None:
                try:
                    from . import helppdf
                    pdf = helppdf.build(pages, cache) / f"{topic}.pdf"
                except Exception as e:  # the help must never take the designer down
                    return Launch(None, unmade_message(pages, e), topic, "",
                                  pages, None, cache)
            target = pdf
        process = _start([path, str(target)])
        if process is not None:
            return Launch(process, None, topic, name, pages, pdf, cache)
    return Launch(None, missing_viewer_message(pages, pdf, bool(start_after)),
                  topic, "", pages, pdf, cache)


def follow(launched: Launch, elapsed: float) -> Union[str, None, Launch]:
    """Asked every POLL_MS, `elapsed` seconds after `launched` started its
    viewer. WATCHING while it is still running and young enough to fail;
    None once it has lasted WATCH_SECONDS or exited cleanly, as Mint's yelp
    wrapper and xdg-open do once they have handed the file over. If it
    failed, the next viewer down the list has been started, and this is
    that Launch, to be followed in turn - or nothing else could be started,
    and its message says which viewer failed and why none other was used."""
    code = launched.process.poll()
    if code is None:
        return WATCHING if elapsed < WATCH_SECONDS else None
    if code == 0:
        return None
    following = launch(launched.topic, launched.pages, launched.cache,
                       start_after=launched.viewer)
    if following.message:
        return following._replace(
            message=f"{launched.viewer} could not show the help "
                    f"(exit status {code}).\n\n{following.message}")
    return following
