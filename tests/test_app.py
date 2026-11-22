import io
import re
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import inspect

from mailpilot import create_app
from mailpilot.extensions import db
from mailpilot.models import (
    Campaign,
    CampaignDelivery,
    CampaignJob,
    EmailDraft,
    EmailSendHistory,
    Notification,
    NotificationPreference,
    Recipient,
    RecipientGroup,
    SecurityEvent,
    WorkspaceSettings,
)
from mailpilot.services.analytics_service import get_analytics_data
from mailpilot.services.notification_service import create_notification
from mailpilot.services.recipient_service import create_recipient


class MailPilotStartupTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        database_path = root / "mailpilot-test.sqlite"
        self.upload_directory = root / "uploads"
        database_uri = f"sqlite:///{database_path.as_posix()}"
        self.app = create_app(
            {
                "TESTING": True,
                "SECRET_KEY": "mailpilot-test-secret",
                "WTF_CSRF_ENABLED": False,
                "AUTH_DISABLED": True,
                "SQLALCHEMY_DATABASE_URI": database_uri,
                "UPLOAD_FOLDER": str(self.upload_directory),
            }
        )
        self.client = self.app.test_client()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.engine.dispose()
        self.temporary_directory.cleanup()

    def test_application_starts_and_renders_home_page(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'data-theme="dark"', response.data)
        self.assertIn(b'data-theme-preference="dark"', response.data)
        self.assertIn(b"MailPilot is ready", response.data)
        self.assertIn(b"theme-toggle", response.data)
        for navigation_item in (
            b"Dashboard",
            b"Recipients",
            b"Email Composer",
            b"Campaigns",
            b"Analytics",
            b"Settings",
            b"Notifications",
            b"Security",
        ):
            self.assertIn(navigation_item, response.data)
        self.assertIn(b"No email activity yet", response.data)
        self.assertIn(b"No campaigns yet", response.data)

    def test_cyber_blue_theme_palettes_are_served_without_old_accent_tokens(self):
        response = self.client.get("/static/css/themes.css")

        self.assertEqual(response.status_code, 200)
        try:
            for color in (
                b"#050B18",
                b"#081426",
                b"#0D1B30",
                b"#00D9FF",
                b"#F0F7FC",
                b"#FFFFFF",
                b"#008FB8",
                b"#DDF5FC",
            ):
                self.assertIn(color, response.data)
            for retired_color in (b"#DC2626", b"#B91C1C", b"#0b1220"):
                self.assertNotIn(retired_color, response.data)
        finally:
            response.close()

    def test_settings_page_is_registered_and_additively_creates_preferences(self):
        response = self.client.get("/settings")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"General settings", response.data)
        self.assertIn(b"Email preferences", response.data)
        self.assertIn(b"Appearance", response.data)
        self.assertIn(b"Data management", response.data)
        self.assertIn(b"Workspace information", response.data)
        self.assertIn(b"aria-current=\"page\"", response.data)
        with self.app.app_context():
            preferences = db.session.get(WorkspaceSettings, 1)
            self.assertEqual(preferences.workspace_name, "MailPilot Workspace")
            self.assertEqual(preferences.theme_preference, "system")
            self.assertTrue(inspect(db.engine).has_table("workspace_settings"))

    def test_settings_persist_and_prefill_new_composer_drafts(self):
        response = self.client.post(
            "/settings",
            data={
                "workspace_name": "Northstar Studio",
                "workspace_description": "A local campaign workspace.",
                "profile_name": "Riley Morgan",
                "profile_email": "Riley@Example.com",
                "timezone": "Asia/Kolkata",
                "date_format": "dd/mm/yyyy",
                "time_format": "12h",
                "default_sender_name": "Riley Morgan",
                "default_sender_email": "Riley@Example.com",
                "default_cc": "Team@Example.com; ops@example.com",
                "default_bcc": "archive@example.com",
                "email_signature": "Best regards,\nRiley",
                "email_footer": "Northstar Studio · Unsubscribe details",
                "theme_preference": "dark",
            },
        )

        self.assertEqual(response.status_code, 303)
        with self.app.app_context():
            preferences = db.session.get(WorkspaceSettings, 1)
            self.assertEqual(preferences.workspace_name, "Northstar Studio")
            self.assertEqual(preferences.profile_email, "riley@example.com")
            self.assertEqual(preferences.timezone, "Asia/Kolkata")
            self.assertEqual(preferences.date_format, "dd/mm/yyyy")
            self.assertEqual(preferences.time_format, "12h")
            self.assertEqual(preferences.default_sender_email, "riley@example.com")
            self.assertEqual(preferences.default_cc, "team@example.com, ops@example.com")
            self.assertEqual(preferences.default_bcc, "archive@example.com")
            self.assertEqual(preferences.theme_preference, "dark")

        composer = self.client.get("/composer")
        self.assertEqual(composer.status_code, 200)
        self.assertIn(b"value=\"team@example.com, ops@example.com\"", composer.data)
        self.assertIn(b"value=\"archive@example.com\"", composer.data)
        self.assertIn(b"Best regards,", composer.data)
        self.assertIn(b"Northstar Studio", composer.data)
        self.assertIn(b"&lt;riley@example.com&gt;", composer.data)
        self.assertIn(b"data-theme=\"dark\"", composer.data)

    def test_settings_validation_rejects_invalid_email_timezone_and_overlapping_defaults(self):
        response = self.client.post(
            "/settings",
            data={
                "workspace_name": "",
                "profile_email": "not-an-email",
                "timezone": "Mars/Olympus",
                "date_format": "year-month-day",
                "time_format": "24",
                "default_sender_name": "Sender",
                "default_sender_email": "sender@example.com",
                "default_cc": "same@example.com",
                "default_bcc": "SAME@example.com",
                "theme_preference": "ultraviolet",
            },
        )

        self.assertEqual(response.status_code, 422)
        self.assertIn(b"Workspace name is required", response.data)
        self.assertIn(b"Enter a valid profile email", response.data)
        self.assertIn(b"Choose a supported timezone", response.data)
        self.assertIn(b"cannot be in both default CC and BCC", response.data)
        with self.app.app_context():
            preferences = db.session.get(WorkspaceSettings, 1)
            self.assertEqual(preferences.workspace_name, "MailPilot Workspace")
            self.assertEqual(preferences.theme_preference, "system")

    def test_theme_toggle_endpoint_persists_theme_and_rejects_unknown_values(self):
        saved = self.client.post("/settings/theme", json={"theme": "dark"})
        invalid = self.client.post("/settings/theme", json={"theme": "ultraviolet"})

        self.assertEqual(saved.status_code, 200)
        self.assertEqual(saved.get_json(), {"theme": "dark", "saved": True})
        self.assertEqual(invalid.status_code, 422)
        with self.app.app_context():
            self.assertEqual(db.session.get(WorkspaceSettings, 1).theme_preference, "dark")
        page = self.client.get("/settings")
        self.assertIn(b"data-theme=\"dark\"", page.data)

        light = self.client.post("/settings/theme", json={"theme": "light"})
        self.assertEqual(light.status_code, 200)
        self.assertEqual(light.get_json(), {"theme": "light", "saved": True})
        light_page = self.client.get("/")
        self.assertIn(b'data-theme="light"', light_page.data)
        with self.app.app_context():
            self.assertEqual(db.session.get(WorkspaceSettings, 1).theme_preference, "light")

    def test_workspace_csv_exports_include_real_rows_and_escape_spreadsheet_formulas(self):
        recipient_id = self.create_recipient(
            "export@example.com", first_name="=1+1", company="Northstar"
        )
        draft_id = self.create_email_draft("Export subject", "<p>Saved campaign content</p>")
        created = self.create_campaign(
            name="=HYPERLINK(\"https://example.invalid\",\"Open\")",
            draft_id=draft_id,
            recipient_ids=str(recipient_id),
        )

        recipients_csv = self.client.get("/settings/export/recipients.csv")
        campaigns_csv = self.client.get("/settings/export/campaigns.csv")

        self.assertEqual(recipients_csv.status_code, 200)
        self.assertEqual(campaigns_csv.status_code, 200)
        self.assertTrue(recipients_csv.mimetype.startswith("text/csv"))
        self.assertTrue(campaigns_csv.mimetype.startswith("text/csv"))
        self.assertIn("mailpilot-recipients.csv", recipients_csv.headers["Content-Disposition"])
        self.assertIn("mailpilot-campaigns.csv", campaigns_csv.headers["Content-Disposition"])
        self.assertIn("'=1+1", recipients_csv.get_data(as_text=True))
        exported_campaigns = campaigns_csv.get_data(as_text=True)
        self.assertIn("'=HYPERLINK", exported_campaigns)
        self.assertIn("Saved campaign content", exported_campaigns)
        self.assertIn("recipient_snapshot", exported_campaigns)
        self.assertIn("export@example.com", exported_campaigns)

    def test_workspace_reset_requires_exact_confirmation_and_preserves_preferences(self):
        recipient_id = self.create_recipient("reset@example.com")
        group_id = self.create_group("Reset audience", [recipient_id])
        draft_id = self.create_email_draft("Reset campaign")
        self.create_campaign(
            name="Reset test campaign",
            draft_id=draft_id,
            recipient_ids=str(recipient_id),
            group_ids=[group_id],
        )
        with self.app.app_context():
            campaign = db.session.scalar(db.select(Campaign).where(Campaign.name == "Reset test campaign"))
            job = CampaignJob(
                campaign_id=campaign.id,
                job_key="workspace-reset-job-key-0123456789-abcd",
                kind="immediate",
                status="queued",
                snapshot={"subject": "Reset campaign", "body_html": "<p>Reset</p>", "recipients": ["reset@example.com"]},
                scheduled_for=datetime.now(timezone.utc).replace(tzinfo=None),
                total_recipients=1,
            )
            db.session.add(job)
            db.session.flush()
            db.session.add(CampaignDelivery(job_id=job.id, email="reset@example.com"))
            db.session.commit()
            job_id = job.id
        saved_settings = self.client.post(
            "/settings",
            data={
                "workspace_name": "Preserved workspace",
                "workspace_description": "Preferences survive data reset.",
                "timezone": "UTC",
                "date_format": "yyyy-mm-dd",
                "time_format": "24h",
                "theme_preference": "light",
            },
        )
        self.assertEqual(saved_settings.status_code, 303)

        rejected = self.client.post("/settings/reset", data={"confirmation": "DELETE"})
        self.assertEqual(rejected.status_code, 422)
        with self.app.app_context():
            self.assertIsNotNone(db.session.get(Recipient, recipient_id))
            self.assertEqual(db.session.scalar(db.select(db.func.count(Campaign.id))), 1)

        completed = self.client.post(
            "/settings/reset", data={"confirmation": "DELETE ALL DATA"}
        )
        self.assertEqual(completed.status_code, 303)
        with self.app.app_context():
            self.assertEqual(db.session.scalar(db.select(db.func.count(Recipient.id))), 0)
            self.assertEqual(db.session.scalar(db.select(db.func.count(Campaign.id))), 0)
            self.assertEqual(db.session.scalar(db.select(db.func.count(EmailDraft.id))), 0)
            self.assertEqual(db.session.scalar(db.select(db.func.count(RecipientGroup.id))), 0)
            retained_job = db.session.get(CampaignJob, job_id)
            self.assertEqual(retained_job.status, "cancelled")
            self.assertEqual(retained_job.snapshot["recipients"], ["reset@example.com"])
            self.assertEqual(db.session.scalar(db.select(CampaignDelivery.status)), "suppressed")
            preferences = db.session.get(WorkspaceSettings, 1)
            self.assertEqual(preferences.workspace_name, "Preserved workspace")
            self.assertEqual(preferences.theme_preference, "light")

    def test_workspace_reset_refuses_to_race_an_active_campaign_worker(self):
        recipient_id = self.create_recipient("active-reset@example.com")
        draft_id = self.create_email_draft("Active reset campaign")
        self.create_campaign(
            name="Active reset campaign",
            draft_id=draft_id,
            recipient_ids=str(recipient_id),
        )
        with self.app.app_context():
            campaign = db.session.scalar(db.select(Campaign).where(Campaign.name == "Active reset campaign"))
            job = CampaignJob(
                campaign_id=campaign.id,
                job_key="workspace-active-job-key-0123456789-abcd",
                kind="immediate",
                status="running",
                lease_owner="worker-test",
                lease_expires_at=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=5),
                snapshot={"recipients": ["active-reset@example.com"]},
                scheduled_for=datetime.now(timezone.utc).replace(tzinfo=None),
                total_recipients=1,
            )
            db.session.add(job)
            db.session.commit()

        response = self.client.post("/settings/reset", data={"confirmation": "DELETE ALL DATA"})
        self.assertEqual(response.status_code, 409)
        self.assertIn(b"Wait for that send to finish", response.data)
        with self.app.app_context():
            self.assertEqual(db.session.scalar(db.select(db.func.count(Recipient.id))), 1)
            self.assertEqual(db.session.scalar(db.select(db.func.count(Campaign.id))), 1)
            self.assertEqual(db.session.scalar(db.select(CampaignJob.status)), "running")

    def test_workspace_reset_refuses_to_remove_a_pending_composer_send(self):
        draft_id = self.create_email_draft("Pending reset message")
        with self.app.app_context():
            db.session.add(EmailSendHistory(
                submission_key="pending-reset-send-key-0123456789-abcde",
                draft_id=draft_id,
                campaign_id=None,
                recipient_addresses={"to": ["person@example.com"], "cc": [], "bcc": []},
                recipient_count=1,
                subject="Pending reset message",
                status="pending",
                consent_confirmed=True,
            ))
            db.session.commit()

        response = self.client.post("/settings/reset", data={"confirmation": "DELETE ALL DATA"})
        self.assertEqual(response.status_code, 409)
        self.assertIn(b"A composer send is still in progress", response.data)
        with self.app.app_context():
            self.assertEqual(db.session.scalar(db.select(db.func.count(EmailDraft.id))), 1)
            self.assertEqual(db.session.scalar(db.select(EmailSendHistory.status)), "pending")

    def test_health_route_checks_sqlite_connection(self):
        response = self.client.get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.get_json(),
            {"status": "ok", "checks": {"database": "ok"}},
        )

    def test_dashboard_data_uses_empty_states_without_fabricating_counts(self):
        response = self.client.get("/api/dashboard")

        self.assertEqual(response.status_code, 200)
        result = response.get_json()
        self.assertEqual(result["data_status"], "available")
        self.assertEqual(result["stats"]["total_recipients"]["value"], 0)
        self.assertEqual(result["stats"]["active_recipients"]["value"], 0)
        self.assertEqual(result["stats"]["inactive_recipients"]["value"], 0)
        self.assertEqual(result["stats"]["campaigns_total"]["value"], 0)
        self.assertEqual(result["stats"]["campaigns_scheduled"]["value"], 0)
        self.assertEqual(result["stats"]["emails_sent"]["value"], 0)
        self.assertEqual(result["stats"]["successful_emails"]["value"], 0)
        self.assertEqual(result["stats"]["failed_emails"]["value"], 0)
        self.assertEqual(result["activity"], [])
        self.assertEqual(result["recent_campaigns"], [])

    def test_additive_campaign_tables_preserve_existing_recipient_rows(self):
        recipient_id = self.create_recipient(
            "preserved@example.com", first_name="Existing", last_name="Contact"
        )
        with self.app.app_context():
            db.create_all()
            db.session.expire_all()
            existing = db.session.get(Recipient, recipient_id)
            self.assertEqual(existing.email, "preserved@example.com")
            self.assertEqual(existing.full_name, "Existing Contact")
            self.assertTrue(inspect(db.engine).has_table("campaigns"))
            self.assertTrue(inspect(db.engine).has_table("recipient_groups"))
            self.assertTrue(inspect(db.engine).has_table("notifications"))
            self.assertTrue(inspect(db.engine).has_table("notification_preferences"))
            self.assertTrue(inspect(db.engine).has_table("security_events"))

    def create_recipient(self, email, **values):
        payload = {"email": email, "is_active": "active"}
        payload.update(values)
        with self.app.app_context():
            return create_recipient(payload).id

    def get_recipient(self, email):
        with self.app.app_context():
            return db.session.scalar(
                db.select(Recipient).where(Recipient.email == email)
            )

    def create_email_draft(self, subject="Campaign source", body_html="<p>Campaign body</p>"):
        response = self.client.post(
            "/composer/drafts",
            data={
                "sender_identity": "workspace",
                "to_recipients": "source@example.com",
                "subject": subject,
                "body_html": body_html,
            },
        )
        self.assertEqual(response.status_code, 303)
        return int(response.headers["Location"].rsplit("/", 1)[-1])

    def create_group(self, name, recipient_ids):
        response = self.client.post(
            "/campaigns/groups",
            data={"name": name, "recipient_ids": ",".join(map(str, recipient_ids))},
        )
        self.assertEqual(response.status_code, 303)
        with self.app.app_context():
            return db.session.scalar(
                db.select(RecipientGroup).where(RecipientGroup.name == name)
            ).id

    def create_campaign(self, *, name, draft_id, status="draft", recipient_ids="", group_ids=None, scheduled_at=""):
        form_data = {
            "name": name,
            "email_draft_id": str(draft_id),
            "status": status,
            "manual_recipient_ids": recipient_ids,
            "scheduled_at": scheduled_at,
        }
        if group_ids:
            form_data["recipient_group_ids"] = [str(value) for value in group_ids]
        return self.client.post("/campaigns", data=form_data)

    def test_add_recipient(self):
        response = self.client.post(
            "/recipients/new",
            data={
                "email": "casey@example.com",
                "first_name": "Casey",
                "last_name": "Morgan",
                "company": "Northwind",
                "is_active": "active",
            },
        )

        self.assertEqual(response.status_code, 303)
        recipient = self.get_recipient("casey@example.com")
        self.assertIsNotNone(recipient)
        self.assertEqual(recipient.full_name, "Casey Morgan")
        self.assertEqual(recipient.company, "Northwind")
        self.assertTrue(recipient.is_active)

    def test_recipient_detail_and_form_pages_render(self):
        recipient_id = self.create_recipient(
            "profile@example.com", first_name="Devon", last_name="Quinn"
        )

        detail_response = self.client.get(f"/recipients/{recipient_id}")
        new_form_response = self.client.get("/recipients/new")
        edit_form_response = self.client.get(f"/recipients/{recipient_id}/edit")

        self.assertEqual(detail_response.status_code, 200)
        self.assertIn(b"Devon Quinn", detail_response.data)
        self.assertIn(b"profile@example.com", detail_response.data)
        self.assertEqual(new_form_response.status_code, 200)
        self.assertEqual(edit_form_response.status_code, 200)

    def test_invalid_email_is_rejected(self):
        response = self.client.post(
            "/recipients/new", data={"email": "not-an-email"}
        )

        self.assertEqual(response.status_code, 422)
        self.assertIn(b"Enter a valid email address", response.data)
        self.assertIsNone(self.get_recipient("not-an-email"))

    def test_duplicate_email_is_case_insensitive(self):
        self.create_recipient("repeat@example.com")

        response = self.client.post(
            "/recipients/new", data={"email": "REPEAT@example.com"}
        )

        self.assertEqual(response.status_code, 409)
        self.assertIn(b"already exists", response.data)
        with self.app.app_context():
            self.assertEqual(db.session.scalar(db.select(db.func.count(Recipient.id))), 1)

    def test_edit_recipient(self):
        recipient_id = self.create_recipient("edit@example.com", first_name="Ari")

        response = self.client.post(
            f"/recipients/{recipient_id}/edit",
            data={
                "email": "ari@example.com",
                "first_name": "Ari",
                "last_name": "Chen",
                "company": "Contoso",
                "is_active": "inactive",
            },
        )

        self.assertEqual(response.status_code, 303)
        edited = self.get_recipient("ari@example.com")
        self.assertEqual(edited.full_name, "Ari Chen")
        self.assertEqual(edited.company, "Contoso")
        self.assertFalse(edited.is_active)

    def test_delete_recipient(self):
        recipient_id = self.create_recipient("delete@example.com")

        response = self.client.post(f"/recipients/{recipient_id}/delete")

        self.assertEqual(response.status_code, 303)
        self.assertIsNone(self.get_recipient("delete@example.com"))

    def test_activate_and_deactivate_recipient(self):
        recipient_id = self.create_recipient("status@example.com")

        response = self.client.post(f"/recipients/{recipient_id}/toggle-status")
        self.assertEqual(response.status_code, 303)
        self.assertFalse(self.get_recipient("status@example.com").is_active)

        response = self.client.post(f"/recipients/{recipient_id}/toggle-status")
        self.assertEqual(response.status_code, 303)
        self.assertTrue(self.get_recipient("status@example.com").is_active)

    def test_search_and_status_filter(self):
        self.create_recipient(
            "ava@example.com", first_name="Ava", last_name="Stone", is_active="active"
        )
        self.create_recipient(
            "noah@example.com", first_name="Noah", last_name="Reed", is_active="inactive"
        )

        search_response = self.client.get("/recipients?q=ava")
        inactive_response = self.client.get("/recipients?status=inactive")

        self.assertIn(b"ava@example.com", search_response.data)
        self.assertNotIn(b"noah@example.com", search_response.data)
        self.assertIn(b"noah@example.com", inactive_response.data)
        self.assertNotIn(b"ava@example.com", inactive_response.data)

    def test_csv_import_summarizes_new_duplicate_and_invalid_rows(self):
        self.create_recipient("taken@example.com")
        csv_text = (
            "email,first_name,last_name,company,status\n"
            "new@example.com,Sam,River,Northwind,active\n"
            "TAKEN@example.com,,,,active\n"
            "not-an-email,Invalid,,,active\n"
            "new@example.com,Repeated,,,active\n"
            ",Missing,,,active\n"
        )

        response = self.client.post(
            "/recipients/import",
            data={"file": (io.BytesIO(csv_text.encode()), "../../contacts.csv")},
            content_type="multipart/form-data",
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Import complete", response.data)
        self.assertIn(b"Total rows", response.data)
        self.assertIn(b"Duplicates", response.data)
        html = response.get_data(as_text=True)
        self.assertRegex(html, r'data-stat-value="import-total"[^>]*>5</div>')
        self.assertRegex(html, r'data-stat-value="imported"[^>]*>1</div>')
        self.assertRegex(html, r'data-stat-value="duplicates"[^>]*>2</div>')
        self.assertRegex(html, r'data-stat-value="invalid"[^>]*>2</div>')
        with self.app.app_context():
            recipients = db.session.scalars(db.select(Recipient)).all()
            emails = {recipient.email for recipient in recipients}
        self.assertEqual(emails, {"taken@example.com", "new@example.com"})

    def test_search_results_are_paginated(self):
        for index in range(23):
            self.create_recipient(f"person{index}@example.com")

        response = self.client.get("/recipients?page=2&per_page=10")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Showing", response.data)
        self.assertIn(b"aria-current=\"page\">2", response.data)
        self.assertEqual(response.data.count(b"recipient-status--active"), 10)

    def test_recipient_csv_upload_uses_generated_name(self):
        response = self.client.post(
            "/uploads/recipients",
            data={
                "file": (
                    io.BytesIO(b"email,name\nalex@example.com,Alex\n"),
                    "../../recipients.csv",
                )
            },
            content_type="multipart/form-data",
        )

        self.assertEqual(response.status_code, 201)
        result = response.get_json()
        self.assertEqual(result["rows_received"], 1)
        self.assertRegex(result["upload_id"], r"^recipients-[0-9a-f]{32}\.csv$")
        saved_files = list(self.upload_directory.iterdir())
        self.assertEqual(len(saved_files), 1)
        self.assertEqual(
            saved_files[0].read_bytes(), b"email,name\nalex@example.com,Alex\n"
        )

    def test_composer_renders_from_sidebar_with_empty_draft_state(self):
        response = self.client.get("/composer")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Email Composer", response.data)
        self.assertIn(b"Saved drafts", response.data)
        self.assertIn(b"No saved drafts yet", response.data)
        self.assertIn(b"Browse recipients", response.data)
        self.assertIn(b"drafts stay local until you explicitly send", response.data.lower())
        self.assertNotIn(b"Send email", response.data)

    def test_save_draft_validates_addresses_and_sanitizes_html(self):
        response = self.client.post(
            "/composer/drafts",
            data={
                "sender_identity": "support",
                "to_recipients": "Casey@Example.com",
                "cc_recipients": "sam@example.com",
                "bcc_recipients": "hidden@example.com",
                "subject": "A useful update",
                "body_html": '<p style="text-align:center" onclick="run()">Hello <strong>there</strong></p><script>alert(1)</script><a href="javascript:alert(1)">bad link</a>',
            },
        )

        self.assertEqual(response.status_code, 303)
        with self.app.app_context():
            draft = db.session.scalar(db.select(EmailDraft))
            self.assertIsNotNone(draft)
            self.assertEqual(draft.sender_identity, "support")
            self.assertEqual(draft.to_recipients, ["casey@example.com"])
            self.assertEqual(draft.cc_recipients, ["sam@example.com"])
            self.assertEqual(draft.bcc_recipients, ["hidden@example.com"])
            self.assertIn('style="text-align: center"', draft.body_html)
            self.assertNotIn("onclick", draft.body_html)
            self.assertNotIn("script", draft.body_html)
            self.assertNotIn("javascript:", draft.body_html)

    def test_composer_rejects_invalid_and_repeated_recipient_addresses(self):
        invalid = self.client.post(
            "/composer/drafts",
            data={"sender_identity": "workspace", "to_recipients": "no-at-sign"},
        )
        duplicate = self.client.post(
            "/composer/drafts",
            data={
                "sender_identity": "workspace",
                "to_recipients": "same@example.com",
                "bcc_recipients": "SAME@example.com",
            },
        )

        self.assertEqual(invalid.status_code, 422)
        self.assertIn(b"Enter valid email addresses", invalid.data)
        self.assertEqual(duplicate.status_code, 422)
        self.assertIn(b"already listed in TO", duplicate.data)
        with self.app.app_context():
            self.assertEqual(db.session.scalar(db.select(db.func.count(EmailDraft.id))), 0)

    def test_edit_saved_draft(self):
        created = self.client.post(
            "/composer/drafts",
            data={
                "sender_identity": "workspace",
                "to_recipients": "draft@example.com",
                "subject": "First",
                "body_html": "<p>Initial body</p>",
            },
        )
        draft_id = int(created.headers["Location"].rsplit("/", 1)[-1])

        edit_page = self.client.get(f"/composer/drafts/{draft_id}")
        updated = self.client.post(
            f"/composer/drafts/{draft_id}",
            data={
                "sender_identity": "personal",
                "to_recipients": "editor@example.com",
                "subject": "Second",
                "body_html": "<p>Revised body</p>",
            },
        )

        self.assertEqual(edit_page.status_code, 200)
        self.assertIn(b"First", edit_page.data)
        self.assertIn(b"Delete draft", edit_page.data)
        self.assertEqual(updated.status_code, 303)
        with self.app.app_context():
            draft = db.session.get(EmailDraft, draft_id)
            self.assertEqual(draft.subject, "Second")
            self.assertEqual(draft.sender_identity, "personal")
            self.assertEqual(draft.to_recipients, ["editor@example.com"])
            self.assertEqual(draft.body_html, "<p>Revised body</p>")

    def test_contact_picker_returns_only_active_matching_recipients(self):
        self.create_recipient(
            "active.person@example.com",
            first_name="Active",
            last_name="Person",
            company="Northwind",
        )
        self.create_recipient(
            "inactive.person@example.com",
            first_name="Inactive",
            last_name="Person",
            is_active="inactive",
        )
        self.create_recipient(
            "another.active@example.com",
            first_name="Another",
            last_name="Active",
            company="Northwind",
        )

        response = self.client.get("/composer/api/recipients?q=Northwind")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.get_json()["recipients"],
            [
                {
                    "id": 3,
                    "name": "Another Active",
                    "email": "another.active@example.com",
                    "company": "Northwind",
                },
                {
                    "id": 1,
                    "name": "Active Person",
                    "email": "active.person@example.com",
                    "company": "Northwind",
                },
            ],
        )

    def test_draft_requires_recipient_subject_and_message_text(self):
        response = self.client.post(
            "/composer/drafts",
            data={
                "sender_identity": "workspace",
                "to_recipients": "valid@example.com",
                "subject": "  ",
                "body_html": "<script>not content</script><p> </p>",
            },
        )

        self.assertEqual(response.status_code, 422)
        self.assertIn(b"Subject is required", response.data)
        self.assertIn(b"Add some message text", response.data)
        with self.app.app_context():
            self.assertEqual(db.session.scalar(db.select(db.func.count(EmailDraft.id))), 0)

    def test_draft_requires_at_least_one_recipient(self):
        response = self.client.post(
            "/composer/drafts",
            data={
                "sender_identity": "workspace",
                "subject": "Valid subject",
                "body_html": "<p>Valid message</p>",
            },
        )

        self.assertEqual(response.status_code, 422)
        self.assertIn(b"Add at least one recipient", response.data)

    def test_complete_draft_lifecycle_uses_saved_active_contacts(self):
        first_id = self.create_recipient(
            "first.active@example.com", first_name="First", is_active="active"
        )
        second_id = self.create_recipient(
            "second.active@example.com", first_name="Second", is_active="active"
        )
        first_contact = self.client.get("/composer/api/recipients?q=first").get_json()["recipients"][0]
        second_contact = self.client.get("/composer/api/recipients?q=second").get_json()["recipients"][0]
        self.assertEqual({first_contact["id"], second_contact["id"]}, {first_id, second_id})

        saved = self.client.post(
            "/composer/drafts",
            data={
                "sender_identity": "workspace",
                "to_recipients": f"{first_contact['email']}, {second_contact['email']}",
                "subject": "Two contact update",
                "body_html": "<p>Hello both of you.</p>",
            },
        )
        draft_id = int(saved.headers["Location"].rsplit("/", 1)[-1])
        reopened = self.client.get(f"/composer/drafts/{draft_id}")
        edited = self.client.post(
            f"/composer/drafts/{draft_id}",
            data={
                "sender_identity": "personal",
                "to_recipients": f"{first_contact['email']}",
                "cc_recipients": f"{second_contact['email']}",
                "subject": "Updated contact update",
                "body_html": "<p><strong>Updated</strong> message.</p>",
            },
        )
        deleted = self.client.post(f"/composer/drafts/{draft_id}/delete")

        self.assertEqual(saved.status_code, 303)
        self.assertEqual(reopened.status_code, 200)
        self.assertIn(b"first.active@example.com", reopened.data)
        self.assertIn(b"second.active@example.com", reopened.data)
        self.assertEqual(edited.status_code, 303)
        self.assertEqual(deleted.status_code, 303)
        with self.app.app_context():
            self.assertIsNone(db.session.get(EmailDraft, draft_id))

    def test_delete_draft_removes_it_and_returns_to_draft_list(self):
        created = self.client.post(
            "/composer/drafts",
            data={
                "sender_identity": "workspace",
                "to_recipients": "delete@example.com",
                "subject": "Delete me",
                "body_html": "<p>Disposable test draft</p>",
            },
        )
        draft_id = int(created.headers["Location"].rsplit("/", 1)[-1])

        deleted = self.client.post(f"/composer/drafts/{draft_id}/delete")
        reopened = self.client.get(f"/composer/drafts/{draft_id}")
        listing = self.client.get("/composer")

        self.assertEqual(deleted.status_code, 303)
        self.assertIn("/composer", deleted.headers["Location"])
        self.assertEqual(reopened.status_code, 404)
        self.assertIn(b"No saved drafts yet", listing.data)
        with self.app.app_context():
            self.assertIsNone(db.session.get(EmailDraft, draft_id))

    def test_campaigns_render_sidebar_and_group_page(self):
        index = self.client.get("/campaigns")
        group_page = self.client.get("/campaigns/groups")
        new_page = self.client.get("/campaigns/new")

        self.assertEqual(index.status_code, 200)
        self.assertEqual(group_page.status_code, 200)
        self.assertEqual(new_page.status_code, 200)
        self.assertIn(b"campaign library", index.data.lower())
        self.assertIn(b"New campaign", index.data)
        self.assertIn(b"Recipient groups", group_page.data)
        self.assertIn(b"Choose a saved email draft", new_page.data)
        self.assertIn(b"Campaigns</span>", new_page.data)

    def test_recipient_group_crud_and_active_member_validation(self):
        active_id = self.create_recipient("group.active@example.com", first_name="Group")
        inactive_id = self.create_recipient(
            "group.inactive@example.com", first_name="Inactive", is_active="inactive"
        )

        invalid = self.client.post(
            "/campaigns/groups",
            data={
                "name": "Only active members",
                "recipient_ids": f"{active_id},{inactive_id}",
            },
        )
        created = self.client.post(
            "/campaigns/groups",
            data={"name": "Product team", "recipient_ids": str(active_id)},
        )
        with self.app.app_context():
            group_id = db.session.scalar(
                db.select(RecipientGroup.id).where(RecipientGroup.name == "Product team")
            )
        edited = self.client.post(
            f"/campaigns/groups/{group_id}/edit",
            data={"name": "Product team", "recipient_ids": str(active_id)},
        )
        listing = self.client.get("/campaigns/groups")
        deleted = self.client.post(f"/campaigns/groups/{group_id}/delete")

        self.assertEqual(invalid.status_code, 422)
        self.assertIn(b"active saved recipients only", invalid.data)
        self.assertEqual(created.status_code, 303)
        self.assertEqual(edited.status_code, 303)
        self.assertIn(b"Product team", listing.data)
        self.assertEqual(deleted.status_code, 303)
        with self.app.app_context():
            self.assertIsNone(db.session.get(RecipientGroup, group_id))

    def test_campaign_create_schedule_detail_edit_and_delete(self):
        first_id = self.create_recipient("campaign.one@example.com", first_name="Avery")
        second_id = self.create_recipient("campaign.two@example.com", first_name="Jordan")
        group_id = self.create_group("Launch audience", [first_id, second_id])
        draft_id = self.create_email_draft("Launch note", "<p>Launch details</p>")
        scheduled_at = (datetime.now() + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M")

        created = self.create_campaign(
            name="Spring launch",
            draft_id=draft_id,
            status="scheduled",
            recipient_ids=str(first_id),
            group_ids=[group_id],
            scheduled_at=scheduled_at,
        )
        campaign_id = int(created.headers["Location"].rsplit("/", 1)[-1])
        details = self.client.get(f"/campaigns/{campaign_id}")
        filtered = self.client.get("/campaigns?q=Spring&status=scheduled")
        edited = self.client.post(
            f"/campaigns/{campaign_id}/edit",
            data={
                "name": "Spring launch updated",
                "email_draft_id": str(draft_id),
                "status": "ready",
                "manual_recipient_ids": str(second_id),
            },
        )
        updated_details = self.client.get(f"/campaigns/{campaign_id}")
        deleted = self.client.post(f"/campaigns/{campaign_id}/delete")

        self.assertEqual(created.status_code, 303)
        self.assertEqual(details.status_code, 200)
        self.assertIn(b"Launch note", details.data)
        self.assertIn(b"Avery", details.data)
        self.assertIn(b"campaign.two@example.com", details.data)
        self.assertIn(b"2</dd>", details.data)
        self.assertIn(b"Spring launch", filtered.data)
        self.assertEqual(edited.status_code, 303)
        self.assertIn(b"Spring launch updated", updated_details.data)
        self.assertIn(b"Ready", updated_details.data)
        self.assertNotIn(b"Scheduled</span>", updated_details.data)
        self.assertEqual(deleted.status_code, 303)
        self.assertEqual(self.client.get(f"/campaigns/{campaign_id}").status_code, 404)
        with self.app.app_context():
            self.assertIsNone(db.session.get(Campaign, campaign_id))

    def test_campaign_validation_rejects_missing_content_audience_and_schedule(self):
        response = self.client.post(
            "/campaigns",
            data={"name": "Incomplete", "status": "scheduled", "scheduled_at": ""},
        )

        self.assertEqual(response.status_code, 422)
        self.assertIn(b"Choose a saved email draft", response.data)
        self.assertIn(b"Select at least one active recipient", response.data)
        self.assertIn(b"Choose a date and time", response.data)

        recipient_id = self.create_recipient("schedule.validation@example.com")
        draft_id = self.create_email_draft("Schedule validation")
        past_schedule = self.create_campaign(
            name="Past schedule",
            draft_id=draft_id,
            status="scheduled",
            recipient_ids=str(recipient_id),
            scheduled_at=(datetime.now() - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M"),
        )
        self.assertEqual(past_schedule.status_code, 422)
        self.assertIn(b"Choose a future date and time", past_schedule.data)

    def test_campaign_search_filter_and_pagination(self):
        recipient_id = self.create_recipient("list@example.com")
        draft_id = self.create_email_draft("List subject")
        for index in range(11):
            response = self.create_campaign(
                name=f"Weekly launch {index}",
                draft_id=draft_id,
                recipient_ids=str(recipient_id),
            )
            self.assertEqual(response.status_code, 303)
        ready = self.create_campaign(
            name="Different state",
            draft_id=draft_id,
            status="ready",
            recipient_ids=str(recipient_id),
        )
        page = self.client.get("/campaigns?q=Weekly&status=draft&page=2&per_page=10")
        excluded = self.client.get("/campaigns?status=scheduled")

        self.assertEqual(ready.status_code, 303)
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"Page 2 of 2", page.data)
        self.assertIn(b"Weekly launch 0", page.data)
        self.assertNotIn(b"Different state", page.data)
        self.assertIn(b"No campaigns found", excluded.data)

    def test_dashboard_reports_real_campaign_counts_and_empty_smtp_metrics(self):
        recipient_id = self.create_recipient("dashboard.campaign@example.com")
        draft_id = self.create_email_draft("Dashboard note")
        self.create_campaign(name="Dashboard draft", draft_id=draft_id, recipient_ids=str(recipient_id))
        scheduled_at = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M")
        self.create_campaign(
            name="Dashboard scheduled",
            draft_id=draft_id,
            status="scheduled",
            recipient_ids=str(recipient_id),
            scheduled_at=scheduled_at,
        )
        self.create_campaign(name="Dashboard ready", draft_id=draft_id, status="ready", recipient_ids=str(recipient_id))
        self.create_campaign(name="Dashboard complete", draft_id=draft_id, status="completed", recipient_ids=str(recipient_id))

        response = self.client.get("/api/dashboard")
        result = response.get_json()

        self.assertEqual(result["stats"]["campaigns_total"]["value"], 4)
        self.assertEqual(result["stats"]["campaigns_draft"]["value"], 1)
        self.assertEqual(result["stats"]["campaigns_scheduled"]["value"], 1)
        self.assertEqual(result["stats"]["campaigns_ready"]["value"], 1)
        self.assertEqual(result["stats"]["campaigns_completed"]["value"], 1)
        self.assertEqual(len(result["recent_campaigns"]), 4)
        self.assertEqual(result["recent_campaigns"][0]["recipient_count"], 1)
        self.assertEqual(result["stats"]["emails_sent"]["value"], 0)
        self.assertEqual(result["stats"]["successful_emails"]["value"], 0)
        self.assertEqual(result["stats"]["failed_emails"]["value"], 0)

    def test_analytics_dashboard_reports_database_counts_and_empty_states(self):
        response = self.client.get("/analytics")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Campaigns created", response.data)
        self.assertIn(b"Recorded application outcomes only", response.data)
        self.assertIn(b"No campaigns were created", response.data)
        self.assertIn(b"SMTP accepted", response.data)
        self.assertIn(b"No SMTP sending outcomes", response.data)
        self.assertIn(b"aria-current=\"page\"", response.data)
        self.assertNotIn(b"Emails sent", response.data)

    def test_analytics_summary_status_distribution_and_recipient_counts_are_real(self):
        active_id = self.create_recipient("analytics.active@example.com")
        self.create_recipient("analytics.inactive@example.com", is_active="inactive")
        draft_id = self.create_email_draft("Analytics source")
        for status in ("draft", "scheduled", "ready", "completed"):
            scheduled_at = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M") if status == "scheduled" else ""
            created = self.create_campaign(
                name=f"Analytics {status}",
                draft_id=draft_id,
                status=status,
                recipient_ids=str(active_id),
                scheduled_at=scheduled_at,
            )
            self.assertEqual(created.status_code, 303)

        response = self.client.get("/analytics?range=30d")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"All campaigns</span><strong>4", response.data)
        self.assertIn(b"Draft campaigns</span><strong>1", response.data)
        self.assertIn(b"Scheduled campaigns</span><strong>1", response.data)
        self.assertIn(b"Ready campaigns</span><strong>1", response.data)
        self.assertIn(b"Completed campaigns</span><strong>1", response.data)
        self.assertIn(b"Total recipients</span><strong>2", response.data)
        self.assertIn(b"Active contacts</span><strong>1", response.data)
        self.assertIn(b"Inactive contacts</span><strong>1", response.data)
        self.assertIn(b"Status distribution", response.data)
        self.assertIn(b"saved in snapshot", response.data)
        self.assertNotIn(b"Delivered", response.data)

    def test_analytics_date_ranges_filter_trends_and_reports_but_keep_current_totals(self):
        recipient_id = self.create_recipient("analytics.range@example.com")
        old_recipient_id = self.create_recipient("analytics.old@example.com")
        draft_id = self.create_email_draft("Range report")
        recent = self.create_campaign(
            name="Recent campaign", draft_id=draft_id, recipient_ids=str(recipient_id)
        )
        older = self.create_campaign(
            name="Older campaign", draft_id=draft_id, status="completed", recipient_ids=str(recipient_id)
        )
        old_date = datetime.now() - timedelta(days=45)
        with self.app.app_context():
            recent_campaign_id = int(recent.headers["Location"].rsplit("/", 1)[-1])
            older_campaign_id = int(older.headers["Location"].rsplit("/", 1)[-1])
            db.session.get(Campaign, older_campaign_id).created_at = old_date
            db.session.get(Recipient, old_recipient_id).created_at = old_date
            db.session.commit()

        short_range = self.client.get("/analytics?range=30d")
        week_range = self.client.get("/analytics?range=7d")
        long_range = self.client.get("/analytics?range=90d")
        all_time = self.client.get("/analytics?range=all")

        self.assertEqual(short_range.status_code, 200)
        self.assertEqual(week_range.status_code, 200)
        self.assertEqual(long_range.status_code, 200)
        self.assertEqual(all_time.status_code, 200)
        self.assertIn(b"Recent campaign", short_range.data)
        self.assertNotIn(b"Older campaign", short_range.data)
        self.assertIn(b"Recent campaign", week_range.data)
        self.assertNotIn(b"Older campaign", week_range.data)
        self.assertIn(b"Older campaign", long_range.data)
        self.assertIn(b"Older campaign", all_time.data)
        for response in (short_range, long_range, all_time):
            self.assertIn(b"All campaigns</span><strong>2", response.data)
            self.assertIn(b"Total recipients</span><strong>2", response.data)
        self.assertIn(b"Cumulative saved contacts over time", short_range.data)
        self.assertIn(b'"value": 1', short_range.data)
        self.assertIn(b'"value": 2', long_range.data)
        with self.app.app_context():
            weekly_data = get_analytics_data("7d")
            monthly_data = get_analytics_data("30d")
            quarterly_data = get_analytics_data("90d")
            self.assertEqual(sum(point["value"] for point in weekly_data["campaign_activity"]), 1)
            self.assertEqual(sum(point["value"] for point in monthly_data["campaign_activity"]), 1)
            self.assertEqual(sum(point["value"] for point in quarterly_data["campaign_activity"]), 2)
            self.assertEqual(monthly_data["recipient_growth"][0]["value"], 1)
            self.assertEqual(monthly_data["recipient_growth"][-1]["value"], 2)
            monthly_statuses = {item["key"]: item["value"] for item in monthly_data["status_distribution"]}
            self.assertEqual(monthly_statuses["draft"], 1)
            self.assertEqual(monthly_statuses["completed"], 0)
            quarterly_statuses = {item["key"]: item["value"] for item in quarterly_data["status_distribution"]}
            self.assertEqual(quarterly_statuses["completed"], 1)

    def test_analytics_report_paginates_and_csv_exports_safe_database_metrics(self):
        recipient_id = self.create_recipient("analytics.csv@example.com")
        draft_id = self.create_email_draft("CSV report")
        for index in range(11):
            created = self.create_campaign(
                name="=HYPERLINK(\"https://example.invalid\",\"Click\")" if index == 0 else f"CSV campaign {index}",
                draft_id=draft_id,
                recipient_ids=str(recipient_id),
            )
            self.assertEqual(created.status_code, 303)

        page_two = self.client.get("/analytics?range=all&page=2&per_page=10")
        export = self.client.get("/analytics/export.csv?range=all")
        exported_text = export.get_data(as_text=True)

        self.assertEqual(page_two.status_code, 200)
        self.assertIn(b"Page 2 of 2", page_two.data)
        self.assertEqual(export.status_code, 200)
        self.assertTrue(export.mimetype.startswith("text/csv"))
        self.assertIn("attachment; filename=\"mailpilot-analytics-all.csv\"", export.headers["Content-Disposition"])
        self.assertIn("'=HYPERLINK", exported_text)
        self.assertIn("recipient count", exported_text)
        self.assertIn("11", exported_text)
        self.assertIn("delivery confirmation", exported_text)
        self.assertIn("SMTP acceptance does not confirm inbox delivery", exported_text)
        lowered = exported_text.casefold()
        self.assertNotIn("emails sent", lowered)
        self.assertIn("opens and clicks are not tracked", lowered)

    def test_deleting_selected_email_draft_preserves_campaign_snapshot(self):
        recipient_id = self.create_recipient("snapshot@example.com")
        draft_id = self.create_email_draft("Snapshot source", "<p>Keep this content</p>")
        created = self.create_campaign(
            name="Draft source snapshot", draft_id=draft_id, recipient_ids=str(recipient_id)
        )
        campaign_id = int(created.headers["Location"].rsplit("/", 1)[-1])

        deleted = self.client.post(f"/composer/drafts/{draft_id}/delete")
        details = self.client.get(f"/campaigns/{campaign_id}")

        self.assertEqual(deleted.status_code, 303)
        self.assertEqual(details.status_code, 200)
        self.assertIn(b"Snapshot source", details.data)
        self.assertIn(b"Keep this content", details.data)
        with self.app.app_context():
            campaign = db.session.get(Campaign, campaign_id)
            self.assertIsNone(campaign.email_draft_id)


