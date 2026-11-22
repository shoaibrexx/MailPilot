import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from mailpilot import create_app
from mailpilot.extensions import db
from mailpilot.models import CampaignDelivery, CampaignDeliveryAttempt, CampaignJob
from werkzeug.middleware.proxy_fix import ProxyFix


class ProductionConfigurationTests(unittest.TestCase):
    def test_production_requires_stable_secret_no_debug_and_secure_cookies(self):
        invalid_configurations = (
            ({"MAILPILOT_SECRET_KEY": ""}, "MAILPILOT_SECRET_KEY"),
            ({"MAILPILOT_SECRET_KEY": "short-secret", "FLASK_DEBUG": "false", "MAILPILOT_SECURE_COOKIES": "true"}, "at least 32 bytes"),
            ({"MAILPILOT_SECRET_KEY": "                                  ", "FLASK_DEBUG": "false", "MAILPILOT_SECURE_COOKIES": "true"}, "MAILPILOT_SECRET_KEY"),
            ({"MAILPILOT_SECRET_KEY": "configured-secret-key-longer-than-thirty-two-bytes", "FLASK_DEBUG": "true", "MAILPILOT_SECURE_COOKIES": "true"}, "FLASK_DEBUG"),
            ({"MAILPILOT_SECRET_KEY": "configured-secret-key-longer-than-thirty-two-bytes", "FLASK_DEBUG": "false", "MAILPILOT_SECURE_COOKIES": "false"}, "MAILPILOT_SECURE_COOKIES"),
        )
        for values, message in invalid_configurations:
            with self.subTest(message=message):
                environment = {
                    "MAILPILOT_ENV": "production",
                    "FLASK_DEBUG": "false",
                    "MAILPILOT_SECURE_COOKIES": "true",
                    **values,
                }
                with patch.dict(os.environ, environment, clear=True):
                    with self.assertRaisesRegex(RuntimeError, message):
                        create_app()

    def test_production_validation_applies_to_factory_overrides_too(self):
        with self.assertRaisesRegex(RuntimeError, "MAILPILOT_SECRET_KEY"):
            create_app({
                "ENVIRONMENT": "production",
                "SECRET_KEY": "",
                "DEBUG": False,
                "SESSION_COOKIE_SECURE": True,
            })

    def test_forwarded_client_ip_and_scheme_are_trusted_only_when_configured(self):
        with tempfile.TemporaryDirectory() as temporary:
            app = create_app({
                "TESTING": True,
                "SECRET_KEY": "proxy-test-secret",
                "TRUSTED_PROXY_HOPS": 1,
                "SQLALCHEMY_DATABASE_URI": f"sqlite:///{(Path(temporary) / 'proxy.sqlite').as_posix()}",
                "UPLOAD_FOLDER": str(Path(temporary) / "uploads"),
            })
            try:
                self.assertIsInstance(app.wsgi_app, ProxyFix)

                @app.before_request
                def observe_proxy_context():
                    from flask import request
                    if request.endpoint == "health.health_check":
                        app.extensions["proxy_context"] = (request.remote_addr, request.is_secure, request.host)

                response = app.test_client().get(
                    "/health",
                    headers={
                        "X-Forwarded-For": "198.51.100.27",
                        "X-Forwarded-Proto": "https",
                        "X-Forwarded-Host": "attacker.example",
                    },
                    environ_overrides={"REMOTE_ADDR": "127.0.0.1"},
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(app.extensions["proxy_context"], ("198.51.100.27", True, "localhost"))
            finally:
                with app.app_context():
                    db.session.remove()
                    db.engine.dispose()

    def test_production_app_can_start_with_isolated_database_and_secure_cookie_config(self):
        with tempfile.TemporaryDirectory() as temporary:
            database_path = Path(temporary) / "production-test.sqlite"
            environment = {
                "MAILPILOT_ENV": "production",
                "MAILPILOT_SECRET_KEY": "production-test-key-longer-than-thirty-two-bytes",
                "FLASK_DEBUG": "false",
                "MAILPILOT_SECURE_COOKIES": "true",
                "DATABASE_URL": f"sqlite:///{database_path.as_posix()}",
            }
            with patch.dict(os.environ, environment, clear=True):
                app = create_app({"TESTING": True, "AUTH_DISABLED": True})
            try:
                self.assertFalse(app.config["DEBUG"])
                self.assertTrue(app.config["SESSION_COOKIE_SECURE"])
                self.assertTrue(app.config["SESSION_COOKIE_HTTPONLY"])
                self.assertEqual(app.config["SESSION_COOKIE_SAMESITE"], "Lax")
                response = app.test_client().get("/health")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers["Strict-Transport-Security"], "max-age=31536000")
                self.assertIn("Content-Security-Policy", response.headers)
            finally:
                with app.app_context():
                    db.session.remove()
                    db.engine.dispose()

    def test_sqlite_foreign_keys_are_enforced_for_new_connections(self):
        with tempfile.TemporaryDirectory() as temporary:
            database_path = Path(temporary) / "foreign-keys.sqlite"
            app = create_app({
                "TESTING": True,
                "SECRET_KEY": "foreign-key-test-secret",
                "SQLALCHEMY_DATABASE_URI": f"sqlite:///{database_path.as_posix()}",
                "UPLOAD_FOLDER": str(Path(temporary) / "uploads"),
            })
            try:
                with app.app_context():
                    self.assertEqual(db.session.connection().exec_driver_sql("PRAGMA foreign_keys").scalar(), 1)
                    job = CampaignJob(
                        campaign_id=123,
                        job_key="foreign-key-job-key-0123456789-abcd",
                        scheduled_for=datetime.now(),
                        total_recipients=1,
                    )
                    db.session.add(job)
                    db.session.flush()
                    delivery = CampaignDelivery(job_id=job.id, email="person@example.com")
                    db.session.add(delivery)
                    db.session.flush()
                    db.session.add(CampaignDeliveryAttempt(
                        delivery_id=delivery.id,
                        attempt_number=1,
                        message_id="<foreign-key@example.test>",
                    ))
                    db.session.commit()
                    job = db.session.get(CampaignJob, job.id)
                    db.session.delete(job)
                    db.session.commit()
                    self.assertEqual(db.session.scalar(db.select(db.func.count(CampaignDelivery.id))), 0)
                    self.assertEqual(db.session.scalar(db.select(db.func.count(CampaignDeliveryAttempt.id))), 0)
            finally:
                with app.app_context():
                    db.session.remove()
                    db.engine.dispose()


if __name__ == "__main__":
    unittest.main()
