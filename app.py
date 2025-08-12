import os
import sqlite3
import secrets
from datetime import datetime, timedelta
from functools import wraps
from flask import Flask, jsonify, request, make_response, render_template, session, redirect, url_for, g
from werkzeug.security import generate_password_hash, check_password_hash
import re


app = Flask(__name__)
DATABASE = os.getenv('DATABASE', 'counters.db')

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
                    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            db.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    username TEXT PRIMARY KEY,
                    password_hash TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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

    @app.before_request
    def load_current_user():
        user_id = session.get('user_id')
        g.user = {'username': user_id} if user_id else None
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

    @app.route('/get-total/<counter_id>', methods=['GET'])
    def get_total(counter_id):
        db = get_db()
        cursor = db.cursor()
        cursor.execute('SELECT count, last_updated FROM counters WHERE id = ?', (counter_id,))
        row = cursor.fetchone()
        if row:
            return jsonify(id=counter_id, count=row['count'], last_updated=row['last_updated'])
        else:
            cursor.execute('INSERT INTO counters (id, count) VALUES (?, 0)', (counter_id,))
            db.commit()
            return jsonify(id=counter_id, count=0, last_updated=datetime.utcnow().isoformat() + 'Z')

    @app.route('/increment/<counter_id>', methods=['POST'])
    def increment_by_one(counter_id):
        db = get_db()
        cursor = db.cursor()
        cursor.execute('SELECT count FROM counters WHERE id = ?', (counter_id,))
        row = cursor.fetchone()
        if row:
            new_count = row['count'] + 1
            cursor.execute('UPDATE counters SET count = ?, last_updated = CURRENT_TIMESTAMP WHERE id = ?', (new_count, counter_id))
        else:
            new_count = 1
            cursor.execute('INSERT INTO counters (id, count) VALUES (?, ?)', (counter_id, new_count))
        db.commit()
        return jsonify(id=counter_id, count=new_count, last_updated=datetime.now().isoformat() + 'Z')

    @app.route('/stats', methods=['GET'])
    def get_stats():
        db = get_db()
        cursor = db.cursor()

        cursor.execute('SELECT COUNT(*) AS total_counters FROM counters')
        total_counters_row = cursor.fetchone()
        total_counters = total_counters_row['total_counters'] if total_counters_row else 0

        cursor.execute('SELECT COALESCE(SUM(count), 0) AS total_count FROM counters')
        total_count_row = cursor.fetchone()
        total_count = total_count_row['total_count'] if total_count_row else 0

        cursor.execute('SELECT MAX(last_updated) AS last_activity FROM counters')
        last_activity_row = cursor.fetchone()
        last_activity = last_activity_row['last_activity'] if last_activity_row else None

        cursor.execute('''
            SELECT id, count, last_updated
            FROM counters
            ORDER BY count DESC
            LIMIT 10
        ''')
        top_counters_rows = cursor.fetchall() or []
        top_counters = [
            {
                'id': row['id'],
                'count': row['count'],
                'last_updated': row['last_updated'],
            }
            for row in top_counters_rows
        ]

        cursor.execute('''
            SELECT id, count, last_updated
            FROM counters
            ORDER BY last_updated DESC
            LIMIT 10
        ''')
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
            if initial_count == 0:
                cursor.execute('INSERT INTO counters (id, count) VALUES (?, 0)', (counter_id,))
            else:
                cursor.execute('INSERT INTO counters (id, count) VALUES (?, ?)', (counter_id, initial_count))
            db.commit()
        except sqlite3.IntegrityError:
            return make_response(jsonify({'error': 'Counter already exists'}), 409)

        return make_response(jsonify({'id': counter_id, 'count': initial_count, 'message': 'created'}), 201)

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

    @app.route('/increase/<counter_id>', methods=['POST'])
    def increase_by_value(counter_id):
        value = int(request.json['value'])
        db = get_db()
        cursor = db.cursor()
        cursor.execute('SELECT count FROM counters WHERE id = ?', (counter_id,))
        row = cursor.fetchone()
        if row:
            new_count = row['count'] + value
            cursor.execute('UPDATE counters SET count = ?, last_updated = CURRENT_TIMESTAMP WHERE id = ?', (new_count, counter_id))
        else:
            new_count = value
            cursor.execute('INSERT INTO counters (id, count) VALUES (?, ?)', (counter_id, new_count))
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