"""
Authentication routes and helpers for the Counter API.
"""
import hashlib
import secrets
import time
from functools import wraps
from flask import Blueprint, request, session, redirect, render_template, make_response, g
from werkzeug.security import generate_password_hash, check_password_hash
try:
    from argon2 import PasswordHasher
except Exception:
    PasswordHasher = None

from .utils import get_or_create_csrf_token, validate_csrf_token, is_safe_url, get_db
from .models import login_key, is_locked, register_failure, clear_attempts

auth_bp = Blueprint('auth', __name__)


def login_required(view_func):
    """Decorator to require login for a view."""
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if not session.get('user_id'):
            return redirect(f'/login?next={request.path}')
        return view_func(*args, **kwargs)
    return wrapped


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    """User login route."""
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
    
    # Get fake hash for timing consistency
    from flask import current_app
    stored_hash = row['password_hash'] if row else (current_app.config.get('FAKE_ARGON2_HASH') or current_app.config['FAKE_PASSWORD_HASH'])

    # Also enforce IP-only lock to reduce username spraying
    from .utils import get_client_ip
    ip_key = f"ip|{get_client_ip()}"
    locked_user, _ = is_locked(db, key)
    locked_ip, _ = is_locked(db, ip_key)
    if locked_user or locked_ip:
        return make_response(render_template('login.html', error='Invalid username or password.'), 200)

    ok = False
    used_pbkdf2 = False
    try:
        if isinstance(stored_hash, str) and stored_hash.startswith('$argon2'):
            if current_app.config['PASSWORD_HASHER']:
                ok = current_app.config['PASSWORD_HASHER'].verify(stored_hash, password)
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
        if used_pbkdf2 and row and current_app.config['PASSWORD_HASHER']:
            new_hash = current_app.config['PASSWORD_HASHER'].hash(password)
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


@auth_bp.route('/logout', methods=['POST'])
def logout():
    """User logout route."""
    csrf_token = request.form.get('csrf_token', '')
    if not validate_csrf_token(csrf_token):
        return make_response('Bad CSRF token', 400)
    session.clear()
    return redirect('/')


@auth_bp.route('/signup', methods=['GET', 'POST'])
def signup():
    """User registration route."""
    if request.method == 'GET':
        get_or_create_csrf_token()
        return render_template('signup.html')

    csrf_token = request.form.get('csrf_token', '')
    if not validate_csrf_token(csrf_token):
        return make_response(render_template('signup.html', error='Invalid session. Please try again.'), 400)

    username = (request.form.get('username') or '').strip()
    password = request.form.get('password') or ''
    if not username or not password:
        return make_response(render_template('signup.html', error='Username and password are required.'), 400)
    if len(username) > 32 or not username.replace('_', '').replace('-', '').isalnum():
        return make_response(render_template('signup.html', error='Username must be <=32 chars and only letters, numbers, _ or -.'), 400)
    if len(password) < 8:
        return make_response(render_template('signup.html', error='Password must be at least 8 characters.'), 400)

    db = get_db()
    cursor = db.cursor()
    
    # Rate limiting for signup attempts
    from .utils import get_client_ip
    key = f"signup|{username.lower()}|{get_client_ip()}"
    locked, _ = is_locked(db, key)
    if locked:
        return make_response(render_template('signup.html', error='Too many attempts. Please try again later.'), 429)

    try:
        # Hash password
        from flask import current_app
        if current_app.config['PASSWORD_HASHER']:
            password_hash = current_app.config['PASSWORD_HASHER'].hash(password)
        else:
            password_hash = generate_password_hash(password)
        
        # Generate unique public_id
        from .utils import generate_public_id
        public_id = generate_public_id()
        while True:
            try:
                cursor.execute('INSERT INTO users (username, password_hash, public_id) VALUES (?, ?, ?)', (username, password_hash, public_id))
                break
            except Exception:
                public_id = generate_public_id()
        
        db.commit()
    except Exception:
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