"""
harvest_item_art.py ;  RUN THIS LOCALLY ON YOUR MACHINE (not on the dev box;
poe2db/poecdn are unreachable from the CraftPath build environment).

Builds data/item_art.json = { base_token: "https://web.poecdn.com/.../Art.png" }
by reading poe2db base-type pages, which embed the official GGG poecdn art URL
for a representative base of each category. CraftPath's forge card then shows the
real icon instead of the placeholder silhouette.

This is a best-effort scraper: poe2db's HTML can change, so it logs what it found
and what it couldn't, and never invents a URL. Bases it can't resolve keep the
existing placeholder silhouette in the UI (no breakage).

Requires:  pip install requests beautifulsoup4
Run:       python harvest_item_art.py
"""
from __future__ import annotations
import json, os, sys, time

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    print("Install deps first:  pip install requests beautifulsoup4")
    sys.exit(1)

DATA = os.path.join(os.path.dirname(__file__), "data")
HEADERS = {"User-Agent": "Mozilla/5.0 (CraftPath art harvester; personal use)"}

# A representative poe2db base page per CraftPath base token. poe2db base pages
# embed the poecdn art <img>. One representative base per category is enough -
# the card art is illustrative, not the exact rolled base.
# Format: base_token -> poe2db item page slug (the part after /us/).
# Edit/extend as needed; unknown tokens are skipped (keep placeholder).
POE2DB_SLUG = {
    "amulet": "Amulets", "ring": "Rings", "belt": "Belts", "quiver": "Quivers",
    "focus": "Foci", "claw": "Claws", "dagger": "Daggers", "flail": "Flails",
    "spear": "Spears", "bow": "Bows", "crossbow": "Crossbows", "staff": "Staves",
    "quarterstaff": "Quarterstaves", "talisman": "Amulets",
    "one_hand_axe": "One_Hand_Axes", "one_hand_mace": "One_Hand_Maces",
    "one_hand_sword": "One_Hand_Swords", "two_hand_axe": "Two_Hand_Axes",
    "two_hand_mace": "Two_Hand_Maces", "two_hand_sword": "Two_Hand_Swords",
    "sceptre": "Sceptres", "wand": "Wands",
    "body_str": "Body_Armours", "boots_str": "Boots", "gloves_str": "Gloves",
    "helmet_str": "Helmets", "shield_str": "Shields",
}
# armour/shield attribute variants reuse the same category art
for slot in ("body", "boots", "gloves", "helmet", "shield"):
    base = {"body": "Body_Armours", "boots": "Boots", "gloves": "Gloves",
            "helmet": "Helmets", "shield": "Shields"}[slot]
    for attr in ("str", "dex", "int", "str_dex", "str_int", "dex_int", "str_dex_int"):
        POE2DB_SLUG.setdefault(f"{slot}_{attr}", base)


def find_art_url(slug: str) -> str | None:
    url = f"https://poe2db.tw/us/{slug}"
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        r.raise_for_status()
    except Exception as e:
        print(f"  ! {slug}: fetch failed ({type(e).__name__})")
        return None
    soup = BeautifulSoup(r.text, "html.parser")
    # poe2db item icons are served from web.poecdn.com, but the path is a hash
    # (often NO .png extension) and may be lazy-loaded in data-src/data-original.
    # Accept any poecdn image URL from src OR common lazy-load attributes.
    def candidate(img):
        for attr in ("src", "data-src", "data-original", "data-lazy-src"):
            v = img.get(attr, "")
            if "poecdn.com" in v and ("/image/" in v or "/gen/" in v):
                return v
        return None
    for img in soup.find_all("img"):
        u = candidate(img)
        if u:
            return u
    # fallback: any poecdn image URL anywhere in the raw HTML (inline styles, JSON)
    import re as _re
    m = _re.search(r'https://web\.poecdn\.com/(?:gen/)?image/[^\s"\'<>)]+', r.text)
    return m.group(0) if m else None


def main():
    out = {}
    art_path = os.path.join(DATA, "item_art.json")
    if os.path.exists(art_path):
        out = json.load(open(art_path))
    seen_slug = {}
    ok = miss = 0
    for token, slug in POE2DB_SLUG.items():
        if slug in seen_slug:          # cache per slug (categories repeat)
            url = seen_slug[slug]
        else:
            url = find_art_url(slug)
            seen_slug[slug] = url
            time.sleep(1.0)            # be polite to poe2db
        if url:
            out[token] = url; ok += 1
            print(f"  ok {token:<18} {url}")
        else:
            miss += 1
            print(f"  -- {token:<18} (no art found; keeps placeholder)")
    json.dump(out, open(art_path, "w"), indent=1)
    print(f"\nwrote {art_path}: {ok} resolved, {miss} missing")
    print("Commit data/item_art.json and redeploy to show real art.")


if __name__ == "__main__":
    main()
