# Getting Started

This guide takes a new user from an empty local database to a real project and proposal while keeping execution explicit.

## Requirements

- Python 3.12 or compatible Python 3 installation
- Git
- Docker only if using the container path
- An external Hermes or supported CLI worker only when you want real execution

## Run Locally

```bash
git clone https://github.com/Reedtrullz/hermes-proposals-dashboard.git
cd hermes-proposals-dashboard
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
HERMES_REQUIRE_AUTH=0 .venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port 8089 --reload
```

Open [http://127.0.0.1:8089/proposals](http://127.0.0.1:8089/proposals).

If the repository was moved after creating `.venv`, recreate the virtual environment. The `.venv/bin/python -m uvicorn` form is intentionally used because moved console-script launchers can retain an old absolute path.

## Explore Without Execution

1. On **Proposals**, select **Try demo**.
2. Read the proposal summary, review thread, timeline, and decision controls.
3. Add a note.
4. Select **Approve** or **Request changes**.
5. Select **Remove demo** to delete demo-linked records.

The demo is clearly marked and does not write worker trigger files.

## Create Real Work

1. Open **Projects** and create a project.
2. Add its desired outcome, for example, "Make client onboarding self-serve."
3. Open the project or return to **Proposals**.
4. Submit a proposal, selecting that project and optionally an agent.
5. The saved proposal opens immediately with **Waiting for worker** status.

At this point the work is recorded, but it is not executing unless an external worker has been configured.

## Understand Recommendations

On a project page, **Suggested next steps** reports what recorded state indicates: resolve a pending decision, route a waiting proposal, inspect work in progress, or define another milestone after completed work.

Select **Ask worker for recommendations** only when a configured worker should generate a deeper planning response. It creates a regular proposal so the request is visible and reviewable.

## Configure Execution Later

A real proposal writes to `$HERMES_HOME/proposals_trigger`. An approval writes `APPROVED:<id>`. Non-Hermes agent assignments may also write `proposals_trigger_executor`.

Continue with [Workers and Executors](Workers-and-Executors.md) before wiring automated execution.

## Keep State Separate During Evaluation

Run isolated experiments with a temporary home:

```bash
HERMES_HOME="$(mktemp -d)" HERMES_REQUIRE_AUTH=0 \
  .venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port 8089
```

The default state location is `~/.hermes`, so an explicit temporary location avoids mixing evaluation records into ongoing work.
