"""The built-in help viewer's GTK window, gtkui/helpviewer.py: the same
checks tests/test_helpview.py makes of the Qt one. Needs a display - run it
under xvfb-run, as tests/run.sh does.
"""
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import _isolate  # noqa: F401

import gi  # noqa: E402
gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
from gi.repository import Gdk, Gtk  # noqa: E402

from zplcore import helpdocs, helpview  # noqa: E402
from gtkui import helpviewer as gviewer  # noqa: E402

failures = 0


def check(name, cond, extra=""):
    global failures
    print(('ok   ' if cond else 'FAIL ') + name + (f"  [{extra}]" if not cond and extra != "" else ""))
    if not cond:
        failures += 1


def pump(cond=lambda: False, seconds=3.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)
        if cond():
            return True
        time.sleep(0.01)
    return cond()


HELP = helpview.Help()
ini = Path(tempfile.mkdtemp(prefix="linuxzpl-gtk-viewer-")) / "settings.ini"
w = gviewer.HelpWindow(HELP, settings_files=[ini])
w.resize(900, 600)
w.show_all()
pump(seconds=0.3)
adjustment = w.scroller.get_vadjustment()
buffer = lambda: w.page.get_buffer()  # noqa: E731


def settle():
    """Until the page has been scrolled into place."""
    return pump(lambda: w.placed)


check("GTK: the header has Back, Forward, Search and the menu, Back and Forward greyed",
      set(w.buttons) == {"back", "forward", "search", "menu"}
      and not w.buttons["back"].get_sensitive() and not w.buttons["forward"].get_sensitive())
menu = [None if isinstance(i, Gtk.SeparatorMenuItem) else i.get_name()
        for i in w.menu.get_children()]
check("GTK: the menu holds the shared list, in order", menu == list(helpview.MENU), menu)
labels = [i.get_label() for i in w.menu.get_children() if not isinstance(i, Gtk.SeparatorMenuItem)]
check("GTK: and the shared labels",
      labels == [helpview.COMMANDS[n][0] for n in helpview.MENU if n], labels)
want = {name: [helpview.gtk_accelerator(k) for k in keys]
        for name, (_l, keys) in helpview.COMMANDS.items()}
check("GTK: every command has its keys", w.accelerators == want, w.accelerators)
check("GTK: the search field is not shown until asked for",
      not w.search_bar.get_search_mode() and not w.find_bar.get_visible())

shown = []
for pid in sorted(HELP.pages):
    w.open(pid)
    title = HELP.document(pid).title
    if (w.header.get_title() != title or w.get_title() != title
            or buffer().get_char_count() < 20):
        shown.append(pid)
check("GTK: every page opens, its title in the header bar and the window's",
      not shown and w.header.get_subtitle() == "LinuxZPL Help", shown)


