# argilla-cli

A production-quality command-line client for [Argilla](https://argilla.io): manage workspaces,
users, datasets and records on an Argilla server from the terminal, and move records between
that server, local files, and the Hugging Face Hub.

Python 3.11+ (CI matrix: 3.11, 3.12, 3.13), a `src/` layout, packaged with setuptools and
managed with **uv**. The console script is `argilla-cli` (`argilla_cli.main:run`). Built on
typer + rich over the official `argilla` SDK.

This is a *thin, well-behaved wrapper*, not a reimplementation: the SDK owns talking to the
server, and this CLI owns everything the SDK leaves to the caller — credential resolution,
error classification, exit codes, output formatting, and the atomicity/ordering guarantees that
turn a half-finished command into a recoverable one.

## Commands

| group | commands |
|---|---|
| `config` | `show`, `doctor`, `list`, `set`, `get`, `use`, `remove` |
| `workspace` | `list`, `show`, `create`, `delete`, `users`, `add-user`, `remove-user` |
| `dataset` | `list`, `show`, `progress`, `create`, `delete`, `settings`, `download`, `push`, `copy`, `to-hub`, `from-hub` |
| `user` | `me`, `list`, `show`, `create`, `delete`, `add-to-workspace`, `remove-from-workspace` |
| `server` | `info`, `whoami`, `health` |

Global flags are declared **once**, on the root callback in `main.py`:
`-o/--output {table,json,yaml,csv}`, `-w/--workspace`, `-p/--profile`, `--api-url`, `--api-key`,
`-y/--yes`, `-v/--verbose`, `-q/--quiet`, `-V/--version`. Commands read them off
`argilla_cli.context.ctx` and `argilla_cli.io_utils` rather than redeclaring their own copy.

That rule is about *presentation and connection* state — `-o`, `-v`, `-q`, `--api-url`,
`--api-key`, `-y` are root-only, and a command that respells one is a bug. Two options are
deliberately declared in both places, and neither is a duplicate to clean up:

- **`-w/--workspace`**, via the shared `WorkspaceOpt` in `options.py`, on every `dataset` command
  and `user list`. The command-level flag names the workspace for that invocation and
  `resolve_workspace_name` falls back to the root `-w` when it's absent, which is what makes both
  `argilla-cli -w nlp-lab dataset list` and `argilla-cli dataset download my-ds -w nlp-lab` work.
  Reuse `WorkspaceOpt` on a new workspace-scoped command rather than respelling the flag.
- **`--profile`**, on `config set` and `config get` only, where it means something *different*
  from the root flag: `-p/--profile` picks the profile settings are read *from*, the local one
  picks the profile a value is written *to* or read out of. That's what lets
  `config set api_key ... --profile staging` work while the global flag stays free to mean
  something else; `_target_profile` falls back to `ctx.profile`, then `$ARGILLA_CLI_PROFILE`,
  then the current profile.

Deleting either local option would break a supported invocation. What stays banned is a
per-command `--json`/`--output` or a second spelling of a flag `options.py` already owns.

## Commands to run

```bash
uv sync --all-extras          # venv + package + hub/export extras + dev group
uv run argilla-cli --help     # run against the checkout, no install step

make check                    # the CI test job's four steps, on one interpreter
make lint                     # uv run ruff check .
make format                   # uv run ruff format .   (--check in CI)
make typecheck                # uv run mypy src
make test                     # uv run pytest --cov=argilla_cli --cov-report=term-missing
make lock                     # uv lock
make build                    # uv build (sdist + wheel)
```

`make check` runs the same four commands as CI's test job — ruff check, ruff format --check,
mypy, pytest — but only on the interpreter your venv is built against. It does **not** cover two
things CI does, so a green `make check` is necessary rather than sufficient:

- the **3.11/3.12/3.13 matrix** — run `UV_PYTHON=3.11 make check` (and 3.13) when a change could
  be version-sensitive;
- the **build job** — `uv build` and `twine check dist/*`, worth running by hand when packaging
  metadata changes.

It also assumes your venv is already synced with `--all-extras`. CI runs
`uv sync --locked --all-extras`, so a venv missing the `hub` extra fails mypy on `jinja2` locally
while CI is green — sync first rather than chasing the error.

Ruff: line length 88, `E,F,I,UP,B`, target `py311`. Mypy runs over `src` with
`disallow_untyped_defs`, `check_untyped_defs`, `warn_unused_ignores`, and deliberately **no**
pinned `python_version` (see the comment in `pyproject.toml` — pinning 3.11 while checking a
3.12+ env makes it parse that env's stubs under the wrong rules).

Dependencies go in via `uv add` / `uv add --optional <extra>` / `uv add --dev`, never by hand.
`uv.lock` is committed and CI runs `uv sync --locked --all-extras`, so a lock change belongs in
the same commit as the `pyproject.toml` change that caused it.

## Layout

```
src/argilla_cli/
  main.py            Typer app; every global flag declared once on the root callback.
  context.py         Per-invocation connection state (profile, workspace, lazy SDK client).
  io_utils.py        Presentation state (format/verbose/quiet) and the one render() entry point.
  errors.py          Error taxonomy, exit codes, map_exception(), @handle_errors.
  settings.py        Layered settings resolution with source tracking.
  profiles.py        Persistent multi-server profile store (config.toml).
  options.py         Reusable option/argument definitions and confirm().
  resources.py       Lookups against the SDK that turn a missing resource into NotFoundError.
  file_io.py         Atomic writes and classified text reads.
  records_io.py      Record formats, JMESPath mapping, streaming reads/writes, spooling.
  clients/           Lazy construction of the Argilla SDK client.
  commands/          One module per group: config, workspace, dataset, user, server.
tests/               pytest suite against a fake SDK, plus contract tests against the real one.
```

## Architecture

### Settings resolution (`settings.py`, `profiles.py`)

Precedence, highest first: **CLI flags → process environment → the selected profile in
`config.toml` → a local `.env`**. `SettingsInfo.source_of(key)` remembers *which* layer won, so
`config show` can name where every effective value actually came from (with secrets masked);
`config doctor` then checks credentials, connectivity and the HF token, passing the original
exception through so an auth failure still exits 10 and a transport failure 11.

Resolution never raises for *missing* credentials — `config show`, the command whose whole job
is diagnosing missing credentials, has to be able to run. Commands that need the server call
`ctx.require_settings()` / `require_credentials()`, which is where `AuthConfigError` (exit 10)
comes from.

Profiles live at `$XDG_CONFIG_HOME/argilla-cli/config.toml` (overridable with
`ARGILLA_CLI_CONFIG`, selectable with `ARGILLA_CLI_PROFILE` or `-p`), so one install can talk to
several servers. Env vars still win over the file, so env-only setups keep working untouched.

Environment variables: `ARGILLA_API_URL`, `ARGILLA_API_KEY`, `HF_TOKEN` (Hub commands only),
`ARGILLA_DEFAULT_OUTPUT_DIR`, `ARGILLA_CLI_CONFIG`, `ARGILLA_CLI_PROFILE`, `XDG_CONFIG_HOME`.
`.env.example` doubles as the reference — **update it whenever a variable is added.**

### Errors and exit codes (`errors.py`) — part of the contract

| code | meaning | exception |
|---|---|---|
| 0 | success | — |
| 1 | unexpected / unclassified | `CLIError` |
| 2 | usage error (bad flags, unsupported values) | `UsageError` |
| 10 | authentication or configuration problem | `AuthConfigError` |
| 11 | network or server-side failure | `NetworkApiError` |
| 12 | resource not found | `NotFoundError` |
| 13 | validation error / missing optional extra | `ValidationError`, `MissingExtraError` |

`map_exception` classifies by **exception type and HTTP status only** — never by matching
substrings against the message (an earlier version's bare `"5"` test reclassified anything whose
text contained the digit as a network failure). The rules, in order:

1. Already a `CLIError` → returned as-is.
2. HTTP status, if one can be found on the exception or its `.response`. Every status from 300
   up is assigned deliberately: 401/403→10, 404→12, 408/429/5xx→11, other 4xx→13, 3xx→11.
   An **unclassifiable** status returns `None` and falls *through* rather than short-circuiting,
   so the transport rules below still get their say.
3. Transport module (`httpx`/`requests`/`urllib3`) or `ConnectionError`/`TimeoutError` → 11.
4. `pydantic`/`pydantic_core` → 13 (a model rejecting user data locally is bad input).
5. `argilla` → by exception name: credentials/unauthorized/forbidden→10, not-found→12, the
   `_ARGILLA_VALIDATION_MARKERS` names→13, everything else→11.

Every command body is wrapped in `@handle_errors`, which re-raises control-flow exceptions
(`typer.Exit`, `typer.Abort`, `click.Abort`, `click.ClickException`) untouched — they subclass
`RuntimeError`, and a hand-rolled `except Exception` swallows deliberate exits and rewrites
documented exit codes. `functools.wraps` sets `__wrapped__`, which Typer follows to build the
right CLI parameters. **Don't add your own try/except around a command body**: raise a typed
error or let the exception propagate. Use `is_classified(exc)` before wrapping an exception in
your own type, so you don't overwrite an auth or network verdict with something vaguer.

### Output (`io_utils.py`)

Every command that emits structured output calls `render()`, which honours the global
`-o/--output` — so a new command supports table/json/yaml/csv without any per-command work.
Never add a per-command `--json` flag. Table column order is first-seen key order (not
alphabetical), so tables lead with the identifying column. `print_ok` is suppressed under
`--quiet` *and* under any structured format (chatter must never contaminate machine-readable
stdout); `print_error`/`print_warn` go to stderr and errors are never suppressed.

Presentation state lives here rather than in `context.py` on purpose: `errors` needs to print
and `context` needs to raise, so putting them together would create an import cycle.

### The SDK seam (`clients/`, `resources.py`, `context.py`)

`import argilla` pulls a large tree and costs noticeable startup time, so it happens **lazily
inside functions** — `--help`, `config show` and shell completion stay fast and keep working
even when the SDK can't be imported. `ctx.client()` looks `get_client` up *through the module*
rather than importing it by name, which is what lets tests monkeypatch it in one place.

The SDK's accessors return `None` for a missing resource instead of raising. Every lookup goes
through `resources.py` so that becomes a clean `NotFoundError` (exit 12) rather than
`'NoneType' object has no attribute 'delete'` with a generic exit 1.

### Record I/O (`records_io.py`, `file_io.py`)

Formats: JSONL (default), CSV (standard library — not routed through pandas), Parquet (needs
the `export` extra; guarded by `MissingExtraError`, not a raw `ImportError`). `--map` applies a
JMESPath expression per output field; `--list-policy` decides what container results collapse
to, defaulting to `join` on export and **`preserve` on push**, because Argilla's structured
properties (`fields`, `metadata`, `suggestions`, `vectors`) must arrive as mappings and lists.

Guarantees the code exists to hold — treat them as invariants when editing:

- **Reads are lazy, and `--limit N` stops at N.** Server-side records are streamed by *iterating*
  `dataset.records` (the narrowest SDK contract), so `--limit` on a download must never become a
  post-filter over a full download. How tightly the local readers hold that line differs by
  format, and the differences are deliberate — check which one you're relying on before writing a
  test against it:
  - **JSONL is byte-exact.** `_iter_jsonl` opens the file in *binary* and decodes one line at a
    time, precisely so an undecodable byte past the limit is never touched. A text handle would
    decode a whole buffer per read and raise while fetching line 1. Keep it binary.
  - **CSV is row-exact, not byte-exact.** `csv.DictReader` requires a text handle, so decoding
    happens in buffer-sized chunks: `islice` parses exactly `limit` rows and no more, but a bad
    byte anywhere in the first chunk surfaces even at `--limit 1`. That's a `ValidationError`
    (exit 13) either way — `_iter_csv`'s own note about `line_num` locating "the region rather
    than the exact line" is the same fact.
  - **Parquet is eager.** It's columnar and can't be streamed by row, so `_iter_parquet`
    materialises the whole file through pandas and the limit only bounds what is passed onward:
    memory scales with the file, and a malformed row past the limit still surfaces.

  The answer for input too large to hold is JSONL, which streams end to end.
- **Nothing is uploaded until the whole input has been checked.** `dataset push` maps and
  parses the entire stream, runs it through the SDK's own `_ingest_records` a batch at a time,
  and *spools it to disk* (`spooled_records`) before the first upload — both the
  fail-before-any-write property and the memory bound, which earlier versions had one of but
  never both. A malformed row at 600 must not leave 500 rows on the server, especially since
  `push` keeps ids so retrying an id-less input would duplicate them.
- **Files are replaced atomically.** `atomic_path` writes to a temporary sibling (created 0o600,
  which matters for the credential store) and `os.replace`s it — an interrupted export must not
  destroy the previous good one, and a truncated `config.toml` must not lose every stored key.
- **`dataset copy` rolls back.** The first batch is read *before* the destination is created, so
  an unreadable source leaves nothing behind; anything failing later deletes the partial
  dataset, guarding `BaseException` so a Ctrl+C mid-copy is covered too.
- **Decode failures are classified.** `UnicodeDecodeError` subclasses `ValueError` and lives in
  `builtins`, so it slips past both `except OSError` and `map_exception`. Read user-pointed text
  through `read_text_file` so one bad byte exits 13, not 1.
- **Private SDK API degrades, never crashes.** `_ingest_records` and the per-record flattener are
  private; their absence downgrades the feature rather than erroring, and `test_sdk_contract`
  fails loudly if they move.

## Testing: this repo is test-driven

**Write the failing test first, then the code that makes it pass.** Every behavioural change —
a new command, a new flag, a bug fix, a changed exit code, a new record format — starts with a
test that fails for the right reason. Don't write the implementation ahead of its test, and
don't retro-fit tests onto code that already works.

"Fails for the right reason" is a real check here, not a formality. A CLI test that runs a
command that doesn't exist yet exits **2** (usage error) — which is a failure, but not *your*
failure. Read the assertion output before writing any implementation: it should be failing on
the behaviour you're adding, not on Typer rejecting an unknown command or on a fixture that
doesn't exist yet.

The loop:

1. Pick the file the change belongs in — `tests/test_<group>.py` for a command, `test_config.py`
   for settings/profiles, `test_records_io.py` for record I/O, `test_render.py` for output,
   `test_regressions.py` for a bug you are fixing, `test_sdk_contract.py` for a new assumption
   about the SDK.
2. Write the test. Assert the **exit code** first and the output second.
3. Run just that test (`uv run pytest tests/test_dataset.py -k my_case`) and read the failure.
4. Implement the smallest change that makes it pass.
5. `make check` before pushing.

Where a change needs a server behaviour the fake doesn't have yet, extend `FakeArgilla` in
`conftest.py` as part of the same commit — and if you are extending it because the *real* SDK
does something the fake didn't model, add the corresponding case to `test_sdk_contract.py` so
the fake can't drift back out of line silently.

Bug fixes get two things, in this order: a test in `test_regressions.py` that reproduces the bug
and fails, then the fix. A bug that was worth fixing is worth pinning — most of this repo's
existing suite is exactly that.

### Conventions the suite already follows

`pytest` from `tests/`, with `pythonpath = ["src"]` and `-q`. Coverage is reported but not
gated.

- **Everything runs against `FakeArgilla`** in `tests/conftest.py`, which mirrors the two SDK
  behaviours the CLI has to get right: accessors are both iterable and callable
  (`client.workspaces` / `client.workspaces("name")`), and a callable accessor returns `None`
  for a missing resource instead of raising.
- Two autouse fixtures do the heavy lifting: `isolated_env` strips every relevant env var,
  chdirs into a tmp dir, points `XDG_CONFIG_HOME`/`ARGILLA_CLI_CONFIG` at it and resets the
  `io_utils`/`ctx` singletons on both sides of the test; `patch_client` routes `ctx.client()` to
  the fake. Neither an ambient `ARGILLA_API_KEY` nor a stray `.env` can leak into a result.
- Commands are exercised end-to-end through Typer's `CliRunner`, asserting on **exit code** as
  much as on output — the exit codes are the contract.
- `tests/test_sdk_contract.py` is the exception: it introspects the *real* installed `argilla`
  package (no server needed) so drift between the SDK and what the fake imitates fails here
  rather than in the field. Add a case here whenever you start depending on a new SDK shape.
- `tests/test_regressions.py` and `tests/test_review_fixes.py` pin previously-fixed bugs. When
  you fix a behavioural bug, add its case there; when you change behaviour these cover, read the
  test's docstring first — it usually records *why*.

## House style

- **Module docstrings carry the "why".** This codebase documents the constraint a module exists
  to satisfy, and often the wrong version that came before it, at the top of the file. Match
  that when adding one: explain the failure mode, not what the functions do.
- **Same for non-obvious code.** Comments explain the alternative that was rejected and what it
  broke (`BaseException` vs `Exception` in the copy rollback, spooling vs listing in push).
  Don't strip them; extend them when the reasoning changes.
- **One place per decision.** Exit-code mapping lives only in `errors.py`, rendering only in
  `io_utils.render`, flag spelling only in `options.py`/`main.py`, resource lookup only in
  `resources.py`. If you find yourself adding a second copy, move the first instead.
- Commands stay thin: resolve inputs, call one or two helpers, `render()` the result.
- `from __future__ import annotations` at the top of every module.

### Adding a command

1. **Write the test first**, in `tests/test_<group>.py`: invoke it through `CliRunner`, assert
   the exit code and the output. Run it, and confirm it fails on the behaviour rather than on
   Typer not knowing the command.
2. Put the command in the relevant `commands/<group>.py` (or create a group and register it with
   `app.add_typer(...)` in `main.py`).
3. Decorate with `@app.command("name")` **and** `@handle_errors`.
4. Reuse options from `options.py` (`WorkspaceOpt`, `LimitOpt`, `resolve_workspace_name`,
   `confirm`) rather than respelling a flag; no per-command `--json`/`--output`.
5. Look resources up via `resources.py`; gate destructive actions behind `confirm()`.
6. Emit through `render()` (and `print_ok` for the human-facing note).
7. Guard optional dependencies with `MissingExtraError`, not a bare import.
8. Extend `FakeArgilla` if the command needs server behaviour the fake lacks — and pin the real
   SDK shape in `test_sdk_contract.py` if that's why.
9. Update `README.md` (the command reference) and `CONTRIBUTING.md` if the convention itself
   changed.

## Change workflow

Every change goes through a pull request against `main`; don't commit or push directly to `main`
unless the current request explicitly says to.

- Branch from an up-to-date `main`, and run `make check` locally before pushing — the CI test
  job's four steps on one interpreter (see **Commands to run** for what it leaves out).
- CI (`.github/workflows/ci.yml`) runs lint, format check, mypy, and pytest with coverage across
  Python 3.11/3.12/3.13 with `uv sync --locked --all-extras`, plus a `build` job that runs
  `uv build` and `twine check`. All of it must be green before merge.
- Fill in `.github/pull_request_template.md` rather than leaving it as-is, and link the issue the
  work came from (`Closes #<n>`) when there is one.
- Commit subjects are imperative and describe the outcome ("Validate the whole push input before
  uploading any of it"), not the mechanics.
- Never commit API keys, HF tokens, or `.env` contents.

## Filing issues

**Open an issue for every bug you encounter, proactively — including ones you find in passing
and are not fixing right now.** An unreported bug found while doing something else is lost the
moment the session ends; the cost of filing is a minute, and the cost of not filing is finding it
again from a user report.

This applies to agents as much as to people. In particular, file one when you:

- find a bug while working on something unrelated (don't widen your current change to fix it —
  file it, and mention the issue number in your PR if it's adjacent);
- have to work around incorrect behaviour to get your actual task done;
- notice the code and its documented contract disagree (a wrong exit code, a flag that doesn't do
  what `README.md` says, an invariant in this file that the code no longer holds);
- hit a failure you decide is out of scope, blocked, or not worth fixing now — "not fixing this"
  is a reason to write it down, not a reason to stay quiet.

Use the templates: `.github/ISSUE_TEMPLATE/bug_report.yml` for bugs,
`.github/ISSUE_TEMPLATE/enhancement.yml` for proposals. Search open issues for a duplicate first,
and never paste a real `ARGILLA_API_KEY` or `HF_TOKEN` into an issue: redact them from any
command line, log, or config you quote.

Both forms also list **`needs-triage`** alongside `bug`/`enhancement`, so a new issue arrives
flagged for triage and a human clears the label once they've looked at it. **This only takes
effect once the label exists in the repo** — GitHub silently drops a label an issue form names
but the repository doesn't have, rather than creating it. At the time of writing `bug` and
`enhancement` exist and `needs-triage` does not, so it needs creating once under Issues → Labels;
until then issues file with the other label only, and nothing else changes. If you find yourself
reading this and the label is still missing, that is the one-click fix — not a reason to edit the
templates.

A good bug report here names the command, the exit code you got and the one you expected, and the
smallest input that reproduces it. When you can, include the failing test — the report is more
useful than the prose, and it's the first thing the fix will need anyway.

## Documentation

- `README.md` — install, configuration, the full command reference, exit codes. Update it in the
  same PR whenever the command surface or a flag changes.
- `CONTRIBUTING.md` — dev loop, dependency management, project layout, command conventions.
- `.env.example` — the environment-variable reference; update it when a variable is added.
- `.github/pull_request_template.md`, `.github/ISSUE_TEMPLATE/` — the PR and issue forms. Keep the
  checklists in step with this file when a convention changes.
