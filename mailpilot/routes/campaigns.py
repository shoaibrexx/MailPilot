from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, url_for
from secrets import token_urlsafe

from ..extensions import db
from ..models import WorkspaceSettings
from ..services.campaign_service import (
    CAMPAIGN_STATUSES,
    CampaignValidationError,
    DuplicateRecipientGroupError,
    campaign_form_values,
    create_campaign,
    create_recipient_group,
    delete_campaign,
    delete_recipient_group,
    get_active_recipients,
    get_campaign,
    get_campaign_summary,
    get_recipient_group,
    group_active_member_count,
    group_form_values,
    list_campaigns,
    list_email_drafts,
    list_recipient_groups,
    recipient_picker_records,
    sanitize_email_html,
    search_active_recipients,
    update_campaign,
    update_recipient_group,
)
from ..services.email_service import smtp_is_configured
from ..services.send_history_service import SendValidationError, has_campaign_send, list_send_history
from ..services.campaign_worker_service import (
    CampaignQueueError,
    authorize_scheduled_campaign,
    campaign_job_summary,
    list_campaign_jobs,
    queue_campaign_send,
)


campaigns_blueprint = Blueprint("campaigns", __name__, url_prefix="/campaigns")


def _positive_int(value, fallback):
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return fallback


def _ids_from_value(value):
    if isinstance(value, (list, tuple, set)):
        parts = [part for item in value for part in str(item).split(",")]
    else:
        parts = str(value or "").split(",")
    ids = []
    for part in parts:
        try:
            value = int(part.strip())
        except (ValueError, TypeError):
            continue
        if value > 0 and value not in ids:
            ids.append(value)
    return ids


def _render_campaign_form(*, campaign=None, form_values=None, errors=None, status=200):
    values = form_values if form_values is not None else campaign_form_values(campaign)
    if hasattr(values, "getlist"):
        values = {
            "name": values.get("name", ""),
            "email_draft_id": values.get("email_draft_id", ""),
            "status": values.get("status", "draft"),
            "scheduled_at": values.get("scheduled_at", ""),
            "manual_recipient_ids": values.get("manual_recipient_ids", ""),
            "recipient_group_ids": values.getlist("recipient_group_ids"),
        }
    manual_ids = _ids_from_value(values.get("manual_recipient_ids", ""))
    selected_group_ids = {
        str(group_id)
        for group_id in _ids_from_value(values.get("recipient_group_ids", []))
    }
    drafts = list_email_drafts()
    return (
        render_template(
            "campaigns/form.html",
            campaign=campaign,
            form_values=values,
            safe_campaign_body=sanitize_email_html(campaign.body_html) if campaign else "",
            errors=errors or {},
            drafts=drafts,
            draft_preview_data=[
                {
                    "id": draft.id,
                    "subject": draft.subject,
                    "body_html": sanitize_email_html(draft.body_html),
                }
                for draft in drafts
            ],
            groups=list_recipient_groups(),
            selected_group_ids=selected_group_ids,
            selected_recipients=recipient_picker_records(
                get_active_recipients(manual_ids)
            ),
            statuses=CAMPAIGN_STATUSES,
            recipient_search_url=url_for("campaigns.recipient_search"),
        ),
        status,
    )


def _render_group_form(*, group=None, form_values=None, errors=None, status=200):
    values = form_values if form_values is not None else group_form_values(group)
    selected_ids = _ids_from_value(values.get("recipient_ids", ""))
    return (
        render_template(
            "campaigns/group_form.html",
            group=group,
            form_values=values,
            errors=errors or {},
            selected_recipients=recipient_picker_records(
                get_active_recipients(selected_ids)
            ),
            recipient_search_url=url_for("campaigns.recipient_search"),
        ),
        status,
    )


def _campaign_or_404(campaign_id):
    campaign = get_campaign(campaign_id)
    if campaign is None:
        abort(404)
    return campaign


def _group_or_404(group_id):
    group = get_recipient_group(group_id)
    if group is None:
        abort(404)
    return group


@campaigns_blueprint.get("")
def index():
    search = request.args.get("q", "").strip()[:120]
    status = request.args.get("status", "all").casefold()
    if status not in {"all", *CAMPAIGN_STATUSES}:
        status = "all"
    page = _positive_int(request.args.get("page", 1), 1)
    try:
        per_page = int(request.args.get("per_page", 10))
    except (TypeError, ValueError):
        per_page = 10
    if per_page not in {10, 25, 50}:
        per_page = 10
    pagination = list_campaigns(search, status, page, per_page)
    if pagination.pages and page > pagination.pages:
        return redirect(
            url_for(
                "campaigns.index",
                page=pagination.pages,
                q=search,
                status=status,
                per_page=per_page,
            )
        )
    return render_template(
        "campaigns/index.html",
        pagination=pagination,
        search=search,
        status=status,
        per_page=per_page,
        summary=get_campaign_summary(),
        statuses=CAMPAIGN_STATUSES,
    )


@campaigns_blueprint.get("/new")
def new():
    return _render_campaign_form()


@campaigns_blueprint.post("")
def create():
    try:
        campaign = create_campaign(request.form)
    except CampaignValidationError as error:
        return _render_campaign_form(
            form_values=request.form, errors=error.errors, status=422
        )
    flash("Campaign created.", "success")
    return redirect(url_for("campaigns.detail", campaign_id=campaign.id), code=303)


