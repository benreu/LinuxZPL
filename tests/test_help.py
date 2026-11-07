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


class Pipe:
    """The built-in viewer's standard input: what was written, or a pipe
    whose reader has gone."""

    def __init__(self, broken=False):
        self.written, self.broken = [], broken

    def write(self, data):
        if self.broken:
            raise BrokenPipeError(32, "Broken pipe")
        self.written.append(data)

    def flush(self):
        pass


class FakeProcess:
    """A viewer that started; poll() answers with `code`, None while running."""
    code = None

    def __init__(self, argv, stdin=None):
        self.argv, self.stdin_kind = argv, stdin
        self.stdin = Pipe() if stdin is subprocess.PIPE else None

    def poll(self):
        return self.code


def fake_start(argv, stdin=subprocess.DEVNULL):
    started.append(argv)
    return FakeProcess(argv, stdin)


def start_failing_for(*names):
    """_start() as it is when a program will not run: by its name on PATH,
    or "python" for the built-in viewer."""
    def start(argv, stdin=subprocess.DEVNULL):
        name = "python" if argv[0] == sys.executable else os.path.basename(argv[0])
        return None if name in names else fake_start(argv, stdin)
    return start


def stubbed(have, start=fake_start):
    """PATH holding only the programs in `have`, and nothing really started.
    _start() is replaced rather than subprocess.Popen, which would be
    replaced for everything - fc-list, as fonts are looked up, too."""
    helpdocs.shutil.which = lambda name: f"/usr/bin/{name}" if name in have else None
    helpdocs._start = start


def unstub():
    helpdocs.shutil.which, helpdocs._start = real_which, real_start


def run(topic=None, have=(), start=fake_start, fresh=True, frontend="qt"):
    """launch(topic) on a machine whose PATH has only the programs in `have`;
    with no built-in viewer running unless `fresh` is False."""
    del started[:]
    if fresh:
        helpdocs._viewer = None
    stubbed(have, start)
    try:
        if topic is None:
            return helpdocs.launch(cache=CACHE, frontend=frontend)
        return helpdocs.launch(topic, cache=CACHE, frontend=frontend)
    finally:
        unstub()


def argvs():
    return list(started)


def builtin(frontend, topic):
    return [sys.executable, str(helpdocs.PROGRAM), f"--{frontend}", "--show-help", topic]


popened = []
real_popen = helpdocs.subprocess.Popen
helpdocs.subprocess.Popen = lambda argv, **kwargs: popened.append((argv, kwargs)) or "process"
try:
    detached = helpdocs._start(["/usr/bin/yelp", "x"])
    piped = helpdocs._start(["/usr/bin/python3", "x"], stdin=subprocess.PIPE)
finally:
    helpdocs.subprocess.Popen = real_popen
check("a viewer is started detached, with nothing tied to this process's terminal",
      detached == "process" and popened[0][1].get("start_new_session") is True
      and all(popened[0][1].get(s) is subprocess.DEVNULL
              for s in ("stdin", "stdout", "stderr")), popened)
check("the built-in viewer is too, but for a pipe to its standard input",
      piped == "process" and popened[1][1].get("start_new_session") is True
      and popened[1][1].get("stdin") is subprocess.PIPE
      and popened[1][1].get("stdout") is subprocess.DEVNULL, popened[1:])


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
check("with no Yelp, LinuxZPL's own viewer is started at the topic, in the toolkit "
      "asked for, with a pipe to its standard input",
      launched.viewer == helpdocs.BUILTIN and launched.message is None
      and argvs() == [builtin("qt", "firmware-update")]
      and launched.process.stdin_kind is subprocess.PIPE and launched.pdf is None
      and helpdocs.PROGRAM.is_file(), (launched, started))

launched = run(have=(), frontend="gtk")
check("and in GTK for the GTK frontend, at the contents",
      argvs() == [builtin("gtk", "index")], started)

launched = run(have=("yelp",), start=start_failing_for("yelp"))
check("a Yelp that cannot be started hands over to the built-in viewer",
      launched.viewer == helpdocs.BUILTIN and argvs() == [builtin("qt", "index")], launched)

