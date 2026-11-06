"""
The built-in help viewer, in Qt: LinuxZPL's own window onto the help pages,
for a system without Yelp, made to look and work like Yelp 42.

It runs as a program of its own, `linuxzpl.py --qt --show-help TOPIC`,
started by helpdocs.launch() as Yelp is: almost every Help button sits in a
modal window, which would block a window of the designer's own, and the help
stays open after the designer closes. Topics asked for later arrive one per
line on standard input, and the window goes to each and comes to the front,
so there is one help window, as there is one Yelp window.

zplcore/helpview.py makes each page into runs and blocks; this draws them
into a QTextDocument with a QTextCursor - no HTML - and shows that in a
QTextBrowser. The header bar is Yelp's: Back and Forward at the left, the
page's title over "LinuxZPL Help", and Search and the menu at the right.
"""

import os
import sys
import time
from typing import List, Optional

from PySide2.QtCore import QSocketNotifier, QSize, Qt, QTimer, QUrl
from PySide2.QtGui import (QBrush, QColor, QDesktopServices, QFont, QFontDatabase,
                           QFontInfo, QIcon, QImage, QKeySequence, QTextBlockFormat,
                           QTextCharFormat, QTextCursor, QTextDocument,
                           QTextFrameFormat, QTextImageFormat, QTextLength,
                           QTextListFormat, QTextTableFormat)
from PySide2.QtWidgets import (QAction, QApplication, QHBoxLayout, QLabel, QLineEdit,
                               QMainWindow, QMenu, QMessageBox, QShortcut, QSizePolicy,
                               QStyle, QTextBrowser, QTextEdit, QToolBar, QToolButton,
                               QVBoxLayout, QWidget)

from zplcore import helpdocs, helpview
from zplcore.helpview import (Code, Heading, Image, ListBlock, Note, PageLinks, Para,
                              Table, Terms, Title, TopicLinks, Trail)

TOPIC_SCHEME = "topic:"     # a link's href within the help
PAGE_MARGIN = 16            # pixels either side of the text, as Yelp's
ICON_SIZE = 22
STANDARD_ICONS = {"dialog-information": QStyle.SP_MessageBoxInformation,
                  "dialog-warning": QStyle.SP_MessageBoxWarning,
                  "dialog-error": QStyle.SP_MessageBoxCritical,
                  "go-previous": QStyle.SP_ArrowBack,
                  "go-next": QStyle.SP_ArrowForward}


# Other names themes give an icon, tried after the freedesktop one.
ICON_FALLBACKS = {"open-menu": ("open-menu-symbolic", "application-menu",
                                "application-menu-symbolic", "format-justify-fill"),
                  "edit-find": ("edit-find-symbolic", "system-search")}


def theme_icon(name: str) -> QIcon:
    """The desktop theme's icon, under its own name or another a theme
    gives it, or Qt's own for the few it has; a null icon otherwise, for
    which the caller shows words."""
    for candidate in (name,) + ICON_FALLBACKS.get(name, ()):
        icon = QIcon.fromTheme(candidate)
        if not icon.isNull():
            return icon
    if name in STANDARD_ICONS:
        return QApplication.style().standardIcon(STANDARD_ICONS[name])
    return QIcon()


def mono_family() -> str:
    """A fixed-pitch family that is installed: the system's own, as the
    desktop reports it, else whatever "monospace" resolves to. Some
    platforms report a proportional face as their fixed one."""
    for font in (QFontDatabase.systemFont(QFontDatabase.FixedFont), QFont("monospace")):
        font.setStyleHint(QFont.TypeWriter)
        info = QFontInfo(font)
        if info.fixedPitch():
            return info.family()
    return "monospace"


def qt_keys(name: str) -> List[QKeySequence]:
    return [QKeySequence(k) for k in helpview.COMMANDS[name][1]]


# --- drawing a page --------------------------------------------------------------

def _colour(hex_colour: str) -> QBrush:
    return QBrush(QColor(hex_colour))


