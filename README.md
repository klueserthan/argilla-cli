# argilla-cli

A CLI for managing [Argilla](https://github.com/argilla-io/argilla) servers —
workspaces, datasets, and users — from your terminal. Built with
[Typer](https://typer.tiangolo.com/).

## Install

### As a tool (recommended)

```bash
# from PyPI, once published
uv tool install argilla-cli

# from GitHub
uv tool install 'argilla-cli @ git+https://github.com/klueserthan/argilla-cli.git'

# with an optional extra (see "Optional extras" below)
uv tool install 'argilla-cli[export] @ git+https://github.com/klueserthan/argilla-cli.git'
```

This installs the `argilla-cli` command in an isolated, managed
environment and puts it on your `PATH`.

### One-off, without installing

```bash
uvx --from 'argilla-cli @ git+https://github.com/klueserthan/argilla-cli.git' argilla-cli --help
```

### From source (development)

```bash
git clone https://github.com/klueserthan/argilla-cli.git
cd argilla-cli
uv sync --all-extras
uv run argilla-cli --help
```

See [CONTRIBUTING.md](./CONTRIBUTING.md) for the full development workflow.

### Optional extras

| Extra | Adds | Needed for |
|---|---|---|
| `export` | `pandas`, `pyarrow` | `--fmt parquet` (JSONL and CSV work without it) |
| `hub` | `huggingface_hub`, `datasets`, `Jinja2` | `dataset to-hub`, `dataset from-hub` |

In practice the Argilla SDK already pulls in `datasets`, which brings
`pandas` and `pyarrow` with it, so both extras are often satisfied by a
plain install — `Jinja2` is usually the only genuinely missing piece. The
extras pin those dependencies explicitly rather than relying on that,
since it is a transitive detail that can change between SDK releases.
Commands that need a missing package fail with exit 13 and an install hint
rather than a stack trace.

Install extras with `uv tool install 'argilla-cli[export,hub] @ ...'` or,
from source, `uv sync --all-extras`.

## Configuration

Configuration is resolved in this order, highest precedence first:

1. **CLI flags** — `--api-url`, `--api-key`
2. **Environment variables** — `ARGILLA_API_URL`, `ARGILLA_API_KEY`, `HF_TOKEN`
3. **Profile** in `config.toml` — see below
4. **`.env` file** in the current directory

A plain environment-variable setup keeps working untouched; profiles are
there for when you need more than one.

### Quick start with environment variables

```bash
cp .env.example .env
# edit .env and set ARGILLA_API_URL / ARGILLA_API_KEY / (optional) HF_TOKEN
argilla-cli config doctor
```

### Profiles (multiple servers)

Profiles let one install talk to several Argilla servers without juggling
environment variables. They live in
`$XDG_CONFIG_HOME/argilla-cli/config.toml` (defaulting to
`~/.config/argilla-cli/config.toml`):

```bash
# write into the "staging" profile
argilla-cli config set api_url https://staging.argilla.example.com --profile staging
argilla-cli config set api_key rbga_staging_key --profile staging

# write into the "prod" profile and make it the default
argilla-cli config set api_url https://argilla.example.com --profile prod
argilla-cli config set api_key rbga_prod_key --profile prod
argilla-cli config use prod

# inspect
argilla-cli config list
argilla-cli config show

# use a non-default profile for one invocation
argilla-cli -p staging dataset list
```

Other config commands: `config get <key>`, `config remove <profile>`.

## Usage

### Global flags

These are declared once on the root command and apply to **every**
subcommand. They must come **before** the subcommand:

```bash
argilla-cli [GLOBAL FLAGS] <group> <command> [ARGS]...
```

| Flag | Short | Description |
|---|---|---|
| `--output {table,json,yaml,csv}` | `-o` | Output format for structured results. Default `table`. |
| `--workspace NAME` | `-w` | Default workspace for this invocation. |
| `--profile NAME` | `-p` | Configuration profile to use. |
| `--api-url URL` | | Override the Argilla API URL. |
| `--api-key KEY` | | Override the Argilla API key. |
| `--yes` | `-y` | Assume yes; never prompt for confirmation. |
| `--verbose` | `-v` | Include underlying error detail. |
| `--quiet` | `-q` | Suppress non-essential output. |
| `--version` | `-V` | Show the version and exit. |

There is **no** per-command `--json` flag — that was removed. Use the
global `-o/--output` flag instead:

```bash
argilla-cli -o json dataset list
argilla-cli -o yaml workspace show my-ws
argilla-cli -o csv user list > users.csv
```

### `config` — inspect and manage configuration

```bash
argilla-cli config show               # effective config, secrets masked, sources shown
argilla-cli config doctor             # check credentials + server connectivity
argilla-cli config list               # list configured profiles
argilla-cli config set api_url <url>  # write a value into a profile
argilla-cli config get api_url        # read a value from a profile
argilla-cli config use staging        # select the default profile
argilla-cli config remove staging     # delete a profile
```

### `workspace` — manage workspaces

```bash
argilla-cli workspace list
argilla-cli workspace show my-ws
argilla-cli workspace create my-ws --exists-ok
argilla-cli workspace delete my-ws
argilla-cli workspace users my-ws
argilla-cli workspace add-user my-ws jane
argilla-cli workspace remove-user my-ws jane
```

### `dataset` — manage datasets

```bash
argilla-cli dataset list                      # every workspace
argilla-cli dataset list -w my-ws             # one workspace
argilla-cli dataset show my-ds -w my-ws       # fields, questions, record counts
argilla-cli dataset delete my-ds -w my-ws
```

If a dataset name exists in more than one workspace, pass `-w` — otherwise the
command stops and tells you which workspaces it found.

#### Export

```bash
argilla-cli dataset download my-ds -w my-ws -O ./my.jsonl
argilla-cli dataset download my-ds --fmt csv                  # no extra required
argilla-cli dataset download my-ds --fmt parquet              # needs the `export` extra
argilla-cli dataset download my-ds --completed-only --limit 500
argilla-cli dataset download my-ds --flatten                  # fields.text instead of nested
argilla-cli dataset download my-ds -O ./out.jsonl --force     # overwrite
```

`-O/--output-path` accepts a file or a directory; a bare stem gains the format
suffix automatically. `--completed-only` and `--limit` behave identically for
every format.

Reshape records on the way out with a JMESPath mapping file
(`{"text": "fields.text", "state": "status"}`):

```bash
argilla-cli dataset download my-ds --map mapping.json
argilla-cli dataset download my-ds --map mapping.json --list-policy first
argilla-cli dataset download my-ds --map mapping.json --list-policy join --list-sep '|'
```

`--list-policy` decides what happens when an expression returns a container:
`join` (the export default), `first`, `error`, or `preserve`.

`preserve` keeps mappings and lists intact instead of flattening them, and
is the default for `push` — Argilla's structured properties (`fields`,
`metadata`, `suggestions`, `vectors`) have to arrive as containers, not
strings. Export defaults to `join` because a tabular cell cannot hold one.
Either command accepts either policy.

#### Import, copy, and reuse

```bash
# upload records from a local file (the inverse of download; --map works here too)
argilla-cli dataset push my-ds -w my-ws --from ./my.jsonl
argilla-cli dataset push my-ds --from ./my.csv --fmt csv

# duplicate a dataset, optionally into another workspace
argilla-cli dataset copy my-ds my-ds-copy -w my-ws --to-workspace other-ws
argilla-cli dataset copy my-ds my-ds-copy -w my-ws --no-records   # settings only

# export settings, then stamp out a new dataset from them
argilla-cli dataset settings my-ds -w my-ws --export ./settings.json
argilla-cli dataset create new-ds -w my-ws --settings ./settings.json
```

#### Progress and Hub interop

```bash
argilla-cli dataset progress my-ds -w my-ws
argilla-cli dataset progress my-ds -w my-ws --by-user

# Hugging Face Hub (needs the `hub` extra)
argilla-cli dataset to-hub my-ds org/my-ds -w my-ws --private
argilla-cli dataset from-hub org/my-ds --name my-ds -w my-ws
```

### `user` — manage users

```bash
argilla-cli user me
argilla-cli user list
argilla-cli user show jane
argilla-cli user create jane --role annotator
argilla-cli user delete jane
argilla-cli user add-to-workspace jane my-ws
argilla-cli user remove-from-workspace jane my-ws
```

### `server` — inspect the server

```bash
argilla-cli server info
argilla-cli server whoami
argilla-cli server health
```

## Agent annotators

argilla-cli can be run by an AI agent holding an Argilla API key with the
**annotator** role, working through a dataset's queue the same way a human
research assistant would: `annotate next` to fetch the next pending
record, `annotate submit` to record a response, `annotate discard` for a
record that cannot be judged. These commands are built on the server's
`/api/v1/me/...` endpoints, so they behave identically regardless of
whether the calling key belongs to an annotator, an admin, or an owner —
what changes is what the server lets that key see and do.

### Security model

The annotator role is enforced **server-side**, not by this CLI — an
annotator key can only read datasets in its assigned workspaces and submit
its own responses to records in those datasets. It cannot create, delete,
or modify datasets or records, cannot write suggestions, and cannot see
other annotators' responses. Handing an agent an annotator-scoped key
rather than an admin or owner key means a misbehaving or compromised agent
is bounded to exactly what a human RA in that role could do.

Multiple annotators — human RAs and agents alike — can work the same
dataset concurrently. Argilla's task distribution settings decide how many
responses a record needs before it counts as completed; once that
threshold is met, the server removes the record from every remaining
annotator's queue automatically. Nothing in this CLI coordinates that —
it's a property of the server.

### Setting up an agent annotator

```bash
# create a dedicated annotator user for the agent (not a shared/personal key)
argilla-cli user create agent-ra-1 --role annotator --first-name "Agent RA"

# give it access to the dataset's workspace
argilla-cli workspace add-user my-ws agent-ra-1

# point the agent's profile (or environment) at that user's API key
argilla-cli config set api_key <agent-key> --profile agent-ra-1
```

Before annotating, the agent should confirm it's running as the intended
identity:

```bash
argilla-cli server health
argilla-cli user me
```

### Command reference

```bash
argilla-cli annotate next my-ds -w my-ws --limit 1        # next pending record(s)
argilla-cli annotate submit my-ds <record-id> -w my-ws \
  --answer question_name=value                            # repeatable --answer
argilla-cli annotate submit my-ds <record-id> -w my-ws \
  --from response.json --status draft                     # complex response, or unsure
argilla-cli annotate discard my-ds <record-id> -w my-ws    # record is unjudgeable
```

`--answer key=value` values that parse as JSON are sent structured;
anything else is sent as a string. `--from file.json` (or `--from -` for
stdin) is the alternative to repeated `--answer` for a multi-question
response. `--status` defaults to `submitted`; pass `--status draft` when
the agent is not confident enough to commit an answer, so a human reviewer
sees it before it counts as a submitted judgment. An empty `annotate next`
result (exit 0) means the queue is empty.

### The `ra-annotator` skill

[`skills/ra-annotator/SKILL.md`](./skills/ra-annotator/SKILL.md) packages
the working loop above as a Claude Code agent skill: read a dataset's
guidelines and question schema once (`dataset show`, `dataset settings`),
then loop `annotate next` → judge the record against the guidelines →
`annotate submit`/`discard` until the queue is empty, with guardrails
against fabricating label values and an exit-code table for what to do on
each failure mode.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | success |
| 1 | unexpected/unclassified error |
| 2 | usage error (bad flags, unsupported values) |
| 10 | authentication or configuration problem |
| 11 | network or server-side failure |
| 12 | resource not found |
| 13 | validation error (bad input, conflicting state, missing extra) |

Pass `-v/--verbose` to include the underlying exception detail alongside
the mapped error message.

## Development

See [CONTRIBUTING.md](./CONTRIBUTING.md) for the full uv-based dev loop.
Short version:

```bash
uv sync --all-extras
make check   # ruff check + ruff format --check + mypy + pytest, same as CI
```
