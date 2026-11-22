from datetime import datetime, timezone

from ..extensions import db


def utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class SecurityEvent(db.Model):
    __tablename__ = "security_events"
    __table_args__ = (
        db.CheckConstraint("length(trim(action)) BETWEEN 1 AND 80", name="ck_security_events_action"),
        db.CheckConstraint("length(trim(details)) BETWEEN 1 AND 250", name="ck_security_events_details"),
        db.CheckConstraint("method IN ('POST', 'PUT', 'PATCH', 'DELETE')", name="ck_security_events_method"),
        db.CheckConstraint("outcome IN ('success', 'rejected')", name="ck_security_events_outcome"),
        db.Index("ix_security_events_created_at_id", "created_at", "id"),
        db.Index("ix_security_events_outcome_created_at", "outcome", "created_at"),
    )

    id = db.Column(db.Integer, primary_key=True)
    action = db.Column(db.String(80), nullable=False)
    details = db.Column(db.String(250), nullable=False)
    endpoint = db.Column(db.String(100), nullable=False)
    method = db.Column(db.String(8), nullable=False)
    status_code = db.Column(db.Integer, nullable=False)
    outcome = db.Column(db.String(12), nullable=False)
    created_at = db.Column(
        db.DateTime, nullable=False, default=utc_now, server_default=db.func.current_timestamp()
    )

    def __repr__(self):
        return f"<SecurityEvent id={self.id} action={self.action!r} outcome={self.outcome!r}>"
