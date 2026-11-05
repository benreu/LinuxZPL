"""
The help pages made into PDF, for a system without Yelp.

Each Mallard page becomes a PDF of its own, named for the page's id, so a
window's Help button still opens just its own topic. A link to another page
is a link to that page's PDF beside it - a GoToR action, which reportlab
writes for href="pdf:<file>" and which Evince, Okular, Atril and Xreader
follow - so index.pdf leads to every topic the way the contents does in Yelp.
A GoToR link opens the other file at its top, so a link to a section of
another page lands on that page. Within a page, a link to a section goes to
its heading, and the headings are the viewer's outline.

Mallard's automatic links are made here the way Yelp makes them. A guide
lists the topics that name it, sorted by title, after its own text and
before its sections; one that names a section of it is listed at the end of
that section's text. A topic starts with the trail back to its guide, and
ends with its "See also" links, which Mallard makes run both ways.

Only the Mallard the pages use is understood. Anything else is refused,
naming the page and the element, so a page can never lose words in the PDF
without the test suite saying so.

The PDFs are made once for each version of the pages, into the user's cache,
and made again only when a page, a figure, the fonts or this converter
changes.
"""

import hashlib
import os
import shutil
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional, Tuple
from xml.sax.saxutils import escape

# Part of every cache key: change it whenever the PDFs made from unchanged
# pages would come out differently.
VERSION = "1"
NS = "{http://projectmallard.org/1.0/}"
# In a finished build only, so a build that died half way is never used.
DONE = ".complete"
LINK_COLOUR = "#1a5fb4"
NOTE_COLOURS = {"tip": "#26a269", "warning": "#c64600", "caution": "#c64600",
                "danger": "#c01c28", "important": "#e5a50a", "bug": "#c01c28"}
NOTE_LABELS = {"tip": "Tip", "warning": "Warning", "caution": "Caution",
               "danger": "Danger", "important": "Important", "bug": "Bug",
               "advanced": "Advanced"}
MONO_INLINE = {"code", "cmd", "file", "sys", "input", "output", "key"}


class UnsupportedMallard(ValueError):
    """A page uses Mallard this converter does not know how to show."""


class FontSet(NamedTuple):
    sans: str
    bold: str
    mono: str
    unicode: bool  # False for the PDF base fonts, which have only cp1252


BASE_FONTS = FontSet("Helvetica", "Helvetica-Bold", "Courier", False)


def _register(sans_path: Optional[str], mono_path: Optional[str]) -> FontSet:
    """reportlab fonts for the help's text. A TrueType face has all of
    Unicode, so the arrows and separators the pages use come out; without
    one the PDF base fonts do, with anything outside cp1252 shown as '?'."""
    if not sans_path or not mono_path:
        return BASE_FONTS
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    def family(path: Path, bold: str, italic: str, bold_italic: str) -> Tuple[str, str]:
        names = {}
        for style, suffix in (("normal", ""), ("bold", bold),
                              ("italic", italic), ("boldItalic", bold_italic)):
            face = path.with_name(path.stem + suffix + path.suffix)
            if not face.is_file():
                face = path
            name = "Help-" + face.stem
            if name not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(TTFont(name, str(face)))
            names[style] = name
        pdfmetrics.registerFontFamily(names["normal"], **names)
        return names["normal"], names["bold"]

    # DejaVu's own spelling of its other styles
    sans, bold = family(Path(sans_path), "-Bold", "-Oblique", "-BoldOblique")
    mono, _ = family(Path(mono_path), "-Bold", "-Oblique", "-BoldOblique")
    return FontSet(sans, bold, mono, True)


_default_fonts: Optional[FontSet] = None


def default_fonts() -> FontSet:
    global _default_fonts
    if _default_fonts is None:
        from . import fonts
        _default_fonts = _register(fonts.file_for_family("DejaVu Sans"),
                                   fonts.file_for_family("DejaVu Sans Mono"))
    return _default_fonts


# --- reading the pages ----------------------------------------------------------

class Page(NamedTuple):
    id: str
    root: ET.Element
    path: Path


