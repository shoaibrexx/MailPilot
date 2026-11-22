from datetime import datetime, timezone

from ..extensions import db


def utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class LoginThrottle(db.Model):
    __tablename__ = "login_throttles"
    __table_args__ = (
        db.CheckConstraint("scope IN ('username', 'source')", name="ck_login_throttles_scope"),
        db.CheckConstraint("attempts >= 0", name="ck_login_throttles_attempts"),
        db.UniqueConstraint("scope", "key_hash", name="uq_login_throttles_scope_hash"),
        db.Index("ix_login_throttles_locked_until", "locked_until"),
    )

    id = db.Column(db.Integer, primary_key=True)
    scope = db.Column(db.String(16), nullable=False)
    key_hash = db.Column(db.String(64), nullable=False)
    attempts = db.Column(db.Integer, nullable=False, default=0)
    window_started_at = db.Column(db.DateTime, nullable=False, default=utc_now)
    locked_until = db.Column(db.DateTime, nullable=True)
    updated_at = db.Column(db.DateTime, nullable=False, default=utc_now, onupdate=utc_now)

    def __repr__(self):
        return f"<LoginThrottle scope={self.scope!r} attempts={self.attempts}>"
