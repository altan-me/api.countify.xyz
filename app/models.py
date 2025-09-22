"""
Database models and operations for the Counter API.
"""
import os
import sqlite3
import secrets
import re
from datetime import datetime, timezone, timedelta
from werkzeug.security import generate_password_hash
try:
    from argon2 import PasswordHasher
except Exception:
    PasswordHasher = None

from .utils import connect_db, generate_public_id, get_db, get_client_ip


def init_db():
    """Initialize the database with required tables."""
    try:
        with connect_db() as db:
            db.execute('''
                CREATE TABLE IF NOT EXISTS counters (
                    id TEXT PRIMARY KEY,
                    count INTEGER NOT NULL DEFAULT 0,
                    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    owner TEXT
                )
            ''')
            db.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    username TEXT PRIMARY KEY,
                    password_hash TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    public_id TEXT
                )
            ''')
            db.execute('''
                CREATE TABLE IF NOT EXISTS auth_attempts (
                    key TEXT PRIMARY KEY,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    last_attempt TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    locked_until TIMESTAMP
                )
            ''')
            
            # Lightweight migration: add owner/public_id columns and restructure counters table if needed
            try:
                cur = db.cursor()
                cur.execute("PRAGMA table_info(counters)")
                cols_info = cur.fetchall()
                colnames = {row[1] for row in cols_info}  # name is at index 1
                if 'owner' not in colnames:
                    cur.execute('ALTER TABLE counters ADD COLUMN owner TEXT')
                
                # Schema v2: introduce stable unique key and allow duplicate names per owner
                if 'counter_key' not in colnames:
                    db.execute('''
                        CREATE TABLE IF NOT EXISTS counters_new (
                            counter_key TEXT PRIMARY KEY,
                            id TEXT NOT NULL,
                            count INTEGER NOT NULL DEFAULT 0,
                            last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            owner TEXT,
                            UNIQUE(owner, id)
                        )
                    ''')
                    # Migrate existing rows
                    cur2 = db.cursor()
                    cur2.execute('SELECT id, count, last_updated, owner FROM counters')
                    rows = cur2.fetchall() or []
                    for row in rows:
                        legacy_name = row['id']
                        owner_val = row['owner']
                        key_val = f"{(owner_val or '')}|{legacy_name}"
                        db.execute(
                            'INSERT OR REPLACE INTO counters_new (counter_key, id, count, last_updated, owner) VALUES (?, ?, ?, ?, ?)',
                            (key_val, legacy_name, row['count'], row['last_updated'], owner_val),
                        )
                    db.execute('DROP TABLE counters')
                    db.execute('ALTER TABLE counters_new RENAME TO counters')
                    db.execute('CREATE INDEX IF NOT EXISTS idx_counters_owner ON counters(owner)')
                
                # Ensure users.public_id exists and is populated with lowercase hex slug
                cur.execute("PRAGMA table_info(users)")
                user_cols = {row[1] for row in cur.fetchall()}
                if 'public_id' not in user_cols:
                    cur.execute('ALTER TABLE users ADD COLUMN public_id TEXT')
                
                # Backfill any missing public_id values
                cur.execute('SELECT username, public_id FROM users')
                for u in cur.fetchall() or []:
                    current_pid = u['public_id'] or ''
                    # Accept only lowercase hex slugs, else replace
                    if not re.fullmatch(r'[0-9a-f]{8,12}', current_pid):
                        new_pid = generate_public_id()
                        # Ensure uniqueness
                        while True:
                            try:
                                db.execute('UPDATE users SET public_id = ? WHERE username = ?', (new_pid, u['username']))
                                break
                            except sqlite3.IntegrityError:
                                new_pid = generate_public_id()
                    elif current_pid != current_pid.lower():
                        # Normalize to lowercase if needed
                        db.execute('UPDATE users SET public_id = ? WHERE username = ?', (current_pid.lower(), u['username']))
                db.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_users_public_id ON users(public_id)')
            except Exception:
                # Non-fatal; continue without interrupting app startup
                pass
            db.commit()
    except Exception as e:
        print(f"Error initializing the database: {e}")


def ensure_default_admin():
    """Ensure a default admin user exists."""
    try:
        with connect_db() as db:
            cursor = db.cursor()
            cursor.execute('SELECT COUNT(*) as c FROM users')
            row = cursor.fetchone()
            num_users = row['c'] if row else 0
            if num_users == 0:
                require_admin = os.getenv('REQUIRE_ADMIN_PASSWORD', '0') == '1'
                provided = os.getenv('ADMIN_PASSWORD')
                if require_admin and not provided:
                    print("ERROR: ADMIN_PASSWORD is required on first run (REQUIRE_ADMIN_PASSWORD=1). Refusing to start without it.")
                    raise SystemExit(1)
                password = provided or secrets.token_urlsafe(16)
                # Use Argon2 if available, else PBKDF2
                try:
                    password_hash = PasswordHasher().hash(password)
                except Exception:
                    password_hash = generate_password_hash(password)
                
                # Generate a unique public_id for the admin user
                public_id = generate_public_id()
                # Ensure uniqueness (though unlikely to collide)
                while True:
                    try:
                        cursor.execute('INSERT INTO users (username, password_hash, public_id) VALUES (?, ?, ?)', ('admin', password_hash, public_id))
                        break
                    except sqlite3.IntegrityError:
                        public_id = generate_public_id()
                
                db.commit()
                print("Created default admin user 'admin'")
                if not provided:
                    print(f"Temporary admin password: {password}")
    except Exception as e:
        print(f"Error ensuring default admin: {e}")


def get_user_by_public_id(public_id: str) -> str | None:
    """Get username by public ID."""
    try:
        db = get_db()
        cur = db.cursor()
        cur.execute('SELECT username FROM users WHERE public_id = ?', (public_id.lower(),))
        row = cur.fetchone()
        return row['username'] if row else None
    except Exception:
        return None


# Rate limiting functions
def login_key(username: str) -> str:
    """Generate a login key for rate limiting."""
    return f"{(username or '').lower()}|{get_client_ip()}"


def is_locked(db, key: str) -> tuple[bool, datetime | None]:
    """Check if a login key is currently locked."""
    cursor = db.cursor()
    cursor.execute('SELECT locked_until, last_attempt, attempts FROM auth_attempts WHERE key = ?', (key,))
    row = cursor.fetchone()
    if not row:
        return False, None
    locked_until = row['locked_until']
    if locked_until:
        try:
            # sqlite returns str timestamp by default when using CURRENT_TIMESTAMP
            if isinstance(locked_until, str):
                ts_str = locked_until.replace('Z', '+00:00')
                locked_dt = datetime.fromisoformat(ts_str)
            else:
                locked_dt = locked_until
            if locked_dt and locked_dt.tzinfo is None:
                locked_dt = locked_dt.replace(tzinfo=timezone.utc)
        except Exception:
            locked_dt = None
        if locked_dt and locked_dt > datetime.now(timezone.utc):
            return True, locked_dt
    return False, None


def register_failure(db, key: str) -> bool:
    """Record a failed attempt. Returns True if now locked."""
    MAX_ATTEMPTS = int(os.getenv('LOGIN_MAX_ATTEMPTS', '5'))
    WINDOW_MINUTES = int(os.getenv('LOGIN_WINDOW_MINUTES', '10'))
    LOCK_MINUTES = int(os.getenv('LOGIN_LOCK_MINUTES', '10'))
    
    cursor = db.cursor()
    cursor.execute('SELECT attempts, last_attempt FROM auth_attempts WHERE key = ?', (key,))
    row = cursor.fetchone()
    now = datetime.now(timezone.utc)
    window_start = now - timedelta(minutes=WINDOW_MINUTES)
    if not row:
        cursor.execute('INSERT INTO auth_attempts (key, attempts, last_attempt) VALUES (?, ?, ?)', (key, 1, now.isoformat()))
        db.commit()
        return False
    attempts = row['attempts'] or 0
    last_attempt = row['last_attempt']
    try:
        if isinstance(last_attempt, str):
            last_dt = datetime.fromisoformat(last_attempt.replace('Z', '+00:00'))
        else:
            last_dt = last_attempt
        if last_dt and last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=timezone.utc)
    except Exception:
        last_dt = now
    if last_dt < window_start:
        attempts = 0
    attempts += 1
    locked_until = None
    if attempts >= MAX_ATTEMPTS:
        locked_until = (now + timedelta(minutes=LOCK_MINUTES)).isoformat()
    cursor.execute('UPDATE auth_attempts SET attempts = ?, last_attempt = ?, locked_until = ? WHERE key = ?', (attempts, now.isoformat(), locked_until, key))
    db.commit()
    return locked_until is not None


def clear_attempts(db, key: str) -> None:
    """Clear failed login attempts for a key."""
    cursor = db.cursor()
    cursor.execute('DELETE FROM auth_attempts WHERE key = ?', (key,))
    db.commit()