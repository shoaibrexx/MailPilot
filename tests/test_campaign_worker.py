from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from mailpilot import create_app
from mailpilot.extensions import db
from mailpilot.models import (
    Campaign,
    CampaignDelivery,
    CampaignDeliveryAttempt,
    CampaignJob,
    EmailDraft,
    EmailSendHistory,
    MailingProfile,
    Recipient,
    Suppression,
    WorkspaceSettings,
)
from mailpilot.services.email_service import DeliveryResult, SMTPConnectionFailure
from mailpilot.services.send_history_service import SendValidationError
from mailpilot.services.unsubscribe_service import add_suppression
from mailpilot.services.campaign_worker_service import (
    CampaignQueueError,
    _scheduled_time_utc,
    authorize_scheduled_campaign,
    process_one_due_job,
    queue_campaign_send,
)
from mailpilot.services.campaign_service import _schedule_value


class CampaignWorkerTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        self.app = create_app({
            "TESTING": True,
            "SECRET_KEY": "worker-tests-only-secret",
            "WTF_CSRF_ENABLED": False,
            "AUTH_DISABLED": True,
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
        with self.app.app_context():
            db.session.add(MailingProfile(id=1, postal_address="101 Main Street\nSpringfield, XX 12345"))
            db.session.commit()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.engine.dispose()
        self.temporary_directory.cleanup()

    def _make_campaign(self, *, status="ready", email="person@example.com", schedule=None):
        with self.app.app_context():
            recipient = Recipient(email=email, first_name="Test", last_name="Recipient", is_active=True)
            db.session.add(recipient)
            db.session.flush()
            draft = EmailDraft(
                sender_identity="workspace",
                to_recipients=[email],
                cc_recipients=[],
                bcc_recipients=[],
                subject="A useful campaign",
                body_html="<p>Hello from the campaign.</p>",
            )
            db.session.add(draft)
            db.session.flush()
            campaign = Campaign(
                name="Worker campaign",
                status=status,
                email_draft_id=draft.id,
                draft_name=draft.subject,
                subject=draft.subject,
                body_html=draft.body_html,
                manual_recipient_ids=[recipient.id],
                recipient_group_ids=[],
                recipient_group_names=[],
                recipient_snapshot=[{"id": recipient.id, "name": recipient.full_name, "email": recipient.email, "company": ""}],
                scheduled_at=schedule,
            )
            db.session.add(campaign)
            db.session.commit()
            return campaign.id

    def _payload(self, key="send-key-0123456789-0123456789-0123456789"):
        return {"submission_key": key, "consent_confirmed": "1"}

    def _accepted(self, *args, **kwargs):
        return DeliveryResult(1, 1, 0, "<worker-message@example.test>", {})

    def _process(self, **kwargs):
        with self.app.app_context():
            return process_one_due_job(**kwargs)

    def test_campaign_post_queues_without_smtp_then_worker_sends_per_recipient_and_records_acceptance(self):
        campaign_id = self._make_campaign()
        payload = self._payload()
        with patch("mailpilot.services.email_service.smtplib.SMTP") as smtp:
            response = self.client.post(f"/campaigns/{campaign_id}/send", data=payload)
            smtp.assert_not_called()
        self.assertEqual(response.status_code, 303)
        with self.app.app_context():
            job = db.session.scalar(db.select(CampaignJob))
            self.assertEqual(job.status, "queued")
            self.assertEqual(job.total_recipients, 1)
            self.assertTrue(job.consent_confirmed)
            self.assertIn("Hello from the campaign", job.snapshot["body_html"])

        smtp_client = Mock()
        smtp_client.send_message.return_value = {}
        with patch("mailpilot.services.email_service.smtplib.SMTP", return_value=smtp_client):
            self.assertTrue(self._process(worker_id="worker-test"))
        with self.app.app_context():
            job = db.session.scalar(db.select(CampaignJob))
            delivery = db.session.scalar(db.select(CampaignDelivery))
            history = db.session.scalar(db.select(EmailSendHistory))
            self.assertEqual(job.status, "completed")
            self.assertEqual(delivery.status, "accepted")
            self.assertEqual(delivery.message_id, smtp_client.send_message.call_args.args[0]["Message-ID"])
            self.assertEqual(history.status, "sent")
            message = smtp_client.send_message.call_args.args[0]
            self.assertIn("List-Unsubscribe", message)
            self.assertIn("Unsubscribe from these emails", message.get_body(preferencelist=("html",)).get_content())
            self.assertNotIn("Bcc", message)
            self.assertEqual(smtp_client.send_message.call_args.kwargs["to_addrs"], ["person@example.com"])

    def test_duplicate_post_and_second_worker_run_do_not_duplicate_campaign_execution(self):
        campaign_id = self._make_campaign()
        payload = self._payload()
        self.client.post(f"/campaigns/{campaign_id}/send", data=payload)
        self.client.post(f"/campaigns/{campaign_id}/send", data=payload)
        with patch("mailpilot.services.campaign_worker_service.send_html_email", side_effect=self._accepted) as sender:
            self.assertTrue(self._process(worker_id="worker-one"))
            self.assertFalse(self._process(worker_id="worker-two"))
        self.assertEqual(sender.call_count, 1)
        with self.app.app_context():
            self.assertEqual(db.session.scalar(db.select(db.func.count(CampaignJob.id))), 1)
            self.assertEqual(db.session.scalar(db.select(db.func.count(EmailSendHistory.id))), 1)

    def test_two_workers_cannot_claim_and_send_the_same_job(self):
        campaign_id = self._make_campaign()
        self.client.post(f"/campaigns/{campaign_id}/send", data=self._payload())
        start = threading.Barrier(2)
        results = []

        def send_with_overlap(*_args, **_kwargs):
            time.sleep(0.1)
            return self._accepted()

        def run_worker(worker_id):
            with self.app.app_context():
                start.wait(timeout=5)
                results.append(process_one_due_job(worker_id=worker_id))

        with patch("mailpilot.services.campaign_worker_service.send_html_email", side_effect=send_with_overlap) as sender:
            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = [executor.submit(run_worker, "worker-a"), executor.submit(run_worker, "worker-b")]
                for future in futures:
                    future.result(timeout=10)
        self.assertEqual(sorted(results), [False, True])
        self.assertEqual(sender.call_count, 1)
        with self.app.app_context():
            self.assertEqual(db.session.scalar(db.select(db.func.count(CampaignDeliveryAttempt.id))), 1)
            self.assertEqual(db.session.scalar(db.select(CampaignDelivery.status)), "accepted")

    def test_stale_job_before_smtp_resumes_after_worker_restart(self):
        campaign_id = self._make_campaign()
        self.client.post(f"/campaigns/{campaign_id}/send", data=self._payload())
        with self.app.app_context():
            job = db.session.scalar(db.select(CampaignJob))
            job.status = "running"
            job.lease_owner = "stopped-worker"
            job.lease_expires_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=1)
            db.session.commit()
        with patch("mailpilot.services.campaign_worker_service.send_html_email", side_effect=self._accepted) as sender:
            self.assertTrue(self._process(worker_id="restarted-worker"))
        self.assertEqual(sender.call_count, 1)
        with self.app.app_context():
            self.assertEqual(db.session.scalar(db.select(CampaignJob.status)), "completed")
            self.assertEqual(db.session.scalar(db.select(CampaignDelivery.status)), "accepted")

    def test_saving_a_draft_never_creates_or_runs_a_campaign_job(self):
        with self.app.app_context():
            db.session.add(EmailDraft(
                sender_identity="workspace",
                to_recipients=["person@example.com"],
                cc_recipients=[],
                bcc_recipients=[],
                subject="Draft only",
                body_html="<p>Not automatically sent.</p>",
            ))
            db.session.commit()
        with patch("mailpilot.services.campaign_worker_service.send_html_email") as sender:
            self.assertFalse(self._process(worker_id="draft-only-worker"))
        sender.assert_not_called()
        with self.app.app_context():
            self.assertEqual(db.session.scalar(db.select(db.func.count(CampaignJob.id))), 0)
            self.assertEqual(db.session.scalar(db.select(db.func.count(CampaignDeliveryAttempt.id))), 0)

    def test_suppression_added_after_queue_is_checked_before_smtp(self):
        campaign_id = self._make_campaign()
        self.client.post(f"/campaigns/{campaign_id}/send", data=self._payload())
        with self.app.app_context():
            add_suppression("person@example.com", scope="global")
        with patch("mailpilot.services.campaign_worker_service.send_html_email") as sender:
            self.assertTrue(self._process(worker_id="worker-suppression"))
            sender.assert_not_called()
        with self.app.app_context():
            delivery = db.session.scalar(db.select(CampaignDelivery))
            job = db.session.scalar(db.select(CampaignJob))
            self.assertEqual(delivery.status, "suppressed")
            self.assertEqual(job.status, "partial")

    def test_transient_connection_failure_retries_only_temporary_recipient(self):
        campaign_id = self._make_campaign()
        self.client.post(f"/campaigns/{campaign_id}/send", data=self._payload())
        with patch(
            "mailpilot.services.campaign_worker_service.send_html_email",
            side_effect=[SMTPConnectionFailure(), self._accepted()],
        ) as sender:
            self.assertTrue(self._process(worker_id="worker-retry"))
            with self.app.app_context():
                job = db.session.scalar(db.select(CampaignJob))
                delivery = db.session.scalar(db.select(CampaignDelivery))
                self.assertEqual(job.status, "retrying")
                self.assertEqual(delivery.status, "temporary_failure")
                retry_time = delivery.retry_at
                self.assertIsNotNone(retry_time)
            self.assertFalse(self._process(worker_id="worker-retry", now=retry_time - timedelta(seconds=1)))
            self.assertTrue(self._process(worker_id="worker-retry", now=retry_time + timedelta(seconds=1)))
        self.assertEqual(sender.call_count, 2)
        with self.app.app_context():
            self.assertEqual(db.session.scalar(db.select(CampaignDelivery.status)), "accepted")
            self.assertEqual(db.session.scalar(db.select(CampaignJob.status)), "completed")

    def test_uncertain_stale_smtp_lease_is_never_retried(self):
        campaign_id = self._make_campaign()
        with self.app.app_context():
            campaign = db.session.get(Campaign, campaign_id)
            job, _created = queue_campaign_send(campaign, self._payload())
            delivery = job.deliveries[0]
            job.status = "running"
            job.lease_owner = "dead-worker"
            job.lease_expires_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=1)
            delivery.status = "sending"
            db.session.commit()
        with patch("mailpilot.services.campaign_worker_service.send_html_email") as sender:
            self.assertFalse(self._process(worker_id="replacement-worker"))
            sender.assert_not_called()
        with self.app.app_context():
            self.assertEqual(db.session.scalar(db.select(CampaignJob.status)), "uncertain")
            self.assertEqual(db.session.scalar(db.select(CampaignDelivery.status)), "uncertain")

    def test_local_daily_recipient_cap_defers_unsent_campaign_recipients(self):
        self.app.config["MAILPILOT_DAILY_RECIPIENT_LIMIT"] = 1
        campaign_id = self._make_campaign()
        with self.app.app_context():
            campaign = db.session.get(Campaign, campaign_id)
            second = Recipient(email="second@example.com", first_name="Second", last_name="Recipient", is_active=True)
            db.session.add(second)
            db.session.flush()
            campaign.manual_recipient_ids = [*campaign.manual_recipient_ids, second.id]
            campaign.recipient_snapshot = [
                *campaign.recipient_snapshot,
                {"id": second.id, "name": second.full_name, "email": second.email, "company": ""},
            ]
            db.session.commit()

        self.client.post(f"/campaigns/{campaign_id}/send", data=self._payload())
        with patch("mailpilot.services.campaign_worker_service.send_html_email", side_effect=self._accepted) as sender:
            self.assertTrue(self._process(worker_id="daily-limit-worker"))
        self.assertEqual(sender.call_count, 1)
        with self.app.app_context():
            job = db.session.scalar(db.select(CampaignJob))
            deliveries = db.session.scalars(db.select(CampaignDelivery).order_by(CampaignDelivery.email)).all()
            attempts = db.session.scalars(db.select(CampaignDeliveryAttempt)).all()
            self.assertEqual(job.status, "retrying")
            self.assertEqual(sum(delivery.status == "accepted" for delivery in deliveries), 1)
            deferred = next(delivery for delivery in deliveries if delivery.status == "pending")
            self.assertIsNotNone(deferred.retry_at)
            self.assertEqual(len(attempts), 1)
            current_time = datetime.now(timezone.utc).replace(tzinfo=None)
            self.assertLessEqual(deferred.retry_at, current_time + timedelta(hours=24, minutes=1))

    def test_scheduled_campaign_requires_explicit_consent_and_uses_workspace_timezone(self):
        schedule_local = datetime(2035, 1, 2, 10, 30)
        campaign_id = self._make_campaign(status="scheduled", schedule=schedule_local)
        with self.app.app_context():
            db.session.add(WorkspaceSettings(id=1, timezone="Asia/Kolkata"))
            db.session.flush()
            campaign = db.session.get(Campaign, campaign_id)
            with self.assertRaises(SendValidationError):
                authorize_scheduled_campaign(campaign, {"consent_confirmed": "0"})
            job, created = authorize_scheduled_campaign(campaign, {"consent_confirmed": "1"})
            self.assertTrue(created)
            self.assertEqual(job.kind, "scheduled")
            self.assertEqual(job.scheduled_for, datetime(2035, 1, 2, 5, 0))
        with patch("mailpilot.services.campaign_worker_service.send_html_email", side_effect=self._accepted) as sender:
            self.assertFalse(self._process(now=datetime(2035, 1, 2, 4, 59)))
            sender.assert_not_called()
            self.assertTrue(self._process(now=datetime(2035, 1, 2, 5, 0)))
        sender.assert_called_once()
        with self.app.app_context():
            self.assertEqual(db.session.scalar(db.select(CampaignDelivery.status)), "accepted")

    def test_repeated_daylight_saving_local_time_is_rejected_before_it_can_be_queued(self):
        with self.app.app_context():
            db.session.add(WorkspaceSettings(id=1, timezone="America/New_York"))
            db.session.commit()
            errors = {}
            scheduled_at = _schedule_value(
                {"scheduled_at": "2027-11-07T01:30"},
                "scheduled",
                errors,
            )
            self.assertIsNone(scheduled_at)
            self.assertIn("occurs twice", errors["scheduled_at"])

            campaign = Campaign(scheduled_at=datetime(2027, 11, 7, 1, 30))
            with self.assertRaisesRegex(CampaignQueueError, "occurs twice"):
                _scheduled_time_utc(campaign)


if __name__ == "__main__":
    unittest.main()
