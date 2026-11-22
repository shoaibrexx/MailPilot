from datetime import datetime, timezone

from ..extensions import db


def utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class MailingProfile(db.Model):
    __tablename__ = "mailing_profile"
    __table_args__ = (
        db.CheckConstraint("id = 1", name="ck_mailing_profile_singleton"),
        db.CheckConstraint("length(postal_address) <= 1000", name="ck_mailing_profile_address_length"),
    )

    id = db.Column(db.Integer, primary_key=True, default=1)
    postal_address = db.Column(db.String(1000), nullable=False, default="", server_default="")
    updated_at = db.Column(db.DateTime, nullable=False, default=utc_now, onupdate=utc_now, server_default=db.func.current_timestamp())

    def __repr__(self):
        return f"<MailingProfile id={self.id}>"
