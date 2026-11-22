from datetime import datetime, timedelta, timezone
from email.utils import make_msgid
from html import escape
import logging
import re
from secrets import token_urlsafe
import socket
import time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import current_app
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from ..extensions import db
from ..models import Campaign, CampaignDelivery, CampaignDeliveryAttempt, CampaignJob, EmailSendHistory, MailingProfile, Recipient, WorkspaceSettings
from .send_limit_service import begin_daily_send_reservation, daily_recipient_limit, daily_send_usage
from .campaign_service import _TextExtractor
from .email_service import (
    MAX_EMAIL_RECIPIENTS,
    MailPilotSMTPError,
    SMTPAuthenticationFailure,
    SMTPConfigurationError,
    SMTPConnectionFailure,
    SMTPDeliveryUncertain,
    SMTPMessageValidationError,
    send_html_email,
    smtp_is_configured,
)
from .notification_service import create_notification
from .send_history_service import SendValidationError, _campaign_addresses, _consent_confirmed, _submission_key
from .unsubscribe_service import UnsubscribeValidationError, create_unsubscribe_token, is_suppressed, normalize_email, unsubscribe_url


MAX_CAMPAIGN_JOB_RECIPIENTS = 100
MAX_DELIVERY_ATTEMPTS = 5
LEASE_DURATION = timedelta(minutes=2)
RETRY_BASE = timedelta(minutes=5)
RETRY_MAX = timedelta(hours=6)
WORKER_POLL_SECONDS = 5
_JOB_KEY_PATTERN = re.compile(r"^[A-Za-z0-9:_-]{32,100}$")
logger = logging.getLogger(__name__)


class CampaignQueueError(ValueError):
    pass


def utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _public_send_ready():
    if not smtp_is_configured(current_app.config):
        raise CampaignQueueError("Configure Gmail SMTP in Settings before queuing campaign delivery.")
    try:
        unsubscribe_url("token-check")
    except UnsubscribeValidationError as error:
        raise CampaignQueueError(str(error)) from None
    profile = db.session.get(MailingProfile, 1)
    if profile is None or not profile.postal_address.strip():
        raise CampaignQueueError("Add the workspace's valid postal mailing address in Settings before sending campaigns.")


def _campaign_addresses_for_queue(campaign):
    addresses = _campaign_addresses(campaign)
    if len(addresses) > MAX_CAMPAIGN_JOB_RECIPIENTS:
        raise SendValidationError({"audience": f"Campaign execution is limited to {MAX_CAMPAIGN_JOB_RECIPIENTS} recipients per approved job."})
    try:
        return [normalize_email(address) for address in addresses]
    except UnsubscribeValidationError:
        raise SendValidationError({"audience": "The saved campaign contains an invalid recipient address. Edit and save the audience again."}) from None


