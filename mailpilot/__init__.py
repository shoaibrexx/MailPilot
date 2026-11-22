from pathlib import Path
from secrets import token_urlsafe
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
from flask import Flask, abort, flash, g, redirect, request, session, url_for
from flask.signals import message_flashed
from sqlalchemy import event
from werkzeug.middleware.proxy_fix import ProxyFix

from .config import config_from_environment
from .extensions import db
from .extensions import csrf
from .models import (
    AuthSession,
    AuthUser,
    Campaign,
    EmailDraft,
    EmailSendHistory,
    Notification,
    NotificationPreference,
    Recipient,
    RecipientGroup,
    SecurityEvent,
    WorkspaceSettings,
    WorkspaceActionThrottle,
)  # Register models before create_all().
from .routes import register_blueprints
from .routes.auth import register_auth_commands
from .services.auth_service import find_auth_session, revoke_auth_session, touch_auth_session
from .services.notification_service import create_notification, unread_notification_count
from .services.security_service import SECURITY_ACTIONS, log_security_event
from .services.campaign_worker_service import register_campaign_worker_command


def create_app(test_config=None):
    """Create and configure a MailPilot Flask application."""
    load_dotenv()

    app = Flask(__name__, instance_relative_config=True)
    app.config.from_mapping(config_from_environment())
    if test_config:
        app.config.update(test_config)

    if not app.config.get("SECRET_KEY"):
        if app.config.get("ENVIRONMENT") == "production":
            raise RuntimeError("MAILPILOT_SECRET_KEY must be configured in production.")
        app.config["SECRET_KEY"] = token_urlsafe(48)
    if app.config.get("ENVIRONMENT") == "production":
        if len(str(app.config["SECRET_KEY"]).encode("utf-8")) < 32:
            raise RuntimeError("MAILPILOT_SECRET_KEY must contain at least 32 bytes in production.")
        if app.config.get("DEBUG"):
            raise RuntimeError("FLASK_DEBUG must be false in production.")
        if not app.config.get("SESSION_COOKIE_SECURE"):
            raise RuntimeError("MAILPILOT_SECURE_COOKIES must be true in production.")
    app.config.setdefault(
        "UPLOAD_FOLDER", str(Path(app.instance_path) / "uploads")
    )

    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    Path(app.config["UPLOAD_FOLDER"]).mkdir(parents=True, exist_ok=True)

    db.init_app(app)
    csrf.init_app(app)

    @app.context_processor
    def inject_workspace_preferences():
        settings = db.session.get(WorkspaceSettings, 1)
        return {
            "current_user": getattr(g, "current_user", None),
            "theme_preference": settings.theme_preference if settings else "system",
            "workspace_name": settings.workspace_name if settings else "MailPilot Workspace",
            "workspace_description": (
                settings.workspace_description if settings else "Organize audiences and campaign plans."
            ),
            "profile_name": settings.profile_name if settings else "",
            "profile_email": settings.profile_email if settings else "",
            "notification_unread_count": unread_notification_count(),
        }

    @app.before_request
    def authenticate_request():
        g.current_user = None
        endpoint = request.endpoint or ""
        if endpoint in {"unsubscribe.landing", "unsubscribe.process"}:
            return None
        if app.config.get("AUTH_DISABLED", False) or endpoint == "static":
            return None
        if request.endpoint is None:
            return None
        if endpoint in {"auth.login", "health.health_check"}:
            return None
        session.permanent = True

        user_id = session.get("auth_user_id")
        user = db.session.get(AuthUser, user_id) if user_id else None
        auth_session = find_auth_session(
            session.get("auth_session_id"), session.get("auth_session_token")
        ) if user_id else None
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        idle_limit = timedelta(minutes=int(app.config.get("AUTH_IDLE_TIMEOUT_MINUTES", 30)))
        valid_session = (
            user is not None
            and user.is_active
            and session.get("auth_version") == user.auth_version
            and auth_session is not None
            and auth_session.user_id == user.id
            and auth_session.auth_version == user.auth_version
            and auth_session.revoked_at is None
            and auth_session.expires_at > now
            and now - auth_session.last_seen_at <= idle_limit
        )
        if not valid_session:
            expired = user_id is not None
            if auth_session is not None and auth_session.revoked_at is None:
                revoke_auth_session(auth_session, now=now)
            session.clear()
            if request.method in {"GET", "HEAD"}:
                if expired:
                    flash("Your workspace session expired. Please sign in again.", "warning")
                return redirect(url_for("auth.login", next=request.full_path if request.query_string else request.path), code=303)
            abort(401)
        if (now - auth_session.last_seen_at).total_seconds() >= 60:
            touch_auth_session(auth_session, now=now)
        g.current_user = user
        g.current_auth_session = auth_session
        return None

    @app.after_request
    def record_security_activity(response):
        endpoint = request.endpoint or ""
        if (
            request.method in {"POST", "PUT", "PATCH", "DELETE"}
            and endpoint in SECURITY_ACTIONS
        ):
            try:
                log_security_event(
                    endpoint=endpoint,
                    method=request.method,
                    status_code=response.status_code,
                )
                if response.status_code >= 400 and endpoint != "auth.login":
                    action = SECURITY_ACTIONS[endpoint]
                    create_notification(
                        "error",
                        f"{action} was rejected with HTTP {response.status_code}.",
                        title="Workspace action needs attention",
                        source="security",
                    )
            except Exception:
                db.session.rollback()
                app.logger.exception("Could not persist a workspace security event.")

        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; base-uri 'self'; object-src 'none'; "
            "frame-ancestors 'none'; form-action 'self'; img-src 'self' data:; "
            "font-src 'self' data:; script-src 'self'; "
            "style-src 'self' 'unsafe-inline'; connect-src 'self'; frame-src 'self'",
        )
        if endpoint != "static":
            response.headers.setdefault("Cache-Control", "no-store")
        if (
            app.config.get("ENVIRONMENT") == "production"
            and app.config.get("SESSION_COOKIE_SECURE")
        ):
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
        return response

    @message_flashed.connect_via(app)
    def persist_flash_notification(sender, message, category, **extra):
        try:
            create_notification(category, message, source="flash")
        except Exception:
            db.session.rollback()
            app.logger.exception("Could not persist a workspace notification.")

    register_blueprints(app)
    register_auth_commands(app)
    register_campaign_worker_command(app)
    trusted_proxy_hops = int(app.config.get("TRUSTED_PROXY_HOPS", 0))
    if trusted_proxy_hops:
        # Enable forwarded client IP/protocol only when the operator explicitly
        # trusts the exact proxy chain in front of the loopback WSGI server.
        app.wsgi_app = ProxyFix(
            app.wsgi_app,
            x_for=trusted_proxy_hops,
            x_proto=trusted_proxy_hops,
            x_host=0,
            x_port=0,
            x_prefix=0,
        )
    with app.app_context():
        if db.engine.dialect.name == "sqlite":
            def enable_sqlite_foreign_keys(connection, _record):
                cursor = connection.cursor()
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.close()

            event.listen(db.engine, "connect", enable_sqlite_foreign_keys)
        db.create_all()
    return app
