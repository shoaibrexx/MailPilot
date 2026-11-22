from calendar import month_name
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import func, select

from ..extensions import db
from ..models import (
    BounceEvent,
    Campaign,
    CampaignDelivery,
    CampaignDeliveryAttempt,
    CampaignJob,
    EmailSendHistory,
    Recipient,
    Suppression,
)


DATE_RANGES = {
    "7d": {"label": "Last 7 days", "days": 7},
    "30d": {"label": "Last 30 days", "days": 30},
    "90d": {"label": "Last 90 days", "days": 90},
    "all": {"label": "All time", "days": None},
}
CAMPAIGN_STATUSES = {
    "draft": "Draft",
    "scheduled": "Scheduled",
    "ready": "Ready",
    "completed": "Completed",
}
REPORT_PAGE_SIZES = {10, 25, 50}


def normalize_date_range(value):
    value = str(value or "30d").casefold()
    return value if value in DATE_RANGES else "30d"


def range_start(range_key, *, now=None):
    range_key = normalize_date_range(range_key)
    days = DATE_RANGES[range_key]["days"]
    if days is None:
        return None
    current = now or datetime.now(timezone.utc).replace(tzinfo=None)
    start_day = current.date() - timedelta(days=days - 1)
    return datetime.combine(start_day, time.min)


def _period_start(value, range_key):
    if range_key == "all":
        return date(value.year, value.month, 1)
    return value


def _next_period(value, range_key):
    if range_key == "all":
        if value.month == 12:
            return date(value.year + 1, 1, 1)
        return date(value.year, value.month + 1, 1)
    return value + timedelta(days=1)


def _period_label(value, range_key):
    if range_key == "all":
        return f"{month_name[value.month][:3]} {value.year}"
    return value.strftime("%b %d")


def _build_periods(start, end_day, range_key):
    cursor = _period_start(start, range_key)
    periods = []
    while cursor <= end_day:
        periods.append(cursor)
        cursor = _next_period(cursor, range_key)
    return periods


def campaign_report_statement(range_key="30d"):
    range_key = normalize_date_range(range_key)
    statement = select(Campaign)
    start = range_start(range_key)
    if start is not None:
        statement = statement.where(Campaign.created_at >= start)
    return statement.order_by(Campaign.created_at.desc(), Campaign.id.desc())


def _status_counts(statement, status_column):
    return dict(db.session.execute(
        statement.with_only_columns(status_column, func.count()).group_by(status_column)
    ).all())


def get_execution_metrics(start=None):
    """Return sending, bounce, queue, and suppression counts from stored outcomes."""
    attempt_statement = select(CampaignDeliveryAttempt.status, func.count())
    composer_statement = select(EmailSendHistory.status, func.count()).where(
        EmailSendHistory.draft_id.is_not(None),
        EmailSendHistory.campaign_id.is_(None),
    )
    bounce_statement = select(BounceEvent.classification, func.count())
    suppression_statement = select(func.count(Suppression.id))
    if start is not None:
        attempt_statement = attempt_statement.where(CampaignDeliveryAttempt.attempted_at >= start)
        composer_statement = composer_statement.where(EmailSendHistory.attempted_at >= start)
        bounce_statement = bounce_statement.where(BounceEvent.bounced_at >= start)
        suppression_statement = suppression_statement.where(Suppression.created_at >= start)

    attempt_counts = _status_counts(attempt_statement, CampaignDeliveryAttempt.status)
    composer_counts = _status_counts(composer_statement, EmailSendHistory.status)
    bounce_counts = _status_counts(bounce_statement, BounceEvent.classification)
    pending_recipients = db.session.scalar(
        select(func.count(CampaignDelivery.id))
        .join(CampaignJob, CampaignDelivery.job_id == CampaignJob.id)
        .where(
            CampaignJob.status.in_(("queued", "running", "retrying")),
            CampaignDelivery.status.in_(("pending", "sending", "temporary_failure")),
        )
    ) or 0
    suppressed_exclusions = db.session.scalar(
        select(func.count(CampaignDelivery.id))
        .join(CampaignJob, CampaignDelivery.job_id == CampaignJob.id)
        .where(CampaignDelivery.status == "suppressed")
    ) or 0

    return {
        "campaign_attempts": sum(int(value or 0) for value in attempt_counts.values()),
        "campaign_accepted": int(attempt_counts.get("accepted", 0) or 0),
        "campaign_failed": int(attempt_counts.get("temporary_failure", 0) or 0)
        + int(attempt_counts.get("permanent_failure", 0) or 0),
        "campaign_uncertain": int(attempt_counts.get("uncertain", 0) or 0)
        + int(attempt_counts.get("sending", 0) or 0),
        "composer_messages_accepted": int(composer_counts.get("sent", 0) or 0),
        "composer_messages_partial": int(composer_counts.get("partial", 0) or 0),
        "composer_messages_failed": int(composer_counts.get("failed", 0) or 0),
        "composer_messages_uncertain": int(composer_counts.get("uncertain", 0) or 0),
        "composer_messages_pending": int(composer_counts.get("pending", 0) or 0),
        "pending_campaign_recipients": int(pending_recipients),
        "suppressed_exclusions": int(suppressed_exclusions),
        "suppression_records": db.session.scalar(select(func.count(Suppression.id))) or 0,
        "suppressions_added": db.session.scalar(suppression_statement) or 0,
        "permanent_bounces": int(bounce_counts.get("permanent", 0) or 0),
        "temporary_bounces": int(bounce_counts.get("temporary", 0) or 0),
    }


