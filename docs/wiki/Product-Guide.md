# Product Guide

## Navigation

The primary interface is organized around four working destinations plus configuration:

| Destination | What it answers |
| --- | --- |
| **Proposals** | What work has been submitted and what state is it in? |
| **Projects** | What initiatives am I advancing and what should happen next? |
| **Reviews** | Which decisions require human input? |
| **Workflows** | What staged processes and handoffs exist? |
| **Settings** | How are goals, agents, budgets, and workers configured? |

## Proposals Inbox

The inbox is the starting point for work intake. It supports:

- A visible new-proposal form with title, outcome, optional project, and optional assignment.
- Filtered views for all items, waiting work, review items, decisions, and completed items.
- A first-run demo action.
- Worker guidance that states the external execution requirement.

New browser submissions redirect to the authoritative proposal detail page rather than leaving an unchanged form behind.

## Proposal Detail

A proposal detail view brings together:

- Summary and outcome.
- Status and decision state.
- Acceptance criteria and metadata.
- Review notes and timeline.
- Assigned agent or external executor context.
- Approve and request-changes actions.

Proposal notes are rendered safely. HTML or script markup appears as text instead of executing in the browser.

## Projects

Projects are durable initiative containers. Each project records a name, description, desired outcome, and lifecycle status. Associated proposals, pending decisions, waiting work, completed items, and recorded costs appear in its view.

Project recommendations are intentionally traceable to stored status and review data. See [Projects and Recommendations](Projects-and-Recommendations.md).

## Reviews

Reviews expose approval requests resulting from proposal risk, estimated cost, executor safety, or workflow outcomes. Proposal-level decisions update both the pending approval record and the proposal state so the interface does not show contradictory results.

## Workflows

Workflow templates represent repeatable staged work. A run can:

- Link to a proposal.
- Progress through stages.
- Record stage notes.
- Capture handoffs between agents.
- Request a decision when a failed run is completed.

## Settings And Setup

Settings provides access to:

- **Projects**: initiatives and next actions.
- **Goals**: higher-level outcomes and success metrics.
- **Agents**: role definitions, assignment, executors, and cost records.
- **Budgets**: manual spending limits by scope.
- **Organization setup**: reporting layout and workflow templates.
- **Worker setup**: an explanation of local storage and external trigger consumption.

## Status Vocabulary

| Status | Meaning |
| --- | --- |
| `waiting` | Saved and awaiting a connected worker or manual review. |
| `processing` | A worker or integration has explicitly reported active work. |
| `review` | Work is ready for human review or decision. |
| `approved` | Human approval has been recorded. |
| `implemented` | The intended work has been completed. |
| `rejected` | Changes were requested or the proposal was declined. |

The UI may display **Needs decision** when a pending approval exists; it is derived from the review record rather than a conflicting stored status.
