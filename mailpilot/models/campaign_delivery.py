from datetime import datetime, timezone

from sqlalchemy import ForeignKey

from ..extensions import db


def utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


DELIVERY_STATUSES = (
    "pending", "sending", "accepted", "temporary_failure", "permanent_failure",
    "suppressed", "uncertain", "bounced_temporary", "bounced_permanent",
)


class CampaignDelivery(db.Model):
    __tablename__ = "campaign_deliveries"
    __table_args__ = (
        db.CheckConstraint("status IN ('pending', 'sending', 'accepted', 'temporary_failure', 'permanent_failure', 'suppressed', 'uncertain', 'bounced_temporary', 'bounced_permanent')", name="ck_campaign_deliveries_status"),
        db.CheckConstraint("length(email) BETWEEN 3 AND 254", name="ck_campaign_deliveries_email_length"),
        db.CheckConstraint("length(error_details) <= 500", name="ck_campaign_deliveries_error_length"),
        db.CheckConstraint("attempt_count BETWEEN 0 AND 10", name="ck_campaign_deliveries_attempts"),
        db.UniqueConstraint("job_id", "email", name="uq_campaign_deliveries_job_email"),
        db.Index("ix_campaign_deliveries_status_retry", "status", "retry_at"),
    )

    id = db.Column(db.Integer, primary_key=True)
    job_id = db.Column(db.Integer, ForeignKey("campaign_jobs.id", ondelete="CASCADE"), nullable=False, index=True)
    email = db.Column(db.String(254, collation="NOCASE"), nullable=False)
    status = db.Column(db.String(24), nullable=False, default="pending", server_default="pending")
    attempt_count = db.Column(db.Integer, nullable=False, default=0, server_default="0")
    message_id = db.Column(db.String(255), nullable=True)
    last_attempt_at = db.Column(db.DateTime, nullable=True)
    retry_at = db.Column(db.DateTime, nullable=True)
    accepted_at = db.Column(db.DateTime, nullable=True)
    bounced_at = db.Column(db.DateTime, nullable=True)
    error_details = db.Column(db.String(500), nullable=False, default="", server_default="")
    created_at = db.Column(db.DateTime, nullable=False, default=utc_now, server_default=db.func.current_timestamp())
    updated_at = db.Column(db.DateTime, nullable=False, default=utc_now, onupdate=utc_now, server_default=db.func.current_timestamp())

    job = db.relationship("CampaignJob", back_populates="deliveries")
    attempts = db.relationship("CampaignDeliveryAttempt", back_populates="delivery", cascade="all, delete-orphan", passive_deletes=True, order_by="CampaignDeliveryAttempt.attempt_number")

    def __repr__(self):
        return f"<CampaignDelivery id={self.id} status={self.status!r}>"
