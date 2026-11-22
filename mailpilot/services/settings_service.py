import re

from email_validator import EmailNotValidError, validate_email
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from ..extensions import db
from ..models import Campaign, CampaignJob, EmailDraft, EmailSendHistory, Recipient, RecipientGroup, WorkspaceSettings


TIMEZONES = (
    "UTC",
    "America/New_York",
    "America/Chicago",
    "America/Denver",
    "America/Los_Angeles",
    "Europe/London",
    "Europe/Paris",
    "Europe/Berlin",
    "Asia/Dubai",
    "Asia/Kolkata",
    "Asia/Singapore",
    "Asia/Tokyo",
    "Australia/Sydney",
    "Pacific/Auckland",
)
THEME_PREFERENCES = {"light", "dark", "system"}
DATE_FORMATS = {"yyyy-mm-dd", "dd/mm/yyyy", "mm/dd/yyyy"}
TIME_FORMATS = {"24h", "12h"}
RESET_CONFIRMATION = "DELETE ALL DATA"
ADDRESS_SEPARATOR = re.compile(r"[,;\r\n]+")


class SettingsValidationError(ValueError):
    def __init__(self, errors, values=None):
        self.errors = errors
        self.values = values or {}
        super().__init__("Please correct the highlighted settings fields.")


class WorkspaceResetBlocked(ValueError):
    pass


def get_settings():
    settings = db.session.get(WorkspaceSettings, 1)
    if settings is None:
        settings = WorkspaceSettings(id=1)
        db.session.add(settings)
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            settings = db.session.get(WorkspaceSettings, 1)
    return settings


def settings_form_values(settings=None):
    settings = settings or get_settings()
    return {
        "workspace_name": settings.workspace_name,
        "workspace_description": settings.workspace_description,
        "profile_name": settings.profile_name,
        "profile_email": settings.profile_email,
        "timezone": settings.timezone,
        "date_format": settings.date_format,
        "time_format": settings.time_format,
        "default_sender_name": settings.default_sender_name,
        "default_sender_email": settings.default_sender_email,
        "email_signature": settings.email_signature,
        "email_footer": settings.email_footer,
        "default_cc": settings.default_cc,
        "default_bcc": settings.default_bcc,
        "theme_preference": settings.theme_preference,
    }


def _clean_text(payload, field, label, limit, errors, *, required=False, multiline=False):
    value = str(payload.get(field, "") or "").strip()
    if required and not value:
        errors[field] = f"{label} is required."
    elif len(value) > limit:
        errors[field] = f"{label} must be {limit} characters or fewer."
    else:
        disallowed = any(
            (ord(character) < 32 and character not in ("\n", "\r", "\t"))
            or ord(character) == 127
            for character in value
        )
        if disallowed or (not multiline and any(character in value for character in "\r\n\t")):
            errors[field] = f"{label} contains unsupported control characters."
    return value


def _normalize_email(value, field, label, errors, *, required=False):
    value = str(value or "").strip()
    if not value:
        if required:
            errors[field] = f"{label} is required."
        return ""
    if len(value) > 254:
        errors[field] = f"{label} must be 254 characters or fewer."
        return ""
    try:
        return validate_email(
            value, check_deliverability=False, allow_smtputf8=False
        ).normalized.casefold()
    except EmailNotValidError:
        errors[field] = f"Enter a valid {label.lower()}."
        return ""


def _normalize_address_list(value, field, label, errors):
    value = str(value or "")
    if len(value) > 5000:
        errors[field] = f"{label} must be 5,000 characters or fewer."
        return ""
    addresses = []
    for address in (part.strip() for part in ADDRESS_SEPARATOR.split(value)):
        if not address:
            continue
        try:
            normalized = validate_email(
                address, check_deliverability=False, allow_smtputf8=False
            ).normalized.casefold()
        except EmailNotValidError:
            errors[field] = f"Enter valid email addresses separated by commas."
            continue
        if normalized in addresses:
            errors[field] = f"Remove the duplicate address {normalized}."
            continue
        addresses.append(normalized)
        if len(addresses) > 100:
            errors[field] = f"Use no more than 100 addresses in {label.lower()}."
            return ""
    return ", ".join(addresses)


