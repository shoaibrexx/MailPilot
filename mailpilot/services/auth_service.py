from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import re
from secrets import token_urlsafe

from flask import current_app
from sqlalchemy import select
from werkzeug.security import check_password_hash, generate_password_hash

from ..extensions import db
from ..models import AuthSession, AuthUser, LoginThrottle


USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@+-]{2,119}$")
PASSWORD_MIN_LENGTH = 14
THROTTLE_WINDOW = timedelta(minutes=15)
LOCK_DURATION = timedelta(minutes=15)
MAX_FAILURES = 5
_DUMMY_PASSWORD_HASH = generate_password_hash("invalid-login-placeholder", method="scrypt")


class AuthenticationSetupError(ValueError):
    pass


def utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def normalize_username(value):
    return str(value or "").strip()[:120].casefold()


def validate_admin_credentials(username, password, display_name="Workspace administrator"):
    errors = {}
    normalized = normalize_username(username)
    if not USERNAME_PATTERN.fullmatch(str(username or "").strip()):
        errors["username"] = "Use 3–120 letters, numbers, dots, underscores, @, +, or hyphens for the username."
    if len(str(password or "")) < PASSWORD_MIN_LENGTH:
        errors["password"] = f"Use a password with at least {PASSWORD_MIN_LENGTH} characters."
    if len(str(password or "")) > 1024:
        errors["password"] = "The password is too long."
    name = " ".join(str(display_name or "").split())
    if not name or len(name) > 120:
        errors["display_name"] = "Enter a display name of 1–120 characters."
    return normalized, name, errors


def _begin_sqlite_write_reservation():
    db.session.commit()
    if db.engine.dialect.name == "sqlite":
        db.session.connection().exec_driver_sql("BEGIN IMMEDIATE")


def create_admin(username, password, display_name="Workspace administrator"):
    normalized, name, errors = validate_admin_credentials(username, password, display_name)
    if errors:
        raise AuthenticationSetupError(" ".join(errors.values()))
    _begin_sqlite_write_reservation()
    try:
        if db.session.scalar(select(AuthUser.id).limit(1)) is not None:
            raise AuthenticationSetupError("A workspace administrator already exists. Use the password-reset command instead.")
        user = AuthUser(
            username=normalized,
            display_name=name,
            password_hash=generate_password_hash(password, method="scrypt"),
        )
        db.session.add(user)
        db.session.commit()
        return user
    except Exception:
        db.session.rollback()
        raise


def change_admin_password(username, password):
    user = db.session.scalar(select(AuthUser).where(AuthUser.username == normalize_username(username)))
    if user is None:
        raise AuthenticationSetupError("No administrator with that username exists.")
    if len(str(password or "")) < PASSWORD_MIN_LENGTH or len(str(password or "")) > 1024:
        raise AuthenticationSetupError(f"Use a password with {PASSWORD_MIN_LENGTH}–1024 characters.")
    user.password_hash = generate_password_hash(password, method="scrypt")
    user.auth_version += 1
    db.session.execute(db.delete(LoginThrottle))
    db.session.commit()
    return user


def _key_hash(scope, value):
    secret = str(current_app.config["SECRET_KEY"]).encode("utf-8")
    message = f"{scope}:{value}".encode("utf-8", errors="replace")
    return hmac.new(secret, message, hashlib.sha256).hexdigest()


def _throttle_keys(username, remote_addr):
    return {
        "username": _key_hash("username", normalize_username(username)),
        "source": _key_hash("source", str(remote_addr or "unknown")),
    }


def is_login_throttled(username, remote_addr, *, now=None):
    now = now or utc_now()
    keys = _throttle_keys(username, remote_addr)
    rows = db.session.scalars(
        select(LoginThrottle).where(
            ((LoginThrottle.scope == "username") & (LoginThrottle.key_hash == keys["username"]))
            | ((LoginThrottle.scope == "source") & (LoginThrottle.key_hash == keys["source"]))
        )
    ).all()
    return any(row.locked_until and row.locked_until > now for row in rows)


