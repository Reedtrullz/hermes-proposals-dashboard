# Projects And Recommendations

Projects make the dashboard useful for more than a single proposal queue. They represent ongoing initiatives such as a product launch, a client migration, a documentation effort, or infrastructure hardening.

## Create A Useful Project

Use a name that remains meaningful over time and a desired outcome that describes the measurable result rather than a vague work category.

| Weak entry | Useful entry |
| --- | --- |
| "Website" | "Proposal dashboard public launch" |
| "Improve UX" | "New users submit and review a proposal without instructions" |
| "AI stuff" | "External worker recommends prioritized next actions for active projects" |

The optional description holds constraints, current state, or stakeholders.

## Attach Proposals

When submitting a proposal, choose its project from the form. Existing integration clients remain compatible: the historic `board` field is used as the project compatibility key. If a legacy API client submits a non-default `board` value, the dashboard exposes it as a visible project record.

## Local Recommendations

The dashboard generates up to three recommended next actions from recorded project data. Examples include:

| Recorded situation | Suggested action |
| --- | --- |
| A proposal has a pending approval | Resolve the waiting decision. |
| Work is saved with `waiting` status | Route or review the waiting proposal. |
| A proposal is processing | Check active work for an update. |
| Work has entered review | Convert review into a decision. |
| Work was rejected | Revise the requested changes. |
| Approved work has not completed | Track it through completion. |
| The project has no proposals | Define the first outcome-bearing proposal. |
| All recorded work is implemented | Choose the next project milestone. |

These suggestions are rules over SQLite records. They are fast, private, and explainable, but they are not model-generated strategy.

## Request AI-Assisted Recommendations

The **Ask worker for recommendations** action creates a scoped proposal with a planning prompt containing the project outcome. The proposal begins in **Waiting for worker** and writes the standard real-proposal trigger.

This approach ensures AI-assisted planning:

- Is visible in the project history.
- Uses configured worker controls.
- Can be reviewed before actions are adopted.
- Does not create hidden provider spend.

## Project Lifecycle

| Project status | Usage |
| --- | --- |
| `active` | Current initiative receiving proposals or decisions. |
| `paused` | Retained context with work temporarily stopped. |
| `completed` | Outcome achieved; retained for history. |
| `archived` | Removed from active selection while preserving records. |

Archive or pause projects rather than deleting operational history.
