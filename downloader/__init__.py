"""Multi-site album/folder downloader."""

from .core import FileEntry, Listing, SiteHandler, detect_handler, register
from .engine import run

# Importing registers each handler with the dispatcher.
from .sites import bunkr, cyberdrop, filester, goonbox, jpg6  # noqa: F401

__all__ = ["FileEntry", "Listing", "SiteHandler", "detect_handler", "register", "run"]