def _make_job(*, campaign, addresses, submission_key, scheduled_for, kind):
    if not 1 <= len(addresses) <= MAX_CAMPAIGN_JOB_RECIPIENTS:
        raise CampaignQueueError(f"A campaign job must contain between 1 and {MAX_CAMPAIGN_JOB_RECIPIENTS} saved recipients.")
    begin_daily_send_reservation()
    if db.session.scalar(select(Campaign.id).where(Campaign.id == campaign.id)) is None:
        db.session.rollback()
        raise CampaignQueueError("This campaign was removed before delivery could be queued. Refresh the campaign list.")
    already = db.session.scalar(
        select(CampaignJob).where(
            CampaignJob.campaign_id == campaign.id,
            CampaignJob.status.in_(("queued", "running", "retrying", "uncertain")),
        ).limit(1)
    )
    if already is not None:
        db.session.rollback()
        return already, False
    history = db.session.scalar(
        select(EmailSendHistory)
        .where(EmailSendHistory.campaign_id == campaign.id)
        .order_by(EmailSendHistory.attempted_at.desc(), EmailSendHistory.id.desc())
        .limit(1)
    )
    if history and history.status in {"pending", "sent", "partial", "uncertain"}:
        existing_job = db.session.scalar(select(CampaignJob).where(CampaignJob.email_send_history_id == history.id))
        db.session.rollback()
        return existing_job, False
    if db.session.scalar(select(EmailSendHistory.id).where(EmailSendHistory.submission_key == submission_key)):
        existing_history = db.session.scalar(select(EmailSendHistory).where(EmailSendHistory.submission_key == submission_key))
        existing_job = db.session.scalar(select(CampaignJob).where(CampaignJob.email_send_history_id == existing_history.id))
        db.session.rollback()
        return existing_job, False

    job = CampaignJob(
        campaign_id=campaign.id,
        job_key=submission_key,
        kind=kind,
        status="queued",
        consent_confirmed=True,
        scheduled_for=scheduled_for,
        total_recipients=len(addresses),
        snapshot={
            "subject": campaign.subject,
            "body_html": campaign.body_html,
            "recipients": addresses,
            "campaign_updated_at": campaign.updated_at.isoformat() if campaign.updated_at else "",
        },
    )
    history = EmailSendHistory(
        submission_key=submission_key,
        draft_id=campaign.email_draft_id,
        campaign_id=campaign.id,
        recipient_addresses={"to": [], "cc": [], "bcc": addresses},
        recipient_count=len(addresses),
        subject=campaign.subject,
        status="pending",
        consent_confirmed=True,
    )
    db.session.add(history)
    db.session.flush()
    job.email_send_history_id = history.id
    db.session.add(job)
    db.session.flush()
    for address in addresses:
        suppressed = is_suppressed(address, campaign.id)
        db.session.add(CampaignDelivery(
            job_id=job.id,
            email=address,
            status="suppressed" if suppressed else "pending",
            error_details="Address was already suppressed when the campaign was queued." if suppressed else "",
        ))
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        active = db.session.scalar(
            select(CampaignJob).where(
                CampaignJob.campaign_id == campaign.id,
                CampaignJob.status.in_(("queued", "running", "retrying", "uncertain")),
            ).limit(1)
        )
        return active, False
    return job, True


def queue_campaign_send(campaign, payload):
    if campaign.status != "ready":
        raise SendValidationError({"send": "Only campaigns marked Ready can be queued for immediate sending."})
    if not _consent_confirmed(payload):
        raise SendValidationError({"consent": "Confirm that the selected recipients agreed to receive this campaign."})
    key = _submission_key(payload)
    _public_send_ready()
    addresses = _campaign_addresses_for_queue(campaign)
    if not campaign.subject.strip():
        raise SendValidationError({"send": "The campaign needs a subject before sending."})
    body = _TextExtractor()
    body.feed(campaign.body_html or "")
    body.close()
    if not body.text().strip():
        raise SendValidationError({"send": "The campaign needs message text before sending."})
    return _make_job(
        campaign=campaign,
        addresses=addresses,
        submission_key=key,
        scheduled_for=utc_now(),
        kind="immediate",
    )


def _scheduled_time_utc(campaign):
    if campaign.scheduled_at is None:
        raise CampaignQueueError("Choose and save a schedule date and time before authorizing scheduled sending.")
    settings = db.session.get(WorkspaceSettings, 1)
    timezone_name = settings.timezone if settings else "UTC"
    try:
        zone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        raise CampaignQueueError("The workspace timezone is invalid. Update it in Settings.") from None
    local_time = campaign.scheduled_at
    aware_time = local_time.replace(tzinfo=zone)
    if aware_time.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None) != local_time:
        raise CampaignQueueError("The scheduled local time falls in a daylight-saving gap. Choose another time.")
    second_occurrence = local_time.replace(tzinfo=zone, fold=1)
    if aware_time.utcoffset() != second_occurrence.utcoffset():
        raise CampaignQueueError("The scheduled local time occurs twice during a daylight-saving transition. Choose another time.")
    return aware_time.astimezone(timezone.utc).replace(tzinfo=None)