def heading_y(section):
    it = buffer().get_iter_at_mark(w.anchors[section])
    line = w.page.get_line_yrange(it)[0]
    return max(line, w.page.get_iter_location(it).y - gviewer.PAGE_MARGIN // 2)


misplaced, moved = [], 0
for topic in sorted(set(helpdocs.WINDOW_HELP.values())):
    w.open(topic)
    settle()
    _page, section = helpdocs.split_topic(topic)
    if not section:
        continue
    expected = min(heading_y(section), adjustment.get_upper() - adjustment.get_page_size())
    if abs(adjustment.get_value() - expected) > 2:
        misplaced.append((topic, expected, adjustment.get_value()))
    moved += adjustment.get_value() > 0
check("GTK: each window's Help topic opens scrolled to its section",
      not misplaced and moved >= 5, (misplaced, moved))

w.open("getting-started")
settle()
check("GTK: a page with no section asked for opens at its top",
      adjustment.get_value() == 0, adjustment.get_value())


def click_text(text, button=1):
    """Click the middle of the first place `text` is, as a mouse does."""
    found = buffer().get_start_iter().forward_search(text, 0, None)
    start, end = found
    middle = buffer().get_iter_at_offset((start.get_offset() + end.get_offset()) // 2)
    rect = w.page.get_iter_location(middle)
    x, y = w.page.buffer_to_window_coords(Gtk.TextWindowType.TEXT, rect.x + 1,
                                          rect.y + rect.height // 2)
    event = Gdk.Event.new(Gdk.EventType.BUTTON_RELEASE)
    event.button.button = button
    event.button.x, event.button.y = float(x), float(y)
    event.button.window = w.page.get_window(Gtk.TextWindowType.TEXT)
    w.page.emit("button-release-event", event)
    settle()


w.history = helpview.History()
w.open("index")
settle()
click_text("Design and print a first label")
check("GTK: clicking a topic link opens that page",
      w.document.id == "getting-started" and w.buttons["back"].get_sensitive(), w.document.id)
click_text("A printer plugged in by USB")
check("GTK: a link to another page's section opens it there",
      w.history.current.topic == "printing#usb" and w.document.id == "printing"
      and abs(adjustment.get_value() - min(heading_y("usb"), adjustment.get_upper()
                                           - adjustment.get_page_size())) <= 2,
      w.history.current)
adjustment.set_value((adjustment.get_upper() - adjustment.get_page_size()) // 2)
pump(seconds=0.1)
left_at = w._scroll()
click_text("Printing")  # the trail's link to its guide's section
check("GTK: the trail's links lead back to the contents",
      w.history.current.topic == "index#printing", w.history.current)
w.handlers["back"]()
settle()
check("GTK: Back returns to the page before, scrolled where it was left",
      w.document.id == "printing" and w._scroll() == left_at
      and w.buttons["forward"].get_sensitive(), (w.document.id, w._scroll(), left_at))
key, mods = Gtk.accelerator_parse("<Alt>Right")
Gtk.accel_groups_activate(w, key, mods)
settle()
check("GTK: and Forward, by its key, goes on again",
      w.history.current.topic == "index#printing" and not w.buttons["forward"].get_sensitive())
key, mods = Gtk.accelerator_parse("<Alt>Left")
Gtk.accel_groups_activate(w, key, mods)
settle()
check("GTK: Back's key works too", w.document.id == "printing")
w.menu_items["contents"].activate()
settle()
check("GTK: Contents opens the contents at its top",
      w.document.id == "index" and adjustment.get_value() == 0)
key, mods = Gtk.accelerator_parse("F1")
Gtk.accel_groups_activate(w, key, mods)
settle()
check("GTK: F1 opens the viewer's own page, or the contents until there is one",
      w.document.id == HELP.viewer_topic())

shown_uris = []
real_show_uri = gviewer.Gtk.show_uri_on_window
gviewer.Gtk.show_uri_on_window = lambda parent, uri, when: shown_uris.append(uri)
try:
    w.follow(("href", "https://www.zebra.com/"))
finally:
    gviewer.Gtk.show_uri_on_window = real_show_uri
check("GTK: a link out of the help goes to the desktop's browser",
      shown_uris == ["https://www.zebra.com/"] and w.document.id == HELP.viewer_topic())

key, mods = Gtk.accelerator_parse("<Control>s")
Gtk.accel_groups_activate(w, key, mods)
pump(seconds=0.2)
check("GTK: Search's key shows the search bar, its button pressed",
      w.search_bar.get_search_mode() and w.buttons["search"].get_active())
w.search_entry.set_text("  firmware  ")
w.search_entry.emit("activate")
settle()
check("GTK: Enter shows the results as a page of their own",
      w.document.id == "search:firmware"
      and w.header.get_title() == "Search results for “firmware”"
      and not w.menu_items["print"].get_sensitive(), w.document.id)
click_text("Update a printer's firmware")
check("GTK: a result opens its page, and Print can print it",
      w.document.id == "firmware-update" and w.menu_items["print"].get_sensitive())
w.handlers["back"]()
settle()
check("GTK: Back returns to the results", w.document.id == "search:firmware")
w.buttons["search"].set_active(False)
pump(seconds=0.2)
check("GTK: the search bar hides with its button",
      not w.search_bar.get_search_mode())

w.open("printing")
settle()
key, mods = Gtk.accelerator_parse("<Control>f")
Gtk.accel_groups_activate(w, key, mods)
w.find_entry.set_text("PRINTER")
pump(lambda: len(w.matches) > 3, seconds=2)
count = len(w.matches)
found_tag = buffer().get_tag_table().lookup("found")
marked = sum(1 for a, _b in w.matches
             if buffer().get_iter_at_offset(a).has_tag(found_tag))
check("GTK: Find marks every place the words are, in any letter case",
      w.find_bar.get_visible() and count > 3 and marked == count
      and w.find_count.get_text() == f"1 of {count}", (count, w.find_count.get_text()))
w.find_next(1)
check("GTK: Next goes to the next", w.find_count.get_text() == f"2 of {count}")
w.find_next(-1)
w.find_next(-1)
check("GTK: Previous from the first wraps to the last",
      w.find_count.get_text() == f"{count} of {count}")
w.open("fonts")
settle()
check("GTK: on another page Find looks again",
      w.find_count.get_text().startswith("1 of") and len(w.matches) != count)
w.find_entry.set_text("zzzznotaword")
pump(lambda: w.find_count.get_text() == "Not found", seconds=2)
check("GTK: words not on the page say so", w.matches == [])
w.find_entry.set_text("Ctrl")
w.find_all()
w.open("keyboard-shortcuts")
settle()
check("GTK: Find reaches the words in a table, which is text on the page like the rest",
      len(w.matches) > 20, len(w.matches))
w.hide_find()
start = buffer().get_start_iter()
check("GTK: closing Find clears its marks",
      not w.find_bar.get_visible()
      and not start.forward_to_tag_toggle(buffer().get_tag_table().lookup("found")))


def body_points():
    """The size of the first paragraph of plain words on the page."""
    for block in w.document.blocks:
        if type(block) is helpview.Para:
            words = helpview.plain(block.runs)[:20]
            start, _end = buffer().get_start_iter().forward_search(words, 0, None)
            return max(t.props.size_points for t in start.get_tags() if t.props.size_set)


w.open("printing")
settle()
before = body_points()
w.menu_items["larger"].activate()
check("GTK: Larger Text sets the page larger, and remembers it",
      w.text_size == 110 and helpview.load_text_size([ini]) == 110
      and abs(body_points() - before * 1.1) < 0.05, (before, body_points()))
again = gviewer.HelpWindow(HELP, settings_files=[ini])
check("GTK: a new window opens at the size remembered", again.text_size == 110)
again.destroy()
for _ in range(12):
    w.smaller()
check("GTK: Smaller Text stops at the smallest, and greys itself there",
      w.text_size == helpview.TEXT_SIZES[0] and not w.menu_items["smaller"].get_sensitive())
w.set_text_size(100)

printed, said = [], []
real_open_pdf = helpdocs.open_pdf
helpdocs.open_pdf = lambda topic, *a, **k: printed.append(topic) or helpdocs.Launch(
    None, "No PDF viewer was found to print the page from.", topic, printing=True)
w.say = said.append
try:
    w.open("fonts#manager")
    w.menu_items["print"].activate()
finally:
    helpdocs.open_pdf = real_open_pdf
check("GTK: Print opens the page's PDF, and says why if it cannot",
      printed == ["fonts"] and said == ["No PDF viewer was found to print the page from."],
      (printed, said))

read_end, write_end = os.pipe()
w.listen(read_end)
os.write(write_end, b"printing#usb\nnowhere\nfon")
check("GTK: a topic from the designer opens in the same window",
      pump(lambda: w.history.current.topic == "printing#usb"), w.history.current)
os.write(write_end, b"ts#manager\n")
check("GTK: one that does not exist is passed over, and one sent in pieces arrives whole",
      pump(lambda: w.history.current.topic == "fonts#manager")
      and [e.topic for e in w.history.entries[-2:]] == ["printing#usb", "fonts#manager"],
      w.history.entries[-3:])
os.close(write_end)
check("GTK: when the designer's end closes, the window stops listening and stays",
      pump(lambda: not w.listening) and w.get_visible())
os.close(read_end)
w.destroy()

run = subprocess.run([sys.executable, str(helpdocs.PAGES.parent.parent / "linuxzpl.py"),
                      "--gtk", "--show-help", "nowhere"],
                     capture_output=True, text=True, timeout=60)
check("GTK: --show-help with a page that does not exist says so and exits 1",
      run.returncode == 1 and "no help page 'nowhere'" in run.stderr, run.stderr)

print("ALL GTK HELP VIEWER CHECKS PASSED" if not failures else f"{failures} FAILED")
sys.exit(1 if failures else 0)
