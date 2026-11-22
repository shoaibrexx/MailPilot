import csv
from io import StringIO

from flask import Blueprint, Response, redirect, render_template, request, url_for

from ..services.analytics_service import (
    DATE_RANGES,
    REPORT_PAGE_SIZES,
    csv_export_summary_rows,
    get_analytics_data,
    get_campaign_execution_stats,
    get_campaign_report_rows,
    normalize_date_range,
)


analytics_blueprint = Blueprint("analytics", __name__, url_prefix="/analytics")


def _positive_int(value, fallback):
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return fallback


def _safe_csv_value(value):
    if not isinstance(value, str):
        return value
    if value.lstrip().startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + value
    return value


@analytics_blueprint.get("")
@analytics_blueprint.get("/")
def index():
    range_key = normalize_date_range(request.args.get("range"))
    per_page = _positive_int(request.args.get("per_page"), 10)
    if per_page not in REPORT_PAGE_SIZES:
        per_page = 10
    data = get_analytics_data(
        range_key,
        page=_positive_int(request.args.get("page"), 1),
        per_page=per_page,
    )
    pagination = data["report_pagination"]
    if pagination.pages and pagination.page > pagination.pages:
        return redirect(
            url_for("analytics.index", range=range_key, per_page=per_page, page=pagination.pages)
        )
    return render_template(
        "analytics/index.html",
        data=data,
        ranges=DATE_RANGES,
        per_page=per_page,
    )


@analytics_blueprint.get("/export.csv")
def export_csv():
    range_key = normalize_date_range(request.args.get("range"))
    data = get_analytics_data(range_key, page=1, per_page=10)
    output = StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["MailPilot analytics report", data["range_label"]])
    writer.writerow(["Metric scope", "Database totals are current; trends and outcome counts use the selected range"])
    writer.writerow([])
    writer.writerow(["Section", "Period", "Metric", "Value"])
    for row in csv_export_summary_rows(data):
        writer.writerow([_safe_csv_value(value) for value in row])
    for point in data["campaign_activity"]:
        writer.writerow(["campaign activity", point["label"], "Campaigns created", point["value"]])
    for point in data["recipient_growth"]:
        writer.writerow(["recipient growth", point["label"], "Cumulative recipients", point["value"]])
    for item in data["status_distribution"]:
        writer.writerow(["status distribution", data["range_label"], item["label"], item["value"]])
    for point in data["sending_activity"]:
        writer.writerow(["sending activity", point["label"], "SMTP accepted messages", point["accepted"]])
        writer.writerow(["sending activity", point["label"], "Known failed attempts", point["failed"]])
        writer.writerow(["sending activity", point["label"], "Partial outcomes", point["partial"]])
        writer.writerow(["sending activity", point["label"], "Uncertain outcomes", point["uncertain"]])
    writer.writerow(["delivery confirmation", data["range_label"], "Unavailable", "SMTP acceptance does not confirm inbox delivery; opens and clicks are not tracked"])
    writer.writerow([])
    writer.writerow([
        "campaign",
        "subject",
        "status",
        "recipient count",
        "SMTP attempts",
        "SMTP accepted",
        "known failures",
        "uncertain outcomes",
        "pending recipients",
        "suppressed or inactive exclusions",
        "permanent bounces",
        "temporary bounces",
        "scheduled at",
        "created at",
    ])
    campaigns = get_campaign_report_rows(range_key)
    campaign_metrics = get_campaign_execution_stats([campaign.id for campaign in campaigns], range_key)
    for campaign in campaigns:
        metrics = campaign_metrics[campaign.id]
        writer.writerow([
            _safe_csv_value(campaign.name),
            _safe_csv_value(campaign.subject),
            campaign.status_label,
            campaign.recipient_count,
            metrics["attempts"],
            metrics["accepted"],
            metrics["failed"],
            metrics["uncertain"],
            metrics["pending"],
            metrics["suppressed_excluded"],
            metrics["permanent_bounces"],
            metrics["temporary_bounces"],
            campaign.scheduled_at.isoformat(sep=" ") if campaign.scheduled_at else "",
            campaign.created_at.isoformat(sep=" ") if campaign.created_at else "",
        ])

    response = Response("\ufeff" + output.getvalue(), content_type="text/csv; charset=utf-8")
    response.headers["Content-Disposition"] = f'attachment; filename="mailpilot-analytics-{range_key}.csv"'
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response