def authorize_scheduled_campaign(campaign, payload):
    if campaign.status != "scheduled":
        raise CampaignQueueError("Only a campaign saved with Scheduled status can be authorized for automatic sending.")
    if not _consent_confirmed(payload):
        raise SendValidationError({"consent": "Confirm the scheduled send and recipient consent before authorizing it."})
    _public_send_ready()
    addresses = _campaign_addresses_for_queue(campaign)
    key = f"schedule:{campaign.id}:{token_urlsafe(28)}"
    due = _scheduled_time_utc(campaign)
    return _make_job(
        campaign=campaign,
        addresses=addresses,
        submission_key=key,
        scheduled_for=due,
        kind="scheduled",
    )


def cancel_campaign_jobs(campaign_id, *, reason="Campaign changed before delivery began."):
    jobs = db.session.scalars(
        select(CampaignJob).where(
            CampaignJob.campaign_id == int(campaign_id),
            CampaignJob.status.in_(("queued", "retrying")),
        )
    ).all()
    for job in jobs:
        accepted = False
        job.status = "cancelled"
        job.error_details = reason[:500]
        job.completed_at = utc_now()
        job.lease_owner = None
        job.lease_expires_at = None
        if job.email_send_history:
            accepted = any(delivery.status in {"accepted", "bounced_temporary", "bounced_permanent"} for delivery in job.deliveries)
            job.email_send_history.status = "partial" if accepted else "failed"
            job.email_send_history.error_details = reason[:1000]
            job.email_send_history.completed_at = utc_now()
    db.session.commit()
    return len(jobs)


def list_campaign_jobs(campaign_id, *, limit=10):
    return db.session.scalars(
        select(CampaignJob)
        .where(CampaignJob.campaign_id == int(campaign_id))
        .order_by(CampaignJob.created_at.desc(), CampaignJob.id.desc())
        .limit(max(1, min(int(limit), 50)))
    ).all()


def campaign_has_running_job(campaign_id):
    return db.session.scalar(
        select(CampaignJob.id).where(
            CampaignJob.campaign_id == int(campaign_id),
            CampaignJob.status == "running",
        ).limit(1)
    ) is not None


def cancel_all_unstarted_campaign_jobs(*, reason, commit=True):
    jobs = db.session.scalars(
        select(CampaignJob).where(CampaignJob.status.in_(("queued", "running", "retrying")))
    ).all()
    now = utc_now()
    for job in jobs:
        job.status = "cancelled"
        job.error_details = str(reason or "Queued delivery was cancelled.")[:500]
        job.completed_at = now
        job.lease_owner = None
        job.lease_expires_at = None
        any_uncertain = False
        accepted = False
        for delivery in job.deliveries:
            if delivery.status == "pending":
                delivery.status = "suppressed"
                delivery.error_details = job.error_details
            elif delivery.status == "sending":
                delivery.status = "uncertain"
                delivery.error_details = "Workspace data was reset while SMTP submission was in progress; acceptance is unknown."
                any_uncertain = True
                if delivery.attempts and delivery.attempts[-1].status == "sending":
                    delivery.attempts[-1].status = "uncertain"
                    delivery.attempts[-1].error_details = delivery.error_details
                    delivery.attempts[-1].completed_at = now
            accepted = accepted or delivery.status in {"accepted", "bounced_temporary", "bounced_permanent"}
        if job.email_send_history:
            job.email_send_history.status = "uncertain" if any_uncertain else ("partial" if accepted else "failed")
            job.email_send_history.error_details = job.error_details[:1000]
            job.email_send_history.completed_at = now
    if commit:
        db.session.commit()
    return len(jobs)


