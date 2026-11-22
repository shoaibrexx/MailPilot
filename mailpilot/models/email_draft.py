from datetime import datetime, timezone

from ..extensions import db


def utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class EmailDraft(db.Model):
    __tablename__ = "email_drafts"
    __table_args__ = (
        db.CheckConstraint(
            "length(sender_identity) BETWEEN 1 AND 32",
            name="ck_email_drafts_sender_length",
        ),
        db.CheckConstraint(
            "length(subject) <= 255",
            name="ck_email_drafts_subject_length",
        ),
        db.CheckConstraint(
            "length(body_html) <= 250000",
            name="ck_email_drafts_body_length",
        ),
        db.Index("ix_email_drafts_updated_at_id", "updated_at", "id"),
    )

    id = db.Column(db.Integer, primary_key=True)
    sender_identity = db.Column(
        db.String(32), nullable=False, default="workspace", server_default="workspace"
    )
    to_recipients = db.Column(db.JSON, nullable=False, default=list)
    cc_recipients = db.Column(db.JSON, nullable=False, default=list)
    bcc_recipients = db.Column(db.JSON, nullable=False, default=list)
    subject = db.Column(db.String(255), nullable=False, default="", server_default="")
    body_html = db.Column(db.Text, nullable=False, default="", server_default="")
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
        return f"<EmailDraft id={self.id} subject={self.subject!r}>"
