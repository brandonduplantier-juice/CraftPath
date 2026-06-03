"""
verify_desecrated.py  (run LOCALLY; needs live network)

Fetches a PoE2DB per-base modifier page and counts its desecrated mods, so you
can verify/replace the estimates in data/desecrated_per_base.json without manual
transcription.

PoE2DB renders the desecrated section under the Ulaman/Amanamu/Kurgal tabs on
each base page, e.g. https://poe2db.tw/us/Boots_dex . The desecrated mods carry
lord tags (ulaman/amanamu/kurgal) in the page markup.

Usage:
    python verify_desecrated.py Boots_dex boots
    python verify_desecrated.py Body_Armours_str body
    python verify_desecrated.py Bow bow

It prints the prefix/suffix desecrated counts it detects and the exact JSON line
to paste into data/desecrated_per_base.json. Always eyeball the page yourself -
this is an aid, not an oracle (PoE2DB markup changes and JS-renders some parts).

PoE2DB base-page slugs (common ones):
    Boots_dex, Boots_str, Boots_int, Boots_str_dex, Boots_str_int, Boots_dex_int
    Gloves_dex, Gloves_str, Gloves_int (+ hybrids)
    Helmets_dex, Helmets_str, Helmets_int (+ hybrids)
    Body_Armours_str, Body_Armours_dex, Body_Armours_int (+ hybrids)
    Bow, Crossbow, Quiver, Wand, Sceptre, Staves, Daggers, Claws, Spears
    Amulets, Rings, Belts
"""
import sys, re, json, os, urllib.request

UA = "poe2craft-verify/1.0 (personal crafting tool)"


def fetch(slug: str) -> str:
    url = f"https://poe2db.tw/us/{slug}"
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", errors="ignore")


def count_desecrated(html: str):
    """Heuristic count of desecrated mods by lord tag presence near affix rows.
    Desecrated mods on PoE2DB are associated with lord names (Ulaman/Amanamu/
    Kurgal). We count distinct mod rows that sit under those lord groupings.
    Because the page JS-renders parts, treat these as guidance and verify by eye.
    """
    lords = ["Ulaman", "Amanamu", "Kurgal"]
    present = [l for l in lords if l in html]
    # crude: count affix stat rows that appear after the first lord marker
    # (the desecrated block). This won't be exact across all pages; the printed
    # numbers are a starting point to confirm against the rendered page.
    idx = min((html.find(l) for l in present if html.find(l) >= 0), default=-1)
    block = html[idx:idx + 8000] if idx >= 0 else ""
    # count "to maximum", "increased", "% to" style stat lines as candidate mods
    candidates = re.findall(r"(?:\+?\(?\d|#%|\+#)", block)
    return {"lords_present": present,
            "rough_stat_markers": len(candidates),
            "note": "ROUGH; open the page and count the desecrated rows under "
                    "the Ulaman/Amanamu/Kurgal tabs to get exact prefix/suffix counts."}


def main():
    if len(sys.argv) < 3:
        print("usage: python verify_desecrated.py <PoE2DB_slug> <base_token>")
        print("example: python verify_desecrated.py Boots_dex boots")
        return
    slug, token = sys.argv[1], sys.argv[2]
    try:
        html = fetch(slug)
    except Exception as e:
        print("fetch failed:", e); return
    info = count_desecrated(html)
    print(f"page: https://poe2db.tw/us/{slug}")
    print(f"lords present: {info['lords_present']}")
    print(f"rough stat markers in desecrated block: {info['rough_stat_markers']}")
    print(info["note"])
    print("\nAfter counting by eye, update data/desecrated_per_base.json, e.g.:")
    print(f'    "{token}": {{"prefix": <P>, "suffix": <S>, '
          f'"source": "verified:{slug}"}}')


if __name__ == "__main__":
    main()
