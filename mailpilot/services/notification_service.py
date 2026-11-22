from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from ..extensions import db
from ..models import Notification, NotificationPreference


CATEGORY_TITLES = {
    "success": "Action completed",
    "warning": "Attention needed",
    "error": "Action failed",
    "info": "Workspace update",
}
CATEGORY_FIELDS = {
    "success": "success_enabled",
    "warning": "warning_enabled",
    "error": "error_enabled",
    "info": "info_enabled",
}
PREFERENCE_FIELD_NAMES = {
    "success_enabled": "success_enabled",
    "warning_enabled": "warning_enabled",
    "error_enabled": "error_enabled",
    "info_enabled": "info_enabled",
}
PAGE_SIZES = {10, 20, 50}


def utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def get_notification_preferences():
    preferences = db.session.get(NotificationPreference, 1)
    if preferences is None:
        preferences = NotificationPreference(id=1)
        db.session.add(preferences)
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            preferences = db.session.get(NotificationPreference, 1)
    return preferences


def notification_preferences_values(preferences=None):
    preferences = preferences or get_notification_preferences()
    return {field: bool(getattr(preferences, field)) for field in PREFERENCE_FIELD_NAMES}


def save_notification_preferences(payload):
    preferences = get_notification_preferences()
    for field in PREFERENCE_FIELD_NAMES:
        value = str(payload.get(field, "") or "").strip().casefold()
        setattr(preferences, field, value in {"1", "true", "yes", "on"})
    db.session.commit()
    return preferences


def create_notification(category, message, *, title=None, source="workspace"):
    category = str(category or "info").strip().casefold()
    if category in {"danger", "critical"}:
        category = "error"
    elif category in {"message", "information"}:
        category = "info"
    if category not in CATEGORY_FIELDS:
        category = "info"
    message = str(message or "").strip()
    if not message:
        return None
    message = message[:1000]
    title = str(title or CATEGORY_TITLES[category]).strip()[:120]
    preferences = get_notification_preferences()
    if not getattr(preferences, CATEGORY_FIELDS[category]):
        return None
    notification = Notification(
        category=category,
        title=title or CATEGORY_TITLES[category],
        message=message,
        source=str(source or "workspace").strip()[:80] or "workspace",
    )
    db.session.add(notification)
    db.session.commit()
    return notification


def record_import_summary(summary):
    imported = int(summary.get("imported", 0) or 0)
    duplicates = int(summary.get("duplicates", 0) or 0)
    invalid = int(summary.get("invalid", 0) or 0)
    skipped = duplicates + invalid
    category = "warning" if skipped else "success"
    title = "Recipient import needs review" if skipped else "Recipient import complete"
    message = (
        f"Imported {imported} recipients; skipped {duplicates} duplicates and "
        f"{invalid} invalid rows."
    )
    return create_notification(category, message, title=title, source="recipient_import")


def unread_notification_count():
    return db.session.scalar(
        select(func.count(Notification.id)).where(Notification.is_read.is_(False))
    ) or 0


def recent_notifications(limit=8):
    limit = max(1, min(int(limit), 20))
    return db.session.scalars(
        select(Notification).order_by(Notification.created_at.desc(), Notification.id.desc()).limit(limit)
    ).all()


def list_notifications(*, page=1, per_page=20, category="all", read_state="all"):
    page = max(1, int(page))
    per_page = int(per_page)
    if per_page not in PAGE_SIZES:
        per_page = 20
    if category not in {"all", "success", "warning", "error", "info"}:
        category = "all"
    if read_state not in {"all", "unread", "read"}:
        read_state = "all"
    statement = select(Notification)
    if category != "all":
        statement = statement.where(Notification.category == category)
    if read_state == "unread":
        statement = statement.where(Notification.is_read.is_(False))
    elif read_state == "read":
        statement = statement.where(Notification.is_read.is_(True))
    statement = statement.order_by(Notification.created_at.desc(), Notification.id.desc())
    return db.paginate(statement, page=page, per_page=per_page, error_out=False), category, read_state


def get_notification(notification_id):
    return db.session.get(Notification, notification_id)


def set_notification_read(notification, is_read):
    notification.is_read = bool(is_read)
    notification.read_at = utc_now() if is_read else None
    db.session.commit()
    return notification


def delete_notification(notification):
    db.session.delete(notification)
    db.session.commit()


def mark_all_notifications_read():
    now = utc_now()
    db.session.execute(
        db.update(Notification)
        .where(Notification.is_read.is_(False))
        .values(is_read=True, read_at=now)
    )
    db.session.commit()


def clear_notifications():
    deleted = db.session.execute(db.delete(Notification)).rowcount or 0
    db.session.commit()
    return deleted


def notification_to_dict(notification):
    return {
        "id": notification.id,
        "category": notification.category,
        "category_label": notification.category_label,
        "title": notification.title,
        "message": notification.message,
        "is_read": bool(notification.is_read),
        "created_at": notification.created_at.isoformat(timespec="seconds") + "Z",
    }
