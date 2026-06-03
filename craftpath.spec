# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for CraftPath Desktop.

Build (on Windows, from the repo root):
    pip install pyinstaller waitress
    pyinstaller craftpath.spec

Output: dist/CraftPathDesktop.exe  (one-file, double-click to run)

Notes:
- datas bundles the read-only resources (data/, templates/, static/,
  prices_cache.json). At runtime respath.py resolves them via sys._MEIPASS.
- hiddenimports lists modules imported dynamically (via `import X` inside
  functions) that PyInstaller's static analysis can miss.
- onefile build: everything packs into a single .exe. First launch is a little
  slower (it unpacks to a temp dir); subsequent launches are fast.
"""
import os

block_cipher = None

# resources to bundle: (source, destination-folder-inside-bundle)
datas = [
    ("data", "data"),
    ("templates", "templates"),
    ("static", "static"),
    ("prices_cache.json", "."),
    ("data/essences_by_class.json", "data"),  # explicit; used by prices seeding
]
# drop any that don't exist so the spec doesn't error on a partial checkout
datas = [(s, d) for (s, d) in datas if os.path.exists(s)]

hiddenimports = [
    # web stack
    "flask", "waitress", "jinja2", "werkzeug",
    # app modules imported dynamically inside functions
    "respath", "app", "solver", "putrefaction", "desecrated", "essences",
    "build_weights", "profit_scanner", "trade_client", "item_parser",
    "prices", "prices_scout", "prices_ninja", "datastore",
    # numeric
    "numpy",
]

a = Analysis(
    ["craftpath_desktop.py"],
    pathex=[os.path.abspath(".")],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # trim weight: these aren't needed by the desktop app
        "matplotlib", "pandas", "scipy", "PIL", "tkinter",
        "pytest", "playwright", "sentry_sdk",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="CraftPathDesktop",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,          # keep the console so users see the URL + can close to quit
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # icon="static/craftpath.ico",   # uncomment if you add an .ico
)
