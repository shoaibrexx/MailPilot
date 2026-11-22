from ..extensions import db


class NotificationPreference(db.Model):
    __tablename__ = "notification_preferences"
    __table_args__ = (
        db.CheckConstraint("id = 1", name="ck_notification_preferences_singleton"),
        db.CheckConstraint("success_enabled IN (0, 1)", name="ck_notification_preferences_success"),
        db.CheckConstraint("warning_enabled IN (0, 1)", name="ck_notification_preferences_warning"),
        db.CheckConstraint("error_enabled IN (0, 1)", name="ck_notification_preferences_error"),
        db.CheckConstraint("info_enabled IN (0, 1)", name="ck_notification_preferences_info"),
    )

    id = db.Column(db.Integer, primary_key=True, default=1)
    success_enabled = db.Column(db.Boolean, nullable=False, default=True, server_default=db.true())
    warning_enabled = db.Column(db.Boolean, nullable=False, default=True, server_default=db.true())
    error_enabled = db.Column(db.Boolean, nullable=False, default=True, server_default=db.true())
    info_enabled = db.Column(db.Boolean, nullable=False, default=True, server_default=db.true())

    def __repr__(self):
        return f"<NotificationPreference id={self.id}>"
