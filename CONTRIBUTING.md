# Contributing to argilla-cli

Thanks for helping revive argilla-cli. This project is managed with
[uv](https://docs.astral.sh/uv/) — there is no `pip install` or manual
virtualenv step; uv handles the interpreter, the virtualenv, and the lock
file for you.

## Dev loop

1. **Clone and sync dependencies** (this creates `.venv` and installs the
   package, its `hub`/`export` extras, and the dev dependency group):

   ```bash
   uv sync --all-extras
   ```

2. **Run the CLI** against your local checkout with `uv run`, no install
   step required:

   ```bash
   uv run argilla-cli --help
   uv run argilla-cli config show
   uv run argilla-cli -o json workspace list
   ```

3. **Make your change.**

4. **Run the full check suite before pushing** — it runs the same four commands as CI's
   test job, on your interpreter only:

   ```bash
   make check
   ```

   which runs, in order: `ruff check`, `ruff format --check`, `mypy src`,
   and `pytest` with coverage. Individual targets (`make lint`, `make
   format`, `make typecheck`, `make test`) are also available; see `make
   help` for the full list.

   CI runs those same steps across Python 3.11, 3.12 and 3.13 and adds a
   separate build job (`uv build` + `twine check`), so a green `make check`
   is necessary but not sufficient. Use `UV_PYTHON=3.11 make check` to check
   another interpreter.

5. Open a PR. CI runs the same matrix across Python 3.11, 3.12, and 3.13.

## Adding a dependency

Always use `uv add` rather than editing `pyproject.toml` by hand — it
resolves the change and updates `uv.lock` in the same step.

```bash
# a runtime dependency
uv add httpx

# a dependency for one of the optional extras
uv add --optional export polars

# a development-only tool (linting, testing, typing)
uv add --dev types-requests
```

**`uv.lock` must be committed** alongside any `pyproject.toml` change. CI
runs `uv sync --locked`, which fails the build if the lock file is out of
date with `pyproject.toml` — so a stale lock file is caught immediately,
not discovered later by a contributor with a different resolution.

If you only need to refresh the lock file without adding anything, run
`make lock` (`uv lock`).

## Project layout

```
src/argilla_cli/
  main.py         Typer app entry point; declares every global flag once
                   on the root callback (-o/-w/-p/--api-url/--api-key/-y/
                   -v/-q/-V).
  context.py       Per-invocation context: resolved profile, workspace,
                   and a lazily-built Argilla client.
  errors.py        Exception taxonomy, exit codes, and the shared
                   @handle_errors decorator (see below).
  io_utils.py      Output formatting (table/json/yaml/csv) and the shared
                   render() helper (see below).
  options.py       Reusable option/argument definitions shared across
                   commands.
  profiles.py      Persistent profile store (config.toml).
  settings.py      Layered settings resolution (flags > env > profile >
                   .env).
  resources.py     Shared lookups against the Argilla client (users,
                   workspaces, datasets).
  clients/         Thin wrapper(s) around the Argilla SDK client.
  commands/        One module per command group: config, workspace,
                   dataset, user, server.
tests/             pytest test suite (see pyproject.toml for pytest config).
```

## Command conventions

Every command function is wrapped in `@handle_errors` (from
`argilla_cli.errors`), which maps exceptions to the CLI's documented exit
codes and prints a single-line error instead of a traceback. Don't wrap
command bodies in your own `try/except` for this — raise a typed error
(`ValidationError`, `NotFoundError`, `AuthConfigError`, `NetworkApiError`,
...) or let the underlying exception propagate and let the decorator
classify it.

Every command that produces structured output calls `render()` (from
`argilla_cli.io_utils`) instead of printing directly. `render()` respects
the global `-o/--output` format (`table`, `json`, `yaml`, `csv`) so a new
command automatically supports all four without extra code.

When adding a new command:

- Put it in the relevant `commands/<group>.py` module (or create a new
  group if it doesn't fit an existing one, and register it with
  `app.add_typer(...)` in `main.py`).
- Reuse option definitions from `options.py` where they already exist
  (e.g. `WorkspaceOpt`) rather than redeclaring a flag with a different
  spelling.
- Do not add a per-command `--json`/`--output` flag — that's the global
  `-o/--output` flag, declared once on the root callback.
- Use `@handle_errors` and `render()` as described above.
- Add a test under `tests/`.
