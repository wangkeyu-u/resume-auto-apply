"""SQLite database layer for the resume auto-apply assistant.

Pure stdlib sqlite3. All rows are returned as dicts for easy JSON serialisation.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
import threading
from datetime import datetime, timezone, timedelta
from typing import Any, Optional

DB_PATH = os.environ.get(
    "RAA_DB_PATH",
    os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "app.db"),
)

# Single shared connection, guarded by one lock. This app is single-user/local,
# so serialising every DB access is the simplest way to make it impossible to
# hit "database is locked" no matter how many threads (scheduler + concurrent
# requests) write at once. WAL + busy_timeout stay as extra safety.
_DB_CONN = None
_DB_LOCK = threading.Lock()


class _LockedConnection:
    """Wraps a sqlite3 connection so every statement is serialised."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def execute(self, *args, **kwargs):
        with _DB_LOCK:
            return self._conn.execute(*args, **kwargs)

    def executemany(self, *args, **kwargs):
        with _DB_LOCK:
            return self._conn.executemany(*args, **kwargs)

    def executescript(self, *args, **kwargs):
        with _DB_LOCK:
            return self._conn.executescript(*args, **kwargs)

    def commit(self):
        with _DB_LOCK:
            return self._conn.commit()

    def rollback(self):
        with _DB_LOCK:
            return self._conn.rollback()

    def __getattr__(self, name):
        return getattr(self._conn, name)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _norm(s: str) -> str:
    return (s or "").lower().strip()


def get_conn():
    global _DB_CONN
    if _DB_CONN is None:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=15000")
        conn.execute("PRAGMA foreign_keys=ON")
        _DB_CONN = _LockedConnection(conn)
    return _DB_CONN


