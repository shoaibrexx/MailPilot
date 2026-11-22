import os
from datetime import timedelta


def _positive_integer(name, default):
    value = int(os.environ.get(name, default))
    if value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _nonnegative_integer(name, default):
    value = int(os.environ.get(name, default))
    if value < 0:
        raise ValueError(f"{name} must be zero or a positive integer")
    return value


def _environment_flag(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().casefold() in {"1", "true", "yes", "on"}


def config_from_environment():
    """Return application settings after python-dotenv has loaded .env."""
    environment = os.environ.get("MAILPILOT_ENV", "development").strip().casefold()
    return {
        "ENVIRONMENT": environment,
        "SECRET_KEY": os.environ.get("MAILPILOT_SECRET_KEY", "").strip() or None,
        "SQLALCHEMY_DATABASE_URI": os.environ.get(
            "DATABASE_URL", "sqlite:///mailpilot.sqlite"
        ),
        "SQLALCHEMY_TRACK_MODIFICATIONS": False,
        "MAX_CONTENT_LENGTH": _positive_integer(
            "MAX_UPLOAD_SIZE_BYTES", 5 * 1024 * 1024
        ),
        "MAX_UPLOAD_ROWS": 100_000,
        "TRUSTED_PROXY_HOPS": _nonnegative_integer("MAILPILOT_TRUSTED_PROXY_HOPS", 0),
        "MAIL_SERVER": os.environ.get("MAIL_SERVER", "smtp.gmail.com").strip(),
        "MAIL_PORT": _positive_integer("MAIL_PORT", 587),
        "MAIL_USERNAME": os.environ.get("MAIL_USERNAME", "").strip(),
        "MAIL_PASSWORD": os.environ.get("MAIL_PASSWORD", ""),
        "MAIL_DEFAULT_SENDER": os.environ.get("MAIL_DEFAULT_SENDER", "").strip(),
        "MAIL_TIMEOUT": _positive_integer("MAIL_TIMEOUT", 20),
        "MAILPILOT_PUBLIC_BASE_URL": os.environ.get("MAILPILOT_PUBLIC_BASE_URL", "").strip().rstrip("/"),
        "MAILPILOT_DAILY_RECIPIENT_LIMIT": _positive_integer("MAILPILOT_DAILY_RECIPIENT_LIMIT", 100),
        "MAIL_IMAP_SERVER": os.environ.get("MAIL_IMAP_SERVER", "imap.gmail.com").strip(),
        "MAIL_IMAP_PORT": _positive_integer("MAIL_IMAP_PORT", 993),
        "MAIL_IMAP_USERNAME": os.environ.get("MAIL_IMAP_USERNAME", "").strip(),
        "MAIL_IMAP_PASSWORD": os.environ.get("MAIL_IMAP_PASSWORD", ""),
        "MAIL_IMAP_FOLDER": os.environ.get("MAIL_IMAP_FOLDER", "INBOX").strip(),
        "MAIL_IMAP_POLL_SECONDS": _positive_integer("MAIL_IMAP_POLL_SECONDS", 60),
        "DEBUG": os.environ.get("FLASK_DEBUG", "false").casefold()
        in {"1", "true", "yes", "on"},
        "SESSION_COOKIE_HTTPONLY": True,
        "SESSION_COOKIE_SAMESITE": "Lax",
        "SESSION_COOKIE_SECURE": _environment_flag(
            "MAILPILOT_SECURE_COOKIES", environment == "production"
        ),
        "SESSION_COOKIE_NAME": "mailpilot_session",
        "PERMANENT_SESSION_LIFETIME": timedelta(hours=8),
        "SESSION_REFRESH_EACH_REQUEST": False,
        "AUTH_IDLE_TIMEOUT_MINUTES": _positive_integer("MAILPILOT_AUTH_IDLE_TIMEOUT_MINUTES", 30),
    }
