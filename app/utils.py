"""
Utility functions for the Counter API application.
"""
import os
import sqlite3
import secrets
import uuid
import hashlib
import time
from datetime import datetime, timezone
from urllib.parse import urlparse, urljoin
from flask import request, g

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


def get_client_ip() -> str:
    """Get client IP address, respecting proxy headers."""
    # With ProxyFix enabled, access_route[0] reflects the client IP from the trusted proxy
    return (request.access_route[0] if request.access_route else request.remote_addr) or 'unknown'


def is_safe_url(target: str) -> bool:
    """Check if a URL is safe for redirects."""
    try:
        ref = urlparse(request.host_url)
        test = urlparse(urljoin(request.host_url, target or ''))
        return test.scheme in ('http', 'https') and ref.netloc == test.netloc and test.path.startswith('/')
    except Exception:
        return False


def get_or_create_csrf_token() -> str:
    """Get or create a CSRF token for the current session."""
    from flask import session
    token = session.get('csrf_token')
    if not token:
        token = secrets.token_urlsafe(16)
        session['csrf_token'] = token
    return token


def validate_csrf_token(token: str) -> bool:
    """Validate a CSRF token against the session."""
    from flask import session
    return token and session.get('csrf_token') and secrets.compare_digest(token, session['csrf_token'])


def get_csrf_from_request() -> str | None:
    """Extract CSRF token from request headers or form data."""
    header_token = request.headers.get('X-CSRF-Token')
    if header_token:
        return header_token
    if request.form:
        return request.form.get('csrf_token')
    payload = request.get_json(silent=True) or {}
    return payload.get('csrf_token')


def parse_key_and_name(param: str) -> tuple[str, str, str | None]:
    """Return (counter_key, name, owner_or_none) from API path param.
    If param includes a '|', treat as full key: 'owner|name'. Empty owner is allowed.
    Otherwise, assume global (no owner) with key '|name'.
    """
    from flask import session
    if '|' in param:
        owner_part, name_part = param.split('|', 1)
        return param, name_part, (owner_part if owner_part != '' else None)
    # Default to namespaced by the logged-in user if present; otherwise global
    owner_part = session.get('user_id')
    if owner_part:
        return f"{owner_part}|{param}", param, owner_part
    return f"|{param}", param, None