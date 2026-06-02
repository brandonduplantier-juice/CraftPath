# GitHub & deployment reference (CraftPath)

Repo: https://github.com/brandonduplantier-juice/CraftPath
Online optimizer: https://craftpath.onrender.com

## Pushing code changes
    cd "C:\Users\brand\Desktop\craftpath_repo"
    git add -A
    git status
    git commit -m "your message"
    git push
Render auto-redeploys on every push to main.

## Cutting a release (desktop download)
Using GitHub CLI:
    gh release create v1.1.0 "C:\path\to\CraftPath-Desktop.zip" --title "CraftPath v1.1.0" --notes-file RELEASE_NOTES.md
Or in the browser: repo -> Releases -> Create new release -> tag -> attach the desktop zip -> Publish.

## Online deploy (already done on Render)
- render.com -> New Web Service -> connect the repo
- Build command: pip install -r requirements.txt
- Start command: gunicorn wsgi:app
- Instance: Free
- wsgi.py forces DEPLOY_MODE=public (no market features, no cookie field on the hosted site)
- Free tier spins down after 15 min idle (about 50s cold start on next visit)

## Error tracking (Sentry)
- app.py activates Sentry only if a SENTRY_DSN env var is set
- Set it in Render: dashboard -> service -> Environment -> add SENTRY_DSN = your DSN
- Local/desktop runs stay Sentry-free

## Safety reminders
- .gitignore blocks RUN_DESKTOP.bat (the cookie-filled launcher). Only the .template ships.
- Never commit a real POESESSID. The repo only contains RUN_DESKTOP.bat.template.
