from datetime import datetime, timezone

from ..extensions import db


def utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class WorkspaceActionThrottle(db.Model):
    __tablename__ = "workspace_action_throttles"
    __table_args__ = (
        db.CheckConstraint("length(action_key) BETWEEN 1 AND 80", name="ck_action_throttle_key_length"),
    )

    action_key = db.Column(db.String(80), primary_key=True)
    next_allowed_at = db.Column(db.DateTime, nullable=False)
    updated_at = db.Column(db.DateTime, nullable=False, default=utc_now, onupdate=utc_now)

    def __repr__(self):
        return f"<WorkspaceActionThrottle action_key={self.action_key!r}>"
