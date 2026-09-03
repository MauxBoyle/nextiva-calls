"""Read candidate messages from an IMAP mailbox without changing their flags."""

from __future__ import annotations

import imaplib
from collections.abc import Callable
from dataclasses import dataclass


class MailboxError(RuntimeError):
    """Raised for a mailbox connection or protocol failure."""


class MailboxAuthenticationError(MailboxError):
    """Raised when IMAP rejects the configured credentials."""


@dataclass(frozen=True)
class RawMessage:
    """A raw RFC message and its stable IMAP details."""

    uid: str
    uidvalidity: str
    content: bytes


class ImapMailbox:
    """Small, injectable wrapper around :class:`imaplib.IMAP4_SSL`."""

    def __init__(
        self,
        server: str,
        username: str,
        password: str,
        *,
        connection_factory: Callable[[str], imaplib.IMAP4_SSL] = imaplib.IMAP4_SSL,
    ) -> None:
        self.server = server
        self.username = username
        self.password = password
        self.connection_factory = connection_factory

    def messages(self, sender: str, subject: str) -> list[RawMessage]:
        """Return candidate messages in ascending UID (oldest-first) order."""
        connection = None
        messages: list[RawMessage] = []
        try:
            connection = self.connection_factory(self.server)
            connection.login(self.username, self.password)
            status, _ = connection.select("INBOX", readonly=True)
            if status != "OK":
                raise MailboxError("Could not select the inbox")
            response = connection.response("UIDVALIDITY")
            uidvalidity = _response_text(response) or "unknown"
            status, data = connection.uid(
                "search",
                None,
                "FROM",
                _quoted_search(sender),
                "SUBJECT",
                _quoted_search(subject),
            )
            if status != "OK":
                raise MailboxError("Could not search the inbox")
            uid_values = data[0].split() if data and data[0] else []
            for uid_bytes in sorted(uid_values, key=int):
                uid = uid_bytes.decode("ascii")
                status, fetched = connection.uid("fetch", uid, "(BODY.PEEK[])")
                if status != "OK":
                    raise MailboxError("Could not fetch a matching message")
                content = _message_bytes(fetched)
                if content is None:
                    raise MailboxError("The mail server returned a malformed message")
                messages.append(
                    RawMessage(uid=uid, uidvalidity=uidvalidity, content=content)
                )
        except imaplib.IMAP4.error as error:
            raise MailboxAuthenticationError(
                "IMAP authentication or protocol error"
            ) from error
        except OSError as error:
            raise MailboxError("Could not connect to the IMAP server") from error
        finally:
            if connection is not None:
                try:
                    connection.logout()
                except (imaplib.IMAP4.error, OSError):
                    pass
        return messages


def _response_text(response: tuple[str, list[bytes]] | tuple[None, None]) -> str:
    if response[1]:
        value = response[1][0]
        return (
            value.decode("ascii", errors="replace")
            if isinstance(value, bytes)
            else str(value)
        )
    return ""


def _message_bytes(data: list[object]) -> bytes | None:
    for item in data:
        if isinstance(item, tuple) and len(item) > 1 and isinstance(item[1], bytes):
            return item[1]
    return None


def _quoted_search(value: str) -> str:
    """Quote configured IMAP search text so it cannot alter the query."""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
