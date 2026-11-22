from sqlalchemy import select

from ..extensions import db
from ..models import SecurityEvent


SECURITY_ACTIONS = {
    "auth.login": "Workspace sign-in attempted",
    "auth.logout": "Workspace administrator signed out",
    "recipients.new_recipient": "Recipient added",
    "recipients.edit": "Recipient updated",
    "recipients.toggle_status": "Recipient status changed",
    "recipients.remove": "Recipient deleted",
    "recipients.import_csv": "Recipient CSV imported",
    "uploads.upload_recipients": "Recipient CSV uploaded",
    "composer.create": "Email draft created",
    "composer.update": "Email draft updated",
    "composer.remove": "Email draft deleted",
    "composer.send": "Email send requested",
    "campaigns.create": "Campaign created",
    "campaigns.edit": "Campaign updated",
    "campaigns.remove": "Campaign deleted",
    "campaigns.send": "Campaign send requested",
    "campaigns.create_group": "Recipient group created",
    "campaigns.edit_group": "Recipient group updated",
    "campaigns.remove_group": "Recipient group deleted",
    "settings.update": "Workspace settings updated",
    "settings.test_smtp": "Gmail SMTP connection tested",
    "settings.save_theme": "Theme preference changed",
    "settings.reset": "Workspace data reset",
    "notifications.mark_read": "Notification marked read",
    "notifications.mark_unread": "Notification marked unread",
    "notifications.remove": "Notification deleted",
    "notifications.mark_all_read": "All notifications marked read",
    "notifications.clear": "Notification history cleared",
    "security.clear_session": "Browser session cleared",
    "security.revoke_session": "Workspace session revoked",
    "security.add_suppression_route": "Recipient suppression added",
    "security.delete_suppression_route": "Recipient suppression removed",
    "settings.update_mailing_profile": "Mailing address updated",
}


def log_security_event(*, endpoint, method, status_code):
    action = SECURITY_ACTIONS.get(endpoint)
    if action is None:
        return None
    outcome = "success" if status_code < 400 else "rejected"
    details = f"{method} {endpoint} returned HTTP {status_code}."
    event = SecurityEvent(
        action=action,
        details=details,
        endpoint=endpoint,
        method=method,
        status_code=int(status_code),
        outcome=outcome,
    )
    db.session.add(event)
    db.session.commit()
    return event


def list_security_events(*, page=1, per_page=25, outcome="all"):
    page = max(1, int(page))
    if per_page not in {25, 50, 100}:
        per_page = 25
    if outcome not in {"all", "success", "rejected"}:
        outcome = "all"
    statement = select(SecurityEvent)
    if outcome != "all":
        statement = statement.where(SecurityEvent.outcome == outcome)
    statement = statement.order_by(SecurityEvent.created_at.desc(), SecurityEvent.id.desc())
    return db.paginate(statement, page=page, per_page=per_page, error_out=False), outcome
