import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

from mailpilot import create_app
from mailpilot.extensions import db
from mailpilot.models import AuthSession, AuthUser, LoginThrottle
from mailpilot.services.auth_service import (
    AuthenticationSetupError,
    authenticate,
    change_admin_password,
    create_admin,
)


class AuthenticationTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        self.app = create_app(
            {
                "TESTING": True,
                "SECRET_KEY": "auth-tests-only-secret",
                "WTF_CSRF_ENABLED": False,
                "SQLALCHEMY_DATABASE_URI": f"sqlite:///{(root / 'auth.sqlite').as_posix()}",
                "UPLOAD_FOLDER": str(root / "uploads"),
            }
        )
        self.client = self.app.test_client()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.engine.dispose()
        self.temporary_directory.cleanup()

    def _create_admin(self):
        with self.app.app_context():
            return create_admin("owner", "a-correct-horse-battery-123", "Workspace Owner")

    def _sign_in(self):
        return self.client.post(
            "/login",
            data={"username": "OWNER", "password": "a-correct-horse-battery-123"},
        )

    def test_private_routes_require_login_but_login_and_health_are_public(self):
        self.assertEqual(self.client.get("/login").status_code, 200)
        self.assertEqual(self.client.get("/health").status_code, 200)
        protected = self.client.get("/recipients")
        self.assertEqual(protected.status_code, 303)
        self.assertIn("/login", protected.headers["Location"])
        self.assertEqual(self.client.post("/recipients/new", data={"email": "private@example.com"}).status_code, 401)

    def test_all_private_module_routes_require_the_workspace_login(self):
        for path in (
            "/",
            "/api/dashboard",
            "/recipients",
            "/composer",
            "/campaigns",
            "/campaigns/groups",
            "/analytics",
            "/settings",
            "/security",
            "/security/suppressions",
            "/notifications",
            "/notifications/api/recent",
            "/settings/export/recipients.csv",
        ):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 303)
                self.assertIn("/login", response.headers["Location"])

    def test_authenticated_module_pages_render_and_logout_closes_access(self):
        self._create_admin()
        self.assertEqual(self._sign_in().status_code, 303)
        for path in (
            "/",
            "/recipients",
            "/recipients/import",
            "/composer",
            "/campaigns",
            "/campaigns/new",
            "/campaigns/groups",
            "/analytics",
            "/settings",
            "/security",
            "/security/suppressions",
            "/notifications",
            "/notifications/api/recent",
        ):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 200)
        self.assertEqual(self.client.get("/unsubscribe/not-a-token").status_code, 404)
        self.assertEqual(self.client.post("/logout").status_code, 303)
        self.assertEqual(self.client.get("/settings").status_code, 303)

    def test_sensitive_response_headers_are_set_without_changing_unsubscribe_policy(self):
        response = self.client.get("/login")
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response.headers["X-Frame-Options"], "DENY")
        self.assertEqual(response.headers["Referrer-Policy"], "strict-origin-when-cross-origin")
        self.assertIn("script-src 'self'", response.headers["Content-Security-Policy"])
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    def test_admin_password_is_hashed_and_public_registration_is_absent(self):
        self._create_admin()
        with self.app.app_context():
            user = db.session.scalar(db.select(AuthUser))
            self.assertNotEqual(user.password_hash, "a-correct-horse-battery-123")
            self.assertTrue(user.password_hash.startswith("scrypt:"))
        self.assertEqual(self.client.get("/register").status_code, 404)

    def test_login_creates_server_session_and_logout_revokes_it(self):
        self._create_admin()
        response = self._sign_in()
        self.assertEqual(response.status_code, 303)
        self.assertEqual(self.client.get("/security").status_code, 200)
        cookie = self.client.get_cookie("mailpilot_session")
        self.assertIsNotNone(cookie)
        with self.app.app_context():
            auth_session = db.session.scalar(db.select(AuthSession))
            self.assertIsNotNone(auth_session)
            self.assertNotIn("a-correct-horse-battery-123", auth_session.token_hash)
            session_id = auth_session.id

        logout = self.client.post("/logout")
        self.assertEqual(logout.status_code, 303)
        with self.app.app_context():
            auth_session = db.session.get(AuthSession, session_id)
            self.assertIsNotNone(auth_session.revoked_at)

        replay = self.app.test_client()
        replay.set_cookie("mailpilot_session", cookie.value)
        blocked = replay.get("/recipients")
        self.assertEqual(blocked.status_code, 303)
        self.assertIn("/login", blocked.headers["Location"])

    def test_bad_logins_are_generic_and_persistently_throttled(self):
        self._create_admin()
        responses = [
            self.client.post("/login", data={"username": "owner", "password": "wrong"})
            for _ in range(5)
        ]
        self.assertTrue(all(response.status_code == 401 for response in responses[:4]))
        self.assertEqual(responses[4].status_code, 429)
        self.assertIn(b"Too many sign-in attempts", responses[4].data)
        with self.app.app_context():
            throttle_rows = db.session.scalars(db.select(LoginThrottle)).all()
            self.assertEqual(len(throttle_rows), 2)
            self.assertTrue(all("owner" not in row.key_hash for row in throttle_rows))
            self.assertTrue(all(row.locked_until is not None for row in throttle_rows))

    def test_password_reset_revokes_existing_sessions_and_weak_password_is_rejected(self):
        self._create_admin()
        self.assertEqual(self._sign_in().status_code, 303)
        with self.app.app_context():
            before = db.session.scalar(db.select(AuthUser))
            old_version = before.auth_version
            session_id = db.session.scalar(db.select(AuthSession.id))
            with self.assertRaises(AuthenticationSetupError):
                change_admin_password("owner", "short")
            change_admin_password("owner", "a-new-very-long-secret-passphrase")
            updated = db.session.get(AuthUser, before.id)
            self.assertGreater(updated.auth_version, old_version)

        blocked = self.client.get("/recipients")
        self.assertEqual(blocked.status_code, 303)
        with self.app.app_context():
            auth_session = db.session.get(AuthSession, session_id)
            self.assertEqual(authenticate("owner", "a-new-very-long-secret-passphrase", "127.0.0.1")[1], None)
            self.assertNotEqual(auth_session.auth_version, db.session.scalar(db.select(AuthUser.auth_version)))

    def test_login_form_is_csrf_protected(self):
        self.app.config["WTF_CSRF_ENABLED"] = True
        response = self.client.post("/login", data={"username": "owner", "password": "irrelevant"})
        self.assertEqual(response.status_code, 400)
        self.assertIn(b"CSRF", response.data)

    def test_login_rejects_backslash_and_encoded_protocol_relative_next_urls(self):
        self._create_admin()
        for next_url in (r"/\\attacker.example", "/%2f%2fattacker.example", "//[invalid", "/safe/path"):
            with self.subTest(next_url=next_url):
                response = self.client.post(
                    "/login",
                    data={"username": "owner", "password": "a-correct-horse-battery-123", "next": next_url},
                )
                if next_url == "/safe/path":
                    self.assertEqual(response.headers["Location"], "/safe/path")
                else:
                    self.assertEqual(response.headers["Location"], "/")

    def test_login_rejects_unbounded_usernames_without_truncating_to_an_account(self):
        self._create_admin()
        response = self.client.post(
            "/login",
            data={
                "username": "owner" + ("x" * 150),
                "password": "a-correct-horse-battery-123",
            },
        )
        self.assertEqual(response.status_code, 401)
        self.assertIn(b"username or password is incorrect", response.data)

    def test_parallel_bad_logins_are_counted_before_password_verification(self):
        self._create_admin()
        start = Barrier(8)

        def attempt(_index):
            with self.app.app_context():
                start.wait(timeout=5)
                _user, failure = authenticate("owner", "wrong-password", "127.0.0.1")
                return failure

        with ThreadPoolExecutor(max_workers=8) as executor:
            failures = list(executor.map(attempt, range(8)))
        self.assertEqual(failures.count("invalid"), 4)
        self.assertEqual(failures.count("locked"), 4)

    def test_concurrent_admin_setup_cannot_create_multiple_workspace_admins(self):
        start = Barrier(2)

        def create(username):
            with self.app.app_context():
                start.wait(timeout=5)
                try:
                    create_admin(username, "a-correct-horse-battery-123", "Workspace Owner")
                    return "created"
                except AuthenticationSetupError:
                    return "exists"

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(create, ("ownerone", "ownertwo")))
        self.assertCountEqual(outcomes, ["created", "exists"])
        with self.app.app_context():
            self.assertEqual(db.session.scalar(db.select(db.func.count(AuthUser.id))), 1)


if __name__ == "__main__":
    unittest.main()
