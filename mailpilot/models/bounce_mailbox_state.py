from datetime import datetime, timezone

from ..extensions import db


def utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class BounceMailboxState(db.Model):
    __tablename__ = "bounce_mailbox_state"
    __table_args__ = (
        db.CheckConstraint("length(mailbox_hash) = 64", name="ck_bounce_mailbox_hash_length"),
        db.CheckConstraint("length(uidvalidity) <= 64", name="ck_bounce_mailbox_uidvalidity_length"),
        db.CheckConstraint("last_uid >= 0", name="ck_bounce_mailbox_last_uid"),
        db.CheckConstraint("length(last_error_type) <= 80", name="ck_bounce_mailbox_error_type"),
    )

    id = db.Column(db.Integer, primary_key=True)
    mailbox_hash = db.Column(db.String(64), nullable=False, unique=True)
    uidvalidity = db.Column(db.String(64), nullable=False, default="", server_default="")
    last_uid = db.Column(db.Integer, nullable=False, default=0, server_default="0")
    last_polled_at = db.Column(db.DateTime, nullable=True)
    last_error_type = db.Column(db.String(80), nullable=False, default="", server_default="")
    updated_at = db.Column(db.DateTime, nullable=False, default=utc_now, onupdate=utc_now, server_default=db.func.current_timestamp())

    def __repr__(self):
        return f"<BounceMailboxState id={self.id} last_uid={self.last_uid}>"
