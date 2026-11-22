from datetime import datetime, timezone
import hashlib
from secrets import token_urlsafe
from urllib.parse import urlsplit

from flask import current_app
from email_validator import EmailNotValidError, validate_email
from sqlalchemy import select

from ..extensions import db
from ..models import MailingProfile, Suppression, UnsubscribeToken


class UnsubscribeValidationError(ValueError):
    pass


def utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def normalize_email(value):
    try:
        return validate_email(str(value or "").strip(), check_deliverability=False, allow_smtputf8=False).normalized.casefold()
    except EmailNotValidError:
        raise UnsubscribeValidationError("Enter a valid email address.") from None


def create_unsubscribe_token(email, campaign_id):
    normalized = normalize_email(email)
    try:
        campaign_id = int(campaign_id)
    except (TypeError, ValueError):
        raise UnsubscribeValidationError("A campaign is required for unsubscribe links.") from None
    raw_token = token_urlsafe(32)
    record = UnsubscribeToken(
        token_hash=hashlib.sha256(raw_token.encode("ascii")).hexdigest(),
        email=normalized,
        campaign_id=campaign_id,
    )
    db.session.add(record)
    db.session.commit()
    return raw_token


def unsubscribe_url(token, *, config=None):
    if not token or any(char not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-" for char in token):
        raise UnsubscribeValidationError("The unsubscribe token is invalid.")
    config = current_app.config if config is None else config
    base = str(config.get("MAILPILOT_PUBLIC_BASE_URL", "") or "").strip().rstrip("/")
    try:
        parts = urlsplit(base)
        hostname = parts.hostname
    except ValueError:
        parts = None
        hostname = None
    if (
        parts is None
        or parts.scheme.casefold() != "https"
        or not hostname
        or parts.username
        or parts.password
        or parts.query
        or parts.fragment
    ):
        raise UnsubscribeValidationError(
            "Set MAILPILOT_PUBLIC_BASE_URL to the HTTPS address that recipients can use before sending campaigns."
        )
    return f"{base}/unsubscribe/{token}"


def get_unsubscribe_token(raw_token):
    raw_token = str(raw_token or "")
    if not raw_token or len(raw_token) > 100:
        return None
    digest = hashlib.sha256(raw_token.encode("ascii", errors="ignore")).hexdigest()
    return db.session.scalar(select(UnsubscribeToken).where(UnsubscribeToken.token_hash == digest))


def get_mailing_profile():
    profile = db.session.get(MailingProfile, 1)
    if profile is None:
        profile = MailingProfile(id=1, postal_address="")
        db.session.add(profile)
        db.session.commit()
    return profile


def save_postal_address(value):
    address = "\n".join(line.strip() for line in str(value or "").splitlines()).strip()
    if len(address) > 1000:
        raise UnsubscribeValidationError("The postal address must be 1,000 characters or fewer.")
    profile = get_mailing_profile()
    profile.postal_address = address
    db.session.commit()
    return profile


def is_suppressed(email, campaign_id=None):
    normalized = normalize_email(email)
    global_match = db.session.scalar(
        select(Suppression.id).where(
            Suppression.email == normalized,
            Suppression.scope == "global",
        ).limit(1)
    )
    if global_match is not None:
        return True
    if campaign_id is None:
        return False
    return db.session.scalar(
        select(Suppression.id).where(
            Suppression.email == normalized,
            Suppression.scope == "campaign",
            Suppression.campaign_id == int(campaign_id),
        ).limit(1)
    ) is not None


def add_suppression(email, *, scope="global", campaign_id=None, source="manual", reason=""):
    normalized = normalize_email(email)
    scope = str(scope or "").strip().casefold()
    source = str(source or "manual").strip().casefold()
    if scope not in {"global", "campaign"}:
        raise UnsubscribeValidationError("Choose global or campaign-level suppression.")
    if source not in {"unsubscribe", "manual", "bounce", "import"}:
        raise UnsubscribeValidationError("Choose a valid suppression source.")
    if scope == "global":
        campaign_id = None
        statement = select(Suppression).where(Suppression.email == normalized, Suppression.scope == "global")
    else:
        try:
            campaign_id = int(campaign_id)
        except (TypeError, ValueError):
            raise UnsubscribeValidationError("Choose a campaign for campaign-level suppression.") from None
        if campaign_id < 1:
            raise UnsubscribeValidationError("Choose a valid campaign.")
        statement = select(Suppression).where(
            Suppression.email == normalized,
            Suppression.scope == "campaign",
            Suppression.campaign_id == campaign_id,
        )
    existing = db.session.scalar(statement)
    if existing:
        return existing, False
    clean_reason = " ".join(str(reason or "").split())[:250]
    suppression = Suppression(
        email=normalized,
        scope=scope,
        campaign_id=campaign_id,
        source=source,
        reason=clean_reason,
    )
    db.session.add(suppression)
    db.session.commit()
    return suppression, True


def apply_unsubscribe(raw_token, *, scope="global"):
    token = get_unsubscribe_token(raw_token)
    if token is None:
        return None
    scope = str(scope or "global").strip().casefold()
    if scope == "campaign":
        if token.campaign_id is None:
            return None
        suppression, _created = add_suppression(
            token.email,
            scope="campaign",
            campaign_id=token.campaign_id,
            source="unsubscribe",
            reason="Recipient used a campaign unsubscribe link.",
        )
    elif scope == "global":
        suppression, _created = add_suppression(
            token.email,
            scope="global",
            source="unsubscribe",
            reason="Recipient opted out of all campaign email.",
        )
    else:
        return None
    if token.used_at is None:
        token.used_at = utc_now()
        db.session.commit()
    return suppression


def list_suppressions(*, search="", scope="all", page=1, per_page=25):
    statement = select(Suppression).order_by(Suppression.created_at.desc(), Suppression.id.desc())
    if scope in {"global", "campaign"}:
        statement = statement.where(Suppression.scope == scope)
    search = str(search or "").strip()[:120]
    if search:
        statement = statement.where(Suppression.email.ilike(f"%{search}%"))
    return db.paginate(statement, page=max(1, int(page)), per_page=per_page, error_out=False)


def remove_suppression(suppression_id):
    suppression = db.session.get(Suppression, int(suppression_id))
    if suppression is None:
        return False
    db.session.delete(suppression)
    db.session.commit()
    return True


def suppression_counts():
    from sqlalchemy import func

    return {
        "global": db.session.scalar(select(func.count(Suppression.id)).where(Suppression.scope == "global")) or 0,
        "campaign": db.session.scalar(select(func.count(Suppression.id)).where(Suppression.scope == "campaign")) or 0,
    }
