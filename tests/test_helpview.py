"""The built-in help viewer: zplcore/helpview.py turning the pages in help/C
into what both viewers draw, its search, Back and Forward and text size, and
helpdocs.open_pdf, which the viewers' Print uses.

No PDF viewer is really started: shutil.which and helpdocs._start are
replaced for those checks, as tests/test_help.py does. The PDFs themselves
are really made, into the throwaway cache _isolate sets.
"""
import os
import re
import shutil
import sys
import tempfile
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import _isolate  # noqa: F401

from zplcore import helpdocs, helppdf, helpview  # noqa: E402
from zplcore.helpview import (Code, Heading, Image, ListBlock, Note,  # noqa: E402
                              PageLinks, Para, Run, Table, Terms, Title,
                              TopicLinks, Trail)

failures = 0


def check(name, cond, extra=""):
    global failures
    print(('ok   ' if cond else 'FAIL ') + name + (f"  [{extra}]" if not cond and extra != "" else ""))
    if not cond:
        failures += 1


M = "{%s}" % helpdocs.MALLARD
HELP = helpview.Help()
docs = {d.id: d for d in HELP.documents()}
check("every page becomes a document", set(docs) == set(helpdocs.topics()), sorted(docs))


# --- walking a document ---------------------------------------------------------

def walk(blocks):
    """Every block, nested ones too."""
    for block in blocks:
        yield block
        if isinstance(block, ListBlock):
            for item in block.items:
                yield from walk(item)
        elif isinstance(block, Terms):
            for _terms, inner in block.items:
                yield from walk(inner)
        elif isinstance(block, Note):
            yield from walk(block.blocks)
        elif isinstance(block, Table):
            for row in block.rows:
                for cell in row:
                    yield from walk(cell)


def all_runs(blocks):
    for block in walk(blocks):
        for name in ("runs", "title"):
            value = getattr(block, name, None)
            if isinstance(value, tuple) and all(isinstance(r, Run) for r in value):
                yield from value
        if isinstance(block, Terms):
            for terms, _inner in block.items:
                for term in terms:
                    yield from term
        if isinstance(block, (TopicLinks, PageLinks)):
            for entry in (block.entries if isinstance(block, TopicLinks)
                          else block.more + block.also):
                yield from entry.desc


def links_in(doc):
    """Every topic the document links to: in its text, its trail, its guide
    lists and its foot."""
    out = []
    for block in walk(doc.blocks):
        if isinstance(block, Trail):
            out += [c.topic for trail in block.trails for c in trail]
        elif isinstance(block, TopicLinks):
            out += [e.topic for e in block.entries]
        elif isinstance(block, PageLinks):
            out += [e.topic for e in block.more + block.also]
    out += [r.link for r in all_runs(doc.blocks) if r.link]
    return out


KNOWN = (Trail, Title, Heading, Para, Code, ListBlock, Terms, Note, Image, Table,
         TopicLinks, PageLinks)
odd = [(d.id, type(b).__name__) for d in docs.values() for b in walk(d.blocks)
       if not isinstance(b, KNOWN)]
check("every block is one the viewers know how to draw", not odd, odd)

bad = []
for doc in docs.values():
    for topic in links_in(doc):
        try:
            HELP.check(topic)
        except ValueError as e:
            bad.append(f"{doc.id}: {e}")
check("every link, trail and listed topic names a page and section that exist",
      not bad, bad)

styles = {s for d in docs.values() for r in all_runs(d.blocks) for s in r.styles}
check("runs carry only the styles the viewers draw",
      styles <= {"bold", "italic", "mono", "gui", "key", "join"}, styles)


def words(text):
    return re.findall(r"\w+", text.casefold())


lost = {}
for pid, doc in docs.items():
    root = ET.parse(helpdocs.PAGES / f"{pid}.page").getroot()
    written = Counter()
    for child in root:
        if child.tag != M + "info":
            written.update(words(" ".join(child.itertext())))
    shown = Counter(words(" ".join(r.text for r in all_runs(doc.blocks))))
    missing = written - shown
    if missing:
        lost[pid] = sorted(missing)[:10]
check("no word a page holds is lost on its way into the viewer", not lost, lost)

# --- what the automatic links hold ----------------------------------------------