def _recover_stale_jobs(now):
    stale = db.session.scalars(
        select(CampaignJob).where(
            CampaignJob.status == "running",
            CampaignJob.lease_expires_at.is_not(None),
            CampaignJob.lease_expires_at <= now,
        )
    ).all()
    for job in stale:
        uncertain = False
        for delivery in job.deliveries:
            if delivery.status == "sending":
                delivery.status = "uncertain"
                delivery.error_details = "Worker stopped during SMTP submission; the recipient was not retried because acceptance is unknown."
                if delivery.attempts and delivery.attempts[-1].status == "sending":
                    delivery.attempts[-1].status = "uncertain"
                    delivery.attempts[-1].error_details = delivery.error_details
                    delivery.attempts[-1].completed_at = now
                uncertain = True
        job.status = "uncertain" if uncertain else "retrying"
        job.error_details = "Worker lease expired. SMTP-in-progress deliveries were marked uncertain." if uncertain else "Worker lease expired before submission; remaining recipients will resume."
        job.lease_owner = None
        job.lease_expires_at = None
        if uncertain and job.email_send_history:
            job.email_send_history.status = "uncertain"
            job.email_send_history.error_details = job.error_details
    if stale:
        db.session.commit()
    return len(stale)


def _claim_due_job(worker_id, now):
    # BEGIN IMMEDIATE serializes the read/claim pair across independent worker processes.
    db.session.remove()
    with db.engine.connect() as connection:
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        job_id = connection.scalar(
            select(CampaignJob.id)
            .where(
                CampaignJob.status.in_(("queued", "retrying")),
                CampaignJob.scheduled_for <= now,
                (CampaignJob.lease_expires_at.is_(None)) | (CampaignJob.lease_expires_at <= now),
            )
            .order_by(CampaignJob.scheduled_for, CampaignJob.id)
            .limit(1)
        )
        if job_id is None:
            connection.commit()
            return None
        connection.execute(
            update(CampaignJob.__table__).where(CampaignJob.id == job_id).values(
                status="running",
                lease_owner=worker_id,
                lease_expires_at=now + LEASE_DURATION,
                started_at=now,
                attempt_count=CampaignJob.attempt_count + 1,
                updated_at=now,
            )
        )
        connection.commit()
        return int(job_id)


def _safe_retry_at(delivery, now):
    delay = min(RETRY_BASE.total_seconds() * (2 ** max(0, delivery.attempt_count - 1)), RETRY_MAX.total_seconds())
    return now + timedelta(seconds=delay)


def _delivery_footer(email, campaign_id):
    raw_token = create_unsubscribe_token(email, campaign_id)
    url = unsubscribe_url(raw_token)
    profile = db.session.get(MailingProfile, 1)
    address_lines = "<br>".join(escape(line) for line in (profile.postal_address or "").splitlines())
    html = (
        '<hr><div style="font-size:12px;color:#555;line-height:1.5">'
        '<p>You are receiving this email because you opted in to communications from this sender.</p>'
        f'<p>{address_lines}</p>'
        f'<p><a href="{escape(url, quote=True)}">Unsubscribe from these emails</a></p>'
        "</div>"
    )
    return html, url


def _cancel_if_campaign_changed(job, *, now):
    if job.campaign_id is None:
        return False
    campaign = db.session.get(Campaign, job.campaign_id)
    expected = str(job.snapshot.get("campaign_updated_at", "") or "")
    actual = campaign.updated_at.isoformat() if campaign and campaign.updated_at else ""
    if campaign is not None and expected == actual:
        return False
    job.status = "cancelled"
    job.error_details = "Campaign was changed or deleted after this job was authorized; unsent recipients were cancelled."
    job.completed_at = now
    job.lease_owner = None
    job.lease_expires_at = None
    accepted = False
    uncertain = False
    for delivery in job.deliveries:
        if delivery.status == "pending":
            delivery.status = "suppressed"
            delivery.error_details = job.error_details
        elif delivery.status == "sending":
            delivery.status = "uncertain"
            delivery.error_details = "Campaign changed during SMTP submission; acceptance is unknown."
            uncertain = True
        accepted = accepted or delivery.status in {"accepted", "bounced_temporary", "bounced_permanent"}
    if job.email_send_history:
        job.email_send_history.status = "uncertain" if uncertain else ("partial" if accepted else "failed")
        job.email_send_history.error_details = job.error_details[:1000]
        job.email_send_history.completed_at = now
    db.session.commit()
    return True