def load(pages_dir: Path) -> Dict[str, Page]:
    pages = {}
    for path in sorted(Path(pages_dir).glob("*.page")):
        root = ET.parse(path).getroot()
        if root.tag != NS + "page":
            raise UnsupportedMallard(f"{path.name}: not a Mallard page")
        if root.get("id") != path.stem:
            raise UnsupportedMallard(
                f"{path.name}: its id is {root.get('id')!r}, not {path.stem!r}")
        pages[path.stem] = Page(path.stem, root, path)
    return pages


def _text(el: Optional[ET.Element]) -> str:
    return " ".join("".join(el.itertext()).split()) if el is not None else ""


def _info_title(root: ET.Element, kind: str) -> Optional[str]:
    info = root.find(NS + "info")
    if info is not None:
        for title in info.findall(NS + "title"):
            if title.get("type") == kind:
                return _text(title)
    return None


def link_title(root: ET.Element) -> str:
    return _info_title(root, "link") or _text(root.find(NS + "title"))


def _sort_key(root: ET.Element) -> str:
    return (_info_title(root, "sort") or link_title(root)).casefold()


def _section(root: ET.Element, sid: str) -> Optional[ET.Element]:
    for section in root.iter(NS + "section"):
        if section.get("id") == sid:
            return section
    return None


class Links:
    """Mallard's automatic links, gathered from every page's info."""

    def __init__(self, pages: Dict[str, Page]):
        self.pages = pages
        self.topics: Dict[Tuple[str, Optional[str]], List[str]] = {}
        self.guides: Dict[str, List[Tuple[str, Optional[str]]]] = {}
        self.seealso: Dict[str, set] = {pid: set() for pid in pages}
        for pid, page in pages.items():
            info = page.root.find(NS + "info")
            for link in (info.findall(NS + "link") if info is not None else ()):
                kind, xref = link.get("type"), link.get("xref")
                if kind not in ("guide", "topic", "seealso") or not xref:
                    raise UnsupportedMallard(
                        f"{pid}.page: <link type={kind!r} xref={xref!r}> in its info")
                target, _, section = xref.partition("#")
                target = target or pid
                if target not in pages:
                    raise UnsupportedMallard(f"{pid}.page: links to {xref!r}, no such page")
                if kind == "guide":
                    self._file(target, section or None, pid)
                elif kind == "topic":
                    self._file(pid, None, target)
                else:
                    self.seealso[pid].add(target)
                    self.seealso[target].add(pid)

    def _file(self, guide: str, section: Optional[str], topic: str):
        topics = self.topics.setdefault((guide, section), [])
        if topic not in topics:
            topics.append(topic)
            self.guides.setdefault(topic, []).append((guide, section))

    def sorted(self, ids) -> List[str]:
        return sorted(set(ids), key=lambda pid: _sort_key(self.pages[pid].root))


# --- writing one page -----------------------------------------------------------

def _styles(fonts: FontSet):
    from reportlab.lib.colors import HexColor
    from reportlab.lib.styles import ParagraphStyle

    grey = HexColor("#5e5c64")
    body = ParagraphStyle("body", fontName=fonts.sans, fontSize=10.5,
                          leading=14.5, spaceAfter=6)
    return {
        "body": body,
        "title": ParagraphStyle("title", body, fontName=fonts.bold, fontSize=20,
                                leading=25, spaceAfter=10),
        "h1": ParagraphStyle("h1", body, fontName=fonts.bold, fontSize=14.5,
                             leading=18, spaceBefore=12, spaceAfter=6,
                             keepWithNext=1),
        "h2": ParagraphStyle("h2", body, fontName=fonts.bold, fontSize=12,
                             leading=15, spaceBefore=10, spaceAfter=4,
                             keepWithNext=1),
        "term": ParagraphStyle("term", body, fontName=fonts.bold, spaceAfter=2,
                               keepWithNext=1),
        "definition": ParagraphStyle("definition", body, leftIndent=18),
        "trail": ParagraphStyle("trail", body, fontSize=9, leading=12,
                                textColor=grey, spaceAfter=8),
        "topic": ParagraphStyle("topic", body, fontSize=11, leading=14,
                                spaceAfter=1, keepWithNext=1),
        "desc": ParagraphStyle("desc", body, fontSize=9.5, leading=12.5,
                               textColor=grey, spaceAfter=8),
        "code": ParagraphStyle("code", body, fontName=fonts.mono, fontSize=9.5,
                               leading=12.5, backColor=HexColor("#f6f5f4"),
                               borderPadding=4, spaceBefore=4, spaceAfter=10),
    }


