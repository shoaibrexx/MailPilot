import csv
import json
from io import StringIO
from flask import current_app

from flask import (
    Blueprint,
    Response,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from sqlalchemy import select

from ..extensions import db
from ..models import Campaign, Recipient
from ..services.settings_service import (
    DATE_FORMATS,
    RESET_CONFIRMATION,
    TIME_FORMATS,
    TIMEZONES,
    SettingsValidationError,
    get_settings,
    reset_workspace_data,
    save_settings,
    save_theme_preference,
    settings_form_values,
    workspace_data_counts,
    WorkspaceResetBlocked,
)
from ..services.notification_service import (
    notification_preferences_values,
    save_notification_preferences,
)
from ..services.email_service import MailPilotSMTPError, smtp_is_configured, test_smtp_connection
from ..services.action_limit_service import SensitiveActionRateLimited, reserve_action_cooldown
from ..services.unsubscribe_service import UnsubscribeValidationError, get_mailing_profile, save_postal_address


settings_blueprint = Blueprint("settings", __name__, url_prefix="/settings")


def _render_settings(*, form_values=None, errors=None, status=200):
    settings = get_settings()
    return (
        render_template(
            "settings/index.html",
            settings=settings,
            form_values=form_values or settings_form_values(settings),
            errors=errors or {},
            timezones=TIMEZONES,
            date_formats=DATE_FORMATS,
            time_formats=TIME_FORMATS,
            data_counts=workspace_data_counts(),
            reset_confirmation=RESET_CONFIRMATION,
            notification_preferences=notification_preferences_values(),
            smtp_configured=smtp_is_configured(),
            mailing_profile=get_mailing_profile(),
        ),
        status,
    )


def _safe_csv_value(value):
    if not isinstance(value, str):
        return value
    if value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def _csv_response(filename, headings, statement, serialize_row):
    connection = db.engine.connect()
    result = connection.execution_options(yield_per=500).execute(statement)

    def generate():
        buffer = StringIO(newline="")
        writer = csv.writer(buffer)
        writer.writerow(headings)
        yield "\ufeff" + buffer.getvalue()
        try:
            for row in result:
                buffer.seek(0)
                buffer.truncate(0)
                writer.writerow([_safe_csv_value(value) for value in serialize_row(row._mapping)])
                yield buffer.getvalue()
        finally:
            result.close()
            connection.close()

    response = Response(generate(), content_type="text/csv; charset=utf-8")
    response.call_on_close(connection.close)
    response.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "no-store"
    return response


@settings_blueprint.get("")
@settings_blueprint.get("/")
def index():
    return _render_settings()


@settings_blueprint.post("")
def update():
    try:
        save_settings(request.form)
        if request.form.get("notification_preferences_submitted") == "1":
            save_notification_preferences(request.form)
    except SettingsValidationError as error:
        return _render_settings(form_values=error.values, errors=error.errors, status=422)
    flash("Settings saved.", "success")
    return redirect(url_for("settings.index"), code=303)


@settings_blueprint.post("/theme")
def save_theme():
    payload = request.get_json(silent=True) or request.form
    try:
        theme = save_theme_preference(payload.get("theme"))
    except SettingsValidationError as error:
        return jsonify({"error": error.errors["theme_preference"]}), 422
    return jsonify({"theme": theme, "saved": True})


@settings_blueprint.post("/smtp/test")
def test_smtp():
    try:
        reserve_action_cooldown("smtp_connection_test", 30)
    except SensitiveActionRateLimited as error:
        flash(f"Wait {error.retry_after_seconds} seconds before testing the SMTP connection again.", "warning")
        return redirect(url_for("settings.index"), code=303)
    try:
        test_smtp_connection(current_app.config)
    except MailPilotSMTPError as error:
        flash(error.public_message, "error")
    else:
        flash("Gmail SMTP connection and authentication succeeded. No test email was sent.", "success")
    return redirect(url_for("settings.index"), code=303)


@settings_blueprint.post("/mailing-profile")
def update_mailing_profile():
    try:
        save_postal_address(request.form.get("postal_address", ""))
    except UnsubscribeValidationError as error:
        flash(str(error), "error")
    else:
        flash("Mailing address saved.", "success")
    return redirect(url_for("settings.index"), code=303)


@settings_blueprint.get("/export/recipients.csv")
def export_recipients():
    return _csv_response(
        "mailpilot-recipients.csv",
        ("id", "email", "first_name", "last_name", "company", "status", "created_at", "updated_at"),
        select(Recipient.__table__).order_by(Recipient.created_at, Recipient.id),
        lambda row: (
            row["id"],
            row["email"],
            row["first_name"] or "",
            row["last_name"] or "",
            row["company"] or "",
            "active" if row["is_active"] else "inactive",
            row["created_at"].isoformat(sep=" ") if row["created_at"] else "",
            row["updated_at"].isoformat(sep=" ") if row["updated_at"] else "",
        ),
    )


@settings_blueprint.get("/export/campaigns.csv")
def export_campaigns():
    return _csv_response(
        "mailpilot-campaigns.csv",
        (
            "id", "name", "status", "email_draft_id", "draft_name", "subject", "body_html",
            "manual_recipient_ids", "recipient_group_ids", "recipient_group_names",
            "recipient_snapshot", "recipient_count", "scheduled_at", "created_at", "updated_at",
        ),
        select(Campaign.__table__).order_by(Campaign.created_at, Campaign.id),
        lambda row: (
            row["id"],
            row["name"],
            row["status"],
            row["email_draft_id"] or "",
            row["draft_name"],
            row["subject"],
            row["body_html"],
            json.dumps(row["manual_recipient_ids"] or [], ensure_ascii=False),
            json.dumps(row["recipient_group_ids"] or [], ensure_ascii=False),
            json.dumps(row["recipient_group_names"] or [], ensure_ascii=False),
            json.dumps(row["recipient_snapshot"] or [], ensure_ascii=False),
            len(row["recipient_snapshot"] or []),
            row["scheduled_at"].isoformat(sep=" ") if row["scheduled_at"] else "",
            row["created_at"].isoformat(sep=" ") if row["created_at"] else "",
            row["updated_at"].isoformat(sep=" ") if row["updated_at"] else "",
        ),
    )


@settings_blueprint.post("/reset")
def reset():
    confirmation = str(request.form.get("confirmation", "") or "").strip()
    if confirmation != RESET_CONFIRMATION:
        return _render_settings(
            errors={
                "confirmation": f'Type "{RESET_CONFIRMATION}" exactly to confirm this action.'
            },
            status=422,
        )
    try:
        deleted = reset_workspace_data()
    except WorkspaceResetBlocked as error:
        return _render_settings(errors={"confirmation": str(error)}, status=409)
    flash(
        "Workspace data cleared: "
        f"{deleted['recipients']} recipients, {deleted['campaigns']} campaigns, "
        f"{deleted['drafts']} drafts, and {deleted['groups']} groups. "
        "Queued sends were cancelled. Campaign execution, bounce, unsubscribe, and suppression records are retained.",
        "success",
    )
    return redirect(url_for("settings.index"), code=303)