def _reserve_delivery_attempt(job_id, delivery_id, worker_id, *, now, message_id):
    """Atomically reserve one SMTP recipient attempt against the local rolling cap."""
    begin_daily_send_reservation()
    job = db.session.get(CampaignJob, job_id)
    delivery = db.session.get(CampaignDelivery, delivery_id)
    if job is None or delivery is None or job.status != "running" or job.lease_owner != worker_id:
        db.session.rollback()
        return None, "stopped"
    if _cancel_if_campaign_changed(job, now=now):
        return None, "stopped"
    if is_suppressed(delivery.email, job.campaign_id):
        delivery.status = "suppressed"
        delivery.retry_at = None
        delivery.error_details = "Address became suppressed before SMTP submission."
        db.session.commit()
        return None, "skipped"
    active_contact_id = db.session.scalar(
        select(Recipient.id).where(
            Recipient.email == delivery.email,
            Recipient.is_active.is_(True),
        ).limit(1)
    )
    if active_contact_id is None:
        delivery.status = "suppressed"
        delivery.retry_at = None
        delivery.error_details = "Excluded at execution because the saved recipient is no longer active."
        db.session.commit()
        return None, "skipped"

    used, next_available = daily_send_usage(now)
    if used >= daily_recipient_limit():
        delivery.retry_at = next_available
        job.status = "retrying"
        job.scheduled_for = next_available
        job.lease_owner = None
        job.lease_expires_at = None
        job.error_details = "The local rolling 24-hour recipient cap was reached; this job will resume when capacity opens."
        db.session.commit()
        return None, "deferred"

    attempt = CampaignDeliveryAttempt(
        delivery_id=delivery.id,
        attempt_number=delivery.attempt_count + 1,
        message_id=message_id,
        status="sending",
        attempted_at=now,
    )
    db.session.add(attempt)
    job.lease_expires_at = now + LEASE_DURATION
    delivery.status = "sending"
    delivery.attempt_count += 1
    delivery.message_id = message_id
    delivery.last_attempt_at = now
    delivery.retry_at = None
    db.session.commit()
    return attempt, "started"


def _record_smtp_result(delivery, result, now):
    delivery.message_id = result.message_id[:255] if result.message_id else delivery.message_id
    if result.accepted_count == 1 and result.refused_count == 0:
        delivery.status = "accepted"
        delivery.accepted_at = now
        delivery.error_details = ""
        delivery.retry_at = None
        return "accepted"
    refusal = next(iter((result.refused or {}).values()), None)
    try:
        code = int(refusal[0]) if refusal else 0
    except (TypeError, ValueError, IndexError):
        code = 0
    if 400 <= code < 500:
        delivery.status = "temporary_failure"
        delivery.retry_at = _safe_retry_at(delivery, now) if delivery.attempt_count < MAX_DELIVERY_ATTEMPTS else None
        delivery.error_details = f"SMTP returned temporary response {code}."[:500]
        return "temporary_failure"
    delivery.status = "permanent_failure"
    delivery.retry_at = None
    delivery.error_details = f"SMTP rejected the recipient with response {code}." if code else "SMTP rejected the recipient."
    return "permanent_failure"


def _smtp_code(result):
    refusal = next(iter((result.refused or {}).values()), None)
    try:
        return str(int(refusal[0]))[:16] if refusal else ""
    except (TypeError, ValueError, IndexError):
        return ""


