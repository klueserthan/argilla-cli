## Summary

<!-- What changes and why. One paragraph. -->

## Linked issue

<!-- "Closes #123", or "Refs #123" if this is partial work. -->

## Type of change

- [ ] Bug fix
- [ ] Enhancement
- [ ] Refactor (no behaviour change)
- [ ] Docs / CI only

## User-facing surface

<!--
The command surface and the exit codes are the CLI's contract: scripts depend on
both. Say "none" if this is internal only.
-->

- [ ] No change to commands, flags, output, or exit codes
- [ ] Additive only (new command, new optional flag, new output field)
- [ ] Breaking — describe below what a caller has to change

## Tests

<!--
This repo is test-driven: the test comes first. Say which test you wrote and what
it failed on before the fix. Note anything you couldn't cover.
-->

- [ ] A failing test was written first, and it failed on the behaviour (not on an
      unknown command or a missing fixture)
- [ ] A bug fix is pinned by a case in `tests/test_regressions.py`
- [ ] A new assumption about the `argilla` SDK is pinned in `tests/test_sdk_contract.py`

## Checklist

- [ ] `make check` passes locally (ruff check, ruff format --check, mypy, pytest)
- [ ] `uv.lock` regenerated in this PR if `pyproject.toml` changed
- [ ] `README.md` updated if the command surface, a flag, or an exit code changed
- [ ] `.env.example` updated if an environment variable was added
- [ ] `CLAUDE.md` / `CONTRIBUTING.md` updated if a convention changed
- [ ] No API keys, HF tokens, or `.env` contents committed
