# Hermes Proposals Dashboard

Hermes is a self-hosted, human-supervised operations dashboard built around proposals. The app adds goals, agents, workflows, reviews, budgets, manual cost tracking, and audit timelines.

## Local Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
HERMES_REQUIRE_AUTH=0 .venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port 8089 --reload
```

Open `http://127.0.0.1:8089/proposals`.

The default SQLite database is `$HERMES_HOME/proposals.db`, or `~/.hermes/proposals.db` when `HERMES_HOME` is not set.

If this repository directory is moved or renamed, recreate `.venv` before launching. Virtual environment console-script shebangs can retain the old absolute path; using `.venv/bin/python -m uvicorn` avoids that launcher issue.

## First Use

1. Open `/proposals/projects` and create projects for the initiatives you are working on.
2. Open `/proposals` and select **Try demo** to open removable sample data. The demo never executes a worker.
3. Review the sample proposal, add a note, and exercise **Approve** or **Request changes**.
4. Select **Remove demo** on the demo detail page to delete only the sample records.
5. Submit real proposals into a project. They are saved as **Waiting for worker**.
6. Open a project to see locally calculated next-step recommendations. These are derived from stored status and decision data, not from an automatic model call.
7. Configure an external Hermes or CLI worker before expecting live execution or deeper AI-assisted recommendations. A project can submit a planning proposal for that worker.

## Validation

```bash
.venv/bin/python -m compileall -q main.py
.venv/bin/python -m pytest -q
docker build -t hermes-proposals-dashboard .
```

## Self-Hosting

```bash
cp .env.example .env
# edit HERMES_API_KEY and AUTH_URL
docker compose up -d --build
```

The compose setup stores SQLite and trigger-file state in the `hermes-data` volume mounted at `/data/hermes`.

## VPS Deployment With Ansible

This repo follows the neighboring `/Users/reidar/Projectos` deployment convention: root-level `ansible.cfg`, `inventory/hosts.yml`, `group_vars/vps/vars.yml`, and `ansible-playbook.yml`.

This repo is set up for agentic Ansible use. The encrypted `group_vars/vps/vault.yml` can live with the project, while the local `.ansible-vault-pass` file is ignored by Git and lets agents run the playbook without prompting for a vault password.

Prepare or rotate the encrypted secret:

```bash
ansible-vault edit group_vars/vps/vault.yml
```

Deploy:

```bash
ansible-playbook ansible-playbook.yml
```

The playbook pulls `ghcr.io/reedtrullz/hermes-proposals-dashboard:latest`, runs it on `127.0.0.1:8089`, persists `$HERMES_HOME` in the `hermes_proposals_data` Docker volume, checks `/health`, updates the `reidar.tech/proposals` Caddy handlers, and reloads Caddy. If the new container fails health checks and a previous image exists, it rolls back.

## Agent Operations Model

- Proposals: `/proposals` records, extended with goals, parent proposals, assigned agents, acceptance criteria, risk, and manual cost.
- Projects: first-class initiative records that group proposals through the existing `board` compatibility field and show local next-step recommendations.
- Agents: local records with role, purpose, prompt, provider/model metadata, allowed tools, monthly budget, manager, and active/paused/disabled state.
- Goals: outcome, success metric, priority, owner, due date, linked proposals, total cost, active agents, and audit timeline.
- Workflows: reusable templates with run stages and explicit handoffs. Seed templates are Feature Delivery, Bug Triage, and Research.
- Budgets: scoped to workspace, goal, project, agent, workflow, or proposal. Costs are estimated/manual only.
- Agent cost tracking: agent budget meters use current-month actual usage records. Open an agent detail page and use "Record Actual Cost" to enter provider/model, tokens, tool calls, and the real billed USD amount from a provider usage page or invoice.
- Reviews: default policy requests a decision for critical-risk proposals, proposal costs above `$2.00`, and completing workflows with failed stages.
- Audit trail: append-only events for proposal, agent, goal, workflow, approval, budget, usage, and handoff changes.

## Existing Integration Points

Creating a real proposal writes its id to `$HERMES_HOME/proposals_trigger`. Approving a proposal writes `APPROVED:<id>`. Demo proposals do not write trigger files. Keep this behavior intact for external Hermes agent loops.

Project pages show immediate recommendations computed from local proposal state, such as unresolved decisions or waiting work. Choosing **Ask worker for recommendations** creates a real waiting proposal in that project, allowing a configured external worker to supply deeper AI-assisted planning through the existing trigger integration.