def _complete_job(job, *, now):
    statuses = [delivery.status for delivery in job.deliveries]
    accepted = statuses.count("accepted") + statuses.count("bounced_temporary") + statuses.count("bounced_permanent")
    if job.status in {"failed", "uncertain", "cancelled"}:
        job.completed_at = job.completed_at or now
        job.lease_owner = None
        job.lease_expires_at = None
        if job.email_send_history:
            job.email_send_history.status = "uncertain" if job.status == "uncertain" else ("partial" if accepted else "failed")
            job.email_send_history.error_details = job.error_details[:1000] or None
            job.email_send_history.completed_at = now
        return
    retryable = any(
        delivery.status == "pending"
        or (delivery.status == "temporary_failure" and delivery.retry_at is not None and delivery.attempt_count < MAX_DELIVERY_ATTEMPTS)
        for delivery in job.deliveries
    )
    uncertain = "uncertain" in statuses
    if any(status == "sending" for status in statuses):
        job.status = "running"
        return
    if retryable:
        job.status = "retrying"
        retry_times = [delivery.retry_at for delivery in job.deliveries if delivery.retry_at and delivery.status in {"temporary_failure", "pending"}]
        job.scheduled_for = min(retry_times or [now + RETRY_BASE])
        return
    if uncertain:
        job.status = "uncertain"
    elif accepted and any(status not in {"accepted", "bounced_temporary", "bounced_permanent"} for status in statuses):
        job.status = "partial"
    elif accepted:
        job.status = "completed"
    elif any(status in {"suppressed", "temporary_failure", "permanent_failure"} for status in statuses):
        job.status = "partial" if any(status == "suppressed" for status in statuses) else "failed"
    else:
        job.status = "failed"
    if job.status not in {"running", "retrying"}:
        job.completed_at = now
        job.lease_owner = None
        job.lease_expires_at = None
        if job.email_send_history:
            history = job.email_send_history
            history.status = {
                "completed": "sent",
                "partial": "partial",
                "failed": "failed",
                "uncertain": "uncertain",
            }[job.status]
            if job.status != "completed":
                summary = f"{accepted} SMTP accepted; {statuses.count('suppressed')} excluded/suppressed; {statuses.count('permanent_failure')} permanent failures; {statuses.count('uncertain')} uncertain."
                history.error_details = summary[:1000]
            history.completed_at = now
        if job.status in {"completed", "partial", "failed", "uncertain"}:
            category = "success" if job.status == "completed" else ("warning" if job.status == "partial" else "error")
            create_notification(
                category,
                f"Campaign job #{job.id} finished with {accepted} SMTP-accepted recipient(s). This does not confirm inbox delivery.",
                title="Campaign execution finished",
                source="campaign-worker",
            )


