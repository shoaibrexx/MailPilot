import csv
import io
from pathlib import Path
from uuid import uuid4

from flask import current_app
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from werkzeug.utils import secure_filename

from ..extensions import db
from ..models import Recipient
from .recipient_service import validate_recipient_data


class InvalidRecipientCSV(ValueError):
    """Raised when an uploaded file has an unsupported CSV structure."""


class UploadTooLarge(ValueError):
    """Raised when a file exceeds the configured upload size limit."""


class ImportConflictError(ValueError):
    """Raised when concurrent changes conflict with the import transaction."""


def _normalize_header(header):
    return "_".join(str(header).strip().casefold().replace("-", " ").split())


def _field_indexes(headers):
    normalized = [_normalize_header(header) for header in headers]
    if not normalized or any(not header for header in normalized):
        raise InvalidRecipientCSV("The CSV must have non-empty column headers.")
    if len(set(normalized)) != len(normalized):
        raise InvalidRecipientCSV("The CSV cannot contain duplicate headers.")
    if "email" not in normalized:
        raise InvalidRecipientCSV("The CSV must include an 'email' column.")

    columns = {name: index for index, name in enumerate(normalized)}
    aliases = {
        "email": ("email",),
        "first_name": ("first_name", "firstname", "given_name", "name"),
        "last_name": ("last_name", "lastname", "family_name", "surname"),
        "company": ("company", "organization", "organisation"),
        "is_active": ("is_active", "status"),
    }
    indexes = {}
    for field, names in aliases.items():
        for name in names:
            if name in columns:
                indexes[field] = columns[name]
                break
    return indexes


def _load_existing_emails(emails):
    existing = set()
    ordered = list(emails)
    for start in range(0, len(ordered), 500):
        batch = ordered[start : start + 500]
        rows = db.session.scalars(
            select(Recipient.email).where(Recipient.email.in_(batch))
        )
        existing.update(email.casefold() for email in rows)
    return existing


def import_recipient_csv(uploaded_file):
    """Validate, summarize and import CSV rows; keep the upload private."""
    safe_original_name = secure_filename(uploaded_file.filename or "")
    if not safe_original_name or Path(safe_original_name).suffix.casefold() != ".csv":
        raise InvalidRecipientCSV("Only CSV files are accepted.")

    max_bytes = current_app.config["MAX_CONTENT_LENGTH"]
    payload = uploaded_file.stream.read(max_bytes + 1)
    if len(payload) > max_bytes:
        raise UploadTooLarge(f"Upload exceeds the {max_bytes}-byte limit.")
    if not payload:
        raise InvalidRecipientCSV("The CSV file is empty.")

    try:
        text_content = payload.decode("utf-8-sig")
        reader = csv.reader(io.StringIO(text_content, newline=""), strict=True)
        headers = next(reader, None)
        if not headers:
            raise InvalidRecipientCSV("The CSV file must include a header row.")
        indexes = _field_indexes(headers)

        total_rows = 0
        invalid_records = 0
        duplicate_records = 0
        seen_in_file = set()
        valid_records = []

        for row in reader:
            if not row or all(not cell.strip() for cell in row):
                continue
            total_rows += 1
            if total_rows > current_app.config["MAX_UPLOAD_ROWS"]:
                raise InvalidRecipientCSV("The CSV contains too many recipient rows.")
            if len(row) != len(headers):
                invalid_records += 1
                continue

            record = {
                field: row[index]
                for field, index in indexes.items()
            }
            if "is_active" not in record or not record["is_active"].strip():
                record["is_active"] = "active"
            values, errors = validate_recipient_data(record)
            if errors:
                invalid_records += 1
                continue
            email = values["email"]
            if email in seen_in_file:
                duplicate_records += 1
                continue
            seen_in_file.add(email)
            valid_records.append(values)
    except UnicodeDecodeError as error:
        raise InvalidRecipientCSV("The CSV must use UTF-8 text encoding.") from error
    except csv.Error as error:
        raise InvalidRecipientCSV("The CSV file could not be parsed.") from error

    existing_emails = _load_existing_emails(
        record["email"] for record in valid_records
    )
    recipients_to_add = []
    for record in valid_records:
        if record["email"] in existing_emails:
            duplicate_records += 1
            continue
        existing_emails.add(record["email"])
        recipients_to_add.append(Recipient(**record))

    upload_directory = Path(current_app.config["UPLOAD_FOLDER"])
    upload_directory.mkdir(parents=True, exist_ok=True)
    stored_name = f"recipients-{uuid4().hex}.csv"
    destination = upload_directory / stored_name
    with destination.open("xb") as saved_file:
        saved_file.write(payload)

    if recipients_to_add:
        try:
            db.session.add_all(recipients_to_add)
            db.session.commit()
        except IntegrityError as error:
            db.session.rollback()
            destination.unlink(missing_ok=True)
            raise ImportConflictError(
                "A recipient with one of these email addresses was added during import. Please retry."
            ) from error

    imported = len(recipients_to_add)
    return {
        "upload_id": stored_name,
        "total_rows": total_rows,
        "rows_received": total_rows,
        "imported": imported,
        "duplicates": duplicate_records,
        "invalid": invalid_records,
    }


def validate_and_store_recipient_csv(uploaded_file):
    """Compatibility wrapper for callers using the original upload service."""
    result = import_recipient_csv(uploaded_file)
    return result["upload_id"], result["total_rows"]
