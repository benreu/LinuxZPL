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
and parallel only - and the way to print on one that has no network at all
(zplcore/connection.py picks between the two).

usblp lets one process hold a node at a time, so an open that finds it busy
waits, within the same stall limit, for whoever has it to let go: a Status
window polling while a label prints, say.
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

# A reply the printer was still sending when its request gave up waits on
# the device for whoever opens it next. Before a request whose reply is
# read, whatever arrives within this much quiet is thrown away, so it is not
# taken for the start of the new reply.
STALE_QUIET = 0.05

_GONE = select.POLLHUP | select.POLLERR | select.POLLNVAL


class PortProblem(OSError):
    """A USB port that cannot be used for a reason the user can do something
    about - no permission, in use, not plugged in, no usblp driver - rather
    than a printer that did not answer. The queries that read any other
    OSError as "the printer could not be asked" let this one through, so its
    advice reaches the user. Its text is the advice alone, without the
    "[Errno n]" an OSError puts in front."""

    def __str__(self):
        return self.strerror or super().__str__()


class PortPermissionError(PortProblem, PermissionError):
    """No permission to open the node: a PortProblem, and a PermissionError
    for anything that asks for one."""


@dataclass(frozen=True)
class LocalPort:
    """A printer reached through a device node, e.g. /dev/usb/lp0."""
    path: str


@dataclass(frozen=True)
class UsbPrinter:
    """A USB printer present now: its node, what it says it is, and the
    serial number its USB descriptor carries (empty if it has none). The
    node number can change when the printer restarts or is plugged in again;
    the serial number does not.

    `interface` is set, and `path` empty, for a printer on the bus that has
    no node because nothing drives it: see list_detached()."""
    path: str
    description: str
    serial: str
    interface: str = ''


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


def list_printers(dev_root: str = '/dev',
                  sys_root: str = '/sys') -> List[UsbPrinter]:
    """Every USB printer node, in number order. The roots are parameters
    only so a test can point them at a fake tree."""
    found = []
    paths = glob.glob(os.path.join(dev_root, 'usb', 'lp*'))
    for path in sorted(paths, key=lambda p: (len(p), p)):
        # `device` is the printer's USB interface; its parent, which the
        # kernel resolves through the symlink, is the USB device that has
        # the serial number.
        device = os.path.join(sys_root, 'class', 'usbmisc',
                              os.path.basename(path), 'device')
        found.append(UsbPrinter(
            path, describe_1284(_read(os.path.join(device, 'ieee1284_id'))),
            _read(os.path.join(device, '..', 'serial')).strip()))
    return found


def list_detached(sys_root: str = '/sys') -> List[UsbPrinter]:
    """Every USB printer on the bus that no driver has, so it has no node.

    usblp makes /dev/usb/lpN for a printer, but a print system's own USB
    driver (a CUPS backend) detaches usblp to talk to the printer itself,
    and one that never attaches it again leaves the printer plugged in and
    working but out of reach here. Printer-class interfaces only, and not
    IPP-over-USB ones (protocol 4), which ipp-usb drives instead. Named from
    the USB device's own strings, since the IEEE 1284 ID is usblp's to read.
    """
    found = []
    pattern = os.path.join(sys_root, 'bus', 'usb', 'devices', '*:*')
    for interface in sorted(glob.glob(pattern)):
        if (_read(os.path.join(interface, 'bInterfaceClass')).strip() != '07'
                or _read(os.path.join(interface, 'bInterfaceProtocol')).strip() == '04'
                or os.path.exists(os.path.join(interface, 'driver'))):
            continue
        name = os.path.basename(interface)
        device = os.path.join(os.path.dirname(interface), name.split(':')[0])
        described = ' '.join(
            part for part in (_read(os.path.join(device, key)).strip()
                              for key in ('manufacturer', 'product')) if part)
        found.append(UsbPrinter('', described,
                                _read(os.path.join(device, 'serial')).strip(),
                                name))
    return found


def list_ports(dev_root: str = '/dev',
               sys_root: str = '/sys') -> List[Tuple[str, str]]:
    """Every USB printer node, as (path, description), in number order. The
    description is what the printer says it is, or empty if it says nothing."""
    return [(printer.path, printer.description)
            for printer in list_printers(dev_root, sys_root)]


def _read(path: str) -> str:
    try:
        with open(path, errors='replace') as f:
            return f.read()
    except OSError:
        return ''


def send(port: LocalPort, payload: bytes, timeout: float,
         read_reply: bool = False,
         cancel: Optional[CancelToken] = None) -> bytes:
    """Send a payload to the printer, optionally reading whatever it replies.
    A cancelled request raises Cancelled and never returns data."""
    fd = _open(port, timeout, cancel)
    try:
        if read_reply:
            _discard_stale(fd, port.path, cancel)
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
    fd = _open(port, timeout, cancel)
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


def _open(port: LocalPort, timeout: float,
          cancel: Optional[CancelToken]) -> int:
    """Open the node, waiting up to `timeout` while another process holds
    it: usblp refuses a second open with EBUSY rather than queueing it."""
    since = time.monotonic()
    while True:
        if cancel is not None and cancel.cancelled:
            raise Cancelled(f"Cancelled before opening {port.path}")
        try:
            return os.open(port.path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        except PermissionError:
            raise PortPermissionError(errno.EACCES,
                                      _permission_hint(port.path)) from None
        except OSError as e:
            if e.errno != errno.EBUSY:
                raise
            if time.monotonic() - since > timeout:
                raise PortProblem(errno.EBUSY, _busy_hint(port.path)) from None
        time.sleep(TICK)


def _busy_hint(path: str) -> str:
    return (f"{path} is in use by another program, or another window of "
            f"this one. If a print queue (CUPS) has this printer, it holds "
            f"the port while it prints.")


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
           cancel: Optional[CancelToken], wait: float = TICK) -> bool:
    """Wait up to `wait`, one TICK by default, for `event`. Raises Cancelled,
    and OSError if the device has gone away - unplugged, or the printer
    restarting."""
    _check(cancel, path)
    poller = select.poll()
    poller.register(fd, event)
    for _fd, got in poller.poll(wait * 1000):
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


def _discard_stale(fd: int, path: str,
                   cancel: Optional[CancelToken]) -> None:
    """Throw away whatever the printer sends before the next request is
    written: the late end of an earlier reply. Over the network that dies
    with its socket; here it would be read as this request's answer."""
    while True:
        try:
            ready = _ready(fd, path, select.POLLIN, cancel, STALE_QUIET)
        except OSError:
            return
        if not ready:
            return
        try:
            if not os.read(fd, 4096):
                return
        except BlockingIOError:
            return


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
