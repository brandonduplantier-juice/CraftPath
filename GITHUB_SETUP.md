# How to put CraftPath on GitHub (step by step)

## A. Create the repo (one time)
1. Go to github.com, sign in (or sign up — free).
2. Click the "+" top-right -> "New repository".
3. Repository name: craftpath
   Owner: your account (juice6121)
   -> This makes the URL github.com/juice6121/craftpath
4. Description: "PoE2 crafting cost & path optimizer"
5. Set to PUBLIC (so others can find/download it).
6. Do NOT check "Add a README" (we already have one).
7. Click "Create repository".

## B. Upload the code (easiest: web upload, no git needed)
1. On the new empty repo page, click "uploading an existing file".
2. Unzip craftpath_repo.zip on your computer.
3. Drag ALL the files/folders from inside it into the GitHub upload area.
   (Make sure RUN_DESKTOP.bat with your cookie is NOT among them — the repo
   ships RUN_DESKTOP.bat.template instead. .gitignore prevents the cookie file.)
4. Scroll down, click "Commit changes".

## C. Cut a Release (this is the download people grab)
1. On your repo page, click "Releases" (right sidebar) -> "Create a new release".
2. Click "Choose a tag", type v1.0.0, click "Create new tag".
3. Release title: CraftPath v1.0.0
4. Paste the contents of RELEASE_NOTES.md into the description.
5. Under "Attach binaries", drag in CraftPath-Desktop.zip.
6. Click "Publish release".

Now github.com/juice6121/craftpath/releases has your downloadable desktop zip.

## D. (Optional) Deploy the free online version
1. Go to render.com, sign up, connect your GitHub.
2. New -> Web Service -> pick the craftpath repo.
3. It auto-detects the Procfile. Click "Create Web Service".
4. After it builds, you get a public URL like craftpath.onrender.com.
   That's the free online optimizer, ready to share.

## After setup: tell Claude your real repo URL
The in-app "Download" link currently points at a placeholder
(github.com/juice6121/craftpath/releases). If your repo name differs, send the
real URL and it gets updated in one line.
