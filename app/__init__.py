"""
Counter API Flask application factory.
"""
import os
import secrets
import hashlib
from datetime import timedelta
from flask import Flask, g, session, request
from werkzeug.security import generate_password_hash
from werkzeug.middleware.proxy_fix import ProxyFix
try:
    from argon2 import PasswordHasher
except Exception:
    PasswordHasher = None

from .models import init_db, ensure_default_admin
from .utils import get_or_create_csrf_token


def create_app():
    """Create and configure the Flask application."""
    # Set template and static folders relative to the project root
    import os
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    app = Flask(__name__, 
                template_folder=os.path.join(project_root, 'templates'),
                static_folder=os.path.join(project_root, 'static'))

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
        """Close database connection."""
        db = g.pop('db', None)
        if db is not None:
            try:
                db.close()
            except Exception:
                pass

    @app.before_request
    def load_current_user():
        """Load current user and handle session security."""
        user_id = session.get('user_id')
        if user_id:
            try:
                from .utils import get_db
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
        """Add security headers to all responses."""
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

    # Register blueprints
    from .auth import auth_bp
    from .api import api_bp
    from .main import main_bp
    
    app.register_blueprint(auth_bp)
    app.register_blueprint(api_bp)
    app.register_blueprint(main_bp)

    return app