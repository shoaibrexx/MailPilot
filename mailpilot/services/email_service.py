from dataclasses import dataclass, field
from email.message import EmailMessage
from email.policy import SMTP as SMTP_POLICY
from email.utils import formataddr, make_msgid, parseaddr
from html.parser import HTMLParser
import smtplib
import ssl

from email_validator import EmailNotValidError, validate_email


MAX_EMAIL_RECIPIENTS = 100
MAX_SUBJECT_LENGTH = 255
MAX_HTML_LENGTH = 250_000


class MailPilotSMTPError(RuntimeError):
    """SMTP error with safe user-facing text and no server response details."""

    public_message = "The email could not be sent. Check the SMTP settings and try again."


class SMTPConfigurationError(MailPilotSMTPError):
    public_message = "Gmail SMTP is not configured. Set the required MAIL_* values in .env and restart MailPilot."


class SMTPAuthenticationFailure(MailPilotSMTPError):
    public_message = "Gmail SMTP authentication failed. Check MAIL_USERNAME and MAIL_PASSWORD; Gmail usually requires an app password."


class SMTPConnectionFailure(MailPilotSMTPError):
    public_message = "MailPilot could not establish a secure connection to Gmail SMTP. Check network access and try again."


class SMTPDeliveryFailure(MailPilotSMTPError):
    public_message = "Gmail SMTP could not accept the message. Review the address list and try again."


class SMTPDeliveryUncertain(MailPilotSMTPError):
    public_message = "The SMTP connection ended during message submission. Gmail may have accepted the message, so check the sender's Sent folder before retrying."


class SMTPMessageValidationError(ValueError):
    pass


@dataclass(frozen=True)
class SMTPSettings:
    server: str
    port: int
    username: str
    password: str = field(repr=False)
    default_sender: str
    timeout: int


@dataclass(frozen=True)
class DeliveryResult:
    recipient_count: int
    accepted_count: int
    refused_count: int
    message_id: str = ""
    refused: dict = field(default_factory=dict)


def _smtp_settings(config):
    try:
        server = str(config.get("MAIL_SERVER", "smtp.gmail.com") or "").strip().casefold()
        port = int(config.get("MAIL_PORT", 587))
        timeout = int(config.get("MAIL_TIMEOUT", 20))
    except (TypeError, ValueError):
        raise SMTPConfigurationError from None

    username = str(config.get("MAIL_USERNAME", "") or "").strip()
    password = str(config.get("MAIL_PASSWORD", "") or "")
    default_sender = str(config.get("MAIL_DEFAULT_SENDER", "") or "").strip()
    if (
        server != "smtp.gmail.com"
        or port != 587
        or timeout < 1
        or not username
        or not password
        or not default_sender
    ):
        raise SMTPConfigurationError
    return SMTPSettings(server, port, username, password, default_sender, timeout)


def _current_settings(config=None):
    if config is None:
        from flask import current_app

        config = current_app.config
    return _smtp_settings(config)


def smtp_is_configured(config=None):
    try:
        _current_settings(config)
    except SMTPConfigurationError:
        return False
    return True


def _validate_email(value, label):
    try:
        return validate_email(
            str(value or "").strip(),
            check_deliverability=False,
            allow_smtputf8=False,
        ).normalized
    except EmailNotValidError:
        raise SMTPMessageValidationError(f"Enter a valid {label} email address.") from None


def _sender_header(value):
    raw = str(value or "").strip()
    if not raw or any(ord(char) < 32 or ord(char) == 127 for char in raw):
        raise SMTPConfigurationError
    name, address = parseaddr(raw)
    address = _validate_email(address, "sender")
    clean_name = " ".join(name.split())[:120]
    return address, formataddr((clean_name, address)) if clean_name else address


def _normalize_recipients(values, label):
    normalized = []
    for value in values or []:
        address = _validate_email(value, label)
        if address.casefold() not in {item.casefold() for item in normalized}:
            normalized.append(address)
    return normalized


class _PlainTextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag.casefold() in {"br", "div", "li", "p", "blockquote", "h1", "h2", "h3"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag.casefold() in {"div", "li", "p", "blockquote", "h1", "h2", "h3"}:
            self.parts.append("\n")

    def handle_data(self, data):
        self.parts.append(data)

