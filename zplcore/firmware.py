"""
Firmware for a printer: what it runs now, and sending it a new file.

A Zebra firmware file is not wrapped in any command. It is sent to the raw
port exactly as downloaded, and the printer flashes itself and restarts - so
there is nothing to read back, and the upload is only a long send_stream().

Either over the network, or down a USB port (local_port.py) - the one way in
to a printer in forced download mode, whose network is not running. The
printer is a connection.Connection, or a local_port.LocalPort picked for
this one job.
"""

from pathlib import Path
from typing import Callable, Optional

from . import connection, printer_io, printer_status
from .connection import Printer

CHUNK = 16 * 1024
# Generous: a printer can stop reading for a while as it erases flash.
UPLOAD_TIMEOUT = 60


def format_elapsed(seconds: float) -> str:
    """m:ss, or h:mm:ss from an hour up."""
    seconds = max(0, int(seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def progress_text(fraction: float, elapsed: float) -> str:
    """`42%  0:12 elapsed, ~0:17 left` for an upload `fraction` done after
    `elapsed` seconds. The estimate waits until there is something to base it
    on, and is only as good as the bytes-handed-over figure it extrapolates."""
    fraction = max(0.0, min(1.0, fraction))
    text = f"{int(fraction * 100)}%  {format_elapsed(elapsed)} elapsed"
    if 0.02 <= fraction < 1.0 and elapsed >= 1:
        text += f", ~{format_elapsed(elapsed * (1 - fraction) / fraction)} left"
    return text


def current_firmware(printer: Printer,
                     cancel: Optional[printer_io.CancelToken] = None) -> Optional[str]:
    """The firmware version ~HI reports, or None if the printer could not be
    asked or did not answer in a form ~HI has.

    Cancelled propagates, and so does PermissionError: neither means the
    printer is out of reach, and a port this user may not open has a fix
    they need to be told."""
    try:
        reply = connection.send_command(printer, '~HI', cancel=cancel)
    except PermissionError:
        raise
    except OSError:
        return None
    ident = printer_status.parse_host_identification(reply)
    return ident.firmware if ident else None


def upload_firmware(printer: Printer, path,
                    progress: Optional[Callable[[int, int], None]] = None,
                    cancel: Optional[printer_io.CancelToken] = None) -> int:
    """Send the firmware file at `path`; returns the number of bytes sent.
    Down a USB port, UPLOAD_TIMEOUT is how long the printer may stop taking
    data; over the network it is the socket's timeout.

    `progress(sent, total)` is called from the calling thread after each
    chunk. Raises ValueError for a file that is missing, not a file, or empty,
    OSError if the printer cannot be reached or drops the connection part-way,
    and printer_io.Cancelled if `cancel` fires.
    """
    path = Path(path)
    if not path.is_file():
        raise ValueError(f"{path} is not a file")
    total = path.stat().st_size
    if total == 0:
        raise ValueError(f"{path.name} is empty")

    def chunks():
        with open(path, 'rb') as f:
            while True:
                chunk = f.read(CHUNK)
                if not chunk:
                    return
                yield chunk

    connection.send_stream(printer, chunks(), total, UPLOAD_TIMEOUT,
                           cancel=cancel, progress=progress)
    return total
