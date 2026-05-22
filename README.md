# Hermes Kanban Dashboard

Hermes is a self-hosted, human-supervised AI agent operations dashboard built around kanban cards. Existing proposal routes remain the card/task surface, and the app adds goals, agents, workflows, approvals, budgets, manual cost tracking, and audit timelines.

## Local Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
HERMES_REQUIRE_AUTH=0 .venv/bin/uvicorn main:app --host 127.0.0.1 --port 8089 --reload
```

Open `http://127.0.0.1:8089/proposals`.

The default SQLite database is `$HERMES_HOME/proposals.db`, or `~/.hermes/proposals.db` when `HERMES_HOME` is not set.

## Validation

```bash
.venv/bin/python -m compileall -q main.py
.venv/bin/python -m pytest -q
docker build -t hermes-kanban-dashboard .
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

The playbook pulls `ghcr.io/reedtrullz/hermes-kanban-dashboard:latest`, runs it on `127.0.0.1:8089`, persists `$HERMES_HOME` in the `hermes_kanban_data` Docker volume, checks `/health`, updates the `kanban.reidar.tech` Caddy block, and reloads Caddy. If the new container fails health checks and a previous image exists, it rolls back.

## Agent Operations Model

- Cards: existing `/proposals` records, extended with goals, parent cards, assigned agents, acceptance criteria, risk, and manual cost.
- Agents: local records with role, purpose, prompt, provider/model metadata, allowed tools, monthly budget, manager, and active/paused/disabled state.
- Goals: outcome, success metric, priority, owner, due date, linked cards, total cost, active agents, and audit timeline.
- Workflows: reusable templates with run stages and explicit handoffs. Seed templates are Feature Delivery, Bug Triage, and Research.
- Budgets: scoped to workspace, goal, project, agent, workflow, or card. Costs are estimated/manual only.
- Approvals: default policy requests approval for critical-risk cards, card costs above `$2.00`, and completing workflows with failed stages.
- Audit trail: append-only events for card, agent, goal, workflow, approval, budget, usage, and handoff changes.

## Existing Integration Points

Creating a card writes its id to `$HERMES_HOME/proposals_trigger`. Approving a card writes `APPROVED:<id>`. Keep this behavior intact for external Hermes agent loops.
