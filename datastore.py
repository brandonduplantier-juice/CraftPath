"""
datastore.py; lightweight data-refinement collection for CraftPath.

Goal: as people paste items, learn what the parser MISSES so the mod-pool data
can be improved over time. We record three things, all aggregate and privacy-safe:

  1. unmatched mod lines  -> ranked "what mods to add" list
  2. unsupported bases    -> ranked "what base pools to add" list
  3. paste health         -> match-rate over time (is the data improving?)

PRIVACY: we NEVER store account names, IGNs, profile URLs, prices, or anything
identifying. Only normalized mod text and base tokens. The scrub() function
strips known PII-bearing lines before anything is recorded.

STORAGE: store-agnostic. Defaults to a local SQLite file (works immediately).
If DATABASE_URL is set (Postgres), uses that for persistence across redeploys.
Switching is one env var; same pattern as SENTRY_DSN.
"""
from __future__ import annotations
import os, re, sqlite3, threading, datetime, json

_LOCK = threading.Lock()
_DB_PATH = os.environ.get("CRAFTPATH_DB", "/tmp/craftpath_data.db")
_PG_URL = os.environ.get("DATABASE_URL", "").strip()

# lines we must never store (account info from trade copies)
_PII = re.compile(r'(ign:|account|view-profile|pathofexile\.com/account|~price|~b/o|listed )', re.I)


def scrub(line: str) -> str | None:
    """Return the line if safe to store, else None. Strips PII-bearing lines."""
    if not line or _PII.search(line):
        return None
    s = line.strip()
    return s if s else None


# ---------------------------------------------------------------------------
# Backend abstraction. SQLite by default; Postgres if DATABASE_URL present.
# ---------------------------------------------------------------------------
def _use_pg():
    return bool(_PG_URL)


def _connect():
    if _use_pg():
        import psycopg  # only imported if Postgres is configured
        return psycopg.connect(_PG_URL)
    conn = sqlite3.connect(_DB_PATH)
    return conn


_INIT_DONE = False
def _init():
    global _INIT_DONE
    if _INIT_DONE:
        return
    ph = "%s" if _use_pg() else "?"
    with _LOCK:
        conn = _connect()
        cur = conn.cursor()
        # frequency tables: text/base + a running count + last_seen
        cur.execute("""CREATE TABLE IF NOT EXISTS unmatched_mods(
            text TEXT PRIMARY KEY, count INTEGER, last_seen TEXT, sample_base TEXT)""")
        cur.execute("""CREATE TABLE IF NOT EXISTS unsupported_bases(
            base TEXT PRIMARY KEY, count INTEGER, last_seen TEXT)""")
        cur.execute("""CREATE TABLE IF NOT EXISTS paste_health(
            day TEXT PRIMARY KEY, pastes INTEGER, lines_total INTEGER, lines_matched INTEGER)""")
        conn.commit(); conn.close()
    _INIT_DONE = True


def _today():
    return datetime.date.today().isoformat()


def _upsert_count(table, key_col, key_val, extra_cols=None):
    """Increment a count row, inserting if new. Backend-agnostic."""
    extra_cols = extra_cols or {}
    now = datetime.datetime.utcnow().isoformat()
    with _LOCK:
        conn = _connect(); cur = conn.cursor()
        ph = "%s" if _use_pg() else "?"
        # try update first
        sets = "count = count + 1, last_seen = " + ph
        params = [now]
        for c, v in extra_cols.items():
            sets += f", {c} = " + ph; params.append(v)
        params.append(key_val)
        cur.execute(f"UPDATE {table} SET {sets} WHERE {key_col} = {ph}", params)
        if cur.rowcount == 0:
            cols = [key_col, "count", "last_seen"] + list(extra_cols.keys())
            vals = [key_val, 1, now] + list(extra_cols.values())
            phs = ", ".join([ph] * len(cols))
            cur.execute(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({phs})", vals)
        conn.commit(); conn.close()


# ---------------------------------------------------------------------------
# Public recording API; called from the parse endpoint. Best-effort: any
# failure is swallowed so data collection NEVER breaks a user's request.
# ---------------------------------------------------------------------------
def record_paste(base_token, n_lines, n_matched, unmatched_lines, unsupported_base=None):
    try:
        _init()
        # per-day health metric
        day = _today()
        with _LOCK:
            conn = _connect(); cur = conn.cursor()
            ph = "%s" if _use_pg() else "?"
            cur.execute(f"UPDATE paste_health SET pastes=pastes+1, lines_total=lines_total+{ph}, "
                        f"lines_matched=lines_matched+{ph} WHERE day={ph}",
                        [n_lines, n_matched, day])
            if cur.rowcount == 0:
                cur.execute(f"INSERT INTO paste_health(day,pastes,lines_total,lines_matched) "
                            f"VALUES ({ph},1,{ph},{ph})", [day, n_lines, n_matched])
            conn.commit(); conn.close()
        # unmatched mods (scrubbed)
        for ln in (unmatched_lines or []):
            safe = scrub(ln)
            if safe:
                _upsert_count("unmatched_mods", "text", safe, {"sample_base": base_token or ""})
        # unsupported base
        if unsupported_base:
            _upsert_count("unsupported_bases", "base", unsupported_base)
    except Exception:
        pass  # collection must never break the request


def summary(limit=50):
    """Return the refinement dashboard data: top missing mods/bases + health."""
    try:
        _init()
        with _LOCK:
            conn = _connect(); cur = conn.cursor()
            cur.execute("SELECT text, count, last_seen, sample_base FROM unmatched_mods "
                        "ORDER BY count DESC LIMIT %d" % int(limit))
            mods = [{"text": r[0], "count": r[1], "last_seen": r[2], "base": r[3]} for r in cur.fetchall()]
            cur.execute("SELECT base, count, last_seen FROM unsupported_bases ORDER BY count DESC LIMIT 50")
            bases = [{"base": r[0], "count": r[1], "last_seen": r[2]} for r in cur.fetchall()]
            cur.execute("SELECT day, pastes, lines_total, lines_matched FROM paste_health ORDER BY day DESC LIMIT 30")
            health = [{"day": r[0], "pastes": r[1], "lines_total": r[2], "lines_matched": r[3],
                       "match_rate": round(r[3]/r[2], 3) if r[2] else None} for r in cur.fetchall()]
            conn.close()
        return {"ok": True, "storage": "postgres" if _use_pg() else "sqlite",
                "top_missing_mods": mods, "unsupported_bases": bases, "health": health}
    except Exception as e:
        return {"ok": False, "error": str(e)}
