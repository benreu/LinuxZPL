"""
The help pages as the built-in help viewer shows them, for a system without
Yelp. No toolkit is imported here: each page becomes a model of runs and
blocks, and qtui/helpviewer.py and gtkui/helpviewer.py draw that model, so
the two viewers cannot come to show a page differently.

The pages are read with helppdf's own reader, and the model follows
helppdf's writer element for element, so a page shows the same words in the
viewer as in its PDF. Mallard's automatic links go where helppdf puts them,
which is where Yelp puts them: the trail back to the guide above the title,
the topics that name a guide after its text (or at the end of the section
they name), and "See also" at the end. Anything the writer does not know is
refused here too, naming the page and the element, so a page can never lose
words in the viewer without the test suite saying so.

The look is Yelp 42's, measured from its screenshots of these pages: grey
titles and headings in light type with a rule under each section heading,
blue topic links over a grey description, <gui> words in grey, keys drawn as
keycaps, steps beside a yellow bar, notes in a grey box behind an icon, a
grey band holding the trail, and under a blue rule at the foot of a topic,
"More Information" (the guides that list it) and "See Also". The foot is
where Yelp and the PDF differ: helppdf has the trail only, and its "See
also" as a heading over a list.

Also here, for both viewers: searching the pages, Back and Forward, and the
text size remembered in settings.ini.
"""

import configparser
import os
import re
from pathlib import Path
from typing import FrozenSet, List, NamedTuple, Optional, Tuple

from . import helpdocs
from .helppdf import (NOTE_LABELS, NS, Links, Page, UnsupportedMallard,
                      _section, _text, link_title, load)

# --- the look ---------------------------------------------------------------------

TEXT = "#000000"
HEADING = "#4b4b49"       # the title, headings, keys and table headings
GREY = "#4d4d4b"          # a guide's descriptions, <gui> words and the trail
FAINT = "#666664"         # a See Also description, after its dash
KEY_JOIN = "#757573"      # the + between keys
LINK = "#2b6bb9"          # links, and the rule over More Information
RULE = "#c0bfbc"          # under a section heading; keycap and code borders
BAND = "#f4f4f4"          # behind the trail, keys, inline code, screens, notes
NOTE_EDGE = "#eaeae9"
STEPS_BAR = "#f8e45c"
STEPS_EDGE = "#f4f4f4"
TABLE_HEAD = "#eaeae9"
FOUND = "#f8e45c"         # Find's matches
FOUND_CURRENT = "#f5c211"

# Body text in points at 100 %, and every other kind of text as a multiple of
# it, as Yelp sets them.
BASE_POINTS = 10.0
SCALE = {"title": 2.25, "h1": 1.9, "h2": 1.5, "h3": 1.25, "topic": 1.2,
         "desc": 0.92, "trail": 0.92}
# The indent of a definition, a list item or a note's text, in body ems.
INDENT_EMS = 1.5

GUI_SEP = " ▸ "      # Settings ▸ Local Fonts…
TRAIL_SEP = " › "    # LinuxZPL Help › Printing
TRAIL_END = " »"     # ... »
DESC_DASH = " — "    # See Also's "title — description"
MORE_INFORMATION = "More Information"
SEE_ALSO = "See Also"
SEARCH_PREFIX = "search:"

MONO_INLINE = {"code", "cmd", "file", "sys", "input", "output"}
# The toolkit icon each kind of note shows, by its freedesktop name; the
# frontends fall back to the note's label where the theme has none.
NOTE_ICONS = {"": "dialog-information", "tip": "dialog-information",
              "advanced": "dialog-information", "important": "dialog-warning",
              "warning": "dialog-warning", "caution": "dialog-warning",
              "danger": "dialog-error", "bug": "dialog-error"}


# --- the model ----------------------------------------------------------------------

class Run(NamedTuple):
    """Some words in one style. `styles` holds any of "bold", "italic",
    "mono", "gui", "key" (a keycap) and "join" (the + between keys).
    `link` is a topic in the help ("fonts#manager"); `href` is an address
    outside it."""
    text: str
    styles: FrozenSet[str] = frozenset()
    link: Optional[str] = None
    href: Optional[str] = None


