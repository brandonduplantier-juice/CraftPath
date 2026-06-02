"""
wsgi.py — production entry point for Exile's Forge (free crafting optimizer).

Run with a real WSGI server (not Flask's dev server) when hosting:
    gunicorn wsgi:app

For public hosting, set DEPLOY_MODE=public so market features (which need a
user's own POESESSID) are hard-disabled server-side. The crafting optimizer —
solver, odds, costs, desecration/putrefaction modeling — stays fully available.
"""
import os
# Default a hosted instance to public mode unless explicitly overridden.
os.environ.setdefault("DEPLOY_MODE", "public")
os.environ.setdefault("MARKET_ACCESS_MODE", "off")

from app import app  # noqa: E402

# gunicorn looks for `app`; nothing else needed here.
if __name__ == "__main__":
    # local production-style run (still single-process)
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port)
