"""
Professional database layer for AutoClipper SaaS
"""
import sqlite3
import hashlib
import secrets
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, List, Dict

DB_PATH = Path(__file__).parent.parent / "autoclipper.db"


def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_conn()
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            google_id TEXT UNIQUE,
            name TEXT,
            picture TEXT,
            plan TEXT DEFAULT 'free',
            tokens INTEGER DEFAULT 50,
            tokens_used INTEGER DEFAULT 0,
            created_at TEXT NOT NULL,
            last_login TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS waitlist (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            name TEXT,
            created_at TEXT NOT NULL
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            url TEXT,
            status TEXT DEFAULT 'pending',
            clips_count INTEGER DEFAULT 0,
            tokens_used INTEGER DEFAULT 0,
            created_at TEXT NOT NULL
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS clips (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id INTEGER,
            file_path TEXT,
            title TEXT,
            drive_link TEXT,
            created_at TEXT NOT NULL
        )
    """)

    # Default settings
    defaults = {
        "prelaunch_mode": "true",
        "banner_text": "🚀 Pre-launch: Join the waitlist for early access + free tokens",
        "banner_active": "true",
        "pricing_free_tokens": "50",
        "pricing_pro_monthly": "29",
        "pricing_pro_6month": "149",
        "pricing_pro_yearly": "249",
        "pricing_pro_monthly_usd": "12",
        "pricing_pro_6month_usd": "60",
        "pricing_pro_yearly_usd": "99",
        "currency_mode": "auto",

        "admin_email": "admin@autoclipper.ai",
        "hero_title": "Turn Any YouTube Video Into Viral Clips",
        "hero_subtitle": "AI-powered clipping for creators and agencies. Paste a link. Get vertical clips with captions in seconds.",
    }
    for k, v in defaults.items():
        c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v))

    conn.commit()
    conn.close()


def get_setting(key: str, default: str = "") -> str:
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT value FROM settings WHERE key = ?", (key,))
    row = c.fetchone()
    conn.close()
    return row["value"] if row else default


def set_setting(key: str, value: str):
    conn = get_conn()
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
    conn.commit()
    conn.close()


def get_all_settings() -> Dict:
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT key, value FROM settings")
    rows = {r["key"]: r["value"] for r in c.fetchall()}
    conn.close()
    return rows


def create_or_get_user(email: str, google_id: str = None, name: str = "", picture: str = "") -> Dict:
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE email = ?", (email,))
    row = c.fetchone()
    if row:
        c.execute("UPDATE users SET last_login = ?, name = COALESCE(?, name), picture = COALESCE(?, picture) WHERE id = ?",
                  (datetime.utcnow().isoformat(), name or None, picture or None, row["id"]))
        conn.commit()
        user = dict(row)
    else:
        tokens = int(get_setting("pricing_free_tokens", "50") or 50)
        c.execute("""
            INSERT INTO users (email, google_id, name, picture, tokens, created_at, last_login)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (email, google_id, name, picture, tokens, datetime.utcnow().isoformat(), datetime.utcnow().isoformat()))
        conn.commit()
        c.execute("SELECT * FROM users WHERE id = ?", (c.lastrowid,))
        user = dict(c.fetchone())
    conn.close()
    return user


def get_user(user_id: int) -> Optional[Dict]:
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE id = ?", (user_id,))
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None


def use_tokens(user_id: int, amount: int) -> bool:
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT tokens FROM users WHERE id = ?", (user_id,))
    row = c.fetchone()
    if not row or row["tokens"] < amount:
        conn.close()
        return False
    c.execute("UPDATE users SET tokens = tokens - ?, tokens_used = tokens_used + ? WHERE id = ?",
              (amount, amount, user_id))
    conn.commit()
    conn.close()
    return True


def add_tokens(user_id: int, amount: int):
    conn = get_conn()
    c = conn.cursor()
    c.execute("UPDATE users SET tokens = tokens + ? WHERE id = ?", (amount, user_id))
    conn.commit()
    conn.close()


def add_waitlist(email: str, name: str = ""):
    conn = get_conn()
    try:
        c = conn.cursor()
        c.execute("INSERT INTO waitlist (email, name, created_at) VALUES (?, ?, ?)",
                  (email, name, datetime.utcnow().isoformat()))
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()


def get_waitlist() -> List[Dict]:
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT * FROM waitlist ORDER BY created_at DESC")
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


init_db()

# Additional tables for full control
def upgrade_db():
    conn = get_conn()
    c = conn.cursor()
    
    # Add referral columns to users if not exist
    try:
        c.execute("ALTER TABLE users ADD COLUMN referral_code TEXT UNIQUE")
        c.execute("ALTER TABLE users ADD COLUMN referred_by TEXT")
        c.execute("ALTER TABLE users ADD COLUMN referral_bonus_tokens INTEGER DEFAULT 0")
    except Exception:
        pass

    # Add missing columns to clips table
    for col, typedef in [
        ("start_sec", "REAL DEFAULT 0"),
        ("end_sec", "REAL DEFAULT 0"),
        ("reason", "TEXT"),
    ]:
        try:
            c.execute(f"ALTER TABLE clips ADD COLUMN {col} {typedef}")
        except Exception:
            pass
    
    c.execute("""
        CREATE TABLE IF NOT EXISTS coupons (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT UNIQUE NOT NULL,
            discount_percent INTEGER DEFAULT 0,
            bonus_tokens INTEGER DEFAULT 0,
            max_uses INTEGER DEFAULT 100,
            used_count INTEGER DEFAULT 0,
            active INTEGER DEFAULT 1,
            expires_at TEXT,
            created_at TEXT NOT NULL
        )
    """)
    
    c.execute("""
        CREATE TABLE IF NOT EXISTS banners (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            image_url TEXT,
            link_url TEXT,
            button_text TEXT,
            active INTEGER DEFAULT 1,
            position TEXT DEFAULT 'hero',
            created_at TEXT NOT NULL
        )
    """)
    
    c.execute("""
        CREATE TABLE IF NOT EXISTS coupon_uses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            coupon_code TEXT,
            user_id INTEGER,
            used_at TEXT NOT NULL,
            UNIQUE(coupon_code, user_id)
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS referrals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            referrer_id INTEGER,
            referred_email TEXT,
            bonus_given INTEGER DEFAULT 0,
            created_at TEXT NOT NULL
        )
    """)
    
    # Extra settings
    extra = {
        "referral_enabled": "true",
        "referral_bonus_tokens": "25",
        "referral_button_text": "Start Referring",
        "waitlist_headline": "Join the Waitlist",
        "waitlist_subtext": "Be first to get access + free tokens",
    }
    for k, v in extra.items():
        c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v))
    
    conn.commit()
    conn.close()

upgrade_db()


def add_clip(job_id: int, file_path: str, title: str = "", start: float = 0, end: float = 0):
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        INSERT INTO clips (job_id, file_path, title, start_sec, end_sec, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (job_id, file_path, title, start, end, datetime.utcnow().isoformat()))
    conn.commit()
    conn.close()


def get_user_clips(user_id: int):
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        SELECT c.* FROM clips c
        JOIN jobs j ON c.job_id = j.id
        WHERE j.user_id = ?
        ORDER BY c.id DESC
    """, (user_id,))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def create_job_record(user_id: int, url: str) -> int:
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        INSERT INTO jobs (user_id, url, status, created_at)
        VALUES (?, ?, 'done', ?)
    """, (user_id, url, datetime.utcnow().isoformat()))
    job_id = c.lastrowid
    conn.commit()
    conn.close()
    return job_id
