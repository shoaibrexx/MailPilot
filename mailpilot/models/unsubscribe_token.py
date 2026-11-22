from datetime import datetime, timezone

from ..extensions import db


def utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class UnsubscribeToken(db.Model):
    __tablename__ = "unsubscribe_tokens"
    __table_args__ = (
        db.CheckConstraint("length(token_hash) = 64", name="ck_unsubscribe_tokens_hash"),
        db.CheckConstraint("length(email) BETWEEN 3 AND 254", name="ck_unsubscribe_tokens_email"),
        db.Index("ix_unsubscribe_tokens_email_campaign", "email", "campaign_id"),
    )

    id = db.Column(db.Integer, primary_key=True)
    token_hash = db.Column(db.String(64), nullable=False, unique=True)
    email = db.Column(db.String(254, collation="NOCASE"), nullable=False)
    campaign_id = db.Column(db.Integer, nullable=True)  # Keep the token valid if its campaign is deleted.
    created_at = db.Column(db.DateTime, nullable=False, default=utc_now, server_default=db.func.current_timestamp())
    used_at = db.Column(db.DateTime, nullable=True)

    def __repr__(self):
        return f"<UnsubscribeToken id={self.id} campaign_id={self.campaign_id}>"
