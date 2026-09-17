"""
A busy row - indeterminate bar plus Cancel - for the dialogs that talk to a
printer, and the one way those dialogs run a network call off the GUI
thread.

Every zplcore printer call blocks for up to its timeout, which used to
freeze the whole window with nothing to look at and no way out. BusyBar.run
moves the call to a worker thread, shows the bar and a working Cancel while
it is out, disables the buttons that would start another one, and hands the
result back on the GUI thread - so the handler code that used to sit after
the call now sits in a `done(result, error)` callback and is otherwise
unchanged.

The worker is a plain threading.Thread, not a QThread: it emits a Signal on
a QObject that lives on the GUI thread, and Qt sees the foreign thread and
queues the delivery - which is all a QThread would have bought here, at the
cost of a lifecycle to manage.
"""

import threading
from typing import Callable, Optional, Sequence

from PySide2.QtCore import QObject, Signal
from PySide2.QtWidgets import QHBoxLayout, QProgressBar, QPushButton, QWidget

from zplcore import printer_io


class _Relay(QObject):
    done = Signal(object, object)
    progress = Signal(str)


class BusyBar(QWidget):
    """Hidden until run() is called; visible, animating and cancellable
    until the call it started comes back.

    `blocked` are the widgets (or QActions) that must not be triggered while
    a call is in flight - the dialog's own Store/Retrieve/Delete/Refresh.
    They go back to whatever enabled state they had before, not simply to
    enabled, so a Delete withheld for a read-only object stays withheld.
    `message`, if given, is where report() lands - a status label's setText,
    or the main window's update_status.
    """

    def __init__(self, blocked: Sequence = (), message: Optional[Callable] = None,
                 parent=None):
        super().__init__(parent)
        self._blocked = list(blocked)
        self._message = message
        self._token: Optional[printer_io.CancelToken] = None
        self._was_enabled = []
        self._dead = False
        self._on_done = None

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._bar = QProgressBar()
        self._bar.setRange(0, 0)
        self._bar.setTextVisible(False)
        self._bar.setFixedWidth(90)
        layout.addWidget(self._bar)
        self._cancel_btn = QPushButton("Cancel")
        layout.addWidget(self._cancel_btn)
        self._cancel_btn.clicked.connect(self.cancel)

        self._relay = _Relay(self)
        self._relay.done.connect(self._finish)
        self._relay.progress.connect(self._show_message)
        self.hide()

    @property
    def running(self) -> bool:
        return self._token is not None

    def run(self, fn: Callable, on_done: Callable) -> None:
        """Run `fn(cancel)` on a worker thread; `on_done(result, error)` on
        the GUI thread afterwards. `error` is None on success, the exception
        otherwise - printer_io.Cancelled when Cancel was pressed, which
        callers treat as "nothing happened" rather than as a failure. A
        second run() while one is in flight is ignored.
        """
        if self._token is not None or self._dead:
            return
        self._token = printer_io.CancelToken()
        self._was_enabled = [w.isEnabled() for w in self._blocked]
        for w in self._blocked:
            w.setEnabled(False)
        self._cancel_btn.setEnabled(True)
        self.show()
        self._on_done = on_done
        token, relay = self._token, self._relay

        def work():
            try:
                result, error = fn(token), None
            except Exception as e:
                result, error = None, e
            try:
                relay.done.emit(result, error)
            except RuntimeError:
                # The dialog was deleted (WA_DeleteOnClose) while the call
                # was out; nobody is left to tell.
                pass

        threading.Thread(target=work, daemon=True).start()

    def report(self, text: str) -> None:
        """Thread-safe progress text, for a `fn` with more than one step."""
        try:
            self._relay.progress.emit(text)
        except RuntimeError:
            pass

    def cancel(self) -> None:
        if self._token is not None:
            self._cancel_btn.setEnabled(False)
            self._token.cancel()

    def abandon(self) -> None:
        """Cancel whatever is running and drop its result when it lands -
        for a dialog closing while a call is still out."""
        self._dead = True
        self.cancel()

    def _finish(self, result, error) -> None:
        if self._dead:
            return
        self.hide()
        for w, enabled in zip(self._blocked, self._was_enabled):
            w.setEnabled(enabled)
        self._token = None
        on_done, self._on_done = self._on_done, None
        on_done(result, error)

    def _show_message(self, text: str) -> None:
        if self._message is not None and not self._dead:
            self._message(text)