@campaigns_blueprint.get("/<int:campaign_id>")
def detail(campaign_id):
    campaign = _campaign_or_404(campaign_id)
    return render_template(
        "campaigns/detail.html",
        campaign=campaign,
        safe_body_html=sanitize_email_html(campaign.body_html),
        send_errors={},
        send_key=token_urlsafe(32),
        smtp_configured=smtp_is_configured(),
        campaign_has_send=has_campaign_send(campaign.id),
        send_history=list_send_history(10, campaign_id=campaign.id),
        jobs=list_campaign_jobs(campaign.id),
        campaign_job_summary=campaign_job_summary,
        workspace_settings=db.session.get(WorkspaceSettings, 1),
    )


@campaigns_blueprint.post("/<int:campaign_id>/send")
def send(campaign_id):
    campaign = _campaign_or_404(campaign_id)
    try:
        job, created = queue_campaign_send(campaign, request.form)
    except SendValidationError as validation_error:
        return render_template(
            "campaigns/detail.html",
            campaign=campaign,
            safe_body_html=sanitize_email_html(campaign.body_html),
            send_errors=validation_error.errors,
            send_key=request.form.get("submission_key") or token_urlsafe(32),
            smtp_configured=smtp_is_configured(),
            campaign_has_send=has_campaign_send(campaign.id),
            send_history=list_send_history(10, campaign_id=campaign.id),
            jobs=list_campaign_jobs(campaign.id),
            campaign_job_summary=campaign_job_summary,
            workspace_settings=db.session.get(WorkspaceSettings, 1),
        ), 422
    except CampaignQueueError as error:
        flash(str(error), "error")
    else:
        if created:
            flash(f"Campaign queued for background delivery to {job.total_recipients} saved recipients. SMTP acceptance will be recorded separately from delivery.", "success")
        else:
            flash("This campaign already has a queued, completed, partial, or uncertain send attempt. Review execution history before taking further action.", "warning")
    return redirect(url_for("campaigns.detail", campaign_id=campaign.id), code=303)


@campaigns_blueprint.post("/<int:campaign_id>/authorize-schedule")
def authorize_schedule(campaign_id):
    campaign = _campaign_or_404(campaign_id)
    try:
        job, created = authorize_scheduled_campaign(campaign, request.form)
    except SendValidationError as error:
        flash(next(iter(error.errors.values()), str(error)), "error")
    except CampaignQueueError as error:
        flash(str(error), "error")
    else:
        if created:
            flash(f"Scheduled sending authorized. The worker will queue this campaign at {job.scheduled_for:%Y-%m-%d %H:%M UTC}.", "success")
        else:
            flash("A campaign job or send history already exists. Review it before authorizing again.", "warning")
    return redirect(url_for("campaigns.detail", campaign_id=campaign.id), code=303)


@campaigns_blueprint.route("/<int:campaign_id>/edit", methods=["GET", "POST"])
def edit(campaign_id):
    campaign = _campaign_or_404(campaign_id)
    if request.method == "GET":
        return _render_campaign_form(campaign=campaign)
    try:
        update_campaign(campaign, request.form)
    except CampaignValidationError as error:
        return _render_campaign_form(
            campaign=campaign,
            form_values=request.form,
            errors=error.errors,
            status=422,
        )
    flash("Campaign updated.", "success")
    return redirect(url_for("campaigns.detail", campaign_id=campaign.id), code=303)


@campaigns_blueprint.post("/<int:campaign_id>/delete")
def remove(campaign_id):
    campaign = _campaign_or_404(campaign_id)
    try:
        delete_campaign(campaign)
    except CampaignValidationError as error:
        flash(next(iter(error.errors.values()), str(error)), "error")
        return redirect(url_for("campaigns.detail", campaign_id=campaign.id), code=303)
    flash("Campaign deleted.", "success")
    return redirect(url_for("campaigns.index"), code=303)


@campaigns_blueprint.get("/api/recipients")
def recipient_search():
    search = request.args.get("q", "").strip()[:120]
    return jsonify({"recipients": search_active_recipients(search, limit=40)})


@campaigns_blueprint.get("/groups")
def groups():
    return render_template(
        "campaigns/groups.html",
        groups=list_recipient_groups(),
        group_active_member_count=group_active_member_count,
    )


@campaigns_blueprint.get("/groups/new")
def new_group():
    return _render_group_form()


@campaigns_blueprint.post("/groups")
def create_group():
    try:
        group = create_recipient_group(request.form)
    except CampaignValidationError as error:
        return _render_group_form(form_values=request.form, errors=error.errors, status=422)
    except DuplicateRecipientGroupError as error:
        return _render_group_form(
            form_values=request.form,
            errors={"name": str(error)},
            status=409,
        )
    flash("Recipient group created.", "success")
    return redirect(url_for("campaigns.groups"), code=303)


@campaigns_blueprint.route("/groups/<int:group_id>/edit", methods=["GET", "POST"])
def edit_group(group_id):
    group = _group_or_404(group_id)
    if request.method == "GET":
        return _render_group_form(group=group)
    try:
        update_recipient_group(group, request.form)
    except CampaignValidationError as error:
        return _render_group_form(
            group=group,
            form_values=request.form,
            errors=error.errors,
            status=422,
        )
    except DuplicateRecipientGroupError as error:
        return _render_group_form(
            group=group,
            form_values=request.form,
            errors={"name": str(error)},
            status=409,
        )
    flash("Recipient group updated.", "success")
    return redirect(url_for("campaigns.groups"), code=303)


@campaigns_blueprint.post("/groups/<int:group_id>/delete")
def remove_group(group_id):
    group = _group_or_404(group_id)
    delete_recipient_group(group)
    flash("Recipient group deleted.", "success")
    return redirect(url_for("campaigns.groups"), code=303)