Runs = Tuple[Run, ...]


class Crumb(NamedTuple):
    text: str
    topic: str


class Trail(NamedTuple):
    """The way back to each guide that lists this page: one line per guide,
    each a row of links."""
    trails: Tuple[Tuple[Crumb, ...], ...]


class Title(NamedTuple):
    runs: Runs


class Heading(NamedTuple):
    level: int                # 1 for a section, 2 for a section in one...
    runs: Runs
    anchor: Optional[str]     # the section's id, which a topic may name


class Para(NamedTuple):
    runs: Runs


class Code(NamedTuple):
    runs: Runs                # its lines as written, every run mono
    screen: bool              # a <screen>, on grey; a <code> is on white


class ListBlock(NamedTuple):
    kind: str                 # "bullet", "numbered", or "steps" beside the bar
    title: Optional[Runs]
    items: Tuple[tuple, ...]  # each item a tuple of blocks


class Terms(NamedTuple):
    title: Optional[Runs]
    items: Tuple[Tuple[Tuple[Runs, ...], tuple], ...]  # (its terms, its blocks)


class Note(NamedTuple):
    kind: str                 # a key of NOTE_LABELS, or "" for a plain note
    title: Optional[Runs]     # its own title, shown over its text
    blocks: tuple
    icon: str                 # from NOTE_ICONS
    label: str                # "Tip", "Warning"..., where no icon can be shown


class Image(NamedTuple):
    path: Path
    width: float              # in pixels, as Mallard gives them; 0 is the image's own
    height: float


class Table(NamedTuple):
    title: Optional[Runs]
    rows: Tuple[Tuple[tuple, ...], ...]  # each cell a tuple of blocks
    header_rows: int


class TopicEntry(NamedTuple):
    topic: str
    title: str
    desc: Runs


class TopicLinks(NamedTuple):
    """Topics as a guide lists them: each title, linked, over its
    description."""
    entries: Tuple[TopicEntry, ...]


class PageLinks(NamedTuple):
    """The foot of a topic, under a blue rule: More Information, the guides
    that list it, each a link alone; then See Also, each link followed by a
    dash and its description."""
    more: Tuple[TopicEntry, ...]
    also: Tuple[TopicEntry, ...]


class Document(NamedTuple):
    """One page as the viewer shows it, or a page of search results."""
    id: str                   # the page's id, or "search:<words>"
    title: str
    blocks: tuple
    anchors: FrozenSet[str]   # the ids a topic may name after #
    page: Optional[str]       # the page's id; None for search results


def is_search(topic: str) -> bool:
    return topic.startswith(SEARCH_PREFIX)


# --- runs ---------------------------------------------------------------------------

def _collapse(runs: List[Run]) -> Runs:
    """Running text as it reads: every stretch of white space one space,
    none at the start or end, and runs in the same style joined up."""
    out: List[Run] = []
    for run in runs:
        text = re.sub(r"\s+", " ", run.text)
        if text.startswith(" ") and (not out or out[-1].text.endswith(" ")):
            text = text[1:]
        if not text:
            continue
        if out and out[-1]._replace(text="") == run._replace(text=""):
            out[-1] = out[-1]._replace(text=out[-1].text + text)
        else:
            out.append(run._replace(text=text))
    if out:
        out[-1] = out[-1]._replace(text=out[-1].text.rstrip())
        if not out[-1].text:
            out.pop()
    return tuple(out)


def plain(runs: Runs) -> str:
    return "".join(run.text for run in runs)