def _event_period(value, range_key):
    return _period_start(value.date(), range_key)


def get_sending_activity(range_key, range_start_at, now):
    attempt_statement = select(CampaignDeliveryAttempt.attempted_at, CampaignDeliveryAttempt.status)
    composer_statement = select(EmailSendHistory.attempted_at, EmailSendHistory.status).where(
        EmailSendHistory.draft_id.is_not(None),
        EmailSendHistory.campaign_id.is_(None),
        EmailSendHistory.status.in_(("sent", "partial", "failed", "uncertain")),
    )
    if range_start_at is not None:
        attempt_statement = attempt_statement.where(CampaignDeliveryAttempt.attempted_at >= range_start_at)
        composer_statement = composer_statement.where(EmailSendHistory.attempted_at >= range_start_at)
    attempts = db.session.execute(attempt_statement).all()
    composer_messages = db.session.execute(composer_statement).all()
    events = [*attempts, *composer_messages]
    if not events:
        return []

    if range_start_at is None:
        chart_start = min(value for value, _status in events).date()
    else:
        chart_start = range_start_at.date()
    periods = _build_periods(chart_start, now.date(), range_key)
    counts = {}
    for timestamp, status in events:
        period = _event_period(timestamp, range_key)
        values = counts.setdefault(period, {"accepted": 0, "failed": 0, "partial": 0, "uncertain": 0})
        if status == "accepted" or status == "sent":
            values["accepted"] += 1
        elif status in {"temporary_failure", "permanent_failure", "failed"}:
            values["failed"] += 1
        elif status == "partial":
            values["partial"] += 1
        elif status in {"uncertain", "sending"}:
            values["uncertain"] += 1
    return [
        {"label": _period_label(period, range_key), **counts.get(period, {"accepted": 0, "failed": 0, "partial": 0, "uncertain": 0})}
        for period in periods
    ]


def get_campaign_execution_stats(campaign_ids, range_key="30d"):
    """Per-campaign actual SMTP outcomes, current queue state, exclusions, and bounces."""
    ids = [int(value) for value in campaign_ids if value is not None]
    if not ids:
        return {}
    start = range_start(range_key)
    stats = {
        campaign_id: {
            "attempts": 0,
            "accepted": 0,
            "failed": 0,
            "uncertain": 0,
            "pending": 0,
            "suppressed_excluded": 0,
            "permanent_bounces": 0,
            "temporary_bounces": 0,
        }
        for campaign_id in ids
    }
    attempt_statement = (
        select(CampaignJob.campaign_id, CampaignDeliveryAttempt.status, func.count())
        .join(CampaignDelivery, CampaignDeliveryAttempt.delivery_id == CampaignDelivery.id)
        .join(CampaignJob, CampaignDelivery.job_id == CampaignJob.id)
        .where(CampaignJob.campaign_id.in_(ids))
    )
    if start is not None:
        attempt_statement = attempt_statement.where(CampaignDeliveryAttempt.attempted_at >= start)
    for campaign_id, status, count in db.session.execute(
        attempt_statement.group_by(CampaignJob.campaign_id, CampaignDeliveryAttempt.status)
    ):
        row = stats[campaign_id]
        row["attempts"] += count
        if status == "accepted":
            row["accepted"] += count
        elif status in {"temporary_failure", "permanent_failure"}:
            row["failed"] += count
        elif status in {"uncertain", "sending"}:
            row["uncertain"] += count

    delivery_statement = (
        select(CampaignJob.campaign_id, CampaignDelivery.status, func.count())
        .join(CampaignDelivery, CampaignDelivery.job_id == CampaignJob.id)
        .where(CampaignJob.campaign_id.in_(ids))
    )
    if start is not None:
        delivery_statement = delivery_statement.where(CampaignJob.created_at >= start)
    for campaign_id, status, count in db.session.execute(
        delivery_statement.group_by(CampaignJob.campaign_id, CampaignDelivery.status)
    ):
        if status in {"pending", "sending", "temporary_failure"}:
            stats[campaign_id]["pending"] += count
        elif status == "suppressed":
            stats[campaign_id]["suppressed_excluded"] += count

    bounce_statement = (
        select(CampaignJob.campaign_id, BounceEvent.classification, func.count())
        .join(CampaignDelivery, CampaignDelivery.job_id == CampaignJob.id)
        .join(BounceEvent, BounceEvent.delivery_id == CampaignDelivery.id)
        .where(CampaignJob.campaign_id.in_(ids))
    )
    if start is not None:
        bounce_statement = bounce_statement.where(BounceEvent.bounced_at >= start)
    for campaign_id, classification, count in db.session.execute(
        bounce_statement.group_by(CampaignJob.campaign_id, BounceEvent.classification)
    ):
        key = "permanent_bounces" if classification == "permanent" else "temporary_bounces"
        stats[campaign_id][key] += count
    return stats