links = helppdf.Links(helppdf.load(helpdocs.PAGES))
wrong = []
for pid, doc in docs.items():
    guides = links.guides.get(pid, [])
    trail = [b for b in doc.blocks if isinstance(b, Trail)]
    got = [tuple(c.topic for c in t) for t in trail[0].trails] if trail else []
    want = [((g, f"{g}#{s}") if s else (g,)) for g, s in guides]
    if got != want:
        wrong.append(f"{pid} trail {got} != {want}")
    listed = [e.topic for b in doc.blocks if isinstance(b, TopicLinks) for e in b.entries]
    want = [t for (g, _s), ts in links.topics.items() if g == pid for t in ts]
    if sorted(listed) != sorted(want):
        wrong.append(f"{pid} lists {sorted(listed)} != {sorted(want)}")
    foot = [b for b in doc.blocks if isinstance(b, PageLinks)]
    also = [e.topic for e in foot[0].also] if foot else []
    more = [e.topic for e in foot[0].more] if foot else []
    if also != links.sorted(links.seealso[pid]):
        wrong.append(f"{pid} see also {also}")
    if more != [f"{g}#{s}" if s else g for g, s in guides]:
        wrong.append(f"{pid} more information {more}")
    if foot and doc.blocks[-1] is not foot[0]:
        wrong.append(f"{pid}: its foot is not last")
check("trails, guide lists, More Information and See Also hold what helppdf's links do",
      not wrong, wrong)

index = docs["index"]
check("the contents has no trail and no foot, being no one's topic",
      not any(isinstance(b, (Trail, PageLinks)) for b in index.blocks))
first = [b for b in index.blocks if isinstance(b, (Heading, TopicLinks))][:2]
check("the contents lists each section's topics under its heading",
      isinstance(first[0], Heading) and first[0].anchor == "start"
      and isinstance(first[1], TopicLinks)
      and "getting-started" in [e.topic for e in first[1].entries], first)
check("a listed topic carries its title and its description",
      any(e.title == "Design and print a first label"
          and helpview.plain(e.desc).startswith("Set up the printer")
          for e in first[1].entries), first[1])

printing = docs["printing"]
check("a topic starts with its trail, then its title",
      isinstance(printing.blocks[0], Trail) and isinstance(printing.blocks[1], Title)
      and [c.text for c in printing.blocks[0].trails[0]] == ["LinuxZPL Help", "Printing"]
      and helpview.plain(printing.blocks[1].runs) == "Print a label")
foot = printing.blocks[-1]
check("and ends with More Information naming its guide's section, then See Also",
      [(e.topic, e.title) for e in foot.more] == [("index#printing", "Printing")]
      and {"fonts", "resolution"} <= {e.topic for e in foot.also}, foot)

# --- sections, and every place Help opens ---------------------------------------

for pid, doc in docs.items():
    headings = {b.anchor for b in walk(doc.blocks) if isinstance(b, Heading) and b.anchor}
    if doc.anchors != helpdocs.section_ids(helpdocs.PAGES / f"{pid}.page") or not (
            doc.anchors <= headings):
        check(f"{pid}: every section has its anchor on its heading", False,
              (doc.anchors, headings))
        break
else:
    check("every section's id is an anchor on its heading", True)

opened = list(helpdocs.WINDOW_HELP.values()) + [helpdocs.PANEL, helpdocs.CONTENTS]
unknown = []
for topic in opened:
    try:
        page, section = helpdocs.split_topic(HELP.check(topic))
        if section and section not in HELP.document(topic).anchors:
            unknown.append(topic)
    except ValueError:
        unknown.append(topic)
check("every window's Help topic opens in the viewer at its section", not unknown, unknown)

for bad_topic in ("fonts#nowhere", "nowhere", "nowhere#manager"):
    try:
        HELP.document(bad_topic)
        raised = False
    except ValueError:
        raised = True
    check(f"asking the viewer for {bad_topic!r}, which does not exist, is an error", raised)

# --- running text and blocks, element by element --------------------------------

start = docs["getting-started"]
steps = next(b for b in start.blocks if isinstance(b, ListBlock))
runs = list(all_runs((steps,)))
check("<steps> is a list of kind steps", steps.kind == "steps" and len(steps.items) == 6)
check("a <guiseq> is its <gui>s joined by ▸, all in the gui style",
      Run("Settings ▸ Default Printer…", frozenset({"gui"})) in runs,
      [r for r in runs if "gui" in r.styles][:2])