class _Writer:
    """The flowables for one page."""

    def __init__(self, page: Page, links: Links, fonts: FontSet, styles):
        self.page, self.pid = page, page.id
        self.links, self.pages = links, links.pages
        self.fonts, self.styles = fonts, styles
        self.unnamed = 0
        self.sep = "▸" if fonts.unicode else "›"

    def refuse(self, el: ET.Element, where: str = ""):
        tag = el.tag[len(NS):] if el.tag.startswith(NS) else el.tag
        raise UnsupportedMallard(
            f"{self.pid}.page: <{tag}>{' ' + where if where else ''} "
            f"is not supported in the PDF help")

    def tag(self, el: ET.Element) -> str:
        if not isinstance(el.tag, str) or not el.tag.startswith(NS):
            self.refuse(el)
        return el.tag[len(NS):]

    def esc(self, text: str) -> str:
        if not self.fonts.unicode:
            text = text.encode("cp1252", "replace").decode("cp1252")
        return escape(text)

    # inline ---------------------------------------------------------------

    def inline(self, el: ET.Element) -> str:
        parts = [self.esc(el.text or "")]
        for child in el:
            parts.append(self.inline_element(child))
            parts.append(self.esc(child.tail or ""))
        return "".join(parts)

    def inline_element(self, el: ET.Element) -> str:
        tag = self.tag(el)
        if tag == "em":
            strong = "strong" in (el.get("style") or "").split()
            return f"<b>{self.inline(el)}</b>" if strong else f"<i>{self.inline(el)}</i>"
        if tag == "gui":
            return f"<b>{self.inline(el)}</b>"
        if tag == "guiseq":
            guis = [self.inline_element(g) for g in el if self.tag(g) == "gui"]
            if len(guis) != len(el):
                self.refuse(el, "holding anything but <gui>")
            return f" {self.sep} ".join(guis)
        if tag == "keyseq":
            joiner = "+" if el.get("type", "combo") == "combo" else " "
            return joiner.join(self.inline_element(k) for k in el)
        if tag in MONO_INLINE:
            return f'<font face="{self.fonts.mono}">{self.inline(el)}</font>'
        if tag == "var":
            return f"<i>{self.inline(el)}</i>"
        if tag in ("app", "span"):
            return self.inline(el)
        if tag == "link":
            return self.link(el)
        self.refuse(el, "in running text")

    def link(self, el: ET.Element) -> str:
        text = self.inline(el).strip()
        xref, href = el.get("xref"), el.get("href")
        if xref is not None:
            target, _, section = xref.partition("#")
            target = target or self.pid
            if target not in self.pages:
                raise UnsupportedMallard(f"{self.pid}.page: links to {xref!r}, no such page")
            root = self.pages[target].root
            if section and _section(root, section) is None:
                raise UnsupportedMallard(f"{self.pid}.page: links to {xref!r}, no such section")
            if not text:
                text = self.esc(_text(_section(root, section).find(NS + "title"))
                                if section else link_title(root))
            if target != self.pid:
                dest = f"pdf:{target}.pdf"
            else:
                dest = "#" + (f"s-{section}" if section else "top")
        elif href:
            dest, text = href, text or self.esc(href)
        else:
            self.refuse(el, "with neither xref nor href")
        return f'<a href="{escape(dest, {chr(34): "&quot;"})}" color="{LINK_COLOUR}">{text}</a>'

    # blocks ---------------------------------------------------------------

    def para(self, markup: str, style: str = "body", outline=None):
        from reportlab.platypus import Paragraph
        p = Paragraph(markup, self.styles[style])
        if outline:
            p._help_outline = outline
        return p

    def blocks(self, parent: ET.Element, sections: bool = False,
               skip=("info", "title")) -> list:
        out = []
        for child in parent:
            tag = self.tag(child)
            if tag in skip or (sections and tag == "section"):
                continue
            out.extend(self.block(child))
        return out

    def block(self, el: ET.Element) -> list:
        from reportlab.platypus import ListFlowable, ListItem, XPreformatted

        tag = self.tag(el)
        if tag == "p":
            return [self.para(self.inline(el))]
        if tag in ("code", "screen"):
            markup = self.inline(el).lstrip("\n").rstrip()
            return [XPreformatted(markup, self.styles["code"])]
        if tag in ("list", "steps"):
            numbered = tag == "steps" or el.get("type") == "numbered"
            items = []
            for item in el:
                if self.tag(item) == "title":
                    continue
                if self.tag(item) != "item":
                    self.refuse(item, f"in <{tag}>")
                items.append(ListItem(self.blocks(item) or [self.para("")]))
            heading = self.titled(el)
            kwargs = ({"bulletType": "1", "bulletFormat": "%s."} if numbered
                      else {"bulletType": "bullet", "start": "•"})
            return heading + [ListFlowable(items, leftIndent=18,
                                           bulletFontName=self.fonts.sans,
                                           bulletFontSize=10.5, **kwargs)]
        if tag == "terms":
            out = self.titled(el)
            for item in el:
                if self.tag(item) == "title":
                    continue
                if self.tag(item) != "item":
                    self.refuse(item, "in <terms>")
                for term in item.findall(NS + "title"):
                    out.append(self.para(self.inline(term), "term"))
                out.extend(self.indented(self.blocks(item)))
            return out
        if tag == "note":
            return [self.note(el)]
        if tag == "media":
            return [self.media(el)]
        if tag == "table":
            return self.titled(el) + [self.table(el)]
        self.refuse(el)

    def titled(self, el: ET.Element) -> list:
        title = el.find(NS + "title")
        return [self.para(self.inline(title), "term")] if title is not None else []

    def indented(self, flowables: list) -> list:
        from reportlab.platypus import Indenter
        return [Indenter(left=18)] + flowables + [Indenter(left=-18)] if flowables else []

    def note(self, el: ET.Element):
        from reportlab.lib.colors import HexColor
        from reportlab.platypus import Table, TableStyle

        style = next((s for s in (el.get("style") or "").split() if s in NOTE_LABELS), "")
        label = NOTE_LABELS.get(style, "Note")
        title = el.find(NS + "title")
        if title is not None:
            label = self.inline(title)
        cell = [self.para(f"<b>{label}</b>")] + self.blocks(el)
        box = Table([[cell]], colWidths=["100%"])
        box.setStyle(TableStyle([
            ("LINEBEFORE", (0, 0), (0, 0), 3, HexColor(NOTE_COLOURS.get(style, "#3584e4"))),
            ("BACKGROUND", (0, 0), (0, 0), HexColor("#f6f5f4")),
            ("LEFTPADDING", (0, 0), (0, 0), 10),
            ("TOPPADDING", (0, 0), (0, 0), 6),
            ("BOTTOMPADDING", (0, 0), (0, 0), 0),
        ]))
        box.spaceBefore, box.spaceAfter = 4, 10
        return box

    def media(self, el: ET.Element):
        from reportlab.platypus import Image

        if el.get("type", "image") != "image" or not el.get("src"):
            self.refuse(el, "that is not an image with a src")
        path = self.page.path.parent / el.get("src")
        if not path.is_file():
            raise UnsupportedMallard(f"{self.pid}.page: no image {el.get('src')!r}")

        class FitImage(Image):
            def wrap(self, avail_width, avail_height):
                if self.drawWidth > avail_width:
                    self.drawHeight *= avail_width / self.drawWidth
                    self.drawWidth = avail_width
                return super().wrap(avail_width, avail_height)

        from PIL import Image as PILImage
        with PILImage.open(path) as img:
            px_width, px_height = img.size
        # Mallard's width and height are in pixels; either alone keeps the
        # image's shape.
        width, height = float(el.get("width") or 0), float(el.get("height") or 0)
        if width and not height:
            height = px_height * width / px_width
        elif height and not width:
            width = px_width * height / px_height
        elif not width:
            width, height = px_width, px_height
        # A pixel at 96 to the inch, the size a screenshot has on screen
        return FitImage(str(path), width=width * 0.75, height=height * 0.75)

    def table(self, el: ET.Element):
        from reportlab.lib.colors import HexColor
        from reportlab.platypus import Table, TableStyle

        rows, header = [], 0
        for child in el:
            tag = self.tag(child)
            if tag == "title":
                continue
            groups = [child] if tag == "tr" else list(child) if tag in ("thead", "tbody", "tfoot") else None
            if groups is None:
                self.refuse(child, "in <table>")
            for tr in groups:
                if self.tag(tr) != "tr":
                    self.refuse(tr, f"in <{tag}>")
                cells = []
                for td in tr:
                    if self.tag(td) not in ("td", "th"):
                        self.refuse(td, "in <tr>")
                    cells.append(self.blocks(td))
                rows.append(cells)
                if tag == "thead":
                    header += 1
        width = max((len(r) for r in rows), default=1)
        rows = [r + [[]] * (width - len(r)) for r in rows]
        table = Table(rows, colWidths=[f"{100 / width}%"] * width, repeatRows=header)
        commands = [("GRID", (0, 0), (-1, -1), 0.5, HexColor("#c0bfbc")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP")]
        if header:
            commands.append(("BACKGROUND", (0, 0), (-1, header - 1), HexColor("#f6f5f4")))
        table.setStyle(TableStyle(commands))
        table.spaceAfter = 10
        return table

    # the page -------------------------------------------------------------

    def topic_links(self, ids) -> list:
        """Each page in `ids` as Yelp lists it: its title, linked, over its
        description."""
        out = []
        for tid in self.links.sorted(ids):
            root = self.pages[tid].root
            out.append(self.para(f'<a href="pdf:{tid}.pdf" color="{LINK_COLOUR}">'
                                 f'{self.esc(link_title(root))}</a>', "topic"))
            info = root.find(NS + "info")
            desc = info.find(NS + "desc") if info is not None else None
            out.append(self.para(self.inline(desc) if desc is not None else "", "desc"))
        return out

    def guided(self, section: Optional[str]) -> list:
        """The topics that name this page, or one of its sections, as guide."""
        return self.topic_links(self.links.topics.get((self.pid, section), ()))

    def trail(self) -> list:
        out = []
        for guide, section in self.links.guides.get(self.pid, ()):
            root = self.pages[guide].root
            text = self.esc(link_title(root))
            if section:
                text += f" › {self.esc(_text(_section(root, section).find(NS + 'title')))}"
            out.append(self.para(f'<a href="pdf:{guide}.pdf" color="{LINK_COLOUR}">'
                                 f'{text}</a>', "trail"))
        return out

    def section(self, el: ET.Element, level: int) -> list:
        sid = el.get("id")
        if not sid:
            self.unnamed += 1
        key = f"s-{sid}" if sid else f"unnamed-{self.unnamed}"
        title = el.find(NS + "title")
        out = [self.para(f'<a name="{key}"/>{self.inline(title)}',
                         "h1" if level == 1 else "h2",
                         outline=(_text(title), key, level))]
        out += self.blocks(el, sections=True)
        out += self.guided(sid) if sid else []
        for sub in el.findall(NS + "section"):
            out += self.section(sub, level + 1)
        return out

    def flowables(self) -> list:
        root = self.page.root
        title = root.find(NS + "title")
        out = self.trail()
        out.append(self.para(f'<a name="top"/>{self.inline(title)}', "title",
                             outline=(_text(title), "top", 0)))
        out += self.blocks(root, sections=True)
        out += self.guided(None)
        for section in root.findall(NS + "section"):
            out += self.section(section, 1)
        also = self.links.seealso[self.pid]
        if also:
            out.append(self.para('<a name="see-also"/>See also', "h1",
                                 outline=("See also", "see-also", 1)))
            out += self.topic_links(also)
        return out


def write_page(page: Page, links: Links, out: Path, fonts: FontSet):
    from reportlab.lib.colors import HexColor
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.units import inch
    from reportlab.platypus import SimpleDocTemplate

    writer = _Writer(page, links, fonts, _styles(fonts))
    story = writer.flowables()
    title = _text(page.root.find(NS + "title"))
    info = page.root.find(NS + "info")

    class Doc(SimpleDocTemplate):
        def afterFlowable(self, flowable):
            mark = getattr(flowable, "_help_outline", None)
            if mark:
                text, key, level = mark
                self.canv.addOutlineEntry(text, key, level=level, closed=False)

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont(fonts.sans, 8)
        canvas.setFillColor(HexColor("#77767b"))
        canvas.drawString(doc.leftMargin, 0.5 * inch, "LinuxZPL Help")
        canvas.drawRightString(doc.leftMargin + doc.width, 0.5 * inch, str(doc.page))
        canvas.restoreState()

    doc = Doc(str(out), pagesize=letter, leftMargin=0.8 * inch, rightMargin=0.8 * inch,
              topMargin=0.75 * inch, bottomMargin=0.8 * inch, title=title,
              author="LinuxZPL", creator="LinuxZPL",
              subject=_text(info.find(NS + "desc")) if info is not None else "")
    doc.build(story, onFirstPage=footer, onLaterPages=footer)


def write_all(pages_dir: Path, out_dir: Path, fonts: FontSet) -> List[Path]:
    pages = load(pages_dir)
    links = Links(pages)
    written = []
    for pid, page in pages.items():
        path = Path(out_dir) / f"{pid}.pdf"
        write_page(page, links, path, fonts)
        written.append(path)
    return written


# --- the cache ------------------------------------------------------------------

def cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return Path(base) / "linuxzpl" / "help"


def fingerprint(pages_dir: Path, fonts: FontSet) -> str:
    """Names one version of the help: every file under the pages' folder,
    figures too, the fonts it is set in, and this converter's VERSION."""
    digest = hashlib.sha256(f"{VERSION}\0{fonts!r}\0".encode())
    pages_dir = Path(pages_dir)
    for path in sorted(p for p in pages_dir.rglob("*") if p.is_file()):
        digest.update(path.relative_to(pages_dir).as_posix().encode() + b"\0")
        digest.update(path.read_bytes())
    return digest.hexdigest()[:16]


def build(pages_dir: Path, cache: Optional[Path] = None,
          fonts: Optional[FontSet] = None) -> Path:
    """The folder holding <id>.pdf for every page, made now unless this
    version of the pages was made before. Built aside and renamed into
    place, so another LinuxZPL building at the same moment, or a build cut
    off half way, never leaves a folder that looks finished and is not.
    Earlier versions' folders are removed once a new one is in place."""
    fonts = fonts or default_fonts()
    root = Path(cache) if cache else cache_dir()
    out = root / fingerprint(pages_dir, fonts)
    if (out / DONE).is_file():
        return out
    root.mkdir(parents=True, exist_ok=True)
    building = Path(tempfile.mkdtemp(prefix=".building-", dir=root))
    try:
        write_all(pages_dir, building, fonts)
        (building / DONE).write_text(VERSION)
        if out.is_dir() and not (out / DONE).is_file():
            shutil.rmtree(out, ignore_errors=True)
        try:
            building.rename(out)
        except OSError:
            if not (out / DONE).is_file():
                raise
            shutil.rmtree(building, ignore_errors=True)  # beaten to it
    except BaseException:
        shutil.rmtree(building, ignore_errors=True)
        raise
    for old in root.iterdir():
        if old.is_dir() and old != out and not old.name.startswith(".building-"):
            shutil.rmtree(old, ignore_errors=True)
    return out
