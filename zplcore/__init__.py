"""
zplcore - everything about ZPL labels that is not a user interface.

The label model, the ZPL read and write, font discovery and printer I/O, the
geometry of editing, and the decisions that determine whether a label prints
correctly. No module here imports a GUI toolkit at import time, so the whole
core can be exercised without a display - and so a frontend for one toolkit
never has to depend on the other's.

FUNCTIONAL_SPEC.md is the contract this implements; both frontends answer to it.
"""

from .model import (BarcodeElement, CircleElement, DesignElement,
                    DiagonalLineElement, Document, EllipseElement, FieldBlock,
                    FrameElement, GraphicSymbolElement, ImageElement,
                    TextBlock, TextElement)
from .parser import parse_label_size, parse_zpl

__all__ = [
    'DesignElement', 'TextElement', 'FrameElement', 'CircleElement',
    'EllipseElement', 'DiagonalLineElement', 'GraphicSymbolElement',
    'BarcodeElement', 'ImageElement', 'FieldBlock', 'TextBlock', 'Document',
    'parse_zpl',
    'parse_label_size',
]