def init_db() -> None:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = get_conn()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS profile (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            name TEXT,
            email TEXT,
            phone TEXT,
            title TEXT,
            location TEXT,
            years_experience INTEGER DEFAULT 0,
            summary TEXT,
            skills TEXT,                -- JSON array of strings
            resume_data TEXT,           -- JSON: educations/experiences/projects/certs/languages/links
            salary_min INTEGER,
            salary_max INTEGER,
            resume_html TEXT,
            updated_at TEXT
        );

        CREATE TABLE IF NOT EXISTS jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT,
            source_id TEXT,
            title TEXT,
            company TEXT,
            location TEXT,
            url TEXT,
            description TEXT,
            salary_text TEXT,
            salary_min INTEGER,
            salary_max INTEGER,
            posted_at TEXT,
            created_at TEXT,
            raw TEXT,
            UNIQUE(source, source_id)
        );

        CREATE TABLE IF NOT EXISTS applications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id INTEGER UNIQUE REFERENCES jobs(id) ON DELETE CASCADE,
            status TEXT DEFAULT 'new',
            match_score REAL DEFAULT 0,
            strengths TEXT,
            cover_letter TEXT,
            resume_variant TEXT,
            screening_report TEXT,      -- JSON: JD requirements vs candidate coverage
            email_to TEXT,
            email_subject TEXT,
            sent_at TEXT,
            notes TEXT,
            created_at TEXT,
            updated_at TEXT
        );

        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        );

        CREATE TABLE IF NOT EXISTS conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            platform TEXT DEFAULT 'boss',     -- boss / linkedin / generic
            job_id INTEGER REFERENCES jobs(id) ON DELETE SET NULL,
            recruiter_name TEXT,
            recruiter_title TEXT,
            job_title TEXT,
            company TEXT,
            status TEXT DEFAULT 'active',       -- active / closed / archived
            last_message_at TEXT,
            created_at TEXT,
            updated_at TEXT,
            UNIQUE(platform, recruiter_name, job_title, company)
        );

        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
            direction TEXT,                      -- inbound (HR->me) / outbound (me->HR)
            text TEXT,
            status TEXT DEFAULT 'received',      -- received / drafted / approved / sent
            reply_to INTEGER,                    -- inbound message id this draft answers
            meta TEXT,                           -- JSON (intent, confidence, etc.)
            created_at TEXT,
            updated_at TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_jobs_source ON jobs(source);
        CREATE INDEX IF NOT EXISTS idx_app_status ON applications(status);
        CREATE INDEX IF NOT EXISTS idx_conv_job ON conversations(job_id);
        CREATE INDEX IF NOT EXISTS idx_msg_conv ON messages(conversation_id);

        CREATE TABLE IF NOT EXISTS blacklist (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT DEFAULT 'company',     -- company / keyword / title
            value TEXT NOT NULL,             -- company name / keyword / title fragment
            reason TEXT,
            source TEXT DEFAULT 'manual',    -- manual / auto (已读不回/不匹配/拒绝)
            until TEXT,                      -- expiry ISO (null = permanent)
            created_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_blacklist_kind ON blacklist(kind);
        """
    )
    conn.commit()
    _migrate(conn)


def _migrate(conn: sqlite3.Connection) -> None:
    """Add columns that may be missing on an already-created database."""
    for table, col, ctype in [
        ("profile", "resume_data", "TEXT"),
        ("applications", "screening_report", "TEXT"),
    ]:
        cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if col not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ctype}")
    conn.commit()


def _locked_query(sql: str, params: tuple = ()) -> list:
    """Execute + fetch fully under the global lock (no gap for another thread)."""
    conn = get_conn()
    with _DB_LOCK:
        cur = conn._conn.execute(sql, params)
        return cur.fetchall()


def _row(conn, sql: str, params: tuple = ()) -> Optional[dict]:
    rows = _locked_query(sql, params)
    return dict(rows[0]) if rows else None


def _rows(conn, sql: str, params: tuple = ()) -> list[dict]:
    return [dict(r) for r in _locked_query(sql, params)]


# ----------------------------- profile ---------------------------------------
def get_profile() -> dict:
    conn = get_conn()
    row = _row(conn, "SELECT * FROM profile WHERE id = 1")
    if row is None:
        conn.execute(
            "INSERT INTO profile (id, updated_at) VALUES (1, ?)", (_now(),)
        )
        conn.commit()
        row = _row(conn, "SELECT * FROM profile WHERE id = 1")
    row["skills"] = json.loads(row.get("skills") or "[]")
    row["resume_data"] = json.loads(row.get("resume_data") or "{}")
    return row


def upsert_profile(data: dict) -> dict:
    conn = get_conn()
    cur = _row(conn, "SELECT id FROM profile WHERE id = 1")
    data["updated_at"] = _now()
    if isinstance(data.get("skills"), list):
        data["skills"] = json.dumps(data["skills"], ensure_ascii=False)
    if isinstance(data.get("resume_data"), dict):
        data["resume_data"] = json.dumps(data["resume_data"], ensure_ascii=False)
    cols = [
        "name", "email", "phone", "title", "location", "years_experience",
        "summary", "skills", "resume_data", "salary_min", "salary_max",
        "resume_html", "updated_at",
    ]
    if cur is None:
        conn.execute(
            "INSERT INTO profile (id, %s) VALUES (1, %s)"
            % (", ".join(cols), ", ".join("?" for _ in cols)),
            tuple(data.get(c) for c in cols),
        )
    else:
        conn.execute(
            "UPDATE profile SET %s WHERE id = 1"
            % ", ".join(f"{c}=?" for c in cols),
            tuple(data.get(c) for c in cols),
        )
    conn.commit()
    return get_profile()


# ------------------------------- jobs ----------------------------------------
def insert_job(job: dict) -> Optional[int]:
    """Insert a job if its (source, source_id) is new. Returns job id or None if dup."""
    conn = get_conn()
    for _attempt in range(4):
        try:
            cur = conn.execute(
                """
                INSERT INTO jobs
                (source, source_id, title, company, location, url, description,
                 salary_text, salary_min, salary_max, posted_at, created_at, raw)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    job.get("source"), job.get("source_id"), job.get("title"),
                    job.get("company"), job.get("location"), job.get("url"),
                    job.get("description"), job.get("salary_text"),
                    job.get("salary_min"), job.get("salary_max"),
                    job.get("posted_at"), _now(), job.get("raw") or "",
                ),
            )
            conn.commit()
            return int(cur.lastrowid)
        except sqlite3.IntegrityError:
            return None
        except sqlite3.OperationalError:
            # transient lock contention (scheduler + request racing) — back off & retry
            time.sleep(0.25 * (_attempt + 1))
    return None


