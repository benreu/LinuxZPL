"""The help pages in help/C, and zplcore/helpdocs.py starting a viewer for them.

No viewer is really started: shutil.which and subprocess.Popen are replaced in
helpdocs for each check, so the suite says what would be run, on any machine,
whether or not it has Yelp.
"""
import os
import subprocess
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import _isolate  # noqa: F401

from zplcore import helpdocs

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

real_which, real_popen = helpdocs.shutil.which, helpdocs.subprocess.Popen
started = []


class FakePopen:
    def __init__(self, argv, **kwargs):
        started.append((argv, kwargs))


def failing_popen(argv, **kwargs):
    raise FileNotFoundError(argv[0])


def run(topic=None, have=(), popen=FakePopen):
    """launch(topic) on a machine whose PATH has only the programs in `have`."""
    del started[:]
    helpdocs.shutil.which = lambda name: f"/usr/bin/{name}" if name in have else None
    helpdocs.subprocess.Popen = popen
    try:
        return helpdocs.launch() if topic is None else helpdocs.launch(topic)
    finally:
        helpdocs.shutil.which, helpdocs.subprocess.Popen = real_which, real_popen


launched = run(have=("yelp",))
check("with Yelp, the contents opens Yelp on the help folder",
      [a for a, _k in started] == [["/usr/bin/yelp", str(helpdocs.PAGES)]]
      and launched.message is None and launched.process is not None, started)
check("the viewer is detached, with nothing tied to this process's terminal",
      started and started[0][1].get("start_new_session") is True
      and all(started[0][1].get(s) is subprocess.DEVNULL
              for s in ("stdin", "stdout", "stderr")), started)

launched = run("firmware-update", have=("yelp",))
check("with Yelp, a topic opens Yelp on that page's file",
      [a for a, _k in started]
      == [["/usr/bin/yelp", str(helpdocs.PAGES / "firmware-update.page")]]
      and launched.message is None, started)

launched = run(have=())
check("with no viewer, nothing is started", started == [] and launched.process is None)
check("and the message says to install Yelp and where the pages are",
      launched.message and "yelp" in launched.message
      and str(helpdocs.PAGES) in launched.message, launched.message)

launched = run(have=("yelp",), popen=failing_popen)
check("a Yelp that cannot be started gives the message too",
      launched.process is None and launched.message
      and "yelp" in launched.message, launched)

try:
    run("no-such-topic", have=("yelp",))
    check("asking for a page that does not exist is an error", False)
except ValueError:
    check("asking for a page that does not exist is an error", started == [])

print("ALL HELP CHECKS PASSED" if not failures else f"{failures} FAILED")
sys.exit(1 if failures else 0)
