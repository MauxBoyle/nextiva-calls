import imaplib

import pytest

from nextiva_calls.mailbox import (
    ImapMailbox,
    MailboxAuthenticationError,
    MailboxError,
    _quoted_search,
)


class FakeConnection:
    def __init__(self, *, fail_login=False, fail_fetch=False):
        self.fail_login = fail_login
        self.fail_fetch = fail_fetch
        self.logged_out = False
        self.calls = []

    def login(self, username, password):
        self.calls.append(("login", username, password))
        if self.fail_login:
            raise imaplib.IMAP4.error("bad credentials")

    def select(self, mailbox, readonly=False):
        self.calls.append(("select", mailbox, readonly))
        return "OK", [b"2"]

    def response(self, name):
        return name, [b"55"]

    def uid(self, command, *args):
        self.calls.append((command, *args))
        if command == "search":
            return "OK", [b"20 3"]
        if self.fail_fetch:
            return "NO", []
        uid = args[0]
        return "OK", [(b"header", f"Subject: report {uid}\n\nbody".encode())]

    def logout(self):
        self.logged_out = True


def test_mailbox_uses_peek_orders_uids_and_logs_out():
    connection = FakeConnection()
    mailbox = ImapMailbox(
        "imap.example", "user", "password", connection_factory=lambda _: connection
    )
    messages = list(mailbox.messages("sender", "subject"))
    assert [item.uid for item in messages] == ["3", "20"]
    assert all(item.uidvalidity == "55" for item in messages)
    assert ("select", "INBOX", True) in connection.calls
    assert ("fetch", "3", "(BODY.PEEK[])") in connection.calls
    assert connection.logged_out is True


def test_mailbox_logs_out_after_authentication_and_fetch_failures():
    connection = FakeConnection(fail_login=True)
    with pytest.raises(MailboxAuthenticationError):
        list(
            ImapMailbox(
                "server", "user", "secret", connection_factory=lambda _: connection
            ).messages("from", "subject")
        )
    assert connection.logged_out

    connection = FakeConnection(fail_fetch=True)
    with pytest.raises(MailboxError):
        list(
            ImapMailbox(
                "server", "user", "secret", connection_factory=lambda _: connection
            ).messages("from", "subject")
        )
    assert connection.logged_out


def test_mailbox_search_text_is_safely_quoted():
    assert (
        _quoted_search('report "today" \\ archive')
        == '"report \\"today\\" \\\\ archive"'
    )
