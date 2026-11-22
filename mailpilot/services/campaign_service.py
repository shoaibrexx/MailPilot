from datetime import datetime, timezone
from html.parser import HTMLParser
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError

from ..extensions import db
from ..models import Campaign, EmailDraft, EmailSendHistory, Recipient, RecipientGroup, WorkspaceSettings
from .composer_service import sanitize_email_html


CAMPAIGN_STATUSES = {
    "draft": "Draft",
    "scheduled": "Scheduled",
    "ready": "Ready",
    "completed": "Completed",
}
MAX_CAMPAIGN_RECIPIENTS = 10_000


class CampaignValidationError(ValueError):
    def __init__(self, errors):
        self.errors = errors
        super().__init__("Please correct the highlighted campaign fields.")


class DuplicateRecipientGroupError(ValueError):
    pass


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)

    def text(self):
        return "".join(self.parts)


def _payload_values(payload, key):
    if hasattr(payload, "getlist"):
        values = payload.getlist(key)
        if values:
            return values
    value = payload.get(key, [])
    if value is None:
        return []
    return value if isinstance(value, (list, tuple, set)) else [value]


def _parse_ids(raw_values, field, errors, maximum=MAX_CAMPAIGN_RECIPIENTS):
    if isinstance(raw_values, str):
        candidates = raw_values.split(",")
    else:
        candidates = []
        for value in raw_values or []:
            candidates.extend(str(value).split(","))
    ids = []
    for candidate in candidates:
        candidate = str(candidate).strip()
        if not candidate:
            continue
        try:
            value = int(candidate)
        except (TypeError, ValueError):
            errors[field] = "Choose valid saved recipients or groups."
            continue
        if value < 1:
            errors[field] = "Choose valid saved recipients or groups."
            continue
        if value not in ids:
            ids.append(value)
        if len(ids) > maximum:
            errors[field] = f"Select no more than {maximum:,} recipients."
            return []
    return ids


def get_active_recipients(recipient_ids):
    ids = list(dict.fromkeys(int(value) for value in recipient_ids or []))
    if not ids:
        return []
    recipients = db.session.scalars(
        select(Recipient).where(
            Recipient.id.in_(ids), Recipient.is_active.is_(True)
        )
    ).all()
    by_id = {recipient.id: recipient for recipient in recipients}
    return [by_id[recipient_id] for recipient_id in ids if recipient_id in by_id]


def recipient_picker_records(recipients):
    return [
        {
            "id": recipient.id,
            "name": recipient.full_name or recipient.email,
            "email": recipient.email,
            "company": recipient.company or "",
        }
        for recipient in recipients
    ]


def search_active_recipients(search="", limit=30):
    search = str(search or "").strip()[:120]
    limit = max(1, min(int(limit), 50))
    query = select(Recipient).where(Recipient.is_active.is_(True))
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
    recipients = db.session.scalars(
        query.order_by(Recipient.last_name, Recipient.first_name, Recipient.id).limit(limit)
    ).all()
    return recipient_picker_records(recipients)


def list_email_drafts(limit=100):
    limit = max(1, min(int(limit), 250))
    return db.session.scalars(
        select(EmailDraft)
        .order_by(EmailDraft.updated_at.desc(), EmailDraft.id.desc())
        .limit(limit)
    ).all()


def list_recipient_groups(limit=250):
    limit = max(1, min(int(limit), 1000))
    return db.session.scalars(
        select(RecipientGroup)
        .order_by(RecipientGroup.name.collate("NOCASE"), RecipientGroup.id)
        .limit(limit)
    ).all()


def group_active_member_count(group):
    return len(get_active_recipients(group.recipient_ids or []))


def _clean_name(payload, key, label, maximum, errors):
    value = str(payload.get(key, "") or "").strip()
    if not value:
        errors[key] = f"{label} is required."
    elif len(value) > maximum:
        errors[key] = f"{label} must be {maximum} characters or fewer."
    elif any(ord(character) < 32 for character in value):
        errors[key] = f"{label} contains unsupported control characters."
    return value


def validate_group_data(payload):
    errors = {}
    name = _clean_name(payload, "name", "Group name", 120, errors)
    recipient_ids = _parse_ids(
        payload.get("recipient_ids", ""), "recipient_ids", errors
    )
    recipients = get_active_recipients(recipient_ids)
    if len(recipients) != len(recipient_ids):
        errors["recipient_ids"] = "Groups can include active saved recipients only. Refresh the selection."
    if not recipient_ids:
        errors["recipient_ids"] = "Add at least one active recipient to this group."
    return {
        "name": name,
        "recipient_ids": [recipient.id for recipient in recipients],
    }, errors


def _group_name_taken(name, excluding_id=None):
    query = select(RecipientGroup.id).where(
        func.lower(RecipientGroup.name) == name.casefold()
    )
    if excluding_id is not None:
        query = query.where(RecipientGroup.id != excluding_id)
    return db.session.scalar(query) is not None


