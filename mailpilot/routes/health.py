from flask import Blueprint, jsonify

from ..services.health_service import database_is_available


health_blueprint = Blueprint("health", __name__)


@health_blueprint.get("/health")
def health_check():
    database_ok = database_is_available()
    status = "ok" if database_ok else "degraded"
    http_status = 200 if database_ok else 503
    return (
        jsonify(
            {
                "status": status,
                "checks": {"database": "ok" if database_ok else "unavailable"},
            }
        ),
        http_status,
    )
