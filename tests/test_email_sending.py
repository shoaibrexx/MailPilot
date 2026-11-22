import os
import smtplib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from mailpilot import create_app
from mailpilot.config import config_from_environment
from mailpilot.extensions import db
from mailpilot.models import (
    Campaign,
    CampaignDeliveryAttempt,
    CampaignJob,
    EmailDraft,
    EmailSendHistory,
    MailingProfile,
    Recipient,
)
from mailpilot.services.composer_service import create_draft
from mailpilot.services.send_history_service import SendValidationError, send_draft
from mailpilot.services.campaign_worker_service import process_one_due_job
from mailpilot.services.email_service import (
    SMTPAuthenticationFailure,
    SMTPConfigurationError,
    SMTPConnectionFailure,
    SMTPMessageValidationError,
    send_html_email,
    test_smtp_connection,
)


class EmailSendingTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        self.app = create_app(
            {
                "TESTING": True,
                "SECRET_KEY": "smtp-test-secret",
                "WTF_CSRF_ENABLED": False,
                "AUTH_DISABLED": True,
                "SQLALCHEMY_DATABASE_URI": f"sqlite:///{(root / 'mailpilot.sqlite').as_posix()}",
                "UPLOAD_FOLDER": str(root / "uploads"),
                "MAIL_SERVER": "smtp.gmail.com",
                "MAIL_PORT": 587,
                "MAIL_USERNAME": "sender@example.com",
                "MAIL_PASSWORD": "test-only-password",
                "MAIL_DEFAULT_SENDER": "MailPilot Test <sender@example.com>",
                "MAIL_TIMEOUT": 20,
                "MAILPILOT_PUBLIC_BASE_URL": "https://mail.example.test",
            }
        )
        self.client = self.app.test_client()
        with self.app.app_context():
            db.session.add(MailingProfile(id=1, postal_address="101 Main Street\nSpringfield, XX 12345"))
            db.session.commit()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.engine.dispose()
        self.temporary_directory.cleanup()

    def _make_draft(self, *, recipient="to@example.com"):
        with self.app.app_context():
            draft = create_draft(
                {
                    "sender_identity": "workspace",
                    "to_recipients": recipient,
                    "cc_recipients": "cc@example.com",
                    "bcc_recipients": "bcc@example.com",
                    "subject": "A useful update",
                    "body_html": "<p>Hello <strong>there</strong>.</p>",
                }
            )
            return draft.id

    def _make_campaign(self, *, status="ready"):
        with self.app.app_context():
            recipient = Recipient(
                email="campaign@example.com",
                first_name="Campaign",
                last_name="Recipient",
                is_active=True,
            )
            db.session.add(recipient)
            db.session.flush()
            draft = create_draft(
                {
                    "sender_identity": "workspace",
                    "to_recipients": recipient.email,
                    "subject": "Campaign update",
                    "body_html": "<p>Campaign message.</p>",
                }
            )
            campaign = Campaign(
                name="Test campaign",
                status=status,
                email_draft_id=draft.id,
                draft_name=draft.subject,
                subject=draft.subject,
                body_html=draft.body_html,
                manual_recipient_ids=[recipient.id],
                recipient_group_ids=[],
                recipient_group_names=[],
                recipient_snapshot=[
                    {
                        "id": recipient.id,
                        "name": recipient.full_name,
                        "email": recipient.email,
                        "company": "",
                    }
                ],
            )
            db.session.add(campaign)
            db.session.commit()
            return campaign.id

    def _smtp_mock(self):
        client = Mock()
        client.send_message.return_value = {}
        return client

    def test_environment_configuration_reads_only_environment_values(self):
        with patch.dict(
            os.environ,
            {
                "MAIL_SERVER": "smtp.gmail.com",
                "MAIL_PORT": "587",
                "MAIL_USERNAME": "person@example.com",
                "MAIL_PASSWORD": "placeholder-secret",
                "MAIL_DEFAULT_SENDER": "person@example.com",
                "MAIL_TIMEOUT": "13",
            },
            clear=True,
        ):
            config = config_from_environment()
        self.assertEqual(config["MAIL_SERVER"], "smtp.gmail.com")
        self.assertEqual(config["MAIL_PORT"], 587)
        self.assertEqual(config["MAIL_USERNAME"], "person@example.com")
        self.assertEqual(config["MAIL_PASSWORD"], "placeholder-secret")
        self.assertEqual(config["MAIL_DEFAULT_SENDER"], "person@example.com")
        self.assertEqual(config["MAIL_TIMEOUT"], 13)

    def test_send_builds_multipart_message_and_keeps_bcc_out_of_headers(self):
        client = self._smtp_mock()
        with patch("mailpilot.services.email_service.smtplib.SMTP", return_value=client) as smtp:
            result = send_html_email(
                to=["to@example.com"],
                cc=["cc@example.com"],
                bcc=["bcc@example.com"],
                subject="Hello",
                body_html="<p>Hello <b>there</b>.</p>",
                config=self.app.config,
            )

        self.assertEqual((result.recipient_count, result.accepted_count, result.refused_count), (3, 3, 0))
        smtp.assert_called_once_with("smtp.gmail.com", 587, timeout=20)
        client.starttls.assert_called_once()
        client.login.assert_called_once_with("sender@example.com", "test-only-password")
        message = client.send_message.call_args.args[0]
        self.assertEqual(message["From"], "MailPilot Test <sender@example.com>")
        self.assertEqual(message["To"], "to@example.com")
        self.assertEqual(message["Cc"], "cc@example.com")
        self.assertIsNone(message.get("Bcc"))
        self.assertEqual(
            client.send_message.call_args.kwargs["to_addrs"],
            ["to@example.com", "cc@example.com", "bcc@example.com"],
        )
        self.assertIn("Hello there.", message.get_body(preferencelist=("plain",)).get_content())
        self.assertIn("<b>there</b>", message.get_body(preferencelist=("html",)).get_content())
        client.quit.assert_called_once()

    def test_smtp_authentication_failure_is_safe_and_closes_connection(self):
        client = self._smtp_mock()
        client.login.side_effect = smtplib.SMTPAuthenticationError(535, b"private server response")
        with patch("mailpilot.services.email_service.smtplib.SMTP", return_value=client):
            with self.assertRaises(SMTPAuthenticationFailure) as raised:
                test_smtp_connection(self.app.config)
        self.assertNotIn("private server response", str(raised.exception))
        client.quit.assert_called_once()
        client.send_message.assert_not_called()

    def test_smtp_connection_failure_and_missing_credentials_are_rejected(self):
        with patch("mailpilot.services.email_service.smtplib.SMTP", side_effect=OSError("private host detail")):
            with self.assertRaises(SMTPConnectionFailure) as raised:
                test_smtp_connection(self.app.config)
        self.assertNotIn("private host detail", str(raised.exception))

        missing = dict(self.app.config)
        missing["MAIL_PASSWORD"] = ""
        with patch("mailpilot.services.email_service.smtplib.SMTP") as smtp:
            with self.assertRaises(SMTPConfigurationError):
                send_html_email(
                    to=["to@example.com"],
                    subject="Hello",
                    body_html="<p>Message</p>",
                    config=missing,
                )
        smtp.assert_not_called()

    def test_invalid_addresses_are_rejected_before_smtp_connection(self):
        with patch("mailpilot.services.email_service.smtplib.SMTP") as smtp:
            with self.assertRaises(SMTPMessageValidationError):
                send_html_email(
                    to=["not-an-email"],
                    subject="Hello",
                    body_html="<p>Message</p>",
                    config=self.app.config,
                )
        smtp.assert_not_called()

    def test_smtp_test_authenticates_without_sending(self):
        client = self._smtp_mock()
        with patch("mailpilot.services.email_service.smtplib.SMTP", return_value=client):
            self.assertTrue(test_smtp_connection(self.app.config))
        client.starttls.assert_called_once()
        client.login.assert_called_once()
        client.send_message.assert_not_called()
        client.quit.assert_called_once()

    def test_settings_smtp_test_uses_connection_test_and_sends_no_message(self):
        client = self._smtp_mock()
        with patch("mailpilot.services.email_service.smtplib.SMTP", return_value=client):
            response = self.client.post("/settings/smtp/test", follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"No test email was sent", response.data)
        client.login.assert_called_once()
        client.send_message.assert_not_called()

    def test_settings_smtp_connection_test_has_a_persistent_cooldown(self):
        client = self._smtp_mock()
        with patch("mailpilot.services.email_service.smtplib.SMTP", return_value=client):
            first = self.client.post("/settings/smtp/test", follow_redirects=True)
            second = self.client.post("/settings/smtp/test", follow_redirects=True)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertIn(b"Wait", second.data)
        self.assertIn(b"before testing the SMTP connection again", second.data)
        client.login.assert_called_once()
        client.send_message.assert_not_called()

    def test_draft_send_is_idempotent_and_records_history_without_body_or_password(self):
        draft_id = self._make_draft()
        key = "send-key-0123456789-0123456789-0123456789"
        payload = {
            "sender_identity": "workspace",
            "to_recipients": "to@example.com",
            "cc_recipients": "cc@example.com",
            "bcc_recipients": "bcc@example.com",
            "subject": "A useful update",
            "body_html": "<p>Hello safely.</p>",
            "consent_confirmed": "1",
            "submission_key": key,
        }
        client = self._smtp_mock()
        with patch("mailpilot.services.email_service.smtplib.SMTP", return_value=client):
            first = self.client.post(f"/composer/drafts/{draft_id}/send", data=payload)
            duplicate = self.client.post(f"/composer/drafts/{draft_id}/send", data=payload)

        self.assertEqual(first.status_code, 303)
        self.assertEqual(duplicate.status_code, 303)
        client.send_message.assert_called_once()
        with self.app.app_context():
            history = db.session.scalar(db.select(EmailSendHistory))
            self.assertEqual(history.status, "sent")
            self.assertEqual(history.recipient_count, 3)
            self.assertEqual(history.recipient_addresses["bcc"], ["bcc@example.com"])
            self.assertEqual(history.draft_id, draft_id)
            self.assertEqual(history.subject, "A useful update")
            self.assertNotIn("Hello safely", str(history.recipient_addresses))
            self.assertNotIn("test-only-password", str(history.error_details))

    def test_send_reservation_rechecks_that_the_draft_still_exists(self):
        draft_id = self._make_draft()
        payload = {
            "sender_identity": "workspace",
            "to_recipients": "to@example.com",
            "subject": "A useful update",
            "body_html": "<p>Hello safely.</p>",
            "consent_confirmed": "1",
            "submission_key": "deleted-draft-send-0123456789-0123456789",
        }
        with self.app.app_context():
            stale_draft = db.session.get(EmailDraft, draft_id)
            db.session.delete(stale_draft)
            db.session.commit()
            with self.assertRaises(SendValidationError) as error:
                send_draft(stale_draft, payload)
            self.assertIn("removed before sending", error.exception.errors["send"])

    def test_draft_send_requires_consent_and_records_safe_authentication_failure(self):
        draft_id = self._make_draft(recipient="to@example.com")
        missing_consent = self.client.post(
            f"/composer/drafts/{draft_id}/send",
            data={
                "sender_identity": "workspace",
                "to_recipients": "to@example.com",
                "subject": "A useful update",
                "body_html": "<p>Hello.</p>",
                "submission_key": "missing-consent-key-0123456789-0123456789",
            },
        )
        self.assertEqual(missing_consent.status_code, 422)

        client = self._smtp_mock()
        client.login.side_effect = smtplib.SMTPAuthenticationError(535, b"credential detail")
        with patch("mailpilot.services.email_service.smtplib.SMTP", return_value=client):
            response = self.client.post(
                f"/composer/drafts/{draft_id}/send",
                data={
                    "sender_identity": "workspace",
                    "to_recipients": "to@example.com",
                    "subject": "A useful update",
                    "body_html": "<p>Hello.</p>",
                    "consent_confirmed": "1",
                    "submission_key": "auth-failure-key-0123456789-0123456789",
                },
            )
        self.assertEqual(response.status_code, 303)
        client.send_message.assert_not_called()
        with self.app.app_context():
            history = db.session.scalar(db.select(EmailSendHistory))
            self.assertEqual(history.status, "failed")
            self.assertNotIn("credential detail", history.error_details)
            self.assertNotIn("test-only-password", history.error_details)

    def test_campaign_send_uses_bcc_and_blocks_second_submission(self):
        campaign_id = self._make_campaign()
        payload = {
            "consent_confirmed": "1",
            "submission_key": "campaign-send-key-0123456789-0123456789",
        }
        client = self._smtp_mock()
        with patch("mailpilot.services.email_service.smtplib.SMTP", return_value=client):
            first = self.client.post(f"/campaigns/{campaign_id}/send", data=payload)
            duplicate = self.client.post(f"/campaigns/{campaign_id}/send", data=payload)
        self.assertEqual(first.status_code, 303)
        self.assertEqual(duplicate.status_code, 303)
        client.send_message.assert_not_called()
        with self.app.app_context():
            job = db.session.scalar(db.select(CampaignJob))
            self.assertEqual(job.status, "queued")
        with patch("mailpilot.services.email_service.smtplib.SMTP", return_value=client):
            with self.app.app_context():
                self.assertTrue(process_one_due_job(worker_id="email-sending-test"))
        client.send_message.assert_called_once()
        message = client.send_message.call_args.args[0]
        self.assertEqual(message["To"], "undisclosed-recipients:;")
        self.assertIsNone(message.get("Bcc"))
        self.assertEqual(client.send_message.call_args.kwargs["to_addrs"], ["campaign@example.com"])
        with self.app.app_context():
            history = db.session.scalar(db.select(EmailSendHistory))
            attempt = db.session.scalar(db.select(CampaignDeliveryAttempt))
            self.assertEqual(history.status, "sent")
            self.assertEqual(history.campaign_id, campaign_id)
            self.assertEqual(history.recipient_addresses["bcc"], ["campaign@example.com"])
            self.assertEqual(attempt.status, "accepted")

    def test_uncertain_campaign_submission_is_locked_to_prevent_duplicate_delivery(self):
        campaign_id = self._make_campaign()
        payload = {
            "consent_confirmed": "1",
            "submission_key": "uncertain-send-key-0123456789-0123456789",
        }
        client = self._smtp_mock()
        client.send_message.side_effect = OSError("connection dropped after data")
        with patch("mailpilot.services.email_service.smtplib.SMTP", return_value=client):
            self.client.post(f"/campaigns/{campaign_id}/send", data=payload)
        with patch("mailpilot.services.email_service.smtplib.SMTP", return_value=client):
            with self.app.app_context():
                self.assertTrue(process_one_due_job(worker_id="uncertain-email-sending-test"))
            
        with patch("mailpilot.services.email_service.smtplib.SMTP", return_value=client):
            retry = self.client.post(
                f"/campaigns/{campaign_id}/send",
                data={**payload, "submission_key": "another-send-key-0123456789-0123456789"},
            )
        self.assertEqual(retry.status_code, 303)
        client.send_message.assert_called_once()
        with self.app.app_context():
            history = db.session.scalar(db.select(EmailSendHistory))
            self.assertEqual(history.status, "uncertain")
            self.assertIn("uncertain", history.error_details)

    def test_uncertain_draft_submission_is_locked_to_prevent_duplicate_delivery(self):
        draft_id = self._make_draft()
        payload = {
            "sender_identity": "workspace",
            "to_recipients": "to@example.com",
            "subject": "A useful update",
            "body_html": "<p>Hello safely.</p>",
            "consent_confirmed": "1",
            "submission_key": "uncertain-draft-key-0123456789-0123456789",
        }
        client = self._smtp_mock()
        client.send_message.side_effect = OSError("connection dropped after data")
        with patch("mailpilot.services.email_service.smtplib.SMTP", return_value=client):
            self.client.post(f"/composer/drafts/{draft_id}/send", data=payload)
            retry = self.client.post(
                f"/composer/drafts/{draft_id}/send",
                data={**payload, "submission_key": "another-draft-key-0123456789-0123456789"},
            )
        self.assertEqual(retry.status_code, 303)
        client.send_message.assert_called_once()
        edit_page = self.client.get(f"/composer/drafts/{draft_id}")
        self.assertIn(b"previous send is pending, partial, or uncertain", edit_page.data)
        with self.app.app_context():
            history = db.session.scalar(db.select(EmailSendHistory))
            self.assertEqual(history.status, "uncertain")

    def test_local_daily_recipient_cap_counts_explicit_composer_sends(self):
        self.app.config["MAILPILOT_DAILY_RECIPIENT_LIMIT"] = 2
        first_draft_id = self._make_draft(recipient="one@example.com,two@example.com")
        second_draft_id = self._make_draft(recipient="three@example.com")
        first_payload = {
            "sender_identity": "workspace",
            "to_recipients": "one@example.com,two@example.com",
            "cc_recipients": "",
            "bcc_recipients": "",
            "subject": "A useful update",
            "body_html": "<p>Hello safely.</p>",
            "consent_confirmed": "1",
            "submission_key": "daily-limit-first-send-0123456789-0123456789",
        }
        second_payload = {
            **first_payload,
            "to_recipients": "three@example.com",
            "submission_key": "daily-limit-second-send-0123456-0123456789",
        }
        client = self._smtp_mock()
        with patch("mailpilot.services.email_service.smtplib.SMTP", return_value=client):
            first = self.client.post(f"/composer/drafts/{first_draft_id}/send", data=first_payload)
            second = self.client.post(f"/composer/drafts/{second_draft_id}/send", data=second_payload)
        self.assertEqual(first.status_code, 303)
        self.assertEqual(second.status_code, 422)
        self.assertIn(b"rolling 24-hour sending cap is 2", second.data)
        client.send_message.assert_called_once()
        with self.app.app_context():
            histories = db.session.scalars(db.select(EmailSendHistory)).all()
            self.assertEqual(len(histories), 1)
            self.assertEqual(histories[0].recipient_count, 2)

    def test_sending_and_smtp_test_routes_require_csrf(self):
        draft_id = self._make_draft()
        self.app.config["WTF_CSRF_ENABLED"] = True
        send_response = self.client.post(
            f"/composer/drafts/{draft_id}/send",
            data={"submission_key": "csrf-send-key-0123456789-0123456789", "consent_confirmed": "1"},
        )
        smtp_test_response = self.client.post("/settings/smtp/test")
        self.assertEqual(send_response.status_code, 400)
        self.assertEqual(smtp_test_response.status_code, 400)
        with self.app.app_context():
            self.assertEqual(db.session.scalar(db.select(db.func.count(EmailSendHistory.id))), 0)


if __name__ == "__main__":
    unittest.main()
