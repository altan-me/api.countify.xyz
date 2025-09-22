import os
import sqlite3
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from functools import wraps
from flask import Flask, jsonify, request, make_response, render_template, session, redirect, url_for, g
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.middleware.proxy_fix import ProxyFix
from urllib.parse import urlparse, urljoin
try:
    from argon2 import PasswordHasher  # type: ignore
except Exception:  # pragma: no cover - optional dependency during linting
    PasswordHasher = None  # type: ignore
import hashlib
import time
import re


app = Flask(__name__)
DATABASE = os.getenv('DATABASE', 'counters.db')

def generate_public_id() -> str:
    """Generate a short, lowercase, URL-friendly public id.
    Uses 40 bits of randomness (10 hex chars)."""
    return uuid.uuid4().hex[:10]

def connect_db():
    """Create a new SQLite connection with sane defaults for web usage."""
    db = sqlite3.connect(
        DATABASE,
        timeout=30.0,
        check_same_thread=False,
    )
    # Improve concurrency and reliability for mixed read/write load
    try:
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('PRAGMA busy_timeout=30000')
        db.execute('PRAGMA synchronous=NORMAL')
        db.execute('PRAGMA foreign_keys=ON')
    except Exception:
        pass
    db.row_factory = sqlite3.Row
    return db

def get_db():
    """Return a per-request SQLite connection stored on Flask's g."""
    if not hasattr(g, 'db') or g.db is None:
        g.db = connect_db()
    return g.db

def init_db():
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
            except Exception as _e:
                # Non-fatal; continue without interrupting app startup
                pass
            db.commit()
    except Exception as e:
        print(f"Error initializing the database: {e}")


def ensure_default_admin():
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

