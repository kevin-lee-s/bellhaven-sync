"""SQLite state: every proposal ever made and what the reviewer decided."""
import json, sqlite3
from datetime import datetime, timezone
from . import config as C

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (id INTEGER PRIMARY KEY, started_at TEXT, finished_at TEXT,
  site_count INTEGER, account_count INTEGER, new_proposals INTEGER, meta TEXT, report TEXT);
CREATE TABLE IF NOT EXISTS proposals (id TEXT PRIMARY KEY, kind TEXT, title TEXT, account_id TEXT,
  payload TEXT, status TEXT, first_run INTEGER, last_run INTEGER, decided_at TEXT, decided_by TEXT,
  reason TEXT, progress TEXT DEFAULT '{}', result TEXT);
"""
# pending -> approved -> applied | failed | stale ; pending -> rejected ; pending -> superseded
DECIDED = ("applied", "rejected", "failed", "stale")


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")


def connect(path=None):
    db = sqlite3.connect(path or C.DB_PATH, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
    return db


def last_site_count(db):
    r = db.execute("SELECT site_count FROM runs WHERE finished_at IS NOT NULL ORDER BY id DESC LIMIT 1").fetchone()
    return r["site_count"] if r else None


def record(db, run_id, proposals):
    """Insert unseen proposals. Anything already known (decided or pending) is left alone."""
    new = 0
    for p in proposals:
        row = db.execute("SELECT status FROM proposals WHERE id=?", (p["id"],)).fetchone()
        if row is None:
            db.execute("INSERT INTO proposals (id,kind,title,account_id,payload,status,first_run,last_run) "
                       "VALUES (?,?,?,?,?,'pending',?,?)",
                       (p["id"], p["kind"], p["title"], p["account_id"], json.dumps(p), run_id, run_id))
            new += 1
        elif row["status"] == "pending":
            db.execute("UPDATE proposals SET last_run=?, payload=? WHERE id=?", (run_id, json.dumps(p), p["id"]))
    # pending items the latest run no longer produces: the CRM or site changed underneath them
    db.execute("UPDATE proposals SET status='superseded', decided_at=? WHERE status='pending' AND last_run<?",
               (now(), run_id))
    return new


def get(db, pid):
    r = db.execute("SELECT * FROM proposals WHERE id=?", (pid,)).fetchone()
    return dict(r) if r else None


def set_status(db, pid, status, **kw):
    cols = ", ".join(f"{k}=?" for k in kw)
    db.execute(f"UPDATE proposals SET status=?{', ' + cols if cols else ''} WHERE id=?",
               (status, *kw.values(), pid))