class PageBuilder:
    """Draws one helpview.Document into a QTextDocument, block by block, with
    a QTextCursor that is always at the document's end."""

    def __init__(self, qdoc: QTextDocument, text_size: int, width: int):
        self.qdoc = qdoc
        self.points = helpview.BASE_POINTS * text_size / 100
        self.width = width
        font = QFont(QApplication.font())
        font.setPointSizeF(self.points)
        qdoc.setDefaultFont(font)
        qdoc.setDocumentMargin(0)
        self.em = QFontInfo(font).pixelSize() or self.points * 4 / 3
        qdoc.setIndentWidth(round(self.em * 1.8))
        self.mono = mono_family()
        self.cursor = QTextCursor(qdoc)
        self.fresh = True       # the cursor's block is empty and not yet used
        self.left = self.right = PAGE_MARGIN
        self.depth = 0          # how many lists deep
        self.pending = 0.0      # room, in ems, the next block or frame leaves above it

    # inline -----------------------------------------------------------------

    def char(self, run=None, scale=1.0, colour=helpview.TEXT, bold=False,
             link_colour=helpview.LINK, code=False) -> QTextCharFormat:
        styles = run.styles if run else frozenset()
        f = QTextCharFormat()
        size = self.points * scale
        f.setForeground(_colour(colour))
        if "gui" in styles:
            f.setForeground(_colour(helpview.GREY))
        if "mono" in styles:
            f.setFontFamily(self.mono)
            size *= 0.9
            if not code:
                f.setBackground(_colour(helpview.BAND))
        if "key" in styles:
            f.setForeground(_colour(helpview.HEADING))
            f.setBackground(_colour(helpview.BAND))
            size *= 0.92
        if "join" in styles:
            f.setForeground(_colour(helpview.KEY_JOIN))
            size *= 0.85
        f.setFontPointSize(size)
        if bold or "bold" in styles:
            f.setFontWeight(QFont.Bold)
        if "italic" in styles:
            f.setFontItalic(True)
        if run is not None and (run.link or run.href):
            f.setAnchor(True)
            f.setAnchorHref(TOPIC_SCHEME + run.link if run.link else run.href)
            f.setForeground(_colour(link_colour))
        return f

    def runs(self, runs, **kwargs):
        for run in runs:
            text = run.text
            if "key" in run.styles:
                text = f" {text} "  # a keycap's room either side
            self.cursor.insertText(text, self.char(run, **kwargs))

    # blocks ------------------------------------------------------------------

    def start(self, top=0.0, bottom=0.0, indent=0.0, line_height=135,
              chars: Optional[QTextCharFormat] = None) -> QTextBlockFormat:
        """A new block to write into, `top`, `bottom` and `indent` in ems."""
        fmt = QTextBlockFormat()
        fmt.setTopMargin((top + self.take_pending()) * self.em)
        fmt.setBottomMargin(bottom * self.em)
        fmt.setLeftMargin(self.left + indent * self.em)
        fmt.setRightMargin(self.right)
        fmt.setLineHeight(line_height, QTextBlockFormat.ProportionalHeight)
        chars = chars or self.char()
        if self.fresh:
            self.cursor.setBlockFormat(fmt)
            # A table cell's first block carries the cell's own format - its
            # background and padding - which the text's must join, not replace.
            held = self.cursor.blockCharFormat()
            if held.isTableCellFormat():
                held.merge(chars)
                self.cursor.setBlockCharFormat(held)
            else:
                self.cursor.setBlockCharFormat(chars)
            self.cursor.setCharFormat(chars)
            self.fresh = False
        else:
            self.cursor.insertBlock(fmt, chars)
        return fmt

    def take_pending(self) -> float:
        pending, self.pending = self.pending, 0.0
        return pending

    def shrink(self):
        """Make the cursor's block a pixel high. A frame or a table is put
        after the block the cursor is in, so one opening a page, or
        following straight on from another frame, would otherwise leave an
        empty line above it."""
        if self.fresh:
            tiny = QTextCharFormat()
            tiny.setFontPointSize(1)
            held = self.cursor.blockCharFormat()
            if held.isTableCellFormat():
                held.merge(tiny)
                tiny = held
            self.cursor.setBlockCharFormat(tiny)
            self.cursor.setCharFormat(tiny)
            block = QTextBlockFormat()
            block.setLineHeight(1, QTextBlockFormat.FixedHeight)
            self.cursor.setBlockFormat(block)

    def spacer(self, pixels: float):
        """An empty line `pixels` high, for room above a frame: Qt leaves
        out the bottom margin of a block a frame follows."""
        tiny = QTextCharFormat()
        tiny.setFontPointSize(1)
        block = QTextBlockFormat()
        block.setLineHeight(pixels, QTextBlockFormat.FixedHeight)
        if self.fresh:
            self.cursor.setBlockFormat(block)
            self.cursor.setBlockCharFormat(tiny)
        else:
            self.cursor.insertBlock(block, tiny)
        self.fresh = False

    def after(self, frame):
        """Carry on below a frame or table just filled."""
        self.cursor.setPosition(frame.lastPosition())
        self.cursor.movePosition(QTextCursor.NextBlock)
        self.fresh = True

    def frame(self, background=None, border=None, padding=0, top=0.0,
              bottom=0.0, margins=True) -> "QTextFrame":  # noqa: F821
        """A frame to write into; Qt 5 pads all four sides alike, so more
        room at the sides is the inner blocks' margins."""
        fmt = QTextFrameFormat()
        if background:
            fmt.setBackground(_colour(background))
        if border:
            fmt.setBorder(1)
            fmt.setBorderBrush(_colour(border))
            fmt.setBorderStyle(QTextFrameFormat.BorderStyle_Solid)
        fmt.setTopMargin((top + self.take_pending()) * self.em)
        fmt.setBottomMargin(bottom * self.em)
        if margins:
            fmt.setLeftMargin(self.left)
            fmt.setRightMargin(self.right)
        fmt.setPadding(padding)
        self.shrink()
        frame = self.cursor.insertFrame(fmt)
        self.fresh = True
        return frame

    def inside(self, left=0, right=0):
        """Save the margins, for a container whose blocks start at its edge."""
        saved = (self.left, self.right)
        self.left, self.right = left, right
        return saved

    def rule(self, colour: str, thickness: int, top=0.0, bottom=0.0):
        """A line across the page: a frame holding nothing, its height the
        line's, filled with its colour. Qt paints a fixed-height frame's
        margins in its colour too, so the room above the line is the block
        before it, and the room below is left to the block after."""
        fmt = QTextFrameFormat()
        fmt.setBackground(_colour(colour))
        fmt.setHeight(QTextLength(QTextLength.FixedLength, thickness))
        fmt.setMargin(0)
        fmt.setPadding(0)
        room = (top + self.take_pending()) * self.em
        if room:
            self.spacer(room)
        self.shrink()
        frame = self.cursor.insertFrame(fmt)
        tiny = QTextCharFormat()
        tiny.setFontPointSize(1)
        self.cursor.setBlockCharFormat(tiny)
        self.cursor.setCharFormat(tiny)
        block = QTextBlockFormat()
        block.setLineHeight(thickness, QTextBlockFormat.FixedHeight)
        self.cursor.setBlockFormat(block)
        self.after(frame)
        self.pending = bottom

    def build(self, document: helpview.Document):
        for block in document.blocks:
            self.block(block)

    def blocks(self, blocks, first_top=None):
        for i, block in enumerate(blocks):
            self.block(block, top=first_top if i == 0 else None)

    def block(self, block, top=None):
        kind = type(block)
        if kind is Trail:
            self.trail(block)
        elif kind is Title:
            self.start(top=0.6, bottom=0.4, line_height=110)
            self.runs(block.runs, scale=helpview.SCALE["title"], colour=helpview.HEADING)
        elif kind is Heading:
            self.heading(block)
        elif kind is Para:
            self.start(top=0 if top is None else top, bottom=0.75)
            self.runs(block.runs)
        elif kind is Code:
            self.code(block)
        elif kind is ListBlock:
            self.list(block)
        elif kind is Terms:
            self.terms(block)
        elif kind is Note:
            self.note(block)
        elif kind is Image:
            self.image(block)
        elif kind is Table:
            self.table(block)
        elif kind is TopicLinks:
            self.topic_links(block)
        elif kind is PageLinks:
            self.page_links(block)
        else:
            raise TypeError(f"no way to draw {kind.__name__}")

    def trail(self, block: Trail):
        saved = self.inside(PAGE_MARGIN - 5, PAGE_MARGIN - 5)
        frame = self.frame(background=helpview.BAND, padding=5, margins=False)
        for trail in block.trails:
            self.start(line_height=120)
            for i, crumb in enumerate(trail):
                if i:
                    self.runs([helpview.Run(helpview.TRAIL_SEP)],
                              scale=helpview.SCALE["trail"], colour=helpview.GREY)
                self.runs([helpview.Run(crumb.text, link=crumb.topic)],
                          scale=helpview.SCALE["trail"], link_colour=helpview.GREY)
            self.runs([helpview.Run(helpview.TRAIL_END)],
                      scale=helpview.SCALE["trail"], colour=helpview.GREY)
        self.after(frame)
        self.left, self.right = saved

    def heading(self, block: Heading):
        scale = helpview.SCALE.get(f"h{min(block.level, 3)}", 1.0)
        self.start(top=1.6 if block.level == 1 else 1.0, bottom=0.15, line_height=110)
        for i, run in enumerate(block.runs):
            f = self.char(run, scale=scale, colour=helpview.HEADING)
            if i == 0 and block.anchor:
                f.setAnchor(True)
                f.setAnchorNames([block.anchor])
            self.cursor.insertText(run.text, f)
        if block.level == 1:
            self.rule(helpview.RULE, 1, bottom=0.5)

    def code(self, block: Code):
        saved = self.left, self.right
        pad = round(self.em * 0.5)
        frame = self.frame(background=helpview.BAND if block.screen else "#ffffff",
                           border=helpview.RULE, padding=pad, top=0.2, bottom=0.9)
        self.left = self.right = max(12 - pad, 0)
        self.start(line_height=120)
        text = helpview.plain(block.runs)
        self.cursor.insertText(text, self.char(block.runs[0] if block.runs else None,
                                               code=True))
        self.after(frame)
        self.left, self.right = saved

    def list(self, block: ListBlock):
        if block.title:
            self.start(top=0.4, bottom=0.3)
            self.runs(block.title, bold=True)
        saved = self.left, self.right
        steps = block.kind == "steps"
        if steps:
            table = self.box(helpview.STEPS_EDGE, None, helpview.STEPS_BAR, 4, top=0.1)
            cursor = table.cellAt(0, 1).firstCursorPosition()
            self.cursor, self.fresh = cursor, True
            self.left, self.right = 0, 0
        style = (QTextListFormat.ListDisc if block.kind == "bullet"
                 else QTextListFormat.ListDecimal)
        self.depth += 1
        qlist = None
        for item in block.items:
            first, rest = item[0], item[1:]
            if type(first) is Para:
                self.start(bottom=0.55)
                self.runs(first.runs)
            else:
                self.start(bottom=0.3)
                rest = item
            fmt = QTextListFormat()
            fmt.setStyle(style)
            fmt.setIndent(self.depth)
            if qlist is None:
                qlist = self.cursor.createList(fmt)
            else:
                qlist.add(self.cursor.block())
            inner = self.left
            self.left = inner + self.depth * self.qdoc.indentWidth()
            self.blocks(rest)
            self.left = inner
        self.depth -= 1
        if steps:
            self.after(table)
            self.left, self.right = saved

    def box(self, edge, background, strip, strip_width, top=0.0, bottom=0.9):
        """A one-row table: a narrow first cell (a coloured bar, or an
        icon's room) and a second holding the content."""
        fmt = QTextTableFormat()
        fmt.setBorder(1)
        fmt.setBorderBrush(_colour(edge))
        fmt.setBorderStyle(QTextFrameFormat.BorderStyle_Solid)
        if hasattr(fmt, "setBorderCollapse"):
            fmt.setBorderCollapse(True)
        fmt.setCellSpacing(0)
        fmt.setCellPadding(0)
        if background:
            fmt.setBackground(_colour(background))
        fmt.setWidth(QTextLength(QTextLength.PercentageLength, 100))
        fmt.setColumnWidthConstraints([QTextLength(QTextLength.FixedLength, strip_width),
                                       QTextLength(QTextLength.VariableLength, 0)])
        fmt.setLeftMargin(self.left)
        fmt.setRightMargin(self.right)
        fmt.setTopMargin((top + self.take_pending()) * self.em)
        fmt.setBottomMargin(bottom * self.em)
        self.shrink()
        table = self.cursor.insertTable(1, 2, fmt)
        bar = table.cellAt(0, 0)
        cell = bar.format().toTableCellFormat()
        if strip:
            cell.setBackground(_colour(strip))
        bar.setFormat(cell)
        content = table.cellAt(0, 1)
        cell = content.format().toTableCellFormat()
        pad = round(self.em * 0.6)
        cell.setTopPadding(pad)
        cell.setBottomPadding(max(pad - round(self.em * 0.55), 2))
        cell.setLeftPadding(12)
        cell.setRightPadding(12)
        content.setFormat(cell)
        return table

    def terms(self, block: Terms):
        if block.title:
            self.start(top=0.4, bottom=0.3)
            self.runs(block.title, bold=True)
        for terms, inner in block.items:
            for term in terms:
                self.start(top=0.3, bottom=0.15)
                self.runs(term)
            saved = self.left
            self.left += helpview.INDENT_EMS * self.em
            self.blocks(inner)
            self.left = saved

    def note(self, block: Note):
        saved = self.left, self.right
        width = ICON_SIZE + 2 * PAGE_MARGIN
        table = self.box(helpview.NOTE_EDGE, helpview.BAND, None, width, top=0.1)
        icon_cell = table.cellAt(0, 0)
        cell = icon_cell.format().toTableCellFormat()
        cell.setTopPadding(round(self.em * 0.6))
        cell.setLeftPadding(PAGE_MARGIN - 4)
        icon_cell.setFormat(cell)
        self.cursor, self.fresh = icon_cell.firstCursorPosition(), True
        self.left = self.right = 0
        self.start(line_height=100)
        pixmap = theme_icon(block.icon).pixmap(ICON_SIZE, ICON_SIZE)
        if pixmap.isNull():
            self.runs([helpview.Run(block.label, frozenset({"bold"}))], scale=0.85)
        else:
            name = f"icon:{block.icon}"
            self.qdoc.addResource(QTextDocument.ImageResource, QUrl(name), pixmap.toImage())
            image = QTextImageFormat()
            image.setName(name)
            image.setWidth(ICON_SIZE)
            image.setHeight(ICON_SIZE)
            image.setToolTip(block.label)
            self.cursor.insertImage(image)
        self.cursor, self.fresh = table.cellAt(0, 1).firstCursorPosition(), True
        if block.title:
            self.start(bottom=0.4)
            self.runs(block.title, bold=True)
        self.blocks(block.blocks)
        self.after(table)
        self.left, self.right = saved

    def image(self, block: Image):
        img = QImage(str(block.path))
        width, height = block.width, block.height
        if width and not height:
            height = img.height() * width / img.width()
        elif height and not width:
            width = img.width() * height / img.height()
        elif not width:
            width, height = img.width(), img.height()
        room = max(self.width - self.left - self.right, 50)
        if width > room:
            width, height = room, height * room / width
        name = f"file:{block.path}"
        self.qdoc.addResource(QTextDocument.ImageResource, QUrl(name), img)
        self.start(bottom=0.75, line_height=100)
        fmt = QTextImageFormat()
        fmt.setName(name)
        fmt.setWidth(width)
        fmt.setHeight(height)
        self.cursor.insertImage(fmt)

    def table(self, block: Table):
        if block.title:
            self.start(top=0.4, bottom=0.3)
            self.runs(block.title, bold=True)
        rows, columns = len(block.rows), max((len(r) for r in block.rows), default=1)
        fmt = QTextTableFormat()
        fmt.setBorder(0)
        fmt.setCellSpacing(0)
        fmt.setCellPadding(round(self.em * 0.45))
        fmt.setWidth(QTextLength(QTextLength.PercentageLength, 100))
        fmt.setLeftMargin(self.left)
        fmt.setRightMargin(self.right)
        fmt.setTopMargin(self.take_pending() * self.em)
        fmt.setBottomMargin(self.em)
        fmt.setHeaderRowCount(block.header_rows)
        self.shrink()
        table = self.cursor.insertTable(max(rows, 1), columns, fmt)
        saved = self.left, self.right
        self.left = self.right = 0
        for r, row in enumerate(block.rows):
            head = r < block.header_rows
            for c, cell_blocks in enumerate(row):
                cell = table.cellAt(r, c)
                if head:
                    cf = cell.format().toTableCellFormat()
                    cf.setBackground(_colour(helpview.TABLE_HEAD))
                    cell.setFormat(cf)
                self.cursor, self.fresh = cell.firstCursorPosition(), True
                for block_ in cell_blocks:
                    if head and type(block_) is Para:
                        self.start(line_height=120)
                        self.runs(block_.runs, bold=True, colour=helpview.HEADING)
                    elif type(block_) is Para:
                        self.start(line_height=120)
                        self.runs(block_.runs)
                    else:
                        self.block(block_)
        self.after(table)
        self.left, self.right = saved

    def topic_links(self, block: TopicLinks):
        for i, entry in enumerate(block.entries):
            self.start(top=0.7 if i else 0.5, line_height=115)
            self.runs([helpview.Run(entry.title, link=entry.topic)],
                      scale=helpview.SCALE["topic"])
            self.start(top=0.1, line_height=120)
            self.runs(entry.desc, scale=helpview.SCALE["desc"], colour=helpview.GREY)

    def page_links(self, block: PageLinks):
        self.rule(helpview.LINK, 2, top=2.0, bottom=0.6)
        for heading, entries, desc in ((helpview.MORE_INFORMATION, block.more, False),
                                       (helpview.SEE_ALSO, block.also, True)):
            if not entries:
                continue
            self.start(top=0.5, bottom=0.2)
            self.runs([helpview.Run(heading)], bold=True, colour=helpview.HEADING)
            for entry in entries:
                self.start(top=0.35, line_height=125)
                self.runs([helpview.Run(entry.title, link=entry.topic)])
                if desc and entry.desc:
                    self.runs([helpview.Run(helpview.DESC_DASH)], colour=helpview.FAINT)
                    self.runs(entry.desc, colour=helpview.FAINT)
        self.start(bottom=1.0)


