"""
Firmware for a printer: what it runs now, and sending it a new file.

A Zebra firmware file is not wrapped in any command. It is sent to the raw
port exactly as downloaded, and the printer flashes itself and restarts - so
there is nothing to read back, and the upload is only a long send_stream().

Either over the network, or down a USB port (local_port.py) - the one way in
to a printer in forced download mode, whose network is not running.
"""

from pathlib import Path
from typing import Callable, Optional

from . import local_port, printer_io, printer_status

CHUNK = 16 * 1024
# Generous: a printer can stop reading for a while as it erases flash.
UPLOAD_TIMEOUT = 60

# What Help in Printer -> Firmware Update says, in both frontends. Forced
# Download Mode is how a ZT410 running a special build was got to take a
# standard V75 file on 2026-10-27, after it refused it running normally;
# the button holds are Zebra's, per model.
HELP = """\
Connection: Network sends to this session's printer. USB lists every USB \
printer this computer can see, by the name the printer gives itself. Its \
/dev/usb/lpN number can change when a printer restarts or is plugged in \
again, so Refresh lists them again.

The file: Zebra ships firmware as a .zip. Unpack it and choose the .zpl \
file inside, such as V75.20.21Z.zpl. It is sent exactly as it is.

If the printer refuses it: the printer checks the file as it arrives and \
shows "Download failed" on its own display if it will not take it. This \
window cannot see that, and goes on sending to the end. Put the printer in \
Forced Download Mode and send the file again:
1. Switch the printer off.
2. Hold the top two menu buttons (ZT410, ZT420) or all three (ZT411, \
ZT421). Other models differ; Zebra's support site has each one's.
3. Switch it on, still holding them, until the display shows "Download \
New Firmware" and "USB: Active".
4. Press Refresh, choose the printer's USB entry, and press Update. The \
current firmware may show as unavailable in this mode; that does not stop \
the update.

The printer's network is off in Forced Download Mode, so it has to be USB.

Once the printer has the whole file it flashes it and restarts by itself. \
Do not switch it off before it has.

No permission to open a USB printer: add yourself to the group the message \
names (usually lp), then log out and back in."""


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


def current_firmware(address: str, port: int,
                     cancel: Optional[printer_io.CancelToken] = None,
                     local: Optional[local_port.LocalPort] = None) -> Optional[str]:
    """The firmware version ~HI reports, or None if the printer could not be
    asked or did not answer in a form ~HI has. With `local` the printer is
    asked on that port instead, and `address` and `port` are not used.

    Cancelled propagates, and so does PermissionError: neither means the
    printer is out of reach, and a port this user may not open has a fix
    they need to be told."""
    try:
        if local is not None:
            reply = local_port.send_command(local, '~HI', cancel=cancel)
        else:
            reply = printer_io.send_command(address, port, '~HI', cancel=cancel)
    except PermissionError:
        raise
    except OSError:
        return None
    ident = printer_status.parse_host_identification(reply)
    return ident.firmware if ident else None


def upload_firmware(address: str, port: int, path,
                    progress: Optional[Callable[[int, int], None]] = None,
                    cancel: Optional[printer_io.CancelToken] = None,
                    local: Optional[local_port.LocalPort] = None) -> int:
    """Send the firmware file at `path`; returns the number of bytes sent.
    With `local` it goes down that port instead of to `address`:`port`, and
    UPLOAD_TIMEOUT is how long the printer may stop taking data.

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

    if local is not None:
        local_port.send_stream(local, chunks(), total, UPLOAD_TIMEOUT,
                               cancel=cancel, progress=progress)
    else:
        printer_io.send_stream(address, port, chunks(), total, UPLOAD_TIMEOUT,
                               cancel=cancel, progress=progress)
    return total
