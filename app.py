import os
import sqlite3
import secrets
import uuid
from datetime import datetime, timedelta
from functools import wraps
from flask import Flask, jsonify, request, make_response, render_template, session, redirect, url_for, g
from werkzeug.security import generate_password_hash, check_password_hash
import re


app = Flask(__name__)
DATABASE = os.getenv('DATABASE', 'counters.db')

def generate_public_id() -> str:
    """Generate a short, lowercase, URL-friendly public id.
    Uses 40 bits of randomness (10 hex chars)."""
    return uuid.uuid4().hex[:10]

def get_db():
    db_path = DATABASE  # Correctly assigning the DATABASE constant to db_path
    db = sqlite3.connect(DATABASE)
    print(f"Connecting to database at {db_path}")  # Debugging output
    db.row_factory = sqlite3.Row
    return db

def init_db():
    try:
        with get_db() as db:
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
        with get_db() as db:
            cursor = db.cursor()
            cursor.execute('SELECT COUNT(*) as c FROM users')
            row = cursor.fetchone()
            num_users = row['c'] if row else 0
            if num_users == 0:
                password = os.getenv('ADMIN_PASSWORD') or secrets.token_urlsafe(16)
                password_hash = generate_password_hash(password)
                cursor.execute('INSERT INTO users (username, password_hash) VALUES (?, ?)', ('admin', password_hash))
                db.commit()
                print("Created default admin user 'admin'")
                if not os.getenv('ADMIN_PASSWORD'):
                    print(f"Temporary admin password: {password}")
    except Exception as e:
        print(f"Error ensuring default admin: {e}")

def create_app():
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=os.getenv('SECRET_KEY') or secrets.token_bytes(32),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SECURE=bool(int(os.getenv('SESSION_COOKIE_SECURE', '0'))),
        SESSION_COOKIE_SAMESITE='Lax',
        SESSION_REFRESH_EACH_REQUEST=True,
    )
    # Rolling session expiration of 2 days
    app.permanent_session_lifetime = timedelta(days=2)
    # Fake password hash to avoid username enumeration timing differences
    app.config['FAKE_PASSWORD_HASH'] = generate_password_hash('notused')
    
    # Database initialization
    init_db()
    ensure_default_admin()

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
                return redirect(url_for('login', next=request.path))
            return view_func(*args, **kwargs)
        return wrapped

    def get_client_ip() -> str:
        # Honor reverse proxy if present
        forwarded = request.headers.get('X-Forwarded-For')
        if forwarded:
            return forwarded.split(',')[0].strip()
        return request.remote_addr or 'unknown'

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
                locked_dt = datetime.fromisoformat(locked_until) if isinstance(locked_until, str) else locked_until
            except Exception:
                locked_dt = None
            if locked_dt and locked_dt > datetime.utcnow():
                return True, locked_dt
        return False, None

    def register_failure(db, key: str) -> bool:
        """Record a failed attempt. Returns True if now locked."""
        cursor = db.cursor()
        cursor.execute('SELECT attempts, last_attempt FROM auth_attempts WHERE key = ?', (key,))
        row = cursor.fetchone()
        now = datetime.utcnow()
        window_start = now - timedelta(minutes=WINDOW_MINUTES)
        if not row:
            cursor.execute('INSERT INTO auth_attempts (key, attempts, last_attempt) VALUES (?, ?, ?)', (key, 1, now.isoformat()))
            db.commit()
            return False
        attempts = row['attempts'] or 0
        last_attempt = row['last_attempt']
        try:
            last_dt = datetime.fromisoformat(last_attempt) if isinstance(last_attempt, str) else last_attempt
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
            return jsonify(id=name, count=0, last_updated=datetime.utcnow().isoformat() + 'Z')

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
        return jsonify(id=counter_id, count=0, last_updated=datetime.utcnow().isoformat() + 'Z')

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
        return jsonify(id=name, count=new_count, last_updated=datetime.now().isoformat() + 'Z')

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
        return jsonify(id=counter_id, count=new_count, last_updated=datetime.now().isoformat() + 'Z')

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
            return make_response(render_template('login.html', error='Username and password are required.'), 400)

        db = get_db()
        # Check lockout status for this username+ip
        key = login_key(username)
        locked, locked_until = is_locked(db, key)
        if locked:
            return make_response(render_template('login.html', error='Too many attempts. Try again later.'), 429)
        cursor = db.cursor()
        cursor.execute('SELECT password_hash FROM users WHERE username = ?', (username,))
        row = cursor.fetchone()
        stored_hash = row['password_hash'] if row else app.config['FAKE_PASSWORD_HASH']
        ok = False
        try:
            ok = check_password_hash(stored_hash, password)
        except Exception:
            ok = False
        if not row or not ok:
            just_locked = register_failure(db, key)
            if just_locked:
                return make_response(render_template('login.html', error='Too many attempts. Try again later.'), 429)
            return make_response(render_template('login.html', error='Invalid credentials.'), 401)

        session['user_id'] = username
        session.permanent = True
        # Rotate CSRF token after login
        session['csrf_token'] = secrets.token_urlsafe(16)
        clear_attempts(db, key)
        next_url = request.args.get('next') or url_for('dashboard')
        return redirect(next_url)

    @app.route('/logout', methods=['POST'])
    def logout():
        csrf_token = request.form.get('csrf_token', '')
        if not validate_csrf_token(csrf_token):
            return make_response('Bad CSRF token', 400)
        session.clear()
        return redirect(url_for('landing'))

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
            with db:
                db.execute('INSERT INTO users (username, password_hash, public_id) VALUES (?, ?, ?)', (username, generate_password_hash(password), pid))
        except sqlite3.IntegrityError:
            # Register a failure against the IP and return conflict
            register_failure(db, key)
            return make_response(render_template('signup.html', error='Username is taken.'), 409)

        # Clear failures on success and log the user in
        clear_attempts(db, key)
        session['user_id'] = username
        session['csrf_token'] = secrets.token_urlsafe(16)
        session.permanent = True
        return redirect(url_for('dashboard'))

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
        return jsonify(id=name, count=new_count, last_updated=datetime.now().isoformat() + 'Z')

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
        return jsonify(id=counter_id, count=new_count, last_updated=datetime.now().isoformat() + 'Z')

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