check("a <keyseq> is its keys as keycaps, joined by a + of its own",
      [r.text for r in runs if r.styles & {"key", "join"}][:3] == ["Ctrl", "+", "S"]
      and next(r for r in runs if r.text == "Ctrl").styles == {"key"}
      and next(r for r in runs if r.text == "+").styles == {"join"})
check("a link with no words of its own shows its section's title",
      Run("A printer plugged in by USB", link="printing#usb") in runs,
      [r for r in runs if r.link])
check("<file> is mono", Run(".zpl", frozenset({"mono"})) in runs)
check("white space in running text reads as single spaces, none at either end",
      all("  " not in r.text and "\n" not in r.text for r in runs)
      and not runs[0].text.startswith(" "))
tip = next(b for b in start.blocks if isinstance(b, Note))
check("a tip is a note with its kind, an icon and a label for want of one",
      tip.kind == "tip" and tip.icon == "dialog-information" and tip.label == "Tip"
      and tip.title is None)
warning = [b for b in walk(docs["firmware-update"].blocks) if isinstance(b, Note)]
check("a warning shows the warning icon",
      [(n.kind, n.icon) for n in warning] == [("", "dialog-information"),
                                              ("warning", "dialog-warning")], warning)

codes = [b for b in docs["templates"].blocks if isinstance(b, Code)]
check("a <screen> and a <code> keep their lines, less the break after the tag",
      [c.screen for c in codes] == [True, False]
      and helpview.plain(codes[1].runs).startswith("from zplcore import Label\n\nlabel =")
      and helpview.plain(codes[1].runs).endswith('label.save("out.zpl")')
      and all(r.styles == {"mono"} for c in codes for r in c.runs), codes)

table = next(b for b in docs["keyboard-shortcuts"].blocks if isinstance(b, Table))
check("a table keeps its <thead> rows as its heading",
      table.header_rows == 1 and len(table.rows[0]) == 2
      and helpview.plain(table.rows[0][0][0].runs) == "Keys", table.rows[:1])
terms = next(b for b in printing.blocks if isinstance(b, Terms))
check("<terms> keeps each item's term over its own blocks",
      helpview.plain(terms.items[0][0][0]) == "Printer"
      and isinstance(terms.items[0][1][0], Para))

edited = Path(tempfile.mkdtemp(prefix="linuxzpl-viewer-pages-")) / "C"
shutil.copytree(helpdocs.PAGES, edited)
page = edited / "index.page"
page.write_text(page.read_text().replace("</section>",
                                         "<quote><p>Not shown</p></quote></section>", 1))
try:
    helpview.Help(edited).document("index")
    check("Mallard the viewer does not know is refused", False)
except helppdf.UnsupportedMallard as e:
    check("Mallard the viewer does not know is refused, naming page and element",
          "index.page" in str(e) and "<quote>" in str(e) and "viewer" in str(e), e)

# --- searching -------------------------------------------------------------------


def found(query):
    doc = HELP.search(query)
    lists = [b for b in doc.blocks if isinstance(b, TopicLinks)]
    return doc, [e.topic for e in lists[0].entries] if lists else []


doc, hits = found("FIRMWARE")
check("a search lists the page with the words in its title first, in any letter case",
      hits and hits[0] == "firmware-update" and doc.page is None
      and doc.id == "search:FIRMWARE" and helpview.is_search(doc.id)
      and doc.title == "Search results for “FIRMWARE”", (doc.id, hits))
_, hits_body = found("forced download")
check("a page whose words are only in a section is linked at that section",
      "firmware-update#refused" in hits_body, hits_body)
_, by_desc = found("darkness")
check("a page with the words in its description comes before one with them in its text",
      by_desc and by_desc[0] == "printing", by_desc)
_, both = found("printer firmware")
_, one = found("printer zzzznotaword")
check("every word must be there", "firmware-update" in both and one == [], (both, one))
doc, hits = found("zzzznotaword")
check("a search that finds nothing says so",
      hits == [] and any(isinstance(b, Para) and "No pages found" in helpview.plain(b.runs)
                         for b in doc.blocks))