class _Reader:
    """The blocks of one page, as helppdf._Writer makes its flowables."""

    def __init__(self, help_: "Help", page: Page):
        self.help, self.page, self.pid = help_, page, page.id
        self.pages, self.links = help_.pages, help_.links

    def refuse(self, el, where: str = ""):
        tag = el.tag[len(NS):] if isinstance(el.tag, str) and el.tag.startswith(NS) else el.tag
        raise UnsupportedMallard(
            f"{self.pid}.page: <{tag}>{' ' + where if where else ''} "
            f"is not supported in the help viewer")

    def tag(self, el) -> str:
        if not isinstance(el.tag, str) or not el.tag.startswith(NS):
            self.refuse(el)
        return el.tag[len(NS):]

    # inline -------------------------------------------------------------------

    def runs(self, el) -> Runs:
        return _collapse(self.inline(el, Run("")))

    def inline(self, el, style: Run) -> List[Run]:
        """The runs of `el`'s text and children, each in `style` (a Run whose
        text is unused) plus whatever its elements add."""
        out = [style._replace(text=el.text)] if el.text else []
        for child in el:
            out += self.inline_element(child, style)
            if child.tail:
                out.append(style._replace(text=child.tail))
        return out

    def inline_element(self, el, style: Run) -> List[Run]:
        tag = self.tag(el)

        def add(*names):
            return style._replace(styles=style.styles | set(names))

        if tag == "em":
            strong = "strong" in (el.get("style") or "").split()
            return self.inline(el, add("bold" if strong else "italic"))
        if tag == "gui":
            return self.inline(el, add("gui"))
        if tag == "guiseq":
            if any(self.tag(g) != "gui" for g in el):
                self.refuse(el, "holding anything but <gui>")
            out: List[Run] = []
            for i, gui in enumerate(el):
                if i:
                    out.append(add("gui")._replace(text=GUI_SEP))
                out += self.inline_element(gui, style)
            return out
        if tag == "keyseq":
            joiner = "+" if el.get("type", "combo") == "combo" else " "
            out = []
            for i, key in enumerate(el):
                if i:
                    out.append(add("join")._replace(text=joiner))
                out += self.inline_element(key, style)
            return out
        if tag in MONO_INLINE:
            return self.inline(el, add("mono"))
        if tag == "key":
            return self.inline(el, add("key"))
        if tag == "var":
            return self.inline(el, add("italic"))
        if tag in ("app", "span"):
            return self.inline(el, style)
        if tag == "link":
            return self.link(el, style)
        self.refuse(el, "in running text")

    def link(self, el, style: Run) -> List[Run]:
        xref, href = el.get("xref"), el.get("href")
        if xref is not None:
            target, _, section = xref.partition("#")
            target = target or self.pid
            if target not in self.pages:
                raise UnsupportedMallard(f"{self.pid}.page: links to {xref!r}, no such page")
            root = self.pages[target].root
            if section and _section(root, section) is None:
                raise UnsupportedMallard(f"{self.pid}.page: links to {xref!r}, no such section")
            linked = style._replace(link=f"{target}#{section}" if section else target)
            out = self.inline(el, linked)
            if not plain(_collapse(out)).strip():
                title = (_text(_section(root, section).find(NS + "title")) if section
                         else link_title(root))
                out = [linked._replace(text=title)]
            return out
        if href:
            linked = style._replace(href=href)
            out = self.inline(el, linked)
            return out if plain(_collapse(out)).strip() else [linked._replace(text=href)]
        self.refuse(el, "with neither xref nor href")

    # blocks -------------------------------------------------------------------

    def blocks(self, parent, sections: bool = False) -> tuple:
        out = []
        for child in parent:
            tag = self.tag(child)
            if tag in ("info", "title") or (sections and tag == "section"):
                continue
            out.append(self.block(child))
        return tuple(out)

    def titled(self, el) -> Optional[Runs]:
        title = el.find(NS + "title")
        return self.runs(title) if title is not None else None

    def block(self, el):
        tag = self.tag(el)
        if tag == "p":
            return Para(self.runs(el))
        if tag in ("code", "screen"):
            runs = self.inline(el, Run("", frozenset({"mono"})))
            text = plain(runs)
            # Its lines as written, less the line break after the opening tag
            # and any white space before the closing one, as helppdf does.
            lead = len(text) - len(text.lstrip("\n"))
            keep = len(text.rstrip()) - lead
            out = []
            for run in runs:
                cut = min(lead, len(run.text))
                lead -= cut
                text = run.text[cut:][:max(keep, 0)]
                keep -= len(text)
                if text:
                    out.append(run._replace(text=text))
            return Code(tuple(out), tag == "screen")
        if tag in ("list", "steps"):
            kind = ("steps" if tag == "steps" else
                    "numbered" if el.get("type") == "numbered" else "bullet")
            items = []
            for item in el:
                if self.tag(item) == "title":
                    continue
                if self.tag(item) != "item":
                    self.refuse(item, f"in <{tag}>")
                items.append(self.blocks(item) or (Para(()),))
            return ListBlock(kind, self.titled(el), tuple(items))
        if tag == "terms":
            items = []
            for item in el:
                if self.tag(item) == "title":
                    continue
                if self.tag(item) != "item":
                    self.refuse(item, "in <terms>")
                terms = tuple(self.runs(t) for t in item.findall(NS + "title"))
                items.append((terms, self.blocks(item)))
            return Terms(self.titled(el), tuple(items))
        if tag == "note":
            kind = next((s for s in (el.get("style") or "").split() if s in NOTE_LABELS), "")
            return Note(kind, self.titled(el), self.blocks(el), NOTE_ICONS[kind],
                        NOTE_LABELS.get(kind, "Note"))
        if tag == "media":
            if el.get("type", "image") != "image" or not el.get("src"):
                self.refuse(el, "that is not an image with a src")
            path = self.page.path.parent / el.get("src")
            if not path.is_file():
                raise UnsupportedMallard(f"{self.pid}.page: no image {el.get('src')!r}")
            return Image(path, float(el.get("width") or 0), float(el.get("height") or 0))
        if tag == "table":
            rows, header = [], 0
            for child in el:
                ctag = self.tag(child)
                if ctag == "title":
                    continue
                group = ([child] if ctag == "tr" else list(child)
                         if ctag in ("thead", "tbody", "tfoot") else None)
                if group is None:
                    self.refuse(child, "in <table>")
                for tr in group:
                    if self.tag(tr) != "tr":
                        self.refuse(tr, f"in <{ctag}>")
                    cells = []
                    for td in tr:
                        if self.tag(td) not in ("td", "th"):
                            self.refuse(td, "in <tr>")
                        cells.append(self.blocks(td))
                    rows.append(tuple(cells))
                    if ctag == "thead":
                        header += 1
            width = max((len(r) for r in rows), default=1)
            rows = [r + ((),) * (width - len(r)) for r in rows]
            return Table(self.titled(el), tuple(rows), header)
        self.refuse(el)

    # the page -----------------------------------------------------------------

    def topic_links(self, ids) -> TopicLinks:
        entries = []
        for tid in self.links.sorted(ids):
            entries.append(self.help.entry(tid))
        return TopicLinks(tuple(entries))

    def guided(self, section: Optional[str]) -> tuple:
        ids = self.links.topics.get((self.pid, section), ())
        return (self.topic_links(ids),) if ids else ()

    def trail(self) -> tuple:
        trails = []
        for guide, section in self.links.guides.get(self.pid, ()):
            root = self.pages[guide].root
            crumbs = [Crumb(link_title(root), guide)]
            if section:
                crumbs.append(Crumb(_text(_section(root, section).find(NS + "title")),
                                    f"{guide}#{section}"))
            trails.append(tuple(crumbs))
        return (Trail(tuple(trails)),) if trails else ()

    def section(self, el, level: int) -> list:
        sid = el.get("id") or None
        out = [Heading(level, self.runs(el.find(NS + "title")), sid)]
        out += self.blocks(el, sections=True)
        out += self.guided(sid) if sid else ()
        for sub in el.findall(NS + "section"):
            out += self.section(sub, level + 1)
        return out

    def document(self) -> Document:
        root = self.page.root
        title = root.find(NS + "title")
        out = list(self.trail())
        out.append(Title(self.runs(title)))
        out += self.blocks(root, sections=True)
        out += self.guided(None)
        for section in root.findall(NS + "section"):
            out += self.section(section, 1)
        more = tuple(self.help.entry(f"{guide}#{section}" if section else guide,
                                     guide_section=True)
                     for guide, section in self.links.guides.get(self.pid, ()))
        also = self.topic_links(self.links.seealso[self.pid]).entries
        if more or also:
            out.append(PageLinks(more, also))
        anchors = frozenset(s.get("id") for s in root.iter(NS + "section") if s.get("id"))
        return Document(self.pid, _text(title), tuple(out), anchors, self.pid)


