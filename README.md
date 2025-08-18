# argilla-cli

A CLI for Argilla, built with Typer.

## Install

Install directly from GitHub (recommended for end users):

```bash
# via pipx (isolated environment)
pipx install 'argilla-cli @ git+https://github.com/klueserthan/argilla-cli.git'

# with CSV/Parquet export support
pipx install 'argilla-cli[export] @ git+https://github.com/klueserthan/argilla-cli.git'
```

Or with pip into your current environment/virtualenv:

```bash
pip install 'argilla-cli @ git+https://github.com/klueserthan/argilla-cli.git'

# with CSV/Parquet export support
pip install 'argilla-cli[export] @ git+https://github.com/klueserthan/argilla-cli.git'
```

From source (development / editable mode):

Use a virtualenv and install in editable mode:

```bash
pip install -e .
```

This will install the console command `argilla-cli`.

## Configure

The CLI reads environment variables, preferring the process environment and loading a local `.env` only when required fields are missing.

Required:
- `ARGILLA_API_URL`
- `ARGILLA_API_KEY`

Optional:
- `HF_TOKEN`

Example `.env`:

You can copy the provided `.env.example` to `.env`:

```
cp .env.example .env
```

Then edit `.env` and set your values:

```
ARGILLA_API_URL=https://argilla.example.com
ARGILLA_API_KEY=rbga_your_api_key
HF_TOKEN=hf_your_hf_token
```

## Usage

Show config:

```bash
argilla-cli config show
```

Doctor:

```bash
argilla-cli config doctor
```

Workspaces:

```bash
argilla-cli workspace list
argilla-cli workspace create my-ws --exists-ok
```

Datasets:

```bash
argilla-cli dataset download my-ds --output ./my.jsonl
# with mapping (JMESPath JSON file)
argilla-cli dataset download my-ds --map mapping.json --fmt jsonl --output ./mapped.jsonl
 
# CSV/Parquet exports require pandas; Parquet also needs a parquet engine like pyarrow
argilla-cli dataset download my-ds --fmt csv --output ./my.csv
argilla-cli dataset download my-ds --fmt parquet --output ./my.parquet
 
# Only include completed records
argilla-cli dataset download my-ds --completed-only --output ./completed.jsonl
```

## Development

- Python 3.11+
- Run tests with pytest

```bash
pytest -q
```

### Optional dependencies

- CSV/Parquet export needs pandas: `pip install pandas` or install the extra: `pip install 'argilla-cli[export] @ git+https://github.com/klueserthan/argilla-cli.git'`
- Parquet writing needs a parquet engine (e.g., pyarrow): `pip install pyarrow` (included in the `export` extra)

