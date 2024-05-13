import os
import sqlite3
from datetime import datetime
from flask import Flask, jsonify, request, make_response


app = Flask(__name__)
DATABASE = os.getenv('DATABASE', 'counters.db')

def get_db():
    db_path = DATABASE  # Correctly assigning the DATABASE constant to db_path
    db = sqlite3.connect(DATABASE)
    print(f"Connecting to database at {db_path}")  # Debugging output
    db.row_factory = sqlite3.Row
    return db

def init_db():
    with get_db() as db:
        db.execute('''
            CREATE TABLE IF NOT EXISTS counters (
                id TEXT PRIMARY KEY,
                count INTEGER NOT NULL DEFAULT 0,
                last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        db.commit()

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

if __name__ == '__main__':
    init_db()
    app.run(debug=True, host='0.0.0.0')
