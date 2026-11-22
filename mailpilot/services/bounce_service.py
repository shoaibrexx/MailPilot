from datetime import datetime, timezone
from email.parser import BytesParser
from email.policy import default
import hashlib
import imaplib
import re
import ssl

from email_validator import EmailNotValidError, validate_email
from flask import current_app
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from ..extensions import db
from ..models import BounceEvent, BounceMailboxState, CampaignDelivery, CampaignDeliveryAttempt
from .unsubscribe_service import add_suppression


MAX_BOUNCE_MESSAGES_PER_POLL = 100
_ENHANCED_STATUS = re.compile(r"^[245]\.\d{1,3}\.\d{1,3}$")


def utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class BounceConfigurationError(ValueError):
    pass


def _imap_settings(config):
    server = str(config.get("MAIL_IMAP_SERVER", "imap.gmail.com") or "").strip().casefold()
    username = str(config.get("MAIL_IMAP_USERNAME", "") or "").strip()
    password = str(config.get("MAIL_IMAP_PASSWORD", "") or "")
    folder = str(config.get("MAIL_IMAP_FOLDER", "INBOX") or "").strip()
    try:
        port = int(config.get("MAIL_IMAP_PORT", 993))
    except (TypeError, ValueError):
        raise BounceConfigurationError("Invalid Gmail IMAP configuration.") from None
    if server != "imap.gmail.com" or port != 993 or not username or not password or not folder or any(ord(c) < 32 or ord(c) == 127 for c in folder):
        raise BounceConfigurationError("Gmail IMAP is not configured.")
    return server, port, username, password, folder[:200]


def imap_is_configured(config=None):
    if config is None:
        config = current_app.config
    try:
        _imap_settings(config)
    except BounceConfigurationError:
        return False
    return True


def _normalize_recipient(value):
    try:
        return validate_email(value, check_deliverability=False, allow_smtputf8=False).normalized.casefold()
    except (EmailNotValidError, TypeError):
        return None


def _original_message_id(message):
    for part in message.walk():
        if part.get_content_type().casefold() != "message/rfc822":
            continue
        payload = part.get_payload()
        for original in payload if isinstance(payload, list) else []:
            message_id = str(original.get("Message-ID", "") or "").strip()
            if message_id and len(message_id) <= 255 and not any(ord(c) < 32 for c in message_id):
                return message_id
    return ""


def parse_delivery_status(raw_message):
    """Parse only RFC 3464 failure/delay recipient blocks; no raw message is retained."""
    message = BytesParser(policy=default).parsebytes(raw_message)
    if message.get_content_type().casefold() != "multipart/report" or str(message.get_param("report-type", "")).casefold() != "delivery-status":
        return []
    message_id = _original_message_id(message)
    if not message_id:
        return []
    status_parts = [part for part in message.walk() if part.get_content_type().casefold() == "message/delivery-status"]
    events = []
    for part in status_parts:
        payload = part.get_payload()
        if not isinstance(payload, list):
            continue
        # RFC 3464 starts with per-message fields, followed by per-recipient blocks.
        for fields in payload[1:]:
            recipient_header = str(fields.get("Final-Recipient", "") or fields.get("Original-Recipient", "") or "")
            if ";" not in recipient_header:
                continue
            recipient = _normalize_recipient(recipient_header.split(";", 1)[1].strip())
            action = str(fields.get("Action", "") or "").strip().casefold()
            status_code = str(fields.get("Status", "") or "").strip()
            if not recipient or action not in {"failed", "delayed"} or not _ENHANCED_STATUS.fullmatch(status_code):
                continue
            if status_code.startswith("5."):
                classification = "permanent"
            elif status_code.startswith("4.") and action == "failed":
                classification = "temporary"
            else:
                # Delayed DSNs describe a still-pending route, not a confirmed bounce.
                continue
            events.append({
                "message_id": message_id,
                "recipient_email": recipient,
                "action": action,
                "status_code": status_code,
                "classification": classification,
            })
    return events