def create_recipient_group(payload):
    values, errors = validate_group_data(payload)
    if errors:
        raise CampaignValidationError(errors)
    if _group_name_taken(values["name"]):
        raise DuplicateRecipientGroupError("A recipient group with this name already exists.")
    group = RecipientGroup(**values)
    db.session.add(group)
    try:
        db.session.commit()
    except IntegrityError as error:
        db.session.rollback()
        raise DuplicateRecipientGroupError(
            "A recipient group with this name already exists."
        ) from error
    return group


def update_recipient_group(group, payload):
    values, errors = validate_group_data(payload)
    if errors:
        raise CampaignValidationError(errors)
    if _group_name_taken(values["name"], excluding_id=group.id):
        raise DuplicateRecipientGroupError("A recipient group with this name already exists.")
    group.name = values["name"]
    group.recipient_ids = values["recipient_ids"]
    try:
        db.session.commit()
    except IntegrityError as error:
        db.session.rollback()
        raise DuplicateRecipientGroupError(
            "A recipient group with this name already exists."
        ) from error
    return group


def get_recipient_group(group_id):
    return db.session.get(RecipientGroup, group_id)


def delete_recipient_group(group):
    group_id = group.id
    campaigns = db.session.scalars(
        select(Campaign).where(Campaign.recipient_group_ids.is_not(None))
    ).all()
    for campaign in campaigns:
        if group_id in (campaign.recipient_group_ids or []):
            campaign.recipient_group_ids = [
                value for value in campaign.recipient_group_ids if value != group_id
            ]
    db.session.delete(group)
    db.session.commit()


def _schedule_value(payload, status, errors):
    raw_value = str(payload.get("scheduled_at", "") or "").strip()
    if status != "scheduled":
        return None
    if not raw_value:
        errors["scheduled_at"] = "Choose a date and time for a scheduled campaign."
        return None
    try:
        scheduled_at = datetime.fromisoformat(raw_value)
    except ValueError:
        errors["scheduled_at"] = "Enter a valid schedule date and time."
        return None
    if scheduled_at.tzinfo is not None:
        errors["scheduled_at"] = "Enter the local date and time shown for the workspace timezone."
        return None
    settings = db.session.get(WorkspaceSettings, 1)
    timezone_name = settings.timezone if settings else "UTC"
    try:
        zone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        errors["scheduled_at"] = "The workspace timezone is invalid. Update it in Settings."
        return None
    local_value = scheduled_at
    aware_value = local_value.replace(tzinfo=zone, fold=0)
    if aware_value.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None) != local_value:
        errors["scheduled_at"] = "This local time does not exist because of a daylight-saving transition. Choose another time."
        return None
    second_occurrence = local_value.replace(tzinfo=zone, fold=1)
    if aware_value.utcoffset() != second_occurrence.utcoffset():
        errors["scheduled_at"] = "This local time occurs twice because of a daylight-saving transition. Choose another time."
        return None
    if aware_value.astimezone(timezone.utc) <= datetime.now(timezone.utc):
        errors["scheduled_at"] = "Choose a future date and time."
    return local_value


def validate_campaign_data(payload):
    errors = {}
    name = _clean_name(payload, "name", "Campaign name", 120, errors)

    status = str(payload.get("status", "draft") or "draft").strip().casefold()
    if status not in CAMPAIGN_STATUSES:
        errors["status"] = "Choose a valid campaign status."

    try:
        draft_id = int(payload.get("email_draft_id", ""))
    except (TypeError, ValueError):
        draft_id = 0
    draft = db.session.get(EmailDraft, draft_id) if draft_id > 0 else None
    if draft is None:
        errors["email_draft_id"] = "Choose a saved email draft for campaign content."
    else:
        subject = str(draft.subject or "").strip()
        body_html = sanitize_email_html(draft.body_html or "")
        text = _TextExtractor()
        text.feed(body_html)
        text.close()
        if not subject:
            errors["email_draft_id"] = "The selected email draft needs a subject."
        if not text.text().strip():
            errors["email_draft_id"] = "The selected email draft needs message text."

    manual_ids = _parse_ids(
        payload.get("manual_recipient_ids", ""), "manual_recipient_ids", errors
    )
    manual_recipients = get_active_recipients(manual_ids)
    if len(manual_recipients) != len(manual_ids):
        errors["manual_recipient_ids"] = "Campaigns can include active saved recipients only. Refresh the selection."

    group_ids = _parse_ids(
        _payload_values(payload, "recipient_group_ids"), "recipient_group_ids", errors
    )
    groups = db.session.scalars(
        select(RecipientGroup).where(RecipientGroup.id.in_(group_ids))
    ).all() if group_ids else []
    groups_by_id = {group.id: group for group in groups}
    if len(groups_by_id) != len(group_ids):
        errors["recipient_group_ids"] = "One or more selected recipient groups no longer exist."

    audience_ids = list(manual_ids)
    for group_id in group_ids:
        group = groups_by_id.get(group_id)
        if group:
            for recipient_id in group.recipient_ids or []:
                if recipient_id not in audience_ids:
                    audience_ids.append(recipient_id)
    if len(audience_ids) > MAX_CAMPAIGN_RECIPIENTS:
        errors["manual_recipient_ids"] = f"Select no more than {MAX_CAMPAIGN_RECIPIENTS:,} recipients."
        audience_ids = []
    audience = get_active_recipients(audience_ids)
    if not audience:
        errors["manual_recipient_ids"] = "Select at least one active recipient or recipient group."

    scheduled_at = _schedule_value(payload, status, errors)
    recipients_snapshot = [
        {
            "id": recipient.id,
            "name": recipient.full_name or recipient.email,
            "email": recipient.email,
            "company": recipient.company or "",
        }
        for recipient in audience
    ]

    values = {
        "name": name,
        "status": status,
        "email_draft_id": draft.id if draft else None,
        "draft_name": (draft.subject or f"Email draft #{draft.id}") if draft else "",
        "subject": subject if draft else "",
        "body_html": body_html if draft else "",
        "manual_recipient_ids": [recipient.id for recipient in manual_recipients],
        "recipient_group_ids": [group.id for group in groups],
        "recipient_group_names": [group.name for group in groups],
        "recipient_snapshot": recipients_snapshot,
        "scheduled_at": scheduled_at,
    }
    return values, errors


