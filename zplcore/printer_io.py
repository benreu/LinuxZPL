"""
Raw socket I/O to a printer, shared by every module that talks to one.

One request/optional-reply primitive, used the same way whether the payload
is a font (zplcore/fonts.py), a graphic (zplcore/graphic_store.py), or a
label itself: connect, send, and - for the commands that answer, like ^HW's
directory listing or ^HG's image data - read back whatever comes over the
same socket.

Every call here blocks, so a frontend runs it off its GUI thread and holds
a CancelToken for the duration: that is the only way to abandon a request
early, since a printer that stays silent (a .TTF it refuses to hand back,
say) leaves the worker stuck inside one recv() until the timeout expires.
"""

import socket
import threading
import time
from typing import Optional


class Cancelled(Exception):
    """A request abandoned by CancelToken.cancel() while in flight.

    Deliberately not an OSError: the query functions in fonts.py,
    graphic_store.py and printer_objects.py all turn OSError into "None,
    the printer could not be asked", and a cancel has to propagate through
    them to the caller who asked for it rather than be mistaken for an
    unreachable printer.
    """


class CancelToken:
    """A handle a GUI thread keeps so it can abort a send() running on another.

    send() attaches its socket here as soon as it has one; cancel() then
    shuts that socket down from whichever thread calls it. shutdown() is
    what actually wakes a recv() or sendall() blocked on the worker thread -
    on Linux, close() from another thread does not - and it is all cancel()
    does: closing is left to send()'s own finally, so two threads never
    race over one file descriptor. A connect() still in progress is the one
    thing shutdown() cannot reach (the socket is not connected yet), so a
    cancel during that phase takes effect when the connect timeout expires
    - bounded by the same few seconds the call would have taken anyway.

    One token per operation, not per dialog: once cancelled it stays
    cancelled, and a send() given an already-cancelled token raises before
    opening a socket at all.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._sock: Optional[socket.socket] = None
        self.cancelled = False

    def cancel(self) -> None:
        with self._lock:
            self.cancelled = True
            sock = self._sock
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def _attach(self, sock: socket.socket) -> None:
        with self._lock:
            if self.cancelled:
                raise Cancelled("Cancelled before connecting")
            self._sock = sock

    def _detach(self) -> None:
        with self._lock:
            self._sock = None


def send(address: str, port: int, payload: bytes, timeout: float,
        read_reply: bool = False,
        cancel: Optional[CancelToken] = None) -> bytes:
    """Send a payload to the printer, optionally reading whatever it replies.

    Raises Cancelled whenever `cancel` was cancelled on the way out -
    however that surfaced: the shutdown may show up as an OSError from the
    blocked call, as an EOF that ends the reply early with partial or empty
    data, or not at all if it landed between two calls. A cancelled request
    never returns data.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    if cancel is not None:
        cancel._attach(sock)
    try:
        reply = _exchange(sock, address, port, payload, timeout, read_reply)
    except OSError:
        if cancel is not None and cancel.cancelled:
            raise Cancelled(f"Cancelled while talking to {address}:{port}") from None
        raise
    finally:
        if cancel is not None:
            cancel._detach()
        sock.close()
    if cancel is not None and cancel.cancelled:
        raise Cancelled(f"Cancelled while talking to {address}:{port}")
    return reply


def _exchange(sock, address, port, payload, timeout, read_reply) -> bytes:
    sock.connect((address, port))
    sock.sendall(payload)
    if not read_reply:
        return b''
    chunks = []
    deadline = time.monotonic() + timeout
    while True:
        try:
            chunk = sock.recv(4096)
        except (socket.timeout, TimeoutError):
            break
        if not chunk:
            break
        chunks.append(chunk)
        # Once the printer starts talking it sends the rest promptly, so
        # drop to a short timeout rather than waiting out the full one.
        sock.settimeout(0.5)
        if time.monotonic() > deadline:
            break
    return b''.join(chunks)


def send_command(address: str, port: int, command: str, timeout: float = 5,
                 cancel: Optional[CancelToken] = None) -> str:
    """Send raw text to the printer and return whatever it writes back.

    No wrapping, no interpretation - the command is sent exactly as given
    (encoded UTF-8, matching how the Print action already encodes label
    text), so both immediate commands like ~HS and full ^XA...^XZ formats
    work unchanged. The reply, if any, is decoded the same way; a reply
    that is not valid UTF-8 (e.g. a binary object) is decoded with
    replacement characters rather than raising, since this is a diagnostic
    view, not a retrieval path - Objects -> Retrieve already exists for
    getting bytes back losslessly.
    """
    reply = send(address, port, command.encode('utf-8'), timeout,
                 read_reply=True, cancel=cancel)
    return reply.decode('utf-8', errors='replace')
