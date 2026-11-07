"""
The built-in help viewer, in GTK: LinuxZPL's own window onto the help pages,
for a system without Yelp, made to look and work like Yelp 42 - the twin of
qtui/helpviewer.py.

It runs as a program of its own, `linuxzpl.py --gtk --show-help TOPIC`,
started by helpdocs.launch() as Yelp is: almost every Help button sits in a
modal window, which would block a window of the designer's own, and the help
stays open after the designer closes. Topics asked for later arrive one per
line on standard input, and the window goes to each and comes to the front.

zplcore/helpview.py makes each page into runs and blocks; this writes them
into a Gtk.TextBuffer with tags, every word in the buffer - tables too, laid
out on tab stops - so Find and copying reach all of it. A TextView has no
boxes, bars or rules, so PageView paints Yelp's beneath the text, placed
from the paragraphs they surround each time it draws. The header bar is
Yelp's: Back and Forward linked at the left, the page's title over
"LinuxZPL Help", and Search and the menu at the right.
"""

import os
import sys
import time
from typing import Dict, List, Optional, Tuple

import gi
gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
from gi.repository import Gdk, GdkPixbuf, GLib, GObject, Gtk, Pango  # noqa: E402

from zplcore import helpdocs, helpview  # noqa: E402
from zplcore.helpview import (Code, Heading, Image, ListBlock, Note,  # noqa: E402
                              PageLinks, Para, Run, Table, Terms, Title,
                              TopicLinks, Trail)

PAGE_MARGIN = 16            # pixels either side of the text, as Yelp's
ICON_SIZE = 22
BOX_PAD = 9                 # inside a box, above its first line and below its last
BOX_GAP = 10                # outside a box, between it and what follows
ICON_FALLBACKS = {"open-menu-symbolic": ("open-menu", "application-menu"),
                  "edit-find-symbolic": ("edit-find", "system-search")}


def _rgb(hex_colour: str) -> Tuple[float, float, float]:
    colour = Gdk.RGBA()
    colour.parse(hex_colour)
    return colour.red, colour.green, colour.blue


def theme_icon(name: str, size: int) -> Optional[GdkPixbuf.Pixbuf]:
    """The desktop theme's icon, or None where the theme has none."""
    theme = Gtk.IconTheme.get_default()
    for candidate in (name, name + "-symbolic"):
        try:
            return theme.load_icon(candidate, size, Gtk.IconLookupFlags.FORCE_SIZE)
        except GLib.Error:
            continue
    return None


def icon_name(name: str) -> str:
    """`name`, or the first other name for it the theme has."""
    theme = Gtk.IconTheme.get_default()
    for candidate in (name,) + ICON_FALLBACKS.get(name, ()):
        if theme.has_icon(candidate):
            return candidate
    return name


# --- the page ----------------------------------------------------------------------

class Box:
    """A box drawn beneath paragraphs: from the top of the line `start` is
    in, less `inset_top`, to the bottom of the line `end` is in, less
    `inset_bottom`; `left` pixels from the view's left edge to `right` from
    its right (None: the full width)."""

    def __init__(self, start, end, left, right, background=None, border=None,
                 bar=None, bar_width=0, icon=None, inset_top=0, inset_bottom=0):
        self.start, self.end = start, end
        self.left, self.right = left, right
        self.background, self.border = background, border
        self.bar, self.bar_width, self.icon = bar, bar_width, icon
        self.inset_top, self.inset_bottom = inset_top, inset_bottom


class Rule:
    """A line across the page, `offset` pixels below the top of the line
    `mark` is in, or above its bottom when `from_bottom`."""

    def __init__(self, mark, colour, thickness, offset, from_bottom):
        self.mark, self.colour, self.thickness = mark, colour, thickness
        self.offset, self.from_bottom = offset, from_bottom


class Keycap:
    def __init__(self, start, end):
        self.start, self.end = start, end


class PageView(Gtk.TextView):
    """The page: a read-only TextView that also paints Yelp's boxes, bars,
    rules and keycaps beneath its text."""

    def __init__(self):
        super().__init__()
        self.set_editable(False)
        self.set_cursor_visible(False)
        self.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.set_bottom_margin(PAGE_MARGIN)
        self.decorations: list = []

    def _line(self, mark) -> Tuple[int, int]:
        return self.get_line_yrange(self.get_buffer().get_iter_at_mark(mark))

    def do_draw_layer(self, layer, cr):
        if layer != Gtk.TextViewLayer.BELOW_TEXT:
            return
        width = self.get_allocated_width()
        seen = self.get_visible_rect()
        for item in self.decorations:
            if isinstance(item, Box):
                top = self._line(item.start)[0] + item.inset_top
                y, h = self._line(item.end)
                bottom = y + h - item.inset_bottom
                if bottom < seen.y or top > seen.y + seen.height:
                    continue
                left = 0 if item.left is None else item.left
                right = width - (0 if item.right is None else item.right)
                if item.background:
                    cr.set_source_rgb(*_rgb(item.background))
                    cr.rectangle(left, top, right - left, bottom - top)
                    cr.fill()
                if item.border:
                    cr.set_source_rgb(*_rgb(item.border))
                    cr.set_line_width(1)
                    cr.rectangle(left + 0.5, top + 0.5, right - left - 1, bottom - top - 1)
                    cr.stroke()
                if item.bar:
                    cr.set_source_rgb(*_rgb(item.bar))
                    cr.rectangle(left, top, item.bar_width, bottom - top)
                    cr.fill()
                if item.icon is not None:
                    Gdk.cairo_set_source_pixbuf(cr, item.icon, left + PAGE_MARGIN - 4,
                                                top + BOX_PAD)
                    cr.paint()
            elif isinstance(item, Rule):
                y, h = self._line(item.mark)
                y = y + h - item.offset - item.thickness if item.from_bottom else y + item.offset
                if seen.y <= y <= seen.y + seen.height:
                    cr.set_source_rgb(*_rgb(item.colour))
                    cr.rectangle(0, y, width, item.thickness)
                    cr.fill()
            elif isinstance(item, Keycap):
                buffer = self.get_buffer()
                start = self.get_iter_location(buffer.get_iter_at_mark(item.start))
                end = self.get_iter_location(buffer.get_iter_at_mark(item.end))
                if start.y != end.y or not (seen.y <= start.y <= seen.y + seen.height):
                    continue
                x, y, w, h = start.x + 0.5, start.y + 1.5, end.x - start.x - 1, start.height - 3
                radius = 2.5
                cr.new_sub_path()
                cr.arc(x + w - radius, y + radius, radius, -1.5708, 0)
                cr.arc(x + w - radius, y + h - radius, radius, 0, 1.5708)
                cr.arc(x + radius, y + h - radius, radius, 1.5708, 3.1416)
                cr.arc(x + radius, y + radius, radius, 3.1416, 4.7124)
                cr.close_path()
                cr.set_source_rgb(*_rgb(helpview.RULE))
                cr.set_line_width(1)
                cr.stroke()