def create_campaign(payload):
    values, errors = validate_campaign_data(payload)
    if errors:
        raise CampaignValidationError(errors)
    campaign = Campaign(**values)
    db.session.add(campaign)
    db.session.commit()
    return campaign


def update_campaign(campaign, payload):
    from .campaign_worker_service import campaign_has_running_job

    if campaign_has_running_job(campaign.id):
        raise CampaignValidationError({"send": "This campaign has a worker actively processing a recipient. Edit it after the current send attempt completes."})
    values, errors = validate_campaign_data(payload)
    if errors:
        raise CampaignValidationError(errors)
    for key, value in values.items():
        setattr(campaign, key, value)
    db.session.commit()
    from .campaign_worker_service import cancel_campaign_jobs

    cancel_campaign_jobs(campaign.id, reason="Campaign was edited after this job was approved. Review the updated campaign and authorize it again.")
    return campaign


def get_campaign(campaign_id):
    return db.session.get(Campaign, campaign_id)


def delete_campaign(campaign):
    from .campaign_worker_service import campaign_has_running_job, cancel_campaign_jobs

    if campaign_has_running_job(campaign.id):
        raise CampaignValidationError({"send": "This campaign has a worker actively processing a recipient. Delete it after the current send attempt completes."})

    cancel_campaign_jobs(campaign.id, reason="Campaign was deleted before queued delivery began.")
    db.session.execute(
        db.update(EmailSendHistory)
        .where(EmailSendHistory.campaign_id == campaign.id)
        .values(campaign_id=None)
    )
    db.session.delete(campaign)
    db.session.commit()


def list_campaigns(search="", status="all", page=1, per_page=10):
    query = select(Campaign)
    if status in CAMPAIGN_STATUSES:
        query = query.where(Campaign.status == status)
    if search:
        escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped}%"
        query = query.where(
            or_(
                Campaign.name.ilike(pattern, escape="\\"),
                Campaign.subject.ilike(pattern, escape="\\"),
            )
        )
    return db.paginate(
        query.order_by(Campaign.updated_at.desc(), Campaign.id.desc()),
        page=page,
        per_page=per_page,
        error_out=False,
    )


def get_campaign_summary():
    counts = {
        status: db.session.scalar(
            select(func.count(Campaign.id)).where(Campaign.status == status)
        ) or 0
        for status in CAMPAIGN_STATUSES
    }
    counts["total"] = sum(counts.values())
    return counts


def get_recent_campaigns(limit=5):
    return db.session.scalars(
        select(Campaign)
        .order_by(Campaign.updated_at.desc(), Campaign.id.desc())
        .limit(max(1, min(int(limit), 20)))
    ).all()


def campaign_form_values(campaign=None):
    if campaign is None:
        return {
            "name": "",
            "email_draft_id": "",
            "status": "draft",
            "scheduled_at": "",
            "manual_recipient_ids": "",
            "recipient_group_ids": [],
        }
    return {
        "name": campaign.name,
        "email_draft_id": str(campaign.email_draft_id or ""),
        "status": campaign.status,
        "scheduled_at": campaign.scheduled_at.strftime("%Y-%m-%dT%H:%M")
        if campaign.scheduled_at
        else "",
        "manual_recipient_ids": ",".join(
            str(value) for value in campaign.manual_recipient_ids or []
        ),
        "recipient_group_ids": [
            str(value) for value in campaign.recipient_group_ids or []
        ],
    }


def group_form_values(group=None):
    return {
        "name": group.name if group else "",
        "recipient_ids": ",".join(str(value) for value in group.recipient_ids or [])
        if group
        else "",
    }