def reserve_login_attempt(username, remote_addr, *, now=None):
    """Atomically reserve one login check and cap concurrent password work."""
    now = now or utc_now()
    keys = _throttle_keys(username, remote_addr)
    _begin_sqlite_write_reservation()
    rows = {}
    for scope, key in keys.items():
        row = db.session.scalar(
            select(LoginThrottle).where(LoginThrottle.scope == scope, LoginThrottle.key_hash == key)
        )
        rows[scope] = row
    if any(row and row.locked_until and row.locked_until > now for row in rows.values()):
        db.session.rollback()
        return True

    locked = False
    for scope, key in keys.items():
        row = rows[scope]
        if row is None:
            row = LoginThrottle(scope=scope, key_hash=key, attempts=0, window_started_at=now)
            db.session.add(row)
        if now - row.window_started_at >= THROTTLE_WINDOW or (row.locked_until and row.locked_until <= now):
            row.attempts = 0
            row.window_started_at = now
            row.locked_until = None
        row.attempts += 1
        row.updated_at = now
        if row.attempts >= MAX_FAILURES:
            row.locked_until = now + LOCK_DURATION
            locked = True
    db.session.commit()
    return locked


def clear_login_failures(username, remote_addr):
    keys = _throttle_keys(username, remote_addr)
    db.session.execute(
        db.delete(LoginThrottle).where(
            ((LoginThrottle.scope == "username") & (LoginThrottle.key_hash == keys["username"]))
            | ((LoginThrottle.scope == "source") & (LoginThrottle.key_hash == keys["source"]))
        )
    )
    db.session.commit()


def authenticate(username, password, remote_addr):
    username_value = str(username or "").strip()
    normalized = normalize_username(username_value)
    username_is_valid = bool(USERNAME_PATTERN.fullmatch(username_value))
    if reserve_login_attempt(normalized, remote_addr):
        return None, "locked"
    user = (
        db.session.scalar(select(AuthUser).where(AuthUser.username == normalized))
        if username_is_valid
        else None
    )
    password_value = str(password or "")
    password_hash = user.password_hash if user and len(password_value) <= 1024 else _DUMMY_PASSWORD_HASH
    password_matches = check_password_hash(
        password_hash,
        password_value[:1024],
    )
    if user is None or not user.is_active or not password_matches:
        return None, "invalid"
    clear_login_failures(normalized, remote_addr)
    user.last_login_at = utc_now()
    db.session.commit()
    return user, None


def get_authenticated_user(user_id):
    try:
        return db.session.get(AuthUser, int(user_id))
    except (TypeError, ValueError):
        return None


def create_auth_session(user):
    raw_token = token_urlsafe(32)
    now = utc_now()
    lifetime = current_app.permanent_session_lifetime
    record = AuthSession(
        user_id=user.id,
        token_hash=hashlib.sha256(raw_token.encode("ascii")).hexdigest(),
        auth_version=user.auth_version,
        created_at=now,
        last_seen_at=now,
        expires_at=now + lifetime,
    )
    db.session.add(record)
    db.session.commit()
    return record, raw_token


def find_auth_session(session_id, raw_token):
    try:
        session_id = int(session_id)
    except (TypeError, ValueError):
        return None
    if not raw_token:
        return None
    digest = hashlib.sha256(str(raw_token).encode("ascii", errors="ignore")).hexdigest()
    return db.session.scalar(
        select(AuthSession).where(AuthSession.id == session_id, AuthSession.token_hash == digest)
    )


def touch_auth_session(record, *, now=None):
    now = now or utc_now()
    record.last_seen_at = now
    db.session.commit()


def revoke_auth_session(record, *, now=None):
    if record is None or record.revoked_at is not None:
        return False
    record.revoked_at = now or utc_now()
    db.session.commit()
    return True


def list_auth_sessions(user_id):
    return db.session.scalars(
        select(AuthSession)
        .where(AuthSession.user_id == int(user_id), AuthSession.revoked_at.is_(None))
        .order_by(AuthSession.last_seen_at.desc(), AuthSession.id.desc())
    ).all()


def revoke_user_session(user_id, session_id):
    record = db.session.scalar(
        select(AuthSession).where(AuthSession.id == int(session_id), AuthSession.user_id == int(user_id))
    )
    return revoke_auth_session(record)
