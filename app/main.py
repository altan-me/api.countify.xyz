"""
Main web interface routes for the Counter API.
"""
from flask import Blueprint, render_template
from .utils import get_or_create_csrf_token
from .auth import login_required

main_bp = Blueprint('main', __name__)


@main_bp.route('/', methods=['GET'])
def landing():
    """Landing page."""
    get_or_create_csrf_token()
    return render_template('landing.html')


@main_bp.route('/dashboard', methods=['GET'])
@login_required
def dashboard():
    """User dashboard."""
    return render_template('dashboard.html')