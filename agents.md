## Project Domain

The purpose of this project is to interact with our Salesforce data efficiently without the user interface.

## Project Setup and Management

- Project instructions apply unless a user explicitly invokes a workflow skill.
- Dependency management: `uv` and `pyproject.toml`. Never use `pip` or a `requirements.txt` file.
- Add a dependency with `uv add <package>`. Never use `uv pip` for dependencies.
- Run the app with `uv run fastapi dev main.py`.
- Branch from `main` as `feature/<name>` or other descriptive branch name and use Conventional Commits.
- Stage changes for review. Don't commit to `main` or push without being asked unless part of a specific wf-pr or wf-publish workflow.

## Coding Conventions

- Type-hint public functions and methods, including their return types.
- Use `pathlib` for path management. Don't use `os.path`.
- Prefer f-strings over `str.format()` or `%` formatting.
- Follow EAFP: handle exceptions rather than checking conditions up front.
- Write Google-style docstrings for every public function and method.
- Embrace idiomatic Python like comprehensions, generators, and decorators.

## Quality Gates

A task is done only when all of these pass:

- `uv run ruff format` leaves the code unchanged.
- `uv run ruff check` reports no errors.
- `uv run mypy main.py` reports no errors.
- `uv run pytest` passes, with a test added for every new endpoint.

## Constraints

- Ask before adding any external dependency.
- Preserve the signature and response shape of existing endpoints.
- Don't use blocking I/O inside `async` functions.
- Keep existing tests intact, and fix the code to make them pass.
- Declare a task done only after the gates pass and docstrings are updated.

## Ignore

Treat everything in `.gitignore` as off-limits to read or edit. On top of that, never open:

- Secrets and `.env` files
- Large data files unrelated to the current task
- Vendored or generated code