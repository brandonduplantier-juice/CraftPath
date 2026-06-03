# Building CraftPath Desktop (.exe)

This packages the full CraftPath crafting optimizer plus live market pricing
into a single Windows .exe that anyone can double-click to run, no Python
install required. It is the same app as the website, with the desktop-only
market features enabled (they use the user's own PoE session).

## Why a desktop build at all

Live market pricing queries the Path of Exile trade API with YOUR PoE session
(POESESSID). That can only run safely on your own machine, never through a
public website, so the market features are desktop-only by design. The free
crafting optimizer works fully without it.

## One-time setup (on your Windows machine)

You need Python 3.11+ (you have 3.13) and two build packages:

```powershell
cd "C:\Users\brand\Desktop\craftpath_repo"
pip install pyinstaller waitress
```

(`waitress` is the local web server the desktop build uses. `gunicorn`, used by
the hosted version, is Unix-only and is not used here.)

## Build

From the repo root:

```powershell
pyinstaller craftpath.spec
```

PyInstaller reads `craftpath.spec`, bundles the code plus all data
(`data/`, `templates/`, `static/`, `prices_cache.json`) into one file, and
writes:

```
dist\CraftPathDesktop.exe
```

First build takes a couple of minutes. The result is a single self-contained
.exe (typically 30 to 80 MB).

## Run

Double-click `dist\CraftPathDesktop.exe`. A console window opens, prints the
local URL, and your browser opens to the app. Keep the console window open
while you use it; close it to stop the server.

## Refreshing prices in the desktop build

The .exe ships with a seed `prices_cache.json`. To refresh prices for the
desktop app, run `prices.py` next to the .exe (or in the repo and rebuild):

```powershell
python prices.py
```

When frozen, the app writes the refreshed `prices_cache.json` next to the .exe
and reads that in preference to the bundled seed, so a refresh sticks without
rebuilding.

## How the packaging works (for future-you)

- `craftpath_desktop.py` is the entry point: it boots the Flask app in desktop
  mode, picks a free localhost port, serves it with waitress, and opens the
  browser.
- `respath.py` resolves resource paths. When PyInstaller freezes the app it
  unpacks bundled files to a temp dir exposed as `sys._MEIPASS`; `respath`
  points data/template reads there when frozen, and next to the source files
  otherwise. Anything that must be WRITTEN at runtime (refreshed prices) goes
  next to the .exe via `writable_dir()`.
- `craftpath.spec` lists the bundled `datas` and the `hiddenimports` for modules
  that are imported dynamically inside functions (which PyInstaller's static
  analysis would otherwise miss): putrefaction, desecrated, build_weights,
  profit_scanner, trade_client, prices_scout, prices_ninja, waitress, etc.

## Troubleshooting

- "Failed to execute script" on launch: usually a missing hidden import. Build
  once with `console=True` (already the default), read the traceback in the
  console, and add the missing module name to `hiddenimports` in
  `craftpath.spec`.
- A page 404s for templates/data: a `datas` entry didn't bundle. Confirm the
  folder exists in the repo before building.
- Antivirus flags the .exe: PyInstaller one-file builds are sometimes false-
  flagged. Code-signing the .exe avoids this; otherwise users may need to allow
  it. This is a known PyInstaller distribution caveat, not a bug in the app.
