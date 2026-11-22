from datetime import datetime, timedelta, timezone

from email_validator import EmailNotValidError, validate_email
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError

from ..extensions import db
from ..models import Recipient


class RecipientValidationError(ValueError):
    def __init__(self, errors):
        self.errors = errors
        super().__init__("Please correct the highlighted recipient fields.")


class DuplicateRecipientError(ValueError):
    pass


def _clean_text(payload, key, label, limit, errors, optional=True):
    value = str(payload.get(key, "") or "").strip()
    if not value and optional:
        return None
    if not value and not optional:
        errors[key] = f"{label} is required."
        return None
    if len(value) > limit:
        errors[key] = f"{label} must be {limit} characters or fewer."
        return None
    if any(ord(character) < 32 for character in value):
        errors[key] = f"{label} contains unsupported control characters."
        return None
    return value


def validate_recipient_data(payload):
    """Normalize form or CSV values and return field-level validation errors."""
    errors = {}
    email = str(payload.get("email", "") or "").strip()
    if not email:
        errors["email"] = "Email address is required."
    elif len(email) > 254:
        errors["email"] = "Email address must be 254 characters or fewer."
    else:
        try:
            email = validate_email(
                email,
                check_deliverability=False,
                allow_smtputf8=False,
            ).normalized.casefold()
        except EmailNotValidError:
            errors["email"] = "Enter a valid email address."

    first_name = _clean_text(payload, "first_name", "First name", 80, errors)
    last_name = _clean_text(payload, "last_name", "Last name", 80, errors)
    company = _clean_text(payload, "company", "Company", 120, errors)

    raw_status = payload.get("is_active", "active")
    if isinstance(raw_status, bool):
        is_active = raw_status
    else:
        normalized_status = str(raw_status).strip().casefold()
        active_values = {"active", "true", "1", "yes", "on"}
        inactive_values = {"inactive", "false", "0", "no", "off"}
        if normalized_status in active_values:
            is_active = True
        elif normalized_status in inactive_values:
            is_active = False
        else:
            errors["is_active"] = "Choose active or inactive status."
            is_active = True

    values = {
        "email": email,
        "first_name": first_name,
        "last_name": last_name,
        "company": company,
        "is_active": is_active,
    }
    return values, errors


def _check_duplicate(email, excluding_id=None):
    query = select(Recipient.id).where(Recipient.email == email)
    if excluding_id is not None:
        query = query.where(Recipient.id != excluding_id)
    return db.session.scalar(query) is not None


def _commit_recipient(recipient, *, is_new):
    try:
        if is_new:
            db.session.add(recipient)
        db.session.commit()
    except IntegrityError as error:
        db.session.rollback()
        raise DuplicateRecipientError(
            "A recipient with this email address already exists."
        ) from error
    return recipient


def create_recipient(payload):
    values, errors = validate_recipient_data(payload)
    if errors:
        raise RecipientValidationError(errors)
    if _check_duplicate(values["email"]):
        raise DuplicateRecipientError(
            "A recipient with this email address already exists."
        )
    return _commit_recipient(Recipient(**values), is_new=True)


def update_recipient(recipient, payload):
    values, errors = validate_recipient_data(payload)
    if errors:
        raise RecipientValidationError(errors)
    if _check_duplicate(values["email"], excluding_id=recipient.id):
        raise DuplicateRecipientError(
            "A recipient with this email address already exists."
        )
    for key, value in values.items():
        setattr(recipient, key, value)
    return _commit_recipient(recipient, is_new=False)


def get_recipient(recipient_id):
    return db.session.get(Recipient, recipient_id)


def delete_recipient(recipient):
    db.session.delete(recipient)
    db.session.commit()


def set_recipient_active(recipient, is_active):
    recipient.is_active = bool(is_active)
    db.session.commit()
    return recipient


def get_recipient_summary():
    total = db.session.scalar(select(func.count(Recipient.id))) or 0
    active = db.session.scalar(
        select(func.count(Recipient.id)).where(Recipient.is_active.is_(True))
    ) or 0
    inactive = total - active
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=30)
    recently_added = db.session.scalar(
        select(func.count(Recipient.id)).where(Recipient.created_at >= cutoff)
    ) or 0
    recent_recipients = db.session.scalars(
        select(Recipient)
        .order_by(Recipient.created_at.desc(), Recipient.id.desc())
        .limit(5)
    ).all()
    return {
        "total": total,
        "active": active,
        "inactive": inactive,
        "recently_added": recently_added,
        "recent_recipients": recent_recipients,
    }


def list_recipients(search="", status="all", page=1, per_page=10):
    query = select(Recipient)
    if status == "active":
        query = query.where(Recipient.is_active.is_(True))
    elif status == "inactive":
        query = query.where(Recipient.is_active.is_(False))

    if search:
        escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped}%"
        query = query.where(
            or_(
                Recipient.first_name.ilike(pattern, escape="\\"),
                Recipient.last_name.ilike(pattern, escape="\\"),
                Recipient.email.ilike(pattern, escape="\\"),
                Recipient.company.ilike(pattern, escape="\\"),
            )
        )

    return db.paginate(
        query.order_by(Recipient.created_at.desc(), Recipient.id.desc()),
        page=page,
        per_page=per_page,
        error_out=False,
    )
