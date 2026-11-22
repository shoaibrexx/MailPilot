import re

from flask import current_app
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from ..extensions import db
from ..models import Campaign, EmailDraft, EmailSendHistory, Recipient
from .composer_service import DraftValidationError, sanitize_email_html, update_draft, validate_draft_data
from .email_service import (
    MAX_EMAIL_RECIPIENTS,
    SMTPDeliveryUncertain,
    MailPilotSMTPError,
    SMTPMessageValidationError,
    send_html_email,
)
from .email_service import _plain_text
from .send_limit_service import DailyLimitExceeded, begin_daily_send_reservation, ensure_daily_send_capacity
from .unsubscribe_service import is_suppressed


SUBMISSION_KEY_PATTERN = re.compile(r"^[A-Za-z0-9_-]{32,80}$")


class SendValidationError(ValueError):
    def __init__(self, errors):
        self.errors = errors
        super().__init__("Please confirm the audience and correct the send details.")


def _submission_key(payload):
    value = str(payload.get("submission_key", "") or "").strip()
    if not SUBMISSION_KEY_PATTERN.fullmatch(value):
        raise SendValidationError({"send": "Refresh the page before sending this message."})
    return value


def _consent_confirmed(payload):
    return str(payload.get("consent_confirmed", "") or "").strip().casefold() in {
        "1", "true", "yes", "on"
    }


def _create_history(*, submission_key, draft_id, campaign_id, addresses, subject, consent_confirmed):
    existing = db.session.scalar(
        select(EmailSendHistory).where(EmailSendHistory.submission_key == submission_key)
    )
    if existing:
        return existing, False
    history = EmailSendHistory(
        submission_key=submission_key,
        draft_id=draft_id,
        campaign_id=campaign_id,
        recipient_addresses=addresses,
        recipient_count=sum(len(items) for items in addresses.values()),
        subject=subject,
        status="pending",
        consent_confirmed=consent_confirmed,
    )
    db.session.add(history)
    try:
        db.session.commit()
        return history, True
    except IntegrityError:
        db.session.rollback()
        existing = db.session.scalar(
            select(EmailSendHistory).where(EmailSendHistory.submission_key == submission_key)
        )
        if existing is None and campaign_id is not None:
            existing = db.session.scalar(
                select(EmailSendHistory)
                .where(
                    EmailSendHistory.campaign_id == campaign_id,
                    EmailSendHistory.status.in_(("pending", "sent", "partial", "uncertain")),
                )
                .order_by(EmailSendHistory.attempted_at.desc(), EmailSendHistory.id.desc())
            )
        if existing is None and draft_id is not None:
            existing = db.session.scalar(
                select(EmailSendHistory)
                .where(
                    EmailSendHistory.draft_id == draft_id,
                    EmailSendHistory.status.in_(("pending", "partial", "uncertain")),
                )
                .order_by(EmailSendHistory.attempted_at.desc(), EmailSendHistory.id.desc())
            )
        if existing:
            return existing, False
        raise


def _finish(history, status, error_details=None):
    history.status = status
    history.error_details = str(error_details or "")[:1000] or None
    history.completed_at = utc_now()
    db.session.commit()
    return history


def utc_now():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).replace(tzinfo=None)


def list_send_history(limit=8, *, draft_id=None, campaign_id=None):
    limit = max(1, min(int(limit), 50))
    statement = select(EmailSendHistory)
    if draft_id is not None:
        statement = statement.where(EmailSendHistory.draft_id == int(draft_id))
    if campaign_id is not None:
        statement = statement.where(EmailSendHistory.campaign_id == int(campaign_id))
    return db.session.scalars(
        statement.order_by(EmailSendHistory.attempted_at.desc(), EmailSendHistory.id.desc()).limit(limit)
    ).all()


