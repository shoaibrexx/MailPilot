from urllib.parse import unquote, urlsplit

import click
from flask import Blueprint, flash, redirect, render_template, request, session, url_for
from sqlalchemy import select

from ..extensions import db
from ..models import AuthUser
from ..services.auth_service import (
    AuthenticationSetupError,
    authenticate,
    change_admin_password,
    create_auth_session,
    create_admin,
    find_auth_session,
    revoke_auth_session,
)


auth_blueprint = Blueprint("auth", __name__)


def _safe_next(value):
    candidate = str(value or "").strip()
    if len(candidate) > 2048:
        return url_for("dashboard.index")
    try:
        parts = urlsplit(candidate)
    except ValueError:
        return url_for("dashboard.index")
    decoded = unquote(candidate)
    if (
        parts.scheme
        or parts.netloc
        or not candidate.startswith("/")
        or decoded.startswith("//")
        or "\\" in decoded
        or any(ord(character) < 32 or ord(character) == 127 for character in candidate)
    ):
        return url_for("dashboard.index")
    return candidate


def _admin_exists():
    return db.session.scalar(select(AuthUser.id).limit(1)) is not None


@auth_blueprint.route("/login", methods=["GET", "POST"])
def login():
    next_url = _safe_next(request.values.get("next"))
    error = ""
    status = 200
    if request.method == "POST":
        if not _admin_exists():
            error = "Create the private workspace administrator from the terminal before signing in."
            status = 503
        else:
            username = str(request.form.get("username", "") or "").strip()
            user, failure = authenticate(username, request.form.get("password", ""), request.remote_addr)
            if user is None:
                error = (
                    "Too many sign-in attempts. Try again in 15 minutes."
                    if failure == "locked"
                    else "The username or password is incorrect."
                )
                status = 429 if failure == "locked" else 401
            else:
                session.clear()
                session.permanent = True
                auth_session, raw_token = create_auth_session(user)
                session["auth_user_id"] = user.id
                session["auth_version"] = user.auth_version
                session["auth_session_id"] = auth_session.id
                session["auth_session_token"] = raw_token
                flash("Signed in to the private workspace.", "success")
                return redirect(next_url, code=303)
    return render_template(
        "auth/login.html",
        next_url=next_url,
        error=error,
        admin_configured=_admin_exists(),
        status=status,
    ), status


@auth_blueprint.post("/logout")
def logout():
    auth_session = find_auth_session(
        session.get("auth_session_id"), session.get("auth_session_token")
    )
    revoke_auth_session(auth_session)
    session.clear()
    flash("You have signed out.", "info")
    return redirect(url_for("auth.login"), code=303)


def register_auth_commands(app):
    @app.cli.command("create-admin")
    @click.option("--username", prompt=True, help="Private workspace username")
    @click.option("--name", "display_name", prompt="Display name", default="Workspace administrator")
    def create_admin_command(username, display_name):
        """Create the first private MailPilot workspace administrator."""
        from getpass import getpass

        password = getpass("New password (14+ characters): ")
        confirmation = getpass("Confirm password: ")
        if password != confirmation:
            raise click.ClickException("The passwords did not match.")
        try:
            create_admin(username, password, display_name)
        except AuthenticationSetupError as error:
            raise click.ClickException(str(error)) from None
        click.echo("Private workspace administrator created.")

    @app.cli.command("set-admin-password")
    @click.option("--username", prompt=True, help="Private workspace username")
    def set_admin_password_command(username):
        """Replace the administrator password and revoke existing sessions."""
        from getpass import getpass

        password = getpass("New password (14+ characters): ")
        confirmation = getpass("Confirm password: ")
        if password != confirmation:
            raise click.ClickException("The passwords did not match.")
        try:
            change_admin_password(username, password)
        except AuthenticationSetupError as error:
            raise click.ClickException(str(error)) from None
        click.echo("Administrator password updated. Existing sessions were revoked.")
