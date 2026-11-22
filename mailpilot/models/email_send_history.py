from datetime import datetime, timezone

from sqlalchemy import ForeignKey

from ..extensions import db


def utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class EmailSendHistory(db.Model):
    __tablename__ = "email_send_history"
    __table_args__ = (
        db.CheckConstraint(
            "status IN ('pending', 'sent', 'partial', 'failed', 'uncertain')",
            name="ck_email_send_history_status",
        ),
        db.CheckConstraint(
            "recipient_count BETWEEN 1 AND 100",
            name="ck_email_send_history_recipient_count",
        ),
        db.CheckConstraint(
            "length(subject) <= 255",
            name="ck_email_send_history_subject_length",
        ),
        db.CheckConstraint(
            "error_details IS NULL OR length(error_details) <= 1000",
            name="ck_email_send_history_error_length",
        ),
        db.CheckConstraint(
            "consent_confirmed IN (0, 1)",
            name="ck_email_send_history_consent_boolean",
        ),
        db.Index("ix_email_send_history_attempted_id", "attempted_at", "id"),
        db.Index(
            "uq_email_send_history_active_campaign",
            "campaign_id",
            unique=True,
            sqlite_where=db.text("status IN ('pending', 'sent', 'partial', 'uncertain')"),
            postgresql_where=db.text("status IN ('pending', 'sent', 'partial', 'uncertain')"),
        ),
        db.Index(
            "uq_email_send_history_blocked_draft",
            "draft_id",
            unique=True,
            sqlite_where=db.text("status IN ('pending', 'partial', 'uncertain')"),
            postgresql_where=db.text("status IN ('pending', 'partial', 'uncertain')"),
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    submission_key = db.Column(db.String(80), nullable=False, unique=True, index=True)
    draft_id = db.Column(
        db.Integer,
        ForeignKey("email_drafts.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    campaign_id = db.Column(
        db.Integer,
        ForeignKey("campaigns.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    recipient_addresses = db.Column(db.JSON, nullable=False, default=dict)
    recipient_count = db.Column(db.Integer, nullable=False)
    subject = db.Column(db.String(255), nullable=False)
    status = db.Column(db.String(16), nullable=False, default="pending", server_default="pending")
    consent_confirmed = db.Column(db.Boolean, nullable=False, default=False, server_default=db.false())
    error_details = db.Column(db.String(1000), nullable=True)
    attempted_at = db.Column(db.DateTime, nullable=False, default=utc_now, server_default=db.func.current_timestamp())
    completed_at = db.Column(db.DateTime, nullable=True)

    @property
    def status_label(self):
        return self.status.title()

    def __repr__(self):
        return f"<EmailSendHistory id={self.id} status={self.status!r} recipient_count={self.recipient_count}>"
