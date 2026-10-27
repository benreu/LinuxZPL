"""
A printer on a local port rather than the network: the USB printer node the
kernel's usblp driver makes for it (/dev/usb/lp0).

The same contracts as printer_io's send(), send_command() and send_stream(),
so a caller can take either path. What differs is underneath. There is no
socket for a CancelToken to shut down, so nothing here ever blocks: the
device is opened non-blocking and every wait is a poll() of one TICK, after
which a cancel is noticed. And `timeout` is a stall limit - how long the
printer may go without taking a byte, as it does while it erases flash -
not a limit on the whole call.

This is the way into a printer whose network is not running, which is how a
Zebra sits in forced download mode: its boot loader listens on USB, serial
and parallel only.
"""

import errno
import glob
import grp
import os
import select
import time
from dataclasses import dataclass
from typing import Callable, Iterable, List, Optional, Tuple

from .printer_io import Cancelled, CancelToken

# How long a wait goes before looking up to see whether it was cancelled.
TICK = 0.2

# After the printer starts answering it sends the rest promptly, so a reply
# ends at this much quiet rather than waiting out the full timeout - as
# printer_io._exchange does.
REPLY_QUIET = 0.5

_GONE = select.POLLHUP | select.POLLERR | select.POLLNVAL


@dataclass(frozen=True)
class LocalPort:
    """A printer reached through a device node, e.g. /dev/usb/lp0."""
    path: str


def describe_1284(device_id: str) -> str:
    """`Zebra Technologies ZTC UPS ZT400` from an IEEE 1284 device ID: its
    manufacturer and model, under the long keys a Zebra uses (MANUFACTURER,
    MODEL) or the short ones most printers use (MFG, MDL). Empty if it names
    neither."""
    fields = {}
    for part in device_id.split(';'):
        key, sep, value = part.partition(':')
        if sep:
            fields[key.strip().upper()] = value.strip()
    names = (fields.get('MANUFACTURER') or fields.get('MFG', ''),
             fields.get('MODEL') or fields.get('MDL', ''))
    return ' '.join(name for name in names if name)


def list_ports(dev_root: str = '/dev',
               sys_root: str = '/sys') -> List[Tuple[str, str]]:
    """Every USB printer node, as (path, description), in number order. The
    description is what the printer says it is, or empty if it says nothing.
    The roots are parameters only so a test can point them at a fake tree."""
    found = []
    paths = glob.glob(os.path.join(dev_root, 'usb', 'lp*'))
    for path in sorted(paths, key=lambda p: (len(p), p)):
        id_file = os.path.join(sys_root, 'class', 'usbmisc',
                               os.path.basename(path), 'device', 'ieee1284_id')
        try:
            with open(id_file, errors='replace') as f:
                described = describe_1284(f.read())
        except OSError:
            described = ''
        found.append((path, described))
    return found


def send(port: LocalPort, payload: bytes, timeout: float,
         read_reply: bool = False,
         cancel: Optional[CancelToken] = None) -> bytes:
    """Send a payload to the printer, optionally reading whatever it replies.
    A cancelled request raises Cancelled and never returns data."""
    fd = _open(port, cancel)
    try:
        _write(fd, port.path, payload, timeout, cancel)
        _drain(fd, port.path, timeout, cancel)
        reply = _read_reply(fd, port.path, timeout, cancel) if read_reply else b''
    finally:
        os.close(fd)
    _check(cancel, port.path)
    return reply


def send_command(port: LocalPort, command: str, timeout: float = 5,
                 cancel: Optional[CancelToken] = None) -> str:
    """printer_io.send_command() on a local port: raw text out, whatever the
    printer writes back decoded with replacement characters."""
    reply = send(port, command.encode('utf-8'), timeout,
                 read_reply=True, cancel=cancel)
    return reply.decode('utf-8', errors='replace')


