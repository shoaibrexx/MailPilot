from datetime import datetime, timezone

from sqlalchemy import ForeignKey

from ..extensions import db


def utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


JOB_STATUSES = ("queued", "running", "retrying", "completed", "partial", "failed", "uncertain", "cancelled")


class CampaignJob(db.Model):
    __tablename__ = "campaign_jobs"
    __table_args__ = (
        db.CheckConstraint("status IN ('queued', 'running', 'retrying', 'completed', 'partial', 'failed', 'uncertain', 'cancelled')", name="ck_campaign_jobs_status"),
        db.CheckConstraint("length(job_key) BETWEEN 32 AND 100", name="ck_campaign_jobs_key_length"),
        db.CheckConstraint("length(error_details) <= 500", name="ck_campaign_jobs_error_length"),
        db.CheckConstraint("total_recipients BETWEEN 0 AND 100", name="ck_campaign_jobs_recipient_limit"),
        db.Index("ix_campaign_jobs_status_due", "status", "scheduled_for"),
        db.Index("ix_campaign_jobs_campaign_created", "campaign_id", "created_at"),
        db.Index(
            "uq_campaign_jobs_active_campaign",
            "campaign_id",
            unique=True,
            sqlite_where=db.text("campaign_id IS NOT NULL AND status IN ('queued', 'running', 'retrying', 'uncertain')"),
            postgresql_where=db.text("campaign_id IS NOT NULL AND status IN ('queued', 'running', 'retrying', 'uncertain')"),
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    # Intentionally not a foreign key so deletion of a campaign preserves its execution history.
    campaign_id = db.Column(db.Integer, nullable=True)
    email_send_history_id = db.Column(db.Integer, ForeignKey("email_send_history.id", ondelete="SET NULL"), nullable=True, unique=True)
    job_key = db.Column(db.String(100), nullable=False, unique=True)
    kind = db.Column(db.String(16), nullable=False, default="immediate", server_default="immediate")
    status = db.Column(db.String(16), nullable=False, default="queued", server_default="queued")
    consent_confirmed = db.Column(db.Boolean, nullable=False, default=False, server_default=db.false())
    snapshot = db.Column(db.JSON, nullable=False, default=dict)
    scheduled_for = db.Column(db.DateTime, nullable=False, default=utc_now, index=True)
    total_recipients = db.Column(db.Integer, nullable=False, default=0, server_default="0")
    attempt_count = db.Column(db.Integer, nullable=False, default=0, server_default="0")
    lease_owner = db.Column(db.String(64), nullable=True)
    lease_expires_at = db.Column(db.DateTime, nullable=True)
    started_at = db.Column(db.DateTime, nullable=True)
    completed_at = db.Column(db.DateTime, nullable=True)
    error_details = db.Column(db.String(500), nullable=False, default="", server_default="")
    created_at = db.Column(db.DateTime, nullable=False, default=utc_now, server_default=db.func.current_timestamp())
    updated_at = db.Column(db.DateTime, nullable=False, default=utc_now, onupdate=utc_now, server_default=db.func.current_timestamp())

    deliveries = db.relationship("CampaignDelivery", back_populates="job", cascade="all, delete-orphan", passive_deletes=True, order_by="CampaignDelivery.id")
    email_send_history = db.relationship("EmailSendHistory", foreign_keys=[email_send_history_id])

    def __repr__(self):
        return f"<CampaignJob id={self.id} status={self.status!r} kind={self.kind!r}>"
