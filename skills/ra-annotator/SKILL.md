---
name: ra-annotator
description: Annotate, label, or classify records in an Argilla dataset via argilla-cli's `annotate` command group, acting as a research-assistant (RA) annotator alongside human RAs and other agents. Use when asked to label records, annotate a dataset, classify records for a study, or work through an Argilla annotation queue from the command line.
---

# RA annotator

Work an Argilla dataset's annotation queue from the command line, using
`argilla-cli`'s `annotate` command group. You act as **one annotator among
several** — human research assistants and other agents may be working the
same dataset concurrently. Argilla's server-side task distribution handles
the coordination: once enough responses land on a record (per the dataset's
distribution settings), the server marks it completed and removes it from
*everyone's* queue, including yours. You never need to check who else has
looked at a record or avoid "stepping on" another annotator — the server
already does that.

Every response you submit is attributed to the Argilla user whose API key
you are holding. That attribution is permanent and visible to the dataset's
owners, so treat each submission as something with your name on it, not a
draft you can quietly walk back later (though `--status draft` exists for
exactly the cases below where you are not ready to commit).

## Prerequisites (operator does this once, not you)

Before an agent can use this skill, a human operator sets up a dedicated
annotator identity — do not do this yourself unless explicitly asked to,
and never with your own elevated credentials:

```bash
argilla-cli user create agent-ra-1 --role annotator --first-name "Agent RA" \
  --password <generated>
argilla-cli workspace add-user <workspace> agent-ra-1
```

The operator then gives the agent a profile or environment pointed at that
user's API key — `argilla-cli config set api_key <key> --profile agent-ra-1`,
or `ARGILLA_API_URL`/`ARGILLA_API_KEY` in the environment. The **annotator**
role is enforced server-side: that key can read datasets only in its
assigned workspaces and submit its own responses. It cannot create, delete,
or modify datasets or records, cannot write suggestions, and cannot see
other annotators' responses — so a compromised or misbehaving agent key can
only do what a human RA in the same role could do.

## Before you start: verify identity

Confirm you're running as the intended annotator identity before touching
any data — a misconfigured profile pointed at an admin or owner key would
still work, just without the safety rails the annotator role provides:

```bash
argilla-cli server health
argilla-cli user me
```

Confirm the reported username is the dedicated annotator account you
expect, not a personal or admin account.

## The working loop

1. **Read the dataset's rules once, before annotating anything:**

   ```bash
   argilla-cli dataset show <dataset> -w <workspace>
   argilla-cli dataset settings <dataset> -w <workspace> -o json
   ```

   `dataset show` gives the guidelines and question names. `dataset
   settings` gives the full question configuration, including the exact
   set of allowed values for every label/rating/multi-label question. Do
   this once per dataset at the start of a session, not before every
   record — but re-read it if a submission comes back with exit 13 (see
   below), since that usually means your understanding of the schema is
   stale.

2. **Fetch the next pending record(s):**

   ```bash
   argilla-cli annotate next <dataset> -w <workspace> --limit 1 -o json
   ```

   An empty result (empty list, exit 0) means the queue is empty — stop,
   there is nothing left for you to do on this dataset right now. Use `-o
   json` so record id, status, fields, suggestions, and any of your own
   existing draft responses are structured and easy to reason over.

3. **Reason about the record against the guidelines** from step 1 — the
   fields, any suggestions shown, and the question definitions.

4. **Submit your response:**

   ```bash
   argilla-cli annotate submit <dataset> <record-id> \
     --answer question_name=value -w <workspace>
   ```

   Use `--answer key=value` (repeatable) for simple responses, or `--from
   file.json` / `--from -` (stdin) for a complex or multi-question
   response. `--status` defaults to `submitted`; pass `--status draft`
   when you are not confident enough to commit (see Guardrails).

5. **Repeat** from step 2 until `annotate next` returns an empty list, then
   stop. Don't pre-fetch or batch ahead of what you are about to judge —
   annotate one record at a time, in order, so a mistake or an interrupted
   run affects at most one record.

## Guardrails

- **Only use label values that appear in `dataset settings`.** Never
  fabricate a label, invent a category, or guess at a spelling variant that
  "seems close enough." If the value you want to submit for a label/rating
  question is not in that question's configured options, you have
  misread the guidelines or the schema has changed — re-read `dataset
  settings`, don't submit anyway.
- **Never guess beyond what the guidelines support.** If the record is
  genuinely ambiguous under the stated guidelines, submit with `--status
  draft` instead of a confident-looking guess. A draft is visible to human
  reviewers and can be corrected or promoted later; a wrong `submitted`
  response looks the same as a confident, correct one and may be counted
  toward completing the record.
- **Use `annotate discard` only when the record itself is unjudgeable**
  per the guidelines — e.g. missing required content, corrupted media, or
  explicitly out of scope — not as a way to skip records you find
  difficult. Discarding is a judgment ("this record cannot be annotated"),
  not a pass button.
- **One record at a time.** Fetch, judge, submit, then fetch again. Don't
  hold multiple records "in flight."
- **Never blindly retry a submission that failed with exit 13** (validation
  error). That means the value, question name, or shape you sent doesn't
  match what the server expects — re-read `dataset settings` first to see
  what changed or what you misread, then retry with a corrected answer.
  Retrying the identical payload will fail identically.

## Exit codes: what to do

| Exit | Meaning | What to do |
|---|---|---|
| 0 | success (including an empty `annotate next` queue) | Continue the loop, or stop if the queue was empty. |
| 2 | usage error — bad flags or arguments | Fix the invocation; this is a mistake in the command you issued, not the data. |
| 10 | auth/config problem | Stop. Do not retry. Your API key is missing, invalid, or lacks access — this needs the operator, not a different command. |
| 11 | network or server-side failure | Transient — safe to retry the same command a small number of times. If it keeps failing, stop and report it rather than looping indefinitely. |
| 12 | not found | The dataset, workspace, or record id doesn't exist or isn't visible to your annotator account. Don't guess at a different id — stop and check the dataset/workspace names you were given. |
| 13 | validation error | Your answer doesn't match the dataset's question schema. Re-read `dataset settings`, correct the payload, then retry once. Do not retry the same payload. |
