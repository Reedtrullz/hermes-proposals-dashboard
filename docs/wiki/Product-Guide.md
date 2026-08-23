# Product Guide

## Navigation

The primary interface is organized around projects, operational views, and configuration:

| Destination | What it answers |
| --- | --- |
| **Projects** | What initiatives am I advancing and what should happen next? |
| **Reviews** | Which decisions require human input? |
| **Workflows** | What staged processes and handoffs exist? |
| **Settings** | How are goals, agents, budgets, and workers configured? |

## Proposal Intake

The standalone Proposals inbox is retired. `GET /proposals` redirects old bookmarks to Projects. Work intake now happens from a project detail page, while unassigned proposals remain linked from Projects.

- Project detail provides a new-proposal form with title, outcome, and optional assignment.
- Projects lists unassigned proposals so they remain discoverable.
- Projects provides the first-run demo action.
- Settings explains the external worker requirement.

Browser submissions redirect to the authoritative proposal detail page.

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
