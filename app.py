import os
from datetime import datetime
from flask import Flask, jsonify, request, make_response
from flask_sqlalchemy import SQLAlchemy
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from sqlalchemy.orm import Session
import redis

# Initialize the database and rate limiter globally
db = SQLAlchemy()

# Configure Redis for Flask-Limiter
redis_url = os.getenv('REDIS_URL')
redis_connection = redis.StrictRedis.from_url(redis_url)
limiter = Limiter(key_func=get_remote_address, storage_uri=redis_url)

class Counter(db.Model):
    id = db.Column(db.String, primary_key=True)
    count = db.Column(db.Integer, default=0)
    last_updated = db.Column(db.DateTime, default=datetime.now)

def create_app():
    app = Flask(__name__)
    app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL')
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

    # Initialize the extensions with the app
    db.init_app(app)
    limiter.init_app(app)

    with app.app_context():
        db.create_all()

    @app.route('/get-total/<counter_id>', methods=['GET'])
    @limiter.limit("20 per minute")
    def get_total(counter_id):
        with Session(db.engine) as session:
            counter = session.get(Counter, counter_id)
            if counter:
                return jsonify(id=counter_id, count=counter.count, last_updated=counter.last_updated.isoformat() + 'Z')
            else:
                new_counter = Counter(id=counter_id, count=0)
                session.add(new_counter)
                session.commit()
                return jsonify(id=counter_id, count=0, last_updated=datetime.now().isoformat() + 'Z')

    @app.route('/increment/<counter_id>', methods=['POST'])
    @limiter.limit("20 per minute")
    def increment_by_one(counter_id):
        with Session(db.engine) as session:
            counter = session.get(Counter, counter_id)
            if counter:
                counter.count += 1
                counter.last_updated = datetime.utcnow()
            else:
                counter = Counter(id=counter_id, count=1)
                session.add(counter)
            session.commit()
            return jsonify(id=counter_id, count=counter.count, last_updated=counter.last_updated.isoformat() + 'Z')

    @app.route('/increase/<counter_id>', methods=['POST'])
    @limiter.limit("20 per minute")
    def increase_by_value(counter_id):
        value = int(request.json['value'])
        with Session(db.engine) as session:
            counter = session.get(Counter, counter_id)
            if counter:
                counter.count += value
                counter.last_updated = datetime.now()
            else:
                counter = Counter(id=counter_id, count=value)
                session.add(counter)
            session.commit()
            return jsonify(id=counter_id, count=counter.count, last_updated=counter.last_updated.isoformat() + 'Z')

    @app.errorhandler(404)
    def not_found(error):
        return make_response(jsonify({'error': 'Not found'}), 404)

    @app.errorhandler(405)
    def method_not_allowed(error):
        return make_response(jsonify({'error': 'Method not allowed'}), 405)
    
    return app

app = create_app()

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0')
