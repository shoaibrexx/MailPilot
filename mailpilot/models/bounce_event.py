from datetime import datetime, timezone

from sqlalchemy import ForeignKey

from ..extensions import db


def utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class BounceEvent(db.Model):
    __tablename__ = "bounce_events"
    __table_args__ = (
        db.CheckConstraint("classification IN ('temporary', 'permanent')", name="ck_bounce_events_classification"),
        db.CheckConstraint("length(recipient_email) BETWEEN 3 AND 254", name="ck_bounce_events_email_length"),
        db.CheckConstraint("length(status_code) <= 16", name="ck_bounce_events_status_code_length"),
        db.CheckConstraint("length(diagnostic) <= 500", name="ck_bounce_events_diagnostic_length"),
        db.Index("ix_bounce_events_email_time", "recipient_email", "bounced_at"),
    )

    id = db.Column(db.Integer, primary_key=True)
    delivery_id = db.Column(db.Integer, ForeignKey("campaign_deliveries.id", ondelete="SET NULL"), nullable=True)
    source_key = db.Column(db.String(255), nullable=False, unique=True)
    recipient_email = db.Column(db.String(254, collation="NOCASE"), nullable=False)
    classification = db.Column(db.String(16), nullable=False)
    status_code = db.Column(db.String(16), nullable=False, default="", server_default="")
    diagnostic = db.Column(db.String(500), nullable=False, default="", server_default="")
    bounced_at = db.Column(db.DateTime, nullable=False, default=utc_now)
    created_at = db.Column(db.DateTime, nullable=False, default=utc_now, server_default=db.func.current_timestamp())

    delivery = db.relationship("CampaignDelivery")

    def __repr__(self):
        return f"<BounceEvent id={self.id} classification={self.classification!r}>"
