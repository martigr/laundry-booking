"""Help page: how to book and cancel, with screenshots from static/help/."""
from flask import Blueprint, render_template

from .auth import login_required
from .db import get_setting
from .slots import SLOT_NAMES, slot_time

bp = Blueprint('help', __name__)


@bp.route('/help')
@login_required
def index():
    return render_template('help.html', slots=[(name, slot_time(name)) for name in SLOT_NAMES],
                           horizon_weeks=int(get_setting('horizon_weeks')))
