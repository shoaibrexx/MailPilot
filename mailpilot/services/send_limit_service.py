from datetime import timedelta

from flask import current_app
from sqlalchemy import func, select

from ..extensions import db
from ..models import CampaignDeliveryAttempt, EmailSendHistory


class DailyLimitExceeded(ValueError):
    pass


def begin_daily_send_reservation():
    """Serialize SQLite quota checks with the durable reservation they create."""
    db.session.commit()
    if db.engine.dialect.name == "sqlite":
        db.session.connection().exec_driver_sql("BEGIN IMMEDIATE")


def daily_recipient_limit(config=None):
    config = current_app.config if config is None else config
    try:
        limit = int(config.get("MAILPILOT_DAILY_RECIPIENT_LIMIT", 100))
    except (TypeError, ValueError):
        limit = 100
    return max(1, min(limit, 5000))


def daily_send_usage(now):
    cutoff = now - timedelta(hours=24)
    campaign_attempts = db.session.scalar(
        select(func.count(CampaignDeliveryAttempt.id)).where(
            CampaignDeliveryAttempt.attempted_at >= cutoff,
        )
    ) or 0
    draft_sends = db.session.scalar(
        select(func.coalesce(func.sum(EmailSendHistory.recipient_count), 0)).where(
            EmailSendHistory.draft_id.is_not(None),
            EmailSendHistory.campaign_id.is_(None),
            EmailSendHistory.attempted_at >= cutoff,
            EmailSendHistory.status.in_(("pending", "sent", "partial", "failed", "uncertain")),
        )
    ) or 0
    oldest_campaign_attempt = db.session.scalar(
        select(func.min(CampaignDeliveryAttempt.attempted_at)).where(CampaignDeliveryAttempt.attempted_at >= cutoff)
    )
    oldest_draft_send = db.session.scalar(
        select(func.min(EmailSendHistory.attempted_at)).where(
            EmailSendHistory.draft_id.is_not(None),
            EmailSendHistory.campaign_id.is_(None),
            EmailSendHistory.attempted_at >= cutoff,
            EmailSendHistory.status.in_(("pending", "sent", "partial", "failed", "uncertain")),
        )
    )
    timestamps = [value for value in (oldest_campaign_attempt, oldest_draft_send) if value is not None]
    next_available = min(timestamps) + timedelta(hours=24) if timestamps else now + timedelta(hours=1)
    return int(campaign_attempts + draft_sends), next_available


def ensure_daily_send_capacity(recipient_count, *, now):
    limit = daily_recipient_limit()
    used, next_available = daily_send_usage(now)
    if int(recipient_count) + used > limit:
        raise DailyLimitExceeded(
            f"MailPilot's local rolling 24-hour sending cap is {limit} recipient attempts. "
            f"{used} are already recorded; try again after {next_available:%Y-%m-%d %H:%M UTC}."
        )
    return limit - used
