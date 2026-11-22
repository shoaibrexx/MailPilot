from datetime import datetime, timezone
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from mailpilot import create_app
from mailpilot.extensions import db
from mailpilot.models import BounceEvent, BounceMailboxState, CampaignDelivery, CampaignDeliveryAttempt, CampaignJob, Suppression
from mailpilot.services.bounce_service import imap_is_configured, parse_delivery_status, poll_bounce_mailbox, process_delivery_status
from mailpilot.services.unsubscribe_service import add_suppression, is_suppressed


def dsn_message(*, status="5.1.1", action="failed"):
    return f"""From: Mail Delivery Subsystem <mailer-daemon@example.net>
MIME-Version: 1.0
Content-Type: multipart/report; report-type=delivery-status; boundary="dsn-boundary"
Subject: Delivery Status Notification

--dsn-boundary
Content-Type: text/plain; charset=utf-8

Delivery was not completed.
--dsn-boundary
Content-Type: message/delivery-status

Reporting-MTA: dns; mx.example.net

Final-Recipient: rfc822; person@example.com
Action: {action}
Status: {status}
Diagnostic-Code: smtp; 550 private server diagnostics for person@example.com
--dsn-boundary
Content-Type: message/rfc822

From: sender@example.com
To: person@example.com
Message-ID: <campaign-message@example.test>
Subject: Actual campaign content

private message body
--dsn-boundary--
""".encode("utf-8")


class BounceTrackingTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        self.app = create_app({
            "TESTING": True,
            "SECRET_KEY": "bounce-tests-only-secret",
            "WTF_CSRF_ENABLED": False,
            "AUTH_DISABLED": True,
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{(root / 'mailpilot.sqlite').as_posix()}",
            "UPLOAD_FOLDER": str(root / "uploads"),
            "MAIL_IMAP_USERNAME": "bouncebox@example.com",
            "MAIL_IMAP_PASSWORD": "imap-test-password",
            "MAIL_IMAP_SERVER": "imap.gmail.com",
            "MAIL_IMAP_PORT": 993,
        })
        with self.app.app_context():
            job = CampaignJob(
                campaign_id=999,
                job_key="bounce-test-job-key-0123456789-0123",
                kind="immediate",
                status="completed",
                consent_confirmed=True,
                snapshot={"subject": "Private subject", "body_html": "<p>Private body</p>", "recipients": ["person@example.com"]},
                scheduled_for=datetime.now(timezone.utc).replace(tzinfo=None),
                total_recipients=1,
            )
            db.session.add(job)
            db.session.flush()
            delivery = CampaignDelivery(
                job_id=job.id,
                email="person@example.com",
                status="accepted",
                attempt_count=1,
                message_id="<campaign-message@example.test>",
                accepted_at=datetime.now(timezone.utc).replace(tzinfo=None),
            )
            db.session.add(delivery)
            db.session.flush()
            db.session.add(CampaignDeliveryAttempt(
                delivery_id=delivery.id,
                attempt_number=1,
                message_id="<campaign-message@example.test>",
                status="accepted",
            ))
            db.session.commit()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.engine.dispose()
        self.temporary_directory.cleanup()

    def test_parser_accepts_real_rfc3464_permanent_dsn_and_does_not_keep_diagnostic_body(self):
        parsed = parse_delivery_status(dsn_message())
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0]["status_code"], "5.1.1")
        with self.app.app_context():
            self.assertEqual(process_delivery_status(dsn_message()), 1)
            delivery = db.session.scalar(db.select(CampaignDelivery))
            event = db.session.scalar(db.select(BounceEvent))
            self.assertEqual(delivery.status, "bounced_permanent")
            self.assertTrue(is_suppressed("person@example.com", 999))
            self.assertEqual(event.classification, "permanent")
            self.assertEqual(event.diagnostic, "5.1.1")
            self.assertNotIn("private server diagnostics", event.diagnostic)
            self.assertNotIn("Private body", str(event))
            self.assertEqual(db.session.scalar(db.select(db.func.count(Suppression.id))), 1)

    def test_temporary_failed_dsn_is_recorded_without_global_suppression(self):
        with self.app.app_context():
            self.assertEqual(process_delivery_status(dsn_message(status="4.2.0")), 1)
            delivery = db.session.scalar(db.select(CampaignDelivery))
            event = db.session.scalar(db.select(BounceEvent))
            self.assertEqual(delivery.status, "bounced_temporary")
            self.assertEqual(event.classification, "temporary")
            self.assertFalse(is_suppressed("person@example.com", 999))

    def test_duplicate_dsn_and_unmatched_or_non_dsn_message_are_ignored(self):
        with self.app.app_context():
            raw = dsn_message()
            self.assertEqual(process_delivery_status(raw), 1)
            self.assertEqual(process_delivery_status(raw), 0)
            self.assertEqual(process_delivery_status(b"Subject: plain mail\n\nnot a DSN"), 0)
            unmatched = dsn_message().replace(b"<campaign-message@example.test>", b"<unknown@example.test>")
            self.assertEqual(process_delivery_status(unmatched), 0)
            self.assertEqual(db.session.scalar(db.select(db.func.count(BounceEvent.id))), 1)

    def test_permanent_bounce_event_commits_when_suppression_already_exists(self):
        with self.app.app_context():
            add_suppression("person@example.com", scope="global", source="manual")
            self.assertEqual(process_delivery_status(dsn_message()), 1)
            db.session.rollback()
            self.assertEqual(db.session.scalar(db.select(db.func.count(BounceEvent.id))), 1)

    def test_delayed_notice_is_not_misreported_as_a_bounce(self):
        self.assertEqual(parse_delivery_status(dsn_message(status="4.2.0", action="delayed")), [])

    def test_imap_poll_uses_peek_and_persists_cursor_without_retaining_message(self):
        raw = dsn_message()
        client = Mock()
        client.login.return_value = ("OK", [b"authenticated"])
        client.select.return_value = ("OK", [b"1"])
        client.response.side_effect = lambda name: (name, [b"777" if name == "UIDVALIDITY" else b"2"])
        client.uid.side_effect = [
            ("OK", [b"1"]),
            ("OK", [(b"1 (UID 1 BODY[] {1024}", raw), b")"]),
        ]
        with patch("mailpilot.services.bounce_service.imaplib.IMAP4_SSL", return_value=client):
            with self.app.app_context():
                self.assertTrue(imap_is_configured())
                self.assertEqual(poll_bounce_mailbox(), 1)
                state = db.session.scalar(db.select(BounceMailboxState))
                event = db.session.scalar(db.select(BounceEvent))
                self.assertEqual(state.last_uid, 1)
                self.assertEqual(state.uidvalidity, "777")
                self.assertEqual(event.classification, "permanent")
                self.assertNotIn("private message body", str(event))
        self.assertEqual(client.uid.call_args_list[1].args[0], "FETCH")
        self.assertIn("BODY.PEEK[]", client.uid.call_args_list[1].args[-1])
        client.logout.assert_called_once()


if __name__ == "__main__":
    unittest.main()