class PageWriter:
    """Writes one helpview.Document into a new Gtk.TextBuffer, paragraph by
    paragraph, gathering what PageView paints, the links and the anchors."""

    def __init__(self, view: PageView, text_size: int, width: int):
        self.view = view
        self.width = width
        self.buffer = Gtk.TextBuffer()
        self.points = helpview.BASE_POINTS * text_size / 100
        self.em = self.points * 96 / 72
        self.left = self.right = PAGE_MARGIN
        self.depth = 0
        self.decorations: list = []
        self.links: Dict[Gtk.TextTag, Tuple[str, str]] = {}
        self.anchors: Dict[str, Gtk.TextMark] = {}
        self._blocks: Dict[tuple, Gtk.TextTag] = {}
        b = self.buffer
        # Inline tags first, then Find's; each paragraph's own tag is put
        # beneath all of them, so a <gui> word stays grey inside a heading.
        self.inline = {
            "bold": b.create_tag(None, weight=Pango.Weight.BOLD),
            "italic": b.create_tag(None, style=Pango.Style.ITALIC),
            "mono": b.create_tag(None, family="monospace", scale=0.9,
                                 background=helpview.BAND),
            "code": b.create_tag(None, family="monospace", scale=0.9),
            "gui": b.create_tag(None, foreground=helpview.GREY),
            "key": b.create_tag(None, foreground=helpview.HEADING,
                                background=helpview.BAND, scale=0.92),
            "join": b.create_tag(None, foreground=helpview.KEY_JOIN, scale=0.85),
            "grey": b.create_tag(None, foreground=helpview.GREY),
            "faint": b.create_tag(None, foreground=helpview.FAINT),
            "heading": b.create_tag(None, foreground=helpview.HEADING),
            "link": b.create_tag(None, foreground=helpview.LINK),
            "quiet-link": b.create_tag(None, foreground=helpview.GREY),
        }
        b.create_tag("found", background=helpview.FOUND)
        b.create_tag("found-current", background=helpview.FOUND_CURRENT)

    # tags and runs ------------------------------------------------------------

    def block_tag(self, **props) -> Gtk.TextTag:
        key = tuple(sorted((k, str(v)) for k, v in props.items()))
        tag = self._blocks.get(key)
        if tag is None:
            tag = self.buffer.create_tag(None, **props)
            tag.set_priority(0)
            self._blocks[key] = tag
        return tag

    def end(self) -> Gtk.TextIter:
        return self.buffer.get_end_iter()

    def mark(self) -> Gtk.TextMark:
        return self.buffer.create_mark(None, self.end(), True)

    def insert(self, text: str, tags):
        self.buffer.insert_with_tags(self.end(), text, *tags)

    def run(self, run: Run, block: Gtk.TextTag, extra=(), link_tag="link", code=False):
        tags = [block] + list(extra)
        for style in ("bold", "italic", "gui", "key", "join"):
            if style in run.styles:
                tags.append(self.inline[style])
        if "mono" in run.styles:
            tags.append(self.inline["code" if code else "mono"])
        if run.link or run.href:
            target = self.buffer.create_tag(None)
            self.links[target] = ("topic", run.link) if run.link else ("href", run.href)
            tags += [self.inline[link_tag], target]
        if "key" in run.styles:
            start = self.mark()
            self.insert(f" {run.text} ", tags)  # a keycap's room either side
            self.decorations.append(Keycap(start, self.mark()))
        else:
            self.insert(run.text, tags)

    def paragraph(self, runs, top=0.0, bottom=0.75, scale=1.0, bold=False, colour=None,
                  extra=(), link_tag="link", left=None, indent=0, tabs=None, inside=0.35,
                  prefix="", code=False) -> Tuple[Gtk.TextMark, Gtk.TextMark]:
        """One paragraph; `top`, `bottom` and `inside` (the room between its
        wrapped lines) in ems. Returns marks at its start and at the end of
        its text."""
        props = dict(size_points=self.points * scale,
                     left_margin=round(self.left if left is None else left),
                     right_margin=round(self.right),
                     pixels_above_lines=round(top * self.em),
                     pixels_below_lines=round(bottom * self.em),
                     pixels_inside_wrap=round(inside * self.em * scale),
                     indent=round(indent))
        if bold:
            props["weight"] = Pango.Weight.BOLD
        if tabs:
            array = Pango.TabArray.new(len(tabs), True)
            for i, x in enumerate(tabs):
                array.set_tab(i, Pango.TabAlign.LEFT, round(x))
            props["tabs"] = array
        block = self.block_tag(**props)
        extra = list(extra) + ([self.inline[colour]] if colour else [])
        start = self.mark()
        if prefix:
            self.insert(prefix, [block] + extra)
        for run in runs:
            self.run(run, block, extra, link_tag, code)
        finish = self.mark()
        self.insert("\n", [block])
        return start, finish

    def pad(self, start, finish, above=0, below=0):
        """More room above a paragraph's first line, or below its last: a
        box's padding and the gap around it."""
        buffer = self.buffer
        if above:
            first = buffer.get_iter_at_mark(start)
            line_end = first.copy()
            line_end.forward_to_line_end()
            tag = buffer.create_tag(None, pixels_above_lines=above)
            buffer.apply_tag(tag, first, line_end)
        if below:
            last = buffer.get_iter_at_mark(finish)
            line_start = last.copy()
            line_start.set_line_offset(0)
            tag = buffer.create_tag(None, pixels_below_lines=below)
            buffer.apply_tag(tag, line_start, last)

    # blocks -----------------------------------------------------------------

    def build(self, document: helpview.Document):
        for block in document.blocks:
            self.block(block)

    def blocks(self, blocks):
        spans = [self.block(b) for b in blocks]
        spans = [s for s in spans if s]
        return (spans[0][0], spans[-1][1]) if spans else None

    def block(self, block):
        """Write `block`; the marks at its start and its end."""
        kind = type(block)
        if kind is Trail:
            return self.trail(block)
        if kind is Title:
            return self.paragraph(block.runs, top=0.6, bottom=0.4, inside=0.1,
                                  scale=helpview.SCALE["title"], colour="heading")
        if kind is Heading:
            return self.heading(block)
        if kind is Para:
            return self.paragraph(block.runs)
        if kind is Code:
            return self.code(block)
        if kind is ListBlock:
            return self.list(block)
        if kind is Terms:
            return self.terms(block)
        if kind is Note:
            return self.note(block)
        if kind is Image:
            return self.image(block)
        if kind is Table:
            return self.table(block)
        if kind is TopicLinks:
            return self.topic_links(block)
        if kind is PageLinks:
            return self.page_links(block)
        raise TypeError(f"no way to draw {kind.__name__}")

    def trail(self, block: Trail):
        spans = []
        scale = helpview.SCALE["trail"]
        for trail in block.trails:
            runs = []
            for i, crumb in enumerate(trail):
                if i:
                    runs.append(Run(helpview.TRAIL_SEP))
                runs.append(Run(crumb.text, link=crumb.topic))
            runs.append(Run(helpview.TRAIL_END))
            spans.append(self.paragraph(runs, bottom=0, scale=scale, colour="grey",
                                        link_tag="quiet-link", inside=0.1))
        start, finish = spans[0][0], spans[-1][1]
        self.pad(start, finish, above=6, below=6 + BOX_GAP)
        self.decorations.append(Box(start, finish, None, None, background=helpview.BAND,
                                    inset_bottom=BOX_GAP))
        return start, finish

    def heading(self, block: Heading):
        level = min(block.level, 3)
        room = round(0.5 * self.em)  # below the rule, before the text after
        start, finish = self.paragraph(block.runs, top=1.6 if level == 1 else 1.0,
                                       bottom=0, inside=0.1, colour="heading",
                                       scale=helpview.SCALE.get(f"h{level}", 1.0))
        if block.anchor:
            self.anchors[block.anchor] = start
        if level == 1:
            self.pad(start, finish, below=room + 4)
            self.decorations.append(Rule(finish, helpview.RULE, 1, room, True))
        return start, finish

    def code(self, block: Code):
        lines = helpview.plain(block.runs).split("\n")
        style = block.runs[0].styles if block.runs else frozenset({"mono"})
        spans = [self.paragraph([Run(line, style)] if line else [], bottom=0, inside=0,
                                left=self.left + 13, code=True)
                 for line in lines]
        start, finish = spans[0][0], spans[-1][1]
        self.pad(start, finish, above=BOX_PAD, below=BOX_PAD + BOX_GAP)
        self.decorations.append(Box(start, finish, self.left, self.right,
                                    background=helpview.BAND if block.screen else "#ffffff",
                                    border=helpview.RULE, inset_bottom=BOX_GAP))
        return start, finish

    def list(self, block: ListBlock):
        title = self.paragraph(block.title, top=0.4, bottom=0.3, bold=True) \
            if block.title else None
        saved = self.left, self.right
        steps = block.kind == "steps"
        if steps:
            box_left, box_right = self.left, self.right
            self.left += 4 + 12
            self.right += 12
        width = round(self.em * 1.8)
        self.depth += 1
        first = last = None
        for number, item in enumerate(block.items, 1):
            bullet = "•" if block.kind == "bullet" else f"{number}."
            head, rest = item[0], item[1:]
            span = self.paragraph(head.runs if type(head) is Para else [], bottom=0.55,
                                  indent=-width, tabs=[width], prefix=bullet + "\t")
            if type(head) is not Para:
                rest = item
            first = first or span[0]
            last = span[1]
            inner = self.left
            self.left += width
            more = self.blocks(rest)
            if more:
                last = more[1]
            self.left = inner
        self.depth -= 1
        self.left, self.right = saved
        if steps and first is not None:
            self.pad(first, last, above=BOX_PAD, below=BOX_PAD + BOX_GAP)
            self.decorations.append(Box(first, last, box_left, box_right,
                                        border=helpview.STEPS_EDGE, bar=helpview.STEPS_BAR,
                                        bar_width=4, inset_bottom=BOX_GAP))
        return (title[0] if title else first), last

    def terms(self, block: Terms):
        spans = []
        if block.title:
            spans.append(self.paragraph(block.title, top=0.4, bottom=0.3, bold=True))
        for terms, inner in block.items:
            for term in terms:
                spans.append(self.paragraph(term, top=0.3, bottom=0.15))
            saved = self.left
            self.left += helpview.INDENT_EMS * self.em
            more = self.blocks(inner)
            if more:
                spans.append(more)
            self.left = saved
        return (spans[0][0], spans[-1][1]) if spans else None

    def note(self, block: Note):
        saved = self.left, self.right
        box_left, box_right = self.left, self.right
        icon = theme_icon(block.icon, ICON_SIZE)
        self.left += ICON_SIZE + 2 * PAGE_MARGIN + 4
        self.right += 12
        spans = []
        if block.title:
            spans.append(self.paragraph(block.title, bottom=0.4, bold=True))
        if icon is None:
            spans.append(self.paragraph([Run(block.label, frozenset({"bold"}))], bottom=0.3))
        more = self.blocks(block.blocks)
        if more:
            spans.append(more)
        self.left, self.right = saved
        start, finish = spans[0][0], spans[-1][1]
        self.pad(start, finish, above=BOX_PAD, below=BOX_PAD + BOX_GAP)
        self.decorations.append(Box(start, finish, box_left, box_right,
                                    background=helpview.BAND, border=helpview.NOTE_EDGE,
                                    icon=icon, inset_bottom=BOX_GAP))
        return start, finish

    def image(self, block: Image):
        pixbuf = GdkPixbuf.Pixbuf.new_from_file(str(block.path))
        width, height = block.width, block.height
        if width and not height:
            height = pixbuf.get_height() * width / pixbuf.get_width()
        elif height and not width:
            width = pixbuf.get_width() * height / pixbuf.get_height()
        elif not width:
            width, height = pixbuf.get_width(), pixbuf.get_height()
        room = max(self.width - self.left - self.right, 50)
        if width > room:
            width, height = room, height * room / width
        pixbuf = pixbuf.scale_simple(max(round(width), 1), max(round(height), 1),
                                     GdkPixbuf.InterpType.BILINEAR)
        start = self.mark()
        block_tag = self.block_tag(left_margin=round(self.left),
                                   pixels_below_lines=round(0.75 * self.em))
        self.buffer.insert_pixbuf(self.end(), pixbuf)
        finish = self.mark()
        self.insert("\n", [block_tag])
        self.buffer.apply_tag(block_tag, self.buffer.get_iter_at_mark(start),
                              self.buffer.get_iter_at_mark(finish))
        return start, finish

    def _cell_runs(self, cell) -> list:
        """A table cell's words as one line of runs: its paragraphs joined."""
        runs = []
        for block in cell:
            words = (list(block.runs) if type(block) is Para
                     else [Run(helpview.plain(r)) for r in _block_texts(block)])
            if runs and words:
                runs.append(Run(" "))
            runs += words
        return runs

    def _measure(self, runs, bold=False) -> int:
        layout = self.view.create_pango_layout(
            "".join(f" {r.text} " if "key" in r.styles else r.text for r in runs))
        font = Pango.FontDescription()
        font.set_size(round(self.points * Pango.SCALE))
        if bold:
            font.set_weight(Pango.Weight.BOLD)
        layout.set_font_description(font)
        return layout.get_pixel_size()[0]

    def table(self, block: Table):
        """Each row one paragraph, its cells on tab stops at the widest of
        each column; the last column's words wrap beneath it."""
        spans = []
        if block.title:
            spans.append(self.paragraph(block.title, top=0.4, bottom=0.3, bold=True))
        rows = [[self._cell_runs(cell) for cell in row] for row in block.rows]
        columns = max((len(r) for r in rows), default=1)
        pad = round(self.em * 0.45)
        room = self.width - self.left - self.right
        widths = []
        for c in range(columns - 1):
            natural = max((self._measure(r[c], i < block.header_rows)
                           for i, r in enumerate(rows) if c < len(r)), default=0)
            widths.append(min(natural + 2 * pad, room * 0.45))
        stops = [sum(widths[:i + 1]) for i in range(len(widths))]
        for i, row in enumerate(rows):
            head = i < block.header_rows
            runs = []
            for c, cell in enumerate(row):
                if c:
                    runs.append(Run("\t"))
                runs += cell
            span = self.paragraph(runs, top=0.45, bottom=0.45, inside=0.2, bold=head,
                                  colour="heading" if head else None,
                                  left=self.left + pad, indent=-(stops[-1] if stops else 0),
                                  tabs=stops or None)
            if head:
                self.decorations.append(Box(span[0], span[1], self.left, self.right,
                                            background=helpview.TABLE_HEAD))
            spans.append(span)
        if spans:
            self.pad(spans[-1][0], spans[-1][1], below=round(self.em * 0.45) + BOX_GAP)
        return (spans[0][0], spans[-1][1]) if spans else None

    def topic_links(self, block: TopicLinks):
        spans = []
        for i, entry in enumerate(block.entries):
            spans.append(self.paragraph([Run(entry.title, link=entry.topic)],
                                        top=0.7 if i else 0.5, bottom=0, inside=0.15,
                                        scale=helpview.SCALE["topic"]))
            spans.append(self.paragraph(entry.desc, top=0.1, bottom=0, inside=0.2,
                                        scale=helpview.SCALE["desc"], colour="grey"))
        return (spans[0][0], spans[-1][1]) if spans else None

    def page_links(self, block: PageLinks):
        spans = []
        first = True
        for heading, entries, desc in ((helpview.MORE_INFORMATION, block.more, False),
                                       (helpview.SEE_ALSO, block.also, True)):
            if not entries:
                continue
            span = self.paragraph([Run(heading)], top=2.6 if first else 0.5, bottom=0.2,
                                  bold=True, colour="heading")
            if first:
                self.decorations.append(Rule(span[0], helpview.LINK, 2,
                                             round(2.0 * self.em), False))
                first = False
            spans.append(span)
            for entry in entries:
                # The link's own colour outranks the faint grey of the rest.
                runs = [Run(entry.title, link=entry.topic)]
                if desc and entry.desc:
                    runs += [Run(helpview.DESC_DASH)] + list(entry.desc)
                spans.append(self.paragraph(runs, top=0.35, bottom=0, inside=0.25,
                                            colour="faint"))
        if spans:
            self.pad(spans[-1][0], spans[-1][1], below=round(self.em))
        return (spans[0][0], spans[-1][1]) if spans else None


