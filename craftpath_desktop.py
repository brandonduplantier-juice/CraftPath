"""
craftpath_desktop.py: entry point for the packaged Windows desktop build.

Boots the Flask app in DESKTOP mode (live market pricing available with the
user's own POESESSID), picks a free local port, serves it with waitress (a
Windows-friendly WSGI server; gunicorn is Unix-only), and opens the default
browser to the app. Closing the console window stops the server.

Build into a single .exe with PyInstaller using craftpath.spec (see README/
DESKTOP_BUILD.md). This file is the PyInstaller entry script.
"""
import os
import sys
import socket
import threading
import webbrowser
import time

# DESKTOP mode: do NOT set DEPLOY_MODE=public, so the local market features
# (which use the user's own PoE session) are available. The app already treats
# the absence of DEPLOY_MODE=public as the local/desktop case.
os.environ.pop("DEPLOY_MODE", None)


def _free_port(preferred=5173) -> int:
    """Return a usable localhost port, preferring a stable one for bookmarks."""
    for port in (preferred, 5174, 5175, 0):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.bind(("127.0.0.1", port))
            p = s.getsockname()[1]
            s.close()
            return p
        except OSError:
            continue
    return preferred


def _open_browser(url: str):
    # small delay so the server is accepting connections first
    time.sleep(1.2)
    try:
        webbrowser.open(url)
    except Exception:
        pass


def main():
    # import after env setup so the app sees the right mode
    from app import app as flask_app

    port = _free_port()
    url = f"http://127.0.0.1:{port}/"

    print("=" * 60)
    print("  CraftPath Desktop  (for Divine Intent)")
    print("  PoE2 crafting optimizer + live market pricing")
    print("=" * 60)
    print(f"  Opening {url}")
    print("  Keep this window open while you use CraftPath.")
    print("  Close this window to stop the server.")
    print("=" * 60)

    threading.Thread(target=_open_browser, args=(url,), daemon=True).start()

    # waitress: production-grade, pure-Python, works on Windows. Falls back to
    # Flask's dev server only if waitress somehow isn't bundled.
    try:
        from waitress import serve
        serve(flask_app, host="127.0.0.1", port=port, threads=8)
    except ImportError:
        flask_app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
