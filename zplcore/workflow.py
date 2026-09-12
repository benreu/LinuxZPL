"""
The decisions that determine whether a label prints correctly.

Each one takes an `ask` callback, so the prompt itself stays native to its
toolkit while the rule about when to ask and what to do with the answer is
shared. These are exactly the places where two frontends drifting apart would
not raise an error - it would print the wrong label - so they live here rather
than being written twice.
"""

import os.path

from . import fonts

# Distinguishes "the printer's resolution changed under an open document" from
# "a file was opened that recorded no resolution", which are answered
# differently: the first knows the document's dpi, the second has to assume.
_FROM_DOCUMENT = object()


def reconcile_dpi(document, printer_dpi, ask, file_dpi=_FROM_DOCUMENT):
    """Settle a design against a printer resolution it was not drawn for.

    Dots only mean a physical size once a resolution is fixed: 1200 dots is 4in
    at 300 dpi and 5.9in at 203. So whenever the two disagree the user has to
    choose, and both entry points - opening a file, and changing the printer -
    ask the same question.

    `ask(old_dpi, printer_dpi, assumed, w_in, h_in)` returns 'rescale', 'keep'
    or 'cancel'. Returns a note for the status bar when it rescaled, else None.
    """
    if file_dpi is _FROM_DOCUMENT:
        old, assumed = document.dpi, False
    else:
        # A file with no recorded resolution is assumed to be 203, the
        # resolution of most ZPL in the wild. Adopting the printer's setting
        # instead would stamp that guess into the file on the next save,
        # mislabelling a 203 dpi label as whatever printer happened to open it.
        assumed = not file_dpi or file_dpi <= 0
        old = fonts.DEFAULT_DPI if assumed else file_dpi

    if old == printer_dpi or not document.elements:
        document.dpi = printer_dpi
        return None

    # What keeping the dots would physically measure on this printer
    w_in = document.label_width / printer_dpi
    h_in = document.label_height / printer_dpi

    answer = ask(old, printer_dpi, assumed, w_in, h_in)
    if answer == 'rescale':
        document.rescale(printer_dpi / old)
        document.dpi = printer_dpi
        return f"rescaled from {old} to {printer_dpi} dpi"

    # Keep Dots and Cancel both leave the dots alone; the document still
    # belongs to this printer now, so it is stamped either way.
    document.dpi = printer_dpi
    return None


def confirm_printer_fonts(document, address, port, ask, on_progress=None):
    """Check the label's fonts are on the printer. False cancels printing.

    `ask(text, detail, uploadable)` returns 'upload', 'print' or 'cancel'.
    `on_progress(message)` reports each upload, if given.
    """
    sources = document.font_sources()
    if not sources:
        return True, None  # nothing but built-in fonts, nothing to check

    installed = fonts.query_printer_fonts(address, port)
    if installed is None:
        # Not the same as "the printer has no fonts": it could not be asked,
        # and the two lead to different prompts.
        answer = ask("The printer could not be asked which fonts it has.",
                     "It may be unreachable, or may not support font queries.\n"
                     "Printing anyway may fall back to a substitute font.",
                     {})
        return _act_on_font_answer(answer, {}, address, port, on_progress)

    missing = {n: p for n, p in sources.items() if n.upper() not in installed}
    if not missing:
        return True, None

    uploadable = {n: p for n, p in missing.items() if p}
    lines = [f"  {fonts.printer_font_path(n)}" +
             ("" if missing[n] else "   (source file unknown)")
             for n in sorted(missing)]
    answer = ask("Fonts used by this label are not on the printer.",
                 "\n".join(lines) + "\n\nMissing fonts print in a substitute typeface.",
                 uploadable)
    return _act_on_font_answer(answer, uploadable, address, port, on_progress)


def _act_on_font_answer(answer, uploadable, address, port, on_progress):
    """Carry out the choice. Returns (proceed, error message or None)."""
    if answer == 'upload':
        for name, path in sorted(uploadable.items()):
            if on_progress:
                on_progress(f"Uploading {fonts.printer_font_path(name)}...")
            try:
                fonts.upload_font(address, port, path, name)
            except Exception as e:
                # A failed upload aborts: printing now would use a substitute.
                return False, f"Upload of {name} failed: {e}"
        return True, None
    return answer == 'print', None


