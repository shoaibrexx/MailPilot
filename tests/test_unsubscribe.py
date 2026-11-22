import tempfile
import unittest
from pathlib import Path

from mailpilot import create_app
from mailpilot.extensions import db
from mailpilot.models import Campaign, Recipient, Suppression, UnsubscribeToken
from mailpilot.services.email_service import send_html_email
from mailpilot.services.unsubscribe_service import (
    add_suppression,
    apply_unsubscribe,
    create_unsubscribe_token,
    is_suppressed,
    save_postal_address,
    unsubscribe_url,
)
from unittest.mock import Mock, patch


class UnsubscribeTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        self.app = create_app({
            "TESTING": True,
            "SECRET_KEY": "unsubscribe-tests-only",
            "WTF_CSRF_ENABLED": False,
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{(root / 'mailpilot.sqlite').as_posix()}",
            "UPLOAD_FOLDER": str(root / "uploads"),
            "MAIL_SERVER": "smtp.gmail.com",
            "MAIL_PORT": 587,
            "MAIL_USERNAME": "sender@example.com",
            "MAIL_PASSWORD": "test-only-password",
            "MAIL_DEFAULT_SENDER": "sender@example.com",
            "MAILPILOT_PUBLIC_BASE_URL": "https://mail.example.test",
        })
        self.client = self.app.test_client()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.engine.dispose()
        self.temporary_directory.cleanup()

    def test_token_is_opaque_persisted_as_hash_and_get_does_not_unsubscribe_or_set_cookie(self):
        with self.app.app_context():
            raw = create_unsubscribe_token("User@Example.com", 73)
            record = db.session.scalar(db.select(UnsubscribeToken))
            self.assertNotEqual(raw, record.token_hash)
            self.assertEqual(len(record.token_hash), 64)
            self.assertEqual(record.email, "user@example.com")
        response = self.client.get(f"/unsubscribe/{raw}")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("Set-Cookie", response.headers)
        with self.app.app_context():
            self.assertFalse(is_suppressed("user@example.com", 73))
            self.assertIsNone(db.session.scalar(db.select(UnsubscribeToken.used_at)))

    def test_public_one_click_post_creates_global_suppression_idempotently(self):
        with self.app.app_context():
            raw = create_unsubscribe_token("user@example.com", 73)
        first = self.client.post(f"/unsubscribe/{raw}", data={"List-Unsubscribe": "One-Click"})
        second = self.client.post(f"/unsubscribe/{raw}", data={"List-Unsubscribe": "One-Click"})
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.mimetype, "text/plain")
        self.assertNotIn("Location", first.headers)
        self.assertNotIn("Set-Cookie", first.headers)
        self.assertEqual(second.status_code, 200)
        with self.app.app_context():
            self.assertTrue(is_suppressed("USER@example.com", 73))
            self.assertEqual(db.session.scalar(db.select(db.func.count(Suppression.id))), 1)

    def test_manual_unsubscribe_can_be_campaign_scoped(self):
        with self.app.app_context():
            raw = create_unsubscribe_token("user@example.com", 73)
        response = self.client.post(f"/unsubscribe/{raw}", data={"scope": "campaign"})
        self.assertEqual(response.status_code, 200)
        with self.app.app_context():
            self.assertTrue(is_suppressed("user@example.com", 73))
            self.assertFalse(is_suppressed("user@example.com", 74))

    def test_invalid_token_fails_without_mutation(self):
        response = self.client.post("/unsubscribe/not-a-real-token", data={"List-Unsubscribe": "One-Click"})
        self.assertEqual(response.status_code, 404)
        with self.app.app_context():
            self.assertEqual(db.session.scalar(db.select(db.func.count(Suppression.id))), 0)

    def test_url_requires_https_public_base_and_safe_token(self):
        with self.app.app_context():
            self.assertEqual(unsubscribe_url("abc_X-123"), "https://mail.example.test/unsubscribe/abc_X-123")
        with self.assertRaises(ValueError):
            unsubscribe_url("ok", config={"MAILPILOT_PUBLIC_BASE_URL": "http://mail.example.test"})
        with self.assertRaises(ValueError):
            unsubscribe_url("ok", config={"MAILPILOT_PUBLIC_BASE_URL": "https://[invalid"})
        with self.assertRaises(ValueError):
            unsubscribe_url("bad\r\nX-Header: yes")

    def test_smtp_unsubscribe_headers_are_allowlisted_and_bcc_is_not_exposed(self):
        client = Mock()
        client.send_message.return_value = {}
        with patch("mailpilot.services.email_service.smtplib.SMTP", return_value=client):
            send_html_email(
                to=[],
                bcc=["person@example.com"],
                subject="Update",
                body_html="<p>Hello</p>",
                headers={
                    "List-Unsubscribe": "<https://mail.example.test/unsubscribe/token>",
                    "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
                },
                config=self.app.config,
            )
        message = client.send_message.call_args.args[0]
        self.assertEqual(message["List-Unsubscribe-Post"], "List-Unsubscribe=One-Click")
        self.assertNotIn("Bcc", message)
        self.assertEqual(client.send_message.call_args.kwargs["to_addrs"], ["person@example.com"])

    def test_mailing_address_persists_and_suppression_is_idempotent(self):
        with self.app.app_context():
            profile = save_postal_address("101 Main Street\nSpringfield, XX 12345")
            self.assertIn("101 Main Street", profile.postal_address)
            _record, created = add_suppression("User@example.com")
            _record, duplicate = add_suppression("user@EXAMPLE.com")
            self.assertTrue(created)
            self.assertFalse(duplicate)


if __name__ == "__main__":
    unittest.main()