    def text(self):
        lines = [" ".join(line.split()) for line in "".join(self.parts).splitlines()]
        return "\n".join(line for line in lines if line).strip()


def _plain_text(html_body):
    parser = _PlainTextExtractor()
    parser.feed(html_body)
    parser.close()
    return parser.text()


def _close_client(client):
    if client is None:
        return
    try:
        client.quit()
    except Exception:
        try:
            client.close()
        except Exception:
            pass


def _open_authenticated_client(settings):
    client = None
    try:
        client = smtplib.SMTP(settings.server, settings.port, timeout=settings.timeout)
        client.ehlo()
        client.starttls(context=ssl.create_default_context())
        client.ehlo()
        client.login(settings.username, settings.password)
        return client
    except smtplib.SMTPAuthenticationError:
        _close_client(client)
        raise SMTPAuthenticationFailure from None
    except (smtplib.SMTPException, OSError, TimeoutError):
        _close_client(client)
        raise SMTPConnectionFailure from None


def test_smtp_connection(config=None):
    """Authenticate to Gmail SMTP, then close; this never sends a message."""
    settings = _current_settings(config)
    client = _open_authenticated_client(settings)
    _close_client(client)
    return True


def send_html_email(*, to=None, cc=None, bcc=None, subject, body_html, headers=None, message_id=None, config=None):
    settings = _current_settings(config)
    to_addresses = _normalize_recipients(to, "recipient")
    cc_addresses = _normalize_recipients(cc, "recipient")
    bcc_addresses = _normalize_recipients(bcc, "recipient")

    all_addresses = []
    for address in (*to_addresses, *cc_addresses, *bcc_addresses):
        normalized = address.casefold()
        if normalized in {item.casefold() for item in all_addresses}:
            raise SMTPMessageValidationError("A recipient appears more than once across To, Cc, or Bcc.")
        all_addresses.append(address)
    if not all_addresses:
        raise SMTPMessageValidationError("Add at least one recipient before sending.")
    if len(all_addresses) > MAX_EMAIL_RECIPIENTS:
        raise SMTPMessageValidationError(f"A message can be sent to at most {MAX_EMAIL_RECIPIENTS} recipients.")

    subject = str(subject or "").strip()
    if (
        not subject
        or len(subject) > MAX_SUBJECT_LENGTH
        or any(ord(char) < 32 or ord(char) == 127 for char in subject)
    ):
        raise SMTPMessageValidationError("Enter a valid subject of 255 characters or fewer.")
    body_html = str(body_html or "")
    if len(body_html) > MAX_HTML_LENGTH:
        raise SMTPMessageValidationError("Email content is too large to send.")
    plain_body = _plain_text(body_html)
    if not plain_body:
        raise SMTPMessageValidationError("Add message text before sending.")

    sender_address, sender_header = _sender_header(settings.default_sender)
    message = EmailMessage(policy=SMTP_POLICY)
    message["From"] = sender_header
    message_id = str(message_id or make_msgid()).strip()
    if len(message_id) > 255 or not message_id or any(ord(char) < 32 or ord(char) == 127 for char in message_id):
        raise SMTPMessageValidationError("The message identifier is invalid.")
    message["Message-ID"] = message_id
    if to_addresses:
        message["To"] = ", ".join(to_addresses)
    else:
        message["To"] = "undisclosed-recipients:;"
    if cc_addresses:
        message["Cc"] = ", ".join(cc_addresses)
    message["Subject"] = subject
    for name, value in (headers or {}).items():
        if name not in {"List-Unsubscribe", "List-Unsubscribe-Post"}:
            raise SMTPMessageValidationError("Unsupported email header.")
        value = str(value or "")
        if not value or any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise SMTPMessageValidationError("An email header contains invalid characters.")
        message[name] = value
    message.set_content(plain_body)
    message.add_alternative(body_html, subtype="html")

    client = _open_authenticated_client(settings)
    try:
        refused = client.send_message(
            message,
            from_addr=sender_address,
            to_addrs=all_addresses,
        )
    except smtplib.SMTPRecipientsRefused as error:
        _close_client(client)
        refused_count = len(getattr(error, "recipients", {}) or {}) or len(all_addresses)
        return DeliveryResult(len(all_addresses), 0, refused_count, message_id, getattr(error, "recipients", {}) or {})
    except Exception:
        _close_client(client)
        raise SMTPDeliveryUncertain from None
    _close_client(client)

    refused_count = len(refused or {})
    return DeliveryResult(
        recipient_count=len(all_addresses),
        accepted_count=max(0, len(all_addresses) - refused_count),
        refused_count=refused_count,
        message_id=message_id,
        refused=refused or {},
    )