def has_campaign_send(campaign_id):
    return db.session.scalar(
        select(EmailSendHistory.id).where(
            EmailSendHistory.campaign_id == int(campaign_id),
            EmailSendHistory.status.in_(("pending", "sent", "partial", "uncertain")),
        ).limit(1)
    ) is not None


def blocking_draft_send(draft_id):
    return db.session.scalar(
        select(EmailSendHistory)
        .where(
            EmailSendHistory.draft_id == int(draft_id),
            EmailSendHistory.status.in_(("pending", "partial", "uncertain")),
        )
        .order_by(EmailSendHistory.attempted_at.desc(), EmailSendHistory.id.desc())
        .limit(1)
    )


def _deliver(history, *, to, cc, bcc, subject, body_html):
    try:
        result = send_html_email(
            to=to,
            cc=cc,
            bcc=bcc,
            subject=subject,
            body_html=body_html,
            config=current_app.config,
        )
    except SMTPMessageValidationError as error:
        _finish(history, "failed", "The message was rejected by local validation.")
        return history, str(error)
    except SMTPDeliveryUncertain as error:
        _finish(history, "uncertain", error.public_message)
        return history, error.public_message
    except MailPilotSMTPError as error:
        _finish(history, "failed", error.public_message)
        return history, error.public_message
    except Exception as error:
        # Log no exception text: an SMTP exception may contain untrusted server data.
        current_app.logger.error("Email delivery stopped after an unexpected %s.", type(error).__name__)
        message = "The SMTP outcome could not be confirmed. Check the sender's Sent folder before retrying."
        _finish(history, "uncertain", message)
        return history, message

    if result.refused_count:
        if result.accepted_count:
            status = "partial"
            detail = "Gmail refused one or more recipients; other recipients may have received the message."
        else:
            status = "failed"
            detail = "Gmail refused all recipients. Check the saved addresses and try again."
        _finish(history, status, detail)
        return history, detail

    _finish(history, "sent")
    return history, None


def send_draft(draft: EmailDraft, payload):
    existing = blocking_draft_send(draft.id)
    if existing is not None:
        return (
            existing,
            "A previous draft send is pending, partial, or uncertain. Check send history and the sender's Sent folder before retrying.",
            True,
        )
    values, errors = validate_draft_data(payload)
    if errors:
        raise DraftValidationError(errors)
    recipient_count = sum(
        len(values[field]) for field in ("to_recipients", "cc_recipients", "bcc_recipients")
    )
    if recipient_count > MAX_EMAIL_RECIPIENTS:
        raise SendValidationError({"to_recipients": f"A message can be sent to at most {MAX_EMAIL_RECIPIENTS} recipients."})
    selected_addresses = [
        address
        for field in ("to_recipients", "cc_recipients", "bcc_recipients")
        for address in values[field]
    ]
    if any(is_suppressed(address) for address in selected_addresses):
        raise SendValidationError({"to_recipients": "At least one selected address has opted out of all campaigns and cannot be sent this email."})
    if not _consent_confirmed(payload):
        raise SendValidationError({"consent": "Confirm that every recipient agreed to receive this message before sending."})

    try:
        begin_daily_send_reservation()
        if db.session.scalar(select(EmailDraft.id).where(EmailDraft.id == draft.id)) is None:
            db.session.rollback()
            raise SendValidationError({"send": "This draft was removed before sending. Refresh the composer and review your current drafts."})
        existing = blocking_draft_send(draft.id)
        if existing is not None:
            db.session.rollback()
            return (
                existing,
                "A previous draft send is pending, partial, or uncertain. Check send history and the sender's Sent folder before retrying.",
                True,
            )
        ensure_daily_send_capacity(recipient_count, now=utc_now())
        if any(is_suppressed(address) for address in selected_addresses):
            raise SendValidationError({"to_recipients": "At least one selected address has opted out of all campaigns and cannot be sent this email."})
    except DailyLimitExceeded as error:
        db.session.rollback()
        raise SendValidationError({"send": str(error)}) from None
    except SendValidationError:
        db.session.rollback()
        raise

    key = _submission_key(payload)
    update_draft(draft, payload)
    addresses = {
        "to": values["to_recipients"],
        "cc": values["cc_recipients"],
        "bcc": values["bcc_recipients"],
    }
    history, created = _create_history(
        submission_key=key,
        draft_id=draft.id,
        campaign_id=None,
        addresses=addresses,
        subject=values["subject"],
        consent_confirmed=True,
    )
    if not created:
        db.session.rollback()
        return history, "This send submission was already processed.", True
    history, error = _deliver(
        history,
        to=addresses["to"],
        cc=addresses["cc"],
        bcc=addresses["bcc"],
        subject=values["subject"],
        body_html=values["body_html"],
    )
    return history, error, False