def _process_job(job_id, worker_id, *, now_override=None):
    now = now_override or utc_now()
    job = db.session.get(CampaignJob, job_id)
    if job is None or job.status != "running" or job.lease_owner != worker_id:
        return False
    history = job.email_send_history
    try:
        body_html = str(job.snapshot.get("body_html", "") or "")
        subject = str(job.snapshot.get("subject", "") or "")
        if len(subject) > 255 or not subject.strip() or not body_html.strip():
            job.status = "failed"
            job.error_details = "The saved campaign content failed local validation."
            if history:
                history.status = "failed"
                history.error_details = job.error_details
                history.completed_at = now
            job.completed_at = now
            db.session.commit()
            return True
        if not current_app.config.get("MAILPILOT_PUBLIC_BASE_URL") or not current_app.config.get("MAIL_DEFAULT_SENDER"):
            job.status = "failed"
            job.error_details = "SMTP sender or HTTPS unsubscribe base URL is not configured."
            if history:
                history.status = "failed"
                history.error_details = job.error_details
                history.completed_at = now
            job.completed_at = now
            db.session.commit()
            create_notification("error", "Campaign job stopped because required SMTP or unsubscribe settings are missing.", source="campaign-worker")
            return True

        for delivery in list(job.deliveries):
            now = now_override or utc_now()
            db.session.refresh(job)
            if job.status != "running" or job.lease_owner != worker_id:
                break
            if _cancel_if_campaign_changed(job, now=now):
                break
            if delivery.status == "accepted" or delivery.status in {"suppressed", "permanent_failure", "uncertain", "bounced_permanent", "bounced_temporary"}:
                continue
            if delivery.retry_at and delivery.retry_at > now:
                continue
            if delivery.status == "temporary_failure":
                if delivery.attempt_count >= MAX_DELIVERY_ATTEMPTS:
                    continue
                if delivery.retry_at and delivery.retry_at > now:
                    continue
            if is_suppressed(delivery.email, job.campaign_id):
                delivery.status = "suppressed"
                delivery.retry_at = None
                delivery.error_details = "Address is suppressed at execution time."
                db.session.commit()
                continue
            active_contact_id = db.session.scalar(
                select(Recipient.id).where(
                    Recipient.email == delivery.email,
                    Recipient.is_active.is_(True),
                ).limit(1)
            )
            if active_contact_id is None:
                delivery.status = "suppressed"
                delivery.retry_at = None
                delivery.error_details = "Excluded at execution because the saved recipient is no longer active."
                db.session.commit()
                continue
            used, next_window = daily_send_usage(now)
            if used >= daily_recipient_limit():
                delivery.retry_at = next_window
                job.status = "retrying"
                job.scheduled_for = next_window
                job.lease_owner = None
                job.lease_expires_at = None
                job.error_details = "The local rolling 24-hour recipient cap was reached; this job will resume when capacity opens."
                db.session.commit()
                break
            html_footer, unsubscribe_link = _delivery_footer(delivery.email, job.campaign_id)
            # Recheck after generating the bearer token and immediately before the SMTP checkpoint.
            if _cancel_if_campaign_changed(job, now=now):
                break
            if is_suppressed(delivery.email, job.campaign_id):
                delivery.status = "suppressed"
                delivery.retry_at = None
                delivery.error_details = "Address became suppressed before SMTP submission."
                db.session.commit()
                continue
            message_id = make_msgid()
            attempt, reservation_status = _reserve_delivery_attempt(
                job.id,
                delivery.id,
                worker_id,
                now=now,
                message_id=message_id,
            )
            if reservation_status in {"stopped", "deferred"}:
                break
            if reservation_status == "skipped":
                continue
            full_html = body_html + html_footer
            try:
                result = send_html_email(
                    to=[],
                    cc=[],
                    bcc=[delivery.email],
                    subject=subject,
                    body_html=full_html,
                    headers={
                        "List-Unsubscribe": f"<{unsubscribe_link}>",
                        "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
                    },
                    message_id=message_id,
                    config=current_app.config,
                )
            except SMTPDeliveryUncertain as error:
                attempt.status = "uncertain"
                attempt.error_details = error.public_message[:500]
                attempt.completed_at = utc_now()
                delivery.status = "uncertain"
                delivery.error_details = error.public_message[:500]
                job.status = "uncertain"
                job.error_details = "A recipient's SMTP result is uncertain; no automatic retry was made."
                if history:
                    history.status = "uncertain"
                    history.error_details = job.error_details
                db.session.commit()
                break
            except SMTPConnectionFailure as error:
                attempt.status = "temporary_failure"
                attempt.error_details = error.public_message[:500]
                attempt.completed_at = utc_now()
                delivery.status = "temporary_failure"
                delivery.retry_at = _safe_retry_at(delivery, now) if delivery.attempt_count < MAX_DELIVERY_ATTEMPTS else None
                delivery.error_details = error.public_message[:500]
                job.status = "retrying" if delivery.retry_at else "failed"
                job.scheduled_for = delivery.retry_at or now
                job.error_details = error.public_message[:500]
                db.session.commit()
                break
            except (SMTPAuthenticationFailure, SMTPConfigurationError) as error:
                attempt.status = "permanent_failure"
                attempt.error_details = error.public_message[:500]
                attempt.completed_at = utc_now()
                delivery.status = "permanent_failure"
                delivery.error_details = error.public_message[:500]
                job.status = "failed"
                job.error_details = error.public_message[:500]
                if history:
                    history.status = "failed"
                    history.error_details = job.error_details
                    history.completed_at = now
                db.session.commit()
                break
            except (SMTPMessageValidationError, MailPilotSMTPError) as error:
                attempt.status = "permanent_failure"
                attempt.error_details = (getattr(error, "public_message", "The message failed local validation.") or "The message failed local validation.")[:500]
                attempt.completed_at = utc_now()
                delivery.status = "permanent_failure"
                delivery.error_details = (getattr(error, "public_message", "The message failed local validation.") or "The message failed local validation.")[:500]
                db.session.commit()
                continue
            except Exception as error:
                logger.error("Campaign delivery stopped after an unexpected %s.", type(error).__name__)
                attempt.status = "uncertain"
                attempt.error_details = "An unexpected error occurred during SMTP submission; acceptance is unknown."
                attempt.completed_at = utc_now()
                delivery.status = "uncertain"
                delivery.error_details = "An unexpected error occurred during SMTP submission; acceptance is unknown."
                job.status = "uncertain"
                job.error_details = delivery.error_details
                if history:
                    history.status = "uncertain"
                    history.error_details = delivery.error_details
                db.session.commit()
                break
            completed_at = utc_now()
            attempt.status = _record_smtp_result(delivery, result, completed_at)
            attempt.message_id = result.message_id or message_id
            attempt.smtp_code = _smtp_code(result)
            attempt.error_details = delivery.error_details[:500]
            attempt.completed_at = completed_at
            db.session.commit()

        _complete_job(job, now=utc_now())
        db.session.commit()
        return True
    except Exception as error:
        db.session.rollback()
        logger.error("Campaign job %s stopped after an unexpected %s.", job_id, type(error).__name__)
        job = db.session.get(CampaignJob, job_id)
        if job and job.status == "running" and job.lease_owner == worker_id:
            job.status = "uncertain"
            job.error_details = "Worker stopped unexpectedly. Any SMTP-in-progress address requires manual review."
            job.lease_owner = None
            job.lease_expires_at = None
            for delivery in job.deliveries:
                if delivery.status == "sending":
                    delivery.status = "uncertain"
                    delivery.error_details = job.error_details
            if job.email_send_history:
                job.email_send_history.status = "uncertain"
                job.email_send_history.error_details = job.error_details
                job.email_send_history.completed_at = utc_now()
            job.completed_at = utc_now()
            db.session.commit()
        return False


