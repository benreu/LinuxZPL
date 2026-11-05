"""The help pages in help/C, and zplcore/helpdocs.py starting a viewer for them.

No viewer is really started: shutil.which and subprocess.Popen are replaced in
helpdocs for each check, so the suite says what would be run, on any machine,
whether or not it has Yelp. The PDFs (zplcore/helppdf.py) are really made,
into throwaway folders, and read back for their links and outline.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import _isolate  # noqa: F401

from zplcore import helpdocs, helppdf

failures = 0


def check(name, cond, extra=""):
    global failures
    print(('ok   ' if cond else 'FAIL ') + name + (f"  [{extra}]" if not cond and extra != "" else ""))
    if not cond:
        failures += 1


M = "{%s}" % helpdocs.MALLARD

# --- the pages ----------------------------------------------------------------

pages = helpdocs.topics()
check("help/C has the contents page and the firmware topic",
      {"index", "firmware-update"} <= set(pages), pages)

trees = {}
for topic in pages:
    path = helpdocs.PAGES / f"{topic}.page"
    try:
        trees[topic] = ET.parse(path).getroot()
    except ET.ParseError as e:
        check(f"{topic}.page is well-formed XML", False, e)
        continue
    root = trees[topic]
    check(f"{topic}.page is a Mallard page", root.tag == M + "page", root.tag)
    check(f"{topic}.page's id matches its file name, as Mallard requires",
          root.get("id") == topic, root.get("id"))
    check(f"{topic}.page has a title", root.find(M + "title") is not None)

check("the contents page is a guide", trees.get("index") is not None
      and trees["index"].get("type") == "guide")


def section_ids(root):
    return {s.get("id") for s in root.iter(M + "section") if s.get("id")}


broken = []
for topic, root in trees.items():
    for element in root.iter():
        xref = element.get("xref")
        if xref is None:
            continue
        target, _, section = xref.partition("#")
        target = target or topic
        if target not in trees:
            broken.append(f"{topic}: {xref} (no such page)")
        elif section and section not in section_ids(trees[target]):
            broken.append(f"{topic}: {xref} (no such section)")
check("every xref names a page, and a section that page has", not broken, broken)

orphans = [t for t, root in trees.items() if t != helpdocs.CONTENTS
           and not any(l.get("type") == "guide"
                       for l in root.iter(M + "link"))]
check("every topic names a guide, so the contents lists it", not orphans, orphans)

# --- starting a viewer --------------------------------------------------------

real_which, real_start = helpdocs.shutil.which, helpdocs._start
started = []
CACHE = Path(tempfile.mkdtemp(prefix="linuxzpl-help-cache-"))


class FakeProcess:
    """A viewer that started; poll() answers with `code`, None while running."""
    code = None

    def __init__(self, argv):
        self.argv = argv

    def poll(self):
        return self.code


def fake_start(argv):
    started.append(argv)
    return FakeProcess(argv)


def start_failing_for(*names):
    """_start() as it is when a program on PATH will not run."""
    def start(argv):
        return None if os.path.basename(argv[0]) in names else fake_start(argv)
    return start


def stubbed(have, start=fake_start):
    """PATH holding only the programs in `have`, and nothing really started.
    _start() is replaced rather than subprocess.Popen, which would be
    replaced for everything - fc-list, as the PDF's fonts are looked up,
    too."""
    helpdocs.shutil.which = lambda name: f"/usr/bin/{name}" if name in have else None
    helpdocs._start = start


def unstub():
    helpdocs.shutil.which, helpdocs._start = real_which, real_start


def run(topic=None, have=(), start=fake_start):
    """launch(topic) on a machine whose PATH has only the programs in `have`."""
    del started[:]
    stubbed(have, start)
    try:
        if topic is None:
            return helpdocs.launch(cache=CACHE)
        return helpdocs.launch(topic, cache=CACHE)
    finally:
        unstub()


def argvs():
    return list(started)


popened = []
real_popen = helpdocs.subprocess.Popen
helpdocs.subprocess.Popen = lambda argv, **kwargs: popened.append((argv, kwargs)) or "process"
try:
    detached = helpdocs._start(["/usr/bin/yelp", "x"])
finally:
    helpdocs.subprocess.Popen = real_popen
check("a viewer is started detached, with nothing tied to this process's terminal",
      detached == "process" and popened[0][1].get("start_new_session") is True
      and all(popened[0][1].get(s) is subprocess.DEVNULL
              for s in ("stdin", "stdout", "stderr")), popened)


def popen_missing(argv, **kwargs):
    raise FileNotFoundError(argv[0])


helpdocs.subprocess.Popen = popen_missing
try:
    check("a program that will not run starts nothing", helpdocs._start(["/nope"]) is None)
finally:
    helpdocs.subprocess.Popen = real_popen

launched = run(have=("yelp", "xdg-open"))
check("with Yelp, the contents opens Yelp on the help folder, and no PDF is made",
      argvs() == [["/usr/bin/yelp", str(helpdocs.PAGES)]]
      and launched.message is None and launched.viewer == "yelp"
      and launched.pdf is None and not any(CACHE.iterdir()), started)

launched = run("firmware-update", have=("yelp",))
check("with Yelp, a topic opens Yelp on that page's file",
      argvs() == [["/usr/bin/yelp", str(helpdocs.PAGES / "firmware-update.page")]]
      and launched.message is None, started)

launched = run("firmware-update", have=("xdg-open", "evince"))
check("with no Yelp, the topic's PDF is made and opened with xdg-open",
      launched.viewer == "xdg-open" and launched.pdf is not None
      and launched.pdf.name == "firmware-update.pdf" and launched.pdf.is_file()
      and argvs() == [["/usr/bin/xdg-open", str(launched.pdf)]], (launched, started))

launched = run(have=("zathura", "evince"))
check("with no xdg-open either, the first PDF viewer in the list is used",
      launched.viewer == "evince"
      and argvs() == [["/usr/bin/evince", str(launched.pdf)]]
      and launched.pdf.name == "index.pdf", started)

launched = run(have=("yelp", "xdg-open"), start=start_failing_for("yelp"))
check("a Yelp that cannot be started hands over to the PDF",
      launched.viewer == "xdg-open" and launched.message is None, launched)

launched = run(have=())
check("with no viewer at all, nothing is started", started == [] and launched.process is None)
check("and the message says to install Yelp or a PDF viewer, and where the pages are",
      launched.message and "yelp" in launched.message and "PDF viewer" in launched.message
      and str(helpdocs.PAGES) in launched.message, launched.message)

real_build = helppdf.build
helppdf.build = lambda pages, cache=None, fonts=None: (_ for _ in ()).throw(
    OSError("No space left on device"))
try:
    launched = run(have=("xdg-open",))
finally:
    helppdf.build = real_build
check("a PDF that cannot be made gives a message with the reason, and starts nothing",
      started == [] and launched.message and "No space left on device" in launched.message
      and "yelp" in launched.message, launched.message)

try:
    run("no-such-topic", have=("yelp",))
    check("asking for a page that does not exist is an error", False)
except ValueError:
    check("asking for a page that does not exist is an error", started == [])

# --- watching the viewer for its first seconds ------------------------------------


class Running(FakeProcess):
    code = None


class Exited(FakeProcess):
    code = 0


class Failed(FakeProcess):
    code = 3


def follow(launched, elapsed, have=()):
    del started[:]
    stubbed(have)
    try:
        return helpdocs.follow(launched, elapsed)
    finally:
        unstub()


base = helpdocs.Launch(None, None, "firmware-update", "xdg-open", helpdocs.PAGES,
                       None, CACHE)
check("a viewer still starting is watched again",
      follow(base._replace(process=Running([])), 1.0) is helpdocs.WATCHING)
check("one that has lasted WATCH_SECONDS is left to it",
      follow(base._replace(process=Running([])), helpdocs.WATCH_SECONDS) is None)
check("one that exited cleanly, having handed the file on, is left to it",
      follow(base._replace(process=Exited([])), 0.5, have=("evince",)) is None
      and started == [])

following = follow(base._replace(process=Failed([])), 0.5, have=("xdg-open", "evince"))
check("an xdg-open that failed hands over to the next PDF viewer, not to itself again",
      isinstance(following, helpdocs.Launch) and following.viewer == "evince"
      and argvs() == [["/usr/bin/evince", str(following.pdf)]]
      and following.pdf.name == "firmware-update.pdf", (following, started))

following = follow(base._replace(process=Failed([])), 0.5, have=("xdg-open",))
check("with nothing after it, the message names what failed and how",
      following.process is None and following.message.startswith(
          "xdg-open could not show the help (exit status 3).")
      and "No other help viewer was found" in following.message, following.message)

following = follow(base._replace(process=Failed([]), viewer="yelp"), 0.5,
                   have=("yelp", "xdg-open"))
check("a Yelp that failed hands over to the PDF",
      following.viewer == "xdg-open" and following.pdf.name == "firmware-update.pdf",
      following)

# --- the PDFs -----------------------------------------------------------------------

out = Path(tempfile.mkdtemp(prefix="linuxzpl-help-pdf-"))
built = helppdf.build(helpdocs.PAGES, out)
pdfs = {p.stem: p.read_bytes() for p in built.glob("*.pdf")}
check("every page is made into a PDF of its own", set(pdfs) == set(pages), sorted(pdfs))
check("each is a PDF", all(b.startswith(b"%PDF-") for b in pdfs.values()))


def remote_links(data):
    return set(m.decode() for m in re.findall(rb"/F \(([^)]*)\)", data))


missing = {t: sorted(l for l in remote_links(b) if not (built / l).is_file())
           for t, b in pdfs.items()}
check("every link to another page's PDF names a file beside it",
      not any(missing.values()), missing)
check("the contents links to the firmware topic, which it lists",
      "firmware-update.pdf" in remote_links(pdfs["index"]), remote_links(pdfs["index"]))
check("the firmware topic's trail links back to the contents",
      "index.pdf" in remote_links(pdfs["firmware-update"]))
check("a page's title is the PDF's title, and its sections are its outline",
      b"/Title (Update a printer's firmware)" in pdfs["firmware-update"]
      and b"/Title (If the printer refuses the file)" in pdfs["firmware-update"])

again = helppdf.build(helpdocs.PAGES, out)
check("the same pages are not made again",
      again == built and all(p.read_bytes() == pdfs[p.stem] for p in again.glob("*.pdf"))
      and [d.name for d in out.iterdir()] == [built.name], [d.name for d in out.iterdir()])

edited = Path(tempfile.mkdtemp(prefix="linuxzpl-help-pages-")) / "C"
shutil.copytree(helpdocs.PAGES, edited)
page = edited / "index.page"
page.write_text(page.read_text().replace("<p>LinuxZPL designs",
                                         "<p>Edited. LinuxZPL designs"))
rebuilt = helppdf.build(edited, out)
check("a changed page makes a new set, and the old one goes",
      rebuilt != built and (rebuilt / helppdf.DONE).is_file()
      and [d.name for d in out.iterdir()] == [rebuilt.name], [d.name for d in out.iterdir()])

base_fonts = helppdf.build(helpdocs.PAGES, Path(tempfile.mkdtemp()), helppdf.BASE_FONTS)
check("with no DejaVu, the PDF base fonts make every page too",
      {p.stem for p in base_fonts.glob("*.pdf")} == set(pages))

page.write_text(page.read_text().replace("</section>",
                                         "<quote><p>Not shown</p></quote></section>"))
try:
    helppdf.build(edited, out)
    check("Mallard the converter does not know is refused", False)
except helppdf.UnsupportedMallard as e:
    check("Mallard the converter does not know is refused, naming page and element",
          "index.page" in str(e) and "<quote>" in str(e), e)
check("and a refused build leaves nothing half made",
      [d.name for d in out.iterdir()] == [rebuilt.name], [d.name for d in out.iterdir()])

print("ALL HELP CHECKS PASSED" if not failures else f"{failures} FAILED")
sys.exit(1 if failures else 0)