def validate_settings_data(payload):
    errors = {}
    values = {
        "workspace_name": _clean_text(
            payload, "workspace_name", "Workspace name", 120, errors, required=True
        ),
        "workspace_description": _clean_text(
            payload, "workspace_description", "Description", 500, errors, multiline=True
        ),
        "profile_name": _clean_text(payload, "profile_name", "Profile name", 120, errors),
        "profile_email": _normalize_email(
            payload.get("profile_email"), "profile_email", "Profile email", errors
        ),
        "timezone": str(payload.get("timezone", "") or "").strip(),
        "date_format": str(payload.get("date_format", "") or "").strip(),
        "time_format": str(payload.get("time_format", "") or "").strip(),
        "default_sender_name": _clean_text(
            payload, "default_sender_name", "Default sender name", 120, errors
        ),
        "default_sender_email": _normalize_email(
            payload.get("default_sender_email"),
            "default_sender_email",
            "Default sender email",
            errors,
        ),
        "email_signature": _clean_text(
            payload, "email_signature", "Email signature", 10000, errors, multiline=True
        ),
        "email_footer": _clean_text(
            payload, "email_footer", "Email footer", 10000, errors, multiline=True
        ),
        "default_cc": _normalize_address_list(
            payload.get("default_cc"), "default_cc", "Default CC", errors
        ),
        "default_bcc": _normalize_address_list(
            payload.get("default_bcc"), "default_bcc", "Default BCC", errors
        ),
        "theme_preference": str(payload.get("theme_preference", "") or "").strip(),
    }

    if values["timezone"] not in TIMEZONES:
        errors["timezone"] = "Choose a supported timezone."
    if values["date_format"] not in DATE_FORMATS:
        errors["date_format"] = "Choose a supported date format."
    if values["time_format"] not in TIME_FORMATS:
        errors["time_format"] = "Choose 12-hour or 24-hour time."
    if values["theme_preference"] not in THEME_PREFERENCES:
        errors["theme_preference"] = "Choose light, dark, or system theme."
    if (
        "default_sender_email" not in errors
        and bool(values["default_sender_name"]) != bool(values["default_sender_email"])
    ):
        errors["default_sender_email"] = "Enter both a sender name and email, or leave both blank."

    cc_addresses = set(filter(None, values["default_cc"].split(", ")))
    bcc_addresses = set(filter(None, values["default_bcc"].split(", ")))
    overlap = cc_addresses.intersection(bcc_addresses)
    if overlap:
        errors["default_bcc"] = "The same address cannot be in both default CC and BCC."
    return values, errors


def save_settings(payload):
    values, errors = validate_settings_data(payload)
    if errors:
        raise SettingsValidationError(errors, values)
    settings = get_settings()
    for field, value in values.items():
        setattr(settings, field, value)
    db.session.commit()
    return settings


def save_theme_preference(theme):
    theme = str(theme or "").strip().casefold()
    if theme not in THEME_PREFERENCES:
        raise SettingsValidationError({"theme_preference": "Choose light, dark, or system theme."})
    settings = get_settings()
    settings.theme_preference = theme
    db.session.commit()
    return theme


def workspace_data_counts():
    return {
        "recipients": db.session.scalar(select(func.count(Recipient.id))) or 0,
        "campaigns": db.session.scalar(select(func.count(Campaign.id))) or 0,
        "drafts": db.session.scalar(select(func.count(EmailDraft.id))) or 0,
        "groups": db.session.scalar(select(func.count(RecipientGroup.id))) or 0,
        "send_history": db.session.scalar(select(func.count(EmailSendHistory.id))) or 0,
    }


def reset_workspace_data():
    counts = workspace_data_counts()
    from .campaign_worker_service import cancel_all_unstarted_campaign_jobs, utc_now

    # Serialize with worker claims and composer send reservations on SQLite.
    db.session.commit()
    if db.engine.dialect.name == "sqlite":
        db.session.connection().exec_driver_sql("BEGIN IMMEDIATE")

    active_worker = db.session.scalar(
        select(CampaignJob.id).where(
            CampaignJob.status == "running",
            CampaignJob.lease_expires_at > utc_now(),
        ).limit(1)
    )
    if active_worker is not None:
        db.session.rollback()
        raise WorkspaceResetBlocked(
            "A campaign worker is actively processing a recipient. Wait for that send to finish before resetting workspace data."
        )

    pending_composer_send = db.session.scalar(
        select(EmailSendHistory.id).where(
            EmailSendHistory.draft_id.is_not(None),
            EmailSendHistory.campaign_id.is_(None),
            EmailSendHistory.status == "pending",
        ).limit(1)
    )
    if pending_composer_send is not None:
        db.session.rollback()
        raise WorkspaceResetBlocked(
            "A composer send is still in progress. Wait for it to finish before resetting workspace data."
        )

    cancel_all_unstarted_campaign_jobs(
        reason="Workspace data was reset before this job completed.",
        commit=False,
    )
    db.session.execute(db.update(CampaignJob).values(email_send_history_id=None))
    for model in (EmailSendHistory, Campaign, RecipientGroup, EmailDraft, Recipient):
        db.session.execute(db.delete(model))
    db.session.commit()
    return counts