check("a search document comes back through document() by its topic",
      HELP.document("search:firmware").blocks == HELP.search("firmware").blocks)
check("the results show each page's title and description",
      HELP.search("firmware").blocks[1].entries[0].title == "Update a printer's firmware")

# --- Back and Forward --------------------------------------------------------------

h = helpview.History()
check("a new history has nowhere to go", h.current is None and not h.can_back()
      and not h.can_forward())
h.go("index")
h.go("fonts#manager", leaving=120.0)
h.go("printing", leaving=300.0)
check("going somewhere records where the page left was scrolled",
      [(e.topic, e.scroll) for e in h.entries] == [("index", 120.0), ("fonts#manager", 300.0),
                                                   ("printing", None)])
entry = h.back(leaving=40.0)
check("Back returns the page before, scrolled as it was left",
      entry.topic == "fonts#manager" and entry.scroll == 300.0 and h.can_forward())
entry = h.forward(leaving=310.0)
check("Forward comes back, scrolled as it was left too",
      entry.topic == "printing" and entry.scroll == 40.0 and not h.can_forward())
h.back()
h.go("text")
check("going somewhere new clears Forward",
      not h.can_forward() and [e.topic for e in h.entries] == ["index", "fonts#manager", "text"])
h.current.scroll = 99.0
same = h.go("text", leaving=50.0)
check("going to the page shown adds nothing and opens it at its place again",
      len(h.entries) == 3 and same.scroll is None)
check("Back at the start stays there",
      helpview.History().back() is None and h.back().topic == "fonts#manager"
      and h.back().topic == "index" and h.back().topic == "index")

# --- the text size --------------------------------------------------------------------

check("Larger and Smaller step through the sizes and stop at the ends",
      helpview.larger(100) == 110 and helpview.smaller(100) == 90
      and helpview.larger(200) == 200 and helpview.smaller(70) == 70
      and helpview.larger(95) == 100)
folder = Path(tempfile.mkdtemp(prefix="linuxzpl-viewer-settings-"))
ini = folder / "linuxzpl" / "settings.ini"
check("with no settings file the size is 100 %",
      helpview.load_text_size([ini]) == helpview.DEFAULT_TEXT_SIZE == 100)
ini.parent.mkdir(parents=True)
ini.write_text("[grid]\nunit = mm\n\n[printer]\naddress = 10.0.0.1\n")
check("a size is saved", helpview.save_text_size([ini], 140))
check("and read back", helpview.load_text_size([ini]) == 140)
saved = ini.read_text()
check("leaving every other section as it was",
      "[grid]" in saved and "unit = mm" in saved and "address = 10.0.0.1" in saved, saved)
ini.write_text(saved.replace("text_size = 140", "text_size = 137"))
check("a size Larger and Smaller do not reach reads as 100 %",
      helpview.load_text_size([ini]) == 100)
ini.write_text(saved.replace("text_size = 140", "text_size = big"))
check("and so does one that is not a number", helpview.load_text_size([ini]) == 100)
blocked = folder / "not-a-folder"
blocked.write_text("a file where a folder would be")
fallback = folder / "fallback.ini"
check("a settings file that cannot be written falls back to the next",
      helpview.save_text_size([blocked / "settings.ini", fallback], 80)
      and helpview.load_text_size([blocked / "settings.ini", fallback]) == 80)

# --- Print: the page's PDF in a PDF viewer --------------------------------------------

real_which, real_start = helpdocs.shutil.which, helpdocs._start
started = []


class FakeProcess:
    code = None

    def __init__(self, argv):
        self.argv = argv

    def poll(self):
        return self.code


class Failed(FakeProcess):
    code = 2


def fake_start(argv):
    started.append(argv)
    return FakeProcess(argv)


def pdf_for(topic, have, start=fake_start, start_after=""):
    del started[:]
    helpdocs.shutil.which = lambda name: f"/usr/bin/{name}" if name in have else None
    helpdocs._start = start
    try:
        return helpdocs.open_pdf(topic, start_after=start_after)
    finally:
        helpdocs.shutil.which, helpdocs._start = real_which, real_start