# --- the help as a whole ------------------------------------------------------------

class Help:
    """Every page, read once. Pages become Documents when first shown."""

    def __init__(self, pages_dir: Path = helpdocs.PAGES):
        self.pages_dir = Path(pages_dir)
        self.pages = load(self.pages_dir)
        self.links = Links(self.pages)
        self._documents = {}

    def check(self, topic: str) -> str:
        """`topic` itself if it names a page here, and a section of that page
        when it names one; ValueError, as helpdocs.launch raises, if not."""
        page, section = helpdocs.split_topic(topic)
        if page not in self.pages:
            raise ValueError(f"no help page {page!r} in {self.pages_dir}")
        if section and _section(self.pages[page].root, section) is None:
            raise ValueError(f"no section {section!r} in help page {page!r}")
        return topic

    def document(self, topic: str) -> Document:
        """The page `topic` names - its section, if it names one, is where
        the viewer scrolls to - or the results of a "search:" topic."""
        if is_search(topic):
            return self.search(topic[len(SEARCH_PREFIX):])
        page, _section_id = helpdocs.split_topic(self.check(topic))
        if page not in self._documents:
            self._documents[page] = _Reader(self, self.pages[page]).document()
        return self._documents[page]

    def entry(self, topic: str, guide_section: bool = False) -> TopicEntry:
        """A page as a guide or a search lists it: its title, linked, with
        its description. A section of a page is "Page › Section", or, as
        More Information names a guide's section, the section's title
        alone."""
        page, section = helpdocs.split_topic(topic)
        root = self.pages[page].root
        info = root.find(NS + "info")
        desc = info.find(NS + "desc") if info is not None else None
        reader = _Reader(self, self.pages[page])
        title = link_title(root)
        if section:
            heading = _text(_section(root, section).find(NS + "title"))
            title = heading if guide_section else title + TRAIL_SEP + heading
        return TopicEntry(topic, title, reader.runs(desc) if desc is not None else ())

    def documents(self) -> List[Document]:
        return [self.document(pid) for pid in sorted(self.pages)]

    # searching -----------------------------------------------------------------

    def _words(self, el, skip=("info", "title", "section")) -> str:
        """The words of `el`'s own text, its sections left out."""
        parts = [el.text or ""]
        for child in el:
            if child.tag[len(NS):] not in skip:
                parts.append(" ".join(child.itertext()))
            parts.append(child.tail or "")
        return " ".join(" ".join(parts).split()).casefold()

    def search(self, query: str) -> Document:
        """The pages holding every word of `query`, in any letter case: first
        those with every word in their title, then in title and description,
        then in one section's title, and then in one section's text or the
        page's own. A page found by a section is linked at that section."""
        query = " ".join(query.split())
        words = query.casefold().split()
        found = []
        for pid, page in self.pages.items():
            root = page.root
            info = root.find(NS + "info")
            title = (_text(root.find(NS + "title")) + " " + link_title(root)).casefold()
            desc = _text(info.find(NS + "desc")).casefold() if info is not None else ""

            def every(text):
                return all(w in text for w in words)

            sections = [(s.get("id"), _text(s.find(NS + "title")).casefold(), s)
                        for s in root.iter(NS + "section") if s.get("id")]
            hit = None
            if every(title):
                hit = (0, pid)
            elif every(title + " " + desc):
                hit = (1, pid)
            else:
                named = next((sid for sid, stitle, _s in sections if every(stitle)), None)
                if named:
                    hit = (2, f"{pid}#{named}")
                else:
                    inside = next((sid for sid, stitle, s in sections
                                   if every(stitle + " " + self._words(s))), None)
                    if inside:
                        hit = (3, f"{pid}#{inside}")
                    elif every(" ".join([title, desc, self._words(root)]
                                        + [self._words(s) for _i, _t, s in sections])):
                        hit = (3, pid)
            if words and hit:
                found.append((hit[0], link_title(root).casefold(), hit[1]))
        found.sort()
        heading = f"Search results for “{query}”"
        blocks: list = [Title((Run(heading),))]
        if found:
            blocks.append(TopicLinks(tuple(self.entry(topic) for _t, _k, topic in found)))
        else:
            blocks.append(Para((Run(f"No pages found for “{query}”."),)))
        return Document(SEARCH_PREFIX + query, heading, tuple(blocks), frozenset(), None)