def process_delivery_status(raw_message, *, reported_at=None):
    reported_at = reported_at or utc_now()
    events = parse_delivery_status(raw_message)
    processed = 0
    for item in events:
        attempt = db.session.scalar(
            select(CampaignDeliveryAttempt)
            .join(CampaignDelivery, CampaignDelivery.id == CampaignDeliveryAttempt.delivery_id)
            .where(
                CampaignDeliveryAttempt.message_id == item["message_id"],
                CampaignDelivery.email == item["recipient_email"],
            )
            .limit(1)
        )
        if attempt is None:
            continue
        delivery = attempt.delivery
        source_key = hashlib.sha256(
            f"{item['message_id']}\0{item['recipient_email']}\0{item['action']}\0{item['status_code']}".encode("utf-8")
        ).hexdigest()
        if db.session.scalar(select(BounceEvent.id).where(BounceEvent.source_key == source_key)):
            continue
        bounce = BounceEvent(
            delivery_id=delivery.id,
            source_key=source_key,
            recipient_email=item["recipient_email"],
            classification=item["classification"],
            status_code=item["status_code"],
            # Store the enhanced status code only; DSN diagnostic text may contain server/user data.
            diagnostic=item["status_code"],
            bounced_at=reported_at,
        )
        db.session.add(bounce)
        delivery.bounced_at = reported_at
        if attempt.attempt_number == delivery.attempt_count:
            delivery.status = "bounced_permanent" if item["classification"] == "permanent" else "bounced_temporary"
            delivery.error_details = f"Bounce report {item['status_code']}."
        try:
            if item["classification"] == "permanent":
                add_suppression(
                    item["recipient_email"],
                    scope="global",
                    source="bounce",
                    reason=f"Permanent bounce {item['status_code']}.",
                )
            # add_suppression commits when it creates a new row, but an already
            # existing suppression does not; commit the event in both cases.
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            if db.session.scalar(select(BounceEvent.id).where(BounceEvent.source_key == source_key)):
                continue
            raise
        processed += 1
    return processed


def _response_value(client, name):
    _kind, values = client.response(name)
    if not values:
        return ""
    return values[0].decode("ascii", errors="ignore") if isinstance(values[0], bytes) else str(values[0])


def _message_bytes(fetch_data):
    for item in fetch_data or []:
        if isinstance(item, tuple) and len(item) > 1 and isinstance(item[1], bytes):
            return item[1]
    return b""


def poll_bounce_mailbox(config=None):
    """Poll Gmail IMAP for newly arrived DSNs. Credentials are read only from config."""
    config = current_app.config if config is None else config
    if not imap_is_configured(config):
        return 0
    server, port, username, password, folder = _imap_settings(config)
    mailbox_hash = hashlib.sha256(
        f"{server.casefold()}|{username.casefold()}|{folder.casefold()}".encode("utf-8")
    ).hexdigest()
    client = None
    processed = 0
    try:
        client = imaplib.IMAP4_SSL(server, port, ssl_context=ssl.create_default_context(), timeout=int(config.get("MAIL_TIMEOUT", 20)))
        client.login(username, password)
        selected, _data = client.select(folder, readonly=True)
        if selected != "OK":
            raise OSError("Mailbox select failed")
        uidvalidity = _response_value(client, "UIDVALIDITY")[:64]
        uidnext_text = _response_value(client, "UIDNEXT")
        try:
            uidnext = max(1, int(uidnext_text))
        except (TypeError, ValueError):
            uidnext = 1
        state = db.session.scalar(select(BounceMailboxState).where(BounceMailboxState.mailbox_hash == mailbox_hash))
        if state is None:
            # On first setup, inspect at most the most recent 1,000 mailbox messages.
            state = BounceMailboxState(mailbox_hash=mailbox_hash, uidvalidity=uidvalidity, last_uid=max(0, uidnext - 1001))
            db.session.add(state)
            db.session.commit()
        elif state.uidvalidity != uidvalidity:
            state.uidvalidity = uidvalidity
            state.last_uid = max(0, uidnext - 1001)
            state.last_error_type = ""
            db.session.commit()

        search_type, search_data = client.uid("SEARCH", None, f"{state.last_uid + 1}:*")
        if search_type != "OK" or not search_data:
            return 0
        uids = [value for value in (search_data[0] or b"").split() if value.isdigit()]
        for uid in uids[:MAX_BOUNCE_MESSAGES_PER_POLL]:
            fetch_type, fetch_data = client.uid("FETCH", uid, "(BODY.PEEK[])")
            if fetch_type != "OK":
                break
            message_bytes = _message_bytes(fetch_data)
            if message_bytes:
                processed += process_delivery_status(message_bytes)
            state.last_uid = int(uid)
            state.last_polled_at = utc_now()
            state.last_error_type = ""
            db.session.commit()
        return processed
    except Exception as error:
        db.session.rollback()
        state = db.session.scalar(select(BounceMailboxState).where(BounceMailboxState.mailbox_hash == mailbox_hash))
        if state is not None:
            state.last_error_type = type(error).__name__[:80]
            state.last_polled_at = utc_now()
            db.session.commit()
        # The caller may log only this exception type, never IMAP details or credentials.
        raise BounceConfigurationError(type(error).__name__) from None
    finally:
        if client is not None:
            try:
                client.logout()
            except Exception:
                pass