def send_stream(port: LocalPort, chunks: Iterable[bytes], total: int,
                timeout: float,
                cancel: Optional[CancelToken] = None,
                progress: Optional[Callable[[int, int], None]] = None) -> None:
    """Send `chunks` one after another, reporting `progress(sent, total)` after
    each - printer_io.send_stream() on a local port.

    A device that goes away only after every byte has been taken is the
    printer restarting to flash itself, not an error; one that goes away
    earlier is. A stall is an error either way: the tail of the file may not
    have reached the printer.
    """
    fd = _open(port, cancel)
    sent = 0
    try:
        for chunk in chunks:
            _write(fd, port.path, chunk, timeout, cancel)
            sent += len(chunk)
            if cancel is not None and cancel.cancelled:
                break
            if progress is not None:
                progress(sent, total)
        if sent >= total:
            _drain(fd, port.path, timeout, cancel)
    except OSError as e:
        if sent < total or isinstance(e, TimeoutError):
            raise
    finally:
        os.close(fd)
    _check(cancel, port.path)


def _open(port: LocalPort, cancel: Optional[CancelToken]) -> int:
    if cancel is not None and cancel.cancelled:
        raise Cancelled(f"Cancelled before opening {port.path}")
    try:
        return os.open(port.path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    except PermissionError:
        raise PermissionError(_permission_hint(port.path)) from None


def _permission_hint(path: str) -> str:
    """What to do about a device node this user cannot open: join the group
    that owns it, which is `lp` for a USB printer. Read from the node rather
    than assumed, since distributions differ."""
    try:
        group = grp.getgrgid(os.stat(path).st_gid).gr_name
    except (OSError, KeyError):
        return f"No permission to open {path}."
    return (f"No permission to open {path}: add yourself to the '{group}' "
            f"group (sudo usermod -aG {group} $USER) and log in again.")


def _check(cancel: Optional[CancelToken], path: str) -> None:
    if cancel is not None and cancel.cancelled:
        raise Cancelled(f"Cancelled while talking to {path}")


def _ready(fd: int, path: str, event: int,
           cancel: Optional[CancelToken]) -> bool:
    """Wait up to one TICK for `event`. Raises Cancelled, and OSError if the
    device has gone away - unplugged, or the printer restarting."""
    _check(cancel, path)
    poller = select.poll()
    poller.register(fd, event)
    for _fd, got in poller.poll(TICK * 1000):
        if got & event:
            return True
        if got & _GONE:
            raise OSError(errno.ENODEV, f"{path} went away")
    return False


def _stalled(path: str, since: float, timeout: float) -> None:
    if time.monotonic() - since > timeout:
        raise TimeoutError(f"{path} stopped taking data for {timeout:g} s")


def _write(fd: int, path: str, data: bytes, timeout: float,
           cancel: Optional[CancelToken]) -> None:
    view = memoryview(data)
    since = time.monotonic()
    while view:
        if _ready(fd, path, select.POLLOUT, cancel):
            try:
                written = os.write(fd, view)
            except BlockingIOError:
                # usblp reports a printer out of paper as writable but
                # refuses the write; wait a tick rather than spin.
                time.sleep(TICK)
                written = 0
            if written:
                view = view[written:]
                since = time.monotonic()
                continue
        _stalled(path, since, timeout)


def _drain(fd: int, path: str, timeout: float,
           cancel: Optional[CancelToken]) -> None:
    """Wait for the last write to finish before the device is closed: usblp
    kills a write still in flight when it is released, which would cut the
    tail off the file. It reports writable again once that write is done."""
    since = time.monotonic()
    while not _ready(fd, path, select.POLLOUT, cancel):
        _stalled(path, since, timeout)


def _read_reply(fd: int, path: str, timeout: float,
                cancel: Optional[CancelToken]) -> bytes:
    chunks = []
    deadline = time.monotonic() + timeout
    while True:
        try:
            ready = _ready(fd, path, select.POLLIN, cancel)
        except OSError:
            break
        if ready:
            try:
                chunk = os.read(fd, 4096)
            except BlockingIOError:
                continue
            if not chunk:
                break
            chunks.append(chunk)
            deadline = time.monotonic() + REPLY_QUIET
        elif time.monotonic() > deadline:
            break
    return b''.join(chunks)
