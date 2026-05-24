# Hermes Proposals Dashboard Wiki

Hermes Proposals Dashboard is a proposal-first interface for organizing projects, reviewing possible work, and coordinating externally executed AI tasks without hiding the human decision point.

## Start Here

| You want to... | Read... |
| --- | --- |
| Run the application and explore it safely | [Getting Started](Getting-Started.md) |
| Understand every visible section | [Product Guide](Product-Guide.md) |
| Track projects and interpret recommendations | [Projects and Recommendations](Projects-and-Recommendations.md) |
| Wire an external Hermes or CLI worker | [Workers and Executors](Workers-and-Executors.md) |
| Understand tables, lifecycle, and boundaries | [Architecture](Architecture.md) |
| Build an API client or worker integration | [API and Integrations](API-and-Integrations.md) |
| Deploy or self-host the service | [Deployment](Deployment.md) |
| Operate or diagnose the app | [Operations and Troubleshooting](Operations-and-Troubleshooting.md) |
| Find quick answers | [FAQ](FAQ.md) |

## The Short Version

1. Create a **Project** for an initiative you are working on.
2. Submit a **Proposal** stating the outcome you want.
3. Review the proposal, notes, cost context, workflow context, and decisions.
4. Allow a configured external worker to process real proposals when you are ready.
5. Retain status, review, and audit records in the dashboard.

## Important Boundary

The dashboard does not automatically call an AI provider or claim that a worker is connected. New real proposals start at **Waiting for worker**. A worker must be configured outside this app to consume trigger files and return progress.

Project recommendations are initially calculated from local status and decision records. The **Ask worker for recommendations** action creates a normal waiting proposal so deeper AI-assisted planning follows the same reviewed execution path.

## Maintained Source

Wiki content is maintained in the repository under `docs/wiki/` so documentation changes travel with tested product changes and can be reviewed in pull requests.
