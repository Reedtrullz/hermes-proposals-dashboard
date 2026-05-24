# Workers And Executors

## Execution Boundary

Hermes Proposals Dashboard is a control surface and local record store. It does not run agent loops or call model providers from the web process. Execution is delegated to an external Hermes worker or CLI-aware integration.

## Trigger Sequence

For a real proposal:

1. The dashboard creates the proposal with **Waiting for worker** status.
2. It writes the proposal identifier to `$HERMES_HOME/proposals_trigger`.
3. If assigned to a supported non-Hermes executor, it writes routing metadata to `$HERMES_HOME/proposals_trigger_executor`.
4. An external worker reads the trigger and performs or delegates the task.
5. Status, review notes, usage, workflow, or completion are reported back through the app/API.
6. Approving real work writes `APPROVED:<proposal_id>` to the unchanged trigger file contract.

Demo proposals do not enter this sequence.

## Supported Executor Types

| Type | Binary | Headless entry point |
| --- | --- | --- |
| `hermes` | native | Hermes worker loop |
| `codex` | `codex` | `codex exec --full-auto "prompt"` |
| `claude-code` | `claude` | `claude -p "prompt"` |
| `opencode` | `opencode` | `opencode run "prompt"` |
| `agy` | `agy` | `agy exec "prompt"` |
| `command-code` | `cmd` | `cmd -p "prompt"` |
| `kilo` | `kilo` | `kilo run --auto "prompt"` |

`command-code` exposes the `cmd` binary and requires Node.js 20 or later.

## Safety

Executor availability is not the same as approval to run it. The dashboard requests approval for dangerous executor routes including Codex and Command Code configurations capable of broad automated changes. Worker implementations should:

- Use constrained workspaces or worktrees.
- Avoid unreviewed bypass flags outside disposable environments.
- Record actual provider costs separately from estimates.
- Return changes for review and validation before deployment.

## Setup Checklist

1. Open **Settings** and read the worker setup panel.
2. Create or enable agent definitions under **Agents** or **Organization setup**.
3. Verify any CLI binary and authentication on the machine that runs the worker.
4. Submit a test proposal assigned to that executor.
5. Confirm trigger file content and the external worker's handling.
6. Review output and cost records before expanding automation.

For package-specific installation and executor status routes, read the repository's [CLI executor reference](../cli-executor-reference.md).