class RecipientCSRFSecurityTests(unittest.TestCase):
    def test_recipient_mutations_require_a_csrf_token(self):
        temporary_directory = tempfile.TemporaryDirectory()
        database_path = Path(temporary_directory.name) / "csrf-test.sqlite"
        app = create_app(
            {
                "TESTING": True,
                "SECRET_KEY": "mailpilot-csrf-test-secret",
                "WTF_CSRF_ENABLED": True,
                "AUTH_DISABLED": True,
                "SQLALCHEMY_DATABASE_URI": f"sqlite:///{database_path.as_posix()}",
                "UPLOAD_FOLDER": str(Path(temporary_directory.name) / "uploads"),
            }
        )
        try:
            client = app.test_client()
            response = client.post(
                "/recipients/new", data={"email": "csrf@example.com"}
            )
            self.assertEqual(response.status_code, 400)
            self.assertIn(b"CSRF", response.data)

            composer_response = client.post(
                "/composer/drafts",
                data={"sender_identity": "workspace", "subject": "Blocked without token"},
            )
            self.assertEqual(composer_response.status_code, 400)
            self.assertIn(b"CSRF", composer_response.data)

            campaign_response = client.post("/campaigns", data={"name": "No token"})
            self.assertEqual(campaign_response.status_code, 400)
            self.assertIn(b"CSRF", campaign_response.data)

            group_response = client.post("/campaigns/groups", data={"name": "No token"})
            self.assertEqual(group_response.status_code, 400)
            self.assertIn(b"CSRF", group_response.data)

            campaign_delete_response = client.post("/campaigns/1/delete")
            self.assertEqual(campaign_delete_response.status_code, 400)
            group_delete_response = client.post("/campaigns/groups/1/delete")
            self.assertEqual(group_delete_response.status_code, 400)

            delete_response = client.post("/composer/drafts/1/delete")
            self.assertEqual(delete_response.status_code, 400)
            self.assertIn(b"CSRF", delete_response.data)

            api_response = client.post(
                "/uploads/recipients",
                data={
                    "file": (
                        io.BytesIO(b"email\napi@example.com\n"),
                        "api.csv",
                    )
                },
                content_type="multipart/form-data",
            )
            self.assertEqual(api_response.status_code, 400)

            import_page = client.get("/recipients/import")
            self.assertEqual(import_page.status_code, 200)
            csrf_match = re.search(rb'name="csrf_token" value="([^"]+)"', import_page.data)
            self.assertIsNotNone(csrf_match)
            token = csrf_match.group(1).decode("ascii")
            protected_api_response = client.post(
                "/uploads/recipients",
                data={
                    "csrf_token": token,
                    "file": (
                        io.BytesIO(b"email\napi@example.com\n"),
                        "api.csv",
                    ),
                },
                content_type="multipart/form-data",
            )
            self.assertEqual(protected_api_response.status_code, 201)
        finally:
            with app.app_context():
                db.session.remove()
                db.engine.dispose()
            temporary_directory.cleanup()


class SettingsCSRFSecurityTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        self.app = create_app(
            {
                "TESTING": True,
                "SECRET_KEY": "mailpilot-settings-csrf-secret",
                "WTF_CSRF_ENABLED": True,
                "AUTH_DISABLED": True,
                "SQLALCHEMY_DATABASE_URI": f"sqlite:///{(root / 'settings-csrf.sqlite').as_posix()}",
                "UPLOAD_FOLDER": str(root / "uploads"),
            }
        )
        self.client = self.app.test_client()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.engine.dispose()
        self.temporary_directory.cleanup()

    def get_csrf_token(self):
        response = self.client.get("/settings")
        self.assertEqual(response.status_code, 200)
        match = re.search(rb'name="csrf_token" value="([^"]+)"', response.data)
        self.assertIsNotNone(match)
        return match.group(1).decode("ascii")

    def test_settings_mutations_require_csrf_token(self):
        self.get_csrf_token()

        update = self.client.post("/settings", data={"workspace_name": "Denied"})
        theme = self.client.post("/settings/theme", json={"theme": "dark"})
        reset = self.client.post(
            "/settings/reset", data={"confirmation": "DELETE ALL DATA"}
        )

        self.assertEqual(update.status_code, 400)
        self.assertEqual(theme.status_code, 400)
        self.assertEqual(reset.status_code, 400)

    def test_settings_save_theme_and_reset_accept_valid_csrf_token(self):
        token = self.get_csrf_token()
        headers = {"X-CSRFToken": token}
        values = {
            "csrf_token": token,
            "workspace_name": "CSRF protected workspace",
            "workspace_description": "Validated settings.",
            "timezone": "UTC",
            "date_format": "yyyy-mm-dd",
            "time_format": "24h",
            "theme_preference": "dark",
        }

        updated = self.client.post("/settings", data=values)
        theme = self.client.post("/settings/theme", json={"theme": "light"}, headers=headers)
        reset = self.client.post(
            "/settings/reset",
            data={"csrf_token": token, "confirmation": "DELETE ALL DATA"},
        )

        self.assertEqual(updated.status_code, 303)
        self.assertEqual(theme.status_code, 200)
        self.assertEqual(reset.status_code, 303)
        with self.app.app_context():
            preferences = db.session.get(WorkspaceSettings, 1)
            self.assertEqual(preferences.workspace_name, "CSRF protected workspace")
            self.assertEqual(preferences.theme_preference, "light")


class SecurityNotificationTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        self.app = create_app(
            {
                "TESTING": True,
                "SECRET_KEY": "mailpilot-security-notification-test-secret",
                "WTF_CSRF_ENABLED": False,
                "AUTH_DISABLED": True,
                "SESSION_COOKIE_SECURE": True,
                "SQLALCHEMY_DATABASE_URI": f"sqlite:///{(root / 'security-notifications.sqlite').as_posix()}",
                "UPLOAD_FOLDER": str(root / "uploads"),
            }
        )
        self.client = self.app.test_client()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.engine.dispose()
        self.temporary_directory.cleanup()

    def test_notification_categories_are_saved_and_displayed(self):
        with self.app.app_context():
            for category in ("success", "warning", "error", "info"):
                create_notification(category, f"A {category} message", title=f"{category.title()} update")
            create_notification("info", "Stored as text", title="<script>alert('x')</script>")
            self.assertEqual(db.session.scalar(db.select(db.func.count(Notification.id))), 5)

        page = self.client.get("/notifications")
        recent = self.client.get("/notifications/api/recent")
        self.assertEqual(page.status_code, 200)
        self.assertEqual(recent.get_json()["unread_count"], 5)
        for label in (b"Success update", b"Warning update", b"Error update", b"Info update"):
            self.assertIn(label, page.data)
        self.assertIn(b"&lt;script&gt;alert(&#39;x&#39;)&lt;/script&gt;", page.data)

    def test_notifications_can_be_marked_read_unread_deleted_and_cleared(self):
        with self.app.app_context():
            first = create_notification("success", "Read workflow")
            second = create_notification("info", "Delete workflow")
            first_id, second_id = first.id, second.id

        marked_read = self.client.post(f"/notifications/{first_id}/read")
        self.assertEqual(marked_read.status_code, 303)
        self.assertEqual(self.client.get("/notifications/api/recent").get_json()["unread_count"], 1)
        marked_unread = self.client.post(f"/notifications/{first_id}/unread")
        self.assertEqual(marked_unread.status_code, 303)
        deleted = self.client.post(f"/notifications/{second_id}/delete")
        self.assertEqual(deleted.status_code, 303)
        with self.app.app_context():
            self.assertIsNone(db.session.get(Notification, second_id))
            self.assertEqual(db.session.get(Notification, first_id).is_read, False)
            create_notification("warning", "Clear history")

        cleared = self.client.post("/notifications/clear")
        self.assertEqual(cleared.status_code, 303)
        with self.app.app_context():
            self.assertEqual(db.session.scalar(db.select(db.func.count(Notification.id))), 0)

    def test_notification_preferences_persist_and_suppress_disabled_categories(self):
        response = self.client.post(
            "/settings",
            data={
                "workspace_name": "Preference workspace",
                "timezone": "UTC",
                "date_format": "yyyy-mm-dd",
                "time_format": "24h",
                "theme_preference": "system",
                "notification_preferences_submitted": "1",
                "warning_enabled": "1",
                "info_enabled": "1",
            },
        )
        self.assertEqual(response.status_code, 303)
        with self.app.app_context():
            preferences = db.session.get(NotificationPreference, 1)
            self.assertFalse(preferences.success_enabled)
            self.assertTrue(preferences.warning_enabled)
            self.assertFalse(preferences.error_enabled)
            self.assertTrue(preferences.info_enabled)
            self.assertIsNone(create_notification("success", "Suppressed"))
            self.assertIsNotNone(create_notification("info", "Allowed"))

        settings_page = self.client.get("/settings")
        self.assertIn(b"Notification preferences", settings_page.data)

    def test_security_page_reports_the_test_authentication_override(self):
        response = self.client.get("/security")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Authentication is disabled for this test app", response.data)
        self.assertIn(b"test configuration bypasses sign-in", response.data)
        self.assertIn(b"mailpilot_session", response.data)
        self.assertIn(b"Secure (HTTPS only)", response.data)
        cookie = response.headers.get("Set-Cookie", "")
        self.assertIn("HttpOnly", cookie)
        self.assertIn("Secure", cookie)
        self.assertIn("SameSite=Lax", cookie)

    def test_rejected_input_is_logged_and_session_clear_is_recorded(self):
        rejected = self.client.post("/recipients/new", data={"email": "invalid"})
        self.assertEqual(rejected.status_code, 422)
        with self.app.app_context():
            event = db.session.scalar(
                db.select(SecurityEvent).where(SecurityEvent.endpoint == "recipients.new_recipient")
            )
            self.assertIsNotNone(event)
            self.assertEqual(event.outcome, "rejected")
            self.assertIsNotNone(
                db.session.scalar(
                    db.select(Notification).where(Notification.category == "error")
                )
            )

        cleared = self.client.post("/security/session/clear")
        self.assertEqual(cleared.status_code, 303)
        with self.app.app_context():
            self.assertIsNotNone(
                db.session.scalar(
                    db.select(SecurityEvent).where(SecurityEvent.endpoint == "security.clear_session")
                )
            )
            self.assertIsNotNone(
                db.session.scalar(db.select(Notification).where(Notification.category == "info"))
            )


class SecurityNotificationCSRSTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        self.app = create_app(
            {
                "TESTING": True,
                "SECRET_KEY": "mailpilot-notification-csrf-secret",
                "WTF_CSRF_ENABLED": True,
                "AUTH_DISABLED": True,
                "SQLALCHEMY_DATABASE_URI": f"sqlite:///{(root / 'notification-csrf.sqlite').as_posix()}",
                "UPLOAD_FOLDER": str(root / "uploads"),
            }
        )
        self.client = self.app.test_client()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.engine.dispose()
        self.temporary_directory.cleanup()

    def test_notification_and_session_mutations_require_and_accept_csrf(self):
        page = self.client.get("/notifications")
        self.assertEqual(page.status_code, 200)
        match = re.search(rb'data-csrf-token="([^"]+)"', page.data)
        self.assertIsNotNone(match)
        token = match.group(1).decode("ascii")
        with self.app.app_context():
            notification = create_notification("info", "Protected notification")
            notification_id = notification.id

        rejected_read = self.client.post(f"/notifications/{notification_id}/read")
        rejected_clear = self.client.post("/notifications/clear")
        rejected_session = self.client.post("/security/session/clear")
        rejected_upload = self.client.post(
            "/uploads/recipients",
            data={"file": (io.BytesIO(b"email\ncsrf@example.com\n"), "csrf.csv")},
            content_type="multipart/form-data",
        )
        self.assertEqual(rejected_read.status_code, 400)
        self.assertEqual(rejected_clear.status_code, 400)
        self.assertEqual(rejected_session.status_code, 400)
        self.assertEqual(rejected_upload.status_code, 400)

        accepted_read = self.client.post(
            f"/notifications/{notification_id}/read", headers={"X-CSRFToken": token}
        )
        accepted_session = self.client.post(
            "/security/session/clear", headers={"X-CSRFToken": token}
        )
        self.assertEqual(accepted_read.status_code, 303)
        self.assertEqual(accepted_session.status_code, 303)


if __name__ == "__main__":
    unittest.main()