def get_analytics_data(range_key="30d", page=1, per_page=10):
    range_key = normalize_date_range(range_key)
    range_start_at = range_start(range_key)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    today = now.date()

    total_recipients = db.session.scalar(select(func.count(Recipient.id))) or 0
    active_recipients = db.session.scalar(
        select(func.count(Recipient.id)).where(Recipient.is_active.is_(True))
    ) or 0
    inactive_recipients = total_recipients - active_recipients

    campaign_status_counts = {
        status: db.session.scalar(
            select(func.count(Campaign.id)).where(Campaign.status == status)
        ) or 0
        for status in CAMPAIGN_STATUSES
    }
    campaign_status_counts["total"] = sum(campaign_status_counts.values())

    campaign_count_statement = select(func.count(Campaign.id))
    recipient_count_statement = select(func.count(Recipient.id))
    campaigns_in_range_statement = select(Campaign.id, Campaign.created_at)
    recipients_in_range_statement = select(Recipient.created_at)
    if range_start_at is not None:
        campaign_count_statement = campaign_count_statement.where(
            Campaign.created_at >= range_start_at
        )
        recipient_count_statement = recipient_count_statement.where(
            Recipient.created_at >= range_start_at
        )
        campaigns_in_range_statement = campaigns_in_range_statement.where(
            Campaign.created_at >= range_start_at
        )
        recipients_in_range_statement = recipients_in_range_statement.where(
            Recipient.created_at >= range_start_at
        )

    campaigns_in_range_count = db.session.scalar(campaign_count_statement) or 0
    recipients_in_range_count = db.session.scalar(recipient_count_statement) or 0
    campaign_events = db.session.execute(campaigns_in_range_statement).all()
    recipient_events = db.session.scalars(recipients_in_range_statement).all()

    campaign_counts_by_period = {}
    for _campaign_id, created_at in campaign_events:
        period = _period_start(created_at.date(), range_key)
        campaign_counts_by_period[period] = campaign_counts_by_period.get(period, 0) + 1

    if range_start_at is None:
        earliest_campaign = db.session.scalar(select(func.min(Campaign.created_at)))
        earliest_recipient = db.session.scalar(select(func.min(Recipient.created_at)))
        earliest_event = min(
            (event for event in (earliest_campaign, earliest_recipient) if event is not None),
            default=None,
        )
        chart_start = earliest_event.date() if earliest_event else today
    else:
        chart_start = range_start_at.date()
    periods = _build_periods(chart_start, today, range_key)

    recipients_added_by_period = {}
    for created_at in recipient_events:
        period = _period_start(created_at.date(), range_key)
        recipients_added_by_period[period] = recipients_added_by_period.get(period, 0) + 1

    if range_start_at is None:
        recipient_growth_total = 0
    else:
        recipient_growth_total = db.session.scalar(
            select(func.count(Recipient.id)).where(Recipient.created_at < range_start_at)
        ) or 0
    recipient_growth = []
    for period in periods:
        recipient_growth_total += recipients_added_by_period.get(period, 0)
        recipient_growth.append(
            {"label": _period_label(period, range_key), "value": recipient_growth_total}
        )

    campaign_activity = [
        {
            "label": _period_label(period, range_key),
            "value": campaign_counts_by_period.get(period, 0),
        }
        for period in periods
    ]

    status_distribution_statement = select(
        Campaign.status, func.count(Campaign.id)
    ).group_by(Campaign.status)
    if range_start_at is not None:
        status_distribution_statement = status_distribution_statement.where(
            Campaign.created_at >= range_start_at
        )
    status_distribution_values = dict(db.session.execute(status_distribution_statement).all())
    status_distribution = [
        {
            "key": status,
            "label": label,
            "value": status_distribution_values.get(status, 0),
        }
        for status, label in CAMPAIGN_STATUSES.items()
    ]

    page = max(1, int(page))
    per_page = int(per_page)
    if per_page not in REPORT_PAGE_SIZES:
        per_page = 10
    report_pagination = db.paginate(
        campaign_report_statement(range_key),
        page=page,
        per_page=per_page,
        error_out=False,
    )
    execution = get_execution_metrics(range_start_at)
    report_campaign_metrics = get_campaign_execution_stats(
        [campaign.id for campaign in report_pagination.items], range_key
    )
    sending_activity = get_sending_activity(range_key, range_start_at, now)

    return {
        "range_key": range_key,
        "range_label": DATE_RANGES[range_key]["label"],
        "range_start": range_start_at.date().isoformat() if range_start_at else None,
        "range_end": today.isoformat(),
        "summary": {
            "total_campaigns": campaign_status_counts["total"],
            "draft_campaigns": campaign_status_counts["draft"],
            "scheduled_campaigns": campaign_status_counts["scheduled"],
            "ready_campaigns": campaign_status_counts["ready"],
            "completed_campaigns": campaign_status_counts["completed"],
            "total_recipients": total_recipients,
            "active_recipients": active_recipients,
            "inactive_recipients": inactive_recipients,
        },
        "campaigns_in_range": campaigns_in_range_count,
        "recipients_added_in_range": recipients_in_range_count,
        "campaign_activity_has_data": campaigns_in_range_count > 0,
        "recipient_growth_has_data": total_recipients > 0,
        "status_distribution_has_data": campaigns_in_range_count > 0,
        "sending_activity_has_data": bool(sending_activity),
        "campaign_activity": campaign_activity if campaigns_in_range_count else [],
        "recipient_growth": recipient_growth if total_recipients else [],
        "sending_activity": sending_activity,
        "execution": execution,
        "status_distribution": status_distribution,
        "report_campaign_metrics": report_campaign_metrics,
        "report_pagination": report_pagination,
    }