# --- Back and Forward ---------------------------------------------------------------

class Entry:
    """A place in the history: the topic, and where the page was scrolled to
    when it was left (None: at the topic's section, or the top)."""

    def __init__(self, topic: str, scroll: Optional[float] = None):
        self.topic, self.scroll = topic, scroll

    def __repr__(self):
        return f"Entry({self.topic!r}, {self.scroll!r})"


class History:
    """Back and Forward as in Yelp. Each move is told where the page it
    leaves was scrolled to, so coming back to it puts it there again."""

    def __init__(self):
        self.entries: List[Entry] = []
        self.index = -1

    @property
    def current(self) -> Optional[Entry]:
        return self.entries[self.index] if self.entries else None

    def can_back(self) -> bool:
        return self.index > 0

    def can_forward(self) -> bool:
        return self.index < len(self.entries) - 1

    def go(self, topic: str, leaving: Optional[float] = None) -> Entry:
        """Somewhere new, which clears Forward. Going to the topic already
        shown adds nothing and opens it at its section again."""
        if self.current is not None:
            if self.current.topic == topic:
                self.current.scroll = None
                return self.current
            self.current.scroll = leaving
        del self.entries[self.index + 1:]
        self.entries.append(Entry(topic))
        self.index += 1
        return self.current

    def back(self, leaving: Optional[float] = None) -> Entry:
        if self.can_back():
            self.current.scroll = leaving
            self.index -= 1
        return self.current

    def forward(self, leaving: Optional[float] = None) -> Entry:
        if self.can_forward():
            self.current.scroll = leaving
            self.index += 1
        return self.current


