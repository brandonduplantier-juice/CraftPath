"""
respath.py: resolve the application's resource root whether running from source
or from a PyInstaller-frozen .exe.

When PyInstaller freezes the app it unpacks bundled data files into a temporary
directory exposed as sys._MEIPASS. Modules that read data/ or templates/ at
runtime must look there when frozen, and next to the source file otherwise.

Usage:
    from respath import resource_root, resource_path
    DATA = resource_path("data")
"""
import os
import sys


def resource_root() -> str:
    """Directory that contains data/, templates/, prices_cache.json, etc."""
    # PyInstaller sets sys._MEIPASS to the unpacked bundle dir at runtime.
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return base
    # running from source: this file lives at the project root
    return os.path.dirname(os.path.abspath(__file__))


def resource_path(*parts) -> str:
    """Absolute path to a bundled resource (read-only when frozen)."""
    return os.path.join(resource_root(), *parts)


def writable_dir() -> str:
    """A directory safe to WRITE to at runtime.

    The frozen bundle dir (sys._MEIPASS) is temporary and read-only in spirit,
    so anything we write (e.g. a refreshed prices_cache.json, the local session)
    goes next to the .exe instead. From source, that's just the project root.
    """
    if getattr(sys, "frozen", False):
        # directory containing the running .exe
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))
