from datetime import datetime, timedelta, timezone
import tempfile
import unittest
from pathlib import Path

from mailpilot import create_app
from mailpilot.extensions import db
from mailpilot.models import (
    BounceEvent,
    Campaign,
    CampaignDelivery,
    CampaignDeliveryAttempt,
    CampaignJob,
    EmailDraft,
    EmailSendHistory,
    Recipient,
    Suppression,
)
from mailpilot.services.analytics_service import get_analytics_data


def utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class AnalyticsExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        self.app = create_app({
            "TESTING": True,
            "SECRET_KEY": "analytics-tests-only-secret",
            "WTF_CSRF_ENABLED": False,
            "AUTH_DISABLED": True,
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{(root / 'mailpilot.sqlite').as_posix()}",
            "UPLOAD_FOLDER": str(root / "uploads"),
        })
        self.client = self.app.test_client()
        self.now = utc_now()
        with self.app.app_context():
            recipient = Recipient(email="accepted@example.com", first_name="Test", last_name="One")
            db.session.add(recipient)
            db.session.flush()
            draft = EmailDraft(
                sender_identity="workspace",
                to_recipients=[recipient.email],
                cc_recipients=[],
                bcc_recipients=[],
                subject="Composer message",
                body_html="<p>Message</p>",
            )
            db.session.add(draft)
            db.session.flush()
            campaign = Campaign(
                name="Tracked campaign",
                status="ready",
                email_draft_id=draft.id,
                draft_name=draft.subject,
                subject="Campaign message",
                body_html="<p>Campaign message</p>",
                manual_recipient_ids=[recipient.id],
                recipient_group_ids=[],
                recipient_group_names=[],
                recipient_snapshot=[{
                    "id": recipient.id,
                    "name": recipient.full_name,
                    "email": recipient.email,
                    "company": "",
                }],
                created_at=self.now,
                updated_at=self.now,
            )
            db.session.add(campaign)
            db.session.flush()
            completed_job = CampaignJob(
                campaign_id=campaign.id,
                job_key="tracked-campaign-job-key-000000001",
                kind="immediate",
                status="partial",
                consent_confirmed=True,
                snapshot={"subject": "Campaign message", "recipients": [recipient.email]},
                scheduled_for=self.now,
                total_recipients=4,
                created_at=self.now,
            )
            db.session.add(completed_job)
            db.session.flush()

            accepted = CampaignDelivery(job_id=completed_job.id, email="accepted@example.com", status="accepted", attempt_count=1, accepted_at=self.now)
            temporary = CampaignDelivery(job_id=completed_job.id, email="temporary@example.com", status="temporary_failure", attempt_count=1, retry_at=self.now + timedelta(minutes=5))
            uncertain = CampaignDelivery(job_id=completed_job.id, email="uncertain@example.com", status="uncertain", attempt_count=1)
            excluded = CampaignDelivery(job_id=completed_job.id, email="excluded@example.com", status="suppressed")
            db.session.add_all([accepted, temporary, uncertain, excluded])
            db.session.flush()
            db.session.add_all([
                CampaignDeliveryAttempt(delivery_id=accepted.id, attempt_number=1, message_id="<accepted@example.test>", status="accepted", attempted_at=self.now, completed_at=self.now),
                CampaignDeliveryAttempt(delivery_id=temporary.id, attempt_number=1, message_id="<temporary@example.test>", status="temporary_failure", attempted_at=self.now - timedelta(minutes=2), completed_at=self.now - timedelta(minutes=1)),
                CampaignDeliveryAttempt(delivery_id=uncertain.id, attempt_number=1, message_id="<uncertain@example.test>", status="uncertain", attempted_at=self.now - timedelta(minutes=3), completed_at=self.now - timedelta(minutes=2)),
            ])
            db.session.add_all([
                BounceEvent(delivery_id=accepted.id, source_key="a" * 64, recipient_email=accepted.email, classification="permanent", status_code="5.1.1", bounced_at=self.now),
                BounceEvent(delivery_id=temporary.id, source_key="b" * 64, recipient_email=temporary.email, classification="temporary", status_code="4.2.0", bounced_at=self.now),
                Suppression(email="excluded@example.com", scope="global", source="manual", reason="User request", created_at=self.now),
            ])

            queued_campaign = Campaign(
                name="Queued campaign",
                status="scheduled",
                draft_name="Composer message",
                subject="Queued message",
                body_html="<p>Queued</p>",
                manual_recipient_ids=[recipient.id],
                recipient_group_ids=[],
                recipient_group_names=[],
                recipient_snapshot=[{"id": recipient.id, "name": recipient.full_name, "email": recipient.email, "company": ""}],
                created_at=self.now,
                updated_at=self.now,
            )
            db.session.add(queued_campaign)
            db.session.flush()
            queued_job = CampaignJob(
                campaign_id=queued_campaign.id,
                job_key="tracked-queued-campaign-job-000001",
                kind="scheduled",
                status="queued",
                consent_confirmed=True,
                snapshot={"subject": "Queued message", "recipients": [recipient.email]},
                scheduled_for=self.now + timedelta(days=1),
                total_recipients=1,
                created_at=self.now,
            )
            db.session.add(queued_job)
            db.session.flush()
            db.session.add(CampaignDelivery(job_id=queued_job.id, email="queued@example.com", status="pending"))

            composer_statuses = ("sent", "partial", "failed", "uncertain")
            for index, status in enumerate(composer_statuses):
                composer_draft = EmailDraft(
                    sender_identity="workspace",
                    to_recipients=[recipient.email],
                    cc_recipients=[],
                    bcc_recipients=[],
                    subject=f"Composer message {index}",
                    body_html="<p>Message</p>",
                )
                db.session.add(composer_draft)
                db.session.flush()
                db.session.add(EmailSendHistory(
                    submission_key=f"composer-history-key-{index:02d}-000000000000",
                    draft_id=composer_draft.id,
                    recipient_addresses={"to": [recipient.email], "cc": [], "bcc": []},
                    recipient_count=1,
                    subject="Composer message",
                    status=status,
                    consent_confirmed=True,
                    attempted_at=self.now - timedelta(minutes=index),
                ))
            db.session.commit()
            self.campaign_id = campaign.id
            self.queued_campaign_id = queued_campaign.id

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.engine.dispose()
        self.temporary_directory.cleanup()

    def test_analytics_counts_actual_campaign_and_composer_outcomes_without_delivery_claims(self):
        with self.app.app_context():
            data = get_analytics_data("30d")
        execution = data["execution"]
        self.assertEqual(execution["campaign_attempts"], 3)
        self.assertEqual(execution["campaign_accepted"], 1)
        self.assertEqual(execution["campaign_failed"], 1)
        self.assertEqual(execution["campaign_uncertain"], 1)
        self.assertEqual(execution["composer_messages_accepted"], 1)
        self.assertEqual(execution["composer_messages_partial"], 1)
        self.assertEqual(execution["composer_messages_failed"], 1)
        self.assertEqual(execution["composer_messages_uncertain"], 1)
        self.assertEqual(execution["pending_campaign_recipients"], 1)
        self.assertEqual(execution["suppressed_exclusions"], 1)
        self.assertEqual(execution["suppression_records"], 1)
        self.assertEqual(execution["permanent_bounces"], 1)
        self.assertEqual(execution["temporary_bounces"], 1)
        metrics = data["report_campaign_metrics"][self.campaign_id]
        self.assertEqual(metrics["attempts"], 3)
        self.assertEqual(metrics["accepted"], 1)
        self.assertEqual(metrics["failed"], 1)
        self.assertEqual(metrics["uncertain"], 1)
        self.assertEqual(metrics["suppressed_excluded"], 1)
        self.assertEqual(metrics["permanent_bounces"], 1)
        self.assertEqual(metrics["temporary_bounces"], 1)
        self.assertEqual(sum(point["accepted"] for point in data["sending_activity"]), 2)
        self.assertEqual(sum(point["failed"] for point in data["sending_activity"]), 2)
        self.assertEqual(sum(point["partial"] for point in data["sending_activity"]), 1)
        self.assertEqual(sum(point["uncertain"] for point in data["sending_activity"]), 2)

    def test_dashboard_and_export_use_real_smtp_and_bounce_records(self):
        dashboard = self.client.get("/api/dashboard").get_json()
        self.assertEqual(dashboard["stats"]["emails_sent"]["value"], 2)
        self.assertEqual(dashboard["stats"]["successful_emails"]["value"], 1)
        self.assertEqual(dashboard["stats"]["failed_emails"]["value"], 2)
        self.assertEqual(sum(point["accepted"] for point in dashboard["activity"]), 2)

        page = self.client.get("/analytics?range=30d")
        export = self.client.get("/analytics/export.csv?range=30d").get_data(as_text=True)
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"Campaign performance", page.data)
        self.assertIn(b"Acceptance is not confirmed delivery", page.data)
        self.assertIn("Campaign SMTP accepted,1", export)
        self.assertIn("Permanent matched bounces,1", export)
        self.assertIn("SMTP accepted", export)
        self.assertIn("does not confirm inbox delivery", export)
        self.assertNotIn("open rate", export.casefold())

    def test_date_range_filters_historical_send_and_bounce_events(self):
        with self.app.app_context():
            old_attempt = CampaignDeliveryAttempt(
                delivery_id=db.session.scalar(db.select(CampaignDelivery.id).where(CampaignDelivery.email == "accepted@example.com")),
                attempt_number=2,
                message_id="<old@example.test>",
                status="accepted",
                attempted_at=self.now - timedelta(days=45),
                completed_at=self.now - timedelta(days=45),
            )
            old_delivery = db.session.get(CampaignDelivery, old_attempt.delivery_id)
            old_delivery.attempt_count = 2
            old_bounce = BounceEvent(
                delivery_id=old_attempt.delivery_id,
                source_key="c" * 64,
                recipient_email="accepted@example.com",
                classification="permanent",
                status_code="5.1.1",
                bounced_at=self.now - timedelta(days=45),
            )
            db.session.add_all([old_attempt, old_bounce])
            db.session.commit()
            recent = get_analytics_data("30d")
            all_time = get_analytics_data("all")
        self.assertEqual(recent["execution"]["campaign_accepted"], 1)
        self.assertEqual(recent["execution"]["permanent_bounces"], 1)
        self.assertEqual(all_time["execution"]["campaign_accepted"], 2)
        self.assertEqual(all_time["execution"]["permanent_bounces"], 2)


if __name__ == "__main__":
    unittest.main()
