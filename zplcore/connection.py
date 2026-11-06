"""
Which printer to talk to - one on the network, or one on this machine's USB -
and the one way every module sends to it.

A Connection is what the user chose: a network address and port, or a USB
printer named by its serial number. The serial number is the identity
because the node a USB printer gets, /dev/usb/lpN, is not stable: the same
printer can come back under another number after a restart. So a USB
Connection is turned into a node only when something is sent, on the worker
thread, by looking the serial number up among the printers present then.

send(), send_command() and send_stream() have printer_io's contracts, with
the printer in place of (address, port). A network Connection goes to
printer_io, a USB one to local_port, and a bare local_port.LocalPort - a node
picked for one job, as Firmware Update does - straight to local_port.
Cancelled and CancelToken stay printer_io's, whichever way a call goes.
"""

import errno
from dataclasses import dataclass, replace
from typing import Callable, Iterable, List, Optional, Tuple, Union

from . import local_port, printer_io

NETWORK = 'network'
USB = 'usb'

SECTION = 'printer'


@dataclass(frozen=True)
class Connection:
    """A network printer (`address`, `port`) or a USB one (`usb_serial`,
    `usb_name`), as `kind` says.

    Both halves are carried whichever is chosen, so switching back to the
    other offers what it was. `usb_name` is what the printer says it is
    (its IEEE 1284 manufacturer and model); it is shown, and it identifies a
    printer whose USB descriptor has no serial number.
    """
    kind: str = NETWORK
    address: str = ''
    port: int = 9100
    usb_serial: str = ''
    usb_name: str = ''

    @classmethod
    def network(cls, address: str, port: int) -> 'Connection':
        return cls(NETWORK, address, port)

    @classmethod
    def usb(cls, serial: str, name: str = '') -> 'Connection':
        return cls(USB, usb_serial=serial, usb_name=name)

    @property
    def is_usb(self) -> bool:
        return self.kind == USB

    def as_network(self, address: str, port: int) -> 'Connection':
        """This network printer chosen, keeping the USB one carried."""
        return replace(self, kind=NETWORK, address=address, port=port)

    def as_usb(self, serial: str, name: str) -> 'Connection':
        """This USB printer chosen, keeping the network address carried."""
        return replace(self, kind=USB, usb_serial=serial, usb_name=name)

    def where(self, with_port: bool = True) -> str:
        """The printer, for a message: `192.168.50.94:9100` (or just the
        address, for the messages that have always named only that), or
        `Zebra Technologies ZTC UPS ZT400 (USB)`."""
        if not self.is_usb:
            return f"{self.address}:{self.port}" if with_port else self.address
        if self.usb_name:
            return f"{self.usb_name} (USB)"
        return f"USB printer {self.usb_serial}"

    def key(self) -> Tuple:
        """What two Connections share when they reach the same printer:
        only the chosen half counts."""
        if self.is_usb:
            return (USB, self.usb_serial or self.usb_name)
        return (NETWORK, self.address, self.port)

    @classmethod
    def read(cls, parser, fallback: 'Connection') -> 'Connection':
        """The [printer] section's connection, or `fallback`'s for a key it
        lacks. A file written before USB printing has no `connection` key
        and reads as network; so does a USB entry that names no printer.
        A malformed port raises ValueError, as configparser does."""
        address = parser.get(SECTION, 'address', fallback=fallback.address)
        port = parser.getint(SECTION, 'port', fallback=fallback.port)
        kind = parser.get(SECTION, 'connection',
                          fallback=fallback.kind).strip().lower()
        serial = parser.get(SECTION, 'usb_serial',
                            fallback=fallback.usb_serial).strip()
        name = parser.get(SECTION, 'usb_name',
                          fallback=fallback.usb_name).strip()
        if kind != USB or not (serial or name):
            kind = NETWORK
        return cls(kind, address, port, serial, name)

    def write(self, parser) -> None:
        """Into the [printer] section, leaving its other keys alone."""
        if not parser.has_section(SECTION):
            parser.add_section(SECTION)
        parser.set(SECTION, 'connection', self.kind)
        parser.set(SECTION, 'address', self.address)
        parser.set(SECTION, 'port', str(self.port))
        for key, value in (('usb_serial', self.usb_serial),
                           ('usb_name', self.usb_name)):
            if value:
                parser.set(SECTION, key, value)
            else:
                parser.remove_option(SECTION, key)


Printer = Union[Connection, local_port.LocalPort]


def list_usb() -> List[Tuple[Connection, str]]:
    """Every USB printer present now, as (Connection, node), in node order."""
    return [(Connection.usb(found.serial, found.description), found.path)
            for found in local_port.list_printers()]


def resolve(printer: Connection) -> local_port.LocalPort:
    """The node a USB printer has now, found by its serial number - or, for
    one without, by its name, the lowest node winning. Raises OSError if it
    is not plugged in."""
    for found in local_port.list_printers():
        if printer.usb_serial:
            if found.serial == printer.usb_serial:
                return local_port.LocalPort(found.path)
        elif printer.usb_name and found.description == printer.usb_name:
            return local_port.LocalPort(found.path)
    raise OSError(errno.ENODEV, _not_connected(printer))


def _not_connected(printer: Connection) -> str:
    if printer.usb_name and printer.usb_serial:
        return (f"{printer.usb_name} (serial number {printer.usb_serial}) "
                f"is not connected by USB.")
    if printer.usb_name:
        return f"{printer.usb_name} is not connected by USB."
    return (f"The USB printer with serial number {printer.usb_serial} is "
            f"not connected.")


def _local(printer: Printer) -> Optional[local_port.LocalPort]:
    """The node to use, or None for a network printer."""
    if isinstance(printer, local_port.LocalPort):
        return printer
    return resolve(printer) if printer.is_usb else None


def send(printer: Printer, payload: bytes, timeout: float,
         read_reply: bool = False,
         cancel: Optional[printer_io.CancelToken] = None) -> bytes:
    """printer_io.send() to whichever printer this is."""
    local = _local(printer)
    if local is None:
        return printer_io.send(printer.address, printer.port, payload,
                               timeout, read_reply=read_reply, cancel=cancel)
    return local_port.send(local, payload, timeout,
                           read_reply=read_reply, cancel=cancel)


def send_command(printer: Printer, command: str, timeout: float = 5,
                 cancel: Optional[printer_io.CancelToken] = None) -> str:
    """printer_io.send_command() to whichever printer this is: raw text
    out, the reply decoded with replacement characters."""
    reply = send(printer, command.encode('utf-8'), timeout,
                 read_reply=True, cancel=cancel)
    return reply.decode('utf-8', errors='replace')


def send_stream(printer: Printer, chunks: Iterable[bytes], total: int,
                timeout: float,
                cancel: Optional[printer_io.CancelToken] = None,
                progress: Optional[Callable[[int, int], None]] = None) -> None:
    """printer_io.send_stream() to whichever printer this is."""
    local = _local(printer)
    if local is None:
        printer_io.send_stream(printer.address, printer.port, chunks, total,
                               timeout, cancel=cancel, progress=progress)
    else:
        local_port.send_stream(local, chunks, total, timeout,
                               cancel=cancel, progress=progress)
