"""Parse email messages and securely identify Nextiva report links."""

from __future__ import annotations

import re
from email.header import decode_header, make_header
from email.message import Message
from html.parser import HTMLParser
from urllib.parse import urlsplit


class MessageError(ValueError):
    """Raised when a matching message has no unambiguous, safe report link."""


class _LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() != "a":
            return
        for name, value in attrs:
            if name.casefold() == "href" and value:
                self._href = value.strip()
                self._text = []
                return

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() != "a" or self._href is None:
            return
        self.links.append((self._href, "".join(self._text)))
        self._href = None
        self._text = []


_TEXT_URL = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)


def decoded_header(message: Message, name: str) -> str:
    """Decode an RFC email header into readable text."""
    raw_value = message.get(name, "")
    try:
        return str(make_header(decode_header(raw_value))).strip()
    except (LookupError, UnicodeError):
        return str(raw_value).strip()


def _decode_part(part: Message) -> str:
    payload = part.get_payload(decode=True)
    if payload is None:
        plain_payload = part.get_payload()
        return plain_payload if isinstance(plain_payload, str) else ""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except LookupError:
        return payload.decode("utf-8", errors="replace")


def message_body(message: Message) -> tuple[str, bool]:
    """Return the preferred body and whether it is HTML.

    HTML is preferred over plain text, and attachments are always ignored.
    """
    html_parts: list[str] = []
    text_parts: list[str] = []
    parts = message.walk() if message.is_multipart() else (message,)
    for part in parts:
        if part.is_multipart() or part.get_filename() is not None:
            continue
        content_type = part.get_content_type().casefold()
        if content_type == "text/html":
            html_parts.append(_decode_part(part))
        elif content_type == "text/plain":
            text_parts.append(_decode_part(part))
    if html_parts:
        return "\n".join(html_parts), True
    return "\n".join(text_parts), False


def _candidate_urls(body: str, is_html: bool) -> list[str]:
    if is_html:
        parser = _LinkParser()
        parser.feed(body)
        report_links = [
            href
            for href, text in parser.links
            if " ".join(text.split()).casefold() == "missed calls"
        ]
        if len(report_links) != 1:
            raise MessageError("HTML message must contain exactly one Missed Calls link")
        return report_links
    return [match.group(0).rstrip(".,);]") for match in _TEXT_URL.finditer(body)]


def extract_report_url(message: Message, allowed_hosts: frozenset[str]) -> str:
    """Return one HTTPS URL hosted by an exactly allowlisted hostname."""
    body, is_html = message_body(message)
    candidates = _candidate_urls(body, is_html)
    safe: list[str] = []
    nextiva_like_invalid = False
    for candidate in candidates:
        try:
            parsed = urlsplit(candidate)
            hostname = (parsed.hostname or "").lower().rstrip(".")
            port_is_default = parsed.port in (None, 443)
            has_credentials = parsed.username is not None or parsed.password is not None
        except ValueError:
            continue
        if hostname in allowed_hosts:
            if (
                parsed.scheme.casefold() == "https"
                and port_is_default
                and not has_credentials
            ):
                safe.append(candidate)
            else:
                nextiva_like_invalid = True
    unique = list(dict.fromkeys(safe))
    if len(unique) > 1:
        raise MessageError("Message contains multiple allowed report links")
    if len(unique) == 1:
        return unique[0]
    if nextiva_like_invalid:
        raise MessageError("Report link must use HTTPS on the default port")
    raise MessageError("Message does not contain an allowlisted report link")


def report_identifier(
    message: Message, *, server: str, uidvalidity: str, uid: str
) -> str:
    """Use Message-ID when available, otherwise build a stable IMAP UID key."""
    message_id = decoded_header(message, "Message-ID")
    if message_id:
        return f"message-id:{message_id.casefold()}"
    return f"imap:{server.casefold()}:INBOX:{uidvalidity}:{uid}"
