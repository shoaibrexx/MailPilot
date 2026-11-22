from flask import Blueprint, jsonify, render_template

from ..services.dashboard_service import get_dashboard_data


dashboard_blueprint = Blueprint("dashboard", __name__)


@dashboard_blueprint.get("/")
def index():
    return render_template("dashboard/index.html")


@dashboard_blueprint.get("/api/dashboard")
def dashboard_data():
    return jsonify(get_dashboard_data())
