from nextiva_calls.app import main


def test_main_logs_greeting(capfd):
    main()
    captured = capfd.readouterr()
    assert "Hello from nextiva_calls!" in captured.err
