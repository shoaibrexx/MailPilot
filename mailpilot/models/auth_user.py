from datetime import datetime, timezone

from ..extensions import db


def utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class AuthUser(db.Model):
    __tablename__ = "auth_users"
    __table_args__ = (
        db.CheckConstraint("length(trim(username)) BETWEEN 3 AND 120", name="ck_auth_users_username"),
        db.CheckConstraint("length(trim(display_name)) BETWEEN 1 AND 120", name="ck_auth_users_display_name"),
        db.CheckConstraint("length(password_hash) <= 512", name="ck_auth_users_password_hash"),
    )

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(120, collation="NOCASE"), nullable=False, unique=True)
    display_name = db.Column(db.String(120), nullable=False, default="Workspace administrator")
    password_hash = db.Column(db.String(512), nullable=False)
    auth_version = db.Column(db.Integer, nullable=False, default=1, server_default="1")
    is_active = db.Column(db.Boolean, nullable=False, default=True, server_default=db.true())
    created_at = db.Column(db.DateTime, nullable=False, default=utc_now, server_default=db.func.current_timestamp())
    last_login_at = db.Column(db.DateTime, nullable=True)

    def __repr__(self):
        return f"<AuthUser id={self.id} username={self.username!r}>"
