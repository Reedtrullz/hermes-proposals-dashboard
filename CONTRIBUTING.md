# Contributing

Hermes Proposals Dashboard is a small server-rendered FastAPI application. Contributions should keep the user-facing model clear: people create and decide proposals; configured external workers perform execution.

## Development Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
HERMES_REQUIRE_AUTH=0 .venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port 8089 --reload
```

Use a temporary `HERMES_HOME` when testing local flows against an isolated database:

```bash
HERMES_HOME="$(mktemp -d)" HERMES_REQUIRE_AUTH=0 \
  .venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port 8089
```

## Contribution Rules

- Use **Proposal** and **Projects** language in UI and documentation; do not reintroduce legacy board terminology in rendered copy.
- Keep `/api/proposals` and trigger-file behavior compatible with external workers.
- Keep migrations idempotent using existing SQLite patterns in `main.py`.
- Do not add automatic paid-provider calls or claim worker connectivity without a real protocol.
- Preserve escaping of user-entered notes and review content.
- Keep credentials, `.env`, SQLite files, virtual environments, and local trigger state out of commits.

## Validation

Before opening a pull request:

```bash
.venv/bin/python -m compileall -q main.py
.venv/bin/python -m pytest -q
docker build -t hermes-proposals-dashboard .
```

For UI changes, run the app with an isolated `HERMES_HOME` and exercise:

1. Empty first-use inbox and demo walkthrough.
2. Real proposal creation and redirect to the waiting detail state.
3. Project assignment and recommendations.
4. Approval or request-changes actions.
5. Mobile-width layout for modified pages.

## Pull Requests

Describe:

- What changed and why.
- Any route, schema, or trigger compatibility effects.
- Validation commands and manual flows exercised.
- Screenshots for visible UI changes when available.

The source for native wiki documentation is maintained in `docs/wiki/`; update relevant pages when behavior changes.