def csv_export_summary_rows(data):
    summary = data["summary"]
    execution = data["execution"]
    rows = [
        ("summary", "all time", "Total campaigns", summary["total_campaigns"]),
        ("summary", "all time", "Draft campaigns", summary["draft_campaigns"]),
        ("summary", "all time", "Scheduled campaigns", summary["scheduled_campaigns"]),
        ("summary", "all time", "Ready campaigns", summary["ready_campaigns"]),
        ("summary", "all time", "Completed campaigns", summary["completed_campaigns"]),
        ("summary", "all time", "Total recipients", summary["total_recipients"]),
        ("summary", "all time", "Active recipients", summary["active_recipients"]),
        ("summary", "all time", "Inactive recipients", summary["inactive_recipients"]),
        ("sending", data["range_label"], "Campaign SMTP recipient attempts", execution["campaign_attempts"]),
        ("sending", data["range_label"], "Campaign SMTP accepted", execution["campaign_accepted"]),
        ("sending", data["range_label"], "Campaign known failed attempts", execution["campaign_failed"]),
        ("sending", data["range_label"], "Campaign uncertain outcomes", execution["campaign_uncertain"]),
        ("sending", data["range_label"], "Composer SMTP accepted messages", execution["composer_messages_accepted"]),
        ("sending", data["range_label"], "Composer partially accepted messages", execution["composer_messages_partial"]),
        ("sending", data["range_label"], "Composer failed attempts", execution["composer_messages_failed"]),
        ("sending", data["range_label"], "Composer uncertain outcomes", execution["composer_messages_uncertain"]),
        ("sending", "current", "Pending campaign recipients", execution["pending_campaign_recipients"]),
        ("suppression", "current", "Suppression records on file", execution["suppression_records"]),
        ("suppression", data["range_label"], "Suppression records added", execution["suppressions_added"]),
        ("campaign execution", "current", "Suppressed or inactive campaign exclusions", execution["suppressed_exclusions"]),
        ("bounces", data["range_label"], "Permanent matched bounces", execution["permanent_bounces"]),
        ("bounces", data["range_label"], "Temporary matched bounces", execution["temporary_bounces"]),
    ]
    return rows


def get_campaign_report_rows(range_key="30d"):
    """Return all campaigns in the selected created-date range for CSV export."""
    return db.session.scalars(campaign_report_statement(range_key)).all()
