# aisc-salesforce

## Installation

Clone the repository, then install the project and its dependencies:

```bash
uv sync
```

## Usage

Run the application via its CLI entrypoint:

```bash
uv run aisc_salesforce
```

Use the development environment settings explicitly:

```bash
uv run --env-file .env aisc_salesforce
```

Or run it as a Python module:

```bash
uv run python -m aisc_salesforce
```

## Environment Variables

`.env.example` is the committed template. Copy it to `.env` for development.

- `LOG_LEVEL` defaults to `INFO`; `.env` sets it to `DEBUG` for verbose console output.
- `LOG_FILE` defaults to `app.log` and controls the path to the log file.

`uv run` does not load `.env` automatically; use `uv run --env-file .env` to load the development environment explicitly.

## Testing

Run the tests:

```bash
uv run pytest
```

Run tests with coverage:

```bash
uv run pytest --cov
```

## Documentation

Preview the documentation locally:

```bash
uv run python scripts/serve_docs.py
```

Build static documentation:

```bash
uv run mkdocs build
```
