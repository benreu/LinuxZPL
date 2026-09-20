"""
A busy row - Gtk.Spinner plus Cancel - for the dialogs that talk to a
printer, and the one way those dialogs run a network call off the GTK main
loop. The GTK twin of qtui/busy.py, with the same API and the same
reasoning; see there for the why.

Completion and progress cross back to the main loop through GLib.idle_add,
which is the one thread-safe door into GTK. Idle callbacks still run inside
a dialog's nested run() loop, so this works from the modal printer dialogs
as well as from the main window.
"""

import threading
from typing import Callable, Optional, Sequence

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import GLib, Gtk

from zplcore import printer_io


class BusyBar(Gtk.Box):
    """Hidden until run() is called; visible, spinning and cancellable
    until the call it started comes back.

    `blocked` widgets are made insensitive while a call is out and restored
    to whatever sensitivity they had before, not simply made sensitive - so
    a Delete withheld for a read-only object stays withheld. `message`, if
    given, is where report() lands. set_no_show_all keeps the row hidden
    through the dialogs' own show_all().
    """

    def __init__(self, blocked: Sequence[Gtk.Widget] = (),
                 message: Optional[Callable] = None):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self._blocked = list(blocked)
        self._message = message
        self._token: Optional[printer_io.CancelToken] = None
        self._was_sensitive = []
        self._dead = False
        self._on_done = None

        self._spinner = Gtk.Spinner()
        self.pack_start(self._spinner, False, False, 0)
        self._cancel_btn = Gtk.Button(label="Cancel")
        self._cancel_btn.connect("clicked", lambda _b: self.cancel())
        self.pack_start(self._cancel_btn, False, False, 0)
        self._spinner.show()
        self._cancel_btn.show()
        self.set_no_show_all(True)
        self.hide()

    @property
    def running(self) -> bool:
        return self._token is not None

    def run(self, fn: Callable, on_done: Callable) -> None:
        """Run `fn(cancel)` on a worker thread; `on_done(result, error)` on
        the main loop afterwards. `error` is None on success, the exception
        otherwise - printer_io.Cancelled when Cancel was pressed, which
        callers treat as "nothing happened" rather than as a failure. A
        second run() while one is in flight is ignored.
        """
        if self._token is not None or self._dead:
            return
        self._token = printer_io.CancelToken()
        self._was_sensitive = [w.get_sensitive() for w in self._blocked]
        for w in self._blocked:
            w.set_sensitive(False)
        self._cancel_btn.set_sensitive(True)
        self._spinner.start()
        self.show()
        self._on_done = on_done
        token = self._token

        def work():
            try:
                result, error = fn(token), None
            except Exception as e:
                result, error = None, e
            GLib.idle_add(self._finish, result, error)

        threading.Thread(target=work, daemon=True).start()

    def report(self, text: str) -> None:
        """Thread-safe progress text, for a `fn` with more than one step."""
        GLib.idle_add(self._show_message, text)

    def cancel(self) -> None:
        if self._token is not None:
            self._cancel_btn.set_sensitive(False)
            self._token.cancel()

    def abandon(self) -> None:
        """Cancel whatever is running and drop its result when it lands -
        for a dialog being destroyed while a call is still out."""
        self._dead = True
        self.cancel()

    def _finish(self, result, error) -> bool:
        if not self._dead:
            self._spinner.stop()
            self.hide()
            for w, sensitive in zip(self._blocked, self._was_sensitive):
                w.set_sensitive(sensitive)
            self._token = None
            on_done, self._on_done = self._on_done, None
            on_done(result, error)
        return False  # one-shot idle callback

    def _show_message(self, text: str) -> bool:
        if self._message is not None and not self._dead:
            self._message(text)
        return False