opened = pdf_for("fonts#manager", ("yelp", "xdg-open", "evince"))
check("Print opens the page's PDF with xdg-open, never Yelp",
      opened.viewer == "xdg-open" and opened.printing and opened.message is None
      and opened.pdf.name == "fonts.pdf" and opened.pdf.is_file()
      and started == [["/usr/bin/xdg-open", str(opened.pdf)]], (opened, started))
opened = pdf_for("index", ("yelp", "okular", "evince"))
check("with no xdg-open, the first PDF viewer in the list",
      opened.viewer == "evince" and started == [["/usr/bin/evince", str(opened.pdf)]])
opened = pdf_for("index", ("yelp",))
check("with no PDF viewer, nothing starts and the message gives the PDF to print",
      started == [] and opened.process is None and opened.printing
      and "No PDF viewer" in opened.message and str(opened.pdf) in opened.message,
      opened.message)

real_build = helppdf.build
helppdf.build = lambda pages, cache=None, fonts=None: (_ for _ in ()).throw(
    OSError("No space left on device"))
try:
    opened = pdf_for("index", ("xdg-open",))
finally:
    helppdf.build = real_build
check("a PDF that cannot be made says why, and starts nothing",
      started == [] and "No space left on device" in opened.message
      and "yelp" not in opened.message.casefold(), opened.message)

try:
    pdf_for("nowhere", ("xdg-open",))
    raised = False
except ValueError:
    raised = True
check("Print of a page that does not exist is an error", raised and started == [])

failed = pdf_for("fonts", ("xdg-open", "evince"))._replace(process=Failed([]))
helpdocs.shutil.which = lambda name: f"/usr/bin/{name}" if name in ("yelp", "evince") else None
helpdocs._start = fake_start
del started[:]
try:
    following = helpdocs.follow(failed, 0.5)
finally:
    helpdocs.shutil.which, helpdocs._start = real_which, real_start
check("an xdg-open that fails on the PDF hands over to the next PDF viewer, not Yelp",
      isinstance(following, helpdocs.Launch) and following.viewer == "evince"
      and following.printing and started == [["/usr/bin/evince", str(following.pdf)]],
      (following, started))

helpdocs.shutil.which = lambda name: None
try:
    following = helpdocs.follow(failed, 0.5)
finally:
    helpdocs.shutil.which = real_which
check("with none after it, the message says it could not show the page's PDF",
      following.message.startswith("xdg-open could not show the page's PDF (exit status 2).")
      and "No other PDF viewer" in following.message, following.message)

# --- the Qt viewer -------------------------------------------------------------------

try:
    from PySide2.QtCore import QEvent, QPointF, Qt, QUrl
    from PySide2.QtGui import QKeySequence, QMouseEvent, QTextCursor
    from PySide2.QtWidgets import QApplication
except ImportError:
    print("SKIPPED: PySide2 is not installed, so the Qt viewer is not checked")
