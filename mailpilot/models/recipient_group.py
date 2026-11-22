from datetime import datetime, timezone

from ..extensions import db


def utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class RecipientGroup(db.Model):
    __tablename__ = "recipient_groups"
    __table_args__ = (
        db.UniqueConstraint("name", name="uq_recipient_groups_name"),
        db.CheckConstraint(
            "length(trim(name)) BETWEEN 1 AND 120",
            name="ck_recipient_groups_name_length",
        ),
        db.Index("ix_recipient_groups_updated_at_id", "updated_at", "id"),
    )

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120, collation="NOCASE"), nullable=False)
    recipient_ids = db.Column(db.JSON, nullable=False, default=list)
    created_at = db.Column(
        db.DateTime,
        nullable=False,
        default=utc_now,
        server_default=db.func.current_timestamp(),
    )
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
        server_default=db.func.current_timestamp(),
    )

    def __repr__(self):
        return f"<RecipientGroup id={self.id} name={self.name!r}>"
