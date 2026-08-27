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

### `annotate` — label records as the calling user

Every other group administers a server; this one does the annotation work.
It talks to Argilla's `/me` endpoints, which any member of the dataset's
workspace may use, so the same three commands work under an **annotator**,
admin or owner key — the key decides whose responses are read and written.

| Command | What it does |
|---|---|
| `annotate next <dataset>` | Show the record(s) still waiting for your response. |
| `annotate submit <dataset> <record-id>` | Answer one record. |
| `annotate discard <dataset> <record-id>` | Discard one record. |

```bash
# what should I annotate next? (one record by default)
argilla-cli annotate next my-ds -w my-ws
argilla-cli -o json annotate next my-ds -w my-ws --limit 5

# answer it: --answer is repeatable, and splits on the first `=`
argilla-cli annotate submit my-ds 1f0c… -w my-ws --answer label=positive
argilla-cli annotate submit my-ds 1f0c… --answer label=positive --answer rating=4

# park an answer without submitting it
argilla-cli annotate submit my-ds 1f0c… --answer label=positive --status draft

# multi-question or structured answers: a JSON object, from a file or stdin
argilla-cli annotate submit my-ds 1f0c… --from answers.json
echo '{"label": "positive", "topics": ["pricing", "support"]}' \
  | argilla-cli annotate submit my-ds 1f0c… --from -

# not annotatable
argilla-cli annotate discard my-ds 1f0c… -w my-ws
```

Notes:

- **The record id comes from `annotate next`.** It is the server's own record
  id; `-o json` gives you it alongside the record's `fields`, its
  `suggestions`, your own existing `my_responses`, and `pending_total` — how
  many records the server still has waiting for you.
- **`--answer` values are JSON when they parse as JSON**, and the literal
  string otherwise. So `rating=4` sends the number `4`, `topics=["a","b"]`
  sends a list, and `label=positive` sends the string.
- **`--answer` and `--from` are mutually exclusive**, and one of them is
  required; passing both or neither exits **13**.
- `--status` accepts `submitted` (default) or `draft`. Anything else is a
  usage error (exit **2**) — discarding has its own command.
- `discard` is not gated behind a confirmation prompt: it writes *your*
  response and destroys nothing shared, and you can submit an answer
  afterwards to replace it.
- A key without access to the dataset's workspace exits **10**; an unknown
  dataset or record exits **12**. Argilla's record ids are global, so `submit`
  and `discard` check that the record really is in the dataset you named
  before writing anything — a record id from a *different* dataset exits
  **12** too, rather than answering the other dataset's record.

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