def process_one_due_job(*, worker_id=None, now=None):
    now_override = now
    now = now or utc_now()
    _recover_stale_jobs(now)
    worker_id = worker_id or token_urlsafe(16)
    job_id = _claim_due_job(worker_id, now)
    if job_id is None:
        return False
    _process_job(job_id, worker_id, now_override=now_override)
    return True


def campaign_job_summary(job):
    counts = {status: 0 for status in ("pending", "sending", "accepted", "temporary_failure", "permanent_failure", "suppressed", "uncertain", "bounced_temporary", "bounced_permanent")}
    for delivery in job.deliveries:
        counts[delivery.status] = counts.get(delivery.status, 0) + 1
    return counts


def register_campaign_worker_command(app):
    import click
    from .bounce_service import BounceConfigurationError, imap_is_configured, poll_bounce_mailbox

    @app.cli.command("campaign-worker")
    @click.option("--once", "run_once", is_flag=True, help="Process currently due jobs, then exit.")
    @click.option("--poll-interval", type=click.IntRange(min=1, max=300), default=WORKER_POLL_SECONDS, show_default=True)
    def campaign_worker_command(run_once, poll_interval):
        """Run the separate persistent campaign delivery worker."""
        worker_id = f"{socket.gethostname()}-{token_urlsafe(12)}"[:64]
        click.echo("MailPilot campaign worker started. Press Ctrl+C to stop.")
        last_bounce_poll = 0.0
        try:
            while True:
                now_monotonic = time.monotonic()
                bounce_interval = int(app.config.get("MAIL_IMAP_POLL_SECONDS", 60))
                if imap_is_configured(app.config) and (run_once or now_monotonic - last_bounce_poll >= bounce_interval):
                    try:
                        imported = poll_bounce_mailbox(app.config)
                        if imported:
                            click.echo(f"Recorded {imported} verified bounce event(s).")
                    except BounceConfigurationError as error:
                        logger.warning("Gmail IMAP bounce poll failed after %s.", str(error))
                    last_bounce_poll = now_monotonic
                processed = process_one_due_job(worker_id=worker_id)
                if run_once and not processed:
                    break
                if not processed:
                    if run_once:
                        break
                    time.sleep(poll_interval)
        except KeyboardInterrupt:
            click.echo("MailPilot campaign worker stopped.")
