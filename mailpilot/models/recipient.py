from datetime import datetime, timezone

from ..extensions import db


def utc_now():
    """Return UTC as a naive datetime for consistent SQLite storage."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Recipient(db.Model):
    __tablename__ = "recipients"
    __table_args__ = (
        db.UniqueConstraint("email", name="uq_recipients_email"),
        db.CheckConstraint(
            "length(trim(email)) BETWEEN 3 AND 254",
            name="ck_recipients_email_length",
        ),
        db.CheckConstraint(
            "first_name IS NULL OR length(first_name) <= 80",
            name="ck_recipients_first_name_length",
        ),
        db.CheckConstraint(
            "last_name IS NULL OR length(last_name) <= 80",
            name="ck_recipients_last_name_length",
        ),
        db.CheckConstraint(
            "company IS NULL OR length(company) <= 120",
            name="ck_recipients_company_length",
        ),
        db.CheckConstraint(
            "is_active IN (0, 1)",
            name="ck_recipients_active_boolean",
        ),
        db.Index("ix_recipients_created_at_id", "created_at", "id"),
        db.Index("ix_recipients_active_created_at", "is_active", "created_at"),
        db.Index("ix_recipients_last_first_name", "last_name", "first_name"),
    )

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(
        db.String(254, collation="NOCASE"), nullable=False
    )
    first_name = db.Column(db.String(80), nullable=True)
    last_name = db.Column(db.String(80), nullable=True)
    company = db.Column(db.String(120), nullable=True)
    is_active = db.Column(
        db.Boolean, nullable=False, default=True, server_default=db.true()
    )
    created_at = db.Column(
        db.DateTime, nullable=False, default=utc_now, server_default=db.func.current_timestamp()
    )
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
        server_default=db.func.current_timestamp(),
    )

    @property
    def full_name(self):
        return " ".join(part for part in (self.first_name, self.last_name) if part)

    def __repr__(self):
        return f"<Recipient id={self.id} email={self.email!r}>"
