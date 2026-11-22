from flask import Blueprint, current_app, flash, g, redirect, render_template, request, session, url_for

from ..services.auth_service import list_auth_sessions, revoke_auth_session, revoke_user_session

from ..services.notification_service import create_notification
from ..services.security_service import list_security_events
from ..services.unsubscribe_service import (
    UnsubscribeValidationError,
    list_suppressions,
    remove_suppression,
    suppression_counts,
    add_suppression,
)
from ..services.campaign_service import list_campaigns


security_blueprint = Blueprint("security", __name__, url_prefix="/security")


@security_blueprint.get("")
@security_blueprint.get("/")
def index():
    try:
        page = max(1, int(request.args.get("page", 1)))
    except (TypeError, ValueError):
        page = 1
    outcome = str(request.args.get("outcome", "all") or "all").casefold()
    pagination, outcome = list_security_events(page=page, outcome=outcome)
    if pagination.pages and pagination.page > pagination.pages:
        return redirect(
            url_for("security.index", page=pagination.pages, outcome=outcome)
        )
    return render_template(
        "security/index.html",
        pagination=pagination,
        outcome=outcome,
        session_lifetime_hours=int(current_app.permanent_session_lifetime.total_seconds() // 3600),
        cookie_http_only=current_app.config["SESSION_COOKIE_HTTPONLY"],
        cookie_secure=current_app.config["SESSION_COOKIE_SECURE"],
        cookie_same_site=current_app.config["SESSION_COOKIE_SAMESITE"],
        auth_sessions=list_auth_sessions(g.current_user.id) if g.current_user else [],
        current_auth_session_id=(g.current_auth_session.id if getattr(g, "current_auth_session", None) else None),
        auth_enabled=not current_app.config.get("AUTH_DISABLED", False),
    )


@security_blueprint.post("/session/clear")
def clear_session():
    auth_session = getattr(g, "current_auth_session", None)
    if auth_session is not None:
        revoke_auth_session(auth_session)
    create_notification(
        "info",
        "The current workspace session was signed out and revoked.",
        title="Workspace session ended",
        source="security",
    )
    session.clear()
    if current_app.config.get("AUTH_DISABLED", False):
        return redirect(url_for("security.index"), code=303)
    return redirect(url_for("auth.login"), code=303)


@security_blueprint.post("/sessions/<int:session_id>/revoke")
def revoke_session(session_id):
    current_session = getattr(g, "current_auth_session", None)
    if current_session and current_session.id == session_id:
        revoke_auth_session(current_session)
        session.clear()
        create_notification("info", "Your current workspace session was revoked.", title="Session revoked", source="security")
        return redirect(url_for("auth.login"), code=303)
    revoked = revoke_user_session(g.current_user.id, session_id)
    if revoked:
        create_notification("success", "A workspace session was revoked.", title="Session revoked", source="security")
    else:
        create_notification("warning", "The requested workspace session was not found.", title="Session not changed", source="security")
    return redirect(url_for("security.index"), code=303)


@security_blueprint.get("/suppressions")
def suppressions():
    search = str(request.args.get("q", "") or "").strip()[:120]
    scope = str(request.args.get("scope", "all") or "all").casefold()
    if scope not in {"all", "global", "campaign"}:
        scope = "all"
    try:
        page = max(1, int(request.args.get("page", 1)))
    except (TypeError, ValueError):
        page = 1
    pagination = list_suppressions(search=search, scope=scope, page=page)
    return render_template(
        "security/suppressions.html",
        pagination=pagination,
        search=search,
        scope=scope,
        counts=suppression_counts(),
        campaigns=list_campaigns(status="all", per_page=1000).items,
    )


@security_blueprint.post("/suppressions")
def add_suppression_route():
    try:
        _record, created = add_suppression(
            request.form.get("email"),
            scope=request.form.get("scope", "global"),
            campaign_id=request.form.get("campaign_id"),
            source="manual",
            reason="Added by workspace administrator.",
        )
    except UnsubscribeValidationError as error:
        flash(str(error), "error")
    else:
        flash("Suppression added." if created else "That address is already suppressed at this scope.", "success" if created else "info")
    return redirect(url_for("security.suppressions"), code=303)


@security_blueprint.post("/suppressions/<int:suppression_id>/delete")
def delete_suppression_route(suppression_id):
    if remove_suppression(suppression_id):
        flash("Suppression removed. The address may be eligible for future campaigns.", "warning")
    else:
        flash("That suppression record was not found.", "info")
    return redirect(url_for("security.suppressions"), code=303)
