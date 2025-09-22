"""
API routes for the Counter API.
"""
import re
from datetime import datetime, timezone
from flask import Blueprint, jsonify, request, make_response, session
from .utils import get_db, parse_key_and_name, get_csrf_from_request, validate_csrf_token
from .models import get_user_by_public_id
from .auth import login_required

api_bp = Blueprint('api', __name__)


@api_bp.route('/get-total/<counter_id>', methods=['GET'])
def get_total(counter_id):
    """Get the total count for a counter."""
    db = get_db()
    cursor = db.cursor()
    counter_key, name, owner_val = parse_key_and_name(counter_id)
    cursor.execute('SELECT count, last_updated FROM counters WHERE counter_key = ?', (counter_key,))
    row = cursor.fetchone()
    if row:
        return jsonify(id=name, count=row['count'], last_updated=row['last_updated'])
    else:
        cursor.execute('INSERT INTO counters (counter_key, id, count, owner) VALUES (?, ?, 0, ?)', (counter_key, name, owner_val))
        db.commit()
        ts = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
        return jsonify(id=name, count=0, last_updated=ts)


@api_bp.route('/get-total/<user_hash>/<counter_id>', methods=['GET'])
def get_total_for_user(user_hash: str, counter_id: str):
    """Get the total count for a user's counter."""
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


@api_bp.route('/increment/<counter_id>', methods=['POST'])
def increment_by_one(counter_id):
    """Increment a counter by one."""
    db = get_db()
    cursor = db.cursor()
    counter_key, name, owner_val = parse_key_and_name(counter_id)
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


@api_bp.route('/increment/<user_hash>/<counter_id>', methods=['POST'])
def increment_by_one_for_user(user_hash: str, counter_id: str):
    """Increment a user's counter by one."""
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


@api_bp.route('/increase/<counter_id>', methods=['POST'])
def increase_by_value(counter_id):
    """Increase a counter by a specified value."""
    value = int(request.json['value'])
    db = get_db()
    cursor = db.cursor()
    counter_key, name, owner_val = parse_key_and_name(counter_id)
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


@api_bp.route('/increase/<user_hash>/<counter_id>', methods=['POST'])
def increase_by_value_for_user(user_hash: str, counter_id: str):
    """Increase a user's counter by a specified value."""
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


@api_bp.route('/stats', methods=['GET'])
def get_stats():
    """Get counter statistics."""
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


@api_bp.route('/counters', methods=['GET'])
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


@api_bp.route('/counters/<counter_id>', methods=['DELETE'])
@login_required
def delete_counter(counter_id: str):
    """Delete a counter."""
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


@api_bp.route('/create-counter', methods=['POST'])
@login_required
def create_counter():
    """Create a new counter."""
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
        counter_key, name, _ = parse_key_and_name(f"{owner_val}|{counter_id}")
        if initial_count == 0:
            cursor.execute('INSERT INTO counters (counter_key, id, count, owner) VALUES (?, ?, 0, ?)', (counter_key, name, owner_val))
        else:
            cursor.execute('INSERT INTO counters (counter_key, id, count, owner) VALUES (?, ?, ?, ?)', (counter_key, name, initial_count, owner_val))
        db.commit()
    except Exception:
        return make_response(jsonify({'error': 'Counter already exists'}), 409)
    return make_response(jsonify({'id': name, 'count': initial_count, 'message': 'created'}), 201)