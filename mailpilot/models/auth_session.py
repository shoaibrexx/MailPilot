from datetime import datetime, timezone

from sqlalchemy import ForeignKey

from ..extensions import db


def utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class AuthSession(db.Model):
    __tablename__ = "auth_sessions"
    __table_args__ = (
        db.CheckConstraint("length(token_hash) = 64", name="ck_auth_sessions_token_hash"),
        db.Index("ix_auth_sessions_user_expiry", "user_id", "expires_at"),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        ForeignKey("auth_users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    token_hash = db.Column(db.String(64), nullable=False, unique=True)
    auth_version = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=utc_now)
    last_seen_at = db.Column(db.DateTime, nullable=False, default=utc_now)
    expires_at = db.Column(db.DateTime, nullable=False)
    revoked_at = db.Column(db.DateTime, nullable=True)

    @property
    def is_active(self):
        return self.revoked_at is None and self.expires_at > utc_now()

    def __repr__(self):
        return f"<AuthSession id={self.id} user_id={self.user_id} revoked={self.revoked_at is not None}>"
