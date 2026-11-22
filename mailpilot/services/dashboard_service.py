from datetime import datetime, timezone

from sqlalchemy import func, select

from ..extensions import db
from ..models import Campaign, Recipient
from .analytics_service import get_execution_metrics, get_sending_activity, range_start


def get_dashboard_data():
    """Return database-backed counts and actual SMTP outcomes without claiming delivery."""
    total = db.session.scalar(select(func.count(Recipient.id))) or 0
    active = db.session.scalar(
        select(func.count(Recipient.id)).where(Recipient.is_active.is_(True))
    ) or 0
    inactive = total - active
    campaign_counts = {
        status: db.session.scalar(
            select(func.count(Campaign.id)).where(Campaign.status == status)
        ) or 0
        for status in ("draft", "scheduled", "ready", "completed")
    }
    campaign_counts["total"] = sum(campaign_counts.values())
    execution = get_execution_metrics()
    accepted = execution["campaign_accepted"] + execution["composer_messages_accepted"]
    known_failures = execution["campaign_failed"] + execution["composer_messages_failed"]
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    activity = get_sending_activity("30d", range_start("30d", now=now), now)
    recent_campaigns = db.session.scalars(
        select(Campaign)
        .order_by(Campaign.updated_at.desc(), Campaign.id.desc())
        .limit(5)
    ).all()
    return {
        "data_status": "available",
        "stats": {
            "total_recipients": {
                "value": total,
                "message": "Saved recipient records.",
            },
            "emails_sent": {
                "value": accepted,
                "message": "Gmail SMTP accepted these messages; inbox delivery is not confirmed.",
            },
            "successful_emails": {
                "value": execution["pending_campaign_recipients"],
                "message": "Campaign recipients still queued, retrying, or being sent.",
            },
            "failed_emails": {
                "value": known_failures,
                "message": "Known SMTP failures across campaign recipient attempts and composer messages.",
            },
            "active_recipients": {
                "value": active,
                "message": "Recipients currently marked active.",
            },
            "inactive_recipients": {
                "value": inactive,
                "message": "Recipients currently marked inactive.",
            },
            "campaigns_total": {
                "value": campaign_counts["total"],
                "message": "Campaign records currently in your workspace.",
            },
            "campaigns_draft": {
                "value": campaign_counts["draft"],
                "message": "Campaigns still being prepared.",
            },
            "campaigns_scheduled": {
                "value": campaign_counts["scheduled"],
                "message": "Campaign plans with a future schedule.",
            },
            "campaigns_ready": {
                "value": campaign_counts["ready"],
                "message": "Campaigns marked ready for future sending.",
            },
            "campaigns_completed": {
                "value": campaign_counts["completed"],
                "message": "Campaigns manually marked complete.",
            },
        },
        "activity": [
            {"label": point["label"], "accepted": point["accepted"], "failed": point["failed"]}
            for point in activity
        ],
        "recent_campaigns": [
            {
                "id": campaign.id,
                "name": campaign.name,
                "subject": campaign.subject,
                "status": campaign.status,
                "recipient_count": campaign.recipient_count,
                "scheduled_at": campaign.scheduled_at.isoformat()
                if campaign.scheduled_at
                else None,
                "updated_at": campaign.updated_at.isoformat()
                if campaign.updated_at
                else None,
            }
            for campaign in recent_campaigns
        ],
    }
