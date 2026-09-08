"""
Rasterising label text for the canvas.

Drawn with the same library that measures it. TextElement.printed_width() asks
PIL for the advance width, so drawing with anything else would let the glyphs
and the box that claims to contain them disagree - and the box is what the
printer honours. Both frontends blit the result; only the wrapping into a
toolkit image differs.
"""

from PIL import (Image as PILImage, ImageDraw as PILImageDraw,
                 ImageFont as PILImageFont)

# One entry per string at one size. A label has a handful of text elements, so
# this only has to survive editing one of them; it exists because the whole
# string is drawn rather than a truncated preview of it, and a drag can repaint
# many times a second.
_CACHE_LIMIT = 64
_cache = {}

# Padding around the glyphs, so an overhanging edge is not clipped
MARGIN = 2


def raster(text: str, font_path: str, font_height: int):
    """The text as a PIL RGBA image with transparent background, or None.

    Cached by (text, font, height) - not by the element - so two elements
    sharing a string and a face share the raster.
    """
    text = text or " "
    height = max(1, int(font_height))
    key = (text, font_path, height)
    hit = _cache.get(key)
    if hit is not None:
        return hit

    try:
        font = PILImageFont.truetype(font_path, height)
    except Exception:
        return None

    bbox = PILImageDraw.Draw(PILImage.new('RGBA', (1, 1))).textbbox(
        (0, 0), text, font=font)
    width = max(1, bbox[2] - bbox[0])
    tall = max(1, bbox[3] - bbox[1])

    image = PILImage.new('RGBA', (width + 2 * MARGIN, tall + 2 * MARGIN), (0, 0, 0, 0))
    PILImageDraw.Draw(image).text((MARGIN - bbox[0], MARGIN - bbox[1]), text,
                                  fill=(0, 0, 0, 255), font=font)

    if len(_cache) >= _CACHE_LIMIT:
        _cache.clear()
    _cache[key] = image
    return image


def clear_cache():
    """Drop every cached raster. Used by the tests."""
    _cache.clear()