def list_jobs(limit: int = 200, offset: int = 0, only_unapplied: bool = False) -> list[dict]:
    conn = get_conn()
    if only_unapplied:
        sql = """
            SELECT j.* FROM jobs j
            LEFT JOIN applications a ON a.job_id = j.id
            WHERE a.id IS NULL
            ORDER BY j.created_at DESC LIMIT ? OFFSET ?
        """
    else:
        sql = "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ? OFFSET ?"
    return _rows(conn, sql, (limit, offset))


def get_job(job_id: int) -> Optional[dict]:
    return _row(get_conn(), "SELECT * FROM jobs WHERE id = ?", (job_id,))


def find_job_by_url(url: str) -> Optional[dict]:
    """Extension: resolve a job by the page url (stored in url or source_id)."""
    if not url:
        return None
    return _row(get_conn(), "SELECT * FROM jobs WHERE url=? OR source_id=?", (url, url))


def find_job_by_text(title: str, company: str = "") -> Optional[dict]:
    """Extension: fuzzy-resolve a job by page title/company (apply-page url differs)."""
    t = (title or "").strip()
    if not t:
        return None
    conn = get_conn()
    like = f"%{t}%"
    if company:
        row = _row(conn,
                   "SELECT * FROM jobs WHERE title LIKE ? AND company LIKE ? ORDER BY created_at DESC LIMIT 1",
                   (like, f"%{company}%"))
        if row:
            return row
    return _row(conn, "SELECT * FROM jobs WHERE title LIKE ? ORDER BY created_at DESC LIMIT 1", (like,))


def job_count() -> int:
    return _row(get_conn(), "SELECT COUNT(*) AS c FROM jobs")["c"]


# --------------------------- applications ------------------------------------
def list_applications(status: Optional[str] = None, limit: int = 200) -> list[dict]:
    conn = get_conn()
    if status:
        rows = _rows(
            conn,
            """SELECT a.*, j.title, j.company, j.location, j.url, j.source
               FROM applications a JOIN jobs j ON j.id = a.job_id
               WHERE a.status = ? ORDER BY a.updated_at DESC LIMIT ?""",
            (status, limit),
        )
    else:
        rows = _rows(
            conn,
            """SELECT a.*, j.title, j.company, j.location, j.url, j.source
               FROM applications a JOIN jobs j ON j.id = a.job_id
               ORDER BY a.updated_at DESC LIMIT ?""",
            (limit,),
        )
    return rows


def get_application(app_id: int) -> Optional[dict]:
    return _row(
        get_conn(),
        """SELECT a.*, j.title, j.company, j.location, j.url, j.source, j.description
           FROM applications a JOIN jobs j ON j.id = a.job_id WHERE a.id = ?""",
        (app_id,),
    )


