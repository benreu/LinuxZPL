"""
Firmware for a printer: what it runs now, and sending it a new file.

A Zebra firmware file is not wrapped in any command. It is sent to the raw
port exactly as downloaded, and the printer flashes itself and restarts - so
there is nothing to read back, and the upload is only a long send_stream().
"""

from pathlib import Path
from typing import Callable, Optional

from . import printer_io, printer_status

CHUNK = 16 * 1024
# Generous: a printer can stop reading for a while as it erases flash.
UPLOAD_TIMEOUT = 60


def current_firmware(address: str, port: int,
                     cancel: Optional[printer_io.CancelToken] = None) -> Optional[str]:
    """The firmware version ~HI reports, or None if the printer could not be
    asked or did not answer in a form ~HI has. Cancelled propagates."""
    try:
        reply = printer_io.send_command(address, port, '~HI', cancel=cancel)
    except OSError:
        return None
    ident = printer_status.parse_host_identification(reply)
    return ident.firmware if ident else None


def upload_firmware(address: str, port: int, path,
                    progress: Optional[Callable[[int, int], None]] = None,
                    cancel: Optional[printer_io.CancelToken] = None) -> int:
    """Send the firmware file at `path`; returns the number of bytes sent.

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

    printer_io.send_stream(address, port, chunks(), total, UPLOAD_TIMEOUT,
                           cancel=cancel, progress=progress)
    return total
