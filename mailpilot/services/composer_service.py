import re
from html import escape
from html.parser import HTMLParser
from urllib.parse import urlsplit

from email_validator import EmailNotValidError, validate_email

from ..extensions import db
from ..models import Campaign, EmailDraft, EmailSendHistory
from .recipient_service import list_recipients
from .settings_service import get_settings


MAX_DRAFT_BODY_LENGTH = 250_000
MAX_DRAFT_RECIPIENTS = 100
SENDER_IDENTITIES = {
    "workspace": "MailPilot Workspace",
    "personal": "Personal profile",
    "support": "Support profile",
}

ALLOWED_TAGS = {
    "a", "blockquote", "br", "div", "em", "h1", "h2", "h3", "hr",
    "i", "li", "ol", "p", "span", "strong", "u", "ul",
}
DROP_CONTENT_TAGS = {"iframe", "math", "object", "script", "style", "svg", "template"}
VOID_TAGS = {"br", "hr"}
ALIGNMENT_TAGS = {"blockquote", "div", "h1", "h2", "h3", "li", "p", "span"}
ALLOWED_ALIGNMENTS = {"left", "center", "right", "justify"}
ADDRESS_SEPARATOR = re.compile(r"[,;\r\n]+")


class DraftValidationError(ValueError):
    def __init__(self, errors):
        self.errors = errors
        super().__init__("Please correct the highlighted draft fields.")


def _safe_link(value):
    value = (value or "").strip()
    if not value or any(ord(char) < 32 for char in value):
        return None
    if value.startswith("#"):
        return value
    scheme = urlsplit(value).scheme.casefold()
    return value if scheme in {"http", "https", "mailto"} else None


def _safe_alignment(value):
    for declaration in (value or "").split(";"):
        if ":" not in declaration:
            continue
        name, setting = declaration.split(":", 1)
        if name.strip().casefold() == "text-align":
            setting = setting.strip().casefold()
            if setting in ALLOWED_ALIGNMENTS:
                return setting
    return None


