from datetime import datetime, timezone

from sqlalchemy import ForeignKey

from ..extensions import db


CAMPAIGN_STATUSES = ("draft", "scheduled", "ready", "completed")


def utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Campaign(db.Model):
    __tablename__ = "campaigns"
    __table_args__ = (
        db.CheckConstraint(
            "length(trim(name)) BETWEEN 1 AND 120",
            name="ck_campaigns_name_length",
        ),
        db.CheckConstraint(
            "length(trim(subject)) BETWEEN 1 AND 255",
            name="ck_campaigns_subject_length",
        ),
        db.CheckConstraint(
            "length(body_html) <= 250000",
            name="ck_campaigns_body_length",
        ),
        db.CheckConstraint(
            "status IN ('draft', 'scheduled', 'ready', 'completed')",
            name="ck_campaigns_status",
        ),
        db.Index("ix_campaigns_status_updated_at", "status", "updated_at"),
        db.Index("ix_campaigns_updated_at_id", "updated_at", "id"),
    )

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    status = db.Column(
        db.String(16), nullable=False, default="draft", server_default="draft"
    )
    email_draft_id = db.Column(
        db.Integer,
        ForeignKey("email_drafts.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    draft_name = db.Column(db.String(255), nullable=False)
    subject = db.Column(db.String(255), nullable=False)
    body_html = db.Column(db.Text, nullable=False)
    manual_recipient_ids = db.Column(db.JSON, nullable=False, default=list)
    recipient_group_ids = db.Column(db.JSON, nullable=False, default=list)
    recipient_group_names = db.Column(db.JSON, nullable=False, default=list)
    recipient_snapshot = db.Column(db.JSON, nullable=False, default=list)
    scheduled_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(
        db.DateTime,
        nullable=False,
        default=utc_now,
        server_default=db.func.current_timestamp(),
    )
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
        server_default=db.func.current_timestamp(),
    )

    @property
    def recipient_count(self):
        return len(self.recipient_snapshot or [])

    @property
    def status_label(self):
        return self.status.title()

    def __repr__(self):
        return f"<Campaign id={self.id} name={self.name!r} status={self.status!r}>"
