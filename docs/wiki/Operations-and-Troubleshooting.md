# Operations And Troubleshooting

## Health And State

The unauthenticated health endpoint is:

```text
GET /health
```

Operational records and triggers live at:

```text
$HERMES_HOME/proposals.db
$HERMES_HOME/proposals_trigger
$HERMES_HOME/proposals_trigger_executor
```

Back up the state directory before database inspection or deployment changes.

## Common Issues

### A proposal is saved but nothing runs

This is expected until an external worker is installed and configured to read the trigger file. The correct new-proposal state is **Waiting for worker**, not active analysis.

Check:

- The worker is running outside the dashboard.
- Its `HERMES_HOME` points to the same persisted location as the app.
- `$HERMES_HOME/proposals_trigger` contains the expected proposal id.
- For CLI delegates, executor metadata exists when expected.

### A CLI executor does not run

Open the relevant agent page or executor status API and confirm the binary is installed on the worker host. In particular:

- `command-code` uses the `cmd` binary, not `command-code`.
- `command-code` requires Node.js 20 or later.
- Each CLI manages its own authentication outside this dashboard.

### A moved local checkout will not launch

Recreate the virtual environment and start via the Python module:

```bash
rm -rf .venv
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
HERMES_REQUIRE_AUTH=0 .venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port 8089 --reload
```

Virtual environment console scripts can preserve the old absolute checkout path.

### A user sees a sign-in redirect locally

Local development must set:

```bash
HERMES_REQUIRE_AUTH=0
```

Hosted instances should retain auth enforcement and a correct `AUTH_URL`.

### A project recommendation does not look AI-generated

That is intentional. Immediate recommendations are derived from status and approval records. Select **Ask worker for recommendations** to create a reviewable planning proposal for an external AI-capable worker.

### Demo content appears in the inbox

Demo records are visibly labeled and shared within the instance. Open the demo proposal and select **Remove demo** to remove only demo-related records.

## Safe Diagnosis Workflow

1. Use `/health` to distinguish web-process failure from workflow state questions.
2. Reproduce with a temporary `HERMES_HOME` before changing real data.
3. Inspect the relevant proposal, review, and audit records through the UI or API.
4. Inspect trigger files only for real execution flows.
5. Run the test suite after changes:

```bash
.venv/bin/python -m compileall -q main.py
.venv/bin/python -m pytest -q
```