class _EmailHTMLSanitizer(HTMLParser):
    """Small allowlist sanitizer for the limited email editor feature set."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.open_tags = []
        self.drop_stack = []

    def handle_starttag(self, tag, attrs):
        tag = tag.casefold()
        if self.drop_stack:
            if tag in DROP_CONTENT_TAGS:
                self.drop_stack.append(tag)
            return
        if tag in DROP_CONTENT_TAGS:
            self.drop_stack.append(tag)
            return
        if tag not in ALLOWED_TAGS:
            return

        safe_attrs = []
        attr_map = {name.casefold(): value for name, value in attrs if name}
        if tag == "a":
            href = _safe_link(attr_map.get("href"))
            if href:
                safe_attrs.append(("href", href))
            if attr_map.get("title"):
                safe_attrs.append(("title", attr_map["title"][:200]))
        if tag in ALIGNMENT_TAGS:
            alignment = _safe_alignment(attr_map.get("style"))
            if alignment:
                safe_attrs.append(("style", f"text-align: {alignment}"))

        serialized_attrs = "".join(
            f' {name}="{escape(value, quote=True)}"' for name, value in safe_attrs
        )
        self.parts.append(f"<{tag}{serialized_attrs}>")
        if tag not in VOID_TAGS:
            self.open_tags.append(tag)

    def handle_startendtag(self, tag, attrs):
        tag = tag.casefold()
        if tag in VOID_TAGS:
            self.handle_starttag(tag, attrs)
        elif tag not in DROP_CONTENT_TAGS:
            self.handle_starttag(tag, attrs)
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        tag = tag.casefold()
        if self.drop_stack:
            if tag == self.drop_stack[-1]:
                self.drop_stack.pop()
            return
        if tag not in self.open_tags:
            return
        index = len(self.open_tags) - 1 - self.open_tags[::-1].index(tag)
        for open_tag in reversed(self.open_tags[index:]):
            self.parts.append(f"</{open_tag}>")
        del self.open_tags[index:]

    def handle_data(self, data):
        if not self.drop_stack:
            self.parts.append(escape(data, quote=False))

    def get_html(self):
        for open_tag in reversed(self.open_tags):
            self.parts.append(f"</{open_tag}>")
        self.open_tags.clear()
        return "".join(self.parts)


class _TextContentExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)

    def get_text(self):
        return "".join(self.parts)


def sanitize_email_html(value):
    parser = _EmailHTMLSanitizer()
    parser.feed(value or "")
    parser.close()
    return parser.get_html()


def _normalize_address_field(raw_value, field, errors):
    raw_value = str(raw_value or "")
    if len(raw_value) > 10_000:
        errors[field] = "Recipient field is too long."
        return []

    addresses = []
    for address in (part.strip() for part in ADDRESS_SEPARATOR.split(raw_value)):
        if not address:
            continue
        try:
            normalized = validate_email(
                address,
                check_deliverability=False,
                allow_smtputf8=False,
            ).normalized.casefold()
        except EmailNotValidError:
            errors[field] = "Enter valid email addresses separated by commas."
            continue
        if normalized in addresses:
            errors[field] = "The same address appears more than once in this field."
            continue
        addresses.append(normalized)
        if len(addresses) > MAX_DRAFT_RECIPIENTS:
            errors[field] = f"A draft can contain at most {MAX_DRAFT_RECIPIENTS} addresses per field."
            return []
    return addresses


def validate_draft_data(payload):
    errors = {}
    sender_identity = str(payload.get("sender_identity", "") or "").strip()
    if sender_identity not in SENDER_IDENTITIES:
        errors["sender_identity"] = "Choose a valid sender profile."

    subject = str(payload.get("subject", "") or "").strip()
    if not subject:
        errors["subject"] = "Subject is required."
    elif len(subject) > 255:
        errors["subject"] = "Subject must be 255 characters or fewer."
    elif any(character in subject for character in "\r\n\x00"):
        errors["subject"] = "Subject cannot contain line breaks or control characters."

    raw_body = str(payload.get("body_html", "") or "")
    if len(raw_body) > MAX_DRAFT_BODY_LENGTH:
        errors["body_html"] = "Draft content is too large. Keep it under 250,000 characters."

    safe_body = "" if len(raw_body) > MAX_DRAFT_BODY_LENGTH else sanitize_email_html(raw_body)
    if len(raw_body) <= MAX_DRAFT_BODY_LENGTH:
        text_content = _TextContentExtractor()
        text_content.feed(safe_body)
        text_content.close()
        if not text_content.get_text().strip():
            errors["body_html"] = "Add some message text before saving this draft."

    values = {
        "sender_identity": sender_identity,
        "to_recipients": _normalize_address_field(
            payload.get("to_recipients"), "to_recipients", errors
        ),
        "cc_recipients": _normalize_address_field(
            payload.get("cc_recipients"), "cc_recipients", errors
        ),
        "bcc_recipients": _normalize_address_field(
            payload.get("bcc_recipients"), "bcc_recipients", errors
        ),
        "subject": subject,
        "body_html": safe_body,
    }

    seen = {}
    for field in ("to_recipients", "cc_recipients", "bcc_recipients"):
        for address in values[field]:
            if address in seen:
                errors[field] = (
                    f"{address} is already listed in {seen[address].upper()}."
                )
            else:
                seen[address] = "to" if field == "to_recipients" else (
                    "cc" if field == "cc_recipients" else "bcc"
                )

    has_recipient_error = any(
        field in errors for field in ("to_recipients", "cc_recipients", "bcc_recipients")
    )
    if not seen and not has_recipient_error:
        errors["to_recipients"] = "Add at least one recipient before saving this draft."

    return values, errors


def create_draft(payload):
    values, errors = validate_draft_data(payload)
    if errors:
        raise DraftValidationError(errors)
    draft = EmailDraft(**values)
    db.session.add(draft)
    db.session.commit()
    return draft


def update_draft(draft, payload):
    values, errors = validate_draft_data(payload)
    if errors:
        raise DraftValidationError(errors)
    for key, value in values.items():
        setattr(draft, key, value)
    db.session.commit()
    return draft


def delete_draft(draft):
    db.session.execute(
        db.update(EmailSendHistory)
        .where(EmailSendHistory.draft_id == draft.id)
        .values(draft_id=None)
    )
    db.session.execute(
        db.update(Campaign)
        .where(Campaign.email_draft_id == draft.id)
        .values(email_draft_id=None)
    )
    db.session.delete(draft)
    db.session.commit()


def get_draft(draft_id):
    return db.session.get(EmailDraft, draft_id)


def list_drafts(limit=20):
    limit = max(1, min(int(limit), 100))
    return db.session.scalars(
        db.select(EmailDraft)
        .order_by(EmailDraft.updated_at.desc(), EmailDraft.id.desc())
        .limit(limit)
    ).all()


def draft_form_values(draft=None):
    if draft is None:
        settings = get_settings()
        starter_blocks = []
        for text in (settings.email_signature, settings.email_footer):
            if text:
                starter_blocks.append(
                    "<p>" + "<br>".join(escape(line) for line in text.splitlines()) + "</p>"
                )
        return {
            "sender_identity": "workspace",
            "to_recipients": "",
            "cc_recipients": settings.default_cc,
            "bcc_recipients": settings.default_bcc,
            "subject": "",
            "body_html": "".join(starter_blocks),
        }
    return {
        "sender_identity": draft.sender_identity,
        "to_recipients": ", ".join(draft.to_recipients or []),
        "cc_recipients": ", ".join(draft.cc_recipients or []),
        "bcc_recipients": ", ".join(draft.bcc_recipients or []),
        "subject": draft.subject,
        "body_html": draft.body_html,
    }


def active_contact_suggestions(search="", limit=30):
    limit = max(1, min(int(limit), 50))
    page = list_recipients(search=search, status="active", page=1, per_page=limit)
    return [
        {
            "id": recipient.id,
            "name": recipient.full_name,
            "email": recipient.email,
            "company": recipient.company or "",
        }
        for recipient in page.items
    ]


def sender_profile_label(identity):
    return SENDER_IDENTITIES.get(identity, SENDER_IDENTITIES["workspace"])