def _campaign_addresses(campaign):
    snapshot = campaign.recipient_snapshot or []
    ids = []
    for entry in snapshot:
        try:
            recipient_id = int(entry.get("id"))
        except (AttributeError, TypeError, ValueError):
            raise SendValidationError({"audience": "The campaign audience snapshot is invalid. Re-save the campaign before sending."}) from None
        if recipient_id not in ids:
            ids.append(recipient_id)
    recipients = db.session.scalars(
        select(Recipient).where(Recipient.id.in_(ids), Recipient.is_active.is_(True))
    ).all() if ids else []
    current = {recipient.id: recipient for recipient in recipients}
    addresses = []
    for entry in snapshot:
        recipient_id = int(entry["id"])
        recipient = current.get(recipient_id)
        snapshot_email = str(entry.get("email", "") or "").casefold()
        if recipient is None or recipient.email.casefold() != snapshot_email:
            raise SendValidationError({"audience": "The campaign audience changed since it was saved. Edit and save the campaign to refresh its active recipients before sending."})
        if recipient.email not in addresses:
            addresses.append(recipient.email)
    if not addresses:
        raise SendValidationError({"audience": "Select at least one active recipient before sending this campaign."})
    if len(addresses) > MAX_EMAIL_RECIPIENTS:
        raise SendValidationError({"audience": f"A campaign send is limited to {MAX_EMAIL_RECIPIENTS} recipients per message."})
    return addresses


def send_campaign(campaign: Campaign, payload):
    if campaign.status != "ready":
        raise SendValidationError({"send": "Only campaigns marked Ready can be sent."})
    if has_campaign_send(campaign.id):
        existing = db.session.scalar(
            select(EmailSendHistory)
            .where(
                EmailSendHistory.campaign_id == campaign.id,
                EmailSendHistory.status.in_(("pending", "sent", "partial", "uncertain")),
            )
            .order_by(EmailSendHistory.attempted_at.desc(), EmailSendHistory.id.desc())
        )
        return existing, "This campaign already has a send attempt that may have reached recipients.", True
    if not _consent_confirmed(payload):
        raise SendValidationError({"consent": "Confirm that the selected recipients agreed to receive this campaign."})
    addresses = _campaign_addresses(campaign)
    body_html = sanitize_email_html(campaign.body_html or "")
    if not campaign.subject.strip() or not _plain_text(body_html):
        raise SendValidationError({"send": "The campaign needs a subject and message text before sending."})
    key = _submission_key(payload)
    recipient_addresses = {"to": [], "cc": [], "bcc": addresses}
    history, created = _create_history(
        submission_key=key,
        draft_id=campaign.email_draft_id,
        campaign_id=campaign.id,
        addresses=recipient_addresses,
        subject=campaign.subject,
        consent_confirmed=True,
    )
    if not created:
        return history, "This campaign send submission was already processed.", True
    history, error = _deliver(
        history,
        to=[],
        cc=[],
        bcc=addresses,
        subject=campaign.subject,
        body_html=body_html,
    )
    return history, error, False


def clear_send_history():
    return db.session.execute(db.delete(EmailSendHistory)).rowcount or 0

