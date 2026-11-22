from flask import Blueprint, Response, abort, render_template, request

from ..extensions import csrf
from ..services.unsubscribe_service import apply_unsubscribe, get_unsubscribe_token


unsubscribe_blueprint = Blueprint("unsubscribe", __name__)


@unsubscribe_blueprint.get("/unsubscribe/<token>")
def landing(token):
    record = get_unsubscribe_token(token)
    if record is None:
        abort(404)
    response = render_template("unsubscribe/landing.html", campaign_id=record.campaign_id)
    return Response(
        response,
        status=200,
        headers={
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer",
            "X-Robots-Tag": "noindex, nofollow",
        },
    )


@unsubscribe_blueprint.post("/unsubscribe/<token>")
@csrf.exempt
def process(token):
    raw_header_value = request.form.get("List-Unsubscribe", "")
    one_click = raw_header_value == "One-Click"
    scope = "global" if one_click else str(request.form.get("scope", "global") or "global")
    suppression = apply_unsubscribe(token, scope=scope)
    if suppression is None:
        abort(404)
    response = Response("You have been unsubscribed.", status=200, mimetype="text/plain")
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    return response
