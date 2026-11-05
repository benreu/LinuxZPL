"""
The help: Mallard pages in help/C, and starting something to show them.

The pages are read where they are, beside the program, so help works straight
from the unpacked zip with nothing installed into /usr/share/help. Yelp is
given the folder for the contents and a page's file for a topic; told a
.page file, it still reads the whole folder as one document, so the page's
links and its trail back to the contents work.

No help viewer is on every Linux system. Yelp ships with GNOME, Cinnamon and
MATE but not with KDE, most Xfce and LXQt installs, or minimal ones. Without
one, launch() starts nothing and hands back a message for the frontend to
show, naming the folder and what to install.

No toolkit is imported here: both frontends call launch() and show its
message in their own way.
"""

import shutil
import subprocess
from pathlib import Path
from typing import List, NamedTuple, Optional

PAGES = Path(__file__).resolve().parent.parent / "help" / "C"
MALLARD = "http://projectmallard.org/1.0/"
CONTENTS = "index"


class Launch(NamedTuple):
    """What launch() did: the viewer it started, or why it started none."""
    process: Optional[subprocess.Popen]
    message: Optional[str]


def topics(pages: Path = PAGES) -> List[str]:
    """Every page's id: its file name without .page, which Mallard requires
    to match the id the page gives itself."""
    return sorted(p.stem for p in pages.glob("*.page"))


def missing_viewer_message(pages: Path = PAGES) -> str:
    return ("No help viewer was found. LinuxZPL's help is shown in Yelp: "
            "install the yelp package, then try again.\n\n"
            f"The help pages are in {pages}")


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


def launch(topic: str = CONTENTS, pages: Path = PAGES) -> Launch:
    """Show the help page `topic`, or the contents. Never waits for the
    viewer. Asking for a page that does not exist is a programming error."""
    page = pages / f"{topic}.page"
    if not page.is_file():
        raise ValueError(f"no help page {topic!r} in {pages}")
    yelp = shutil.which("yelp")
    if yelp:
        process = _start([yelp, str(pages if topic == CONTENTS else page)])
        if process is not None:
            return Launch(process, None)
    return Launch(None, missing_viewer_message(pages))
