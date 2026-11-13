"""
font_names.ini: printer font names fixed by hand rather than truncated from
the filename, read from the Extra Font Folder.

Pure zplcore - no toolkit import, no display needed.
"""

import os, shutil, sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from zplcore import fonts as zpl_fonts
from zplcore import parser as zpl_parser
from zplcore.model import Document, TextElement

fails = []
def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + ((" -- " + str(extra)) if extra else ""))
    if not cond: fails.append(name)

DEJAVU = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
DEJAVU_BOLD = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
DEJAVU_MONO = Path("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf")

tmp = Path(tempfile.mkdtemp(prefix='linuxzpl-names-'))
folder = tmp / 'Fonts'
folder.mkdir()
regular = folder / 'DejaVuSans.ttf'
bold = folder / 'DejaVuSans-Bold.ttf'
shutil.copy(DEJAVU, regular)
shutil.copy(DEJAVU_BOLD, bold)


def write_map(text):
    (folder / zpl_fonts.FONT_NAME_MAP_FILE).write_text(text)
    zpl_fonts.font_name_map(refresh=True)


try:
    # --- no folder, no map: everything is as it always was -----------------
    zpl_fonts.set_extra_font_dir('')
    check("no Extra Font Folder: the map is empty",
          zpl_fonts.font_name_map().source is None
          and not zpl_fonts.font_name_map().names)
    check("no map: a name is still truncated from the filename",
          zpl_fonts.printer_font_name(str(bold)) == 'DEJAVUSA',
          zpl_fonts.printer_font_name(str(bold)))

    # --- a map gives each face its own name, both ways ----------------------
    write_map("# fleet fonts\n"
              "DJVREG = DejaVuSans.ttf\n"
              "djvbold = DejaVuSans-Bold.ttf   # lower case is upper-cased\n"
              f"DJVMONO = {DEJAVU_MONO}\n")
    zpl_fonts.set_extra_font_dir(str(folder))
    name_map = zpl_fonts.font_name_map()
    check("the map is read from the Extra Font Folder",
          name_map.source == str(folder / zpl_fonts.FONT_NAME_MAP_FILE),
          name_map.source)
    check("relative, lower-case and absolute entries all load",
          set(name_map.names) == {'DJVREG', 'DJVBOLD', 'DJVMONO'}
          and not name_map.problems, (name_map.names, name_map.problems))
    check("forward: a mapped file gets its mapped name",
          zpl_fonts.printer_font_name(str(bold)) == 'DJVBOLD',
          zpl_fonts.printer_font_name(str(bold)))
    check("forward: the mapped name ignores `taken`",
          zpl_fonts.printer_font_name(str(bold), taken={'DJVBOLD'}) == 'DJVBOLD')
    check("forward: an absolute entry outside the folder works too",
          zpl_fonts.printer_font_name(str(DEJAVU_MONO)) == 'DJVMONO')
    check("reverse: a mapped name gives its file, Bold not Regular",
          zpl_fonts.file_for_printer_name('djvbold') == os.path.realpath(bold),
          zpl_fonts.file_for_printer_name('djvbold'))
    check("a name not in the map still derives as before",
          zpl_fonts.file_for_printer_name('DEJAVUSA') is not None)

    # --- a mapped name is reserved -------------------------------------------
    write_map(f"DEJAVUSA = DejaVuSans-Bold.ttf\n")
    check("reverse: a mapped name beats every file that truncates to it",
          zpl_fonts.file_for_printer_name('DEJAVUSA') == os.path.realpath(bold),
          zpl_fonts.file_for_printer_name('DEJAVUSA'))
    derived = zpl_fonts.printer_font_name(str(DEJAVU))
    check("forward: an unmapped file cannot derive onto a mapped name",
          derived != 'DEJAVUSA' and len(derived) <= 8, derived)

    # --- a mapped file that has gone does not fall back --------------------
    gone = folder / 'Gone.ttf'
    shutil.copy(DEJAVU, gone)
    write_map("DEJAVUSA = Gone.ttf\n")
    gone.unlink()
    check("a mapped name whose file has gone gives None, not a lookalike",
          zpl_fonts.file_for_printer_name('DEJAVUSA') is None,
          zpl_fonts.file_for_printer_name('DEJAVUSA'))

    # --- bad lines are skipped and reported by line number -----------------
    write_map("[names]\n"                          # 1: a header, ignored
              "GOOD = DejaVuSans.ttf\n"            # 2
              "TOOLONGNAME = DejaVuSans.ttf\n"     # 3: 11 characters
              "BAD NAME = DejaVuSans.ttf\n"        # 4: a space
              "MISSING = Nowhere.ttf\n"            # 5: no such file
              "GOOD = DejaVuSans-Bold.ttf\n"       # 6: the name again
              "AGAIN = DejaVuSans.ttf\n"           # 7: the file again
              "NOEQUALS\n"                         # 8
              "NOTTF = font_names.ini\n")          # 9: not a .ttf
    name_map = zpl_fonts.font_name_map()
    check("only the first good entry survives",
          name_map.names == {'GOOD': os.path.realpath(regular)}, name_map.names)
    for number in (3, 4, 5, 6, 7, 8, 9):
        check(f"line {number}'s problem is reported with its number",
              any(p.startswith(f"line {number}:") for p in name_map.problems),
              name_map.problems)
    check("the header line is not a problem",
          not any(p.startswith("line 1:") for p in name_map.problems))
    report = zpl_fonts.name_map_report()
    check("the Local Fonts report lists the names and the skipped lines",
          any(line.strip().startswith("GOOD") for line in report)
          and any("line 5:" in line for line in report), report)

    # --- changing the folder drops the cached map --------------------------
    zpl_fonts.set_extra_font_dir(str(tmp))
    check("set_extra_font_dir drops the cached map",
          zpl_fonts.font_name_map().source is None)
    zpl_fonts.set_extra_font_dir(str(folder))
    check("and the folder back brings it back",
          'GOOD' in zpl_fonts.font_name_map().names)

    # --- a loaded label finds the mapped file, and saves the path unchanged
    write_map("LIBSANNB = DejaVuSans-Bold.ttf\n")
    loaded = zpl_parser.parse_zpl(
        '^XA^FO50,50^A@N,30,20,E:LIBSANNB.TTF^FDx^FS^XZ')[0]
    element = loaded.elements[0]
    check("parsing a mapped ^A@ resolves its local file",
          element.font_path == os.path.realpath(bold), element.font_path)
    check("and the ^A@ path is written back as the file gave it",
          '^A@N,30,20,E:LIBSANNB.TTF' in loaded.to_zpl(), loaded.to_zpl())

    # --- one font file, one name, within a label ---------------------------
    zpl_fonts.set_extra_font_dir('')
    document = Document()
    first = TextElement(0, 0, 'a')
    second = TextElement(0, 40, 'b')
    document.elements += [first, second]
    name = zpl_fonts.printer_font_name(
        str(DEJAVU), taken=document.printer_font_names(exclude=first,
                                                        same_file=str(DEJAVU)))
    document.set_element_font(first, str(DEJAVU), 'DejaVu Sans', name)
    again = zpl_fonts.printer_font_name(
        str(DEJAVU), taken=document.printer_font_names(exclude=second,
                                                        same_file=str(DEJAVU)))
    check("the same file picked twice gets the same name, not a suffix",
          name == again == 'DEJAVUSA', (name, again))
    other = zpl_fonts.printer_font_name(
        str(DEJAVU_BOLD), taken=document.printer_font_names(
            exclude=second, same_file=str(DEJAVU_BOLD)))
    check("a different file that truncates the same still gets a suffix",
          other != 'DEJAVUSA', other)
finally:
    zpl_fonts.set_extra_font_dir('')
    shutil.rmtree(tmp, ignore_errors=True)

print()
print(f"{len(fails)} failure(s)" if fails else "all passed")
sys.exit(1 if fails else 0)
