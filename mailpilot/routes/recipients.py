from flask import (
    Blueprint,
    abort,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from werkzeug.exceptions import RequestEntityTooLarge

from ..services.recipient_import_service import (
    ImportConflictError,
    InvalidRecipientCSV,
    UploadTooLarge,
    import_recipient_csv,
)
from ..services.notification_service import record_import_summary
from ..services.recipient_service import (
    DuplicateRecipientError,
    RecipientValidationError,
    create_recipient,
    delete_recipient,
    get_recipient,
    get_recipient_summary,
    list_recipients,
    set_recipient_active,
    update_recipient,
)


recipients_blueprint = Blueprint("recipients", __name__, url_prefix="/recipients")


def _render_form(*, recipient=None, form_values=None, errors=None, status=200):
    is_edit = recipient is not None
    return (
        render_template(
            "recipients/form.html",
            recipient=recipient,
            form_values=form_values or {},
            errors=errors or {},
            form_title="Edit recipient" if is_edit else "Add recipient",
            submit_label="Save changes" if is_edit else "Add recipient",
        ),
        status,
    )


def _get_or_404(recipient_id):
    recipient = get_recipient(recipient_id)
    if recipient is None:
        abort(404)
    return recipient


@recipients_blueprint.get("")
def index():
    search = request.args.get("q", "").strip()[:120]
    status = request.args.get("status", "all").casefold()
    if status not in {"all", "active", "inactive"}:
        status = "all"

    try:
        page = max(1, int(request.args.get("page", 1)))
    except (TypeError, ValueError):
        page = 1
    try:
        per_page = int(request.args.get("per_page", 10))
    except (TypeError, ValueError):
        per_page = 10
    if per_page not in {10, 25, 50}:
        per_page = 10

    pagination = list_recipients(search, status, page, per_page)
    if pagination.pages and page > pagination.pages:
        return redirect(
            url_for(
                "recipients.index",
                page=pagination.pages,
                q=search,
                status=status,
                per_page=per_page,
            ),
            code=302,
        )
    summary = get_recipient_summary()
    return render_template(
        "recipients/index.html",
        pagination=pagination,
        search=search,
        status=status,
        per_page=per_page,
        summary=summary,
    )


@recipients_blueprint.route("/new", methods=["GET", "POST"])
def new_recipient():
    if request.method == "GET":
        return _render_form(form_values={"is_active": "active"})

    try:
        recipient = create_recipient(request.form)
    except RecipientValidationError as error:
        return _render_form(
            form_values=request.form, errors=error.errors, status=422
        )
    except DuplicateRecipientError as error:
        return _render_form(
            form_values=request.form,
            errors={"email": str(error)},
            status=409,
        )

    flash(f"{recipient.email} was added to your recipients.", "success")
    return redirect(url_for("recipients.index"), code=303)


@recipients_blueprint.get("/<int:recipient_id>")
def detail(recipient_id):
    return render_template("recipients/detail.html", recipient=_get_or_404(recipient_id))


@recipients_blueprint.route("/<int:recipient_id>/edit", methods=["GET", "POST"])
def edit(recipient_id):
    recipient = _get_or_404(recipient_id)
    if request.method == "GET":
        return _render_form(
            recipient=recipient,
            form_values={
                "first_name": recipient.first_name or "",
                "last_name": recipient.last_name or "",
                "email": recipient.email,
                "company": recipient.company or "",
                "is_active": "active" if recipient.is_active else "inactive",
            },
        )

    try:
        update_recipient(recipient, request.form)
    except RecipientValidationError as error:
        return _render_form(
            recipient=recipient,
            form_values=request.form,
            errors=error.errors,
            status=422,
        )
    except DuplicateRecipientError as error:
        return _render_form(
            recipient=recipient,
            form_values=request.form,
            errors={"email": str(error)},
            status=409,
        )

    flash(f"{recipient.email} was updated.", "success")
    return redirect(url_for("recipients.detail", recipient_id=recipient.id), code=303)


@recipients_blueprint.post("/<int:recipient_id>/toggle-status")
def toggle_status(recipient_id):
    recipient = _get_or_404(recipient_id)
    set_recipient_active(recipient, not recipient.is_active)
    state = "activated" if recipient.is_active else "deactivated"
    flash(f"{recipient.email} was {state}.", "success")
    return redirect(url_for("recipients.index"), code=303)


@recipients_blueprint.post("/<int:recipient_id>/delete")
def remove(recipient_id):
    recipient = _get_or_404(recipient_id)
    email = recipient.email
    delete_recipient(recipient)
    flash(f"{email} was deleted.", "success")
    return redirect(url_for("recipients.index"), code=303)


@recipients_blueprint.route("/import", methods=["GET", "POST"])
def import_csv():
    if request.method == "GET":
        return render_template("recipients/import.html", summary=None, error=None)

    uploaded_file = request.files.get("file")
    if uploaded_file is None:
        return render_template(
            "recipients/import.html",
            summary=None,
            error="Choose a CSV file to import.",
        ), 422

    try:
        summary = import_recipient_csv(uploaded_file)
    except UploadTooLarge as error:
        return render_template(
            "recipients/import.html", summary=None, error=str(error)
        ), 413
    except InvalidRecipientCSV as error:
        return render_template(
            "recipients/import.html", summary=None, error=str(error)
        ), 422
    except ImportConflictError as error:
        return render_template(
            "recipients/import.html", summary=None, error=str(error)
        ), 409

    record_import_summary(summary)
    return render_template(
        "recipients/import.html", summary=summary, error=None
    ), 200


@recipients_blueprint.errorhandler(RequestEntityTooLarge)
def import_upload_too_large(_error):
    maximum_bytes = request.max_content_length or 0
    return render_template(
        "recipients/import.html",
        summary=None,
        error=f"The CSV exceeds the {maximum_bytes}-byte upload limit.",
    ), 413