def upsert_application(job_id: int, data: dict, status: Optional[str] = None) -> dict:
    conn = get_conn()
    existing = _row(conn, "SELECT id FROM applications WHERE job_id = ?", (job_id,))
    data["updated_at"] = _now()
    if isinstance(data.get("screening_report"), dict):
        data["screening_report"] = json.dumps(data["screening_report"], ensure_ascii=False)
    cols = [
        "match_score", "strengths", "cover_letter", "resume_variant",
        "screening_report", "email_to", "email_subject", "sent_at",
        "notes", "updated_at",
    ]
    if existing is None:
        base = {
            "job_id": job_id,
            "status": status or "new",
            "created_at": _now(),
        }
        for c in cols:
            base[c] = data.get(c)
        keys = list(base.keys())
        conn.execute(
            "INSERT INTO applications (%s) VALUES (%s)"
            % (", ".join(keys), ", ".join("?" for _ in keys)),
            tuple(base.get(k) for k in keys),
        )
    else:
        upd = {c: data.get(c) for c in cols}
        if status:
            upd["status"] = status
        conn.execute(
            "UPDATE applications SET %s WHERE job_id = ?"
            % ", ".join(f"{c}=?" for c in upd),
            tuple(upd.values()) + (job_id,),
        )
    conn.commit()
    return _row(get_conn(), "SELECT * FROM applications WHERE job_id = ?", (job_id,))


def update_application_status(app_id: int, status: str, extra: dict | None = None) -> Optional[dict]:
    conn = get_conn()
    extra = extra or {}
    extra["status"] = status
    extra["updated_at"] = _now()
    conn.execute(
        "UPDATE applications SET %s WHERE id = ?"
        % ", ".join(f"{c}=?" for c in extra),
        tuple(extra.values()) + (app_id,),
    )
    conn.commit()
    return get_application(app_id)


def application_count_by_status() -> dict:
    rows = _rows(get_conn(), "SELECT status, COUNT(*) c FROM applications GROUP BY status")
    return {r["status"]: r["c"] for r in rows}


# ----------------------------- stats / dashboard ---------------------------
def application_stats() -> dict:
    """Aggregate the whole pipeline into funnel + kanban + rates for the dashboard."""
    conn = get_conn()
    status_counts = application_count_by_status()
    jobs_total = job_count()

    def c(s: str) -> int:
        return int(status_counts.get(s, 0))

    matched = c("matched") + c("drafted")
    sent = c("sent") + c("applied")
    interviewing = c("interviewing")
    rejected = c("rejected")

    # engagement / reply signal from conversations
    conv_rows = _rows(
        conn,
        """SELECT convs.id,
                  (SELECT COUNT(*) FROM messages m
                     WHERE m.conversation_id=convs.id AND m.direction='outbound'
                       AND m.status IN ('approved','sent')) AS out_cnt,
                  (SELECT COUNT(*) FROM messages m
                     WHERE m.conversation_id=convs.id AND m.direction='inbound') AS in_cnt
           FROM conversations convs""",
    )
    total_convs = len(conv_rows)
    engaged = sum(1 for r in conv_rows if r["out_cnt"] > 0)
    replied_by_hr = sum(1 for r in conv_rows if (r["in_cnt"] or 0) > 1)
    conv_active = _rows(conn, "SELECT COUNT(*) c FROM conversations WHERE status='active'")[-1]["c"]

    send_rate = round(sent / jobs_total * 100, 1) if jobs_total else 0.0
    interview_rate = round(interviewing / sent * 100, 1) if sent else 0.0
    engage_rate = round(engaged / total_convs * 100, 1) if total_convs else 0.0
    reply_rate = round(replied_by_hr / total_convs * 100, 1) if total_convs else 0.0

    # kanban: applications grouped by status
    kanban_statuses = ["new", "matched", "drafted", "approved", "sent",
                       "applied", "interviewing", "rejected", "archived"]
    by_status: dict[str, list] = {}
    for st in kanban_statuses:
        apps = list_applications(status=st, limit=300)
        by_status[st] = [
            {"id": a["id"], "title": a["title"], "company": a["company"],
             "match_score": a["match_score"], "sent_at": a["sent_at"],
             "job_id": a["job_id"], "status": a["status"]}
            for a in apps
        ]

    # recent sent activity
    recent = [a for a in list_applications(limit=300) if a.get("sent_at")]
    recent.sort(key=lambda a: a["sent_at"], reverse=True)

    return {
        "jobs_total": jobs_total,
        "status_counts": status_counts,
        "funnel": {"jobs": jobs_total, "matched": matched, "approved": c("approved"),
                   "sent": sent, "interviewing": interviewing, "rejected": rejected},
        "rates": {"send_rate": send_rate, "interview_rate": interview_rate,
                  "engage_rate": engage_rate, "reply_rate": reply_rate,
                  "total_convs": total_convs, "engaged": engaged,
                  "replied_by_hr": replied_by_hr, "conv_active": conv_active},
        "by_status": by_status,
        "recent": [{"title": a["title"], "company": a["company"], "sent_at": a["sent_at"]}
                   for a in recent[:12]],
    }


