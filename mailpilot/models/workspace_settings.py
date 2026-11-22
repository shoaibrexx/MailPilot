from datetime import datetime, timezone

from ..extensions import db


def utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class WorkspaceSettings(db.Model):
    __tablename__ = "workspace_settings"
    __table_args__ = (
        db.CheckConstraint("id = 1", name="ck_workspace_settings_singleton"),
        db.CheckConstraint(
            "length(trim(workspace_name)) BETWEEN 1 AND 120",
            name="ck_workspace_settings_name",
        ),
        db.CheckConstraint(
            "length(workspace_description) <= 500",
            name="ck_workspace_settings_description",
        ),
        db.CheckConstraint("length(profile_name) <= 120", name="ck_workspace_settings_profile_name"),
        db.CheckConstraint("length(profile_email) <= 254", name="ck_workspace_settings_profile_email"),
        db.CheckConstraint("length(default_sender_name) <= 120", name="ck_workspace_settings_sender_name"),
        db.CheckConstraint("length(default_sender_email) <= 254", name="ck_workspace_settings_sender_email"),
        db.CheckConstraint("length(email_signature) <= 10000", name="ck_workspace_settings_signature"),
        db.CheckConstraint("length(email_footer) <= 10000", name="ck_workspace_settings_footer"),
        db.CheckConstraint("length(default_cc) <= 5000", name="ck_workspace_settings_cc"),
        db.CheckConstraint("length(default_bcc) <= 5000", name="ck_workspace_settings_bcc"),
        db.CheckConstraint(
            "theme_preference IN ('light', 'dark', 'system')",
            name="ck_workspace_settings_theme",
        ),
        db.CheckConstraint(
            "date_format IN ('yyyy-mm-dd', 'dd/mm/yyyy', 'mm/dd/yyyy')",
            name="ck_workspace_settings_date_format",
        ),
        db.CheckConstraint(
            "time_format IN ('24h', '12h')",
            name="ck_workspace_settings_time_format",
        ),
        db.CheckConstraint(
            "timezone IN ('UTC', 'America/New_York', 'America/Chicago', 'America/Denver', 'America/Los_Angeles', 'Europe/London', 'Europe/Paris', 'Europe/Berlin', 'Asia/Dubai', 'Asia/Kolkata', 'Asia/Singapore', 'Asia/Tokyo', 'Australia/Sydney', 'Pacific/Auckland')",
            name="ck_workspace_settings_timezone",
        ),
    )

    id = db.Column(db.Integer, primary_key=True, default=1)
    workspace_name = db.Column(
        db.String(120), nullable=False, default="MailPilot Workspace", server_default="MailPilot Workspace"
    )
    workspace_description = db.Column(
        db.String(500), nullable=False, default="Organize audiences and campaign plans.",
        server_default="Organize audiences and campaign plans.",
    )
    profile_name = db.Column(db.String(120), nullable=False, default="", server_default="")
    profile_email = db.Column(db.String(254), nullable=False, default="", server_default="")
    timezone = db.Column(db.String(64), nullable=False, default="UTC", server_default="UTC")
    date_format = db.Column(
        db.String(16), nullable=False, default="yyyy-mm-dd", server_default="yyyy-mm-dd"
    )
    time_format = db.Column(db.String(8), nullable=False, default="24h", server_default="24h")
    default_sender_name = db.Column(db.String(120), nullable=False, default="", server_default="")
    default_sender_email = db.Column(db.String(254), nullable=False, default="", server_default="")
    email_signature = db.Column(db.Text, nullable=False, default="", server_default="")
    email_footer = db.Column(db.Text, nullable=False, default="", server_default="")
    default_cc = db.Column(db.Text, nullable=False, default="", server_default="")
    default_bcc = db.Column(db.Text, nullable=False, default="", server_default="")
    theme_preference = db.Column(
        db.String(8), nullable=False, default="system", server_default="system"
    )
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
        server_default=db.func.current_timestamp(),
    )

    def __repr__(self):
        return f"<WorkspaceSettings id={self.id} name={self.workspace_name!r}>"