else:
    import subprocess
    import time
    app = QApplication.instance() or QApplication([])
    from qtui import helpviewer as qviewer

    def settle(cond, seconds=3.0):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            app.processEvents()
            if cond():
                return True
            time.sleep(0.02)
        return cond()

    qt_ini = Path(tempfile.mkdtemp(prefix="linuxzpl-qt-viewer-")) / "settings.ini"
    w = qviewer.HelpWindow(HELP, settings_files=[qt_ini])
    w.resize(900, 600)
    w.show()
    app.processEvents()
    bar = w.page.verticalScrollBar()

    check("Qt: the header has Back, Forward, Search and the menu, Back and Forward greyed",
          set(w.buttons) == {"back", "forward", "search", "menu"}
          and not w.buttons["back"].isEnabled() and not w.buttons["forward"].isEnabled())
    menu = [a.objectName() or None for a in w.menu.actions()]
    check("Qt: the menu holds the shared list, in order",
          [m or None for m in menu] == list(helpview.MENU), menu)
    keys = {name: [k.toString() for k in w.actions[name].shortcuts()]
            for name in helpview.COMMANDS}
    want = {name: [QKeySequence(k).toString() for k in ks]
            for name, (_l, ks) in helpview.COMMANDS.items()}
    check("Qt: every command has its keys", keys == want, keys)

    shown = []
    for pid in sorted(docs):
        w.open(pid)
        if (w.title_label.text() != docs[pid].title or w.windowTitle() != docs[pid].title
                or w.page.document().isEmpty()):
            shown.append(pid)
    check("Qt: every page opens, its title in the header and the window's",
          not shown and w.subtitle_label.text() == "LinuxZPL Help", shown)

    def anchor_top(name):
        """Where the heading with anchor `name` starts on the page."""
        qdoc = w.page.document()
        block = qdoc.begin()
        while block.isValid():
            it = block.begin()
            while not it.atEnd():
                if name in it.fragment().charFormat().anchorNames():
                    return qdoc.documentLayout().blockBoundingRect(block).top()
                it += 1
            block = block.next()
        return None

    misplaced, moved = [], 0
    for topic in sorted(set(helpdocs.WINDOW_HELP.values())):
        w.open(topic)
        _page, section = helpdocs.split_topic(topic)
        if not section:
            continue
        top = anchor_top(section)
        if top is None or abs(bar.value() - min(round(top), bar.maximum())) > 2:
            misplaced.append((topic, top, bar.value(), bar.maximum()))
        moved += bar.value() > 0
    check("Qt: each window's Help topic opens scrolled to its section",
          not misplaced and moved >= 5, (misplaced, moved))

    def click_text(text):
        """Click the middle of the first place `text` is shown, as a mouse does."""
        found = w.page.document().find(text)
        cursor = QTextCursor(w.page.document())
        cursor.setPosition((found.selectionStart() + found.selectionEnd()) // 2)
        point = QPointF(w.page.cursorRect(cursor).center())
        for kind in (QEvent.MouseButtonPress, QEvent.MouseButtonRelease):
            QApplication.sendEvent(w.page.viewport(), QMouseEvent(
                kind, point, Qt.LeftButton,
                Qt.LeftButton if kind == QEvent.MouseButtonPress else Qt.NoButton,
                Qt.NoModifier))
        app.processEvents()

    w.history = helpview.History()
    w.open("index")
    click_text("Design and print a first label")
    check("Qt: clicking a topic link opens that page",
          w.document.id == "getting-started" and w.buttons["back"].isEnabled(), w.document.id)
    click_text("A printer plugged in by USB")
    check("Qt: a link to another page's section opens it there",
          w.history.current.topic == "printing#usb" and w.document.id == "printing"
          and abs(bar.value() - min(round(anchor_top("usb")), bar.maximum())) <= 2,
          w.history.current)
    bar.setValue(bar.maximum() // 2)
    left_at = bar.value()
    click_text("Printing")  # the trail's link to its guide's section
    check("Qt: the trail's links lead back to the contents",
          w.history.current.topic == "index#printing", w.history.current)
    w.actions["back"].trigger()
    check("Qt: Back returns to the page before, scrolled where it was left",
          w.document.id == "printing" and bar.value() == left_at
          and w.buttons["forward"].isEnabled(), (w.document.id, bar.value(), left_at))
    w.actions["forward"].trigger()
    check("Qt: and Forward goes on again",
          w.history.current.topic == "index#printing" and not w.buttons["forward"].isEnabled())
    w.actions["contents"].trigger()
    check("Qt: Contents opens the contents at its top",
          w.document.id == "index" and bar.value() == 0)
    w.actions["viewer_help"].trigger()
    check("Qt: F1 opens the viewer's own page, or the contents until there is one",
          w.document.id == HELP.viewer_topic())

    opened_urls = []
    real_open_url = qviewer.QDesktopServices.openUrl
    qviewer.QDesktopServices.openUrl = lambda url: opened_urls.append(url.toString())
    try:
        w.follow_link(QUrl("https://www.zebra.com/"))
    finally:
        qviewer.QDesktopServices.openUrl = real_open_url
    check("Qt: a link out of the help goes to the desktop's browser",
          opened_urls == ["https://www.zebra.com/"] and w.document.id == HELP.viewer_topic())

    w.actions["search"].trigger()
    check("Qt: Search shows the search bar, its button pressed",
          w.search_bar.isVisible() and w.buttons["search"].isChecked())
    w.search_entry.setText("  firmware  ")
    w.search_entry.returnPressed.emit()
    check("Qt: Enter shows the results as a page of their own",
          w.document.id == "search:firmware"
          and w.title_label.text() == "Search results for “firmware”"
          and not w.actions["print"].isEnabled(), w.document.id)
    click_text("Update a printer's firmware")
    check("Qt: a result opens its page, and Print can print it",
          w.document.id == "firmware-update" and w.actions["print"].isEnabled())
    w.actions["back"].trigger()
    check("Qt: Back returns to the results", w.document.id == "search:firmware")
    w.set_search_shown(False)
    check("Qt: the search bar hides again",
          not w.search_bar.isVisible() and not w.buttons["search"].isChecked())

    w.open("printing")
    w.actions["find"].trigger()
    w.find_entry.setText("PRINTER")
    count = len(w.matches)
    check("Qt: Find marks every place the words are, in any letter case",
          w.find_bar.isVisible() and count > 3 and len(w.page.extraSelections()) == count
          and w.find_count.text() == f"1 of {count}", (count, w.find_count.text()))
    w.find_next(1)
    check("Qt: Next goes to the next", w.find_count.text() == f"2 of {count}")
    w.find_next(-1)
    w.find_next(-1)
    check("Qt: Previous from the first wraps to the last",
          w.find_count.text() == f"{count} of {count}")
    w.open("fonts")
    check("Qt: on another page Find looks again",
          w.find_count.text().startswith("1 of") and len(w.matches) != count)
    w.find_entry.setText("zzzznotaword")
    check("Qt: words not on the page say so",
          w.matches == [] and w.find_count.text() == "Not found")
    w.hide_find()
    check("Qt: closing Find clears its marks",
          not w.find_bar.isVisible() and w.page.extraSelections() == [])

    size_before = w.page.document().defaultFont().pointSizeF()
    w.actions["larger"].trigger()
    check("Qt: Larger Text sets the page larger, and remembers it",
          w.text_size == 110 and helpview.load_text_size([qt_ini]) == 110
          and abs(w.page.document().defaultFont().pointSizeF() - size_before * 1.1) < 0.05)
    again = qviewer.HelpWindow(HELP, settings_files=[qt_ini])
    check("Qt: a new window opens at the size remembered", again.text_size == 110)
    again.close()
    for _ in range(12):
        w.actions["smaller"].trigger()
    check("Qt: Smaller Text stops at the smallest, and greys itself there",
          w.text_size == helpview.TEXT_SIZES[0] and not w.actions["smaller"].isEnabled())
    w.set_text_size(100)

    printed, said = [], []
    real_open_pdf = helpdocs.open_pdf
    helpdocs.open_pdf = lambda topic, *a, **k: printed.append(topic) or helpdocs.Launch(
        None, "No PDF viewer was found to print the page from.", topic, printing=True)
    w.say = said.append
    try:
        w.open("fonts#manager")
        w.actions["print"].trigger()
    finally:
        helpdocs.open_pdf = real_open_pdf
    check("Qt: Print opens the page's PDF, and says why if it cannot",
          printed == ["fonts"] and said == ["No PDF viewer was found to print the page from."],
          (printed, said))

    read_end, write_end = os.pipe()
    w.listen(read_end)
    os.write(write_end, b"printing#usb\nnowhere\nfon")
    check("Qt: a topic from the designer opens in the same window",
          settle(lambda: w.history.current.topic == "printing#usb"), w.history.current)
    os.write(write_end, b"ts#manager\n")
    check("Qt: one that does not exist is passed over, and one sent in pieces arrives whole",
          settle(lambda: w.history.current.topic == "fonts#manager")
          and [e.topic for e in w.history.entries[-2:]] == ["printing#usb", "fonts#manager"],
          w.history.entries[-3:])
    os.close(write_end)
    check("Qt: when the designer's end closes, the window stops listening and stays",
          settle(lambda: not w._notifier.isEnabled()) and w.isVisible())
    os.close(read_end)
    w.close()

    run = subprocess.run([sys.executable, str(helpdocs.PAGES.parent.parent / "linuxzpl.py"),
                          "--qt", "--show-help", "fonts#nowhere"],
                         capture_output=True, text=True, timeout=60)
    check("Qt: --show-help with a topic that does not exist says so and exits 1",
          run.returncode == 1 and "no section 'nowhere'" in run.stderr, run.stderr)

print("ALL HELP VIEWER CHECKS PASSED" if not failures else f"{failures} FAILED")
sys.exit(1 if failures else 0)