# ----------------------------- blacklist ------------------------------------
def add_blacklist(kind: str, value: str, reason: str = "", source: str = "manual",
                  until_days: int | None = None) -> dict:
    conn = get_conn()
    until = None
    if until_days:
        until = (datetime.now(timezone.utc) + timedelta(days=until_days)).isoformat()
    conn.execute(
        "INSERT INTO blacklist (kind, value, reason, source, until, created_at) "
        "VALUES (?,?,?,?,?,?)",
        (_norm(kind) or "company", (value or "").strip(), reason, source, until, _now()),
    )
    conn.commit()
    return _row(conn, "SELECT * FROM blacklist WHERE id=last_insert_rowid()")


def list_blacklist(active_only: bool = True) -> list[dict]:
    conn = get_conn()
    if active_only:
        rows = _rows(conn, "SELECT * FROM blacklist WHERE until IS NULL OR until > ? ORDER BY id DESC",
                     (_now(),))
    else:
        rows = _rows(conn, "SELECT * FROM blacklist ORDER BY id DESC")
    return rows


def is_blacklisted(kind: str, value: str) -> bool:
    if not value:
        return False
    v = _norm(value)
    for r in list_blacklist(active_only=True):
        if (r.get("kind") or "") != _norm(kind):
            continue
        bv = _norm(r.get("value", ""))
        if bv and (v in bv or bv in v):
            return True
    return False


def is_company_blacklisted(name: str) -> bool:
    """Company blacklist uses substring match (handles 分公司 / 全称差异)."""
    if not name:
        return False
    n = _norm(name)
    for r in list_blacklist(active_only=True):
        if (r.get("kind") or "") != "company":
            continue
        bv = _norm(r.get("value", ""))
        if bv and (bv in n or n in bv):
            return True
    return False


def remove_blacklist(bl_id: int) -> None:
    conn = get_conn()
    conn.execute("DELETE FROM blacklist WHERE id=?", (bl_id,))
    conn.commit()


def prune_blacklist() -> int:
    """Drop expired entries (companies blacklisted for a temporary window)."""
    conn = get_conn()
    cur = conn.execute("DELETE FROM blacklist WHERE until IS NOT NULL AND until < ?", (_now(),))
    conn.commit()
    return cur.rowcount


# ------------------------------ chat ----------------------------------------
def get_or_create_conversation(platform: str, recruiter_name: str, job_title: str,
                               company: str, job_id: int | None = None) -> dict:
    conn = get_conn()
    row = _row(
        conn,
        """SELECT * FROM conversations
           WHERE platform=? AND recruiter_name=? AND job_title=? AND company=?""",
        (platform, recruiter_name, job_title, company),
    )
    if row is None:
        conn.execute(
            """INSERT INTO conversations
               (platform, job_id, recruiter_name, recruiter_title, job_title, company,
                status, last_message_at, created_at, updated_at)
               VALUES (?,?,?,?,?,?, 'active', ?,?,?)""",
            (platform, job_id, recruiter_name, "", job_title, company,
             _now(), _now(), _now()),
        )
        conn.commit()
        row = _row(
            conn,
            """SELECT * FROM conversations
               WHERE platform=? AND recruiter_name=? AND job_title=? AND company=?""",
            (platform, recruiter_name, job_title, company),
        )
    return row