# --- the window ------------------------------------------------------------------

class HelpWindow(QMainWindow):
    """One help window: a page at a time, with Back and Forward, Search,
    Find, the text size and Print."""

    def __init__(self, help_: Optional[helpview.Help] = None, settings_files=None):
        super().__init__()
        self.help = help_ or helpview.Help()
        self.history = helpview.History()
        if settings_files is None:
            from .window import _config_path, _fallback_config_path
            settings_files = (_config_path(), _fallback_config_path())
        self.settings_files = tuple(settings_files)
        self.text_size = helpview.load_text_size(self.settings_files)
        self.document: Optional[helpview.Document] = None
        self.matches: List[QTextCursor] = []
        self.match = -1
        self._notifier = None
        self._buffer = b""
        self._qdoc: Optional[QTextDocument] = None
        self.setWindowTitle(helpview.WINDOW_TITLE)
        self.setWindowIcon(QIcon.fromTheme("help-browser"))
        self.resize(900, 720)
        self.actions = {}
        self._build_actions()
        self._build_header()
        self._build_body()
        self._update_actions()

    # building ------------------------------------------------------------------

    def _build_actions(self):
        handlers = {"back": self.go_back, "forward": self.go_forward,
                    "search": self.toggle_search, "find": self.show_find,
                    "print": self.print_page, "larger": self.larger,
                    "smaller": self.smaller,
                    "contents": lambda: self.open(helpdocs.CONTENTS),
                    "viewer_help": lambda: self.open(self.help.viewer_topic()),
                    "close": self.close}
        for name, (label, _keys) in helpview.COMMANDS.items():
            action = QAction(label, self)
            action.setObjectName(name)
            action.setShortcuts(qt_keys(name))
            action.setShortcutContext(Qt.WindowShortcut)
            action.triggered.connect(lambda _checked=False, h=handlers[name]: h())
            self.addAction(action)
            self.actions[name] = action
        self.actions["search"].setCheckable(True)

    def _button(self, name: str, icon_name: str) -> QToolButton:
        button = QToolButton()
        button.setObjectName(name)
        button.setAutoRaise(False)
        icon = theme_icon(icon_name)
        label, keys = helpview.COMMANDS.get(name, ("Menu", ()))
        if icon.isNull():
            button.setText(label)
        else:
            button.setIcon(icon)
        button.setToolTip(f"{label} ({keys[0]})" if keys else label)
        button.setIconSize(QSize(16, 16))
        return button

    def _build_header(self):
        bar = QToolBar("Header")
        bar.setObjectName("header")
        bar.setMovable(False)
        bar.setFloatable(False)
        bar.toggleViewAction().setVisible(False)
        self.addToolBar(Qt.TopToolBarArea, bar)
        self.buttons = {}
        for name, icon in (("back", "go-previous"), ("forward", "go-next")):
            button = self._button(name, icon)
            button.clicked.connect(self.actions[name].trigger)
            bar.addWidget(button)
            self.buttons[name] = button

        def stretch():
            spacer = QWidget()
            spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
            return spacer

        bar.addWidget(stretch())
        titles = QWidget()
        column = QVBoxLayout(titles)
        column.setContentsMargins(0, 2, 0, 2)
        column.setSpacing(0)
        self.title_label = QLabel(helpview.WINDOW_TITLE)
        self.title_label.setObjectName("title")
        bold = self.title_label.font()
        bold.setBold(True)
        self.title_label.setFont(bold)
        self.subtitle_label = QLabel(helpview.WINDOW_TITLE)
        self.subtitle_label.setObjectName("subtitle")
        small = self.subtitle_label.font()
        small.setPointSizeF(small.pointSizeF() * 0.85)
        self.subtitle_label.setFont(small)
        self.subtitle_label.setStyleSheet(f"color: {helpview.GREY};")
        for label in (self.title_label, self.subtitle_label):
            label.setAlignment(Qt.AlignHCenter)
            column.addWidget(label)
        bar.addWidget(titles)
        bar.addWidget(stretch())
        search = self._button("search", "edit-find")
        search.setCheckable(True)
        search.toggled.connect(self.set_search_shown)
        bar.addWidget(search)
        self.buttons["search"] = search
        menu_button = self._button("menu", "open-menu")
        menu = QMenu(menu_button)
        menu.setObjectName("menu")
        for name in helpview.MENU:
            if name is None:
                menu.addSeparator()
            else:
                menu.addAction(self.actions[name])
        menu_button.setMenu(menu)
        menu_button.setPopupMode(QToolButton.InstantPopup)
        menu_button.setStyleSheet("QToolButton::menu-indicator { image: none; }")
        bar.addWidget(menu_button)
        self.buttons["menu"] = menu_button
        self.menu = menu

    def _build_body(self):
        body = QWidget()
        column = QVBoxLayout(body)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)

        self.search_bar = QWidget()
        self.search_bar.setObjectName("search_bar")
        row = QHBoxLayout(self.search_bar)
        row.setContentsMargins(8, 6, 8, 6)
        self.search_entry = QLineEdit()
        self.search_entry.setObjectName("search_entry")
        self.search_entry.setPlaceholderText("Search the help")
        self.search_entry.setClearButtonEnabled(True)
        self.search_entry.returnPressed.connect(self.search)
        row.addStretch(1)
        row.addWidget(self.search_entry, 3)
        row.addStretch(1)
        QShortcut(QKeySequence(Qt.Key_Escape), self.search_bar,
                  lambda: self.set_search_shown(False),
                  context=Qt.WidgetWithChildrenShortcut)
        self.search_bar.hide()
        column.addWidget(self.search_bar)

        self.page = QTextBrowser()
        self.page.setObjectName("page")
        self.page.setOpenLinks(False)
        self.page.setOpenExternalLinks(False)
        self.page.setFrameShape(QTextBrowser.NoFrame)
        palette = self.page.palette()
        palette.setColor(palette.Base, QColor("#ffffff"))
        palette.setColor(palette.Text, QColor(helpview.TEXT))
        self.page.setPalette(palette)
        self.page.anchorClicked.connect(self.follow_link)
        column.addWidget(self.page, 1)

        self.find_bar = QWidget()
        self.find_bar.setObjectName("find_bar")
        row = QHBoxLayout(self.find_bar)
        row.setContentsMargins(8, 4, 8, 4)
        self.find_entry = QLineEdit()
        self.find_entry.setObjectName("find_entry")
        self.find_entry.setPlaceholderText("Find in this page")
        self.find_entry.textChanged.connect(self.find_all)
        self.find_entry.returnPressed.connect(lambda: self.find_next(1))
        QShortcut(QKeySequence(Qt.SHIFT + Qt.Key_Return), self.find_entry,
                  lambda: self.find_next(-1), context=Qt.WidgetShortcut)
        self.find_count = QLabel("")
        self.find_count.setObjectName("find_count")
        row.addWidget(self.find_entry, 1)
        for name, text, step in (("find_previous", "Previous", -1), ("find_next", "Next", 1)):
            button = QToolButton()
            button.setObjectName(name)
            icon = theme_icon("go-up" if step < 0 else "go-down")
            button.setIcon(icon) if not icon.isNull() else button.setText(text)
            button.setToolTip(text)
            button.clicked.connect(lambda _c=False, s=step: self.find_next(s))
            row.addWidget(button)
        row.addWidget(self.find_count)
        row.addStretch(1)
        close = QToolButton()
        close.setObjectName("find_close")
        icon = theme_icon("window-close")
        close.setIcon(icon) if not icon.isNull() else close.setText("Close")
        close.setToolTip("Close the find bar (Escape)")
        close.clicked.connect(self.hide_find)
        row.addWidget(close)
        QShortcut(QKeySequence(Qt.Key_Escape), self.find_bar, self.hide_find,
                  context=Qt.WidgetWithChildrenShortcut)
        self.find_bar.hide()
        column.addWidget(self.find_bar)
        self.setCentralWidget(body)

    # going places ----------------------------------------------------------------

    def _scroll(self) -> Optional[float]:
        return float(self.page.verticalScrollBar().value()) if self.document else None

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

    def follow_link(self, url: QUrl):
        href = url.toString()
        if href.startswith(TOPIC_SCHEME):
            self.open(href[len(TOPIC_SCHEME):])
        else:
            QDesktopServices.openUrl(url)

    def _show(self, entry: helpview.Entry):
        document = self.help.document(entry.topic)
        if document is not self.document:
            self._draw(document)
        self._place(entry)
        self._update_actions()
        if self.find_bar.isVisible():
            self.find_all()
        self.page.setFocus()

    def _draw(self, document: helpview.Document):
        qdoc = QTextDocument(self.page)
        PageBuilder(qdoc, self.text_size, self.page.viewport().width()).build(document)
        # The browser deletes only the document it started with; each page
        # drawn here goes when the next replaces it.
        old, self._qdoc = self._qdoc, qdoc
        self.page.setDocument(qdoc)
        if old is not None:
            old.deleteLater()
        self.document = document
        self.title_label.setText(document.title)
        self.setWindowTitle(document.title)

    def _lay_out(self):
        """Finish laying the page out now, so the scroll bar's range is
        right before a place is scrolled to."""
        self.page.document().setTextWidth(self.page.viewport().width())
        self.page.document().documentLayout().documentSize()

    def _place(self, entry: helpview.Entry):
        self._lay_out()
        bar = self.page.verticalScrollBar()
        _page, section = (("", "") if helpview.is_search(entry.topic)
                          else helpdocs.split_topic(entry.topic))
        if entry.scroll is not None:
            bar.setValue(int(entry.scroll))
        elif section:
            self.page.scrollToAnchor(section)
        else:
            bar.setValue(0)

    def _update_actions(self):
        self.actions["back"].setEnabled(self.history.can_back())
        self.actions["forward"].setEnabled(self.history.can_forward())
        self.buttons["back"].setEnabled(self.history.can_back())
        self.buttons["forward"].setEnabled(self.history.can_forward())
        self.actions["print"].setEnabled(bool(self.document and self.document.page))
        self.actions["larger"].setEnabled(self.text_size < helpview.TEXT_SIZES[-1])
        self.actions["smaller"].setEnabled(self.text_size > helpview.TEXT_SIZES[0])

    # search and find -------------------------------------------------------------

    def toggle_search(self):
        self.set_search_shown(not self.search_bar.isVisible())

    def set_search_shown(self, shown: bool):
        self.search_bar.setVisible(shown)
        for widget in (self.buttons["search"], self.actions["search"]):
            widget.blockSignals(True)
            widget.setChecked(shown)
            widget.blockSignals(False)
        if shown:
            self.search_entry.setFocus()
            self.search_entry.selectAll()
        else:
            self.page.setFocus()

    def search(self):
        words = " ".join(self.search_entry.text().split())
        if words:
            self.open(helpview.SEARCH_PREFIX + words)

    def show_find(self):
        self.find_bar.show()
        self.find_entry.setFocus()
        self.find_entry.selectAll()
        self.find_all()

    def hide_find(self):
        self.find_bar.hide()
        self.matches, self.match = [], -1
        self.page.setExtraSelections([])
        self.page.setFocus()

    def find_all(self, *_args):
        """Every place the find bar's words are on the page, in any letter
        case, the first of them the current one."""
        text = self.find_entry.text()
        self.matches = []
        if text:
            qdoc = self.page.document()
            cursor = qdoc.find(text, 0)
            while not cursor.isNull():
                self.matches.append(QTextCursor(cursor))
                cursor = qdoc.find(text, cursor)
        self.match = 0 if self.matches else -1
        self._show_matches()

    def find_next(self, step: int = 1):
        if self.matches:
            self.match = (self.match + step) % len(self.matches)
            self._show_matches()

    def _show_matches(self):
        selections = []
        for i, cursor in enumerate(self.matches):
            selection = QTextEdit.ExtraSelection()
            selection.cursor = cursor
            fmt = QTextCharFormat()
            fmt.setBackground(_colour(helpview.FOUND_CURRENT if i == self.match
                                      else helpview.FOUND))
            selection.format = fmt
            selections.append(selection)
        self.page.setExtraSelections(selections)
        if self.matches:
            self.find_count.setText(f"{self.match + 1} of {len(self.matches)}")
            here = QTextCursor(self.matches[self.match])
            here.clearSelection()
            self.page.setTextCursor(here)
            self.page.ensureCursorVisible()
        else:
            self.find_count.setText("Not found" if self.find_entry.text() else "")

    # the text size -----------------------------------------------------------------

    def larger(self):
        self.set_text_size(helpview.larger(self.text_size))

    def smaller(self):
        self.set_text_size(helpview.smaller(self.text_size))

    def set_text_size(self, size: int):
        if size == self.text_size:
            return
        self.text_size = size
        helpview.save_text_size(self.settings_files, size)
        if self.document is not None:
            bar = self.page.verticalScrollBar()
            fraction = bar.value() / bar.maximum() if bar.maximum() else 0.0
            self._draw(self.document)
            self._lay_out()
            bar.setValue(round(fraction * bar.maximum()))
            if self.find_bar.isVisible():
                self.find_all()
        self._update_actions()

    # print ---------------------------------------------------------------------------

    def print_page(self):
        """Open the page's PDF in a PDF viewer, to print it from there."""
        if not (self.document and self.document.page):
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            launched = helpdocs.open_pdf(self.document.page)
        finally:
            QApplication.restoreOverrideCursor()
        self._follow(launched)

    def _follow(self, launched: helpdocs.Launch):
        if launched.message:
            self.say(launched.message)
            return
        started = time.monotonic()

        def poll():
            following = helpdocs.follow(launched, time.monotonic() - started)
            if following is helpdocs.WATCHING:
                QTimer.singleShot(helpdocs.POLL_MS, poll)
            elif following is not None:
                self._follow(following)

        QTimer.singleShot(helpdocs.POLL_MS, poll)

    def say(self, message: str):
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Information)
        box.setWindowTitle("Print")
        box.setText("The page could not be opened to print")
        box.setInformativeText(message)
        box.exec_()

    # topics from the designer ---------------------------------------------------------

    def listen(self, fd: int = 0):
        """Take topics, one per line, from `fd` - the designer's pipe to
        standard input - going to each as it comes. At the end of the input
        the window stops listening and stays open."""
        self._fd, self._buffer = fd, b""
        self._notifier = QSocketNotifier(fd, QSocketNotifier.Read, self)
        self._notifier.activated.connect(self._read_topics)

    def _read_topics(self, *_args):
        try:
            data = os.read(self._fd, 4096)
        except OSError:
            data = b""
        if not data:
            self._notifier.setEnabled(False)
            return
        *lines, self._buffer = (self._buffer + data).split(b"\n")
        for line in lines:
            topic = line.decode("utf-8", "replace").strip()
            if topic:
                self.receive(topic)

    def receive(self, topic: str):
        """A topic the designer asked for: go there and come to the front.
        One that does not exist is left alone."""
        try:
            self.open(topic)
        except ValueError as e:
            print(f"linuxzpl help: {e}", file=sys.stderr)
            return
        if self.isMinimized():
            self.showNormal()
        self.show()
        self.raise_()
        self.activateWindow()


def main(topic: str = helpdocs.CONTENTS, listen: Optional[bool] = None) -> int:
    app = QApplication(sys.argv[:1])
    app.setApplicationName(helpview.WINDOW_TITLE)
    window = HelpWindow()
    window.show()
    window.open(topic)
    if listen is None:
        listen = not os.isatty(0)
    if listen:
        window.listen(0)
    window.raise_()
    window.activateWindow()
    return app.exec_()
