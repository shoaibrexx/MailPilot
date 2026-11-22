from flask import Blueprint, current_app, jsonify, request
from werkzeug.exceptions import RequestEntityTooLarge

from ..services.recipient_import_service import (
    ImportConflictError,
    InvalidRecipientCSV,
    UploadTooLarge,
    import_recipient_csv,
)
from ..services.notification_service import record_import_summary


uploads_blueprint = Blueprint("uploads", __name__)


@uploads_blueprint.post("/uploads/recipients")
def upload_recipients():
    uploaded_file = request.files.get("file")
    if uploaded_file is None:
        return jsonify({"error": "Upload a CSV file in the 'file' field."}), 400

    try:
        summary = import_recipient_csv(uploaded_file)
    except UploadTooLarge as error:
        return jsonify({"error": str(error)}), 413
    except InvalidRecipientCSV as error:
        return jsonify({"error": str(error)}), 400
    except ImportConflictError as error:
        return jsonify({"error": str(error)}), 409

    record_import_summary(summary)
    return jsonify(summary), 201


@uploads_blueprint.errorhandler(RequestEntityTooLarge)
def upload_too_large(_error):
    maximum_bytes = current_app.config["MAX_CONTENT_LENGTH"]
    return (
        jsonify({"error": f"Upload exceeds the {maximum_bytes}-byte limit."}),
        413,
    )