# --- the text size ------------------------------------------------------------------

HELP_SECTION = "help"
TEXT_SIZES = (70, 80, 90, 100, 110, 120, 140, 160, 180, 200)  # per cent
DEFAULT_TEXT_SIZE = 100


def larger(size: int) -> int:
    return next((s for s in TEXT_SIZES if s > size), TEXT_SIZES[-1])


def smaller(size: int) -> int:
    return next((s for s in reversed(TEXT_SIZES) if s < size), TEXT_SIZES[0])


def load_text_size(candidates) -> int:
    """The text size settings.ini holds, from the first of `candidates` with
    a [help] section, as grid.load_grid reads; 100 where there is none, or
    the one there is not a size Larger and Smaller step through."""
    for path in candidates:
        parser = configparser.ConfigParser(interpolation=None)
        try:
            if not parser.read(path, encoding="utf-8"):
                continue
        except (configparser.Error, UnicodeDecodeError):
            continue
        if not parser.has_section(HELP_SECTION):
            continue
        try:
            size = int(parser[HELP_SECTION].get("text_size", ""))
        except ValueError:
            return DEFAULT_TEXT_SIZE
        return size if size in TEXT_SIZES else DEFAULT_TEXT_SIZE
    return DEFAULT_TEXT_SIZE


def save_text_size(candidates, size: int) -> bool:
    """Write the [help] section, leaving every other one as it was, as
    grid.save_grid does. The designer writes the same file, each time read
    afresh, so neither undoes the other's settings."""
    for path in candidates:
        parser = configparser.ConfigParser(interpolation=None)
        try:
            parser.read(path, encoding="utf-8")
            if not parser.has_section(HELP_SECTION):
                parser.add_section(HELP_SECTION)
            parser.set(HELP_SECTION, "text_size", str(size))
            os.makedirs(os.path.dirname(os.fspath(path)), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                parser.write(f)
            return True
        except (configparser.Error, OSError, UnicodeDecodeError):
            continue
    return False
