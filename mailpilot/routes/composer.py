from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, url_for
from secrets import token_urlsafe

from ..services.composer_service import (
    DraftValidationError,
    active_contact_suggestions,
    create_draft,
    delete_draft,
    draft_form_values,
    get_draft,
    list_drafts,
    sanitize_email_html,
    sender_profile_label,
    update_draft,
)
from ..services.composer_service import SENDER_IDENTITIES
from ..services.settings_service import get_settings
from ..services.email_service import smtp_is_configured
from ..services.send_history_service import (
    SendValidationError,
    blocking_draft_send,
    list_send_history,
    send_draft,
)


composer_blueprint = Blueprint("composer", __name__, url_prefix="/composer")


def _render_composer(*, draft=None, form_values=None, errors=None, status=200, send_key=None):
    values = form_values if form_values is not None else draft_form_values(draft)
    return (
        render_template(
            "composer/index.html",
            draft=draft,
            form_values=values,
            preview_body=sanitize_email_html(values.get("body_html", "")),
            errors=errors or {},
            drafts=list_drafts(),
            sender_identities=SENDER_IDENTITIES,
            sender_label=sender_profile_label,
            settings=get_settings(),
            smtp_configured=smtp_is_configured(),
            send_history=list_send_history(8),
            draft_send_blocked=bool(blocking_draft_send(draft.id)) if draft else False,
            send_key=send_key or token_urlsafe(32),
        ),
        status,
    )


def _draft_or_404(draft_id):
    draft = get_draft(draft_id)
    if draft is None:
        abort(404)
    return draft


@composer_blueprint.get("")
def index():
    return _render_composer()


@composer_blueprint.post("/drafts")
def create():
    try:
        draft = create_draft(request.form)
    except DraftValidationError as error:
        return _render_composer(
            form_values=request.form, errors=error.errors, status=422
        )

    flash("Draft saved.", "success")
    return redirect(url_for("composer.edit", draft_id=draft.id), code=303)


@composer_blueprint.get("/drafts/<int:draft_id>")
def edit(draft_id):
    return _render_composer(draft=_draft_or_404(draft_id))


@composer_blueprint.post("/drafts/<int:draft_id>")
def update(draft_id):
    draft = _draft_or_404(draft_id)
    try:
        update_draft(draft, request.form)
    except DraftValidationError as error:
        return _render_composer(
            draft=draft, form_values=request.form, errors=error.errors, status=422
        )

    flash("Draft updated.", "success")
    return redirect(url_for("composer.edit", draft_id=draft.id), code=303)


@composer_blueprint.post("/drafts/<int:draft_id>/send")
def send(draft_id):
    draft = _draft_or_404(draft_id)
    try:
        history, error, duplicate = send_draft(draft, request.form)
    except DraftValidationError as validation_error:
        return _render_composer(
            draft=draft,
            form_values=request.form,
            errors=validation_error.errors,
            status=422,
            send_key=request.form.get("submission_key"),
        )
    except SendValidationError as validation_error:
        return _render_composer(
            draft=draft,
            form_values=request.form,
            errors=validation_error.errors,
            status=422,
            send_key=request.form.get("submission_key"),
        )

    if duplicate:
        flash(error or "This send submission was already processed.", "warning")
    elif error:
        flash(error, "error")
    else:
        flash(f"Email sent to {history.recipient_count} recipients.", "success")
    return redirect(url_for("composer.edit", draft_id=draft.id), code=303)


@composer_blueprint.post("/drafts/<int:draft_id>/delete")
def remove(draft_id):
    draft = _draft_or_404(draft_id)
    delete_draft(draft)
    flash("Draft deleted.", "success")
    return redirect(url_for("composer.index"), code=303)


@composer_blueprint.get("/api/recipients")
def recipients():
    search = request.args.get("q", "").strip()[:120]
    return jsonify({"recipients": active_contact_suggestions(search=search, limit=30)})
