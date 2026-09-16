"""
Raw socket I/O to a printer, shared by every module that talks to one.

One request/optional-reply primitive, used the same way whether the payload
is a font (zplcore/fonts.py), a graphic (zplcore/graphic_store.py), or a
label itself: connect, send, and - for the commands that answer, like ^HW's
directory listing or ^HG's image data - read back whatever comes over the
same socket.
"""

import socket
import time


def send(address: str, port: int, payload: bytes, timeout: float,
        read_reply: bool = False) -> bytes:
    """Send a payload to the printer, optionally reading whatever it replies."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
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
    finally:
        sock.close()