def create_app():
    app = Flask(__name__)

    # Trust the upstream proxy (e.g., Nginx) for client information
    try:
        trusted_hops = int(os.getenv('TRUSTED_PROXY_DEPTH', '1'))
    except Exception:
        trusted_hops = 1
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=trusted_hops, x_proto=trusted_hops, x_host=trusted_hops, x_port=trusted_hops, x_prefix=trusted_hops)

    app.config.update(
        SECRET_KEY=os.getenv('SECRET_KEY') or secrets.token_bytes(32),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SECURE=bool(int(os.getenv('SESSION_COOKIE_SECURE', '0'))),
        SESSION_COOKIE_SAMESITE=os.getenv('SESSION_COOKIE_SAMESITE', 'Strict'),
        SESSION_REFRESH_EACH_REQUEST=True,
    )
    # Rolling session expiration of 2 days
    app.permanent_session_lifetime = timedelta(days=2)
    # Fake password hash to avoid username enumeration timing differences
    app.config['FAKE_PASSWORD_HASH'] = generate_password_hash('notused')

    # Strong password hasher (Argon2id). Also keep a fake Argon2 hash for timing uniformity
    password_hasher = PasswordHasher() if PasswordHasher else None
    app.config['PASSWORD_HASHER'] = password_hasher
    try:
        app.config['FAKE_ARGON2_HASH'] = password_hasher.hash('notused') if password_hasher else None
    except Exception:
        # Fallback in the unlikely event hashing fails at startup
        app.config['FAKE_ARGON2_HASH'] = None
    
    # Database initialization
    init_db()
    ensure_default_admin()

    @app.teardown_appcontext
    def close_db(exception=None):
        db = g.pop('db', None)
        if db is not None:
            try:
                db.close()
            except Exception:
                pass

    # Helpers
    def get_or_create_csrf_token() -> str:
        token = session.get('csrf_token')
        if not token:
            token = secrets.token_urlsafe(16)
            session['csrf_token'] = token
        return token

    def validate_csrf_token(token: str) -> bool:
        return token and session.get('csrf_token') and secrets.compare_digest(token, session['csrf_token'])

    def login_required(view_func):
        @wraps(view_func)
        def wrapped(*args, **kwargs):
            if not session.get('user_id'):
                return redirect(f'/login?next={request.path}')
            return view_func(*args, **kwargs)
        return wrapped

    def get_client_ip() -> str:
        # With ProxyFix enabled, access_route[0] reflects the client IP from the trusted proxy
        return (request.access_route[0] if request.access_route else request.remote_addr) or 'unknown'

    def is_safe_url(target: str) -> bool:
        try:
            ref = urlparse(request.host_url)
            test = urlparse(urljoin(request.host_url, target or ''))
            return test.scheme in ('http', 'https') and ref.netloc == test.netloc and test.path.startswith('/')
        except Exception:
            return False

    def login_key(username: str) -> str:
        return f"{(username or '').lower()}|{get_client_ip()}"

    MAX_ATTEMPTS = int(os.getenv('LOGIN_MAX_ATTEMPTS', '5'))
    WINDOW_MINUTES = int(os.getenv('LOGIN_WINDOW_MINUTES', '10'))
    LOCK_MINUTES = int(os.getenv('LOGIN_LOCK_MINUTES', '10'))

    def is_locked(db, key: str) -> tuple[bool, datetime | None]:
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
        cursor = db.cursor()
        cursor.execute('DELETE FROM auth_attempts WHERE key = ?', (key,))
        db.commit()

    def get_user_by_public_id(public_id: str) -> str | None:
        try:
            db = get_db()
            cur = db.cursor()
            cur.execute('SELECT username FROM users WHERE public_id = ?', (public_id.lower(),))
            row = cur.fetchone()
            return row['username'] if row else None
        except Exception:
            return None

    @app.before_request
    def load_current_user():
        user_id = session.get('user_id')
        if user_id:
            try:
                db = get_db()
                cur = db.cursor()
                cur.execute('SELECT public_id FROM users WHERE username = ?', (user_id,))
                row = cur.fetchone()
                public_id = row['public_id'] if row else None
            except Exception:
                public_id = None
            g.user = {'username': user_id, 'public_id': public_id}
        else:
            g.user = None
        # Ensure a CSRF token exists for safe methods so templates can render logout forms
        if request.method in ('GET', 'HEAD'):
            token = session.get('csrf_token')
            if not token:
                session['csrf_token'] = secrets.token_urlsafe(16)
        # Make logged-in sessions permanent so they honor rolling 2-day expiry
        if g.user:
            session.permanent = True

        # Bind session to a lightweight user-agent fingerprint to reduce hijacking risk
        try:
            current_ua = (request.headers.get('User-Agent') or '').encode()
            current_ua_hash = hashlib.sha256(current_ua).hexdigest()
            stored_ua_hash = session.get('ua_hash')
            if g.user and stored_ua_hash and stored_ua_hash != current_ua_hash:
                session.clear()
                g.user = None
            elif g.user and not stored_ua_hash:
                session['ua_hash'] = current_ua_hash
        except Exception:
            pass

        # Generate a per-request CSP nonce for inline scripts
        try:
            g.csp_nonce = secrets.token_urlsafe(16)
        except Exception:
            g.csp_nonce = None

    @app.after_request
    def add_security_headers(resp):
        # Core headers
        resp.headers.setdefault('X-Content-Type-Options', 'nosniff')
        resp.headers.setdefault('X-Frame-Options', 'DENY')
        resp.headers.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
        resp.headers.setdefault('Permissions-Policy', 'geolocation=()')

        # Content Security Policy with nonce for inline scripts and allowance for jsdelivr CDN
        nonce = getattr(g, 'csp_nonce', None)
        script_src = ["'self'", 'https://cdn.jsdelivr.net']
        if nonce:
            script_src.append(f"'nonce-{nonce}'")
        csp = (
            "default-src 'self'; "
            f"script-src {' '.join(script_src)}; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; "
            "connect-src 'self'; "
            "font-src 'self' data:; "
            "object-src 'none'; "
            "base-uri 'self'; "
            "frame-ancestors 'none'"
        )
        resp.headers.setdefault('Content-Security-Policy', csp)

        # Enable HSTS only if explicitly requested and (ideally) TLS is used at the proxy
        if bool(int(os.getenv('ENABLE_HSTS', '0'))):
            resp.headers.setdefault('Strict-Transport-Security', 'max-age=31536000; includeSubDomains; preload')
        return resp

    def get_csrf_from_request() -> str | None:
        header_token = request.headers.get('X-CSRF-Token')
        if header_token:
            return header_token
        if request.form:
            return request.form.get('csrf_token')
        payload = request.get_json(silent=True) or {}
        return payload.get('csrf_token')

    def _parse_key_and_name(param: str) -> tuple[str, str, str | None]:
        """Return (counter_key, name, owner_or_none) from API path param.
        If param includes a '|', treat as full key: 'owner|name'. Empty owner is allowed.
        Otherwise, assume global (no owner) with key '|name'.
        """
        if '|' in param:
            owner_part, name_part = param.split('|', 1)
            return param, name_part, (owner_part if owner_part != '' else None)
        # Default to namespaced by the logged-in user if present; otherwise global
        owner_part = session.get('user_id')
        if owner_part:
            return f"{owner_part}|{param}", param, owner_part
        return f"|{param}", param, None

    @app.route('/get-total/<counter_id>', methods=['GET'])
    def get_total(counter_id):
        db = get_db()
        cursor = db.cursor()
        counter_key, name, owner_val = _parse_key_and_name(counter_id)
        cursor.execute('SELECT count, last_updated FROM counters WHERE counter_key = ?', (counter_key,))
        row = cursor.fetchone()
        if row:
            return jsonify(id=name, count=row['count'], last_updated=row['last_updated'])
        else:
            cursor.execute('INSERT INTO counters (counter_key, id, count, owner) VALUES (?, ?, 0, ?)', (counter_key, name, owner_val))
            db.commit()
            ts = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
            return jsonify(id=name, count=0, last_updated=ts)

    @app.route('/get-total/<user_hash>/<counter_id>', methods=['GET'])
    def get_total_for_user(user_hash: str, counter_id: str):
        owner = get_user_by_public_id(user_hash)
        if not owner:
            return make_response(jsonify({'error': 'Unknown user'}), 404)
        db = get_db()
        cursor = db.cursor()
        counter_key = f"{owner}|{counter_id}"
        cursor.execute('SELECT count, last_updated FROM counters WHERE counter_key = ?', (counter_key,))
        row = cursor.fetchone()
        if row:
            return jsonify(id=counter_id, count=row['count'], last_updated=row['last_updated'])
        cursor.execute('INSERT INTO counters (counter_key, id, count, owner) VALUES (?, ?, 0, ?)', (counter_key, counter_id, owner))
        db.commit()
        ts = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
        return jsonify(id=counter_id, count=0, last_updated=ts)

    @app.route('/increment/<counter_id>', methods=['POST'])
    def increment_by_one(counter_id):
        db = get_db()
        cursor = db.cursor()
        counter_key, name, owner_val = _parse_key_and_name(counter_id)
        cursor.execute('SELECT count FROM counters WHERE counter_key = ?', (counter_key,))
        row = cursor.fetchone()
        if row:
            new_count = row['count'] + 1
            cursor.execute('UPDATE counters SET count = ?, last_updated = CURRENT_TIMESTAMP WHERE counter_key = ?', (new_count, counter_key))
        else:
            new_count = 1
            cursor.execute('INSERT INTO counters (counter_key, id, count, owner) VALUES (?, ?, ?, ?)', (counter_key, name, new_count, owner_val))
        db.commit()
        ts = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
        return jsonify(id=name, count=new_count, last_updated=ts)

    @app.route('/increment/<user_hash>/<counter_id>', methods=['POST'])
    def increment_by_one_for_user(user_hash: str, counter_id: str):
        owner = get_user_by_public_id(user_hash)
        if not owner:
            return make_response(jsonify({'error': 'Unknown user'}), 404)
        db = get_db()
        cursor = db.cursor()
        counter_key = f"{owner}|{counter_id}"
        cursor.execute('SELECT count FROM counters WHERE counter_key = ?', (counter_key,))
        row = cursor.fetchone()
        if row:
            new_count = row['count'] + 1
            cursor.execute('UPDATE counters SET count = ?, last_updated = CURRENT_TIMESTAMP WHERE counter_key = ?', (new_count, counter_key))
        else:
            new_count = 1
            cursor.execute('INSERT INTO counters (counter_key, id, count, owner) VALUES (?, ?, ?, ?)', (counter_key, counter_id, new_count, owner))
        db.commit()
        ts = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
        return jsonify(id=counter_id, count=new_count, last_updated=ts)

    @app.route('/stats', methods=['GET'])
    def get_stats():
        db = get_db()
        cursor = db.cursor()

        # If a user is logged in, limit stats to their counters
        owner = session.get('user_id')
        params = ()
        where_owner = ''
        if owner:
            where_owner = 'WHERE owner = ?'
            params = (owner,)

        cursor.execute(f'SELECT COUNT(*) AS total_counters FROM counters {where_owner}', params)
        total_counters_row = cursor.fetchone()
        total_counters = total_counters_row['total_counters'] if total_counters_row else 0

        cursor.execute(f'SELECT COALESCE(SUM(count), 0) AS total_count FROM counters {where_owner}', params)
        total_count_row = cursor.fetchone()
        total_count = total_count_row['total_count'] if total_count_row else 0

        cursor.execute(f'SELECT MAX(last_updated) AS last_activity FROM counters {where_owner}', params)
        last_activity_row = cursor.fetchone()
        last_activity = last_activity_row['last_activity'] if last_activity_row else None

        cursor.execute(
            f'''
            SELECT id, count, last_updated
            FROM counters
            {where_owner}
            ORDER BY count DESC
            LIMIT 10
            ''',
            params,
        )
        top_counters_rows = cursor.fetchall() or []
        top_counters = [
            {
                'id': row['id'],
                'count': row['count'],
                'last_updated': row['last_updated'],
            }
            for row in top_counters_rows
        ]

        cursor.execute(
            f'''
            SELECT id, count, last_updated
            FROM counters
            {where_owner}
            ORDER BY last_updated DESC
            LIMIT 10
            ''',
            params,
        )
        recent_rows = cursor.fetchall() or []
        recently_updated = [
            {
                'id': row['id'],
                'count': row['count'],
                'last_updated': row['last_updated'],
            }
            for row in recent_rows
        ]

        return jsonify({
            'total_counters': total_counters,
            'total_count': total_count,
            'last_activity': last_activity,
            'top_counters': top_counters,
            'recently_updated': recently_updated,
        })

    @app.route('/counters', methods=['GET'])
    @login_required
    def list_counters():
        """Paginated counters list for the current user with optional search and sorting."""
        try:
            page = int(request.args.get('page', '1'))
        except Exception:
            page = 1
        try:
            page_size = int(request.args.get('page_size', '20'))
        except Exception:
            page_size = 20
        page = max(1, page)
        page_size = min(max(1, page_size), 100)

        q = (request.args.get('q') or '').strip()
        sort = (request.args.get('sort') or 'last_updated').lower()
        order = (request.args.get('order') or 'desc').lower()
        sort_map = {
            'id': 'id',
            'count': 'count',
            'last_updated': 'last_updated',
        }
        sort_col = sort_map.get(sort, 'last_updated')
        order_dir = 'ASC' if order == 'asc' else 'DESC'

        db = get_db()
        cursor = db.cursor()

        owner = session.get('user_id')
        where = 'WHERE owner = ?'
        params = [owner]
        if q:
            where += ' AND id LIKE ?'
            params.append(f"%{q}%")

        cursor.execute(f'SELECT COUNT(*) AS total FROM counters {where}', tuple(params))
        total_row = cursor.fetchone() or {'total': 0}
        total = int(total_row['total'] or 0)

        offset = (page - 1) * page_size
        cursor.execute(
            f'''SELECT id, count, last_updated
                FROM counters
                {where}
                ORDER BY {sort_col} {order_dir}
                LIMIT ? OFFSET ?''',
            tuple(params + [page_size, offset])
        )
        rows = cursor.fetchall() or []
        items = [
            {'id': r['id'], 'count': r['count'], 'last_updated': r['last_updated']}
            for r in rows
        ]

        total_pages = (total + page_size - 1) // page_size if page_size else 1
        return jsonify({
            'items': items,
            'total': total,
            'page': page,
            'page_size': page_size,
            'total_pages': total_pages,
        })

    @app.route('/counters/<counter_id>', methods=['DELETE'])
    @login_required
    def delete_counter(counter_id: str):
        token = get_csrf_from_request()
        if not validate_csrf_token(token):
            return make_response(jsonify({'error': 'Bad CSRF token'}), 400)
        counter_id = (counter_id or '').strip()
        if not counter_id:
            return make_response(jsonify({'error': 'id is required'}), 400)
        if len(counter_id) > 64 or not re.fullmatch(r'[A-Za-z0-9_-]+', counter_id):
            return make_response(jsonify({'error': 'id must be <=64 chars and only letters, numbers, _ or -'}), 400)

        owner = session.get('user_id')
        db = get_db()
        cur = db.cursor()
        cur.execute('DELETE FROM counters WHERE owner = ? AND id = ?', (owner, counter_id))
        db.commit()
        if cur.rowcount == 0:
            return make_response(jsonify({'error': 'Not found'}), 404)
        return ('', 204)

    @app.route('/', methods=['GET'])
    def landing():
        get_or_create_csrf_token()
        return render_template('landing.html')

    @app.route('/dashboard', methods=['GET'])
    @login_required
    def dashboard():
        return render_template('dashboard.html')

    @app.route('/create-counter', methods=['POST'])
    @login_required
    def create_counter():
        token = get_csrf_from_request()
        if not validate_csrf_token(token):
            return make_response(jsonify({'error': 'Bad CSRF token'}), 400)

        data = request.get_json(silent=True) or {}
        counter_id = (data.get('id') or '').strip()
        try:
            initial_count = int(data.get('initial_count') or 0)
        except Exception:
            initial_count = 0

        if not counter_id:
            return make_response(jsonify({'error': 'id is required'}), 400)
        if len(counter_id) > 64 or not re.fullmatch(r'[A-Za-z0-9_-]+', counter_id):
            return make_response(jsonify({'error': 'id must be <=64 chars and only letters, numbers, _ or -'}), 400)
        if initial_count < 0:
            return make_response(jsonify({'error': 'initial_count must be >= 0'}), 400)
        # Enforce SQLite 64-bit signed integer max to avoid overflow/undefined behavior
        if initial_count > 9223372036854775807:
            return make_response(jsonify({'error': 'initial_count is too large'}), 400)

        db = get_db()
        cursor = db.cursor()
        try:
            owner_val = session.get('user_id')
            counter_key, name, _ = _parse_key_and_name(f"{owner_val}|{counter_id}")
            if initial_count == 0:
                cursor.execute('INSERT INTO counters (counter_key, id, count, owner) VALUES (?, ?, 0, ?)', (counter_key, name, owner_val))
            else:
                cursor.execute('INSERT INTO counters (counter_key, id, count, owner) VALUES (?, ?, ?, ?)', (counter_key, name, initial_count, owner_val))
            db.commit()
        except sqlite3.IntegrityError:
            return make_response(jsonify({'error': 'Counter already exists'}), 409)

        return make_response(jsonify({'id': name, 'count': initial_count, 'message': 'created'}), 201)

    @app.route('/login', methods=['GET', 'POST'])
    def login():
        if request.method == 'GET':
            get_or_create_csrf_token()
            return render_template('login.html')

        # POST
        csrf_token = request.form.get('csrf_token', '')
        if not validate_csrf_token(csrf_token):
            return make_response(render_template('login.html', error='Invalid session. Please try again.'), 400)

        username = (request.form.get('username') or '').strip()
        password = request.form.get('password') or ''
        if not username or not password:
            # Use a generic error to avoid leaking information
            return make_response(render_template('login.html', error='Invalid username or password.'), 200)

        db = get_db()
        # Check lockout status for this username+ip
        key = login_key(username)
        locked, locked_until = is_locked(db, key)
        if locked:
            # Generic error message to avoid revealing lockout state
            return make_response(render_template('login.html', error='Invalid username or password.'), 200)
        cursor = db.cursor()
        cursor.execute('SELECT password_hash FROM users WHERE username = ?', (username,))
        row = cursor.fetchone()
        stored_hash = row['password_hash'] if row else (app.config.get('FAKE_ARGON2_HASH') or app.config['FAKE_PASSWORD_HASH'])

        # Also enforce IP-only lock to reduce username spraying
        ip_key = f"ip|{get_client_ip()}"
        locked_user, _ = is_locked(db, key)
        locked_ip, _ = is_locked(db, ip_key)
        if locked_user or locked_ip:
            return make_response(render_template('login.html', error='Invalid username or password.'), 200)

        ok = False
        used_pbkdf2 = False
        try:
            if isinstance(stored_hash, str) and stored_hash.startswith('$argon2'):
                if app.config['PASSWORD_HASHER']:
                    ok = app.config['PASSWORD_HASHER'].verify(stored_hash, password)
                else:
                    ok = False
            else:
                ok = check_password_hash(stored_hash, password)
                used_pbkdf2 = True
        except Exception:
            ok = False
        if not row or not ok:
            register_failure(db, key)
            register_failure(db, ip_key)
            # Small backoff to slow brute-force attempts
            try:
                time.sleep(0.2)
            except Exception:
                pass
            # Always return a generic error and 200 OK to avoid signaling enumeration or lockout
            return make_response(render_template('login.html', error='Invalid username or password.'), 200)

        # Successful login: migrate legacy PBKDF2 hashes to Argon2id
        try:
            if used_pbkdf2 and row and app.config['PASSWORD_HASHER']:
                new_hash = app.config['PASSWORD_HASHER'].hash(password)
                cursor.execute('UPDATE users SET password_hash = ? WHERE username = ?', (new_hash, username))
                db.commit()
        except Exception:
            pass

        session['user_id'] = username
        session.permanent = True
        # Don't rotate CSRF token after login to avoid logout issues
        # The token rotation on login was causing logout forms to have stale tokens
        # Bind UA fingerprint
        try:
            ua = (request.headers.get('User-Agent') or '').encode()
            session['ua_hash'] = hashlib.sha256(ua).hexdigest()
        except Exception:
            pass
        clear_attempts(db, key)
        clear_attempts(db, ip_key)
        next_url = request.args.get('next')
        if not next_url or not is_safe_url(next_url):
            next_url = '/dashboard'
        return redirect(next_url)

    @app.route('/logout', methods=['POST'])
    def logout():
        csrf_token = request.form.get('csrf_token', '')
        if not validate_csrf_token(csrf_token):
            return make_response('Bad CSRF token', 400)
        session.clear()
        return redirect('/')

    @app.route('/signup', methods=['GET', 'POST'])
    def signup():
        if request.method == 'GET':
            get_or_create_csrf_token()
            return render_template('signup.html')

        csrf_token = request.form.get('csrf_token', '')
        if not validate_csrf_token(csrf_token):
            return make_response(render_template('signup.html', error='Invalid session. Please try again.'), 400)

        username = (request.form.get('username') or '').strip()
        password = request.form.get('password') or ''
        confirm = request.form.get('confirm') or ''

        # Basic validation
        if not username or not password or not confirm:
            return make_response(render_template('signup.html', error='All fields are required.'), 400)
        if password != confirm:
            return make_response(render_template('signup.html', error='Passwords do not match.'), 400)
        if len(username) < 3 or len(username) > 32 or not re.fullmatch(r'[A-Za-z0-9_-]+', username):
            return make_response(render_template('signup.html', error='Username must be 3-32 chars of letters, numbers, _ or -'), 400)
        if len(password) < 8:
            return make_response(render_template('signup.html', error='Password must be at least 8 characters.'), 400)

        # Basic rate limit per IP for signups using existing attempts table
        db = get_db()
        key = f"signup|{get_client_ip()}"
        locked, _ = is_locked(db, key)
        if locked:
            return make_response(render_template('signup.html', error='Too many attempts. Try again later.'), 429)

        try:
            pid = generate_public_id()
            hasher = app.config.get('PASSWORD_HASHER')
            password_hash = hasher.hash(password) if hasher else generate_password_hash(password)
            with db:
                db.execute('INSERT INTO users (username, password_hash, public_id) VALUES (?, ?, ?)', (username, password_hash, pid))
        except sqlite3.IntegrityError:
            # Register a failure against the IP and return conflict
            register_failure(db, key)
            return make_response(render_template('signup.html', error='Username is taken.'), 409)

        # Clear failures on success and log the user in
        clear_attempts(db, key)
        session['user_id'] = username
        # Don't rotate CSRF token to avoid logout form issues
        session.permanent = True
        try:
            ua = (request.headers.get('User-Agent') or '').encode()
            session['ua_hash'] = hashlib.sha256(ua).hexdigest()
        except Exception:
            pass
        return redirect('/dashboard')

    @app.route('/increase/<counter_id>', methods=['POST'])
    def increase_by_value(counter_id):
        value = int(request.json['value'])
        db = get_db()
        cursor = db.cursor()
        counter_key, name, owner_val = _parse_key_and_name(counter_id)
        cursor.execute('SELECT count FROM counters WHERE counter_key = ?', (counter_key,))
        row = cursor.fetchone()
        if row:
            new_count = row['count'] + value
            cursor.execute('UPDATE counters SET count = ?, last_updated = CURRENT_TIMESTAMP WHERE counter_key = ?', (new_count, counter_key))
        else:
            new_count = value
            cursor.execute('INSERT INTO counters (counter_key, id, count, owner) VALUES (?, ?, ?, ?)', (counter_key, name, new_count, owner_val))
        db.commit()
        ts = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
        return jsonify(id=name, count=new_count, last_updated=ts)

    @app.route('/increase/<user_hash>/<counter_id>', methods=['POST'])
    def increase_by_value_for_user(user_hash: str, counter_id: str):
        value = int(request.json['value'])
        owner = get_user_by_public_id(user_hash)
        if not owner:
            return make_response(jsonify({'error': 'Unknown user'}), 404)
        db = get_db()
        cursor = db.cursor()
        counter_key = f"{owner}|{counter_id}"
        cursor.execute('SELECT count FROM counters WHERE counter_key = ?', (counter_key,))
        row = cursor.fetchone()
        if row:
            new_count = row['count'] + value
            cursor.execute('UPDATE counters SET count = ?, last_updated = CURRENT_TIMESTAMP WHERE counter_key = ?', (new_count, counter_key))
        else:
            new_count = value
            cursor.execute('INSERT INTO counters (counter_key, id, count, owner) VALUES (?, ?, ?, ?)', (counter_key, counter_id, new_count, owner))
        db.commit()
        ts = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
        return jsonify(id=counter_id, count=new_count, last_updated=ts)

    @app.errorhandler(404)
    def not_found(error):
        return make_response(jsonify({'error': 'Not found'}), 404)

    @app.errorhandler(405)
    def method_not_allowed(error):
        return make_response(jsonify({'error': 'Method not allowed'}), 405)
    
    return app

app = create_app()

if __name__ == '__main__':
    init_db()
    app.run(debug=True, host='0.0.0.0')