launched = run(have=("yelp",), start=start_failing_for("yelp", "python"))
check("with neither, nothing is started", started == [] and launched.process is None)
check("and the message names both, says to install Yelp, and where the pages are",
      launched.message and "Neither Yelp nor LinuxZPL's own help viewer" in launched.message
      and "yelp" in launched.message and "PDF" not in launched.message
      and str(helpdocs.PAGES) in launched.message, launched.message)

try:
    run("no-such-topic", have=("yelp",))
    check("asking for a page that does not exist is an error", False)
except ValueError:
    check("asking for a page that does not exist is an error", started == [])

# --- one built-in viewer, given each topic in turn -----------------------------

first = run("fonts", have=())
second = run("printing#usb", have=(), fresh=False)
check("while the built-in viewer runs, the next topic is written to it, not "
      "another started",
      started == [] and second.handed and second.process is first.process
      and first.process.stdin.written == [b"printing#usb\n"], (started, second))
check("and a topic it was handed is not watched", helpdocs.follow(second, 0.0) is None)
yelp_now = run("printing", have=("yelp",), fresh=False)
check("with Yelp found again, Yelp opens it, as it comes first",
      yelp_now.viewer == "yelp" and not yelp_now.handed)
first.process.code = 0
third = run("text", have=(), fresh=False)
check("once that viewer has closed, a new one is started",
      argvs() == [builtin("qt", "text")] and not third.handed
      and third.process is not first.process)
other = run("text", have=(), fresh=False, frontend="gtk")
check("a viewer in the other toolkit is not handed the topic",
      argvs() == [builtin("gtk", "text")] and not other.handed)
helpdocs._viewer.stdin.broken = True
after = run("frame", have=(), fresh=False, frontend="gtk")
check("nor is one whose pipe has broken, and a new one starts",
      argvs() == [builtin("gtk", "frame")] and not after.handed, (started, after))
helpdocs._viewer = None

# --- a section of a page, and each window's Help -------------------------------

check("a topic splits into its page and section",
      helpdocs.split_topic("fonts#manager") == ("fonts", "manager")
      and helpdocs.split_topic("fonts") == ("fonts", ""))
check("the core finds a page's sections as the pages' own check does",
      all(helpdocs.section_ids(helpdocs.PAGES / f"{t}.page") == section_ids(trees[t])
          for t in pages))

for key, topic in sorted(helpdocs.WINDOW_HELP.items()):
    page, section = helpdocs.split_topic(topic)
    check(f"window {key}'s Help names a page that exists: {topic}",
          page in trees and (not section or section in section_ids(trees[page])),
          (page, section))

launched = run("fonts#manager", have=("yelp",))
check("with Yelp, a topic with a section opens the page's file at that section",
      argvs() == [["/usr/bin/yelp", str(helpdocs.PAGES / "fonts.page") + "#manager"]]
      and launched.topic == "fonts#manager", started)

launched = run("fonts#manager", have=("xdg-open",))
check("with no Yelp, the built-in viewer is given the section too",
      argvs() == [builtin("qt", "fonts#manager")] and launched.topic == "fonts#manager",
      (launched, started))

for bad in ("fonts#nowhere", "nowhere#manager", "nowhere"):
    try:
        run(bad, have=("yelp",))
        raised = False
    except ValueError:
        raised = True
    check(f"asking for {bad!r}, which does not exist, is an error", raised and not started)

# The Properties panel's Help: the page about the one element selected.
from zplcore import model
doc = model.Document()
kinds = [
    (doc.add_text_element("words"), "text"),
    (doc.add_time_element(), "text#time"),
    (doc.add_serial_element(), "text#serial"),
    (doc.add_numbered_element(), "text#numbered"),
    (doc.add_frame_element(), "frame"),
    (doc.add_circle_element(), "circle-ellipse"),
    (doc.add_ellipse_element(), "circle-ellipse"),
    (doc.add_diagonal_element(), "diagonal"),
    (doc.add_graphic_symbol_element(), "symbol"),
    (doc.add_barcode_element(), "barcode"),
    (model.ImageElement(), "image"),
    (doc.add_stored_graphic_element(), "stored-graphics#use"),
]
for element, topic in kinds:
    got = helpdocs.element_topic([element])
    page, section = helpdocs.split_topic(got)
    check(f"the panel's Help for a {type(element).__name__} opens {topic}",
          got == topic and page in trees
          and (not section or section in section_ids(trees[page])), got)
