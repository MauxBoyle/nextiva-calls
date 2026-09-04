import pytest

from nextiva_calls import app
from nextiva_calls.mailbox import MailboxAuthenticationError
from nextiva_calls.storage import StorageError

REQUIRED_ENV = {
    "EMAIL_USERNAME": "learner@example.test",
    "EMAIL_APP_PASSWORD": "SUPER_SECRET_VALUE",
    "NEXTIVA_EMAIL_SUBJECT": "Daily Report",
    "NEXTIVA_AGENT_LOOKUP_FILE": "agent_lookup.csv",
}


def set_required_env(monkeypatch):
    for name, value in REQUIRED_ENV.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("LOG_FILE", "")


@pytest.mark.parametrize(("import_result", "exit_code"), [(True, 0), (False, 1)])
def test_main_exit_codes(monkeypatch, import_result, exit_code):
    set_required_env(monkeypatch)
    monkeypatch.setattr(app, "run_import", lambda _: import_result)
    assert app.main() == exit_code


def test_main_reports_missing_configuration_without_secret(monkeypatch, capfd):
    set_required_env(monkeypatch)
    monkeypatch.delenv("EMAIL_USERNAME")
    assert app.main() == 1
    output = capfd.readouterr().err
    assert "EMAIL_USERNAME" in output
    assert "SUPER_SECRET_VALUE" not in output


@pytest.mark.parametrize(
    "error",
    [MailboxAuthenticationError("SUPER_SECRET_VALUE"), StorageError("broken state")],
)
def test_main_handles_fatal_failures_with_safe_logging(monkeypatch, capfd, error):
    set_required_env(monkeypatch)

    def fail(_):
        raise error

    monkeypatch.setattr(app, "run_import", fail)
    assert app.main() == 1
    output = capfd.readouterr().err
    assert "SUPER_SECRET_VALUE" not in output


def test_main_hides_unexpected_exception_details(monkeypatch, capfd):
    set_required_env(monkeypatch)

    def fail(_):
        raise RuntimeError("https://ct.nextiva.com/?token=SUPER_SECRET_VALUE")

    monkeypatch.setattr(app, "run_import", fail)
    assert app.main() == 1
    output = capfd.readouterr().err
    assert "SUPER_SECRET_VALUE" not in output
