from datetime import datetime, timezone

from ..extensions import db


def utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Notification(db.Model):
    __tablename__ = "notifications"
    __table_args__ = (
        db.CheckConstraint(
            "category IN ('success', 'warning', 'error', 'info')",
            name="ck_notifications_category",
        ),
        db.CheckConstraint("length(trim(title)) BETWEEN 1 AND 120", name="ck_notifications_title"),
        db.CheckConstraint("length(trim(message)) BETWEEN 1 AND 1000", name="ck_notifications_message"),
        db.CheckConstraint("is_read IN (0, 1)", name="ck_notifications_read_boolean"),
        db.Index("ix_notifications_created_at_id", "created_at", "id"),
        db.Index("ix_notifications_read_created_at", "is_read", "created_at"),
        db.Index("ix_notifications_category_created_at", "category", "created_at"),
    )

    id = db.Column(db.Integer, primary_key=True)
    category = db.Column(db.String(12), nullable=False, default="info", server_default="info")
    title = db.Column(db.String(120), nullable=False)
    message = db.Column(db.String(1000), nullable=False)
    source = db.Column(db.String(80), nullable=False, default="workspace", server_default="workspace")
    is_read = db.Column(db.Boolean, nullable=False, default=False, server_default=db.false())
    created_at = db.Column(
        db.DateTime, nullable=False, default=utc_now, server_default=db.func.current_timestamp()
    )
    read_at = db.Column(db.DateTime, nullable=True)

    @property
    def category_label(self):
        return self.category.title() if self.category != "info" else "Information"

    def __repr__(self):
        return f"<Notification id={self.id} category={self.category!r}>"
