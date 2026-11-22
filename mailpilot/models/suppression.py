from datetime import datetime, timezone

from ..extensions import db


def utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Suppression(db.Model):
    __tablename__ = "suppressions"
    __table_args__ = (
        db.CheckConstraint("scope IN ('global', 'campaign')", name="ck_suppressions_scope"),
        db.CheckConstraint("source IN ('unsubscribe', 'manual', 'bounce', 'import')", name="ck_suppressions_source"),
        db.CheckConstraint(
            "(scope = 'global' AND campaign_id IS NULL) OR (scope = 'campaign' AND campaign_id IS NOT NULL)",
            name="ck_suppressions_campaign_scope",
        ),
        db.CheckConstraint("length(email) BETWEEN 3 AND 254", name="ck_suppressions_email_length"),
        db.CheckConstraint("length(reason) <= 250", name="ck_suppressions_reason_length"),
        db.Index("uq_suppressions_global_email", "email", unique=True, sqlite_where=db.text("scope = 'global'"), postgresql_where=db.text("scope = 'global'")),
        db.Index("uq_suppressions_campaign_email", "campaign_id", "email", unique=True, sqlite_where=db.text("scope = 'campaign'"), postgresql_where=db.text("scope = 'campaign'")),
        db.Index("ix_suppressions_created_at", "created_at"),
    )

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(254, collation="NOCASE"), nullable=False)
    scope = db.Column(db.String(16), nullable=False)
    campaign_id = db.Column(db.Integer, nullable=True)  # Intentionally not a foreign key: preserve unsubscribes after deletion.
    source = db.Column(db.String(16), nullable=False, default="manual", server_default="manual")
    reason = db.Column(db.String(250), nullable=False, default="", server_default="")
    created_at = db.Column(db.DateTime, nullable=False, default=utc_now, server_default=db.func.current_timestamp())

    @property
    def scope_label(self):
        return "All campaigns" if self.scope == "global" else f"Campaign #{self.campaign_id}"

    def __repr__(self):
        return f"<Suppression id={self.id} scope={self.scope!r} source={self.source!r}>"
