from datetime import datetime, timezone

from sqlalchemy import ForeignKey

from ..extensions import db


def utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


ATTEMPT_STATUSES = ("sending", "accepted", "temporary_failure", "permanent_failure", "uncertain")


class CampaignDeliveryAttempt(db.Model):
    __tablename__ = "campaign_delivery_attempts"
    __table_args__ = (
        db.CheckConstraint("status IN ('sending', 'accepted', 'temporary_failure', 'permanent_failure', 'uncertain')", name="ck_campaign_delivery_attempts_status"),
        db.CheckConstraint("attempt_number BETWEEN 1 AND 10", name="ck_campaign_delivery_attempts_number"),
        db.CheckConstraint("length(message_id) <= 255", name="ck_campaign_delivery_attempts_message_id"),
        db.CheckConstraint("length(error_details) <= 500", name="ck_campaign_delivery_attempts_error"),
        db.CheckConstraint("length(smtp_code) <= 16", name="ck_campaign_delivery_attempts_smtp_code"),
        db.UniqueConstraint("delivery_id", "attempt_number", name="uq_campaign_delivery_attempt_number"),
        db.Index("ix_campaign_delivery_attempts_attempted_at", "attempted_at"),
        db.Index("ix_campaign_delivery_attempts_message_id", "message_id"),
    )

    id = db.Column(db.Integer, primary_key=True)
    delivery_id = db.Column(db.Integer, ForeignKey("campaign_deliveries.id", ondelete="CASCADE"), nullable=False, index=True)
    attempt_number = db.Column(db.Integer, nullable=False)
    message_id = db.Column(db.String(255), nullable=False)
    status = db.Column(db.String(24), nullable=False, default="sending", server_default="sending")
    smtp_code = db.Column(db.String(16), nullable=False, default="", server_default="")
    error_details = db.Column(db.String(500), nullable=False, default="", server_default="")
    attempted_at = db.Column(db.DateTime, nullable=False, default=utc_now, server_default=db.func.current_timestamp())
    completed_at = db.Column(db.DateTime, nullable=True)

    delivery = db.relationship("CampaignDelivery", back_populates="attempts")

    def __repr__(self):
        return f"<CampaignDeliveryAttempt id={self.id} status={self.status!r}>"
