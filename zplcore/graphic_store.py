"""
The in-session store `^IS` writes to and `^XG`/`^IM`/`^IL` read from.

A real printer holds these objects in its own memory (R:, E:, B:, A:), so a
label can save one and a later label - in a different file entirely - can
recall it. This module is that memory, for as long as the app keeps running:
a plain module-level dict, not persisted to disk, not scoped to a document.

Keyed on name + extension only. A real printer also distinguishes the device
prefix, but this app has no per-device storage areas to run out of or manage,
so modelling that distinction would add bookkeeping without adding anything a
user could observe - see FUNCTIONAL_SPEC.md section 18.
"""

import re
from typing import Optional

from PIL import Image as PILImage

_STORE: dict = {}

_SPEC = re.compile(
    r'\s*(?:[A-Za-z]:)?\s*([A-Za-z0-9]{1,8})?\s*(?:\.([A-Za-z0-9]{1,8}))?\s*$')


def key(raw_spec: str) -> str:
    """The canonical NAME.EXT this spec is stored/recalled under.

    Best-effort: a spec that does not look like `d:o.x` at all still becomes a
    key, rather than raising, matching how the rest of this codebase reads
    ZPL - tolerantly, so a stray or unusual spec is never the reason a file
    fails to open.
    """
    raw = (raw_spec or '').strip()
    match = _SPEC.match(raw)
    if not match or (not match.group(1) and not match.group(2)):
        return raw.upper() or 'UNKNOWN.GRF'
    name = (match.group(1) or 'UNKNOWN').upper()
    ext = (match.group(2) or 'GRF').upper()
    return f"{name}.{ext}"


def store(raw_spec: str, image: PILImage.Image) -> None:
    _STORE[key(raw_spec)] = image


def recall(raw_spec: str) -> Optional[PILImage.Image]:
    return _STORE.get(key(raw_spec))


def clear() -> None:
    """Forget every stored object. Tests use this so one case's ^IS cannot
    resolve another case's ^XG/^IM/^IL."""
    _STORE.clear()
