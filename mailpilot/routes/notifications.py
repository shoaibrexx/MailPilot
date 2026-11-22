from flask import Blueprint, abort, jsonify, redirect, render_template, request, url_for

from ..services.notification_service import (
    clear_notifications,
    delete_notification,
    get_notification,
    list_notifications,
    mark_all_notifications_read,
    notification_to_dict,
    recent_notifications,
    set_notification_read,
    unread_notification_count,
)


notifications_blueprint = Blueprint("notifications", __name__, url_prefix="/notifications")


def _pagination_values():
    try:
        page = max(1, int(request.values.get("page", 1)))
    except (TypeError, ValueError):
        page = 1
    category = str(request.values.get("category", "all") or "all").casefold()
    read_state = str(request.values.get("read_state", "all") or "all").casefold()
    return page, category, read_state


def _redirect_to_history():
    _page, category, read_state = _pagination_values()
    return redirect(
        url_for("notifications.index", category=category, read_state=read_state),
        code=303,
    )


@notifications_blueprint.get("")
@notifications_blueprint.get("/")
def index():
    page, category, read_state = _pagination_values()
    pagination, category, read_state = list_notifications(
        page=page, category=category, read_state=read_state
    )
    if pagination.pages and pagination.page > pagination.pages:
        return redirect(
            url_for(
                "notifications.index",
                page=pagination.pages,
                category=category,
                read_state=read_state,
            )
        )
    return render_template(
        "notifications/index.html",
        pagination=pagination,
        category=category,
        read_state=read_state,
        unread_count=unread_notification_count(),
    )


@notifications_blueprint.get("/api/recent")
def recent():
    return jsonify(
        {
            "notifications": [notification_to_dict(item) for item in recent_notifications(8)],
            "unread_count": unread_notification_count(),
        }
    )


@notifications_blueprint.post("/<int:notification_id>/read")
def mark_read(notification_id):
    notification = get_notification(notification_id)
    if notification is None:
        abort(404)
    set_notification_read(notification, True)
    if request.is_json:
        return jsonify({"saved": True, "unread_count": unread_notification_count()})
    return _redirect_to_history()


@notifications_blueprint.post("/<int:notification_id>/unread")
def mark_unread(notification_id):
    notification = get_notification(notification_id)
    if notification is None:
        abort(404)
    set_notification_read(notification, False)
    if request.is_json:
        return jsonify({"saved": True, "unread_count": unread_notification_count()})
    return _redirect_to_history()


@notifications_blueprint.post("/<int:notification_id>/delete")
def remove(notification_id):
    notification = get_notification(notification_id)
    if notification is None:
        abort(404)
    delete_notification(notification)
    if request.is_json:
        return jsonify({"deleted": True, "unread_count": unread_notification_count()})
    return _redirect_to_history()


@notifications_blueprint.post("/read-all")
def mark_all_read():
    mark_all_notifications_read()
    if request.is_json:
        return jsonify({"saved": True, "unread_count": 0})
    return _redirect_to_history()


@notifications_blueprint.post("/clear")
def clear():
    deleted = clear_notifications()
    if request.is_json:
        return jsonify({"deleted": deleted, "unread_count": 0})
    return redirect(url_for("notifications.index"), code=303)
