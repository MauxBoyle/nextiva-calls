# nextiva-calls

## Installation

Clone the repository, move into the project directory, and install the dependencies:

```bash
git clone <repository-url>
cd nextiva-calls
uv sync
```

## Usage

Run the application through its CLI entrypoint:

```bash
uv run nextiva_calls
```

Load the development environment explicitly and run the CLI:

```bash
uv run --env-file .env nextiva_calls
```

You can also run it as a Python module:

```bash
uv run python -m nextiva_calls
```

## Environment Variables

`.env.example` is the environment template. Copy it to `.env` for development:

```bash
cp .env.example .env
```

- `LOG_LEVEL` controls console logging. It defaults to `INFO` and is set to `DEBUG` in `.env` for more detail.
- `LOG_FILE` controls the log file path. It defaults to `app.log`.

The application does not load `.env` automatically. Use `uv run --env-file .env` to load the development settings explicitly.

## Testing

Run the tests:

```bash
uv run pytest
```

Run the tests and measure coverage:

```bash
uv run pytest --cov
```

## Documentation

Preview the documentation locally:

```bash
uv run python scripts/serve_docs.py
```

Build the static documentation site:

```bash
uv run mkdocs build
```
