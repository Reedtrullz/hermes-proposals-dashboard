# About Hermes Proposals Dashboard

Hermes Proposals Dashboard is a human-supervised work intake and review system for projects that may use AI-assisted execution. It is designed for a simple question: when several initiatives and possible agent tasks are competing for attention, what should be reviewed, approved, or advanced next?

## Product Purpose

The dashboard separates intent, decision, and execution:

1. A person defines a project and submits a proposal describing an outcome.
2. The dashboard records the proposal, links context, and makes review state visible.
3. A person approves, requests changes, or routes work to an external worker.
4. External Hermes or CLI execution returns results into the reviewable workflow.

This boundary matters. The dashboard is the local system of record; it does not pretend that merely saving a proposal means an agent is working on it.

## Design Principles

### Proposal first

Work begins as a proposal with an outcome and an explicit lifecycle, not as an opaque automated run. Projects give proposals durable context.

### Humans decide

Important actions remain visible and reversible where possible. Risk, spend, and failed workflow stages can require explicit approval.

### Honest automation

The application displays **Waiting for worker** until execution has genuinely begun. It does not manufacture connection status or automatically invoke a paid model.

### Explainable recommendations

The Projects area suggests next actions from recorded state: pending decisions, waiting proposals, review items, and completed milestones. Deeper AI recommendations use the same external worker contract as any other real proposal.

### Local-first operation

SQLite and trigger files keep the system understandable and self-hostable. Integration boundaries are small enough to automate without locking the project into a provider.

## What The Dashboard Covers

- Projects, outcomes, scoped proposals, and recommendation prompts.
- Proposal creation, review notes, approval or change requests, and audit history.
- Agents, executor routing, organization setup, workflows, handoffs, goals, budgets, and cost records.
- Safe demo records for learning the interface without executing work.
- Browser pages and JSON endpoints suitable for external worker integration.

## What It Does Not Cover

- It is not an autonomous agent runtime.
- It does not directly pay for or call LLM APIs.
- It does not infer that a worker is connected.
- It does not replace source control review, CI, or deployment controls.

## Deployment

The configured hosted route is [reidar.tech/proposals](https://reidar.tech/proposals). The project can also run locally or through Docker Compose for private self-hosting.

## Documentation Map

Start with the [Getting Started guide](docs/wiki/Getting-Started.md), then use the [Product Guide](docs/wiki/Product-Guide.md) and [Architecture guide](docs/wiki/Architecture.md) for deeper operational context.