check("with nothing selected, or several elements, it opens the panel's own page",
      helpdocs.element_topic([]) == helpdocs.PANEL
      and helpdocs.element_topic([kinds[0][0], kinds[9][0]]) == helpdocs.PANEL
      and helpdocs.PANEL in trees)

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


base = helpdocs.Launch(None, None, "firmware-update", "yelp", helpdocs.PAGES,
                       None, CACHE, frontend="qt")
check("a viewer still starting is watched again",
      follow(base._replace(process=Running([])), 1.0) is helpdocs.WATCHING)
check("one that has lasted WATCH_SECONDS is left to it",
      follow(base._replace(process=Running([])), helpdocs.WATCH_SECONDS) is None)
check("one that exited cleanly, having handed the file on, is left to it",
      follow(base._replace(process=Exited([])), 0.5, have=("yelp",)) is None
      and started == [])

helpdocs._viewer = None
following = follow(base._replace(process=Failed([])), 0.5, have=("yelp",))
check("a Yelp that failed hands over to the built-in viewer, not to itself again",
      isinstance(following, helpdocs.Launch) and following.viewer == helpdocs.BUILTIN
      and argvs() == [builtin("qt", "firmware-update")], (following, started))

helpdocs._viewer = None
following = follow(base._replace(process=Failed([]), topic="fonts#manager",
                                 frontend="gtk"), 0.5, have=("yelp",))
check("one asked for a section hands over that section, in the same toolkit",
      following.viewer == helpdocs.BUILTIN and argvs() == [builtin("gtk", "fonts#manager")],
      (following, started))

helpdocs._viewer = None
following = follow(base._replace(process=Failed([]), viewer=helpdocs.BUILTIN), 0.5,
                   have=("yelp",))
check("with nothing after it, the message names what failed, in words, and how",
      following.process is None and following.message.startswith(
          "LinuxZPL's own help viewer could not show the help (exit status 3).")
      and "Neither Yelp nor" in following.message, following.message)
helpdocs._viewer = None

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

# --- the man page -------------------------------------------------------------------

import linuxzpl  # noqa: E402

MAN = helpdocs.PAGES.parent / "linuxzpl.1"
man_source = MAN.read_text()
# The words a reader sees: font changes dropped, and \- the hyphen it prints.
man_words = re.sub(r"\\f[BIRP]", "", man_source).replace("\\-", "-")

expected, options = [], set()
for action in linuxzpl.build_parser()._actions:
    for option in action.option_strings:
        options.add(option)
        takes = action.nargs != 0 and action.metavar
        value = f"[{action.metavar}]" if action.nargs == "?" else action.metavar
        expected.append(f"{option} {value}" if takes else option)
missing = [e for e in expected if e not in man_words]
check("the man page names every option linuxzpl.py takes, with its value",
      not missing and len(expected) >= 7, (missing, expected))
stale = sorted(set(re.findall(r"(?<![\w-])--[a-z][a-z-]*", man_words)) - options)
check("and no option linuxzpl.py does not take", not stale, stale)
check("its SYNOPSIS names the program as it is run",
      ".B linuxzpl.py" in man_source)

if shutil.which("groff"):
    warned = subprocess.run(["groff", "-man", "-ww", "-z", str(MAN)],
                            capture_output=True, text=True)
    check("groff finds nothing in the man page to warn about",
          warned.returncode == 0 and not warned.stderr, warned.stderr)
else:
    print("SKIPPED: groff is not installed, so the man page is not set")

print("ALL HELP CHECKS PASSED" if not failures else f"{failures} FAILED")
sys.exit(1 if failures else 0)