def _block_texts(block) -> List[tuple]:
    """The runs of a block that is not a paragraph, for a table cell."""
    out = []
    for name in ("runs", "title"):
        value = getattr(block, name, None)
        if isinstance(value, tuple) and value and isinstance(value[0], Run):
            out.append(value)
    return out


# --- the window -------------------------------------------------------------------

class HelpWindow(Gtk.Window):
    """One help window: a page at a time, with Back and Forward, Search,
    Find, the text size and Print."""

    def __init__(self, help_: Optional[helpview.Help] = None, settings_files=None):
        super().__init__(title=helpview.WINDOW_TITLE)
        self.help = help_ or helpview.Help()
        self.history = helpview.History()
        if settings_files is None:
            from .window import _config_path, _fallback_config_path
            settings_files = (_config_path(), _fallback_config_path())
        self.settings_files = tuple(settings_files)
        self.text_size = helpview.load_text_size(self.settings_files)
        self.document: Optional[helpview.Document] = None
        self.links: Dict[Gtk.TextTag, Tuple[str, str]] = {}
        self.anchors: Dict[str, Gtk.TextMark] = {}
        self.matches: List[Tuple[int, int]] = []
        self.match = -1
        self._watch = None
        self._buffer = b""
        self._placing = None    # the idle call that will scroll the page into place
        self.set_default_size(900, 720)
        self.set_icon_name("help-browser")
        self.accel_group = Gtk.AccelGroup()
        self.add_accel_group(self.accel_group)
        self.accelerators: Dict[str, List[str]] = {}
        self.handlers = {"back": self.go_back, "forward": self.go_forward,
                         "search": self.toggle_search, "find": self.show_find,
                         "print": self.print_page, "larger": self.larger,
                         "smaller": self.smaller,
                         "contents": lambda: self.open(helpdocs.CONTENTS),
                         "viewer_help": lambda: self.open(self.help.viewer_topic()),
                         "close": self.close}
        self._build_header()
        self._build_body()
        self._bind_keys()
        self._update_actions()

    # building ------------------------------------------------------------------

    def _icon_button(self, name: str, icon: str, toggle=False) -> Gtk.Button:
        button = Gtk.ToggleButton() if toggle else Gtk.Button()
        button.set_name(name)
        button.set_image(Gtk.Image.new_from_icon_name(icon_name(icon), Gtk.IconSize.BUTTON))
        label, keys = helpview.COMMANDS.get(name, ("Menu", ()))
        button.set_tooltip_text(f"{label} ({keys[0]})" if keys else label)
        button.set_focus_on_click(False)
        return button

    def _build_header(self):
        header = Gtk.HeaderBar()
        header.set_show_close_button(True)
        header.set_title(helpview.WINDOW_TITLE)
        header.set_subtitle(helpview.WINDOW_TITLE)
        self.header = header
        self.buttons: Dict[str, Gtk.Button] = {}
        linked = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        linked.get_style_context().add_class("linked")
        for name, icon in (("back", "go-previous-symbolic"), ("forward", "go-next-symbolic")):
            button = self._icon_button(name, icon)
            button.connect("clicked", lambda _b, n=name: self.handlers[n]())
            linked.pack_start(button, False, False, 0)
            self.buttons[name] = button
        header.pack_start(linked)

        menu_button = Gtk.MenuButton()
        menu_button.set_name("menu")
        menu_button.set_image(Gtk.Image.new_from_icon_name(icon_name("open-menu-symbolic"),
                                                           Gtk.IconSize.BUTTON))
        menu_button.set_tooltip_text("Menu")
        menu = Gtk.Menu()
        menu.set_accel_group(self.accel_group)
        self.menu_items: Dict[str, Gtk.MenuItem] = {}
        for name in helpview.MENU:
            if name is None:
                menu.append(Gtk.SeparatorMenuItem())
                continue
            item = Gtk.MenuItem(label=helpview.COMMANDS[name][0])
            item.set_name(name)
            item.connect("activate", lambda _i, n=name: self.handlers[n]())
            menu.append(item)
            self.menu_items[name] = item
        menu.show_all()
        menu_button.set_popup(menu)
        self.menu = menu
        header.pack_end(menu_button)
        self.buttons["menu"] = menu_button

        search = self._icon_button("search", "edit-find-symbolic", toggle=True)
        header.pack_end(search)
        self.buttons["search"] = search
        self.set_titlebar(header)

    def _build_body(self):
        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.add(column)

        self.search_bar = Gtk.SearchBar()
        self.search_bar.set_name("search_bar")
        self.search_entry = Gtk.SearchEntry()
        self.search_entry.set_name("search_entry")
        self.search_entry.set_placeholder_text("Search the help")
        self.search_entry.set_width_chars(40)
        self.search_entry.connect("activate", lambda _e: self.search())
        self.search_bar.add(self.search_entry)
        self.search_bar.connect_entry(self.search_entry)
        self.search_bar.set_show_close_button(False)
        self.buttons["search"].bind_property(
            "active", self.search_bar, "search-mode-enabled",
            GObject.BindingFlags.BIDIRECTIONAL | GObject.BindingFlags.SYNC_CREATE)
        self.search_bar.connect("notify::search-mode-enabled", self._search_toggled)
        column.pack_start(self.search_bar, False, False, 0)

        self.scroller = Gtk.ScrolledWindow()
        self.scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.page = PageView()
        self.page.set_name("page")
        self.page.connect("button-release-event", self._on_release)
        self.page.connect("motion-notify-event", self._on_motion)
        self.scroller.add(self.page)
        # The boxes and rules hang on the positions of other lines, so as
        # the view works out line heights - and the page's height changes -
        # all of it is drawn again, not just the lines it measured.
        self.scroller.get_vadjustment().connect("changed", lambda _a: self.page.queue_draw())
        column.pack_start(self.scroller, True, True, 0)

        self.find_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self.find_bar.set_name("find_bar")
        self.find_bar.set_border_width(4)
        self.find_bar.set_no_show_all(True)
        self.find_entry = Gtk.SearchEntry()
        self.find_entry.set_name("find_entry")
        self.find_entry.set_placeholder_text("Find in this page")
        self.find_entry.connect("search-changed", self.find_all)
        self.find_entry.connect("activate", lambda _e: self.find_next(1))
        self.find_entry.connect("next-match", lambda _e: self.find_next(1))
        self.find_entry.connect("previous-match", lambda _e: self.find_next(-1))
        self.find_entry.connect("stop-search", lambda _e: self.hide_find())
        self.find_entry.connect("key-press-event", self._on_find_key)
        self.find_bar.pack_start(self.find_entry, True, True, 0)
        for name, icon, text, step in (("find_previous", "go-up-symbolic", "Previous", -1),
                                       ("find_next", "go-down-symbolic", "Next", 1)):
            button = Gtk.Button.new_from_icon_name(icon, Gtk.IconSize.BUTTON)
            button.set_name(name)
            button.set_tooltip_text(text)
            button.connect("clicked", lambda _b, s=step: self.find_next(s))
            self.find_bar.pack_start(button, False, False, 0)
        self.find_count = Gtk.Label(label="")
        self.find_count.set_name("find_count")
        self.find_bar.pack_start(self.find_count, False, False, 6)
        close = Gtk.Button.new_from_icon_name("window-close-symbolic", Gtk.IconSize.BUTTON)
        close.set_name("find_close")
        close.set_relief(Gtk.ReliefStyle.NONE)
        close.set_tooltip_text("Close the find bar (Escape)")
        close.connect("clicked", lambda _b: self.hide_find())
        self.find_bar.pack_end(close, False, False, 0)
        for child in self.find_bar.get_children():
            child.show()
        column.pack_start(self.find_bar, False, False, 0)

    def _bind_keys(self):
        for name, (_label, keys) in helpview.COMMANDS.items():
            self.accelerators[name] = [helpview.gtk_accelerator(k) for k in keys]
            for i, accel in enumerate(self.accelerators[name]):
                key, mods = Gtk.accelerator_parse(accel)
                item = self.menu_items.get(name)
                if item is not None:
                    item.add_accelerator("activate", self.accel_group, key, mods,
                                         Gtk.AccelFlags.VISIBLE if i == 0 else 0)
                else:
                    self.accel_group.connect(key, mods, 0,
                                             lambda *_a, n=name: self._key(n))

    def _key(self, name: str) -> bool:
        widget = self.buttons.get(name)
        if widget is None or widget.get_sensitive():
            self.handlers[name]()
        return True

    # going places --------------------------------------------------------------

    def _scroll(self) -> Optional[float]:
        """Where the page is scrolled to: the offset of the text at its top."""
        if self.document is None:
            return None
        rect = self.page.get_visible_rect()
        found = self.page.get_iter_at_location(rect.x, rect.y)
        it = found[1] if isinstance(found, tuple) else found
        return float(it.get_offset())

    def open(self, topic: str):
        """Go to `topic` - a page, "page#section", or "search:words" - as a
        link does. A page or section that does not exist is a ValueError."""
        if not helpview.is_search(topic):
            self.help.check(topic)
        self._show(self.history.go(topic, self._scroll()))

    def go_back(self):
        if self.history.can_back():
            self._show(self.history.back(self._scroll()))

    def go_forward(self):
        if self.history.can_forward():
            self._show(self.history.forward(self._scroll()))

    def follow(self, target: Tuple[str, str]):
        kind, where = target
        if kind == "topic":
            self.open(where)
        else:
            Gtk.show_uri_on_window(self, where, Gdk.CURRENT_TIME)

    def link_at(self, x: float, y: float) -> Optional[Tuple[str, str]]:
        """The link at a point of the page's text window, if any."""
        bx, by = self.page.window_to_buffer_coords(Gtk.TextWindowType.TEXT, int(x), int(y))
        found = self.page.get_iter_at_location(bx, by)
        if isinstance(found, tuple):
            ok, it = found
            if not ok:
                return None
        else:
            it = found
        for tag in it.get_tags():
            if tag in self.links:
                return self.links[tag]
        return None

    def _on_release(self, _view, event):
        if event.button != 1 or self.page.get_buffer().get_has_selection():
            return False
        target = self.link_at(event.x, event.y)
        if target is not None:
            self.follow(target)
        return False

    def _on_motion(self, _view, event):
        window = self.page.get_window(Gtk.TextWindowType.TEXT)
        if window is not None:
            name = "pointer" if self.link_at(event.x, event.y) else "text"
            window.set_cursor(Gdk.Cursor.new_from_name(window.get_display(), name))
        return False

    def _show(self, entry: helpview.Entry):
        document = self.help.document(entry.topic)
        if document is not self.document:
            self._draw(document)
        self._place(entry)
        self._update_actions()
        if self.find_bar.get_visible():
            self.find_all()
        self.page.grab_focus()

    def _draw(self, document: helpview.Document):
        width = self.page.get_allocated_width()
        writer = PageWriter(self.page, self.text_size, width if width > 1 else 900)
        writer.build(document)
        # Writing leaves the cursor at the end, and the view scrolls to the
        # cursor when it takes the focus.
        writer.buffer.place_cursor(writer.buffer.get_start_iter())
        self.page.set_buffer(writer.buffer)
        self.page.decorations = writer.decorations
        self.links, self.anchors = writer.links, writer.anchors
        self.matches, self.match = [], -1
        self.document = document
        self.header.set_title(document.title)
        self.set_title(document.title)

    def _scroll_to(self, offset: Optional[int], heading: bool = False):
        """Put the line holding `offset` at the top of the view, or the top
        of the page for None, once the view has measured every line: it
        does so in idle calls of its own, and a scroll asked for before
        they finish - straight after a new page, say - lands wrong. A
        heading keeps a little of the room above it."""
        self.scroller.get_vadjustment().set_value(0)
        if self._placing is not None:
            GLib.source_remove(self._placing)

        def place():
            self._placing = None
            adjustment = self.scroller.get_vadjustment()
            y = 0
            if offset is not None:
                it = self.page.get_buffer().get_iter_at_offset(offset)
                y = self.page.get_line_yrange(it)[0]
                if heading:
                    y = max(y, self.page.get_iter_location(it).y - round(PAGE_MARGIN / 2))
            adjustment.set_value(min(y, adjustment.get_upper() - adjustment.get_page_size()))
            return False

        self._placing = GLib.idle_add(place, priority=GLib.PRIORITY_LOW)

    @property
    def placed(self) -> bool:
        """Whether the page has been scrolled into place since it changed."""
        return self._placing is None

    def _place(self, entry: helpview.Entry):
        buffer = self.page.get_buffer()
        _page, section = (("", "") if helpview.is_search(entry.topic)
                          else helpdocs.split_topic(entry.topic))
        if entry.scroll is not None:
            self._scroll_to(int(entry.scroll))
        elif section and section in self.anchors:
            self._scroll_to(buffer.get_iter_at_mark(self.anchors[section]).get_offset(),
                            heading=True)
        else:
            self._scroll_to(None)

    def _update_actions(self):
        self.buttons["back"].set_sensitive(self.history.can_back())
        self.buttons["forward"].set_sensitive(self.history.can_forward())
        self.menu_items["print"].set_sensitive(bool(self.document and self.document.page))
        self.menu_items["larger"].set_sensitive(self.text_size < helpview.TEXT_SIZES[-1])
        self.menu_items["smaller"].set_sensitive(self.text_size > helpview.TEXT_SIZES[0])

    # search and find -------------------------------------------------------------

    def toggle_search(self):
        self.set_search_shown(not self.search_bar.get_search_mode())

    def set_search_shown(self, shown: bool):
        self.search_bar.set_search_mode(shown)

    def _search_toggled(self, *_args):
        if self.search_bar.get_search_mode():
            self.search_entry.grab_focus()
        else:
            self.page.grab_focus()

    def search(self):
        words = " ".join(self.search_entry.get_text().split())
        if words:
            self.open(helpview.SEARCH_PREFIX + words)

    def show_find(self):
        self.find_bar.show()
        self.find_entry.grab_focus()
        self.find_all()

    def hide_find(self):
        self.find_bar.hide()
        self._clear_found()
        self.matches, self.match = [], -1
        self.page.grab_focus()

    def _on_find_key(self, _entry, event):
        if event.keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and \
                event.state & Gdk.ModifierType.SHIFT_MASK:
            self.find_next(-1)
            return True
        return False

    def _clear_found(self, names=("found", "found-current")):
        """Take Find's marks off the page. Fresh iterators for each tag:
        changing tags leaves a buffer's iterators stale, and GTK can crash
        on one used after."""
        buffer = self.page.get_buffer()
        for name in names:
            if buffer.get_tag_table().lookup(name):
                start, end = buffer.get_bounds()
                buffer.remove_tag_by_name(name, start, end)

    def find_all(self, *_args):
        """Every place the find bar's words are on the page, in any letter
        case, the first of them the current one."""
        # The entry tells of a change a moment after it, by when the bar
        # may have been closed.
        text = self.find_entry.get_text() if self.find_bar.get_visible() else ""
        self._clear_found()
        self.matches = []
        buffer = self.page.get_buffer()
        if text and self.document is not None:
            it = buffer.get_start_iter()
            while True:
                found = it.forward_search(text, Gtk.TextSearchFlags.CASE_INSENSITIVE, None)
                if not found:
                    break
                start, end = found
                self.matches.append((start.get_offset(), end.get_offset()))
                buffer.apply_tag_by_name("found", start, end)
                # Tagging left both stale; carry on from a fresh one.
                it = buffer.get_iter_at_offset(self.matches[-1][1])
        self.match = 0 if self.matches else -1
        self._show_matches()

    def find_next(self, step: int = 1):
        if self.matches:
            self.match = (self.match + step) % len(self.matches)
            self._show_matches()

    def _show_matches(self):
        buffer = self.page.get_buffer()
        self._clear_found(("found-current",))
        if self.matches:
            first, last = self.matches[self.match]
            a, b = buffer.get_iter_at_offset(first), buffer.get_iter_at_offset(last)
            buffer.apply_tag_by_name("found-current", a, b)
            self.find_count.set_text(f"{self.match + 1} of {len(self.matches)}")
            here = buffer.get_iter_at_offset(first)  # the tagging left `a` stale
            mark = buffer.get_mark("found") or buffer.create_mark("found", here, True)
            buffer.move_mark(mark, here)
            self.page.scroll_to_mark(mark, 0.1, False, 0.0, 0.0)
        else:
            self.find_count.set_text("Not found" if self.find_entry.get_text() else "")

    # the text size ---------------------------------------------------------------

    def larger(self):
        if self.text_size < helpview.TEXT_SIZES[-1]:
            self.set_text_size(helpview.larger(self.text_size))

    def smaller(self):
        if self.text_size > helpview.TEXT_SIZES[0]:
            self.set_text_size(helpview.smaller(self.text_size))

    def set_text_size(self, size: int):
        if size == self.text_size:
            return
        self.text_size = size
        helpview.save_text_size(self.settings_files, size)
        if self.document is not None:
            top = self._scroll()
            self._draw(self.document)
            self._scroll_to(int(top or 0))
            if self.find_bar.get_visible():
                self.find_all()
        self._update_actions()

    # print -----------------------------------------------------------------------

    def print_page(self):
        """Open the page's PDF in a PDF viewer, to print it from there."""
        if not (self.document and self.document.page):
            return
        window = self.get_window()
        if window is not None:
            window.set_cursor(Gdk.Cursor.new_from_name(window.get_display(), "wait"))
            Gdk.flush()
        try:
            launched = helpdocs.open_pdf(self.document.page)
        finally:
            if window is not None:
                window.set_cursor(None)
        self._follow(launched)

    def _follow(self, launched: helpdocs.Launch):
        if launched.message:
            self.say(launched.message)
            return
        started = time.monotonic()

        def poll():
            following = helpdocs.follow(launched, time.monotonic() - started)
            if following is helpdocs.WATCHING:
                return True
            if following is not None:
                self._follow(following)
            return False

        GLib.timeout_add(helpdocs.POLL_MS, poll)

    def say(self, message: str):
        dialog = Gtk.MessageDialog(transient_for=self, flags=0,
                                   message_type=Gtk.MessageType.INFO,
                                   buttons=Gtk.ButtonsType.OK,
                                   text="The page could not be opened to print")
        dialog.set_title("Print")
        dialog.format_secondary_text(message)
        dialog.run()
        dialog.destroy()

    # topics from the designer ---------------------------------------------------------

    def listen(self, fd: int = 0):
        """Take topics, one per line, from `fd` - the designer's pipe to
        standard input - going to each as it comes. At the end of the input
        the window stops listening and stays open."""
        self._fd, self._buffer = fd, b""
        self._watch = GLib.io_add_watch(fd, GLib.PRIORITY_DEFAULT,
                                        GLib.IOCondition.IN | GLib.IOCondition.HUP,
                                        self._read_topics)

    @property
    def listening(self) -> bool:
        return self._watch is not None

    def _read_topics(self, _fd, _condition):
        try:
            data = os.read(self._fd, 4096)
        except OSError:
            data = b""
        if not data:
            self._watch = None
            return False
        *lines, self._buffer = (self._buffer + data).split(b"\n")
        for line in lines:
            topic = line.decode("utf-8", "replace").strip()
            if topic:
                self.receive(topic)
        return True

    def receive(self, topic: str):
        """A topic the designer asked for: go there and come to the front.
        One that does not exist is left alone."""
        try:
            self.open(topic)
        except ValueError as e:
            print(f"linuxzpl help: {e}", file=sys.stderr)
            return
        self.present()


def main(topic: str = helpdocs.CONTENTS, listen: Optional[bool] = None) -> int:
    window = HelpWindow()
    window.connect("destroy", Gtk.main_quit)
    window.show_all()
    window.open(topic)
    if listen is None:
        listen = not os.isatty(0)
    if listen:
        window.listen(0)
    window.present()
    Gtk.main()
    return 0
