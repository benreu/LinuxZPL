"""The GTK3 frontend. Imports PyGObject; nothing in zplcore does."""

from .window import ZPLViewerWindow, main

__all__ = ['ZPLViewerWindow', 'main']