def unsaved_changes_gate(is_dirty, ask, save):
    """Ask what to do with unsaved work. True means it is safe to continue.

    `ask()` returns 'save', 'discard' or 'cancel'; `save()` returns whether a
    file was actually written. A cancelled or failed save must abort the
    operation rather than carry on and lose the work.
    """
    if not is_dirty:
        return True
    answer = ask()
    if answer == 'discard':
        return True
    if answer == 'save':
        return bool(save())
    return False


# The name the program goes by, shown whenever no file is open.
APP_TITLE = 'LinuxZPL'


def window_title(filepath) -> str:
    """What the title bar says: the file being edited, or the program's name.

    Both frontends ask here. Left to themselves they had drifted to "ZPL
    Viewer" and "LinuxZPL (Qt)" - two names for one program, neither of which
    said which of the user's labels was on screen.
    """
    return os.path.basename(filepath) if filepath else APP_TITLE


ZPL_SUFFIX = '.zpl'


def save_filename(name: str) -> str:
    """The path a save actually writes, given the one the chooser handed back.

    A chooser returns exactly what was typed, so "label" would be written with
    no extension and then be invisible to the *.zpl filter that has to find it
    again. Only a missing extension is filled in: someone who typed .txt meant
    it, and .ZPL is already one.

    Callers must run this *before* deciding whether the file exists. Appending
    afterwards would mean the toolkit confirmed one path and the save clobbered
    a different one.
    """
    return name if os.path.splitext(name)[1] else name + ZPL_SUFFIX


def confirm_save_path(chosen, ask, exists=os.path.exists):
    """The path to write, or None to go back to the chooser.

    `ask(path)` asks whether to replace an existing file. It is only asked when
    a suffix was added, because then the file being overwritten is not the one
    the chooser already confirmed - it never showed this name to anyone.
    """
    path = save_filename(chosen)
    if path == chosen or not exists(path):
        return path
    return path if ask(path) else None


# Commands whose effect the model actually keeps: it turns them into elements,
# or they carry nothing of their own. Anything else in a file changes what
# prints and will not survive a save, because the model has nowhere to put it.
#
# This answers "is it safe to say nothing?". parser.STRUCTURAL answers the
# different question "can the parser skip it without choking?" - ^CI could be
# skipped but not kept, and listing it here said otherwise, so a file's
# encoding was dropped without a word.
# ^GF is absent deliberately: whether one can be read depends on how its data
# is encoded, so unsupported_commands() asks zplcore.graphics per field.
# ^FN, ^FV, ^DF and ^XF are the stored-format family, modelled since a
# template's variable fields became real placeholders rather than either
# vanishing or being handed an invented value.
MODELLED = {'^FO', '^FT', '^FD', '^FS', '^BY', '^BC', '^GB', '^FB',
            '^PW', '^LL', '^XA', '^XZ', '^FX', '^CF',
            '^FN', '^FV', '^DF', '^XF'}


def unsupported_commands(zpl_content: str) -> list:
    """Commands in the file that a save would drop, in the order they appear.

    The designer rebuilds a file from its model rather than editing the text,
    so anything the model cannot hold is gone the moment the user saves. That
    is worth saying out loud, rather than letting someone discover it on a
    printed label.
    """
    from .parser import tokenise

    from . import graphics

    seen = []
    for command, params in tokenise(zpl_content):
        if command.startswith('^A'):        # every font is modelled
            continue
        if command == '^GF':
            # Only some spellings of ^GF can be read. One that cannot has to be
            # named, or a label loses an image and is told nothing - which is
            # what a blanket entry in MODELLED did.
            name = graphics.unsupported(params)
            if name and name not in seen:
                seen.append(name)
            continue
        if command in MODELLED or command in seen:
            continue
        seen.append(command)
    return seen


def warn_unsupported(zpl_content: str, notify) -> list:
    """Tell the user what opening this file has quietly left behind.

    `notify(commands)` shows it however the toolkit shows things. Returns the
    commands so a caller can log or test them.
    """
    dropped = unsupported_commands(zpl_content)
    if dropped:
        notify(dropped)
    return dropped
