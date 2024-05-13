import sqlite3
from flask import Flask, jsonify, request

app = Flask(__name__)
DATABASE = 'counters.db'

def get_db():
    # Create a database connection each time this is called
    db = sqlite3.connect(DATABASE)
    db.row_factory = sqlite3.Row  # This enables column access by name: row['column_name']
    return db

def init_db():
    # Initialize the database and create table if it doesn't exist
    with get_db() as db:
        db.execute('''
            CREATE TABLE IF NOT EXISTS counters (
                id TEXT PRIMARY KEY,
                count INTEGER NOT NULL DEFAULT 0
            )
        ''')

@app.route('/get-total/<counter_id>', methods=['GET'])
def get_total(counter_id):
    db = get_db()
    cursor = db.cursor()
    cursor.execute('SELECT count FROM counters WHERE id = ?', (counter_id,))
    row = cursor.fetchone()
    if row:
        return jsonify(count=row['count'])
    else:
        cursor.execute('INSERT INTO counters (id, count) VALUES (?, 0)', (counter_id,))
        db.commit()
        return jsonify(count=0)

@app.route('/increment/<counter_id>', methods=['POST'])
def increment_by_one(counter_id):
    db = get_db()
    cursor = db.cursor()
    cursor.execute('SELECT count FROM counters WHERE id = ?', (counter_id,))
    row = cursor.fetchone()
    if row:
        new_count = row['count'] + 1
        cursor.execute('UPDATE counters SET count = ? WHERE id = ?', (new_count, counter_id))
    else:
        new_count = 1
        cursor.execute('INSERT INTO counters (id, count) VALUES (?, ?)', (counter_id, new_count))
    db.commit()
    return jsonify(count=new_count)

@app.route('/increase/<counter_id>', methods=['POST'])
def increase_by_value(counter_id):
    value = int(request.json['value'])  # Expecting value to be sent in JSON body of request
    db = get_db()
    cursor = db.cursor()
    cursor.execute('SELECT count FROM counters WHERE id = ?', (counter_id,))
    row = cursor.fetchone()
    if row:
        new_count = row['count'] + value
        cursor.execute('UPDATE counters SET count = ? WHERE id = ?', (new_count, counter_id))
    else:
        new_count = value
        cursor.execute('INSERT INTO counters (id, count) VALUES (?, ?)', (counter_id, new_count))
    db.commit()
    return jsonify(count=new_count)

if __name__ == '__main__':
    init_db()  # Ensure the database and table are ready
    app.run(debug=True)  # Start the application with debugging enabled