def update_conversation(conv_id: int, data: dict) -> dict:
    conn = get_conn()
    data["updated_at"] = _now()
    conn.execute(
        "UPDATE conversations SET %s WHERE id=?" % ", ".join(f"{c}=?" for c in data),
        tuple(data.values()) + (conv_id,),
    )
    conn.commit()
    return _row(get_conn(), "SELECT * FROM conversations WHERE id=?", (conv_id,))


def list_conversations(status: str | None = None) -> list[dict]:
    conn = get_conn()
    if status:
        rows = _rows(
            conn,
            "SELECT * FROM conversations WHERE status=? ORDER BY last_message_at DESC",
            (status,),
        )
    else:
        rows = _rows(conn, "SELECT * FROM conversations ORDER BY last_message_at DESC")
    # attach unread inbound count
    for r in rows:
        r["unread"] = _row(
            conn,
            """SELECT COUNT(*) c FROM messages
               WHERE conversation_id=? AND direction='inbound' AND status='received'""",
            (r["id"],),
        )["c"]
        r["job"] = _row(conn, "SELECT title, company FROM jobs WHERE id=?", (r.get("job_id"),)) if r.get("job_id") else None
    return rows


def get_conversation(conv_id: int) -> Optional[dict]:
    return _row(get_conn(), "SELECT * FROM conversations WHERE id=?", (conv_id,))


def list_messages(conv_id: int) -> list[dict]:
    conn = get_conn()
    rows = _rows(
        conn,
        "SELECT * FROM messages WHERE conversation_id=? ORDER BY id ASC",
        (conv_id,),
    )
    for r in rows:
        r["meta"] = json.loads(r.get("meta") or "{}")
    return rows


def add_message(conv_id: int, direction: str, text: str, status: str = "received",
                reply_to: int | None = None, meta: dict | None = None) -> dict:
    conn = get_conn()
    conn.execute(
        """INSERT INTO messages
           (conversation_id, direction, text, status, reply_to, meta, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?)""",
        (conv_id, direction, text, status, reply_to,
         json.dumps(meta or {}, ensure_ascii=False), _now(), _now()),
    )
    conn.execute(
        "UPDATE conversations SET last_message_at=?, updated_at=? WHERE id=?",
        (_now(), _now(), conv_id),
    )
    conn.commit()
    return _row(get_conn(), "SELECT * FROM messages WHERE id=last_insert_rowid()")


def update_message(msg_id: int, data: dict) -> dict:
    conn = get_conn()
    data["updated_at"] = _now()
    conn.execute(
        "UPDATE messages SET %s WHERE id=?" % ", ".join(f"{c}=?" for c in data),
        tuple(data.values()) + (msg_id,),
    )
    conn.commit()
    return _row(get_conn(), "SELECT * FROM messages WHERE id=?", (msg_id,))


def pending_outbound(conv_id: int) -> Optional[dict]:
    """The next approved outbound message waiting to be typed into the platform."""
    conn = get_conn()
    row = _row(
        conn,
        """SELECT * FROM messages
           WHERE conversation_id=? AND direction='outbound' AND status='approved'
           ORDER BY id ASC LIMIT 1""",
        (conv_id,),
    )
    if row:
        row["meta"] = json.loads(row.get("meta") or "{}")
    return row


# ------------------------------ settings -------------------------------------
def get_settings() -> dict:
    conn = get_conn()
    rows = _rows(conn, "SELECT key, value FROM settings")
    out: dict[str, Any] = {}
    for r in rows:
        try:
            out[r["key"]] = json.loads(r["value"])
        except (json.JSONDecodeError, TypeError):
            out[r["key"]] = r["value"]
    return out


def set_setting(key: str, value: Any) -> None:
    conn = get_conn()
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, json.dumps(value, ensure_ascii=False)),
    )
    conn.commit()


def init_db_settings() -> None:
    """Seed settings from config/defaults.json if empty."""
    import json as _json
    if get_settings():
        return
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config", "defaults.json")
    with open(path, "r", encoding="utf-8") as f:
        defaults = _json.load(f)
    for k, v in defaults.items():
        set_setting(k, v)
